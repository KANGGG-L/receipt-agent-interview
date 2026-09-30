# -*- coding: utf-8 -*-
"""F3：供应商×SKU 别名一致性巡检（`scripts/check_vendor_sku_alias.py`）的判别式用例。

背景（why 需要这一批）：别名表被污染时**所有接口都正常返回**，只是匹配悄悄变差
（读取侧把死链/无供应商维度的行静默判为未命中并回落全局匹配）。四类异常的形态：
① 指向不存在 / 已停用 / 跨租户的 SKU；② 同 (租户, 供应商, 写法) 多条 active；
③ canonical 与目标 SKU 归一后不一致（疑似指向被翻转/重复 SKU）；④ 供应商维度缺失。
本批为每一类各造一条实物（外加若干条"干净"与"刻意失效"的对照行），断言都被检出 ——
所以**去掉任意一类的判定，对应用例必然失败**（反证见任务报告）。

同时覆盖：0 行时必须报「无污染可清」而不是报错；表不存在时如实报「无法巡检」
（不能把"表没建起来"含混成"无污染"）；租户收窄；CLI 的 --strict 退出码；
以及回填预演（候选集复用回填脚本的 plan()）。

全部离线假数据：模块级临时 DB + 临时 RAG 目录，零外部调用、零 live 库写入。
"""

import importlib
import json
import os
import sqlite3
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="check_vendor_sku_alias_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_check.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
for _p in (_DEMO_DIR, os.path.join(_DEMO_DIR, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

import app.db as db  # noqa: E402
import app.services.rag as rag  # noqa: E402
import check_vendor_sku_alias as checker  # noqa: E402

rag.PERSIST_DIR = os.environ["RAG_DIR"]
rag._vs = None
rag._vs_tenant = {}


@pytest.fixture(autouse=True)
def _reset_checker_caches():
    """每个用例一套全新的巡检缓存与沙箱（模块级缓存会跨用例串库）。"""
    def _reset():
        checker._NORMALIZER = None
        checker._SANDBOX.update({"dir": None, "db_path": None, "rag_dir": None})
    _reset()
    yield
    _reset()


def _fresh_db():
    """每个用例独立数据库文件（与 P1/P2 系列用例同款）。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, "check_%s.db" % uuid.uuid4().hex[:8])
    importlib.reload(db)
    rag.PERSIST_DIR = os.environ["RAG_DIR"]
    rag._vs = None
    rag._vs_tenant = {}
    return db.DB_PATH


# -------------------------------------------------------------
# 夹具：建出四类异常各一条 + 干净/刻意失效的对照行
# -------------------------------------------------------------
def _insert_alias(tenant_id="default", supplier_id=None, sku_id=None, raw_name="",
                  canonical_name="", status="active", valid_to=""):
    """直接 INSERT（绕开 ORM 的唯一索引语义），返回 alias id。"""
    conn = sqlite3.connect(db.DB_PATH)
    try:
        cur = conn.execute(
            "INSERT INTO vendor_sku_alias (tenant_id, supplier_id, sku_id, raw_name,"
            " canonical_name, unit, hit_count, valid_from, valid_to, status,"
            " source_ref, updated_at) VALUES (?,?,?,?,?,'',0,'',?,?,'test','')",
            (tenant_id, supplier_id, sku_id, raw_name, canonical_name, valid_to, status))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _drop_unique_index():
    """去掉唯一索引，模拟"索引缺失/迁移前建的表"（正常库不可能出现同键多条）。"""
    conn = sqlite3.connect(db.DB_PATH)
    try:
        conn.execute("DROP INDEX IF EXISTS ux_vendor_sku_alias_key")
        conn.commit()
    finally:
        conn.close()


def _set_sku_inactive(sku_id):
    conn = sqlite3.connect(db.DB_PATH)
    try:
        conn.execute("UPDATE skus SET active=0 WHERE id=?", (int(sku_id),))
        conn.commit()
    finally:
        conn.close()


def _build_anomaly_db():
    """四类异常各一条（另含对照行），返回 (db_path, 关键 id 字典)。"""
    _fresh_db()
    sup1, e1 = db.create_supplier("佳發行", tenant_id="default")
    assert e1 is None, e1
    main_sku, e2 = db.create_sku("本地牛腩", tenant_id="default")
    other_sku, e3 = db.create_sku("港式牛腩", tenant_id="default")
    dead_sku, e4 = db.create_sku("停用品", tenant_id="default")
    assert None not in (main_sku, other_sku, dead_sku), (e2, e3, e4)
    _set_sku_inactive(dead_sku)

    ids = {}
    # 干净行（对照组）：写法与目标 SKU 规范名一致、供应商与 SKU 都在、键唯一
    ids["clean"] = _insert_alias("default", sup1, main_sku, "本地牛腩", "本地牛腩")
    # ① 死链 A：指向不存在的 SKU
    ids["missing_sku"] = _insert_alias("default", sup1, 999999, "悬空写法", "悬空写法")
    # ① 死链 B：指向已停用 SKU（合并联动漏改指的形态）
    ids["inactive_sku"] = _insert_alias("default", sup1, dead_sku, "停用品", "停用品")
    # ① 死链 C：别名与目标 SKU 跨租户（读取侧 tenant_ok 判为死链）
    ids["tenant_mismatch"] = _insert_alias("t2", sup1, main_sku, "跨租户写法", "跨租户写法")
    # ② 同键多条：supplier_id 为 NULL 时 SQLite 唯一索引把 NULL 当互不相同 → 可重复共存
    ids["dup_null_a"] = _insert_alias("default", None, main_sku, "无供应商重复", "本地牛腩")
    ids["dup_null_b"] = _insert_alias("default", None, main_sku, "无供应商重复", "本地牛腩")
    # ② 同键多条：索引缺失的表（模拟迁移前/表外写入）
    _drop_unique_index()
    ids["dup_idx_a"] = _insert_alias("default", sup1, main_sku, "索引缺失重复", "本地牛腩")
    ids["dup_idx_b"] = _insert_alias("default", sup1, main_sku, "索引缺失重复", "本地牛腩")
    # ③ canonical 与目标 SKU 归一后不一致，且另有同租户 SKU 的归一形 == canonical
    #    （疑似被翻转指向重复 SKU）
    ids["flipped"] = _insert_alias("default", sup1, other_sku, "本吧", "本地牛腩")
    # ③ 同形态但无第三角（合法的缩写别名：canonical 是供应商写法的归一形）
    ids["abbrev"] = _insert_alias("default", sup1, other_sku, "油鸡", "走地雞")
    # ④ 供应商档案不存在 / 为 NULL
    ids["missing_supplier"] = _insert_alias("default", 88888, main_sku,
                                            "档案缺失写法", "本地牛腩")
    ids["null_supplier"] = _insert_alias("default", None, main_sku,
                                         "无供应商写法", "本地牛腩")
    # 刻意失效的行（不算异常）：retired / 已过期
    ids["retired"] = _insert_alias("default", sup1, 999999, "已退休写法", "已退休写法",
                                   status="retired")
    ids["expired"] = _insert_alias("default", sup1, main_sku, "已过期写法", "本地牛腩",
                                   valid_to="2000-01-01T00:00:00")
    return db.DB_PATH, {"sup1": int(sup1), "main_sku": int(main_sku),
                        "other_sku": int(other_sku), "dead_sku": int(dead_sku),
                        "ids": ids}


# -------------------------------------------------------------
# 判别式：四类各一条，逐类断言（去掉该类判定 → 对应用例必失败）
# -------------------------------------------------------------
def test_class1_dangling_sku_detected():
    path, _ = _build_anomaly_db()
    report = checker.check_vendor_sku_alias(db_path=path, simulate_backfill=False)
    check = report["checks"]["dangling_sku"]
    assert check["count"] == 4, check
    assert check["by_reason"] == {"missing_sku": 2, "inactive_sku": 1,
                                 "tenant_mismatch": 1}, check["by_reason"]
    assert report["verdict"] == "polluted"


def test_class2_duplicate_active_key_detected():
    path, ctx = _build_anomaly_db()
    report = checker.check_vendor_sku_alias(db_path=path, simulate_backfill=False)
    check = report["checks"]["duplicate_active_key"]
    keys = {(g["tenant_id"], g["supplier_id"], g["raw_name"]): g
            for g in check["groups"]}
    assert check["count"] == 2, check
    null_group = keys[("default", None, "无供应商重复")]
    assert null_group["active_rows"] == 2
    assert sorted(null_group["alias_ids"]) == sorted(
        [ctx["ids"]["dup_null_a"], ctx["ids"]["dup_null_b"]])
    idx_group = keys[("default", ctx["sup1"], "索引缺失重复")]
    assert idx_group["active_rows"] == 2


def test_class3_canonical_mismatch_detected_with_flip_triage():
    path, ctx = _build_anomaly_db()
    report = checker.check_vendor_sku_alias(db_path=path, simulate_backfill=False)
    check = report["checks"]["canonical_mismatch"]
    assert check["count"] == 2, check["items"]
    by_id = {it["alias_id"]: it for it in check["items"]}
    flipped = by_id[ctx["ids"]["flipped"]]
    assert flipped["sku_name"] == "港式牛腩" and flipped["canonical_name"] == "本地牛腩"
    # 第三角：同租户另有 active SKU 的归一形 == canonical → 疑似被翻转
    assert flipped["other_sku_ids"] == [ctx["main_sku"]]
    assert flipped["suspect_flipped_pointer"] is True
    abbrev = by_id[ctx["ids"]["abbrev"]]
    assert abbrev["other_sku_ids"] == []
    assert abbrev["suspect_flipped_pointer"] is False
    assert check["suspect_flipped_pointer"] == 1


def test_class4_missing_supplier_detected():
    path, _ = _build_anomaly_db()
    report = checker.check_vendor_sku_alias(db_path=path, simulate_backfill=False)
    check = report["checks"]["missing_supplier"]
    assert check["count"] == 5, check
    assert check["by_reason"] == {"missing_supplier": 1, "null_supplier": 3,
                                 "tenant_mismatch": 1}, check["by_reason"]


def test_lifecycle_rows_not_counted_as_duplicate_or_canonical():
    """retired / 已过期行：占 rows 计数，但不进 ②（唯一口径只看 active）与 ③。"""
    path, _ = _build_anomaly_db()
    report = checker.check_vendor_sku_alias(db_path=path, simulate_backfill=False)
    rows = report["rows"]
    assert rows["total"] == 14 and rows["inactive"] == 1 and rows["expired"] == 1
    assert rows["active"] == 12
    assert rows["checked"] == 12
    # ①/④ 对全部行统计（死链与状态无关）：retired 行指 999999 会计入 missing_sku
    assert report["checks"]["dangling_sku"]["by_reason"]["missing_sku"] == 2


def test_clean_db_reports_no_pollution():
    """只有干净行 → 明确输出「无污染可清」，且四类全 0。"""
    _fresh_db()
    sup, err = db.create_supplier("佳發行", tenant_id="default")
    assert err is None
    sku, err2 = db.create_sku("本地牛腩", tenant_id="default")
    assert err2 is None
    _insert_alias("default", sup, sku, "本地牛腩", "本地牛腩")
    report = checker.check_vendor_sku_alias(db_path=db.DB_PATH, simulate_backfill=False)
    assert report["findings_total"] == 0
    assert report["verdict"] == "clean"
    assert "无污染可清" in report["verdict_text"]
    assert report["table_present"] is True


def test_empty_table_reports_no_pollution_not_error():
    """live 现状：表存在但 0 行 → 报「无污染可清（别名表 0 行）」，不是报错。"""
    _fresh_db()
    report = checker.check_vendor_sku_alias(db_path=db.DB_PATH, simulate_backfill=False)
    assert report["verdict"] == "clean"
    assert report["verdict_text"] == "无污染可清（别名表 0 行）"
    assert report["db_error"] is None
    assert report["rows"]["total"] == 0


def test_missing_table_is_reported_not_treated_as_clean():
    """表不存在（迁移没跑）→ 如实报"无法巡检"，不得含混成"无污染"。"""
    db_file = os.path.join(_TMP, "no_table_%s.db" % uuid.uuid4().hex[:8])
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE dummy (id INTEGER)")
    conn.commit()
    conn.close()
    report = checker.check_vendor_sku_alias(db_path=db_file, simulate_backfill=False)
    assert report["table_present"] is False
    assert report["verdict"] == "no_table"
    assert "无法巡检" in report["verdict_text"]
    assert report["verdict"] != "clean"


def test_missing_db_is_reported():
    report = checker.check_vendor_sku_alias(
        db_path=os.path.join(_TMP, "nope_%s.db" % uuid.uuid4().hex[:8]),
        simulate_backfill=False)
    assert report["verdict"] == "no_db"
    assert report["db_error"]


def test_tenant_scope_filters_rows():
    path, _ = _build_anomaly_db()
    report = checker.check_vendor_sku_alias(db_path=path, tenant_id="t2",
                                            simulate_backfill=False)
    assert report["tenant_scope"] == "t2"
    assert report["rows"]["total"] == 1
    # 跨租户行在 t2 视角下：目标 SKU 与供应商都属 default → ① 与 ④ 各一条
    assert report["checks"]["dangling_sku"]["count"] == 1
    assert report["checks"]["missing_supplier"]["count"] == 1


def test_simulate_backfill_predicts_canonical_mismatch():
    """回填预演：候选集复用回填脚本 plan()，并预计第 ③ 类的条数（含样本）。"""
    _fresh_db()
    sup, err = db.create_supplier("佳發行", tenant_id="default")
    assert err is None
    sku, err2 = db.create_sku("本地牛腩", tenant_id="default")
    assert err2 is None
    rid = db.create_receipt_record(supplier_name="佳發行", supplier_id=sup,
                                   tenant_id="default", status="parsed")
    db.set_receipt_items(rid, [{
        "name": "本吧", "quantity": 1, "unit": "斤", "unit_price": 10.0,
        "amount": 10.0, "sku_id": int(sku), "matched": 1, "confidence": 0.9,
        "price_anomaly": 0, "price_anomaly_direction": "", "price_diff_percent": 0.0,
        "fuzzy_candidates": [], "entity_candidates": [],
    }])
    report = checker.check_vendor_sku_alias(db_path=db.DB_PATH, simulate_backfill=True)
    sim = report["backfill_simulation"]
    assert sim["error"] is None, sim
    assert sim["candidate_count"] == 1
    assert sim["predicted"] == {"dangling_sku": 0, "duplicate_active_key": 0,
                               "canonical_mismatch": 1, "missing_supplier": 0}
    assert sim["canonical_mismatch_samples"][0]["sku_name"] == "本地牛腩"
    # 巡检主结论（0 行别名表）不受预演影响
    assert report["verdict"] == "clean"


def test_cli_strict_exit_code_and_json(capsys):
    """CLI 接线：--json 输出可解析 JSON；--strict 在有异常时退出码 2。"""
    path, _ = _build_anomaly_db()
    assert checker.main(["--json", "--db-path", path,
                         "--no-simulate-backfill"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "polluted"
    assert checker.main(["--db-path", path, "--no-simulate-backfill",
                         "--strict"]) == 2
    capsys.readouterr()


def test_cli_reads_live_default_path_without_error(capsys):
    """缺省路径解析：不给 --db-path 时按 DB_PATH 指向的库巡检（测试里即临时库）。"""
    _fresh_db()
    assert checker.main(["--json", "--no-simulate-backfill"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["db_path"] == os.path.abspath(db.DB_PATH)
    assert payload["verdict"] == "clean"
