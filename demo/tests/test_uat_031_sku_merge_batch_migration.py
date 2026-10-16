# -*- coding: utf-8 -*-
"""UAT-031 专项测试：SKU 合并批次迁移与 FIFO 成本溯源防暂估。

覆盖验收标准（1-5 项正向与安全边界）：
1. 源 SKU 带有 open 且 remaining=1 的批次，合并进主 SKU 后，receipt / inventory log / batch
   三类记录统一归属主 SKU，同时保留 source provenance（source_sku_id 与 note 溯源留痕）。
2. 合并后主 SKU 可用量 2kg，消耗 1.25kg 时按真实批次 FIFO 扣减，不出现 estimated_partial。
3. 失败注入（合并过程中任一步失败）必须整体事务回滚，不出现半合并状态：
   不得只改库存总量、不得留下已迁移一半的批次。
4. 其它 tenant 的批次数据必须完全不变；B 租户 baseline 前后对账零意外改动；跨租户合并严格拦截。
5. 历史已消耗 trace 必须保留；不得以删除原批次或改写历史来"修好"FIFO。
6. 存量孤儿批次修复严格遵循 dry-run + 单独批准约束，默认 dry-run 不做真实写操作。
"""

import importlib
import os
import sys
import tempfile
import uuid
import pytest

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

_TMP = tempfile.mkdtemp(prefix="uat031_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_uat031.db")
os.environ["AUTH_ENABLED"] = "0"

import app.db as db  # noqa: E402
from app.services.costing_service import CostingService  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch):
    """每个用例独占一个全新数据库，确保互不干扰且零生产污染。"""
    test_db = os.path.join(_TMP, f"uat031_{uuid.uuid4().hex[:8]}.db")
    monkeypatch.setenv("DB_PATH", test_db)
    db.DB_PATH = test_db
    db._make_engine()
    yield
    if os.path.exists(test_db):
        try:
            os.remove(test_db)
        except Exception:
            pass


def _mk_sku(name, base_unit="kg", stock=0.0, last_price=0.0, tenant_id="uat_a"):
    sid, err = db.create_sku(name, base_unit=base_unit, tenant_id=tenant_id)
    assert err is None, f"建 SKU 失败: {err}"
    if stock > 0 or last_price > 0:
        s = db.get_session()
        try:
            sku = s.get(db._SkuRow, sid)
            sku.current_stock = stock
            sku.last_unit_price = last_price
            s.commit()
        finally:
            s.close()
    return sid


def _mk_receipt_and_item(sku_id, name, qty, unit, unit_price, tenant_id="uat_a"):
    s = db.get_session()
    try:
        rec = db._ReceiptRow(
            supplier_name="测试供应商",
            receipt_date="2026-10-01",
            total_amount=qty * unit_price,
            status="approved",
            tenant_id=tenant_id,
            created_at=db.now_iso(),
        )
        s.add(rec)
        s.flush()
        it = db._ItemRow(
            receipt_id=rec.id,
            name=name,
            quantity=qty,
            unit=unit,
            unit_price=unit_price,
            amount=qty * unit_price,
            sku_id=sku_id,
            matched=1,
        )
        s.add(it)
        s.commit()
        return rec.id, it.id
    finally:
        s.close()


def _mk_stock_log(sku_id, name, qty, unit, amount, receipt_id, tenant_id="uat_a"):
    s = db.get_session()
    try:
        log = db._StockLogRow(
            sku_id=sku_id,
            name=name,
            qty=qty,
            unit=unit,
            amount=amount,
            vendor="测试供应商",
            date="2026-10-01",
            receipt_id=receipt_id,
            kind="in",
            note="正常入库",
            created_at=db.now_iso(),
            tenant_id=tenant_id,
        )
        s.add(log)
        s.commit()
        return log.id
    finally:
        s.close()


def _mk_batch(sku_id, qty, unit_price, unit="kg", date="2026-10-01", receipt_id=None):
    s = db.get_session()
    try:
        batch = CostingService.record_inbound_batch(
            session=s,
            sku_id=sku_id,
            qty=qty,
            unit_price=unit_price,
            unit=unit,
            date=date,
            receipt_id=receipt_id,
        )
        s.commit()
        return batch.id
    finally:
        s.close()


# -----------------------------------------------------------------------------
# 1. 核心正向：源 SKU open remaining=1 批次并入主 SKU，receipt/log/batch 统一主 SKU + source provenance
# -----------------------------------------------------------------------------
def test_req1_open_batch_and_records_migrated_with_source_provenance():
    # 建立主 SKU (牛肉) 与副 SKU (牛肉丸)，各自入库 1kg，批次 remaining_qty=1.0
    p_id = _mk_sku("本地新鲜牛肉", "kg", stock=1.0, last_price=40.0, tenant_id="uat_a")
    s_id = _mk_sku("手打牛肉丸", "kg", stock=1.0, last_price=50.0, tenant_id="uat_a")

    r_p, it_p = _mk_receipt_and_item(p_id, "本地新鲜牛肉", 1.0, "kg", 40.0, tenant_id="uat_a")
    log_p = _mk_stock_log(p_id, "本地新鲜牛肉", 1.0, "kg", 40.0, r_p, tenant_id="uat_a")
    b_p = _mk_batch(p_id, 1.0, 40.0, unit="kg", date="2026-10-01", receipt_id=r_p)

    r_s, it_s = _mk_receipt_and_item(s_id, "手打牛肉丸", 1.0, "kg", 50.0, tenant_id="uat_a")
    log_s = _mk_stock_log(s_id, "手打牛肉丸", 1.0, "kg", 50.0, r_s, tenant_id="uat_a")
    b_s = _mk_batch(s_id, 1.0, 50.0, unit="kg", date="2026-10-02", receipt_id=r_s)

    # 执行 SKU 合并：副 SKU s_id 并入主 SKU p_id
    res, err = db.merge_skus(p_id, [s_id], tenant_id="uat_a")
    assert err is None, f"合并失败: {err}"
    assert res is not None
    assert res["primary_sku_id"] == p_id
    assert res["new_stock"] == 2.0

    session = db.get_session()
    try:
        # 1. 验证主 SKU 与副 SKU 状态
        p_sku = session.get(db._SkuRow, p_id)
        s_sku = session.get(db._SkuRow, s_id)
        assert p_sku.current_stock == 2.0
        assert s_sku.current_stock == 0.0
        assert s_sku.active == 0

        # 2. 验证 receipt_items 统一归属主 SKU，且保留 source_sku_id 溯源
        it_s_row = session.get(db._ItemRow, it_s)
        assert it_s_row.sku_id == p_id, "副 SKU 明细行 sku_id 必须迁移至主 SKU"
        assert getattr(it_s_row, "source_sku_id", None) == s_id, "副 SKU 明细行必须保留合并前源 SKU provenance"

        it_p_row = session.get(db._ItemRow, it_p)
        assert it_p_row.sku_id == p_id, "主 SKU 明细行保持主 SKU 归属"

        # 3. 验证 inventory_log 统一归属主 SKU，且保留 source_sku_id 与 note 溯源
        log_s_row = session.get(db._StockLogRow, log_s)
        assert log_s_row.sku_id == p_id, "副 SKU 库存流水 sku_id 必须迁移至主 SKU"
        assert getattr(log_s_row, "source_sku_id", None) == s_id, "副 SKU 库存流水必须保留合并前源 SKU provenance"
        assert f"from_sku_id={s_id}" in (log_s_row.note or ""), "流水备注必须包含合并源 SKU 溯源留痕"

        log_p_row = session.get(db._StockLogRow, log_p)
        assert log_p_row.sku_id == p_id

        # 4. 验证 inventory_batches 统一归属主 SKU，且保留 source_sku_id 溯源
        b_s_row = session.get(db._InventoryBatchRow, b_s)
        assert b_s_row.sku_id == p_id, "副 SKU 批次 sku_id 必须迁移至主 SKU"
        assert b_s_row.remaining_qty == 1.0, "批次剩余数量必须完整保留"
        assert b_s_row.is_closed == 0, "未消耗批次必须保持 open 状态"
        assert getattr(b_s_row, "source_sku_id", None) == s_id, "批次必须保留合并前源 SKU provenance"

        b_p_row = session.get(db._InventoryBatchRow, b_p)
        assert b_p_row.sku_id == p_id
    finally:
        session.close()


# -----------------------------------------------------------------------------
# 2. 核心正向：合并后可用 2kg，消耗 1.25kg 不出现 estimated_partial
# -----------------------------------------------------------------------------
def test_req2_fifo_deduction_after_merge_no_estimated_partial():
    p_id = _mk_sku("本地新鲜牛肉", "kg", stock=1.0, last_price=40.0, tenant_id="uat_a")
    s_id = _mk_sku("手打牛肉丸", "kg", stock=1.0, last_price=50.0, tenant_id="uat_a")

    b_p = _mk_batch(p_id, 1.0, 40.0, unit="kg", date="2026-10-01")
    b_s = _mk_batch(s_id, 1.0, 50.0, unit="kg", date="2026-10-02")

    # 合并 SKU
    res, err = db.merge_skus(p_id, [s_id], tenant_id="uat_a")
    assert err is None

    session = db.get_session()
    try:
        # 消耗 1.25kg（大于单批次 1kg，但小于合计 2kg）
        cost, details = CostingService.deduct_consumption_fifo(
            session=session,
            sku_id=p_id,
            qty_needed=1.25,
            unit_needed="kg",
            date="2026-10-03",
        )
        session.commit()

        # 应按 FIFO 先扣第一批 1.0kg ($40)，再扣第二批 0.25kg ($50)
        expected_cost = round(1.0 * 40.0 + 0.25 * 50.0, 2)  # 52.50
        assert round(cost, 2) == expected_cost, f"成本核算不符: {cost} vs {expected_cost}"
        assert len(details) == 2, f"必须由两个真实批次组成，实际明细: {details}"

        # 检查明细中的批次引用与无暂估
        assert details[0]["batch_id"] == b_p
        assert details[0]["qty_consumed"] == 1.0
        assert details[1]["batch_id"] == b_s
        assert details[1]["qty_consumed"] == 0.25

        # 检查数据库中批次状态
        b_p_row = session.get(db._InventoryBatchRow, b_p)
        b_s_row = session.get(db._InventoryBatchRow, b_s)
        assert b_p_row.remaining_qty == 0.0
        assert b_p_row.is_closed == 1

        assert b_s_row.remaining_qty == 0.75
        assert b_s_row.is_closed == 0

        # 确认没有任何 is_estimated=1 的批次被创建
        est_batches = session.query(db._InventoryBatchRow).filter(
            db._InventoryBatchRow.sku_id == p_id,
            db._InventoryBatchRow.is_estimated == 1,
        ).all()
        assert len(est_batches) == 0, f"绝不得生成暂估批次: {est_batches}"
    finally:
        session.close()


# -----------------------------------------------------------------------------
# 3. 负向边界：失败注入（合并过程中任一步失败）必须整体事务回滚
# -----------------------------------------------------------------------------
def test_req3_failure_injection_transaction_rollback(monkeypatch):
    p_id = _mk_sku("主牛肉", "kg", stock=1.0, last_price=40.0, tenant_id="uat_a")
    s_id = _mk_sku("副牛肉丸", "kg", stock=1.0, last_price=50.0, tenant_id="uat_a")

    r_p, it_p = _mk_receipt_and_item(p_id, "主牛肉", 1.0, "kg", 40.0, tenant_id="uat_a")
    log_p = _mk_stock_log(p_id, "主牛肉", 1.0, "kg", 40.0, r_p, tenant_id="uat_a")
    b_p = _mk_batch(p_id, 1.0, 40.0, unit="kg", date="2026-10-01", receipt_id=r_p)

    r_s, it_s = _mk_receipt_and_item(s_id, "副牛肉丸", 1.0, "kg", 50.0, tenant_id="uat_a")
    log_s = _mk_stock_log(s_id, "副牛肉丸", 1.0, "kg", 50.0, r_s, tenant_id="uat_a")
    b_s = _mk_batch(s_id, 1.0, 50.0, unit="kg", date="2026-10-02", receipt_id=r_s)

    # 注入故障：在别名改指步骤抛出异常（模拟事务提交前末尾步骤崩溃）
    def crash_repoint(*a, **kw):
        raise RuntimeError("模拟别名改指数据库死锁/磁盘故障")

    monkeypatch.setattr(db, "_repoint_vendor_sku_alias", crash_repoint)

    # 执行合并，必须捕获或识别事务失败
    with pytest.raises(RuntimeError):
        db.merge_skus(p_id, [s_id], tenant_id="uat_a")

    # 验证数据库完整回滚：零半合并状态
    session = db.get_session()
    try:
        p_sku = session.get(db._SkuRow, p_id)
        s_sku = session.get(db._SkuRow, s_id)
        assert p_sku.current_stock == 1.0, "主 SKU 库存不得被部分累加"
        assert s_sku.current_stock == 1.0, "副 SKU 库存不得被清零"
        assert s_sku.active == 1, "副 SKU 状态不得被停用"

        # 批次不得迁移
        b_s_row = session.get(db._InventoryBatchRow, b_s)
        assert b_s_row.sku_id == s_id, "副 SKU 批次不得停留在主 SKU"

        # 明细行不得迁移
        it_s_row = session.get(db._ItemRow, it_s)
        assert it_s_row.sku_id == s_id, "副 SKU 明细行不得停留在主 SKU"

        # 库存流水不得迁移
        log_s_row = session.get(db._StockLogRow, log_s)
        assert log_s_row.sku_id == s_id, "副 SKU 流水不得停留在主 SKU"
    finally:
        session.close()


# -----------------------------------------------------------------------------
# 4. 隔离安全：其它 tenant 的批次数据完全不变，跨租户合并严格拒绝
# -----------------------------------------------------------------------------
def test_req4_other_tenant_batches_untouched_and_cross_tenant_rejected():
    # A 租户
    p_a = _mk_sku("A牛肉", "kg", stock=1.0, tenant_id="uat_a")
    s_a = _mk_sku("A牛肉丸", "kg", stock=1.0, tenant_id="uat_a")
    b_a = _mk_batch(s_a, 1.0, 50.0, unit="kg")

    # B 租户
    p_b = _mk_sku("B牛肉", "kg", stock=3.0, tenant_id="uat_b")
    s_b = _mk_sku("B牛肉丸", "kg", stock=5.0, tenant_id="uat_b")
    b_b1 = _mk_batch(p_b, 3.0, 30.0, unit="kg")
    b_b2 = _mk_batch(s_b, 5.0, 25.0, unit="kg")

    # 快照 B 租户数据
    session = db.get_session()
    try:
        b_b1_before = (session.get(db._InventoryBatchRow, b_b1).sku_id,
                       session.get(db._InventoryBatchRow, b_b1).remaining_qty)
        b_b2_before = (session.get(db._InventoryBatchRow, b_b2).sku_id,
                       session.get(db._InventoryBatchRow, b_b2).remaining_qty)
        p_b_stock_before = session.get(db._SkuRow, p_b).current_stock
        s_b_stock_before = session.get(db._SkuRow, s_b).current_stock
    finally:
        session.close()

    # 1. 尝试跨租户合并（将 B 租户的 s_b 合并到 A 租户的 p_a），必须被拦截
    res_cross, err_cross = db.merge_skus(p_a, [s_b], tenant_id="uat_a")
    assert res_cross is None
    assert err_cross == "SECONDARY_NOT_FOUND", f"跨租户合并必须返回 SECONDARY_NOT_FOUND: {err_cross}"

    # 尝试在 tenant_id 为 None 时跨租户合并，同样必须拒绝
    res_cross_none, err_cross_none = db.merge_skus(p_a, [s_b], tenant_id=None)
    assert res_cross_none is None

    # 2. 在 A 租户内部正常合并
    res_a, err_a = db.merge_skus(p_a, [s_a], tenant_id="uat_a")
    assert err_a is None

    # 3. 对账 B 租户数据：零改动
    session = db.get_session()
    try:
        b_b1_after = (session.get(db._InventoryBatchRow, b_b1).sku_id,
                      session.get(db._InventoryBatchRow, b_b1).remaining_qty)
        b_b2_after = (session.get(db._InventoryBatchRow, b_b2).sku_id,
                      session.get(db._InventoryBatchRow, b_b2).remaining_qty)
        assert b_b1_after == b_b1_before, "B 租户批次 1 绝不得发生变动"
        assert b_b2_after == b_b2_before, "B 租户批次 2 绝不得发生变动"
        assert session.get(db._SkuRow, p_b).current_stock == p_b_stock_before
        assert session.get(db._SkuRow, s_b).current_stock == s_b_stock_before
    finally:
        session.close()


# -----------------------------------------------------------------------------
# 5. 历史守护：历史已消耗 trace 必须保留；不得以删除原批次或改写历史来修好 FIFO
# -----------------------------------------------------------------------------
def test_req5_historical_consumed_traces_preserved():
    p_id = _mk_sku("主鲜牛肉", "kg", stock=1.0, last_price=40.0, tenant_id="uat_a")
    s_id = _mk_sku("副牛肉丸", "kg", stock=2.0, last_price=50.0, tenant_id="uat_a")

    b_p = _mk_batch(p_id, 1.0, 40.0, unit="kg", date="2026-10-01")
    b_s = _mk_batch(s_id, 2.0, 50.0, unit="kg", date="2026-10-02")

    session = db.get_session()
    try:
        # 在合并发生前，副 SKU 发生过一次真实消耗（消耗 1.0kg，剩余 1.0kg）
        cost_pre, details_pre = CostingService.deduct_consumption_fifo(
            session=session,
            sku_id=s_id,
            qty_needed=1.0,
            unit_needed="kg",
            date="2026-10-02",
        )
        session.commit()
        assert len(details_pre) == 1
        assert details_pre[0]["batch_id"] == b_s
        assert details_pre[0]["qty_consumed"] == 1.0

        # 模拟生成 daily_consumption_details 历史记录
        cons_detail = db._DailyConsumptionDetailRow(
            consumption_id=999,
            sku_id=s_id,
            qty_consumed=1.0,
            unit="kg",
            unit_cost=50.0,
            total_cost=50.0,
            batch_id=b_s,
            batch_date="2026-10-02",
        )
        session.add(cons_detail)
        # 更新副 SKU 账面库存
        s_row = session.get(db._SkuRow, s_id)
        s_row.current_stock = 1.0
        session.commit()
    finally:
        session.close()

    # 执行 SKU 合并
    res, err = db.merge_skus(p_id, [s_id], tenant_id="uat_a")
    assert err is None
    assert res["new_stock"] == 2.0  # 1.0 + 1.0

    session = db.get_session()
    try:
        # 验证历史已消耗记录依然完好存在，引用指向同一个 batch_id
        hist_dt = session.query(db._DailyConsumptionDetailRow).filter_by(consumption_id=999).first()
        assert hist_dt is not None, "历史消耗记录不得被删除"
        assert hist_dt.batch_id == b_s, "历史消耗引用的 batch_id 必须保持完好"
        assert hist_dt.qty_consumed == 1.0

        # 原批次 b_s 没有被物理删除，initial_qty 保持 2.0，当前 remaining_qty 为 1.0
        b_s_row = session.get(db._InventoryBatchRow, b_s)
        assert b_s_row is not None, "原批次绝不能被删除"
        assert b_s_row.initial_qty == 2.0
        assert b_s_row.remaining_qty == 1.0
        assert b_s_row.sku_id == p_id, "批次归集到主 SKU"

        # 主 SKU 此时可再扣减 2.0kg（主 SKU 原 1kg + 副 SKU 剩余 1kg）
        cost_post, details_post = CostingService.deduct_consumption_fifo(
            session=session,
            sku_id=p_id,
            qty_needed=2.0,
            unit_needed="kg",
            date="2026-10-03",
        )
        session.commit()
        assert len(details_post) == 2
        assert any(d["batch_id"] == b_s and d["qty_consumed"] == 1.0 for d in details_post)
    finally:
        session.close()


# -----------------------------------------------------------------------------
# 6. 约束守卫：存量孤儿批次修复默认 dry-run，未经明确单独批准不得执行真实写
# -----------------------------------------------------------------------------
def test_req6_orphan_batch_dry_run_default():
    # 构造历史遗留孤儿批次（属于 active=0 的 SKU）
    p_id = _mk_sku("老牛肉", "kg", stock=2.0, tenant_id="uat_a")
    s_id = _mk_sku("遗留停用SKU", "kg", stock=0.0, tenant_id="uat_a")

    # 手动置停 s_id 模拟历史半合并遗留
    session = db.get_session()
    try:
        s_sku = session.get(db._SkuRow, s_id)
        s_sku.active = 0
        session.commit()
    finally:
        session.close()

    # 属于停用 SKU 的孤儿批次
    b_orphan = _mk_batch(s_id, 1.0, 35.0, unit="kg")

    # 1. 只读巡检
    orphans = db.find_orphan_inventory_batches(tenant_id="uat_a")
    orphan_ids = [b["id"] for b in orphans]
    assert b_orphan in orphan_ids, "巡检必须发现停用 SKU 下遗留的孤儿批次"

    # 2. 默认调用 migrate_orphan_inventory_batches（dry_run=True, approved=False）
    report = db.migrate_orphan_inventory_batches(tenant_id="uat_a")
    assert report.get("dry_run") is True, "存量数据迁移必须默认 dry_run=True"
    assert report.get("approved") is False, "存量数据迁移必须默认 approved=False"
    assert report.get("status") == "PENDING_APPROVAL"

    # 验证批次依然留在原停用 SKU，未被未经授权自动修改
    session = db.get_session()
    try:
        b_row = session.get(db._InventoryBatchRow, b_orphan)
        assert b_row.sku_id == s_id, "未获批准前，真实数据绝不能被静默篡改"
    finally:
        session.close()
