# -*- coding: utf-8 -*-
"""I18N-08：分类稳定主键，以及未迁移库仍按字符串工作。"""

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from app.i18n_text import master_key
from _b2_helpers import hdr, new_tenant


def test_master_key_folds_category_and_supplier_examples():
    assert master_key("肉类") == master_key("肉類")
    assert master_key("嘉顿") == master_key("嘉頓")
    assert master_key("肉类") != master_key("蔬菜")


def test_traditional_category_reuses_simplified_sku_category():
    from app.main import app
    client = TestClient(app)
    tn = new_tenant()
    first = client.post("/api/inventory/skus", headers=hdr(tn), json={
        "name": "分类甲", "category": "肉类", "base_unit": "斤"})
    assert first.status_code == 200 and first.json()["status"] == "success", first.text
    second = client.post("/api/inventory/skus", headers=hdr(tn), json={
        "name": "分类乙", "category": "肉類", "base_unit": "斤"})
    assert second.status_code == 200 and second.json()["status"] == "success", second.text
    listed = client.get("/api/inventory", headers=hdr(tn), params={"category": "肉類"})
    assert listed.status_code == 200, listed.text
    rows = listed.json()["data"]
    names = {row["name"] for row in rows}
    assert names == {"分类甲", "分类乙"}
    assert {row["category"] for row in rows} == {"肉类"}


def test_traditional_supplier_does_not_create_a_second_row():
    from app.main import app
    client = TestClient(app)
    tn = new_tenant()
    created = client.post("/api/suppliers", headers=hdr(tn), json={"name": "嘉顿"})
    assert created.status_code == 200 and created.json()["status"] == "success", created.text
    again = client.post("/api/suppliers", headers=hdr(tn), json={"name": "嘉頓"})
    assert again.status_code == 200 and again.json()["status"] == "success", again.text
    assert again.json()["id"] == created.json()["id"]
    assert again.json().get("folded") is True
    listed = client.get("/api/suppliers", headers=hdr(tn), params={"q": "嘉頓"})
    assert listed.status_code == 200, listed.text
    rows = listed.json()["data"]
    assert len(rows) == 1
    assert rows[0]["name"] == "嘉顿"


def test_traditional_dish_category_reuses_simplified_label():
    from app.main import app
    client = TestClient(app)
    tn = new_tenant()
    body = {"price": 12, "ingredients": []}
    first = client.post("/api/dishes", headers=hdr(tn), json={
        "name": "折叠菜甲", "category": "肉类", **body})
    assert first.status_code == 200 and first.json()["status"] == "success", first.text
    second = client.post("/api/dishes", headers=hdr(tn), json={
        "name": "折叠菜乙", "category": "肉類", **body})
    assert second.status_code == 200 and second.json()["status"] == "success", second.text
    listed = client.get("/api/dishes", headers=hdr(tn), params={"category": "肉類"})
    assert listed.status_code == 200, listed.text
    rows = listed.json()["data"]
    assert {row["name"] for row in rows} == {"折叠菜甲", "折叠菜乙"}
    assert {row["category"] for row in rows} == {"肉类"}


def _client():
    from app.main import app
    return TestClient(app)


def _assert_no_internal_keys(payload):
    def walk(obj):
        if isinstance(obj, dict):
            assert "match_key" not in obj
            assert "master_key" not in obj
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)
    walk(payload)


def _registry(tenant_id, domain=None):
    from sqlalchemy import text
    from app import db
    sql = ("SELECT id, tenant_id, domain, label, match_key, system_default "
           "FROM category_registry WHERE tenant_id = :t")
    params = {"t": tenant_id}
    if domain:
        sql += " AND domain = :d"
        params["d"] = domain
    sql += " ORDER BY id"
    with db._engine.connect() as conn:
        return [tuple(row) for row in conn.execute(text(sql), params)]


def _sku_binding(sku_id):
    from sqlalchemy import text
    from app import db
    with db._engine.connect() as conn:
        row = conn.execute(text(
            "SELECT category, category_id FROM skus WHERE id = :i"),
            {"i": int(sku_id)}).fetchone()
    return (row[0], row[1]) if row else None


def test_category_path_predicate_without_opening_sqlite(tmp_path):
    from app import db
    demo_dir = os.path.realpath(os.path.dirname(db._default_db_path))
    repo_root = os.path.realpath(os.path.join(demo_dir, ".."))
    protected = [
        db._default_db_path,
        os.path.join(repo_root, "app.db"),
        os.path.join(repo_root, "receipts.db"),
        os.path.join(demo_dir, "receipts.db"),
    ]
    for path in protected:
        assert db.is_category_migration_protected(path) is True
    assert db.is_category_migration_protected("") is True
    assert db.is_category_migration_protected("receipt_demo.db") is True
    assert db.is_category_migration_protected("./receipt_demo.db") is True
    fresh = tmp_path / "synthetic-category.db"
    assert db.is_category_migration_protected(str(fresh)) is False
    link = tmp_path / "link-to-default.db"
    os.symlink(db._default_db_path, link)
    assert db.is_category_migration_protected(str(link)) is True


def test_category_choose_label_count_min_id_and_unicode():
    from app import db
    assert db.category_choose_label([(1, "肉類"), (2, "肉类"), (3, "肉类")]) == "肉类"
    assert db.category_choose_label([(2, "肉類"), (1, "肉类")]) == "肉类"
    assert db.category_choose_label([(1, "肉類"), (2, "肉类")]) == "肉類"
    assert db.category_choose_label([(5, "乙"), (5, "甲")]) == "乙"


def test_category_source_guard_has_empty_protected_branch():
    from app import db
    text = open(db.__file__, encoding="utf-8").read()
    needle = (
        "if is_category_migration_protected(DB_PATH):\n"
        "        pass\n"
        "    else:\n"
        "        _apply_category_registry_migration(_engine)"
    )
    assert text.count(needle) == 2
    assert "sqlite_sequence" not in text


def _seed_legacy_category_db(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE skus (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR,
            category VARCHAR,
            base_unit VARCHAR,
            min_stock_alert FLOAT,
            current_stock FLOAT,
            last_unit_price FLOAT,
            sku_code VARCHAR,
            active INTEGER,
            tenant_id VARCHAR
        );
        CREATE INDEX ix_skus_name ON skus (name);
        CREATE TABLE dishes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR,
            category VARCHAR,
            price FLOAT,
            description VARCHAR,
            status VARCHAR,
            version INTEGER,
            created_at VARCHAR,
            updated_at VARCHAR,
            tenant_id VARCHAR
        );
        CREATE INDEX ix_dishes_name ON dishes (name);
        CREATE INDEX ix_dishes_category ON dishes (category);
    """)
    sku_rows = [
        ("等量简", "肉类", "eq"),
        ("等量繁", "肉類", "eq"),
        ("多数简甲", "肉类", "many"),
        ("多数简乙", "肉类", "many"),
        ("多数繁", "肉類", "many"),
        ("最小繁", "肉類", "trad"),
        ("最小简", "肉类", "trad"),
        ("空分类", "", "empty"),
    ]
    conn.executemany(
        "INSERT INTO skus (name, category, base_unit, tenant_id, active) "
        "VALUES (?, ?, '斤', ?, 1)",
        sku_rows)
    dish_rows = [
        ("等量菜简", "肉类", "deq"),
        ("等量菜繁", "肉類", "deq"),
    ]
    conn.executemany(
        "INSERT INTO dishes (name, category, price, status, version, tenant_id) "
        "VALUES (?, ?, 10, 'active', 1, ?)",
        dish_rows)
    conn.commit()
    conn.close()


def test_ac6_migration_is_idempotent_and_keeps_assigned_rows(tmp_path):
    from sqlalchemy import text
    from app import db
    path = str(tmp_path / "migrate-me.db")
    _seed_legacy_category_db(path)
    db.DB_PATH = path
    db._make_engine()
    db.ensure_schema_current()

    def sku_map(tenant):
        with db._engine.connect() as conn:
            return {
                name: (category, category_id)
                for name, category, category_id in conn.execute(text(
                    "SELECT name, category, category_id FROM skus WHERE tenant_id = :t"),
                    {"t": tenant})
            }

    eq = sku_map("eq")
    assert eq["等量简"][0] == "肉类"
    assert eq["等量繁"][0] == "肉类"
    assert eq["等量简"][1] == eq["等量繁"][1]
    assert isinstance(eq["等量简"][1], int) and eq["等量简"][1] > 0
    assert eq["等量简"][1] != "肉类"
    assert eq["等量简"][1] != master_key("肉类")
    many = sku_map("many")
    assert {row[0] for row in many.values()} == {"肉类"}
    assert len({row[1] for row in many.values()}) == 1
    trad = sku_map("trad")
    assert trad["最小繁"][0] == "肉類"
    assert trad["最小简"][0] == "肉類"
    assert trad["最小繁"][1] == trad["最小简"][1]
    empty = sku_map("empty")["空分类"]
    assert empty[0] == ""
    assert empty[1] is None

    with db._engine.connect() as conn:
        dish_rows = list(conn.execute(text(
            "SELECT name, category, category_id FROM dishes WHERE tenant_id = 'deq'")))
    assert {row[1] for row in dish_rows} == {"肉类"}
    assert dish_rows[0][2] == dish_rows[1][2]
    with db._engine.connect() as conn:
        domains = {row[0] for row in conn.execute(text(
            "SELECT domain FROM category_registry WHERE label = '肉类'"))}
    assert "sku" in domains and "dish" in domains

    kept_id = eq["等量简"][1]
    with db._engine.begin() as conn:
        conn.execute(text(
            "UPDATE skus SET category = :c WHERE name = '等量简'"),
            {"c": "不应被改写"})
        conn.execute(text(
            "INSERT INTO dishes (name, category, price, status, version, tenant_id) "
            "VALUES ('稍后其他', '其他', 1, 'active', 1, 'later')"))
    db.ensure_schema_current()
    again = sku_map("eq")
    assert again["等量简"] == ("不应被改写", kept_id)
    assert again["等量繁"][1] == kept_id
    assert again["等量繁"][0] == "肉类"
    with db._engine.connect() as conn:
        later = conn.execute(text(
            "SELECT category, category_id FROM dishes WHERE name = '稍后其他'")).fetchone()
        other_rows = conn.execute(text(
            "SELECT id FROM category_registry WHERE label = '其他'")).fetchall()
    assert later[0] == "默认"
    assert isinstance(later[1], int) and later[1] > 0
    assert other_rows == []
    versions = []
    with db._engine.connect() as conn:
        versions = [row[0] for row in conn.execute(text(
            "SELECT name FROM schema_version"))]
    assert all("category" not in str(name) for name in versions)
    assert db.schema_version() == 5


def test_ac1_to_ac4_sku_identity_and_custom_label():
    client = _client()
    tn = new_tenant()
    first = client.post("/api/inventory/skus", headers=hdr(tn), json={
        "name": "身份甲", "category": "肉类", "base_unit": "斤"})
    second = client.post("/api/inventory/skus", headers=hdr(tn), json={
        "name": "身份乙", "category": "肉類", "base_unit": "斤"})
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    listed = client.get("/api/inventory", headers=hdr(tn), params={"category": "肉類"})
    assert listed.status_code == 200, listed.text
    _assert_no_internal_keys(listed.json())
    rows = listed.json()["data"]
    assert {row["name"] for row in rows} == {"身份甲", "身份乙"}
    assert {row["category"] for row in rows} == {"肉类"}
    assert rows[0]["id"] != rows[1]["id"]
    assert rows[0]["category_id"] == rows[1]["category_id"]
    cid = rows[0]["category_id"]
    assert isinstance(cid, int) and cid > 0
    assert cid != "肉类"
    assert cid != master_key("肉类")
    reg = [row for row in _registry(tn, "sku") if row[3] == "肉类"]
    assert len(reg) == 1
    assert reg[0][0] == cid
    assert reg[0][2] == "sku"

    veg = client.post("/api/inventory/skus", headers=hdr(tn), json={
        "name": "身份菜", "category": "蔬菜", "base_unit": "斤"})
    assert veg.status_code == 200, veg.text
    veg_id = client.get("/api/inventory", headers=hdr(tn), params={"category": "蔬菜"}).json()["data"]
    assert {row["name"] for row in veg_id} == {"身份菜"}
    assert veg_id[0]["category_id"] != cid

    frozen = client.post("/api/inventory/skus", headers=hdr(tn), json={
        "name": "冻货甲", "category": "冻货", "base_unit": "斤"})
    frozen_trad = client.post("/api/inventory/skus", headers=hdr(tn), json={
        "name": "冻货乙", "category": "凍貨", "base_unit": "斤"})
    assert frozen.status_code == 200 and frozen_trad.status_code == 200
    frozen_rows = client.get("/api/inventory", headers=hdr(tn), params={"category": "凍貨"}).json()["data"]
    assert {row["name"] for row in frozen_rows} == {"冻货甲", "冻货乙"}
    assert {row["category"] for row in frozen_rows} == {"冻货"}
    assert frozen_rows[0]["category_id"] != cid
    blank = client.post("/api/inventory/skus", headers=hdr(tn), json={
        "name": "无分类货", "category": "   ", "base_unit": "斤"})
    assert blank.status_code == 200, blank.text
    blank_row = client.get("/api/inventory/skus/%s" % blank.json()["id"], headers=hdr(tn)).json()["data"]
    assert blank_row["category"] == ""
    assert blank_row["category_id"] is None
    _assert_no_internal_keys(blank_row)


def test_ac5_dish_identity_default_and_restore():
    client = _client()
    tn = new_tenant()
    body = {"price": 12, "ingredients": []}
    first = client.post("/api/dishes", headers=hdr(tn), json={
        "name": "身份菜甲", "category": "肉类", **body})
    second = client.post("/api/dishes", headers=hdr(tn), json={
        "name": "身份菜乙", "category": "肉類", **body})
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    listed = client.get("/api/dishes", headers=hdr(tn), params={"category": "肉類"})
    rows = listed.json()["data"]
    _assert_no_internal_keys(listed.json())
    assert {row["name"] for row in rows} == {"身份菜甲", "身份菜乙"}
    assert {row["category"] for row in rows} == {"肉类"}
    assert rows[0]["id"] != rows[1]["id"]
    cid = rows[0]["category_id"]
    assert rows[1]["category_id"] == cid
    assert isinstance(cid, int) and cid > 0
    reg = [row for row in _registry(tn, "dish") if row[3] == "肉类"]
    assert len(reg) == 1 and reg[0][0] == cid

    updated = client.put("/api/dishes/%s" % first.json()["data"]["id"], headers=hdr(tn), json={
        "category": "肉類"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["category"] == "肉类"
    assert updated.json()["data"]["category_id"] == cid
    assert len([row for row in _registry(tn, "dish") if row[4] == master_key("肉类")]) == 1

    fresh = new_tenant()
    cats_a = client.get("/api/dishes/categories", headers=hdr(fresh, role="staff")).json()["data"]
    cats_b = client.get("/api/dishes/categories", headers=hdr(fresh, role="staff")).json()["data"]
    defaults_a = [row for row in cats_a if row["name"] == "默认" and row["count"] == 0]
    defaults_b = [row for row in cats_b if row["name"] == "默认" and row["count"] == 0]
    assert len(defaults_a) == 1 and len(defaults_b) == 1
    assert isinstance(defaults_a[0]["category_id"], int) and defaults_a[0]["category_id"] > 0
    assert defaults_a[0]["category_id"] == defaults_b[0]["category_id"]
    denied = client.delete("/api/dishes/categories/默认", headers=hdr(fresh))
    assert denied.status_code == 400
    assert denied.json()["msg"] == "系统默认分组无法删除"
    folded = client.delete("/api/dishes/categories/默認", headers=hdr(fresh))
    assert folded.status_code == 400
    assert folded.json()["msg"] == "系统默认分组无法删除"

    veg = client.post("/api/dishes", headers=hdr(tn), json={
        "name": "身份菜丙", "category": "蔬菜", **body})
    assert veg.status_code == 200, veg.text
    removed = client.delete("/api/dishes/categories/肉類", headers=hdr(tn), params={"replace_with": "蔬菜"})
    assert removed.status_code == 200, removed.text
    moved = client.get("/api/dishes", headers=hdr(tn), params={"category": "蔬菜"}).json()["data"]
    assert {row["name"] for row in moved} == {"身份菜甲", "身份菜乙", "身份菜丙"}
    assert {row["category"] for row in moved} == {"蔬菜"}
    assert len({row["category_id"] for row in moved}) == 1
    assert [row for row in _registry(tn, "dish") if row[3] == "肉类"] == []

    created = client.post("/api/dishes", headers=hdr(tn), json={
        "name": "快照菜", "category": "冻货", **body})
    dish_id = created.json()["data"]["id"]
    frozen_id = created.json()["data"]["category_id"]
    client.put("/api/dishes/%s" % dish_id, headers=hdr(tn), json={"category": "蔬菜"})
    restored = client.post("/api/dishes/%s/versions/1/restore" % dish_id, headers=hdr(tn), json={})
    assert restored.status_code == 200, restored.text
    again = client.get("/api/dishes/%s" % dish_id, headers=hdr(tn)).json()["data"]
    assert again["category"] == "冻货"
    assert again["category_id"] == frozen_id
    snap = client.get("/api/dishes/%s/versions/1" % dish_id, headers=hdr(tn)).json()["data"]
    blob = json.dumps(snap, ensure_ascii=False)
    assert "category_id" not in blob


def test_ac7_category_id_bind_mismatch_and_domain():
    client = _client()
    owner = new_tenant()
    other = new_tenant()
    made = client.post("/api/inventory/skus", headers=hdr(owner), json={
        "name": "绑定甲", "category": "肉类", "base_unit": "斤"})
    cid = client.get("/api/inventory", headers=hdr(owner)).json()["data"][0]["category_id"]
    only_id = client.post("/api/inventory/skus", headers=hdr(owner), json={
        "name": "绑定乙", "category_id": cid, "base_unit": "斤"})
    assert only_id.status_code == 200, only_id.text
    detail = client.get("/api/inventory/skus/%s" % only_id.json()["id"], headers=hdr(owner)).json()["data"]
    assert detail["category_id"] == cid
    assert detail["category"] == "肉类"
    before = _sku_binding(made.json()["id"])
    reg_before = _registry(owner, "sku")
    mismatch = client.patch("/api/inventory/skus/%s" % made.json()["id"], headers=hdr(owner), json={
        "category_id": cid, "category": "蔬菜"})
    assert mismatch.status_code == 400, mismatch.text
    body = mismatch.json()
    assert body["code"] == "CATEGORY_MISMATCH"
    assert body["msg"] == "代码和分类名称不是同一条"
    assert "match_key" not in body["msg"]
    assert _sku_binding(made.json()["id"]) == before
    assert _registry(owner, "sku") == reg_before

    foreign = client.post("/api/inventory/skus", headers=hdr(other), json={
        "name": "别租户货", "category_id": cid, "base_unit": "斤"})
    assert foreign.status_code == 400, foreign.text
    assert foreign.json()["code"] == "CATEGORY_NOT_FOUND"
    assert foreign.json()["msg"] == "分类不存在"
    assert client.get("/api/inventory", headers=hdr(other)).json()["data"] == []
    assert _registry(other, "sku") == []

    dish = client.post("/api/dishes", headers=hdr(owner), json={
        "name": "域隔离菜", "category": "肉类", "price": 8, "ingredients": []})
    dish_cid = dish.json()["data"]["category_id"]
    assert dish_cid != cid
    crossed = client.post("/api/inventory/skus", headers=hdr(owner), json={
        "name": "错域货", "category_id": dish_cid, "base_unit": "斤"})
    assert crossed.status_code == 400
    assert crossed.json()["code"] == "CATEGORY_NOT_FOUND"
    assert "错域货" not in {row["name"] for row in client.get("/api/inventory", headers=hdr(owner)).json()["data"]}

    numeric = client.post("/api/inventory/skus", headers=hdr(owner), json={
        "name": "数字标签货", "category": "12", "base_unit": "斤"})
    assert numeric.status_code == 200, numeric.text
    by_label = client.get("/api/inventory", headers=hdr(owner), params={"category": "12"}).json()["data"]
    by_id = client.get("/api/inventory", headers=hdr(owner), params={"category_id": cid}).json()["data"]
    assert {row["name"] for row in by_label} == {"数字标签货"}
    assert "绑定甲" not in {row["name"] for row in by_label}
    assert "数字标签货" not in {row["name"] for row in by_id}
    assert {row["category_id"] for row in by_id} == {cid}

    rebound = client.patch("/api/inventory/skus/%s" % numeric.json()["id"], headers=hdr(owner), json={
        "category": "蔬菜"})
    assert rebound.status_code == 200, rebound.text
    assert [row for row in _registry(owner, "sku") if row[3] == "12"] != []


def test_ac8_export_traditional_filter_keeps_label_cell():
    client = _client()
    tn = new_tenant()
    meat = client.post("/api/inventory/skus", headers=hdr(tn), json={
        "name": "导出肉", "category": "肉类", "base_unit": "斤"})
    other = client.post("/api/inventory/skus", headers=hdr(tn), json={
        "name": "导出菜", "category": "蔬菜", "base_unit": "斤"})
    assert meat.status_code == 200 and other.status_code == 200
    preview = client.post("/api/export/preview", headers=hdr(tn), json={
        "report_id": "inventory_stocktake",
        "params": {"category": "肉類"},
    })
    assert preview.status_code == 200, preview.text
    rows = preview.json()["data"]["preview_rows"]
    assert [row[1] for row in rows] == ["导出肉"]
    assert rows[0][2] == "肉类"
    assert not isinstance(rows[0][2], int)
    dish = client.post("/api/dishes", headers=hdr(tn), json={
        "name": "导出菜品", "category": "肉类", "price": 20, "ingredients": []})
    client.post("/api/dishes", headers=hdr(tn), json={
        "name": "导出素菜", "category": "蔬菜", "price": 18, "ingredients": []})
    assert dish.status_code == 200, dish.text
    margin = client.post("/api/export/preview", headers=hdr(tn), json={
        "report_id": "dish_cost_margin",
        "params": {"dish_category": "肉類", "cost_granularity": "period"},
    })
    assert margin.status_code == 200, margin.text
    names = [row[1] for row in margin.json()["data"]["preview_rows"]]
    assert names == ["导出菜品"]
    assert margin.json()["data"]["preview_rows"][0][2] == "肉类"


def test_ac9_supplier_alias_does_not_enter_category_registry():
    client = _client()
    tn = new_tenant()
    before = _registry(tn)
    created = client.post("/api/suppliers", headers=hdr(tn), json={"name": "供应商甲"})
    assert created.status_code == 200, created.text
    listed = client.get("/api/suppliers", headers=hdr(tn)).json()["data"]
    assert len(listed) == 1
    code = listed[0]["supplier_code"]
    again = client.post("/api/suppliers", headers=hdr(tn), json={"name": "供應商甲"})
    assert again.status_code == 200, again.text
    assert again.json()["id"] == created.json()["id"]
    assert again.json().get("folded") is True
    listed = client.get("/api/suppliers", headers=hdr(tn), params={"q": "供應商甲"}).json()["data"]
    assert len(listed) == 1
    assert listed[0]["name"] == "供应商甲"
    assert listed[0]["supplier_code"] == code
    assert _registry(tn) == before


def test_unique_conflict_reuses_existing_category_id():
    from app import db
    client = _client()
    tn = new_tenant()
    made = client.post("/api/inventory/skus", headers=hdr(tn), json={
        "name": "冲突甲", "category": "干货", "base_unit": "斤"})
    assert made.status_code == 200, made.text
    cid = client.get("/api/inventory", headers=hdr(tn)).json()["data"][0]["category_id"]
    session = db.get_session()
    real = db._category_find_by_key
    state = {"n": 0}

    def fake_find(*args, **kwargs):
        state["n"] += 1
        if state["n"] == 1:
            return None
        return real(*args, **kwargs)

    try:
        db._category_find_by_key = fake_find
        again_id, label = db._category_insert_registry(
            session, tn, "sku", "乾貨", master_key("干货"), 0)
        session.rollback()
    finally:
        db._category_find_by_key = real
        session.close()
    assert again_id == cid
    assert label == "干货"
    assert len([row for row in _registry(tn, "sku") if row[4] == master_key("干货")]) == 1


def test_protected_unmigrated_string_api_does_not_create_schema(tmp_path, monkeypatch):
    from app import db
    calls = []
    monkeypatch.setattr(db, "is_category_migration_protected", lambda path=None: True)
    monkeypatch.setattr(db, "_apply_category_registry_migration", lambda engine: calls.append(1))
    path = str(tmp_path / "protected-unmigrated.db")
    db.DB_PATH = path
    db._make_engine()
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO skus (name, category, base_unit, tenant_id, active) "
        "VALUES ('预置货', '肉类', '斤', 'pre', 1)")
    conn.commit()
    conn.close()
    db._make_engine()
    db.ensure_schema_current()
    db.ensure_schema_current()
    assert calls == []
    conn = sqlite3.connect(path)
    tables = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    sku_cols = [row[1] for row in conn.execute("PRAGMA table_info(skus)")]
    dish_cols = [row[1] for row in conn.execute("PRAGMA table_info(dishes)")]
    stored = conn.execute("SELECT category FROM skus WHERE name = '预置货'").fetchone()[0]
    conn.close()
    assert "category_registry" not in tables
    assert "category_id" not in sku_cols
    assert "category_id" not in dish_cols
    assert stored == "肉类"

    client = _client()
    tn = new_tenant()
    created = client.post("/api/inventory/skus", headers=hdr(tn), json={
        "name": "旧接口货", "category": "肉类", "base_unit": "斤"})
    assert created.status_code == 200, created.text
    folded = client.post("/api/inventory/skus", headers=hdr(tn), json={
        "name": "旧接口繁", "category": "肉類", "base_unit": "斤"})
    assert folded.status_code == 200, folded.text
    listed = client.get("/api/inventory", headers=hdr(tn), params={"category": "肉類"})
    assert listed.status_code == 200, listed.text
    rows = listed.json()["data"]
    assert {row["name"] for row in rows} == {"旧接口货", "旧接口繁"}
    assert {row["category"] for row in rows} == {"肉类"}
    assert {row["category_id"] for row in rows} == {None}
    patched = client.patch("/api/inventory/skus/%s" % created.json()["id"], headers=hdr(tn), json={
        "category": "肉類"})
    assert patched.status_code == 200, patched.text
    detail = client.get("/api/inventory/skus/%s" % created.json()["id"], headers=hdr(tn)).json()["data"]
    assert detail["category"] == "肉類"
    assert detail["category_id"] is None
    dish = client.post("/api/dishes", headers=hdr(tn), json={
        "name": "旧接口菜", "category": "肉类", "price": 9, "ingredients": []})
    assert dish.status_code == 200, dish.text
    assert dish.json()["data"]["category"] == "肉类"
    assert dish.json()["data"]["category_id"] is None
    # 未迁移导出仍是精确比较。PATCH 把第一条改成了「肉類」，第二条创建时沿用了「肉类」。
    exact = client.post("/api/export/preview", headers=hdr(tn), json={
        "report_id": "inventory_stocktake",
        "params": {"category": "肉类"},
    })
    assert [row[1] for row in exact.json()["data"]["preview_rows"]] == ["旧接口繁"]
    trad = client.post("/api/export/preview", headers=hdr(tn), json={
        "report_id": "inventory_stocktake",
        "params": {"category": "肉類"},
    })
    assert [row[1] for row in trad.json()["data"]["preview_rows"]] == ["旧接口货"]
    conn = sqlite3.connect(path)
    tables = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    sku_cols = [row[1] for row in conn.execute("PRAGMA table_info(skus)")]
    conn.close()
    assert "category_registry" not in tables
    assert "category_id" not in sku_cols
    assert calls == []
