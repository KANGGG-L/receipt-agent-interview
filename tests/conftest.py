# -*- coding: utf-8 -*-
"""tests/ 顶层夹具：离线可跑的环境锁定。

BUG-04（step12 记录）：裸跑 pytest 时，模块级 load_dotenv() 会把本机的
AUTH_ENABLED=1 注入进程，导致约 196 个权限相关用例失败。此处在**任何测试模块
导入之前**（conftest 先于测试模块加载）把鉴权锁定为关闭，保证离线全绿。

dotenv 的 load_dotenv() 默认 override=False，因此这里预设的环境变量不会被
后续 .env 覆盖。若某用例需要验证鉴权开启的行为，请在用例内显式 monkeypatch
app.auth 的判定函数，而不是放开这里的全局开关。

图片落盘隔离（N2）：`_isolate_test_db` 除隔离 DB 外，还会把图片输出目录
（原图 / 预处理产物 / 凭证）重定向到 tmp_path。why：只隔离 DB 时，上传端点仍
把文件写进 live 目录 demo/uploads，跑一次根套件就留下无单据引用的孤儿文件
（如 `<id>.jpg` / `<id>_prep.jpg`）。详见夹具内注释。

兜底判据（S1）：不能只看「生效库是否仍等于 live」——那样会把「上一个测试文件
遗留在 db.DB_PATH 上的临时库」误当成「本用例自己的隔离」而整体跳过，泄漏便自我
延续。现在用进程级 registry 区分「本模块自己切过的库」（尊重）与「别的模块遗留的
库」（判为泄漏，强制隔离救回），判据与记录逻辑见 `_classify_db_path` / 夹具本体。

向量库落盘隔离（P0-T2）：`_isolate_rag_dir` 把 `RAG_DIR` 与 `rag.PERSIST_DIR`
指向 tmp_path 并清 `rag._vs` / `rag._vs_tenant`。why：rag.PERSIST_DIR 缺省是 cwd
相对路径 "./.rag_chroma"，只隔离 DB 时「DB 进临时库、向量进 live 向量库」——
live 向量库里那批 vendor 为测试夹具名的孤儿即由此而来。

产物目录落盘隔离（Q3-M4）：`_isolate_artifacts_dir` 把 `ARTIFACTS_DIR` 指向本轮
tmp_path。why：`_finalize` 每完成一次识别就向仓库内
`artifacts/memory/parse_log.jsonl` **追加**一行（P2d-metrics 审核 M4 实测该文件会被
测试写到 1.4MB）；该路径按模块 __file__ 计算，DB / RAG / 图片隔离都覆盖不到它，
故单独设一个可覆盖点（supervisor._memory_log_path 读 ARTIFACTS_DIR）。
"""

import ipaddress
import os
import socket
import sys

import pytest

# 鉴权关闭，必须在 import app.* 之前
os.environ["AUTH_ENABLED"] = "0"

# 嵌入档锁定为离线 hash，必须在 import app.* 之前（O1 起 rag 的嵌入档由
# app/services/embeddings.get_embeddings() 决定）。why：接线后默认档是 hash 且不需要
# 网络，但若开发机 shell / .env 里恰好设了 RAG_EMBEDDING_PROVIDER=siliconflow，
# 任何走记忆写入/检索的用例都会真发 HTTP（慢、需要凭据、失败还会回落）—— 测试必须
# 自足可复现。需要其他档的用例请在用例内 monkeypatch（见 tests/test_embeddings_provider.py）。
os.environ["RAG_EMBEDDING_PROVIDER"] = "hash"

# demo 应用根目录（tests 与 demo/tests 都能 import app.*）
_DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "demo"))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

# 仓库根目录（tests 直接 import ai_registry.*）
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# live demo 库路径：db.py 的默认值就是 demo/receipt_demo.db（见 app/db.py）
_LIVE_DB_PATH = os.path.abspath(os.path.join(_DEMO_DIR, "receipt_demo.db"))

# live 图片输出目录：只在某模块的落盘常量仍指向它时才改写，尊重已有隔离设置
_LIVE_UPLOAD_DIR = os.path.abspath(os.path.join(_DEMO_DIR, "uploads"))

# rag.py 源码路径：用来识别「同一个 rag.py 被多个模块名导入」产生的别名模块
_RAG_SOURCE = os.path.abspath(os.path.join(_DEMO_DIR, "app", "services", "rag.py"))


def _redirect_image_output_dirs(monkeypatch, tmp_path):
    """把图片落盘目录重定向到本轮用例的临时目录，返回改写后的 uploads 目录。

    why：只隔离 DB 是不够的 —— 上传 / 换图 / 凭证端点会把原图与预处理产物写进
    模块级常量（`api_receipts.UPLOAD_DIR`、`api_finance.VOUCHER_DIR`、
    `main.UPLOAD_DIR`），默认值就是 live 目录 demo/uploads。DB 已隔离时这些
    文件没有任何单据引用，却仍留在 live 目录，跑一次根套件即留下孤儿文件。

    只改写「仍指向 live」的模块，保留用例可能已有的隔离设置；且只动已经 import
    的模块（不主动 import app.main，避免触发其模块级副作用），未 import 的模块
    自然也不可能写文件。测试函数体内的 monkeypatch 在本夹具之后生效，优先级更高。
    """
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    voucher_dir = upload_dir / "vouchers"
    voucher_dir.mkdir(parents=True, exist_ok=True)

    for mod_name, attr, target in (
        ("app.api_receipts", "UPLOAD_DIR", upload_dir),
        ("app.api_finance", "VOUCHER_DIR", voucher_dir),
        ("app.main", "UPLOAD_DIR", upload_dir),
    ):
        mod = sys.modules.get(mod_name)
        if mod is None or not hasattr(mod, attr):
            continue
        try:
            current = os.path.abspath(str(getattr(mod, attr)))
        except Exception:
            continue
        if current != _LIVE_UPLOAD_DIR and not current.startswith(_LIVE_UPLOAD_DIR + os.sep):
            continue
        monkeypatch.setattr(mod, attr, target)
    return upload_dir


# -------------------------------------------------------------
# 环境锁定（D20）：域名 DNS 解析固定为一个公网地址
# -------------------------------------------------------------
_DNS_PIN_IP = "1.1.1.1"


@pytest.fixture(autouse=True)
def _pin_dns_resolution(monkeypatch):
    """把 socket.getaddrinfo 固定解析到公网地址，消除「本机解析器行为」这道隐藏变量。

    why：本套件大量用例用真实域名（https://api.siliconflow.cn/v1 等）作为引擎 base_url，
    而 security_guard.validate_safe_external_url 对域名做 DNS 解析、再按 IP 段判安全性，
    于是这些用例的结论取决于运行机器的解析器。D20 起 198.18.0.0/15（RFC 2544 基准段）
    被列为阻断段：在启用 fake-IP DNS 代理（Clash/Surge 等把**任意**域名解析成 198.18.x.x）
    的机器上，一切域名校验都会被正确拒绝，进而让「保存引擎配置」类用例整片假失败——
    那是本机解析器行为泄漏进用例，不是产品缺陷。本夹具把解析锁成公网地址，
    使域名形态的用例在离线/代理环境下结论一致（与套件「离线可跑的环境锁定」目标一致）。

    只锁定「域名」的解析：host 已是 IP 字面量（127.0.0.1 / ::1 等）时本就不经 DNS，
    沿用真实实现——否则会把浏览器用例的本地服务探测
    （如 socket.create_connection(("127.0.0.1", 15010))）错解析到公网地址，
    令服务自举的「是否已在监听」误判为真、跳过启动，最终 ERR_CONNECTION_REFUSED。

    需要特定解析行为的用例（SSRF 重绑定、解析失败 fail-closed，以及 D20 新用例的
    「解析到 198.18.x.x」形态）在用例内自行 monkeypatch socket.getaddrinfo；用例内的
    setattr 在 autouse 夹具之后执行，优先级更高，故这些断言完全不受本夹具影响。
    """
    _real_getaddrinfo = socket.getaddrinfo

    def _public_getaddrinfo(host, port, *args, **kwargs):
        try:
            ipaddress.ip_address(str(host))
        except ValueError:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                     (_DNS_PIN_IP, int(port or 0)))]
        return _real_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", _public_getaddrinfo)


# -------------------------------------------------------------
# S1：DB 隔离兜底的判据与「库路径归属」registry
# -------------------------------------------------------------
# 进程级：{测试模块文件绝对路径: 该模块在用例执行期间自己切过去的库路径集合}
# 只在「用例执行期间 db.DB_PATH 被改成了别的库」时登记（见 _record_module_local）。
# 模块级 os.environ["DB_PATH"] 声明的环境库在 setup 时已经在位，不会被登记，
# 因此「环境库全局共享」的既有行为不受影响。
_MODULE_LOCAL_DB = {}

# 进程级：本轮真被兜底救回来的泄漏记录 {(模块, 泄漏库路径, 归属模块)}
_LEAKS_RESCUED = set()


def _module_key(node):
    """取用例所属模块文件的绝对路径，作为 registry 的键。

    用 node.path（pytest>=7 为 pathlib.Path）而不是 nodeid：nodeid 带用例名，
    会让同一模块的用例各自成为独立的键，登记不起作用。
    """
    path = getattr(node, "path", None)
    if path is None:
        path = getattr(node, "fspath", None)
    return os.path.abspath(str(path)) if path is not None else "<unknown>"


def _foreign_leak_owner(path, module_key, registry):
    """path 是否为「别的模块在用例执行期间切过去的库」；是则返回归属模块，否则 None。

    单一实现，供 `_classify_db_path` 与夹具「隔离落点选择」共用，避免同型判据抄两份。
    """
    for other, paths in registry.items():
        if other != module_key and path in paths:
            return other
    return None


def _classify_db_path(current, live_path, env_path, module_key, registry):
    """把当前生效的 db.DB_PATH 归类，返回 (kind, 归属模块或 None)。

    kind 取值：
      "live"    —— 仍是 live 默认库，必须隔离；
      "owned"   —— 本模块自己在用例期间切过去的库，尊重；
      "leak"    —— 别的模块遗留的库，判为泄漏并强制隔离（这就是兜底救回）；
      "ambient" —— 等于 env 声明的环境库，尊重（模块级显式声明与全局共享的语义）。

    纯函数，不碰 db 模块，便于单元测试直接断言判据（见
    tests/test_conftest_db_isolation_judgement.py）。
    """
    if current == live_path:
        return "live", None
    if current in registry.get(module_key, ()):
        return "owned", None
    owner = _foreign_leak_owner(current, module_key, registry)
    if owner is not None:
        return "leak", owner
    if env_path and current == env_path:
        return "ambient", None
    return "leak", None


def _alias_db_modules(primary):
    """同进程内「同一个 db.py 被另一个模块名导入」产生的别名模块（如 demo.app.db）。

    why：demo/ 没有 __init__.py，`import app.db` 与 `from demo.app import db` 会各自
    按源码导入一次，得到两个模块实例、各自持有 DB_PATH。兜底只改 app.db 时，别名模块
    仍指向旧库（默认即 live），隔离不完整 —— 实测根套件里写 demo.app.db、读 app.db 的
    用例会因此读写分离（tests/test_fix_b_p0_sku_explosion.py）。
    """
    out = []
    try:
        primary_file = os.path.abspath(str(primary.__file__))
    except Exception:
        return out
    for name, mod in list(sys.modules.items()):
        if mod is None or mod is primary:
            continue
        if not name.endswith(".db") and not name.endswith("db"):
            continue
        try:
            if os.path.abspath(str(mod.__file__)) != primary_file:
                continue
        except Exception:
            continue
        if hasattr(mod, "DB_PATH") and hasattr(mod, "_make_engine"):
            out.append(mod)
    return out


def _sync_db_path(mods, path):
    """把若干 db 模块实例（主模块 + 别名模块）统一指向 path 并重建引擎。"""
    for mod in mods:
        try:
            mod.DB_PATH = path
            mod._make_engine()
        except Exception:
            continue


def _record_module_local(module_key, expected_path, db_module, aliases=()):
    """用例收尾：若 db.DB_PATH 被用例自己改成了别的库，登记为本模块的本地库。

    why：只有「在用例执行期间出现」的路径才可能是上个文件的遗留物。这里记录
    「setup 时的预期路径 → 收尾时实际路径」的差异，下次别的模块再看到同一个
    路径就能判为泄漏；同一模块自己继续用则照旧尊重。

    别名模块（demo.app.db 等）同样纳入登记，避免「换个 import 写法就能绕过判据」。
    """
    live = os.path.abspath(str(db_module._default_db_path))
    for mod in (db_module,) + tuple(aliases):
        try:
            now_path = os.path.abspath(str(mod.DB_PATH))
        except Exception:
            continue
        if now_path == expected_path or now_path == live:
            continue
        _MODULE_LOCAL_DB.setdefault(module_key, set()).add(now_path)


def _reset_isolation_registry():
    """清空进程级 registry（仅供测试夹具复位，生产路径不需要调用）。"""
    _MODULE_LOCAL_DB.clear()
    _LEAKS_RESCUED.clear()


@pytest.fixture(autouse=True)
def _isolate_test_db(tmp_path, monkeypatch, request):
    """P17 治本：未显式隔离时，强制把测试库指向临时库，杜绝污染 live 库。

    why：本项目测试长期没有代码级隔离，conftest 从不兜底；只要有用例 import
    app.db 且未让 db.DB_PATH 脱离 live，就会读写 live 库 demo/receipt_demo.db。

    S1 兜底判据（为什么不能只看「是否等于 live」）：只看「生效的 db.DB_PATH 是否
    仍等于 live」时，无法区分 (a) 本用例主动隔离、(b) 上一个测试文件把它自己的
    临时库泄漏在 db.DB_PATH 上。两种情况看起来都是非 live，于是旧判据对 (b) 也
    整体跳过隔离，泄漏会自我延续（先跑的文件改成陌生库且不还原，后续文件就落到
    那个库上读到别人的遗留数据，如 running 实验）。

    现在的判据（`_classify_db_path`）：
      - 仍是 live 默认库            → 隔离（原有行为）；
      - 属于本模块自己切过的库      → 尊重（模块自身隔离语义不变）；
      - 属于别的模块切过的库        → 判为泄漏，强制隔离救回；
      - 等于 env 声明的库（环境库） → 尊重（模块级 os.environ 声明的既有语义）；
      - 其余非 live 路径            → 判为泄漏，强制隔离救回。
    隔离落点：优先沿用 env 声明的环境库（同进程可能存在 demo.app.db 这类「另一个
    包路径」读同一个库，落点一致才不会读写分离），env 不可信时才用本用例临时库。

    别名模块：demo/ 无 __init__.py，`app.db` 与 `demo.app.db` 是同一份源码的两个模块
    实例、各自持有 DB_PATH；兜底会把已加载的别名模块一并对齐（见 `_alias_db_modules`），
    否则「换个 import 写法」就能绕过隔离、甚至指回 live 库。

    与 demo/conftest.py 的同名夹具共用同一套判据与同名 registry；两处独立实现是
    因为 pytest 只加载各目录自己的 conftest，不做跨目录夹具继承。
    「泄漏」分支例外：它还原成「进入本用例时的库」（非 live），不还原成 live ——
    否则会给识别 Job 的后台线程留一个「用例结束后仍写 live 库」的窗口。
    """
    # N2：图片落盘目录先于 DB 分支重定向，避免「DB 已被用例隔离」时提前 return 漏掉
    _redirect_image_output_dirs(monkeypatch, tmp_path)

    from app import db as _db

    raw_env = os.environ.get("DB_PATH", "").strip()
    env_db_path = os.path.abspath(raw_env) if raw_env else ""
    if env_db_path in (os.path.abspath("./receipt_demo.db"), os.path.abspath("receipt_demo.db")):
        # env 里的是「默认 live 库」的等价写法，不算显式声明
        env_db_path = _LIVE_DB_PATH

    module_key = _module_key(request.node)
    entry_path = os.path.abspath(str(_db.DB_PATH))
    kind, source = _classify_db_path(
        entry_path, _LIVE_DB_PATH, env_db_path, module_key, _MODULE_LOCAL_DB)

    # 隔离落点：优先沿用 env 声明的环境库（它同时是 demo.app.db 等「另一个包路径」
    # 读取的库，用同一个库才不会出现同进程内两套 app 包读写分离）；只有 env 没声明、
    # 或 env 本身就是别的模块泄漏出来的库时，才落回本用例专属临时库。
    env_is_sane = bool(raw_env) and bool(env_db_path) and env_db_path != _LIVE_DB_PATH \
        and _foreign_leak_owner(env_db_path, module_key, _MODULE_LOCAL_DB) is None

    # 别名模块（demo.app.db 等）：必须与主模块同库，否则同进程内读写分离/绕过隔离
    aliases = _alias_db_modules(_db)

    isolated = False
    restore_to = _LIVE_DB_PATH
    if kind in ("live", "leak"):
        isolated_db_path = raw_env if env_is_sane else str(tmp_path / "pytest_isolated.db")
        monkeypatch.setenv("DB_PATH", isolated_db_path)
        _sync_db_path([_db] + aliases, isolated_db_path)
        isolated = True
        if kind == "leak":
            _LEAKS_RESCUED.add((module_key, entry_path, source))
            # 收尾还原成「进入本用例时的样子」（一个非 live 库），而不是 live：
            # 识别 Job 跑在后台线程里，用例结束后仍可能调用 db.* 落库（实测根套件
            # 会因此把 live 库文件写一次，数据虽幂等但仍是触碰 live）。旧实现在这
            # 一分支压根不还原，故保持「不改动发现时的进程状态」最安全。
            restore_to = entry_path
    else:
        # 尊重既有隔离语义时不改主模块，但别名模块仍要对齐到主模块，
        # 否则会出现「app.db 读 A、demo.app.db 读写 B」的读写分离。
        _sync_db_path(aliases, os.path.abspath(str(_db.DB_PATH)))
    expected_path = os.path.abspath(str(_db.DB_PATH))

    try:
        yield
    finally:
        _record_module_local(module_key, expected_path, _db, aliases)
        if isolated:
            _sync_db_path([_db] + aliases, restore_to)


# -------------------------------------------------------------
# P0-T2：RAG（向量库）落盘隔离
# -------------------------------------------------------------
def _loaded_rag_modules():
    """已加载的全部 rag 模块实例（主名与 demo.app.services.rag 这类别名各一份）。

    why：与 db 的别名模块同源 —— demo/ 没有 __init__.py，`from app.services import rag`
    与 `from demo.app.services import rag` 会各按源码导入一次，得到两个模块实例、各自
    持有 PERSIST_DIR 与 `_vs` 缓存。兜底只改主模块时，别名模块仍指向 live 向量库。
    """
    out = []
    for _name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        try:
            if os.path.abspath(str(getattr(mod, "__file__", ""))) != _RAG_SOURCE:
                continue
        except Exception:
            continue
        if hasattr(mod, "PERSIST_DIR") and hasattr(mod, "_store"):
            out.append(mod)
    return out


def _sync_rag_dir(mods, path):
    """把若干 rag 模块实例统一指向 path，并清空进程内 collection 缓存。

    why 清缓存：`_vs` / `_vs_tenant` 记住的是「上一次用那个 persist_directory 打开的
    Chroma 句柄」，只改常量不清缓存会继续沿用旧目录读写 —— 隔离只做一半。
    """
    for mod in mods:
        try:
            mod.PERSIST_DIR = path
            mod._vs = None
            mod._vs_tenant = {}
        except Exception:
            continue


@pytest.fixture(autouse=True)
def _isolate_rag_dir(tmp_path, monkeypatch):
    """P0-T2：未显式隔离时，强制把向量库落盘目录指向本轮临时目录。

    why：`rag.PERSIST_DIR` 的缺省落点由 `app/services/rag_paths.py` 按**应用根**定位
    （P8a-R7 前为 cwd 相对 "./.rag_chroma"，故从仓库根跑测试会落到 `<repo>/.rag_chroma`），
    而 conftest 只隔离了 DB 与图片目录 —— 于是测试写入会落到 **live 向量库
    demo/.rag_chroma**。live 向量库里已积累多批带 memory_id 的孤儿（vendor 全是测试
    夹具名「测试供应商」），根因即此。

    隔离落点固定用本轮 tmp_path（不同于 DB 夹具「优先沿用 env 声明的环境库」）：向量库
    没有「模块级声明的环境库必须全局共享」的既有语义，逐用例独立目录可同时消除跨用例
    串扰（同名 collection 残留）。

    只处理已经加载的 rag 模块（含 demo.app.services.rag 这类别名实例）；尚未 import 时
    设置 env 即足够 —— rag.py 在 import 时读 RAG_DIR。

    收尾不把 PERSIST_DIR 还原成进入时的值：缺省值可能就是 live 路径，还原会给「用例
    结束后仍可能调用 rag 的代码」留一个写 live 向量库的窗口（与 DB 夹具 leak 分支同理）。
    PERSIST_DIR 保持指向本轮临时目录，下一个用例的夹具再覆盖。
    """
    rag_dir = tmp_path / "rag_chroma"
    rag_dir.mkdir(parents=True, exist_ok=True)
    target = str(rag_dir)
    monkeypatch.setenv("RAG_DIR", target)

    _sync_rag_dir(_loaded_rag_modules(), target)
    yield


# -------------------------------------------------------------
# Q3-M4：artifacts/ 产物目录落盘隔离
# -------------------------------------------------------------
@pytest.fixture(autouse=True)
def _isolate_artifacts_dir(tmp_path, monkeypatch):
    """Q3-M4：把产物目录 artifacts/ 重定向到本轮临时目录，避免测试往仓库内追加写。

    why：supervisor 记忆落盘（`_finalize` → `app/chains/supervisor._memory_log_path()`）
    的缺省落点是仓库内 artifacts/memory/parse_log.jsonl，且是**追加写**；该路径按模块
    __file__ 计算，DB / RAG / 图片隔离都覆盖不到它 —— 任何真正跑识别链路的用例（含门禁、
    重试、租户等不调 VLM 的分支用例）都会往仓库里留下记录（实测该文件已累积 1.4MB）。

    这里设置 ARTIFACTS_DIR，supervisor 在**每次落盘时**读它（见 _memory_log_path 的
    docstring），因此对「用例内才 import supervisor」的时序同样生效：不依赖模块是否
    已加载、也不受 `app.chains.supervisor` / `demo.app.chains.supervisor` 别名模块影响
    （环境变量是进程级的）。这与 DB_PATH / RAG_DIR 两条隔离面同款机制。不采用
    「monkeypatch 已加载模块的落盘常量」的做法，正因它只能覆盖夹具跑之前就已 import
    的模块，那正是本仓高发的「隔离只做一半」（①类：同根因的多个入口只堵一个）；
    覆盖点本身落在产品代码里（supervisor._memory_log_path 读 ARTIFACTS_DIR）。

    落点固定用本轮 tmp_path（同 `_isolate_rag_dir`）：产物目录没有「模块级声明的环境
    目录必须全局共享」的既有语义，逐用例独立目录同时消除跨用例串扰。

    与 tests/conftest.py 的同名夹具互为镜像；两处独立实现是因为 pytest 只加载各目录
    自己的 conftest，不做跨目录夹具继承。
    """
    artifacts_dir = tmp_path / "artifacts" / "memory"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    yield
