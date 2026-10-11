# -*- coding: utf-8 -*-
"""安全加固第二批 D17：供应商与单据关联结构修复（归一 key 关联 + supplier_id 回填）。

覆盖：
1. 多变体（祥興系）归一后归属同一供应商（supplier_stats / get_supplier_receipts 计数一致）；
2. 孤儿名串不凭空建档，且可见性符合口径（无档案名字串保持 supplier_id=NULL、不进统计）；
3. supplier_id 回填幂等（二次执行为 no-op）且已接入既有初始化路径；
4. 空 supplier_name 不被匹配到任何档案；
5. 关联口径优先 supplier_id（已挂他供应商的行不得再按名字计入本供应商）；
6. merge_suppliers 用归一 key 匹配待合并单据并回填 supplier_id 到保留方；
7. 回填按租户 scope（跨租户同名不误挂）。

数据一律假数据（虚构商号，随机/固定组合防串扰）。
"""

import importlib
import os
import sys
import tempfile
import uuid

# 隔离环境：独立 DB（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="supplier_linkage_normalized_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_linkage.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")

_DEMO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo")
sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402

TENANT_A = "tenantA"
TENANT_B = "tenantB"

# 祥興系多变体：归一 key 均为「祥兴」（含英文括号注变体与繁简变体）
_XIANGXING_VARIANTS = [
    "祥興食品",
    "祥興食品測試",
    "祥兴食品",
    "Cheung Hing Fast Food Tableware (HK) Ltd.",
]


def _fresh_db():
    os.environ["DB_PATH"] = os.path.join(_TMP, f"lk_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    return db


def _sid_snapshot(_db):
    """全部单据的 id -> supplier_id 快照（幂等断言用）。"""
    s = _db.get_session()
    try:
        return {r.id: r.supplier_id for r in s.query(_db._ReceiptRow).all()}
    finally:
        s.close()


def _client():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    return TestClient(fastapi_app)


# -------------------------------------------------------------
# 1. 多变体归一归属同一供应商
# -------------------------------------------------------------
def test_multi_variant_receipts_link_to_same_supplier():
    from app.services.supplier_normalizer import canonical_supplier_key
    _db = _fresh_db()
    # 先确认这些变体确实共享同一归一 key（否则断言无意义）
    assert len({canonical_supplier_key(v) for v in _XIANGXING_VARIANTS}) == 1

    sup_id, err = _db.create_supplier("祥興食品", tenant_id="default")
    assert err is None
    rids = {v: _db.create_receipt(supplier_name=v, status="approved")
            for v in _XIANGXING_VARIANTS}

    _db._backfill_receipt_supplier_ids(_db._engine)

    for v, rid in rids.items():
        assert _db.get_receipt_row(rid).supplier_id == sup_id, \
            f"变体 {v} 应归一到同一供应商 id={sup_id}"

    # supplier_stats：全部变体计入同一供应商
    assert _db.supplier_stats(sup_id)["receipt_count"] == len(_XIANGXING_VARIANTS)
    # list_receipts_for_supplier：同样包含全部变体
    matched = _db.list_receipts_for_supplier(sup_id)
    assert len(matched) == len(_XIANGXING_VARIANTS)
    assert {x.id for x in matched} == set(rids.values())


# -------------------------------------------------------------
# 2. 孤儿名串不凭空建档，可见性符合口径
# -------------------------------------------------------------
def test_orphan_names_not_materialized():
    _db = _fresh_db()
    existing_id, err = _db.create_supplier("已有供应商", tenant_id="default")
    assert err is None
    n_sup_before = len(_db.list_suppliers(include_inactive=True))

    rid_ok = _db.create_receipt(supplier_name="已有供应商", status="approved")
    rid_orphan1 = _db.create_receipt(supplier_name="无档案供应商甲", status="approved")
    rid_orphan2 = _db.create_receipt(supplier_name="无档案供应商乙", status="approved")

    _db._backfill_receipt_supplier_ids(_db._engine)

    # 精确匹配成功
    assert _db.get_receipt_row(rid_ok).supplier_id == existing_id
    # 无档案名字串保持 NULL（绝不凭空建档）
    assert _db.get_receipt_row(rid_orphan1).supplier_id is None
    assert _db.get_receipt_row(rid_orphan2).supplier_id is None
    assert len(_db.list_suppliers(include_inactive=True)) == n_sup_before, \
        "孤儿名串不得触发供应商建档"
    # 可见性口径：孤儿名串不进任何供应商统计
    assert _db.supplier_stats(existing_id)["receipt_count"] == 1


# -------------------------------------------------------------
# 3. 回填幂等 + 接入既有初始化路径
# -------------------------------------------------------------
def test_backfill_idempotent_second_run_noop():
    _db = _fresh_db()
    _db.create_supplier("祥興食品", tenant_id="default")
    _db.create_receipt(supplier_name="祥興食品測試", status="approved")
    _db.create_receipt(supplier_name="无档案名", status="approved")

    _db._backfill_receipt_supplier_ids(_db._engine)
    snap1 = _sid_snapshot(_db)
    _db._backfill_receipt_supplier_ids(_db._engine)  # 二次执行
    snap2 = _sid_snapshot(_db)
    assert snap1 == snap2, "二次回填必须为 no-op（不得改写任何行）"


def test_backfill_wired_into_init_path():
    """回填须接入 _make_engine 初始化路径（reload 即触发，无需手工调用）。"""
    _db = _fresh_db()
    sup_id, err = _db.create_supplier("祥興食品", tenant_id="default")
    assert err is None
    rid = _db.create_receipt(supplier_name="祥興食品測試", status="approved")
    # 尚未走过初始化路径：新行 supplier_id 仍为 NULL
    assert _db.get_receipt_row(rid).supplier_id is None

    importlib.reload(_db)  # 触发 _make_engine() -> 既有幂等迁移路径
    assert _db.get_receipt_row(rid).supplier_id == sup_id


def test_empty_supplier_name_not_matched():
    _db = _fresh_db()
    target_id, err = _db.create_supplier("某供应商", tenant_id="default")
    assert err is None

    rid = _db.create_receipt(supplier_name="", status="approved")
    _db._backfill_receipt_supplier_ids(_db._engine)

    assert _db.get_receipt_row(rid).supplier_id is None, \
        "空 supplier_name 不得被匹配到任何档案"
    assert _db.supplier_stats(target_id)["receipt_count"] == 0


# -------------------------------------------------------------
# 4. 关联口径优先 supplier_id（已挂他供应商的行不再按名字计入）
# -------------------------------------------------------------
def test_linkage_prefers_supplier_id_over_name():
    _db = _fresh_db()
    sup_x, err = _db.create_supplier("祥興食品", tenant_id="default")
    assert err is None
    sup_y, err = _db.create_supplier("其他供应商", tenant_id="default")
    assert err is None

    r1 = _db.create_receipt(supplier_name="祥興食品", status="approved")
    r2 = _db.create_receipt(supplier_name="祥興食品測試", status="approved")
    # r3 名字归一命中 sup_x，但已明确挂到 sup_y —— 不得再按名字计入 sup_x
    r3 = _db.create_receipt(supplier_name="祥興食品測試", status="approved")
    _db.update_receipt(r3, supplier_id=sup_y)

    _db._backfill_receipt_supplier_ids(_db._engine)
    assert _db.get_receipt_row(r3).supplier_id == sup_y, "已挂行不得被回填改写"

    assert _db.supplier_stats(sup_x)["receipt_count"] == 2
    assert {r.id for r in _db.list_receipts_for_supplier(sup_x)} == {r1, r2}
    # sup_y：仅 r3
    assert _db.supplier_stats(sup_y)["receipt_count"] == 1


# -------------------------------------------------------------
# 5. merge_suppliers：归一 key 匹配 + 回填 supplier_id 到保留方
# -------------------------------------------------------------
def test_merge_suppliers_backfills_supplier_id_to_keep():
    _db = _fresh_db()
    keep_id, err = _db.create_supplier("金百加咖啡茶飲批發", tenant_id="default")
    assert err is None
    # create_supplier 会按归一 key 去重，故用原生行落一条同 key 的变体档案
    s = _db.get_session()
    try:
        drop = _db._SupplierRow(name="金百家", tenant_id="default",
                                supplier_code="SUP-DROP-TEST")
        s.add(drop)
        s.commit()
        drop_id = drop.id
    finally:
        s.close()

    rid = _db.create_receipt(supplier_name="金百家", status="approved")

    resp = _client().post("/api/suppliers/merge",
                          json={"keep_id": keep_id, "drop_id": drop_id},
                          headers={"X-Role": "owner"})
    assert resp.json()["status"] == "success", resp.text

    row = _db.get_receipt_row(rid)
    assert row.supplier_id == keep_id, "合并须把待合并单据 supplier_id 回填到保留方"
    assert row.supplier_name == "金百加咖啡茶飲批發", "展示名跟随保留方"
    assert _db.get_supplier(drop_id).active == 0, "被合并方应停用"


# -------------------------------------------------------------
# 6. 回填按租户 scope（跨租户同名不误挂）
# -------------------------------------------------------------
def test_backfill_scoped_by_tenant():
    _db = _fresh_db()
    sup_a, err = _db.create_supplier("跨租供应商", tenant_id=TENANT_A)
    assert err is None

    # B 租户同名单据：B 无档案，不得挂到 A 的档案上
    rid_b = _db.create_receipt(supplier_name="跨租供应商", status="approved",
                               tenant_id=TENANT_B)
    _db._backfill_receipt_supplier_ids(_db._engine)
    assert _db.get_receipt_row(rid_b).supplier_id is None

    # A 租户同名单据：精确匹配到 A 的档案
    rid_a = _db.create_receipt(supplier_name="跨租供应商", status="approved",
                               tenant_id=TENANT_A)
    _db._backfill_receipt_supplier_ids(_db._engine)
    assert _db.get_receipt_row(rid_a).supplier_id == sup_a


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
