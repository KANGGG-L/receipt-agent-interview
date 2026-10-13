# -*- coding: utf-8 -*-
"""T3（P2）回归：本地解码失败时不得把原始二进制冒充 JPEG 发给 VLM。

why: 修复前 `extract_chain._image_data_url` 把 PIL 整段包在 try 里，任何异常都
`except: pass` 后直接 base64(原文件) 并按其扩展名猜 mime。对 heic/heif/tif/tiff
这类非 Web 格式，本机通常没有 pillow-heif / TIFF 解码器，于是把损坏/不可解码的
二进制冒充 image/jpeg 发给 VLM，触发上游 400/500；而这类失败会被误读成
"模型能力不行"。修复后：
  - 非 Web 格式解码失败 → 抛 ImageDecodeError（可读原因），不再发送损坏数据；
  - Web 格式（mime 可信）保留原文件回退，并受体积上限约束；
  - 超像素上限 → 受控拒绝（W-SEC-3：删除了旧的「临时置 None 放宽重试」，见第 4 节）；
  - 正常 jpg 路径逐字节不变（本文件用旧实现复刻做对照）。
"""

import base64
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from PIL import Image

from app.chains import extract_chain
from app.chains.extract_chain import _image_data_url

# 用 getattr 取异常类型而非直接 import：这样"回滚本次修复"后，本文件的用例会以
# 断言失败（DID NOT RAISE）暴露回归，而不是在收集期抛 ImportError——后者无法
# 证明这些用例真的锁住了行为。
ImageDecodeError = getattr(extract_chain, "ImageDecodeError", None)

# 一段肯定打不开的字节（PIL 会抛 UnidentifiedImageError）
_GARBAGE = b"this is definitely not a real image payload"


def test_image_decode_error_type_is_exported():
    """P2 修复必须导出可读的图片解码异常类型。"""
    assert ImageDecodeError is not None, "extract_chain 应导出 ImageDecodeError"
    assert issubclass(ImageDecodeError, Exception)


def _legacy_pil_encode(image_path):
    """复刻修复前的 PIL 编码块，作为「正常 jpg 逐字节不变」的对照基准。"""
    from PIL import Image as _Image, ImageOps
    with _Image.open(image_path) as img:
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass
        max_side = max(img.size) if img.size[0] and img.size[1] else 0
        if max_side > 1000:
            scale = 1000.0 / max_side
            img = img.resize(
                (max(1, int(img.size[0] * scale)), max(1, int(img.size[1] * scale))),
                _Image.BILINEAR,
            )
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _write(path, data):
    with open(path, "wb") as f:
        f.write(data)
    return str(path)


def _data_url_payload(url):
    prefix, _, b64 = url.partition(",")
    assert prefix.startswith("data:") and prefix.endswith(";base64"), prefix
    return prefix[: -len(";base64")], base64.b64decode(b64)


# ---------------------------------------------------------------
# 1. 非 Web 格式解码失败 → 明确报错，而不是发出损坏数据
# ---------------------------------------------------------------
def test_fake_heic_raises_readable_error(tmp_path):
    assert ImageDecodeError is not None
    p = _write(tmp_path / "fake_receipt.heic", _GARBAGE)
    with pytest.raises(ImageDecodeError) as ei:
        _image_data_url(p)
    msg = str(ei.value)
    assert "图片解码失败" in msg
    assert "已阻止向识别模型发送损坏数据" in msg
    assert "fake_receipt.heic" in msg
    # 明确说明是非 Web 格式与解码器原因，便于排查
    assert "非 Web 格式" in msg
    assert "原因：" in msg and "Error" in msg


@pytest.mark.parametrize("ext", ["heic", "heif", "tif", "tiff"])
def test_all_non_web_exts_raise(tmp_path, ext):
    assert ImageDecodeError is not None
    p = _write(tmp_path / f"bad.{ext}", _GARBAGE)
    with pytest.raises(ImageDecodeError):
        _image_data_url(p)


def test_legacy_behavior_would_have_sent_garbage(tmp_path):
    """反证：同一输入在修复前的回退分支下会被当成 JPEG 发出去（证明用例非恒真）。"""
    p = _write(tmp_path / "fake_receipt.heic", _GARBAGE)
    legacy_url = "data:image/jpeg;base64," + base64.b64encode(_GARBAGE).decode()
    mime, payload = _data_url_payload(legacy_url)
    assert mime == "data:image/jpeg"  # .heic 被误标成 JPEG
    assert payload == _GARBAGE  # 原始二进制原样发出
    # 新实现必须拒绝同一输入
    assert ImageDecodeError is not None
    with pytest.raises(ImageDecodeError):
        _image_data_url(p)


def test_undecodable_non_web_file_no_longer_leaks_bytes(tmp_path):
    """确认异常信息里不夹带二进制内容（避免日志/前端提示里出现乱码）。"""
    assert ImageDecodeError is not None
    p = _write(tmp_path / "broken.tif", _GARBAGE)
    with pytest.raises(ImageDecodeError) as ei:
        _image_data_url(p)
    assert _GARBAGE.decode("ascii", "ignore") not in str(ei.value)


# ---------------------------------------------------------------
# 2. Web 格式保留原文件回退，mime 正确
# ---------------------------------------------------------------
@pytest.mark.parametrize(
    "ext,mime",
    [("jpg", "data:image/jpeg"), ("jpeg", "data:image/jpeg"),
     ("png", "data:image/png"), ("webp", "data:image/webp")],
)
def test_web_format_fallback_keeps_raw_bytes_and_mime(tmp_path, ext, mime):
    p = _write(tmp_path / f"broken.{ext}", _GARBAGE)
    url = _image_data_url(p)
    got_mime, payload = _data_url_payload(url)
    assert got_mime == mime
    assert payload == _GARBAGE, "Web 格式回退必须与改动前逐字节一致"


def test_web_fallback_rejects_oversized_original(tmp_path, monkeypatch):
    assert ImageDecodeError is not None
    monkeypatch.setattr(extract_chain, "_MAX_RAW_FALLBACK_BYTES", 4)
    p = _write(tmp_path / "broken.jpg", _GARBAGE)
    with pytest.raises(ImageDecodeError) as ei:
        _image_data_url(p)
    assert "原文件过大" in str(ei.value)
    assert "已阻止全量 base64 回退" in str(ei.value)


# ---------------------------------------------------------------
# 3. 正常 jpg 路径逐字节不变
# ---------------------------------------------------------------
def test_normal_jpg_byte_identical_to_legacy(tmp_path):
    p = str(tmp_path / "normal.jpg")
    Image.new("RGB", (240, 120), (200, 30, 30)).save(p, format="JPEG", quality=90)
    assert _image_data_url(p) == _legacy_pil_encode(p)


def test_normal_jpg_still_limited_to_max_side(tmp_path):
    """限边 1000 必须保留（原行为），否则超大原图会全量发送。"""
    p = str(tmp_path / "big.jpg")
    Image.new("RGB", (2400, 1200), (10, 200, 10)).save(p, format="JPEG", quality=90)
    _, payload = _data_url_payload(_image_data_url(p))
    with Image.open(io.BytesIO(payload)) as img:
        assert max(img.size) <= extract_chain._MAX_IMAGE_SIDE
        assert img.size == (1000, 500)


def test_normal_tiff_that_decodes_is_accepted(tmp_path):
    """tif 属非 Web 格式，但本机 PIL 能解码时不得误报错（只拦"解码失败"）。"""
    p = str(tmp_path / "ok.tif")
    Image.new("RGB", (80, 40), (1, 2, 3)).save(p, format="TIFF")
    mime, payload = _data_url_payload(_image_data_url(p))
    assert mime == "data:image/jpeg"
    with Image.open(io.BytesIO(payload)) as img:
        assert img.format == "JPEG"


# ---------------------------------------------------------------
# 4. W-SEC-3：超像素上限受控拒绝，且全程不改写 PIL 全局状态
#    （旧的「临时置 None 再还原」的放宽重试已删除——那是进程级竞态）
# ---------------------------------------------------------------
def test_oversized_pixel_image_rejected_and_global_untouched(tmp_path, monkeypatch):
    """超过进程级像素上限（image_web.MAX_IMAGE_PIXELS 单一口径）→ ImageDecodeError。

    反证（mutation 说明）：若有人把「放宽重试」加回来，本用例以 DID NOT RAISE
    暴露回归；若实现重新出现 Image.MAX_IMAGE_PIXELS 全局写入，末尾的全局值断言
    失败。
    """
    from PIL import Image as PILImage
    from app.services import image_web

    p = str(tmp_path / "huge_pixels.jpg")
    Image.new("RGB", (300, 300), (7, 7, 7)).save(p, format="JPEG", quality=90)

    # 把进程级像素上限压到极小（唯一口径是 image_web.MAX_IMAGE_PIXELS）
    monkeypatch.setattr(image_web, "MAX_IMAGE_PIXELS", 10)
    before = PILImage.MAX_IMAGE_PIXELS
    with pytest.raises(ImageDecodeError) as ei:
        _image_data_url(p)
    assert "像素" in str(ei.value) and "上限" in str(ei.value)
    # 全程不改写 PIL 全局（旧实现会在救援路径临时置 None）
    assert PILImage.MAX_IMAGE_PIXELS == before


def test_pillow_bomb_error_wrapped_as_decode_error(tmp_path, monkeypatch):
    """声明维度触发 Pillow DecompressionBombError 时同样受控拒绝（不裸抛）。"""
    from PIL import Image as PILImage

    p = str(tmp_path / "bomb.jpg")
    Image.new("RGB", (300, 300), (7, 7, 7)).save(p, format="JPEG", quality=90)
    # 300*300 > 2*10 → Image.open 即抛 DecompressionBombError
    monkeypatch.setattr(PILImage, "MAX_IMAGE_PIXELS", 10)
    with pytest.raises(ImageDecodeError):
        _image_data_url(p)


def test_pixel_limit_restored_even_when_decode_fails(tmp_path, monkeypatch):
    """Web 格式解码失败的原始回退路径保持，且不改写 PIL 全局。"""
    from PIL import Image as PILImage

    p = _write(tmp_path / "still_broken.jpg", _GARBAGE)
    monkeypatch.setattr(PILImage, "MAX_IMAGE_PIXELS", 12)
    before = PILImage.MAX_IMAGE_PIXELS
    url = _image_data_url(p)  # Web 格式 → 回退原文件，不抛错
    assert _data_url_payload(url)[1] == _GARBAGE
    assert PILImage.MAX_IMAGE_PIXELS == before


# ---------------------------------------------------------------
# 5. N3：解码失败必须在 extract_receipt 内短路
#    （不抛异常、不进降级分支、不调用任何引擎）
# ---------------------------------------------------------------
class _CountingModel:
    """统计 invoke 次数的假引擎（鸭子类型：识别链路只用到 kind 与 invoke）。"""

    kind = "openai"

    def __init__(self):
        self.calls = 0

    def invoke(self, prompt):  # 本组用例断言它一次都不该被调用
        self.calls += 1
        return '{"vendor": "不应被调用"}'


def _fake_heic(tmp_path):
    """PIL 打不开的假 .heic（非 Web 格式 → 解码失败必须显式报错而不是发损坏数据）。"""
    return _write(tmp_path / "undecodable.heic", _GARBAGE)


def _valid_png(tmp_path):
    p = str(tmp_path / "ok.png")
    Image.new("RGB", (40, 20), (9, 9, 9)).save(p, format="PNG")
    return p


def test_extract_receipt_returns_error_without_calling_engine(tmp_path, monkeypatch):
    """N3：解码失败 → 返回可读 error dict，且引擎 invoke 次数为 0（不降级重试）。"""
    model = _CountingModel()
    # 兜底：万一短路失效进入了降级分支，也不许真的去构建/调用备用引擎
    # （否则会联网、计费，并把「用例失败」伪装成网络错误）。
    import app.llm as llm_mod

    def _must_not_fallback(*a, **kw):
        raise AssertionError("图片解码失败时不得触发降级引擎构建")

    monkeypatch.setattr(llm_mod, "_build", _must_not_fallback)

    ret = extract_chain.extract_receipt(
        _fake_heic(tmp_path), model=model, enable_canary=False)

    assert model.calls == 0, "解码失败不得调用任何引擎（首选与备用都不许）"
    assert ret["data"] is None
    assert ret["raw"] == ""
    assert "图片解码失败" in ret["error"]
    assert "已阻止向识别模型发送损坏数据" in ret["error"]
    # 不得被伪装成「引擎调用异常 → 已自动降级」
    assert "fallback_triggered" not in ret
    assert "VLM 调用失败" not in ret["error"]
    assert "降级" not in ret["error"]
    # 零 token / 零成本：本地失败不应产生任何计费口径
    assert ret["cost_hkd"] == 0.0
    assert ret["token_usage"]["total_tokens"] == 0
    assert ret["engine"] == "openai"


def test_extract_receipt_does_not_raise_on_undecodable_image(tmp_path):
    """N3 反证配套：修复前 ImageDecodeError 会穿透 extract_receipt（调用方拿不到 dict）。"""
    ret = extract_chain.extract_receipt(
        _fake_heic(tmp_path), model=_CountingModel(), enable_canary=False)
    assert isinstance(ret, dict)


def test_decode_failure_result_shape_matches_engine_failure(tmp_path, monkeypatch):
    """N3/N4：解码失败返回结构与「引擎调用失败」同构（Job 层无需单开分支）。

    N4 起解码失败额外携带 deterministic_error=True 标记（供 supervisor 直接结束
    重试阶梯）；除此之外两者键集必须一致，且引擎失败不得携带该标记
    （否则会被当成确定性失败、破坏既有重试语义）。
    """
    # 关闭识别引擎的自动兜底，确保第二段走的确实是「首选引擎失败」返回分支。
    # 历史注记：兜底原为本机 CodeBuddy CLI（判据是模型 kind != "codebuddy"），
    # 已于 2026-09-02 弃用，现为 DashScope 官方通道兜底。

    decode_ret = extract_chain.extract_receipt(
        _fake_heic(tmp_path), model=_CountingModel(), enable_canary=False)

    class _BoomModel:
        kind = "openai"  # 兜底已关闭 → 直接走"调用失败"返回

        def invoke(self, prompt):
            raise RuntimeError("engine boom")

    engine_ret = extract_chain.extract_receipt(
        _valid_png(tmp_path), model=_BoomModel(), enable_canary=False)
    assert "VLM 调用失败" in engine_ret["error"]
    # 唯一差异键就是确定性标记；其余键集完全一致
    assert decode_ret.get("deterministic_error") is True
    assert "deterministic_error" not in engine_ret
    # 2026-10-10 契约对齐：2e42ea3 起引擎失败结果新增 adapted_params（自适应参数观测）。
    # 确定性解码失败不发起 LLM 请求，无 adapted_params 属正确语义——两态键差恒为
    # {deterministic_error}（仅 decode 有）与 {adapted_params}（仅 engine 有）。
    assert set(decode_ret.keys()) ^ set(engine_ret.keys()) == \
        {"deterministic_error", "adapted_params"}
