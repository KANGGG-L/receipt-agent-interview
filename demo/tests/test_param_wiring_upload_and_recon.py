# -*- coding: utf-8 -*-
"""两处「形参声明了却没接线」的收口验证。

B1 批量上传的灰测分组开关 `treatment`
  原状：`upload_batch(treatment: str = Form("true"))` 函数体内零使用；单张上传
  `upload_receipt` 在同一形参上用 `grp = "treatment" if ... else "control"` 落了埋点。
  前端两处（`main.js` 的 uploadSinglePhoto / triggerBatchAnalysis）都传了
  `/api/upload[_batch]?treatment=<勾选>` —— 注意是**查询串**，而形参是 Form
  （FastAPI 的 Form 不读查询串），故形参恒为默认值、勾选被静默忽略；批量侧还完全
  不上报 upload 事件，漏斗（db.query_funnel 按 grp 过滤）因此丢失整批数据。

B2 对账列表的供应商筛选 `supplier_id`
  原状：`list_reconciliation(request, supplier_id: int = None)` 从不使用该形参，始终
  返回全量；前端 `loadReconTasks()` 明确按选中供应商拼 `?supplier_id=`，筛选无效。

B2 延伸 支付列表的供应商筛选 `supplier_id`（同类第三处）
  原状：`list_payments` 用 `if supplier_id: sup = ...; if sup: 过滤` —— supplier_id
  不存在或属于别的租户时过滤整段被跳过，**静默退回全量**，把不属于该供应商（甚至
  跨租户）的支付一并返回。现统一为「找不到就是空列表」，与 list_reconciliation 同口径。

不写 live 库：只挂 router + monkeypatch 掉所有重活（存盘/识别 Job/DB 读写/埋点落库）。
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/receipt_demo_param_wiring_test.db")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import api_finance, api_receipts
from app.services import preprocess

STAFF_HEADERS = {"X-Role": "staff"}
OWNER_HEADERS = {"X-Role": "owner"}


def _make_client(router):
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def batch_env(monkeypatch, tmp_path):
    """把批量上传的所有副作用替换为记录器。"""
    events = []

    def _save_upload(_f):
        p = tmp_path / "fake_upload.jpg"
        p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
        return str(p)

    monkeypatch.setattr(api_receipts, "_save_upload", _save_upload)
    monkeypatch.setattr(api_receipts, "_laplacian_variance", lambda _p: None)
    monkeypatch.setattr(api_receipts, "public_image_url", lambda *a, **k: "")
    monkeypatch.setattr(api_receipts, "build_detail", lambda _row: {})
    monkeypatch.setattr(api_receipts, "start_recognition_job",
                        lambda _p, **kw: ("job-1", 9001))
    monkeypatch.setattr(api_receipts.db, "get_receipt_row",
                        lambda *a, **k: SimpleNamespace(version=1))
    monkeypatch.setattr(preprocess, "apply_pipeline",
                        lambda p: (p, {"applied": False}))
    # 埋点：只记录，不落库（_track_event 会调 db.log_user_event）
    monkeypatch.setattr(api_receipts.db, "log_user_event",
                        lambda **kw: events.append(kw))
    return events


def _files():
    return [("files", ("a.jpg", b"\xff\xd8\xff\xe0abc", "image/jpeg"))]


# ------------------------------------------------------------------
# B1 批量上传：形参真的生效（grp 落组正确）
# ------------------------------------------------------------------
@pytest.mark.parametrize("q,expect", [
    ("treatment=false", "control"),
    ("treatment=true", "treatment"),
    ("", "treatment"),          # 缺省仍为 treatment（与本仓既有默认一致）
])
def test_upload_batch_records_grp_from_query(batch_env, q, expect):
    client = _make_client(api_receipts.router)
    url = "/api/upload_batch" + (f"?{q}" if q else "")
    r = client.post(url, files=_files(), headers=STAFF_HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success"
    ups = [e for e in batch_env if e.get("event_type") == "upload"]
    assert len(ups) == 1, "批量上传必须上报 upload 事件（漏斗按 receipt_id 去重）"
    assert ups[0]["grp"] == expect
    assert ups[0]["receipt_id"] == 9001


def test_upload_single_and_batch_share_same_grp_resolution(batch_env):
    """单张与批量对同一查询串给出一致的 grp（同一口径，避免再次分叉）。"""
    client = _make_client(api_receipts.router)
    # 单张：同步模式会轮询 600 次，改用 async=true 直接返回
    r1 = client.post("/api/upload?treatment=false&async=true",
                     files=[("receipt", ("a.jpg", b"\xff\xd8\xff\xe0abc", "image/jpeg"))],
                     headers=STAFF_HEADERS)
    assert r1.status_code == 200, r1.text
    r2 = client.post("/api/upload_batch?treatment=false", files=_files(),
                     headers=STAFF_HEADERS)
    assert r2.status_code == 200, r2.text
    grps = [e["grp"] for e in batch_env if e.get("event_type") == "upload"]
    assert grps == ["control", "control"]


# ------------------------------------------------------------------
# B2 对账列表：按 supplier_id 过滤
# ------------------------------------------------------------------
@pytest.fixture
def recon_env(monkeypatch):
    monkeypatch.setattr(api_finance, "RECON_TASKS", {
        "t1": {"id": "t1", "supplier_id": 11, "period_start": "2026-08-01",
               "period_end": "2026-08-31", "period_type": "month",
               "status": "reconciling",
               "lines": [{"match_status": "matched"}, {"match_status": "pending"}]},
        "t2": {"id": "t2", "supplier_id": 22, "period_start": "2026-08-01",
               "period_end": "2026-08-31", "period_type": "month",
               "status": "reconciling", "lines": []},
    })
    monkeypatch.setattr(
        api_finance.db, "get_supplier",
        lambda sid, tenant_id=None: SimpleNamespace(id=sid, name=f"供应商{sid}"))


def test_reconciliation_filters_by_supplier(recon_env):
    client = _make_client(api_finance.router)

    all_rows = client.get("/api/reconciliation", headers=OWNER_HEADERS).json()["data"]
    assert [r["id"] for r in all_rows] == ["t1", "t2"]

    one = client.get("/api/reconciliation?supplier_id=11",
                     headers=OWNER_HEADERS).json()["data"]
    assert [r["id"] for r in one] == ["t1"], "选中供应商后必须只返回该供应商的任务"
    assert one[0]["supplier_name"] == "供应商11"
    assert one[0]["summary"] == {"total_lines": 2, "matched": 1, "open_issues": 1}

    other = client.get("/api/reconciliation?supplier_id=22",
                       headers=OWNER_HEADERS).json()["data"]
    assert [r["id"] for r in other] == ["t2"]

    none = client.get("/api/reconciliation?supplier_id=99",
                      headers=OWNER_HEADERS).json()["data"]
    assert none == [], "不存在的供应商应返回空列表，而不是退回全量"


# ------------------------------------------------------------------
# B2 延伸：支付列表按 supplier_id 过滤（同类第三处，原先会静默退回全量）
# ------------------------------------------------------------------
@pytest.fixture
def payments_env(monkeypatch):
    monkeypatch.setattr(api_finance.db, "list_payments", lambda: [
        {"id": 1, "supplier_name": "供应商11", "amount": 100.0},
        {"id": 2, "supplier_name": "供应商22", "amount": 200.0},
    ])
    # 只认 11 / 22；99 视为「不存在或跨租户」
    monkeypatch.setattr(
        api_finance.db, "get_supplier",
        lambda sid, tenant_id=None: (SimpleNamespace(id=sid, name=f"供应商{sid}")
                                     if sid in (11, 22) else None))


def test_payments_filters_by_supplier(payments_env):
    client = _make_client(api_finance.router)

    all_rows = client.get("/api/payments", headers=OWNER_HEADERS).json()["data"]
    assert [r["id"] for r in all_rows] == [1, 2], "不带 supplier_id 时返回全量"

    one = client.get("/api/payments?supplier_id=11",
                     headers=OWNER_HEADERS).json()["data"]
    assert [r["id"] for r in one] == [1], "选中供应商后必须只返回该供应商的支付"

    none = client.get("/api/payments?supplier_id=99",
                      headers=OWNER_HEADERS).json()["data"]
    assert none == [], "不存在/跨租户的供应商必须返回空列表，不得静默退回全量"
