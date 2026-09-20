# -*- coding: utf-8 -*-
"""M4（P2 第三份副本）回归：llm.py 的 _path_to_data_url 不再自带裸 base64 实现。

why: T3 修了 extract_chain._image_data_url、R2 修了 audit_chain 的本地副本，但
llm._path_to_data_url 仍是同一根因的第三份实现——PIL 整段包在 try 里，任何异常都
`except: pass` 后直接 base64(原文件) 并按扩展名猜 mime（.heic 被归到 image/jpeg）。
本机通常没有 pillow-heif / TIFF 解码器，于是 OpenAIChatModel 这条腿会把不可解码的
原始二进制冒充 JPEG 发给上游（400/500），且这类失败会被误读成"模型能力不行"。
修复后 llm 的这条路径统一复用 extract_chain 的实现（单一实现、单一修复），本文件锁定：
  1. 全仓 VLM 数据通道（app/llm.py + app/chains/*.py）只剩一份 base64 图片编码实现；
  2. llm._path_to_data_url 确实是"转发"而非另写一份（monkeypatch 可观测）；
  3. 识别腿 / 审核腿 / OpenAI 兼容转 data URL 三条路径对假 .heic 行为一致（同一异常）；
  4. 正常 Web 格式（jpg/jpeg/png/webp）与改动前逐字节一致。

scope 说明：demo/app/api_evalset.py 的 _thumbnail_b64 也用 base64，但它是浏览器展示用
缩略图（promote/评测仍用原图），从不发给任何模型，因此不属于 P2 的数据通道，本任务不动。

全程不碰 DB、不发网络请求。
"""

import base64
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from PIL import Image

from app import llm
from app.chains import audit_chain, extract_chain
from app.models import EngineConfig, ReceiptData, ReceiptItem

# 用 getattr 取异常类型而非直接 import：这样"回滚本次修复"后，本文件的用例会以
# 断言失败（DID NOT RAISE）暴露回归，而不是在收集期抛 ImportError。
ImageDecodeError = getattr(extract_chain, "ImageDecodeError", None)

# 一段肯定打不开的字节（PIL 会抛 UnidentifiedImageError）
_GARBAGE = b"this is definitely not a real image payload"

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")


def _write(path, data):
    with open(path, "wb") as f:
        f.write(data)
    return str(path)


def _data_url_payload(url):
    prefix, _, b64 = url.partition(",")
    assert prefix.startswith("data:") and prefix.endswith(";base64"), prefix
    return prefix[: -len(";base64")], base64.b64decode(b64)


def _legacy_path_to_data_url(path):
    """复刻修复前 llm._path_to_data_url 的实现，作为反证与逐字节对照基准。"""
    try:
        from PIL import Image as _Image, ImageOps
        with _Image.open(path) as img:
            try:
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass
            max_side = max(img.size) if img.size[0] and img.size[1] else 0
            if max_side > 1000:
                scale = 1000.0 / max_side
                new_w = max(1, int(img.size[0] * scale))
                new_h = max(1, int(img.size[1] * scale))
                img = img.resize((new_w, new_h), _Image.BILINEAR)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85, optimize=True)
            b64 = base64.b64encode(buf.getvalue()).decode()
            return "data:image/jpeg;base64," + b64
    except Exception:
        pass
    ext = os.path.splitext(path)[1].lstrip(".").lower() or "jpg"
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "webp": "image/webp", "gif": "image/gif"}.get(ext, "image/jpeg")
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:{mime};base64,{b64}"


def _make_data():
    return ReceiptData(
        doc_form="thermal",
        vendor="測試供應商",
        date="2026-01-01",
        items=[ReceiptItem(name="魚", qty=1, unit="斤", unit_price=10, amount=10)],
        total=10.0,
        payment_marked=False,
        confidence=0.9,
    )


def _prompt_urls(prompt):
    urls = []
    for msg in prompt:
        content = getattr(msg, "content", None)
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    urls.append(part["image_url"]["url"])
    return urls


# ---------------------------------------------------------------
# 1. 单一实现：VLM 数据通道不得再留裸 base64 副本
# ---------------------------------------------------------------
def test_vlm_channel_has_single_base64_image_encoder():
    """app/llm.py + app/chains/*.py 里 b64encode 只允许出现在 extract_chain.py。"""
    targets = [os.path.join(_APP_DIR, "llm.py")]
    chains_dir = os.path.join(_APP_DIR, "chains")
    targets += [os.path.join(chains_dir, n)
                for n in sorted(os.listdir(chains_dir)) if n.endswith(".py")]
    offenders = []
    for path in targets:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        if "b64encode" in src:
            offenders.append(os.path.basename(path))
    assert offenders == ["extract_chain.py"], f"图片编码实现应唯一，实际: {offenders}"


def test_path_to_data_url_source_is_a_thin_delegator():
    """源码层面：llm._path_to_data_url 必须转发给 extract_chain，且自己不做 base64。"""
    with open(os.path.join(_APP_DIR, "llm.py"), "r", encoding="utf-8") as f:
        src = f.read()
    # 只截到本函数体（到下一个顶层 def / class 为止），避免把后续代码的用法误算进来
    body = re.search(r"^def _path_to_data_url\(.*?(?=^def |^class |\Z)", src,
                     flags=re.M | re.S)
    assert body, "未找到 _path_to_data_url 定义"
    code = body.group(0)
    assert "extract_chain._image_data_url" in code, "应复用 extract_chain 的实现"
    assert "b64encode" not in code, "不得再自带裸 base64 回退"


def test_path_to_data_url_delegates_to_extract_chain(tmp_path, monkeypatch):
    """行为层面：monkeypatch extract_chain 的实现后 llm 的返回值随之变化。"""
    p = str(tmp_path / "ok.jpg")
    Image.new("RGB", (40, 20), (5, 6, 7)).save(p, format="JPEG", quality=90)
    sentinel = "data:image/jpeg;base64,U0VOVElORUw="
    monkeypatch.setattr(extract_chain, "_image_data_url", lambda path: sentinel)
    assert llm._path_to_data_url(p) == sentinel


# ---------------------------------------------------------------
# 2. 非 Web 格式解码失败 → 明确报错，而不是发出损坏数据
# ---------------------------------------------------------------
def test_fake_heic_raises_readable_error(tmp_path):
    assert ImageDecodeError is not None
    p = _write(tmp_path / "fake_receipt.heic", _GARBAGE)
    with pytest.raises(ImageDecodeError) as ei:
        llm._path_to_data_url(p)
    msg = str(ei.value)
    assert "图片解码失败" in msg
    assert "已阻止向识别模型发送损坏数据" in msg
    assert "fake_receipt.heic" in msg
    assert "非 Web 格式" in msg


@pytest.mark.parametrize("ext", ["heic", "heif", "tif", "tiff"])
def test_all_non_web_exts_raise(tmp_path, ext):
    assert ImageDecodeError is not None
    p = _write(tmp_path / f"bad.{ext}", _GARBAGE)
    with pytest.raises(ImageDecodeError):
        llm._path_to_data_url(p)


def test_legacy_impl_would_have_sent_garbage(tmp_path):
    """反证：同一输入在修复前的实现下会被当成 JPEG 原样发出（证明用例非恒真）。"""
    p = _write(tmp_path / "fake_receipt.heic", _GARBAGE)
    mime, payload = _data_url_payload(_legacy_path_to_data_url(p))
    assert mime == "data:image/jpeg", ".heic 曾被误标成 JPEG"
    assert payload == _GARBAGE, "原始二进制曾被原样发出"
    # 新实现必须拒绝同一输入
    assert ImageDecodeError is not None
    with pytest.raises(ImageDecodeError):
        llm._path_to_data_url(p)


def test_error_message_does_not_leak_binary(tmp_path):
    assert ImageDecodeError is not None
    p = _write(tmp_path / "broken.tif", _GARBAGE)
    with pytest.raises(ImageDecodeError) as ei:
        llm._path_to_data_url(p)
    assert _GARBAGE.decode("ascii", "ignore") not in str(ei.value)


# ---------------------------------------------------------------
# 3. 三条路径（识别 / 审核 / OpenAI 兼容转 data URL）行为一致
# ---------------------------------------------------------------
def test_three_paths_consistent_for_fake_heic(tmp_path):
    assert ImageDecodeError is not None
    p = _write(tmp_path / "fake_receipt.heic", _GARBAGE)
    models = []
    # 识别腿
    with pytest.raises(ImageDecodeError):
        extract_chain._image_data_url(p)
    models.append("extract_chain")
    # 审核腿（vlm prompt 组装）
    with pytest.raises(ImageDecodeError):
        audit_chain.build_audit_prompt(p, _make_data())
    models.append("audit_chain")
    # OpenAI 兼容转 data URL
    with pytest.raises(ImageDecodeError):
        llm._path_to_data_url(p)
    models.append("llm")
    assert models == ["extract_chain", "audit_chain", "llm"]


def test_three_paths_identical_for_normal_jpg(tmp_path):
    """正常 jpg：三条路径拿到逐字节相同的 data URL（同一实现，不是三份近似实现）。"""
    p = str(tmp_path / "ok.jpg")
    Image.new("RGB", (120, 60), (11, 22, 33)).save(p, format="JPEG", quality=90)
    from_llm = llm._path_to_data_url(p)
    from_extract = extract_chain._image_data_url(p)
    from_audit = _prompt_urls(audit_chain.build_audit_prompt(p, _make_data()))[0]
    assert from_llm == from_extract == from_audit
    assert from_llm == _legacy_path_to_data_url(p), "正常 jpg 必须与改动前逐字节一致"


# ---------------------------------------------------------------
# 4. Web 格式保留原文件回退，mime 与 ext 表一致（且与改动前一致）
# ---------------------------------------------------------------
@pytest.mark.parametrize(
    "ext,mime",
    [("jpg", "data:image/jpeg"), ("jpeg", "data:image/jpeg"),
     ("png", "data:image/png"), ("webp", "data:image/webp")],
)
def test_web_format_fallback_matches_legacy_bytes(tmp_path, ext, mime):
    p = _write(tmp_path / f"broken.{ext}", _GARBAGE)
    got_mime, payload = _data_url_payload(llm._path_to_data_url(p))
    legacy_mime, legacy_payload = _data_url_payload(_legacy_path_to_data_url(p))
    assert got_mime == mime == legacy_mime
    assert payload == _GARBAGE == legacy_payload


def test_web_fallback_rejects_oversized_original(tmp_path, monkeypatch):
    assert ImageDecodeError is not None
    monkeypatch.setattr(extract_chain, "_MAX_RAW_FALLBACK_BYTES", 4)
    p = _write(tmp_path / "broken.jpg", _GARBAGE)
    with pytest.raises(ImageDecodeError) as ei:
        llm._path_to_data_url(p)
    assert "原文件过大" in str(ei.value)
    assert "已阻止全量 base64 回退" in str(ei.value)


# ---------------------------------------------------------------
# 5. OpenAIChatModel 的 prompt 组装链路（_lc_to_openai）同样被覆盖
# ---------------------------------------------------------------
def test_lc_to_openai_raises_for_local_heic_path(tmp_path):
    assert ImageDecodeError is not None
    from langchain_core.messages import HumanMessage

    p = _write(tmp_path / "fake_receipt.heic", _GARBAGE)
    msg = HumanMessage(content=[
        {"type": "text", "text": "识别这张单据"},
        {"type": "image_url", "image_url": {"url": p}},
    ])
    with pytest.raises(ImageDecodeError):
        llm._lc_to_openai(msg)


def test_lc_to_openai_converts_local_jpg_path(tmp_path):
    from langchain_core.messages import HumanMessage

    p = str(tmp_path / "ok.jpg")
    Image.new("RGB", (80, 40), (3, 4, 5)).save(p, format="JPEG", quality=90)
    msg = HumanMessage(content=[
        {"type": "text", "text": "识别这张单据"},
        {"type": "image_url", "image_url": {"url": p}},
    ])
    content = llm._lc_to_openai(msg)["content"]
    url = [b for b in content if b.get("type") == "image_url"][0]["image_url"]["url"]
    assert url == extract_chain._image_data_url(p)


def test_lc_to_openai_keeps_existing_data_url_untouched():
    """已是 data URL 时不得重新解码编码（避免无谓的二次压缩与成本）。"""
    from langchain_core.messages import HumanMessage

    existing = "data:image/jpeg;base64,QUJD"
    msg = HumanMessage(content=[{"type": "image_url", "image_url": {"url": existing}}])
    content = llm._lc_to_openai(msg)["content"]
    assert content[0]["image_url"]["url"] == existing
