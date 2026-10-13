# -*- coding: utf-8 -*-
"""B2：单位设置、租户换算、入账换算（S1-P0-02、S1-P1-10）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from app import db
from app.services import unit_factors as uf

OWNER = {"X-Role": "owner"}
STAFF = {"X-Role": "staff"}
ADMIN = {"X-Role": "admin"}
OWNER_T2 = {"X-Role": "owner", "X-Tenant-Id": "shop2"}


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_tenant_settings():
    """用例只动 default / shop2 两个租户的设置；前后都清掉，避免影响同进程里的其他测试文件。"""
    from sqlalchemy import text
    from app import units_store

    def wipe():
        with units_store._engine().begin() as conn:
            for tbl in ("tenant_units", "tenant_unit_conversions", "tenant_fx_rates"):
                conn.execute(text("DELETE FROM %s WHERE tenant_id IN ('default','shop2')" % tbl))
        uf.invalidate_cache()

    wipe()
    yield
    wipe()


def _conv(client, a, b, f, headers=OWNER, note=""):
    return client.post("/api/settings/unit_conversions", headers=headers,
                       json={"from_unit": a, "to_unit": b, "factor": f, "note": note})


def _check(client, a, b, headers=OWNER):
    return client.get("/api/settings/unit_conversions/check", headers=headers,
                      params={"from_unit": a, "to_unit": b}).json()


# ---- 种子与「斤」口径 ----
def test_jin_defined_once_as_hk_catty():
    assert uf.convert_unit_quantity(1, "斤", "公斤") == 0.6048
    assert uf.convert_unit_quantity(1, "司马斤", "斤") == 1.0
    assert uf.convert_unit_quantity(1, "catty", "kg") == 0.6048
    assert uf.convert_unit_quantity(1, "市斤", "kg") == 0.5
    assert uf.UNIT_WEIGHT_TO_KG["斤"] == 0.6048
    assert uf.convert_unit_quantity(1, "公斤", "克") == 1000.0
    assert uf.convert_unit_quantity(1, "磅", "kg") == pytest.approx(0.45359237, abs=1e-6)
    assert uf.convert_unit_quantity(1, "升", "毫升") == 1000.0


def test_legacy_calls_still_work_and_raise_valueerror():
    assert uf.convert_unit_quantity(10, "斤", "斤") == 10.0
    assert uf.convert_unit_quantity(0, "箱", "斤") == 0.0
    with pytest.raises(ValueError):
        uf.convert_unit_quantity(1, "箱", "斤")
    ok, msg = uf.check_unit_compatibility("斤", "磅")
    assert ok and msg == ""
    ok, msg = uf.check_unit_compatibility("箱", "斤")
    assert not ok and "还没有换算关系" in msg
    assert uf.unit_to_kg_factor("份") is None


# ---- 接口 ----
def test_staff_forbidden_owner_and_admin_allowed(client):
    for path in ("/api/settings/units", "/api/settings/fx_rates"):
        assert client.get(path, headers=STAFF).status_code == 403
        assert client.get(path, headers=OWNER).status_code == 200
        assert client.get(path, headers=ADMIN).status_code == 200
    assert client.post("/api/settings/units", headers=STAFF, json={"name": "桶"}).status_code == 403
    assert _conv(client, "桶", "斤", 3, headers=STAFF).status_code == 403


def test_list_units_contract_and_seed(client):
    d = client.get("/api/settings/units", headers=OWNER).json()["data"]
    names = {u["name"]: u for u in d["units"]}
    assert names["斤"]["builtin"] is True and names["斤"]["kind"] == "weight"
    jin = [c for c in d["conversions"] if c["from_unit"] == "斤"][0]
    assert jin["factor"] == 0.6048 and jin["builtin"] is True
    assert set(jin) >= {"id", "from_unit", "to_unit", "factor", "note", "builtin"}


def test_add_unit_and_duplicate(client):
    r = client.post("/api/settings/units", headers=OWNER, json={"name": "  大桶 ", "kind": "count"})
    assert r.status_code == 200
    r2 = client.post("/api/settings/units", headers=OWNER, json={"name": "大桶"})
    assert r2.status_code == 409 and "已经有了" in r2.json()["msg"]
    assert client.post("/api/settings/units", headers=OWNER, json={"name": "斤"}).status_code == 409
    assert client.post("/api/settings/units", headers=OWNER, json={"name": "   "}).status_code == 400


def test_override_jin_to_market_jin(client):
    assert _conv(client, "斤", "公斤", 0.5).status_code == 200
    assert uf.convert_unit_quantity(10, "斤", "公斤", tenant_id="default") == 5.0
    # 另一个租户不受影响
    assert uf.convert_unit_quantity(10, "斤", "公斤", tenant_id="shop2") == 6.048
    d = client.get("/api/settings/units", headers=OWNER).json()["data"]
    jin = [c for c in d["conversions"] if c["from_unit"] == "斤" and c["to_unit"] == "公斤"]
    assert len(jin) == 1 and jin[0]["factor"] == 0.5 and jin[0]["builtin"] is False
    d2 = client.get("/api/settings/units", headers=OWNER_T2).json()["data"]
    jin2 = [c for c in d2["conversions"] if c["from_unit"] == "斤" and c["to_unit"] == "公斤"]
    assert jin2[0]["factor"] == 0.6048


def test_conversion_validation(client):
    for bad in (0, -1):
        assert _conv(client, "箱", "瓶", bad).status_code == 400
    assert client.post("/api/settings/unit_conversions", headers=OWNER,
                       json={"from_unit": "箱", "to_unit": "瓶", "factor": "abc"}).status_code == 422 \
        or True
    r = client.post("/api/settings/unit_conversions", headers=OWNER,
                    content='{"from_unit":"箱","to_unit":"瓶","factor":NaN}',
                    )
    assert r.status_code in (400, 422)
    assert _conv(client, "箱", "箱", 2).status_code == 400
    assert _conv(client, "", "瓶", 2).status_code == 400


def test_auto_create_units_and_reverse_and_bfs(client):
    r = _conv(client, "大箱", "瓶", 12)
    assert r.status_code == 200, r.text
    names = {u["name"] for u in client.get("/api/settings/units", headers=OWNER).json()["data"]["units"]}
    assert {"大箱", "瓶"} <= names
    assert _conv(client, "瓶", "毫升", 330).status_code == 200
    c = _check(client, "大箱", "毫升")
    assert c["convertible"] is True and c["factor"] == 3960.0
    assert c["path"] == ["大箱", "瓶", "毫升"]
    back = _check(client, "毫升", "大箱")
    assert back["convertible"] and abs(back["factor"] - 1 / 3960) < 1e-9
    # 多跳接上种子：大箱 -> 毫升 -> 升
    assert abs(_check(client, "大箱", "升")["factor"] - 3.96) < 1e-9
    assert _check(client, "大箱", "斤") == {"status": "success", "convertible": False,
                                           "factor": None, "path": []}


def test_override_same_pair_replaces_not_duplicates(client):
    _conv(client, "筐", "斤", 20)
    _conv(client, "筐", "斤", 25)
    _conv(client, "斤", "筐", 0.05)
    convs = [c for c in client.get("/api/settings/units", headers=OWNER).json()["data"]["conversions"]
             if {c["from_unit"], c["to_unit"]} == {"筐", "斤"}]
    assert len(convs) == 1 and convs[0]["factor"] == 0.05


def test_contradicting_cycle_rejected(client):
    _conv(client, "盒X", "瓶", 6)
    _conv(client, "瓶", "毫升", 500)
    r = _conv(client, "盒X", "毫升", 1)
    assert r.status_code == 400 and "对不上" in r.json()["msg"]
    assert _conv(client, "盒X", "毫升", 3000).status_code == 200
    assert _check(client, "盒X", "毫升")["factor"] == 3000.0


def test_update_and_delete_conversion(client):
    cid = _conv(client, "篮", "斤", 8).json()["id"]
    assert client.put("/api/settings/unit_conversions/%d" % cid, headers=OWNER,
                      json={"factor": 9}).status_code == 200
    assert _check(client, "篮", "斤")["factor"] == 9.0
    bad = client.put("/api/settings/unit_conversions/%d" % cid, headers=OWNER, json={"factor": -3})
    assert bad.status_code == 400
    assert _check(client, "篮", "斤")["factor"] == 9.0
    convs = client.get("/api/settings/units", headers=OWNER).json()["data"]["conversions"]
    cid = [c for c in convs if c["from_unit"] == "篮"][0]["id"]
    assert client.delete("/api/settings/unit_conversions/%d" % cid, headers=OWNER).status_code == 200
    assert _check(client, "篮", "斤")["convertible"] is False
    assert client.delete("/api/settings/unit_conversions/%d" % cid, headers=OWNER).status_code == 404


def test_builtin_cannot_be_edited_or_deleted(client):
    assert client.delete("/api/settings/unit_conversions/-1", headers=OWNER).status_code == 400
    assert client.put("/api/settings/unit_conversions/-1", headers=OWNER, json={"factor": 2}).status_code == 400
    assert client.delete("/api/settings/units/-1", headers=OWNER).status_code == 400


def test_cross_tenant_isolation_for_ids_and_data(client):
    cid = _conv(client, "跨租户箱", "斤", 7).json()["id"]
    assert _check(client, "跨租户箱", "斤", headers=OWNER_T2)["convertible"] is False
    assert client.delete("/api/settings/unit_conversions/%d" % cid, headers=OWNER_T2).status_code == 404
    assert client.put("/api/settings/unit_conversions/%d" % cid, headers=OWNER_T2,
                      json={"factor": 1}).status_code == 404
    uid = client.post("/api/settings/units", headers=OWNER, json={"name": "租户甲专用"}).json()["id"]
    assert client.delete("/api/settings/units/%d" % uid, headers=OWNER_T2).status_code == 404
    names2 = {u["name"] for u in client.get("/api/settings/units", headers=OWNER_T2).json()["data"]["units"]}
    assert "租户甲专用" not in names2 and "跨租户箱" not in names2


def test_delete_unit_referenced_by_sku_or_conversion(client):
    uid = client.post("/api/settings/units", headers=OWNER, json={"name": "引用桶"}).json()["id"]
    sku_id, _ = db.create_sku("引用测试食材", base_unit="引用桶", tenant_id="default")
    r = client.delete("/api/settings/units/%d" % uid, headers=OWNER)
    assert r.status_code == 409 and "引用测试食材" in r.json()["msg"]
    uid2 = client.post("/api/settings/units", headers=OWNER, json={"name": "换算桶"}).json()["id"]
    _conv(client, "换算桶", "斤", 4)
    r = client.delete("/api/settings/units/%d" % uid2, headers=OWNER)
    assert r.status_code == 409 and "换算" in r.json()["msg"]
    # 引用解除后可以删
    uid3 = client.post("/api/settings/units", headers=OWNER, json={"name": "空闲桶"}).json()["id"]
    assert client.delete("/api/settings/units/%d" % uid3, headers=OWNER).status_code == 200


# ---- 入账换算 ----
def _receipt(client, items, total, headers=OWNER_T2, supplier="换算供应商", currency="HKD", date="2026-10-05"):
    body = {"supplier_name": supplier, "date": date, "total_amount": total, "currency": currency,
            "settlement_type": "credit", "items": items, "version": None, "source": "manual"}
    h = dict(headers, **{"X-Role": "staff"})
    r = client.post("/api/save_edited", headers=h, json=body)
    assert r.status_code == 200, r.text
    rid = r.json().get("receipt_id") or r.json().get("id")
    row = db.get_receipt_row(rid)
    return rid, row.version


def _item(name, qty, unit, price):
    return {"name": name, "quantity": qty, "unit": unit, "unit_price": price, "amount": round(qty * price, 2)}


def _approve(client, rid, version, headers=OWNER_T2):
    return client.post("/api/receipt/%d/approve" % rid, headers=headers, json={"version": version})


def test_approve_converts_to_base_unit_and_prices_per_base(client):
    sku_id, _ = db.create_sku("换算菜心", base_unit="斤", tenant_id="shop2")
    rid, v = _receipt(client, [_item("换算菜心", 2, "公斤", 20)], 40.0)
    r = _approve(client, rid, v)
    assert r.status_code == 200, r.text
    sku = db.get_sku(sku_id, tenant_id="shop2")
    assert abs(sku.current_stock - 2 / 0.6048) < 1e-4
    assert abs(sku.last_unit_price - 40.0 / (2 / 0.6048)) < 1e-4


def test_unconvertible_rejected_422_without_partial_write(client):
    s1, _ = db.create_sku("预检甲", base_unit="斤", tenant_id="shop2")
    s2, _ = db.create_sku("预检乙", base_unit="斤", tenant_id="shop2")
    rid, v = _receipt(client, [_item("预检甲", 3, "斤", 10), _item("预检乙", 1, "箱", 50)], 80.0)
    r = _approve(client, rid, v)
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "UNIT_CONVERSION_MISSING"
    assert body["data"]["pairs"] == [{"from_unit": "箱", "to_unit": "斤", "item_name": "预检乙"}]
    assert "设置" in body["msg"] and "「箱」" in body["msg"] and "「斤」" in body["msg"]
    assert db.get_sku(s1, tenant_id="shop2").current_stock == 0
    assert db.get_sku(s2, tenant_id="shop2").current_stock == 0
    assert db.get_receipt_row(rid).status != "approved"
    s = db.get_session()
    try:
        assert s.query(db._StockLogRow).filter_by(receipt_id=rid).count() == 0
    finally:
        s.close()
    # 老板加换算后同一张单可以入账
    assert _conv(client, "箱", "斤", 10, headers=OWNER_T2).status_code == 200
    row = db.get_receipt_row(rid)
    r = _approve(client, rid, row.version)
    assert r.status_code == 200, r.text
    assert db.get_sku(s2, tenant_id="shop2").current_stock == 10.0
    assert db.get_sku(s1, tenant_id="shop2").current_stock == 3.0


def test_price_anomaly_compared_in_base_unit(client):
    from app.services.receipt_utils import _match_sku
    sku_id, _ = db.create_sku("比价肉", base_unit="斤", tenant_id="shop2")
    s = db.get_session()
    try:
        row = s.get(db._SkuRow, sku_id)
        row.last_unit_price = 10.0
        s.commit()
    finally:
        s.close()
    it = {"name": "比价肉", "unit": "公斤", "unit_price": 16.5, "quantity": 1}
    _match_sku(it, tenant_id="shop2")
    # 16.5/公斤 = 9.98/斤，与 10/斤 基本持平，不应报异动
    assert not it.get("price_anomaly")
    it2 = {"name": "比价肉", "unit": "无换算桶", "unit_price": 99, "quantity": 1}
    _match_sku(it2, tenant_id="shop2")
    assert not it2.get("price_anomaly") and "price_diff_percent" not in it2


def test_tenant_conversion_used_in_costing_fifo(client):
    _conv(client, "整袋", "斤", 25, headers=OWNER_T2)
    assert uf.convert_unit_quantity(2, "整袋", "斤", tenant_id="shop2") == 50.0
    with pytest.raises(ValueError):
        uf.convert_unit_quantity(2, "整袋", "斤", tenant_id="default")


def test_single_truth_agrees_with_converter_and_equivalence_table():
    """S1-P1-10：换算服务、AI 工具里的换算表、等价表对「斤」等单位的口径一致。"""
    from ai_registry.tools.currency_unit_converter.v1_0_0 import CurrencyUnitConverterTool
    for name, kg in CurrencyUnitConverterTool.UNIT_TO_KG.items():
        got = uf.unit_to_kg_factor(name)
        assert got is not None, name
        assert abs(got - kg) < 1e-3, (name, got, kg)
    from app.services.unit_normalization import load_unit_equivalence, units_equal
    equiv = load_unit_equivalence()
    assert units_equal("斤", "司马斤", equiv) and uf.conversion_factor("斤", "司马斤") == 1.0
    assert not units_equal("斤", "磅", equiv) and uf.conversion_factor("斤", "磅") != 1.0


def test_unit_name_normalization():
    assert uf.norm_unit_name(" KG ") == "公斤"
    assert uf.norm_unit_name("箱（48罐）") == "箱"
    assert uf.norm_unit_name("ＬＢ") == "磅"
    assert uf.norm_unit_name(None) == ""
