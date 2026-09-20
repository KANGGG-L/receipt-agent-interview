# -*- coding: utf-8 -*-
"""R2（P2）回归：审核腿复用识别腿的图片编码实现，不再把损坏二进制冒充 JPEG 发出。

why: T3 只修了 extract_chain 的一份 _image_data_url；audit_chain 还留着一份本地副本
（裸 base64 + 按扩展名猜 mime，.heic/.tif 被标成 image/jpeg）。当 audit_mode 配成
vlm/ondemand 时审核腿会重演同类失败：把不可解码的原始二进制发给上游 → 400/500，
且会被误读成"模型能力不行"。本文件锁定三件事：
  1. 全仓 app/chains 下只有一份 _image_data_url 实现；
  2. 审核 prompt 确实走的是 extract_chain 的实现（monkeypatch 可观测）；
  3. vlm / ondemand 路径下 .heic 解码失败 → 优雅 skip，且模型一次都没被调用。

全程不碰 DB、不发网络请求（模型为假实现）。
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app.chains import audit_chain, extract_chain
from app.models import EngineConfig, ReceiptData, ReceiptItem

# 一段肯定打不开的字节（PIL 会抛 UnidentifiedImageError）
_GARBAGE = b"this is definitely not a real image payload"

ImageDecodeError = getattr(extract_chain, "ImageDecodeError", None)


class _FakeAuditModel:
    """假审核模型：只记录是否被调用，绝不发网络请求。"""

    kind = "fake"

    def __init__(self):
        self.calls = 0
        self.last_prompt = None

    def invoke(self, prompt):
        self.calls += 1
        self.last_prompt = prompt
        return "{}"


def _write(path, data):
    with open(path, "wb") as f:
        f.write(data)
    return str(path)


def _make_data(confidence=0.9):
    return ReceiptData(
        doc_form="thermal",
        vendor="測試供應商",
        date="2026-01-01",
        items=[ReceiptItem(name="魚", qty=1, unit="斤", unit_price=10, amount=10)],
        total=10.0,
        payment_marked=False,
        confidence=confidence,
    )


def _prompt_urls(prompt):
    """取出多模态 prompt 里的 image_url 值。"""
    urls = []
    for msg in prompt:
        content = getattr(msg, "content", None)
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    urls.append(part["image_url"]["url"])
    return urls


# ---------------------------------------------------------------
# 1. 单一实现：audit_chain 不得再自带副本
# ---------------------------------------------------------------
def test_only_one_image_data_url_implementation_in_chains():
    """app/chains 下只允许存在一份 _image_data_url 实现（消除 P2 的另一份副本）。"""
    chains_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "chains")
    defining = []
    for name in sorted(os.listdir(chains_dir)):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(chains_dir, name), "r", encoding="utf-8") as f:
            src = f.read()
        if re.search(r"^def _image_data_url\(", src, flags=re.M):
            defining.append(name)
    assert defining == ["extract_chain.py"], f"实现应唯一，实际: {defining}"
    # 审核模块不再绑定该名字（否则说明副本还在）
    assert not hasattr(audit_chain, "_image_data_url")


def test_legacy_local_copy_would_have_sent_garbage(tmp_path):
    """反证：旧副本对同一输入会产出 data:image/jpeg + 原始二进制（证明用例非恒真）。"""
    p = _write(tmp_path / "fake_receipt.heic", _GARBAGE)
    ext = os.path.splitext(p)[1].lstrip(".").lower()
    legacy_mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                   "webp": "image/webp"}.get(ext, "image/jpeg")
    assert legacy_mime == "image/jpeg"       # .heic 被误标成 JPEG
    import base64
    legacy_url = f"data:{legacy_mime};base64," + base64.b64encode(_GARBAGE).decode()
    assert base64.b64decode(legacy_url.partition(",")[2]) == _GARBAGE  # 原始二进制原样发出
    # 新实现必须拒绝同一输入（build_audit_prompt 复用 extract_chain）
    assert ImageDecodeError is not None
    with pytest.raises(ImageDecodeError):
        audit_chain.build_audit_prompt(p, _make_data())


# ---------------------------------------------------------------
# 2. 审核 prompt 确实复用 extract_chain 的实现
# ---------------------------------------------------------------
def test_audit_prompt_uses_extract_chain_impl(tmp_path, monkeypatch):
    """monkeypatch extract_chain 的实现后审核 prompt 随之变化 → 证明是同一份实现。"""
    p = str(tmp_path / "ok.jpg")
    from PIL import Image
    Image.new("RGB", (40, 20), (5, 6, 7)).save(p, format="JPEG", quality=90)

    sentinel = "data:image/jpeg;base64,U0VOVElORUw="
    monkeypatch.setattr(extract_chain, "_image_data_url", lambda path: sentinel)
    prompt = audit_chain.build_audit_prompt(p, _make_data())
    assert _prompt_urls(prompt) == [sentinel]


def test_normal_jpg_output_matches_extract_chain(tmp_path):
    """正常 jpg：审核腿与识别腿拿到逐字节相同的 data URL（复用而非另写一份）。"""
    p = str(tmp_path / "ok2.jpg")
    from PIL import Image
    Image.new("RGB", (60, 30), (9, 8, 7)).save(p, format="JPEG", quality=90)
    prompt = audit_chain.build_audit_prompt(p, _make_data())
    url = _prompt_urls(prompt)[0]
    assert url == extract_chain._image_data_url(p)
    assert url.startswith("data:image/jpeg;base64,")


# ---------------------------------------------------------------
# 3. vlm / ondemand 路径：.heic 解码失败 → 优雅 skip，不调用模型
# ---------------------------------------------------------------
def test_vlm_mode_undecodable_heic_skips_without_calling_model(tmp_path):
    assert ImageDecodeError is not None
    p = _write(tmp_path / "broken.heic", _GARBAGE)
    cfg = EngineConfig(audit_enabled=True, audit_mode="vlm")
    model = _FakeAuditModel()
    result = audit_chain.run_audit(p, _make_data(), model=model, config=cfg)
    assert result["skipped"] is True
    assert "audit_image_decode_failed" in result["reason"]
    assert "非 Web 格式" in result["reason"], "应给出可读的解码失败原因"
    assert model.calls == 0, "解码失败不得把损坏二进制发给审核模型"
    assert result["mode"] == "vlm"


@pytest.mark.parametrize("ext", ["heic", "heif", "tif", "tiff"])
def test_all_non_web_exts_skip_gracefully(tmp_path, ext):
    p = _write(tmp_path / f"broken.{ext}", _GARBAGE)
    cfg = EngineConfig(audit_enabled=True, audit_mode="vlm")
    model = _FakeAuditModel()
    result = audit_chain.run_audit(p, _make_data(), model=model, config=cfg)
    assert result["skipped"] is True
    assert "audit_image_decode_failed" in result["reason"]
    assert model.calls == 0


def test_ondemand_low_confidence_also_skips(tmp_path):
    """ondemand 低置信度会升级到 vlm，同样不得抛穿。"""
    p = _write(tmp_path / "broken.heic", _GARBAGE)
    cfg = EngineConfig(audit_enabled=True, audit_mode="ondemand")
    model = _FakeAuditModel()
    result = audit_chain.run_audit(p, _make_data(confidence=0.1), model=model, config=cfg)
    assert result["skipped"] is True
    assert "audit_image_decode_failed" in result["reason"]
    assert model.calls == 0


def test_ondemand_high_confidence_stays_text(tmp_path):
    """对照：高置信度走 text 模式，完全不读原图（即使文件损坏也不报错）。"""
    p = _write(tmp_path / "broken.heic", _GARBAGE)
    cfg = EngineConfig(audit_enabled=True, audit_mode="ondemand")
    model = _FakeAuditModel()
    result = audit_chain.run_audit(p, _make_data(confidence=0.99), model=model, config=cfg)
    assert result.get("skipped") is False
    assert result["mode"] == "text"
    assert model.calls == 0


def test_skip_reason_does_not_leak_binary(tmp_path):
    """异常信息里不夹带二进制内容（避免日志/前端提示出现乱码）。"""
    p = _write(tmp_path / "broken.tif", _GARBAGE)
    cfg = EngineConfig(audit_enabled=True, audit_mode="vlm")
    result = audit_chain.run_audit(p, _make_data(), model=_FakeAuditModel(), config=cfg)
    assert _GARBAGE.decode("ascii", "ignore") not in result["reason"]
