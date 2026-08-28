# -*- coding: utf-8 -*-
"""Gap E2 租户隔离专项测试：tenant_id 贯穿业务主表。

覆盖（TDD，先于实现编写）：
1. A 租户写入的数据，B 租户查询不可见：
   receipts / receipt_items / suppliers / skus / dishes / inventory_log
2. vendor_memory：同一供应商不同租户的记忆互不覆盖（对齐 rag.py
   per-tenant Chroma collection 的隔离口径：同租户同 vendor 一条，跨租户不可见）
3. db.scoped() 统一过滤语义：tenant_id 为 None/空 不过滤（向后兼容）
4. API 层 X-Tenant-Id 头透传：跨租户列表不可见、跨租户按 id 取详情 404
5. API 层上传识别主链路（P0-1）：/api/upload 创建的单据携带请求租户，
   A 租户上传的单据 B 租户列表不可见（识别管线打桩，无外部调用）

数据一律使用假数据（供应商A / VendorA / SKU甲，租户 tenantA / tenantB）。
"""

import os
import sys
import tempfile
import uuid

# 隔离环境：独立 DB（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="tenant_isolation_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_tenant.db")

_DEMO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo")
sys.path.insert(0, _DEMO_DIR)

import importlib  # noqa: E402

import app.db as db  # noqa: E402

TENANT_A = "tenantA"
TENANT_B = "tenantB"


def _fresh_db():
    """每个用例独立数据库文件，避免跨用例串扰。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"tenant_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    return db


def _mk_receipt(_db, vendor, tenant_id=None):
    return _db.create_receipt(supplier_name=vendor, status="parsed",
                              tenant_id=tenant_id)


# -------------------------------------------------------------
# 1. receipts：A 租户写入，B 租户不可见
# -------------------------------------------------------------
def test_receipts_isolated_between_tenants():
    _db = _fresh_db()
    rid_a = _mk_receipt(_db, "供应商A", tenant_id=TENANT_A)
    rid_b = _mk_receipt(_db, "供应商B", tenant_id=TENANT_B)

    rows_a = {r.id for r in _db.list_receipt_rows(tenant_id=TENANT_A)}
    rows_b = {r.id for r in _db.list_receipt_rows(tenant_id=TENANT_B)}
    assert rid_a in rows_a and rid_b not in rows_a, "tenantA 只能看见自己的单据"
    assert rid_b in rows_b and rid_a not in rows_b, "tenantB 只能看见自己的单据"
    # 不传租户 = 不过滤（内部脚本/既有测试路径向后兼容）
    assert {rid_a, rid_b} <= {r.id for r in _db.list_receipt_rows()}


def test_receipt_items_isolated_between_tenants():
    _db = _fresh_db()
    rid_a = _mk_receipt(_db, "供应商A", tenant_id=TENANT_A)
    _db.set_receipt_items(rid_a, [{
        "name": "菜心", "raw_name": "菜心", "quantity": 2, "unit": "斤",
        "raw_unit": "斤", "unit_price": 10, "amount": 20,
        "sku_id": None, "cost_center_id": None, "confidence": 0.9,
        "matched": 0, "price_anomaly": 0, "price_anomaly_direction": "",
        "price_diff_percent": 0.0, "unit_conversion_warning": "",
        "fuzzy_candidates": [], "entity_candidates": [],
    }], tenant_id=TENANT_A)

    assert len(_db.get_receipt_items(rid_a, tenant_id=TENANT_A)) == 1
    assert _db.get_receipt_items(rid_a, tenant_id=TENANT_B) == [], \
        "tenantB 不得读取 tenantA 的单据明细"


# -------------------------------------------------------------
# 2. suppliers / skus：A 租户写入，B 租户不可见
# -------------------------------------------------------------
def test_suppliers_isolated_between_tenants():
    _db = _fresh_db()
    sup_a, err = _db.create_supplier("供应商A", tenant_id=TENANT_A)
    assert err is None

    names_a = [s.name for s in _db.list_suppliers(tenant_id=TENANT_A)]
    names_b = [s.name for s in _db.list_suppliers(tenant_id=TENANT_B)]
    assert "供应商A" in names_a
    assert "供应商A" not in names_b, "tenantB 不得看见 tenantA 的供应商"

    assert _db.find_supplier_by_name("供应商A", tenant_id=TENANT_A) is not None
    assert _db.find_supplier_by_name("供应商A", tenant_id=TENANT_B) is None


def test_skus_isolated_between_tenants():
    _db = _fresh_db()
    sku_a, err = _db.create_sku("SKU甲", category="蔬菜", base_unit="斤",
                                tenant_id=TENANT_A)
    assert err is None

    names_a = [s.name for s in _db.list_skus(tenant_id=TENANT_A)]
    names_b = [s.name for s in _db.list_skus(tenant_id=TENANT_B)]
    assert "SKU甲" in names_a
    assert "SKU甲" not in names_b, "tenantB 不得看见 tenantA 的 SKU"


# -------------------------------------------------------------
# 3. dishes：A 租户写入，B 租户不可见（走 db.scoped 统一过滤）
# -------------------------------------------------------------
def test_dishes_isolated_between_tenants():
    _db = _fresh_db()
    s = _db.get_session()
    try:
        s.add(_db._DishRow(name="菜品甲", category="主食", price=30.0,
                           tenant_id=TENANT_A))
        s.add(_db._DishRow(name="菜品乙", category="主食", price=25.0,
                           tenant_id=TENANT_B))
        s.commit()

        names_a = {r.name for r in
                   _db.scoped(s.query(_db._DishRow), _db._DishRow,
                              TENANT_A).all()}
        names_b = {r.name for r in
                   _db.scoped(s.query(_db._DishRow), _db._DishRow,
                              TENANT_B).all()}
        assert "菜品甲" in names_a and "菜品乙" not in names_a
        assert "菜品乙" in names_b and "菜品甲" not in names_b, \
            "dishes 必须按租户隔离"

        # scoped 语义：tenant_id 为 None/空 不过滤
        all_names = {r.name for r in
                     _db.scoped(s.query(_db._DishRow), _db._DishRow,
                                None).all()}
        assert all_names == {"菜品甲", "菜品乙"}
    finally:
        s.close()


# -------------------------------------------------------------
# 4. inventory_log：A 租户写入，B 租户不可见
# -------------------------------------------------------------
def test_inventory_log_isolated_between_tenants():
    _db = _fresh_db()
    sku_a, _err = _db.create_sku("SKU甲", tenant_id=TENANT_A)
    _db.apply_stock_log(sku_id=sku_a, name="SKU甲", qty=5, unit="斤",
                        amount=50, vendor="供应商A", date="2026-08-01",
                        receipt_id=None, kind="in", note="",
                        tenant_id=TENANT_A)

    s = _db.get_session()
    try:
        logs_a = _db.scoped(s.query(_db._StockLogRow), _db._StockLogRow,
                            TENANT_A).all()
        logs_b = _db.scoped(s.query(_db._StockLogRow), _db._StockLogRow,
                            TENANT_B).all()
        assert len(logs_a) == 1
        assert logs_b == [], "tenantB 不得看见 tenantA 的库存流水"
    finally:
        s.close()
    # 价格历史同样按租户过滤
    assert len(_db.price_history(sku_a, tenant_id=TENANT_A)) == 1
    assert _db.price_history(sku_a, tenant_id=TENANT_B) == []


# -------------------------------------------------------------
# 5. vendor_memory：同供应商不同租户互不覆盖（对齐 Chroma 隔离口径）
# -------------------------------------------------------------
def test_vendor_memory_same_vendor_tenants_do_not_overwrite():
    _db = _fresh_db()
    _db.upsert_vendor_memory("VendorA", "notes-A-v1", "sample-A",
                             tenant_id=TENANT_A)
    # 同一 vendor、不同租户 upsert：不得覆盖 tenantA 的记忆
    _db.upsert_vendor_memory("VendorA", "notes-B-v1", "sample-B",
                             tenant_id=TENANT_B)

    mem_a = _db.get_vendor_memory("VendorA", tenant_id=TENANT_A)
    mem_b = _db.get_vendor_memory("VendorA", tenant_id=TENANT_B)
    assert mem_a is not None and mem_a["notes"] == "notes-A-v1"
    assert mem_b is not None and mem_b["notes"] == "notes-B-v1"

    # tenantA 再次 upsert 只演进自己的记忆，tenantB 不受影响
    _db.upsert_vendor_memory("VendorA", "notes-A-v2", "sample-A2",
                             tenant_id=TENANT_A)
    assert _db.get_vendor_memory("VendorA", tenant_id=TENANT_A)["notes"] == "notes-A-v2"
    assert _db.get_vendor_memory("VendorA", tenant_id=TENANT_B)["notes"] == "notes-B-v1"


def test_vendor_memory_default_tenant_backward_compat():
    """rag.py 现有调用不带 tenant_id，落 default 租户且可读回（兼容口径）。"""
    _db = _fresh_db()
    _db.upsert_vendor_memory("VendorA", "notes-default", "sample-default")
    mem = _db.get_vendor_memory("VendorA")
    assert mem is not None and mem["notes"] == "notes-default"
    # 不传租户视为 default 租户，与显式 tenant_id="default" 等价
    mem2 = _db.get_vendor_memory("VendorA", tenant_id="default")
    assert mem2 is not None and mem2["notes"] == "notes-default"
    # 其他租户不可见
    assert _db.get_vendor_memory("VendorA", tenant_id=TENANT_B) is None


# -------------------------------------------------------------
# 6. API 层：X-Tenant-Id 头透传（取法与 X-Role 一致）
# -------------------------------------------------------------
def test_api_tenant_isolation_via_header():
    _db = _fresh_db()
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    client = TestClient(fastapi_app)

    rid_a = _mk_receipt(_db, "供应商A", tenant_id=TENANT_A)
    rid_b = _mk_receipt(_db, "供应商B", tenant_id=TENANT_B)

    ha = {"X-Role": "staff", "X-Tenant-Id": TENANT_A}
    hb = {"X-Role": "staff", "X-Tenant-Id": TENANT_B}

    resp_a = client.get("/api/receipts", headers=ha)
    assert resp_a.status_code == 200
    ids_a = {r["id"] for r in resp_a.json()["data"]}
    assert rid_a in ids_a and rid_b not in ids_a

    resp_b = client.get("/api/receipts", headers=hb)
    ids_b = {r["id"] for r in resp_b.json()["data"]}
    assert rid_b in ids_b and rid_a not in ids_b

    # 跨租户按 id 取详情：404（等价于不存在）
    assert client.get(f"/api/receipt/{rid_a}", headers=hb).status_code == 404
    assert client.get(f"/api/receipt/{rid_a}", headers=ha).status_code == 200

    # 不带 X-Tenant-Id 头 = default 租户：两个非 default 单据均不可见
    resp_default = client.get("/api/receipts", headers={"X-Role": "staff"})
    ids_default = {r["id"] for r in resp_default.json()["data"]}
    assert rid_a not in ids_default and rid_b not in ids_default


def test_api_suppliers_isolated_via_header():
    _db = _fresh_db()
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    client = TestClient(fastapi_app)

    ha = {"X-Role": "owner", "X-Tenant-Id": TENANT_A}
    hb = {"X-Role": "owner", "X-Tenant-Id": TENANT_B}

    resp = client.post("/api/suppliers", json={"name": "供应商A"}, headers=ha)
    assert resp.status_code == 200, resp.text

    names_a = [x["name"] for x in client.get("/api/suppliers", headers=ha).json()["data"]]
    names_b = [x["name"] for x in client.get("/api/suppliers", headers=hb).json()["data"]]
    assert "供应商A" in names_a
    assert "供应商A" not in names_b, "tenantB 不得通过 API 看到 tenantA 的供应商"


# -------------------------------------------------------------
# 7. API 层：上传识别主链路租户透传（P0-1 返工）
# -------------------------------------------------------------
def test_api_upload_receipts_isolated_via_header():
    """A 租户经 /api/upload 上传入口创建的单据：A 租户列表可见，B 租户不可见。

    识别管线打桩（run_pipeline 返回 data=None），不触发外部模型/网络调用；
    单据行由 start_recognition_job 在请求线程内同步创建（验证 P0-1 租户透传
    到 create_receipt），管线异步结果与列表可见性断言无关。
    """
    _db = _fresh_db()
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app

    # 打桩识别管线：Job 线程内 `from app.chains import supervisor`
    # 取的是同一模块对象，替换模块属性即可拦截，测试结束还原
    from app.chains import supervisor as _supervisor
    _orig_run_pipeline = _supervisor.run_pipeline

    def _fake_run_pipeline(image_path, **kwargs):
        return {"data": None, "last_error": "mock: pipeline stubbed"}

    _supervisor.run_pipeline = _fake_run_pipeline
    try:
        client = TestClient(fastapi_app)
        ha = {"X-Role": "staff", "X-Tenant-Id": TENANT_A}
        hb = {"X-Role": "staff", "X-Tenant-Id": TENANT_B}

        resp_a = client.post(
            "/api/upload", headers=ha,
            files={"receipt": ("fake_a.jpg", b"fake-jpeg-bytes-tenantA" * 8, "image/jpeg")},
            data={"async_": "true", "force": "true"})
        assert resp_a.status_code == 200, resp_a.text
        body_a = resp_a.json()
        assert body_a["status"] == "queued", body_a
        rid_a = int(body_a["receipt_id"])

        resp_b = client.post(
            "/api/upload", headers=hb,
            files={"receipt": ("fake_b.jpg", b"fake-jpeg-bytes-tenantB" * 8, "image/jpeg")},
            data={"async_": "true", "force": "true"})
        assert resp_b.status_code == 200, resp_b.text
        rid_b = int(resp_b.json()["receipt_id"])

        # 写入层断言：上传入口创建的单据行必须携带请求租户（P0-1 透传）
        row_a = _db.get_receipt_row(rid_a)
        assert row_a is not None and row_a.tenant_id == TENANT_A, \
            "上传链路创建的单据必须落 A 租户"
        row_b = _db.get_receipt_row(rid_b)
        assert row_b is not None and row_b.tenant_id == TENANT_B, \
            "上传链路创建的单据必须落 B 租户"

        # 列表可见性断言：A 租户可见自己的、看不到 B 的；B 反之
        ids_a = {r["id"] for r in client.get("/api/receipts", headers=ha).json()["data"]}
        assert rid_a in ids_a and rid_b not in ids_a, \
            "tenantA 列表只可见自己上传的单据"
        ids_b = {r["id"] for r in client.get("/api/receipts", headers=hb).json()["data"]}
        assert rid_b in ids_b and rid_a not in ids_b, \
            "tenantB 列表不得可见 tenantA 上传的单据"
    finally:
        _supervisor.run_pipeline = _orig_run_pipeline


# -------------------------------------------------------------
# 8. API 层：feedback 租户口径收敛（P3-4）—— header 覆盖 body
# -------------------------------------------------------------
def test_api_feedback_header_overrides_body_tenant():
    """P3-4 口径：X-Tenant-Id header > body.tenant_id > default。

    客户端 body 不得覆盖服务端 header 用于归属校验与 receipt_feedback 落库；
    无 header 时 body 兜底兼容（归属校验与落库使用同一解析值）。
    """
    _db = _fresh_db()
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    client = TestClient(fastapi_app)

    # 场景 1：header=tenantA、body 谎报 tenantB —— 归属校验必须按 header（可成功反馈）
    rid_a = _mk_receipt(_db, "供应商A", tenant_id=TENANT_A)
    resp = client.post(
        f"/api/receipt/{rid_a}/feedback",
        json={"like": 1, "comment": "", "item_index": None,
              "tenant_id": TENANT_B},
        headers={"X-Role": "staff", "X-Tenant-Id": TENANT_A})
    assert resp.status_code == 200, resp.text
    assert resp.json()["feedback"]["tenant_id"] == TENANT_A, \
        "header 与 body 冲突时落库租户必须取 header 值"

    # 场景 2：body 声称 tenantB 去 feedback tenantA 的单据（带 tenantA header）
    # → 按 header=tenantA 归属校验通过；不产生 tenantB 的反馈行
    fbs_a = _db.list_receipt_feedbacks(receipt_id=rid_a)
    assert len(fbs_a) == 1 and fbs_a[0]["tenant_id"] == TENANT_A
    fbs_b = _db.list_receipt_feedbacks(tenant_id=TENANT_B)
    assert all(f["receipt_id"] != rid_a for f in fbs_b), \
        "body.tenant_id 不得覆盖 header 将反馈写入其他租户"

    # 场景 3：无 header 时 body 兜底生效（老客户端兼容路径）
    rid_b = _mk_receipt(_db, "供应商B", tenant_id=TENANT_B)
    resp2 = client.post(
        f"/api/receipt/{rid_b}/feedback",
        json={"like": 1, "comment": "", "item_index": None,
              "tenant_id": TENANT_B},
        headers={"X-Role": "staff"})
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["feedback"]["tenant_id"] == TENANT_B, \
        "无 header 时应按 body.tenant_id 兜底解析"

    # 场景 4：header 与单据归属冲突（跨租户）→ 404 视为不存在
    resp3 = client.post(
        f"/api/receipt/{rid_b}/feedback",
        json={"like": 1, "comment": "", "item_index": None,
              "tenant_id": TENANT_B},
        headers={"X-Role": "staff", "X-Tenant-Id": TENANT_A})
    assert resp3.status_code == 404, "header 声明的租户看不到他租单据，应 404"

    # 场景 5：header 与 body 均缺省 → default 租户
    rid_d = _mk_receipt(_db, "供应商D", tenant_id="default")
    resp4 = client.post(
        f"/api/receipt/{rid_d}/feedback",
        json={"like": 1, "comment": "", "item_index": None},
        headers={"X-Role": "staff"})
    assert resp4.status_code == 200, resp4.text
    assert resp4.json()["feedback"]["tenant_id"] == "default"


# -------------------------------------------------------------
# 9. 聚合口径租户化（P3-5）：cost_summary / deduplicate_skus_by_canonical
# -------------------------------------------------------------
def test_cost_summary_and_dedupe_scoped_by_tenant():
    _db = _fresh_db()
    from app.services.inventory import cost_summary

    sku_a, err = _db.create_sku("彙總食材甲", tenant_id=TENANT_A)
    assert err is None
    _db.apply_stock_log(sku_id=sku_a, name="彙總食材甲", qty=4, unit="斤",
                        amount=40, vendor="供應商A", date="2026-01-01",
                        receipt_id=None, kind="in", tenant_id=TENANT_A)
    sku_b, err = _db.create_sku("彙總食材乙", tenant_id=TENANT_B)
    assert err is None
    _db.apply_stock_log(sku_id=sku_b, name="彙總食材乙", qty=9, unit="斤",
                        amount=90, vendor="供應商B", date="2026-01-02",
                        receipt_id=None, kind="in", tenant_id=TENANT_B)

    cs_a = cost_summary(tenant_id=TENANT_A)
    assert set(cs_a["items"].keys()) == {"彙總食材甲"}, \
        "cost_summary 按租户 scope 时不得混入他租 SKU"
    cs_b = cost_summary(tenant_id=TENANT_B)
    assert set(cs_b["items"].keys()) == {"彙總食材乙"}
    cs_all = cost_summary()
    assert set(cs_all["items"].keys()) == {"彙總食材甲", "彙總食材乙"}, \
        "tenant_id=None 不过滤（向后兼容）"

    # deduplicate_skus_by_canonical：A 租户去重不得动 B 租户同名变体
    _db.create_sku("彙總食材甲_1234567890", tenant_id=TENANT_A)
    _db.create_sku("彙總食材甲_1234567890", tenant_id=TENANT_B)

    reports_a = _db.deduplicate_skus_by_canonical(tenant_id=TENANT_A)
    assert len(reports_a) == 1 and reports_a[0]["canonical"] == "彙總食材甲"
    names_b = {s.name for s in _db.list_skus(include_inactive=True,
                                             tenant_id=TENANT_B)}
    assert "彙總食材甲_1234567890" in names_b, \
        "A 租户的去重自愈不得影响 B 租户数据"

    # B 租户的 serial 变体是单成员组（无合并对象），走残留清理路径归一重命名，
    # 且 scope 限定在 B 租户内
    reports_b = _db.deduplicate_skus_by_canonical(tenant_id=TENANT_B)
    assert reports_b == []
    names_b_after = {s.name for s in _db.list_skus(include_inactive=True,
                                                   tenant_id=TENANT_B)}
    assert names_b_after == {"彙總食材乙", "彙總食材甲"}, \
        f"B 租户残留 serial 变体应归一为 canonical，实际 {names_b_after}"


# -------------------------------------------------------------
# 10. db 原语租户防御（Wave A F2）：delete_sku / merge_skus
# -------------------------------------------------------------
def test_delete_sku_cross_tenant_defense():
    """B 租户调 db.delete_sku(id, tenant_id=tenantB) 不得删除 A 租户的 SKU。

    返回约定：与「不存在」同形态 (False, 'NOT_FOUND')；SKU 行保持原状
    （不物理删除、不停用）。None=不过滤（向后兼容）。
    """
    _db = _fresh_db()
    sku_a, err = _db.create_sku("删除食材甲", tenant_id=TENANT_A)
    assert err is None

    ok, action = _db.delete_sku(sku_a, tenant_id=TENANT_B)
    assert (ok, action) == (False, "NOT_FOUND"), "跨租户删除必须按不存在拒绝"

    row = _db.get_sku(sku_a)
    assert row is not None and row.active == 1, \
        "B 租户的删除调用不得删除或停用 A 租户的 SKU"

    # 同租户删除行为不变
    ok2, action2 = _db.delete_sku(sku_a, tenant_id=TENANT_A)
    assert (ok2, action2) == (True, "DELETED")

    # tenant_id=None 不过滤（向后兼容）
    sku_none, err = _db.create_sku("删除食材乙", tenant_id=TENANT_A)
    assert err is None
    ok3, _act = _db.delete_sku(sku_none)
    assert ok3 is True


def test_merge_skus_cross_tenant_defense():
    """merge_skus 跨租户防御：primary 与每个 secondary 均按租户校验。

    返回约定：跨租户 primary → (None, 'PRIMARY_NOT_FOUND')；
    跨租户 secondary → (None, 'SECONDARY_NOT_FOUND')；拒绝时零副作用
    （副 SKU 不停用、库存不合并、流水不迁移）。None=不过滤（向后兼容）。
    """
    _db = _fresh_db()
    p_a, _ = _db.create_sku("合并主材甲", tenant_id=TENANT_A)
    s_a, _ = _db.create_sku("合并副材甲", tenant_id=TENANT_A)
    _db.apply_stock_log(sku_id=s_a, name="合并副材甲", qty=3, unit="斤",
                        amount=30, vendor="供应商A", date="2026-08-01",
                        receipt_id=None, kind="in", tenant_id=TENANT_A)
    s_b, _ = _db.create_sku("合并副材乙", tenant_id=TENANT_B)
    _db.apply_stock_log(sku_id=s_b, name="合并副材乙", qty=8, unit="斤",
                        amount=80, vendor="供应商B", date="2026-08-02",
                        receipt_id=None, kind="in", tenant_id=TENANT_B)

    # 跨租户 primary：拒绝
    res, err = _db.merge_skus(s_b, [s_a], tenant_id=TENANT_A)
    assert (res, err) == (None, "PRIMARY_NOT_FOUND"), \
        "跨租户 primary 必须按不存在拒绝"

    # 跨租户 secondary：拒绝且不生效
    res, err = _db.merge_skus(p_a, [s_a, s_b], tenant_id=TENANT_A)
    assert (res, err) == (None, "SECONDARY_NOT_FOUND"), \
        "跨租户 secondary 必须被拒绝"
    row_a = _db.get_sku(s_a)
    assert row_a is not None and row_a.active == 1, \
        "被拒绝的合并不得停用 A 租户副 SKU"
    row_b = _db.get_sku(s_b)
    assert row_b is not None and row_b.active == 1 \
        and (row_b.current_stock or 0) > 0, \
        "被拒绝的合并不得动 B 租户 SKU 的库存与状态"

    # 同租户合并行为不变：A 租户口径下副 SKU 正常迁移停用
    res, err = _db.merge_skus(p_a, [s_a], tenant_id=TENANT_A)
    assert err is None and res["merged_sku_ids"] == [s_a]
    assert _db.get_sku(s_a).active == 0, "同租户合并应正常停用副 SKU"


# -------------------------------------------------------------
# 11. daily_consumption 租户化（Wave A F3）：列表隔离 + void 跨租户防御
# -------------------------------------------------------------
def test_api_daily_consumption_isolated_and_void_guarded():
    """B 租户不得看见/冲销 A 租户的每日消耗记录。

    daily_dish_consumptions 无 tenant_id 列，归属经关联 dish（缺失时退回
    扣减明细关联 SKU）判定：列表按行归属过滤；跨租户 void 返回与「不存在」
    一致的 404 形态，且不得改写台账、不得写冲销流水。走真实批量消耗 API
    （FIFO 扣减 + 流水）构造数据，全假数据。
    """
    _db = _fresh_db()
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    client = TestClient(fastapi_app)

    ha = {"X-Role": "owner", "X-Tenant-Id": TENANT_A}
    hb = {"X-Role": "owner", "X-Tenant-Id": TENANT_B}

    # 假数据：A 租户 SKU + 入库批次（FIFO 池）+ 餐品
    sku_a, err = _db.create_sku("消耗食材甲", tenant_id=TENANT_A)
    assert err is None
    _db.apply_stock_log(sku_id=sku_a, name="消耗食材甲", qty=10, unit="斤",
                        amount=100, vendor="供应商A", date="2026-08-20",
                        receipt_id=None, kind="in", tenant_id=TENANT_A)
    s = _db.get_session()
    try:
        dish = _db._DishRow(name="菜品丙", category="主食", price=30.0,
                            tenant_id=TENANT_A)
        s.add(dish)
        s.commit()
        dish_id = dish.id
    finally:
        s.close()

    # 经真实批量消耗 API（staff 角色）产生 A 租户消耗记录 + FIFO 明细 + 流水
    resp = client.post(
        "/api/dishes/daily_consumption/batch",
        json={"date": "2026-08-20", "notes": "假数据",
              "items": [{"dish_id": dish_id, "quantity": 2}]},
        headers={"X-Role": "staff", "X-Tenant-Id": TENANT_A})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success", body
    cid = body["data"]["consumption_ids"][0]

    # 列表隔离：同一天，A 租户可见自己的记录，B 租户不可见
    ids_a = [c["id"] for c in client.get(
        "/api/dishes/daily_consumption", params={"date": "2026-08-20"},
        headers=ha).json()["data"]["consumptions"]]
    assert cid in ids_a, "A 租户应可见自己的消耗记录"
    ids_b = [c["id"] for c in client.get(
        "/api/dishes/daily_consumption", params={"date": "2026-08-20"},
        headers=hb).json()["data"]["consumptions"]]
    assert cid not in ids_b, "B 租户的消耗列表不得包含 A 租户的数据"

    # 跨租户 void：404 与「不存在」同形态；台账不被改写、无冲销流水
    resp_void = client.post(
        f"/api/dishes/daily_consumption/{cid}/void", headers=hb)
    assert resp_void.status_code == 404, resp_void.text
    assert resp_void.json()["msg"] == "消耗记录不存在"
    s = _db.get_session()
    try:
        assert s.get(_db._DailyConsumptionRow, cid).is_void == 0, \
            "跨租户 void 不得改写 A 租户的消耗台账"
        adjust_logs = s.query(_db._StockLogRow).filter(
            _db._StockLogRow.kind == "adjust").all()
        assert adjust_logs == [], \
            "跨租户 void 不得把冲销流水写进任何租户的台账"
    finally:
        s.close()

    # 同租户 void 行为不变：owner 冲销成功并回写 is_void
    resp_ok = client.post(
        f"/api/dishes/daily_consumption/{cid}/void", headers=ha)
    assert resp_ok.status_code == 200, resp_ok.text
    s = _db.get_session()
    try:
        assert s.get(_db._DailyConsumptionRow, cid).is_void == 1
    finally:
        s.close()
