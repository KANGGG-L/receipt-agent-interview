# -*- coding: utf-8 -*-
"""FIFO 批次库存与成本核算引擎（财务级高精度 + 多单位折算 + 冲销回滚）。

核心职责：
1. record_inbound_batch: 记录入库批次（收据审核 / 手动入库）
2. deduct_consumption_fifo: 按 FIFO 顺序逐批次扣减，支持跨批次价格波动、多单位换算、超卖暂估与尾差平衡
3. void_consumption_fifo: 反向冲销回滚批次剩余量与 SKU 库存
4. 多级度量衡单位精准换算（kg / g / 斤 / 司马斤 / 磅 / 升 / 毫升 / 份 等）
"""

from typing import Dict, List, Optional, Tuple
from app import db


# 单位折算表：统一归一到标准单位
UNIT_WEIGHT_TO_KG = {
    "kg": 1.0,
    "公斤": 1.0,
    "千克": 1.0,
    "g": 0.001,
    "克": 0.001,
    "mg": 0.000001,
    "毫克": 0.000001,
    "斤": 0.5,           # 市斤 (500g)
    "市斤": 0.5,
    "两": 0.05,
    "市两": 0.05,
    "司马斤": 0.6048,
    "司馬斤": 0.6048,
    "港斤": 0.6048,
    "司马两": 0.0378,
    "司馬兩": 0.0378,
    "港两": 0.0378,
    "港兩": 0.0378,
    "磅": 0.45359237,
    "lb": 0.45359237,
    "lbs": 0.45359237,
    "t": 1000.0,
    "吨": 1000.0,
    "噸": 1000.0,
}

UNIT_VOLUME_TO_L = {
    "l": 1.0,
    "升": 1.0,
    "liter": 1.0,
    "ml": 0.001,
    "毫升": 0.001,
}

DIMENSION_WEIGHT = set(UNIT_WEIGHT_TO_KG.keys())
DIMENSION_VOLUME = set(UNIT_VOLUME_TO_L.keys())
DIMENSION_COUNT = {
    "份", "件", "个", "個", "只", "隻", "条", "條", "包", "瓶", "罐", "盒", "碗", "杯", "碟", "支", "粒"
}


def check_unit_compatibility(u1: str, u2: str) -> Tuple[bool, str]:
    """校验两个单位是否同量纲可换算。"""
    u1_norm = (u1 or "").strip().lower()
    u2_norm = (u2 or "").strip().lower()
    if not u1_norm or not u2_norm:
        return False, "单位不能为空"
    if u1_norm == u2_norm:
        return True, ""
    w1, w2 = u1_norm in UNIT_WEIGHT_TO_KG, u2_norm in UNIT_WEIGHT_TO_KG
    if w1 and w2:
        return True, ""
    v1, v2 = u1_norm in UNIT_VOLUME_TO_L, u2_norm in UNIT_VOLUME_TO_L
    if v1 and v2:
        return True, ""
    return False, f"单位「{u1}」与「{u2}」属于不同度量体系（如重量与计件），无法自动折算"


def convert_unit_quantity(qty: float, from_unit: str, to_unit: str) -> float:
    """将数量从 from_unit 转换为 to_unit。
    
    若两个单位处于同一量纲（重量或体积），执行精准转换；
    若单位完全相同，保持原数值；
    若跨量纲或无法折算，抛出明确 ValueError。
    """
    if qty == 0.0:
        return 0.0
    u_from = (from_unit or "").strip().lower()
    u_to = (to_unit or "").strip().lower()

    if u_from == u_to:
        return float(qty)

    # 重量体系转换
    if u_from in UNIT_WEIGHT_TO_KG and u_to in UNIT_WEIGHT_TO_KG:
        kg_qty = qty * UNIT_WEIGHT_TO_KG[u_from]
        return round(kg_qty / UNIT_WEIGHT_TO_KG[u_to], 6)

    # 体积体系转换
    if u_from in UNIT_VOLUME_TO_L and u_to in UNIT_VOLUME_TO_L:
        l_qty = qty * UNIT_VOLUME_TO_L[u_from]
        return round(l_qty / UNIT_VOLUME_TO_L[u_to], 6)

    # 跨量纲不可换算，坚决阻断
    raise ValueError(f"跨量纲单位不可换算: {from_unit} -> {to_unit}")


class CostingService:
    """FIFO 批次库存与成本核算服务。"""

    convert_unit_quantity = staticmethod(convert_unit_quantity)

    @staticmethod
    def record_inbound_batch(
        session,
        sku_id: int,
        qty: float,
        unit_price: float,
        unit: str,
        date: Optional[str] = None,
        receipt_id: Optional[int] = None,
    ):
        """记录入库批次（写入 inventory_batches 表）。"""
        if session is None:
            raise ValueError("session is required")
        if not sku_id or qty <= 0:
            return None

        inbound_date = (date or "").strip() or db.now_iso()[:10]
        unit_val = (unit or "").strip()
        unit_cost = round(float(unit_price), 4)
        initial_qty = round(float(qty), 4)

        # 检查是否已存在同收据同 SKU 的批次（保证幂等）
        if receipt_id is not None:
            existing = session.query(db._InventoryBatchRow).filter_by(
                receipt_id=int(receipt_id),
                sku_id=int(sku_id),
            ).first()
            if existing:
                return existing

        batch = db._InventoryBatchRow(
            sku_id=int(sku_id),
            receipt_id=int(receipt_id) if receipt_id is not None else None,
            inbound_date=inbound_date,
            unit_cost=unit_cost,
            initial_qty=initial_qty,
            remaining_qty=initial_qty,
            unit=unit_val,
            is_closed=1 if initial_qty <= 0 else 0,
            is_estimated=0,
            created_at=db.now_iso(),
        )
        session.add(batch)
        session.flush()
        return batch

    @staticmethod
    def get_active_batches(session, sku_id: int):
        """获取指定 SKU 的可用未关闭批次，按 inbound_date ASC, id ASC 严格排序。"""
        return (
            session.query(db._InventoryBatchRow)
            .filter(
                db._InventoryBatchRow.sku_id == int(sku_id),
                db._InventoryBatchRow.is_closed == 0,
                db._InventoryBatchRow.remaining_qty > 0,
            )
            .order_by(
                db._InventoryBatchRow.inbound_date.asc(),
                db._InventoryBatchRow.id.asc(),
            )
            .all()
        )

    @staticmethod
    def deduct_consumption_fifo(
        session,
        sku_id: int,
        qty_needed: float,
        unit_needed: str,
        date: Optional[str] = None,
    ) -> Tuple[float, List[Dict]]:
        """按 FIFO 先进先出顺序扣减批次库存并计算成本。
        
        返回: (total_cost, details_list)
        details_list 包含每一段批次扣减的明细:
        [
            {
                "sku_id": int,
                "qty_consumed": float,
                "unit": str,
                "unit_cost": float,
                "total_cost": float,
                "batch_id": int or None,
                "batch_date": str,
            }
        ]
        """
        if session is None:
            raise ValueError("session is required")

        qty_needed = round(float(qty_needed), 4)
        if qty_needed <= 0:
            return 0.0, []

        unit_needed = (unit_needed or "").strip()
        date_str = (date or "").strip() or db.now_iso()[:10]

        active_batches = CostingService.get_active_batches(session, sku_id)
        details: List[Dict] = []
        remaining_needed = qty_needed
        total_cost = 0.0

        for b in active_batches:
            if remaining_needed <= 1e-6:
                break

            # 将批次当前剩余量折算到需求单位
            avail_in_needed = convert_unit_quantity(b.remaining_qty, b.unit, unit_needed)
            if avail_in_needed <= 0:
                b.is_closed = 1
                continue

            if avail_in_needed <= remaining_needed + 1e-6:
                # 批次被完全耗尽
                take_in_needed = avail_in_needed
                take_in_batch_unit = b.remaining_qty
                b.remaining_qty = 0.0
                b.is_closed = 1
            else:
                # 批次部分扣减
                take_in_needed = remaining_needed
                take_in_batch_unit = convert_unit_quantity(take_in_needed, unit_needed, b.unit)
                b.remaining_qty = round(b.remaining_qty - take_in_batch_unit, 4)
                if b.remaining_qty <= 0:
                    b.remaining_qty = 0.0
                    b.is_closed = 1

            batch_cost = round(take_in_batch_unit * b.unit_cost, 2)
            unit_cost_in_needed = round(batch_cost / take_in_needed, 4) if take_in_needed > 0 else 0.0
            total_cost += batch_cost

            details.append({
                "sku_id": int(sku_id),
                "qty_consumed": round(take_in_needed, 4),
                "unit": unit_needed,
                "unit_cost": unit_cost_in_needed,
                "total_cost": batch_cost,
                "batch_id": b.id,
                "batch_date": b.inbound_date,
            })
            remaining_needed = round(remaining_needed - take_in_needed, 4)

        # 若批次不足 / 超卖，生成暂估批次
        if remaining_needed > 1e-6:
            # 寻找参考单价：优先取最近批次，其次取 SKU last_unit_price
            ref_unit_cost = 0.0
            last_batch = (
                session.query(db._InventoryBatchRow)
                .filter(db._InventoryBatchRow.sku_id == int(sku_id))
                .order_by(db._InventoryBatchRow.id.desc())
                .first()
            )
            if last_batch and last_batch.unit_cost > 0:
                factor = convert_unit_quantity(1.0, unit_needed, last_batch.unit)
                ref_unit_cost = round(last_batch.unit_cost * factor, 4)
            else:
                sku_row = session.get(db._SkuRow, int(sku_id))
                if sku_row and sku_row.last_unit_price > 0:
                    factor = convert_unit_quantity(1.0, unit_needed, sku_row.base_unit)
                    ref_unit_cost = round(sku_row.last_unit_price * factor, 4)

            est_cost = round(remaining_needed * ref_unit_cost, 2)
            est_batch = db._InventoryBatchRow(
                sku_id=int(sku_id),
                receipt_id=None,
                inbound_date=date_str,
                unit_cost=ref_unit_cost,
                initial_qty=0.0,
                remaining_qty=0.0,
                unit=unit_needed,
                is_closed=1,
                is_estimated=1,
                created_at=db.now_iso(),
            )
            session.add(est_batch)
            session.flush()

            total_cost += est_cost
            details.append({
                "sku_id": int(sku_id),
                "qty_consumed": round(remaining_needed, 4),
                "unit": unit_needed,
                "unit_cost": ref_unit_cost,
                "total_cost": est_cost,
                "batch_id": est_batch.id,
                "batch_date": date_str,
            })
            remaining_needed = 0.0

        # 平衡分摊尾差
        total_cost = round(total_cost, 2)
        sum_details = round(sum(d["total_cost"] for d in details), 2)
        diff = round(total_cost - sum_details, 2)
        if details and abs(diff) > 0.0001:
            details[-1]["total_cost"] = round(details[-1]["total_cost"] + diff, 2)

        return total_cost, details

    @staticmethod
    def void_consumption_fifo(session, consumption_id: int) -> bool:
        """反向冲销已录入的消耗：作废记录并回滚批次剩余量与 SKU 当前库存。"""
        if session is None:
            raise ValueError("session is required")

        cons = session.get(db._DailyConsumptionRow, int(consumption_id))
        if not cons or cons.is_void == 1:
            return False

        cons.is_void = 1

        details = (
            session.query(db._DailyConsumptionDetailRow)
            .filter(db._DailyConsumptionDetailRow.consumption_id == cons.id)
            .all()
        )

        for dt in details:
            if dt.batch_id:
                batch = session.get(db._InventoryBatchRow, dt.batch_id)
                if batch and batch.is_estimated == 0:
                    qty_in_batch_unit = convert_unit_quantity(dt.qty_consumed, dt.unit, batch.unit)
                    batch.remaining_qty = round(batch.remaining_qty + qty_in_batch_unit, 4)
                    if batch.remaining_qty > 0:
                        batch.is_closed = 0

            # 恢复 SKU 当前库存
            sku = session.get(db._SkuRow, dt.sku_id)
            if sku:
                qty_in_sku_unit = convert_unit_quantity(dt.qty_consumed, dt.unit, sku.base_unit)
                sku.current_stock = round(sku.current_stock + qty_in_sku_unit, 4)

        return True
