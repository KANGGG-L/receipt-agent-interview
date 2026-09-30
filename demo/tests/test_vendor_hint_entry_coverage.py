# -*- coding: utf-8 -*-
"""P2-T6 入口覆盖：重试 / 重解析从单据行派生 vendor_hint，批量入口显式判为范围外。

why：供应商记忆首轮注入只有 `vendor_hint` 一条通道，而此前**只有单张上传**入口会填充它
（B3 已接前端下拉）。重试（`/api/receipt/{id}/retry`）与换图重解析
（`/api/receipt/{id}/replace-image`）两条入口在派发识别 Job 时**完全不带 hint**，
尽管单据行上已经有 `supplier_name` —— 「已经知道供应商却仍不注入记忆」，首轮命中率被
白白压低。本文件把三条入口的归属钉死：

1. 重试 / 重解析：从 `row.supplier_name` 派生 hint（零成本、**不调预判模型**）；
   行上供应商为空时 hint 为空（不猜、不编造），流程照常成功。
2. 批量上传：**显式判为范围外**，不新增 `vendor_hint` 形参。理由见
   `api_receipts.upload_batch` 的 docstring（扁平 `list[UploadFile]` 契约无法表达
   「每张各自的供应商」，整批共用一个 hint 会给其余子单据注入错误先验；且前端批量
   路径不发送该字段，单方面加形参即「定义了没接线」）。本文件用签名断言守住这个
   结论：若将来有人加了形参却没接前端，这里会失败，逼一次明确的决定。

反证（已实测，见交付说明）：把 retry 与 replace-image 两处的派生调用换回常量空串，
本文件的 4 条派生用例即失败（hint 拿到 "" 而非供应商名）。

隔离：模块级先落 DB_PATH / RAG_DIR 到 /tmp，再用 conftest 的 autouse 夹具兜底。
识别 Job 全部打桩，不跑 LLM；只挂单个 router（不 import app.main），不触发 startup 自愈。
"""

import inspect
import logging
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/receipt_demo_vendor_hint_entry.db")
os.environ.setdefault("RAG_DIR", "/tmp/receipt_demo_vendor_hint_entry_rag")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import api_receipts, db

STAFF_HEADERS = {"X-Role": "staff"}


def _make_client():
    """只挂 api_receipts 路由：不 import app.main，避开 startup 自愈写库/建语料。"""
    app = FastAPI()
    app.include_router(api_receipts.router)
    return TestClient(app)


@pytest.fixture()
def job_calls(monkeypatch):
    """替换识别 Job 派发：记录入参，不真正跑识别管线。"""
    calls = []

    def _stub(image_path, vendor_hint="", receipt_id=None, tenant_id=None,
              image_guard=None):
        calls.append({"image_path": image_path, "vendor_hint": vendor_hint,
                      "receipt_id": receipt_id, "tenant_id": tenant_id})
        return ("job-stub-1", receipt_id)

    monkeypatch.setattr(api_receipts, "start_recognition_job", _stub)
    return calls


@pytest.fixture()
def precheck_bomb(monkeypatch):
    """预判模型调用探测器：重试/重解析/批量三条入口都不许调用它。

    why：重试与重解析场景单据行上已有供应商，另调一次小模型只是浪费额度与延迟；
    批量场景则连「供应商是谁」都无从确定。任一入口调用即视为回归。
    """
    from app.services import vendor_precheck
    calls = []

    def _bomb(*a, **k):
        calls.append((a, k))
        raise AssertionError("重试/重解析/批量入口不得调用供应商预判模型")

    monkeypatch.setattr(vendor_precheck, "predict_vendor", _bomb)
    return calls


def _mk_receipt(tmp_path, supplier_name="", status="edited"):
    """建一张带真实原图文件的单据（假 JPEG，>30 字节，通过画质前置检查）。"""
    img = tmp_path / "orig_receipt.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"x" * 60)
    rid = db.create_receipt(supplier_name=supplier_name, status=status)
    db.update_receipt(rid, image_path=str(img))
    return rid, img


def _sharp_png_bytes():
    """随机噪声图：Laplacian 方差远高于阈值，可通过画质门禁。"""
    import io
    import numpy as np
    from PIL import Image
    rng = np.random.default_rng(11)
    arr = rng.integers(0, 256, size=(64, 64), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


# -------------------------------------------------------------
# 1. 重试入口：从单据行派生 vendor_hint
# -------------------------------------------------------------
def test_retry_derives_vendor_hint_from_row(job_calls, precheck_bomb, tmp_path):
    rid, _ = _mk_receipt(tmp_path, supplier_name="德利行")
    client = _make_client()
    r = client.post(f"/api/receipt/{rid}/retry", headers=STAFF_HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued"
    assert len(job_calls) == 1
    assert job_calls[0]["vendor_hint"] == "德利行", \
        "重试必须把单据行上的供应商作为 vendor_hint 派发（否则首轮记忆注入不可达）"
    assert job_calls[0]["receipt_id"] == rid, "仍复用原单据，不新建"
    assert precheck_bomb == [], "重试不得调用预判模型"


def test_retry_empty_supplier_keeps_hint_empty(job_calls, precheck_bomb, tmp_path):
    """行上供应商为空 → hint 为空，且不为了凑命中率编造供应商。"""
    rid, _ = _mk_receipt(tmp_path, supplier_name="   ")
    client = _make_client()
    r = client.post(f"/api/receipt/{rid}/retry", headers=STAFF_HEADERS)
    assert r.status_code == 200, r.text
    assert job_calls[0]["vendor_hint"] == ""
    assert precheck_bomb == []


# -------------------------------------------------------------
# 2. 重解析（换图重跑）入口：同口径派生
# -------------------------------------------------------------
def test_replace_image_derives_vendor_hint_from_row(job_calls, precheck_bomb,
                                                    monkeypatch, tmp_path):
    rid, _ = _mk_receipt(tmp_path, supplier_name="新記凍肉")

    new_img = tmp_path / "retake.png"
    new_img.write_bytes(_sharp_png_bytes())
    monkeypatch.setattr(api_receipts, "_save_upload", lambda _f: str(new_img))
    monkeypatch.setattr(api_receipts, "public_image_url", lambda *a, **k: "")

    client = _make_client()
    r = client.post(f"/api/receipt/{rid}/replace-image",
                    files={"receipt": ("retake.png", _sharp_png_bytes(), "image/png")},
                    data={"force": "false"}, headers=STAFF_HEADERS)
    assert r.status_code == 200, r.text
    assert len(job_calls) == 1
    assert job_calls[0]["vendor_hint"] == "新記凍肉", \
        "重解析必须把单据行上的供应商作为 vendor_hint 派发（与 /retry 同一口径）"
    assert precheck_bomb == [], "重解析不得调用预判模型"


def test_replace_image_empty_supplier_keeps_hint_empty(job_calls, precheck_bomb,
                                                       monkeypatch, tmp_path):
    rid, _ = _mk_receipt(tmp_path, supplier_name="")
    new_img = tmp_path / "retake.png"
    new_img.write_bytes(_sharp_png_bytes())
    monkeypatch.setattr(api_receipts, "_save_upload", lambda _f: str(new_img))
    monkeypatch.setattr(api_receipts, "public_image_url", lambda *a, **k: "")

    client = _make_client()
    r = client.post(f"/api/receipt/{rid}/replace-image",
                    files={"receipt": ("retake.png", _sharp_png_bytes(), "image/png")},
                    headers=STAFF_HEADERS)
    assert r.status_code == 200, r.text
    assert job_calls[0]["vendor_hint"] == ""
    assert precheck_bomb == []


# -------------------------------------------------------------
# 3. 派生是只读的：不新建供应商档案、不因解析失败阻断
# -------------------------------------------------------------
def test_derive_hint_is_read_only_and_never_raises(monkeypatch, tmp_path, caplog):
    rid, _ = _mk_receipt(tmp_path, supplier_name="未建档供應商")
    s = db.get_session()
    try:
        before = s.query(db._SupplierRow).count()
    finally:
        s.close()

    row = db.get_receipt_row(rid)
    assert api_receipts._vendor_hint_from_row(row, "default") == "未建档供應商"

    s = db.get_session()
    try:
        after = s.query(db._SupplierRow).count()
    finally:
        s.close()
    assert after == before, "派生 hint 只读，绝不新建供应商档案（P2-T1 口径）"

    # 已有 supplier_id 时不再查库：把 resolve 换成探测器，确认未被调用
    calls = []
    monkeypatch.setattr(db, "resolve_supplier_id",
                        lambda *a, **k: calls.append(a) or 1)
    assert api_receipts._vendor_hint_from_row(
        SimpleNamespace(supplier_name="德利行", supplier_id=7)) == "德利行"
    assert calls == [], "行上已有 supplier_id 时不得再查库"

    # 未锚定（resolve 返回 None）→ hint 取值不变 + 留一条可见的 warning 日志
    # why：该分支存在的唯一理由就是让运维能区分「没供应商可用」与「有名字但没锚定
    # 档案」；若用 INFO，默认日志级别下不可见，等于把这条分支写死了。先自校准 caplog
    # 采集通道（注入一条已知日志并断言被捕获），否则「没捕获到」可能只是通道坏了。
    with caplog.at_level(logging.WARNING, logger="api_receipts"):
        logging.getLogger("api_receipts").warning("[calib] caplog 采集通道自校准")
        assert any("[calib]" in r.getMessage() for r in caplog.records), \
            "caplog 未捕获到自校准日志，采集通道无效，后续断言不可信"
        caplog.clear()

        monkeypatch.setattr(db, "resolve_supplier_id", lambda *a, **k: None)
        assert api_receipts._vendor_hint_from_row(
            SimpleNamespace(supplier_name="德利行", supplier_id=None)) == "德利行", \
            "未锚定不改变 hint 取值形态"

    hits = [r for r in caplog.records if "未锚定到供应商档案" in r.getMessage()]
    assert hits, "未锚定时必须留下可见日志（运维据此区分两种未命中）"
    assert all(r.levelno >= logging.WARNING for r in hits), \
        "该日志必须是 warning 级：INFO 在默认日志级别下不可见，等于没记"

    # 解析抛错必须吞成空日志、不阻断、也不改变 hint 取值
    def _raise(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(db, "resolve_supplier_id", _raise)
    assert api_receipts._vendor_hint_from_row(
        SimpleNamespace(supplier_name="德利行", supplier_id=None)) == "德利行"

    # 空/缺失 supplier_name → 空串，不抛
    assert api_receipts._vendor_hint_from_row(
        SimpleNamespace(supplier_name=None, supplier_id=None)) == ""
    assert api_receipts._vendor_hint_from_row(SimpleNamespace()) == ""


# -------------------------------------------------------------
# 4. 批量入口：显式范围外（不新增形参、不调预判模型、hint 恒为空）
# -------------------------------------------------------------
def test_batch_upload_has_no_vendor_hint_param():
    """批量手选判为范围外的结构化守卫。

    若将来要支持，必须先解决「每张各自的供应商」的表达方式并同时接前端；只加形参
    就是本仓高发的「定义了没接线」。这里让「悄悄加一个没人发的形参」直接失败。
    """
    params = inspect.signature(api_receipts.upload_batch).parameters
    assert "vendor_hint" not in params, (
        "批量上传已判定为范围外：扁平 files 契约无法表达每张各自的供应商，"
        "整批共用一个 hint 会向其余子单据注入错误先验。若要支持，请先补前端逐文件"
        "配对并在 upload_batch docstring 里更新结论，而不是只加形参。")
    doc = api_receipts.upload_batch.__doc__ or ""
    assert "范围外" in doc, "范围外结论必须写在 upload_batch 的 docstring 里（计划里不留空）"


def test_batch_upload_dispatches_empty_hint(monkeypatch, tmp_path):
    """行为断言：批量派发时 hint 为空、不调预判模型，流程照常成功。"""
    from app.services import preprocess
    from app.services import vendor_precheck

    calls = []

    def _stub(image_path, vendor_hint="", receipt_id=None, tenant_id=None,
              image_guard=None):
        calls.append({"vendor_hint": vendor_hint})
        return ("job-batch-1", 9001)

    def _save(_f):
        # 批量链路会读文件算 md5，必须真的存在
        p = tmp_path / "fake_upload.jpg"
        p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
        return str(p)

    def _precheck_bomb(*a, **k):
        raise AssertionError("批量入口不得调用预判模型")

    monkeypatch.setattr(api_receipts, "start_recognition_job", _stub)
    monkeypatch.setattr(api_receipts, "_save_upload", _save)
    monkeypatch.setattr(api_receipts, "_laplacian_variance", lambda _p: None)
    monkeypatch.setattr(api_receipts, "public_image_url", lambda *a, **k: "")
    monkeypatch.setattr(api_receipts, "build_detail", lambda _row: {})
    monkeypatch.setattr(preprocess, "apply_pipeline",
                        lambda p: (p, {"applied": False}))
    monkeypatch.setattr(api_receipts.db, "get_receipt_row",
                        lambda *a, **k: SimpleNamespace(version=1))
    monkeypatch.setattr(api_receipts.db, "log_user_event", lambda **kw: None)
    monkeypatch.setattr(vendor_precheck, "predict_vendor", _precheck_bomb)

    client = _make_client()
    r = client.post("/api/upload_batch",
                    files=[("files", ("a.jpg", b"\xff\xd8\xff\xe0abc", "image/jpeg"))],
                    headers=STAFF_HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success"
    assert calls == [{"vendor_hint": ""}], \
        "批量入口不注入供应商先验（范围外），hint 恒为空"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
