# -*- coding: utf-8 -*-
"""Playwright E2E 用例共享的本地服务自举帮助模块（唯一自举路径）。

背景：`demo/tests/` 下有一批真实浏览器用例需要 127.0.0.1:15010 上的活体服务。
历史上每个文件各自抄一份 `ensure_server()`：多数直接 `-m uvicorn` 起服务，**没有**
锁定域名 DNS 解析；只有少数经 `_playwright_serve.py` 启动器拉起。于是「谁先起服务」
由文件名顺序决定 —— 自带裸 `ensure_server()` 的文件先起了一个未锁定服务，后续用例
盲目复用它，导致 A/B 实验创建这类要过 `security_guard` SSRF 校验的请求被 fail-closed
拒绝（本机 fake-IP DNS 把域名解析成 198.18.x.x），出现「顺序相关」的假失败。

本模块把服务自举收敛成**唯一一条路径**：
  1. 唯一启动器：所有服务一律经 `_playwright_serve.py` 拉起，该启动器在服务进程内
     锁定「域名」DNS 解析为公网地址、放行 IP 字面量（见该文件 docstring）。因此经本
     模块起的服务**必然**带 DNS 锁定，不存在「未锁定服务」这条支路。
  2. 只读数据源：被测服务以 **live 库的只读拷贝** 为 `DB_PATH`。拷贝方向是
     live → 临时目录（单向），live 库只被读取，绝不被写入。why：demo/conftest.py
     的 autouse 夹具会把 `DB_PATH` 指向 tmp_path 里的空库，子进程若继承该环境便拿不到
     任何单据/实验数据，页面断言只能等到超时。显式注入副本路径可得到确定性数据源。
  3. 复用必须可判定归属：会话内复用**只认本进程自己起的 handle**；端口上若有「非本次
     会话」的服务，先经 `lsof`+`ps` 校验其命令行确含本启动器（即 pinned）才复用，
     否则**拒绝盲目复用**并报错。why：盲目复用任意 15010 上的服务，正是上述顺序相关
     假失败的根因。
  4. 并发安全：多个 pytest 进程（如 pytest-xdist）可能同时自举。启动动作放在一把
     文件锁里串行化，并在锁内二次探测，规避「两个进程同时判空、同时 bind 同一端口」
     的经典竞态。
  5. 精确回收：仅在「本进程启动了服务」时才会在结束时杀掉它，并删除临时副本目录。

除 15010 这条共享路径外，需要独立受控数据源/端口的用例（如 A/B 抽屉用例的 15011）
也统一经 `start_pinned_server()` 起服务，同样必然带 DNS 锁定，不再各写一份 Popen。
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

BASE_PORT = int(os.environ.get("PW_TEST_PORT", "15010"))
BASE_URL = os.environ.get("PW_TEST_BASE_URL", os.environ.get("TEST_BASE_URL", "http://127.0.0.1:%d" % BASE_PORT))

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.abspath(os.path.join(TESTS_DIR, "../.."))
DEMO_DIR = os.path.join(ROOT_DIR, "demo")
LIVE_DB = os.environ.get("PW_TEST_DB_SOURCE", os.path.join(DEMO_DIR, "receipt_demo.db"))
# 服务启动器：与被测服务同进程地锁定域名 DNS 解析（见该文件 docstring）。
LAUNCHER = os.path.join(TESTS_DIR, "_playwright_serve.py")

# 启动等待上限（秒）：uvicorn 冷启动 + 依赖导入 + create_all 一般在数秒内完成，
# 留足余量但不至于让异常情况静默挂死。
STARTUP_TIMEOUT = 40.0

# 本 pytest 会话内由本模块启动的共享服务句柄（15010）。非 None 即「我们自己起的」，
# 会话内复用只认它，绝不复用会话外来源不明的服务。
_SESSION_HANDLE = None


class ServerHandle:
    """本进程启动的服务句柄：持有子进程、临时副本目录与日志文件。

    `stop()` 是幂等的；仅在拿到句柄时才会被调用（复用已有服务时返回 None）。
    """

    def __init__(self, proc, data_dir, log_file):
        self.proc = proc
        self.data_dir = data_dir
        self.log_file = log_file
        self._stopped = False

    def stop(self, timeout=10.0):
        if self._stopped:
            return
        self._stopped = True
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=timeout)
        except OSError:
            pass
        finally:
            if self.log_file is not None:
                try:
                    self.log_file.close()
                except OSError:
                    pass
            if self.data_dir:
                shutil.rmtree(self.data_dir, ignore_errors=True)

    def read_log(self):
        """读取服务 stdout/stderr 日志（诊断用）；不可读时返回空串。"""
        if self.log_file is None:
            return ""
        try:
            path = self.log_file.name
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return ""


def current_base_url():
    """当前自举服务根地址。ensure_server 可能改过端口，用例要在夹具之后读。"""
    return BASE_URL


def _explicit_base_url():
    return os.environ.get("PW_TEST_BASE_URL", os.environ.get("TEST_BASE_URL", "")).strip()


def _free_port():
    import socket
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _retarget_base(port):
    """把模块级端口与 BASE_URL 改到空闲端口。不回收、不连接原来的监听者。"""
    global BASE_PORT, BASE_URL
    BASE_PORT = int(port)
    BASE_URL = "http://127.0.0.1:%d" % BASE_PORT


def _occupied_by_foreign(port):
    """端口上有监听，且不是本启动器拉起的 pinned 服务。"""
    if not _listener_pids(port):
        return False
    return not _listener_is_pinned(port)


def is_healthy(timeout=2.0, base_url=None):
    """探测 `/api/health` 是否就绪（uvicorn 已开始服务请求）。

    该端点在鉴权白名单内、匿名可读，且按设计「组件异常也返回 200、绝不 500」，
    因此任何 < 500 的 HTTP 响应都说明服务已能处理请求（即已就绪）。

    `base_url` 缺省时读模块全局 BASE_URL。不能把缺省值绑在函数定义时：
    ensure_server 遇到非 pinned 占用会改端口，定义期绑定仍会去探原来的地址。
    """
    if base_url is None:
        base_url = BASE_URL
    try:
        with urllib.request.urlopen(base_url + "/api/health", timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as exc:
        return exc.code < 500
    except (urllib.error.URLError, OSError):
        return False


def _listener_pids(port):
    """返回监听指定 TCP 端口的进程 PID 列表；lsof 不可用时返回空列表。"""
    try:
        out = subprocess.run(
            ["lsof", "-nP", "-iTCP:%d" % port, "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(tok) for tok in out.split() if tok.strip().isdigit()]


def _process_command(pid):
    """返回进程的完整命令行；不可读时返回空串。"""
    try:
        out = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.strip()


def _listener_is_pinned(port):
    """端口上的监听进程是否为「经本启动器（_playwright_serve.py）拉起」的。

    通过 lsof 取监听 PID、再经 ps 校验命令行确含本启动器文件名。无法判定（lsof/ps
    不可用、或取不到监听进程）时返回 False —— 宁可判定为「不是我们的」而拒绝复用，
    也不盲目复用来源不明的服务。
    """
    if os.environ.get("PLAYWRIGHT_REUSE_ANY_SERVER") == "1":
        return True
    launcher_name = os.path.basename(LAUNCHER)
    for pid in _listener_pids(port):
        cmd = _process_command(pid)
        if cmd and launcher_name in cmd:
            return True
    return False


def _reuse_pinned_or_raise(port):
    """端口上已有服务时：确认是 pinned 服务才允许复用，否则拒绝并给出处置指引。

    返回 None（复用外部服务、本进程无权回收）。非 pinned 时抛 RuntimeError：未锁定
    域名 DNS 解析的服务会让 A/B 实验创建等 SSRF 校验 fail-closed 假失败，必须显式失败
    而不是静默测到错误行为。
    """
    if not _listener_is_pinned(port):
        raise RuntimeError(
            "端口 %d 上已有服务在监听，但它不是本套件自举的 pinned 服务（进程命令行不含 "
            "%s）。拒绝盲目复用：非 pinned 服务未锁定域名 DNS 解析，会让 A/B 实验创建等 "
            "SSRF 校验 fail-closed 假失败。请先结束该进程再重跑，例如："
            "lsof -nP -iTCP:%d -sTCP:LISTEN 找到 PID 后 kill。"
            % (port, os.path.basename(LAUNCHER), port))
    return None


def _copy_live_db():
    """把 live 库（含 WAL 侧车文件）单向下拷到临时目录，返回 (目录, 副本路径)。

    WAL 模式下已提交数据可能仍在 `-wal` 中，必须一并拷贝，副本才能读出主库之外的
    数据。live 库缺失时仍会创建空副本目录并返回一个 **全新空库** 路径：宁可无数据也
    绝不让子进程落到 live 库上（守住「不写 live 库」的硬约束）。
    """
    tmp_dir = tempfile.mkdtemp(prefix="playwright_ui_db_")
    dst = os.path.join(tmp_dir, "playwright_ui.db")
    custom_source = os.environ.get("PW_TEST_DB_SOURCE", "").strip()
    no_live = os.environ.get("PW_TEST_NO_LIVE_DB", "").strip() == "1"

    src = custom_source if (custom_source and os.path.isfile(custom_source)) else (LIVE_DB if not no_live else None)
    if src and os.path.isfile(src):
        try:
            shutil.copy2(src, dst)
            for suffix in ("-wal", "-shm"):
                side = src + suffix
                if os.path.isfile(side):
                    shutil.copy2(side, dst + suffix)
        except OSError:
            pass
    return tmp_dir, dst


def _acquire_start_lock():
    """返回一把排他文件锁（已上锁），用于串行化多进程的服务启动。

    平台不支持 fcntl（如 Windows）时静默降级为「无锁」：单进程顺序执行不受影响，
    退化为与既有 ensure_server() 同级的并发表现，不会因此报错。
    """
    lock_path = os.path.join(tempfile.gettempdir(), "receipt_demo_playwright_%d.lock" % BASE_PORT)
    try:
        handle = open(lock_path, "w")
    except OSError:
        return None
    try:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_EX)
    except (ImportError, OSError):
        pass
    return handle


def _release_start_lock(lock_handle):
    if lock_handle is None:
        return
    try:
        import fcntl
        fcntl.flock(lock_handle, fcntl.LOCK_UN)
    except (ImportError, OSError):
        pass
    try:
        lock_handle.close()
    except OSError:
        pass


def start_pinned_server(port, data_dir, db_path, extra_env=None,
                        startup_timeout=STARTUP_TIMEOUT):
    """经启动器在指定端口拉起一个 **pinned** 服务，返回必须由调用方回收的 ServerHandle。

    唯一启动入口：`subprocess.Popen` 只出现在这里，命令行恒为 `python _playwright_serve.py`
    （端口经 `PW_SERVER_PORT` 注入），因此必然带域名 DNS 锁定。

    `data_dir` 由调用方准备（临时目录），失败路径会负责回收它；`db_path` 会作为
    `DB_PATH` 注入，`extra_env` 可追加 RAG_DIR 等覆盖。端口已被占用（疑似残留进程）时
    子进程会以非零码退出，这里抛 RuntimeError 并附日志尾部，避免「静默测了另一份数据」。
    """
    base_url = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    # 覆盖继承来的 DB_PATH（pytest 隔离夹具留下的空库），指向只读副本。
    env["DB_PATH"] = db_path
    env["PW_SERVER_PORT"] = str(port)
    if extra_env:
        env.update(extra_env)

    log_path = os.path.join(data_dir, "server.log")
    log_file = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, LAUNCHER],
        cwd=DEMO_DIR,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )

    start = time.time()
    while time.time() - start < startup_timeout:
        if is_healthy(base_url=base_url):
            return ServerHandle(proc, data_dir, log_file)
        if proc.poll() is not None:
            # 子进程先退出了：多半是端口竞争失败（别人已 bind）。读取日志尾部后回收。
            tail = ""
            try:
                log_file.flush()
                with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                    tail = fh.read()[-2000:]
            except OSError:
                pass
            try:
                log_file.close()
            except OSError:
                pass
            shutil.rmtree(data_dir, ignore_errors=True)
            raise RuntimeError(
                "本地服务启动即退出（exit=%s，端口 %d 疑似被占用），日志尾部：\n%s"
                % (proc.returncode, port, tail))

    handle = ServerHandle(proc, data_dir, log_file)
    tail = handle.read_log()[-2000:]
    handle.stop()
    raise RuntimeError("本地服务在 %.0fs 内未就绪（端口 %d），日志尾部：\n%s"
                       % (startup_timeout, port, tail))


def ensure_server():
    """确保 15010 上有可用的本地服务。

    返回 `ServerHandle`（本进程刚启动，结束时应 `stop()`）或 `None`
    （复用会话外已确认 pinned 的服务，本进程无权结束它）。
    """
    global _SESSION_HANDLE

    # 1) 会话内复用：只认本进程自己起的 handle，无需再校验归属。
    cached = _SESSION_HANDLE
    if cached is not None:
        if cached.proc.poll() is None and is_healthy():
            return cached
        # 自己起的服务已不健康：回收后重新自举。
        cached.stop()
        _SESSION_HANDLE = None

    # 默认端口被常驻演示服务占用时：不杀、不复用、不写它的库。
    # 改到空闲端口自举 pinned 服务（临时库由 start_pinned_server 注入）。
    # 调用方若显式给了 PW_TEST_BASE_URL / TEST_BASE_URL，仍按原端口失败关闭，
    # 避免地址和实际进程各说各话。
    if not _explicit_base_url() and _occupied_by_foreign(BASE_PORT):
        _retarget_base(_free_port())

    # 2) 端口上已有服务：确认是 pinned 才复用，否则拒绝。
    if is_healthy():
        return _reuse_pinned_or_raise(BASE_PORT)

    lock_handle = _acquire_start_lock()
    try:
        # 锁内二次探测：等待期间别的进程可能已经把服务拉起来了。
        if is_healthy():
            return _reuse_pinned_or_raise(BASE_PORT)

        data_dir, data_db = _copy_live_db()
        try:
            handle = start_pinned_server(BASE_PORT, data_dir, data_db)
        except RuntimeError:
            # 子进程因端口竞争先行退出：若此刻已有 pinned 服务就绪则复用，否则抛出。
            if is_healthy():
                return _reuse_pinned_or_raise(BASE_PORT)
            raise
        _SESSION_HANDLE = handle
        return handle
    finally:
        _release_start_lock(lock_handle)
