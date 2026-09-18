# -*- coding: utf-8 -*-
"""非 Web 格式原图 → 浏览器可渲染 JPEG（单一职责，无 Web 依赖）。

why: `.heic/.heif/.tif/.tiff` 经 `/uploads` 的 StaticFiles 直挂时，Starlette
`guess_type` 不认识这些扩展名，会返回 `content-type: text/plain`，浏览器必然
不渲染；且前端转码只覆盖「用户选文件那一刻」，对已入库文件没有任何通道。
因此服务端需按需把非 Web 原图转成 JPEG 供 `<img src>` 直接展示。

- is_non_web_image_path(path): 是否为浏览器无法直接渲染的图片路径
- transcode_to_jpeg_bytes(src, quality): 纯函数转码（入参为文件路径），无磁盘副作用
- transcode_bytes_to_jpeg(data, quality): 纯函数转码（入参为上传字节流，供 /api/convert-image 复用）
- web_preview_path(src): 返回可渲染路径；非 Web 格式落 `<stem>_web.jpg` 缓存副本
- preview_cache_path(src) / remove_preview_cache(src): 缓存副本定位与定向清理
- referenced_preview_stems(image_paths) / sweep_orphan_previews(dir, stems): 孤儿缓存判定与兜底扫描
- _sweep_stale_tmp(dir): 自身临时文件残骸（SIGKILL 场景）的幂等兜底清理

约束（刻意为之）：只读源文件、只在源文件同目录写缓存副本，不改 DB、不改
`image_path`、不删原图；识别/审核链路读的始终是本地原文件。

模块边界（复杂度评估结论，2026-09-18）：刻意保持单文件，不拆为
image_transcode / image_cache / image_sweep 等模块。why:
- 真实逻辑量约 265 行，20 个函数平均约 13 行纯代码；文件总行数中约 55% 是注释与
  docstring，行数规模带来的复杂度观感主要来自文档密度而非逻辑规模；
- 各关注点是同一条「按需转码」链路上的顺序阶段，而非可独立演进的子系统：
  它们共享扩展名集合、缓存后缀、像素上限、HEIF 注册状态与锁序不变量，拆开
  必须在模块间重新导出这些常量与状态，反而新增耦合面；
- 调用方与测试直接依赖本模块符号：测试内有 8 处对 `_HEIF_REGISTERED` /
  `_HEIF_WARNED` / `_HEIF_REGISTER_ERROR` / `_TMP_SWEPT_DIRS` 的 monkeypatch，
  以及 11 处对 `_cache_hit` / `_sweep_stale_tmp` 的私有函数直调；改名或搬家会让
  这些引用静默失效（monkeypatch 落到不存在的属性上不报错，测试假绿），属高回归
  风险；
- PIL 已是函数内惰性 import，本模块顶层只依赖标准库，拆模块并不能降低导入成本。
故当前无「导入慢 / 循环依赖 / 误改频发」的实际痛点，不做纯结构重构。将来若真
出现上述具体痛点，再按最小风险方案拆分，且不得改动本模块公开函数签名。
"""

import io
import logging
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger("image_web")

# 浏览器无法直接渲染、需服务端转码的扩展名
NON_WEB_EXTS = {".heic", ".heif", ".tif", ".tiff"}

# 其中真正需要 pillow-heif 解码器的子集：TIFF 由 Pillow 原生支持，缺 pillow-heif
# 时仍应能正常转码，不能被一并判死。
_HEIF_EXTS = {".heic", ".heif"}

# ISO BMFF ftyp box 的 HEIF/HEIC major brand：用于给「上传字节流」判定是否需要
# HEIF 解码器（字节流没有扩展名可用）
_HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis",
                b"hevm", b"hevs", b"mif1", b"msf1"}

# 转码质量：与 api_receipts 既有 /api/convert-image 口径一致（92 + optimize）
JPEG_QUALITY = 92

# 预览副本长边上限：超过则等比缩放，仅作用于预览副本，不影响识别输入（识别读原图）
MAX_PREVIEW_SIDE = 4000

# 本模块自用的像素上限（Pillow 默认 89478485）。why: 不通过给
# `PIL.Image.MAX_IMAGE_PIXELS` 赋值实现 —— 那是进程级全局量，会一并改写同进程内
# 识别 / preprocess 链路的 PIL 全局状态；且 Pillow 的真实规则是「> 2×上限才 raise、
# > 上限只 warn」，改全局并不构成硬边界。改为打开图片后只读比较 img.size
# （见 _ensure_pixels_within_limit），超限抛受控异常。放宽到 120M 以覆盖高像素
# 扫描件，同时仍是内存炸弹的硬边界。
MAX_IMAGE_PIXELS = 120_000_000

# 缓存副本后缀：与 preprocess.py 的 `<原名>_prep.jpg` 命名风格保持一致
_PREVIEW_SUFFIX = "_web.jpg"

# _atomic_write 落临时文件用的后缀（mkstemp 形态：`<dst.stem>.<随机串>.tmp`）
_TMP_SUFFIX = ".tmp"

# 缓存副本落盘后的最终权限。why: mkstemp 固定产出 0600，与 uploads/ 内既有上传件
# （通常 0644）不一致。同进程 FileResponse 读取不受影响，但若将来由 nginx 等「另一
# 用户身份」的静态服务器托管该目录，0600 会导致读不到图。这里显式对齐 0644。
# 注意用 os.chmod 而非 os.umask：chmod 设的是绝对模式，不受进程 umask 影响。
_PREVIEW_FILE_MODE = 0o644

# 陈旧临时文件的年龄阈值（秒）。why: 正常转码写临时文件只存活毫秒级，远达不到该
# 阈值；加阈值是确保清理绝不误删其他线程「正在写入」的临时文件（见 _sweep_stale_tmp）。
_STALE_TMP_AGE_SECONDS = 3600

# 按目标路径的锁表 + 保护字典的全局锁：并发下同一张图只转一次。
# why: 键是「源文件同目录的 _web.jpg 目标路径」，若只增不减会随单据数量在进程内
# 无限累积，故用 _LOCK_REFS 引用计数在最后一个持有者释放后摘除条目（见 _lock_for）。
_LOCKS = {}
_LOCK_REFS = {}
_LOCKS_GUARD = threading.Lock()

# HEIF 解码器注册状态（失败只 WARN 一次；后果由调用方按「输入是否真的需要
# HEIF 解码」收窄，见 _register_heif_opener 的 why）
_HEIF_REGISTERED = False
_HEIF_WARNED = False
_HEIF_REGISTER_ERROR = None

# 保护上述三个状态的模块级锁。why: 注册是「读 _HEIF_REGISTERED/_HEIF_REGISTER_ERROR
# → 执行注册 → 写回」的复合操作，多线程首次并发进入时若不加锁，各线程都会读到
# 未注册而重复调用 register_heif_opener()（该函数幂等、无实际危害，但重复注册与
# 「只 WARN 一次」的判断都属竞态）。锁内只有 import 与一次注册调用，都是一次性的
# 纯内存操作、无文件 IO，持锁时间可忽略。
# 锁序说明（防死锁）：调用方先持 _LOCKS 的按目标锁再进入本锁（web_preview_path →
# transcode_* → 本函数），反向不存在 —— 本函数内部不再获取任何其他锁，故无环。
_HEIF_GUARD = threading.Lock()

# 已做过陈旧临时文件兜底扫描的目录：每进程每目录只扫一次，避免请求热路径重复
# iterdir；进程重启后重新扫一次，正好覆盖「上次进程被 SIGKILL」的场景。
_TMP_SWEPT_DIRS = set()
_TMP_SWEEP_GUARD = threading.Lock()


def is_non_web_image_path(path) -> bool:
    """判断路径是否为浏览器无法直接渲染的图片（HEIC/HEIF/TIFF）。

    why: URL 构造与端点共用同一判据，避免两处扩展名列表漂移。
    """
    if not path:
        return False
    try:
        return os.path.splitext(str(path))[1].lower() in NON_WEB_EXTS
    except Exception:
        return False


def preview_stem(path) -> str:
    """取文件在预览缓存命名里使用的 stem（缓存名为 `<stem>_web.jpg`）；取不到返回 ""。

    why: 缓存命名规则此前只存在于 web_preview_path 内部，清理逻辑若自行拼字符串
    会与转码侧命名漂移（改一处忘一处即误删或漏删）；统一由此函数给出唯一口径。
    """
    name = os.path.basename(str(path or "").strip())
    return os.path.splitext(name)[0]


def preview_cache_path(src_path):
    """返回非 Web 源文件对应的预览缓存路径；Web 格式或无法判定时返回 None。

    why: 定向清理（replace-image 换图后）需要在不触发转码的前提下知道副本落在哪，
    且必须与 web_preview_path 的落盘位置严格一致。
    """
    if not is_non_web_image_path(src_path):
        return None
    stem = preview_stem(src_path)
    if not stem:
        return None
    return Path(str(src_path)).with_name(stem + _PREVIEW_SUFFIX)


def _register_heif_opener() -> bool:
    """尽力注册 HEIF/HEIC 解码器，返回是否可用；失败只 WARN 一次，不抛异常。

    why: 旧实现在两个转码入口无条件调用本函数且失败即 raise，等于把可选的
    pillow-heif 升级成整个端点的硬依赖 —— 环境缺依赖或版本不兼容时，连 jpg/png/webp
    这些根本不需要 HEIF 解码的输入都会 500（改造前是 `except Exception: pass` 静默降级）。
    这里改为返回布尔结果，由调用方只在「输入确实是 HEIC/HEIF」时才据此给出明确错误。

    并发安全：整个「查状态 → 注册 → 写状态」在 _HEIF_GUARD 内完成，保证多线程首次
    并发调用只真正执行一次 register_heif_opener()（幂等函数，重复执行无危害，但重复
    注册与重复 WARN 判断属竞态）；锁内不含文件 IO，不构成热路径阻塞。
    """
    global _HEIF_REGISTERED, _HEIF_WARNED, _HEIF_REGISTER_ERROR
    with _HEIF_GUARD:
        if _HEIF_REGISTERED:
            return True
        if _HEIF_REGISTER_ERROR is not None:
            return False
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except Exception as e:
            _HEIF_REGISTER_ERROR = e
            if not _HEIF_WARNED:
                _HEIF_WARNED = True
                logger.warning(
                    "[WARN] pillow_heif.register_heif_opener 失败，HEIC/HEIF 原图无法转码: %s", e)
            return False
        _HEIF_REGISTERED = True
        return True


def _heif_unavailable_error() -> RuntimeError:
    """构造「HEIF 解码器不可用」的明确异常，附上注册失败原因便于排障。"""
    msg = "HEIC/HEIF 解码器不可用（请确认已安装 pillow-heif 且版本兼容）"
    if _HEIF_REGISTER_ERROR is not None:
        msg = f"{msg}: {_HEIF_REGISTER_ERROR}"
    return RuntimeError(msg)


def _looks_like_heif_bytes(data) -> bool:
    """按 ISO BMFF 的 ftyp box 判断上传字节流是否为 HEIF/HEIC（无需解码器）。

    why: /api/convert-image 手里只有 bytes，没有扩展名；要把「缺解码器」的后果
    收窄到真实 HEIC/HEIF 输入，必须先能识别它。只读前 12 字节即可判定。
    """
    if not data or len(data) < 12:
        return False
    if data[4:8] != b"ftyp":
        return False
    return data[8:12].lower() in _HEIF_BRANDS


def _ensure_pixels_within_limit(img):
    """打开后只读比较像素数，超限抛受控异常（刻意不改 PIL 全局）。

    why: 旧实现给 `Image.MAX_IMAGE_PIXELS` 赋 120M，是进程级全局写入，会污染同进程
    内识别 / preprocess 链路的 PIL 状态；且 Pillow 只在像素 > 2×上限时 raise、
    > 上限时仅 warn，赋值并不构成硬边界。改为读 img.size 自行判定，既保留「超大图不
    抛 DecompressionBombError 打断请求」的目标，又不动全局值。
    """
    pixels = int(img.size[0]) * int(img.size[1])
    if pixels > MAX_IMAGE_PIXELS:
        raise RuntimeError(
            f"图片像素 {pixels} 超过上限 {MAX_IMAGE_PIXELS}，拒绝转码预览")


def _open_image(src_path):
    """打开图片文件并自行校验像素上限，超限转受控异常并关闭句柄。"""
    from PIL import Image
    try:
        img = Image.open(src_path)
    except Image.DecompressionBombError as e:
        # 仅当像素 > 2×Pillow 默认上限时 Pillow 才抛；统一转成受控异常
        raise RuntimeError(
            f"图片像素超过上限 {MAX_IMAGE_PIXELS}，拒绝转码预览: {e}") from e
    try:
        _ensure_pixels_within_limit(img)
    except Exception:
        img.close()
        raise
    return img


def _normalize_and_encode(img, quality) -> bytes:
    """EXIF 矫正朝向 + mode 归 RGB/L + 超长边等比缩放 + 编码 JPEG。

    why: 无论源是文件路径还是上传字节流，转码语义必须完全一致（旧实现里
    /api/convert-image 自写一份转码体，漏了缩放且质量参数分叉）。这里把「解码之后」
    的全部处理收敛成唯一实现。
    """
    from PIL import Image, ImageOps

    # 依 EXIF 矫正拍摄朝向
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass

    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    width, height = img.size
    longest = max(width, height)
    if longest > MAX_PREVIEW_SIDE:
        scale = MAX_PREVIEW_SIDE / float(longest)
        img = img.resize((max(1, int(width * scale)), max(1, int(height * scale))),
                         Image.LANCZOS)

    out_buf = io.BytesIO()
    img.save(out_buf, format="JPEG", quality=int(quality), optimize=True)
    return out_buf.getvalue()


def transcode_to_jpeg_bytes(src_path, quality=JPEG_QUALITY) -> bytes:
    """把图片文件转成 JPEG 字节（纯函数：不落盘、不改源文件）。

    转码语义沿用既有 /api/convert-image：exif_transpose 矫正朝向 + mode 归 RGB +
    save(JPEG, quality, optimize=True)。仅当长边超过 MAX_PREVIEW_SIDE 时等比缩放
    （只影响预览副本，识别输入仍是原图）。HEIF 解码器仅在扩展名为 .heic/.heif 时才
    注册，缺 pillow-heif 不影响 TIFF 及 Web 格式。
    """
    path = str(src_path)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"原图文件不存在: {path}")

    # 只有 HEIC/HEIF 才依赖 pillow-heif；TIFF 及任何 Web 格式不受其缺失影响
    ext = os.path.splitext(path)[1].lower()
    if ext in _HEIF_EXTS and not _register_heif_opener():
        raise _heif_unavailable_error()

    with _open_image(path) as img:
        return _normalize_and_encode(img, quality)


def transcode_bytes_to_jpeg(data, quality=JPEG_QUALITY) -> bytes:
    """把上传得到的图片字节流转成 JPEG 字节（纯函数：不落盘）。

    why: /api/convert-image 手里是 `await file.read()` 的 bytes，而文件路径入口
    `transcode_to_jpeg_bytes` 只接受路径。若在 api_receipts.py 里另写一份解码逻辑，
    两条转码路径必然再次漂移（旧实现正因如此漏掉 MAX_PREVIEW_SIDE、并静默吞掉
    pillow_heif 注册失败）。此处只把「打开入口」换成 BytesIO，其余处理完全共用。

    解码器缺失的后果同样按输入收窄：仅当字节流确实是 HEIF/HEIC 时才报明确错误，
    jpg/png/webp 等照常转码（字节流无扩展名，用 ftyp brand 识别）。
    """
    if _looks_like_heif_bytes(data) and not _register_heif_opener():
        raise _heif_unavailable_error()

    from PIL import Image
    try:
        opened = Image.open(io.BytesIO(data))
    except Image.DecompressionBombError as e:
        raise RuntimeError(
            f"图片像素超过上限 {MAX_IMAGE_PIXELS}，拒绝转码预览: {e}") from e

    # with 保证异常路径也会 close；像素校验沿用与文件入口一致的自查逻辑
    with opened as img:
        _ensure_pixels_within_limit(img)
        return _normalize_and_encode(img, quality)


@contextmanager
def _lock_for(key: str):
    """按 key 取互斥锁；最后一个使用者释放后回收该 key 的条目。

    why: `_LOCKS` 若只增不减，会随被转码过的单据数量在进程内无限累积。用引用计数
    保证「仍有等待者时绝不摘除」：只有计数归零（临界区内与等待队列都空）才 pop，
    否则新线程会在摘除后另建一把锁，与仍持旧锁的线程同时转码同一张图（写入虽原子，
    但白做一次转码）。
    """
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        _LOCK_REFS[key] = _LOCK_REFS.get(key, 0) + 1
    lock.acquire()
    try:
        yield lock
    finally:
        lock.release()
        with _LOCKS_GUARD:
            remaining = _LOCK_REFS.get(key, 1) - 1
            if remaining > 0:
                _LOCK_REFS[key] = remaining
            else:
                _LOCK_REFS.pop(key, None)
                _LOCKS.pop(key, None)


def _cache_hit(src: Path, dst: Path) -> bool:
    """缓存命中条件：副本存在 && size > 0 && 副本 mtime 不早于源文件 mtime。

    why: 源文件被覆盖（replace-image）后 mtime 变大，旧副本自动失效，不会串图。

    比较用 `st_mtime_ns`（纳秒）而非 `st_mtime`（浮点秒）：秒级精度下，同一秒内
    原地覆盖同一路径（源 mtime 只前进不到 1 秒）时 `dst >= src` 恒为真，会命中旧
    缓存展示错图。replace-image 走 db.new_id() 必产新文件名，该窗口实际不可达，
    但纳秒比较把这条理论路径也一并消除，改造成本仅为取另一个字段。
    """
    try:
        dst_stat = dst.stat()
    except OSError:
        return False
    if dst_stat.st_size <= 0:
        return False
    try:
        src_stat = src.stat()
    except OSError:
        return False
    return dst_stat.st_mtime_ns >= src_stat.st_mtime_ns


def _atomic_write(dst: Path, data: bytes):
    """同目录 mkstemp 写临时文件后 os.replace 原子替换，finally 清理临时文件。

    why: 并发读者只会看到「完整副本」或「不存在副本」，不会读到半截 JPEG。
    os.fdopen 自身也可能抛异常（极端内存 / 描述符压力），而它失败时不会替调用方关闭
    fd，故在 fdopen 外面显式兜底 os.close(fd)，避免 mkstemp 的描述符泄漏。

    权限：mkstemp 产出 0600，与 uploads/ 内既有上传件不一致，故在 os.replace 之后把
    目标文件 chmod 到 _PREVIEW_FILE_MODE（0644）。why 放在 replace 之后而非
    mkstemp 之后：临时文件在替换前始终维持 0600，不会被其他用户读到半截内容，原子
    替换语义与「替换前不可见」的性质都不变。chmod 失败仅 WARN —— 文件内容已正确
    落盘，权限对齐属加固项，不应让一次预览请求 500。

    已知取舍（SIGKILL）：finally 只在进程仍能执行 Python 时生效。进程被 SIGKILL /
    断电硬中断时 finally 不执行，临时文件 `<dst.stem>.<随机串>.tmp` 会残留在
    uploads/ 内。这些残骸永不被复用（mkstemp 每次新随机名），故不构成正确性问题，
    只随崩溃次数缓慢累积；由 _sweep_stale_tmp 在下次调用入口做幂等兜底清理，而非在
    本函数里做任何补偿（写路径必须保持最短、无额外目录扫描）。
    """
    fd, tmp_path = tempfile.mkstemp(prefix=dst.stem + ".", suffix=_TMP_SUFFIX,
                                    dir=str(dst.parent))
    try:
        try:
            f = os.fdopen(fd, "wb")
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        with f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, dst)
        tmp_path = None
        try:
            os.chmod(dst, _PREVIEW_FILE_MODE)
        except OSError as e:
            logger.warning("[WARN] 设置预览缓存权限失败 %s: %s", dst, e)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _is_own_tmp_name(name: str) -> bool:
    """判断文件名是否为 _atomic_write 自身产生的临时文件（`<dst.stem>.<随机串>.tmp`）。

    why: 清理必须只认自己的产物，绝不能误删目录里其他模块/用户文件的 `*.tmp`。
    本模块的临时文件由 mkstemp(prefix=dst.stem + ".", suffix=".tmp") 生成，而预览
    副本的 dst.stem 一律以 `_web` 结尾（`<stem>_web.jpg`），故判据取「以 .tmp 结尾，
    且剥掉 .tmp 后最后一个 `.` 之前的部分以 `_web` 结尾」。
    """
    if not name.endswith(_TMP_SUFFIX):
        return False
    body = name[:-len(_TMP_SUFFIX)]
    idx = body.rfind(".")
    if idx <= 0:
        return False
    return body[:idx].endswith("_web")


def _sweep_stale_tmp(directory) -> int:
    """清理目录内自身产生且已超龄的 `*.tmp` 残骸，返回删除数量（幂等、尽力而为）。

    why: 见 _atomic_write 的「已知取舍」—— 只有 SIGKILL / 断电这类 finally 不执行的
    场景才会留下残骸，这里给出兜底，避免 uploads/ 随崩溃次数缓慢累积垃圾。

    硬约束（防误删）：
    - 只处理本模块自身命名模式的临时文件（见 _is_own_tmp_name），其他 `*.tmp` 不碰；
    - 只删 mtime 早于 `_STALE_TMP_AGE_SECONDS` 的 —— 正常转码的临时文件只存活毫秒级，
      年龄阈值确保绝不误删其他线程正在写入的中间态；
    - 每进程每目录只扫一次（`_TMP_SWEPT_DIRS`），不在请求热路径上反复 iterdir；
      节流只在「iterdir 列目录成功」之后才登记 —— 若目录枚举本身因权限/IO 失败，
      不得把该目录记成已扫（否则本进程内再也不重试，兜底形同失效）；
    - 只删文件、不递归、不碰任何 `*_web.jpg` 与源原图；删除失败仅 WARN，绝不影响转码。
    """
    base = Path(str(directory))
    key = str(base)
    with _TMP_SWEEP_GUARD:
        if key in _TMP_SWEPT_DIRS:
            return 0

    # 先真正枚举目录：失败则直接返回且不登记，保证下次调用仍会尝试（带失败回退的节流）
    try:
        entries = list(base.iterdir())
    except OSError as e:
        logger.warning("[WARN] 扫描陈旧临时文件失败 %s: %s", base, e)
        return 0

    # 枚举成功才登记「已扫」；单个文件的 stat/unlink 失败不影响本次扫描的有效性
    with _TMP_SWEEP_GUARD:
        _TMP_SWEPT_DIRS.add(key)

    removed = 0
    cutoff = time.time() - _STALE_TMP_AGE_SECONDS
    for p in entries:
        if not _is_own_tmp_name(p.name):
            continue
        try:
            if not p.is_file() or p.stat().st_mtime >= cutoff:
                continue
            p.unlink()
            removed += 1
        except OSError as e:
            logger.warning("[WARN] 清理陈旧临时文件失败 %s: %s", p, e)
    if removed:
        logger.info("[INFO] 清理预览转码陈旧临时文件 %d 个: %s", removed, base)
    return removed


def web_preview_path(src_path) -> Path:
    """返回可直接交给前端渲染的路径。

    - 已是 Web 格式（jpg/jpeg/png/webp 等）→ 原路径，逐字节不做任何处理；
    - 非 Web 格式 → 源文件同目录的 `<stem>_web.jpg` 缓存副本（缺失/过期才转码）。
    """
    src = Path(str(src_path))
    if not is_non_web_image_path(src):
        return src

    dst = preview_cache_path(src)
    # 调用入口的幂等兜底：清掉上次进程被硬中断留下的自身临时文件残骸（每进程每目录
    # 只扫一次，不在热路径重复开销），再做缓存判定
    _sweep_stale_tmp(dst.parent)
    if _cache_hit(src, dst):
        return dst

    with _lock_for(str(dst)):
        # 双重检查：等锁期间可能已被其他线程/请求写好
        if _cache_hit(src, dst):
            return dst
        _atomic_write(dst, transcode_to_jpeg_bytes(src))
    return dst


def referenced_preview_stems(image_paths) -> set:
    """从单据的 image_path 值集合算出「仍被引用」的预览缓存 stem 集合。

    why: 缓存清理必须先证明「已无任何单据引用该源文件」，否则会误删正被展示的图。
    以 stem 为判据（缓存名即 `<stem>_web.jpg`），调用方一次取全量 image_path 即可
    覆盖所有单据，无需对每个缓存做模糊匹配；只统计非 Web 格式路径 —— Web 格式
    （jpg/png/webp）不会生成预览副本，计入只会让判定失真。
    """
    stems = set()
    for p in image_paths or ():
        if not is_non_web_image_path(p):
            continue
        stem = preview_stem(p)
        if stem:
            stems.add(stem)
    return stems


def remove_preview_cache(src_path) -> bool:
    """删除某个源文件对应的预览缓存副本；源文件本体一律不碰。

    why: replace-image 换图后旧源文件的 `<stem>_web.jpg` 再无请求路径可达（新
    image_path 指向新的 db.new_id() 文件名），需定向清理以免随重拍次数累积。
    「是否确实已无引用」由调用方判定（见 referenced_preview_stems），本函数只执行
    删除。缓存不存在或删除失败均返回 False —— 清理是尽力而为，不得影响请求主流程。
    """
    cache = preview_cache_path(src_path)
    if cache is None:
        return False
    try:
        if cache.is_file():
            cache.unlink()
            return True
    except OSError as e:
        logger.warning("[WARN] 清理预览缓存失败 %s: %s", cache, e)
    return False


def sweep_orphan_previews(upload_dir, referenced_stems=None):
    """扫描 uploads 目录清理无主预览缓存，返回被删路径列表（幂等、只碰 `*_web.jpg`）。

    why: 定向清理只能覆盖「调用方知道旧源路径」的场景（replace-image 等）；换图后
    进程崩溃、外部直接改库/删文件等情况仍会留下无主缓存，长期在 uploads 累积。
    故提供启动期兜底扫描。

    判定（任一成立即视为无主，两个方向）：
    - 该 stem 已不存在任何非 Web 源文件（源文件没了，缓存永远不可能被请求到）；
    - `referenced_stems` 非 None 且该 stem 不在其中（没有任何单据引用该源文件）。

    硬约束：只处理 `*_web.jpg` 且必须是文件；源原图（.heic/.jpg/.png 等）一律不删；
    `referenced_stems` 为 None 时退化为仅按第一条判据，绝不因「引用信息缺失」而删。

    引用信息一致性保护：只有当 `referenced_stems` 至少有一个 stem 能在本目录找到源
    文件时，才认为这份引用集合描述的就是本目录。否则（库与目录不匹配、库为空、测试
    用临时库等）统一按「无引用信息」处理 —— 宁可留下无主缓存，也不能把有效缓存删光。
    """
    removed = []
    base = Path(str(upload_dir))
    if not base.is_dir():
        return removed

    caches = []
    present_stems = set()
    try:
        for p in base.iterdir():
            if not p.is_file():
                continue
            if p.name.endswith(_PREVIEW_SUFFIX):
                caches.append(p)
            elif is_non_web_image_path(p.name):
                present_stems.add(p.stem)
    except OSError as e:
        logger.warning("[WARN] 扫描预览缓存失败 %s: %s", base, e)
        return removed

    if referenced_stems is not None:
        if not (present_stems & set(referenced_stems)):
            logger.info("[INFO] 预览缓存引用集合与本目录无交集，退化为仅按源文件存在性清理")
            referenced_stems = None

    for cache in sorted(caches):
        # 缓存名是 <stem>_web.jpg，缓存 stem 需剥掉整个后缀（不是 splitext 的扩展名）
        stem = cache.name[:-len(_PREVIEW_SUFFIX)]
        if not stem:
            continue
        if stem in present_stems and (referenced_stems is None or stem in referenced_stems):
            continue  # 源文件仍在，且（无引用信息 或 仍被引用）→ 保留
        try:
            cache.unlink()
            removed.append(cache)
        except OSError as e:
            logger.warning("[WARN] 清理无主预览缓存失败 %s: %s", cache, e)
    return removed
