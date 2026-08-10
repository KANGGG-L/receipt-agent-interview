# -*- coding: utf-8 -*-
"""幂等入库 + 成本核算 + AI 复盘入口（对齐完整版 T5 + 面试三业务能力）。

纪律：只有 owner 在复核 approve 后写库存；写 SKU 流水（inventory_log）；
重复 approve 幂等（状态机拦截）。
"""

from app import db


def apply_receipt_to_inventory(row):
    """approve 入账：把收据明细写入 SKU 库存（幂等，仅 approved 状态）。

    - 匹配到 SKU → 累加 current_stock + 记录价格历史
    - 未匹配/错配 → 重新匹配或自动建 SKU（完整版走 SKU 匹配/人工确认）
    """
    from app.services.receipt_utils import _normalize_sku_name

    items = db.get_receipt_items(row.id)
    for it in items:
        name = it["name"]
        sku_id = it.get("sku_id")

        # 校验已匹配 SKU 是否合理：SKU 核心词与商品核心词不一致 → 视为错配，重新匹配
        if sku_id:
            sku = db.get_sku(sku_id)
            item_core = _normalize_sku_name(name)
            sku_core = _normalize_sku_name(sku.name) if sku else ""
            # 单字核心词（如"茶"）不参与互含判断，视为错配 → 重新匹配/建
            if (not sku) or len(sku_core) < 2 or len(item_core) < 2 \
                    or (sku_core not in item_core and item_core not in sku_core):
                sku_id = None  # 错配 → 重新匹配

        if not sku_id:
            sku = db.find_sku_by_name(name)
            if sku is None:
                # 尝试核心词匹配现有 SKU（避免为近似商品建重复 SKU）
                item_core = _normalize_sku_name(name)
                if item_core and len(item_core) >= 2:
                    for cand in db.list_skus(include_inactive=True):
                        cand_core = _normalize_sku_name(cand.name)
                        if cand_core and len(cand_core) >= 2 and cand_core == item_core:
                            sku = cand
                            break
                if sku is None:
                    sku_id, _ = db.create_sku(name, base_unit=it.get("unit") or "")
                else:
                    sku_id = sku.id
            else:
                sku_id = sku.id
        # 回写明细行的 sku_id（否则前端显示"无 SKU"）
        if sku_id and it.get("id"):
            db.update_item_sku(it["id"], sku_id)
        db.apply_stock_log(
            sku_id=sku_id, name=name,
            qty=it.get("quantity", 0), unit=it.get("unit") or "",
            amount=it.get("amount", 0), vendor=row.supplier_name,
            date=row.receipt_date or "", receipt_id=row.id, kind="in",
        )


def cost_summary() -> dict:
    """成本核算：按商品累计 + 总成本。"""
    skus = db.list_skus(include_inactive=True)
    total = sum(s.current_stock * s.last_unit_price for s in skus)
    items = {}
    for s in skus:
        items[s.name] = {
            "qty": s.current_stock, "amount": s.current_stock * s.last_unit_price,
            "unit": s.base_unit, "vendor": "", "last_price": s.last_unit_price,
        }
    return {"total_cost": round(total, 2), "items": items}
