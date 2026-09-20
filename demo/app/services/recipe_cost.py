# -*- coding: utf-8 -*-
"""餐品配方成本唯一实现（成本口径单一来源）。

历史问题：`_dish_to_dict` 做了单位换算，而三处导出报表都是裸 `qty × price`，
同一配方在「详情 / 配方报表 / 日销售成本 / 毛利偏差」四处得到不同理论成本。

本模块提供唯一实现并供四处调用：
- compute_ingredient_cost：单品组件成本（有效领料量折算 × 最近单价）；
- compute_recipe_theory：整单理论成本 + 逐组件明细（sku 组件与子配方组件）。

子配方组件经 recipe_expand.expand_recipe 展开，与日消耗扣减共用同一展开口径。
读路径（详情/报表）不允许 500：跨量纲/子配方缺失一律降级为 (0.0, warning)。
"""

from typing import Dict, List, Optional, Tuple

from app.services.unit_factors import convert_unit_quantity
from app.services.recipe_expand import (
    component_id_of,
    component_type_of,
    component_rows,
    effective_qty_of,
    expand_recipe,
    dish_exists,
    yield_of,
)


def compute_ingredient_cost(session, sku, consumption_qty, unit,
                            yield_rate: float = 1.0) -> Tuple[float, str]:
    """单品组件成本 = 折算到 SKU 基准单位的有效用量 × 最近单价。

    有效领料量 = 配方用量 / yield_rate（出成率越低，实际领料越多）。
    跨量纲/无效单位返回 (0.0, warning)，不抛错（读路径不 500）。
    """
    if sku is None:
        return 0.0, "组件对应食材不存在"
    try:
        yr = float(yield_rate) if yield_rate else 1.0
        if yr <= 0:
            yr = 1.0
        effective_qty = float(consumption_qty or 0.0) / yr
        target_unit = sku.base_unit or unit
        converted = convert_unit_quantity(effective_qty, unit, target_unit)
    except ValueError as e:
        return 0.0, f"单位不可换算（{unit} -> {getattr(sku, 'base_unit', '')}）：{e}"
    except Exception as e:  # noqa: BLE001 - 读路径降级
        return 0.0, f"成本折算失败：{e}"
    cost = round(converted * float(sku.last_unit_price or 0.0), 2)
    return cost, ""


def _sku_component_entry(session, row) -> Dict:
    from app import db

    cid = component_id_of(row)
    sku = session.get(db._SkuRow, int(cid)) if cid is not None else None
    yr = yield_of(row)
    cost, warning = compute_ingredient_cost(
        session, sku, getattr(row, "consumption_qty", 0.0),
        getattr(row, "unit", ""), yr)
    return {
        "id": getattr(row, "id", None),
        "component_type": "sku",
        "component_id": cid,
        "component_name": sku.name if sku else "",
        "sku_id": cid,
        "sku_name": sku.name if sku else "",
        "sku_category": sku.category if sku else "",
        "sku_base_unit": sku.base_unit if sku else "",
        "sku_current_stock": sku.current_stock if sku else 0.0,
        "sku_last_unit_price": sku.last_unit_price if sku else 0.0,
        "consumption_qty": getattr(row, "consumption_qty", 0.0),
        "unit": getattr(row, "unit", "") or "",
        "yield_rate": yr,
        "loss_rate": round(1.0 - yr, 4),
        "notes": getattr(row, "notes", "") or "",
        "ingredient_cost": cost,
        "warning": warning,
        "expanded": [],
    }


def _dish_component_entry(session, row, tenant_id=None) -> Dict:
    from app import db

    cid = component_id_of(row)
    sub_dish = dish_exists(session, cid, tenant_id) if cid is not None else None
    yr = yield_of(row)
    sub_name = sub_dish.name if sub_dish else f"子配方#{cid}"
    cost = 0.0
    warning = ""
    expanded: List[Dict] = []
    try:
        if sub_dish is None:
            raise ValueError(f"子配方 #{cid} 不存在或无权使用")
        sub_map = expand_recipe(session, cid, effective_qty_of(row), tenant_id)
        for sku_id, info in sub_map.items():
            sku = session.get(db._SkuRow, int(sku_id))
            line_cost, line_warn = compute_ingredient_cost(
                session, sku, info.get("qty", 0.0), info.get("unit", ""))
            cost += line_cost
            if line_warn and not warning:
                warning = line_warn
            expanded.append({
                "sku_id": sku_id,
                "sku_name": sku.name if sku else "",
                "qty": info.get("qty", 0.0),
                "unit": info.get("unit", ""),
                "unit_price": sku.last_unit_price if sku else 0.0,
                "ingredient_cost": line_cost,
                "warning": line_warn,
            })
    except Exception as e:  # noqa: BLE001 - 读路径降级，不 500
        warning = f"子配方展开失败：{e}"
    return {
        "id": getattr(row, "id", None),
        "component_type": "dish",
        "component_id": cid,
        "component_name": sub_name,
        "sku_id": None,
        "sku_name": sub_name,
        "sku_category": "子配方",
        "sku_base_unit": "",
        "sku_current_stock": 0.0,
        "sku_last_unit_price": 0.0,
        "consumption_qty": getattr(row, "consumption_qty", 0.0),
        "unit": getattr(row, "unit", "") or "份",
        "yield_rate": yr,
        "loss_rate": round(1.0 - yr, 4),
        "notes": getattr(row, "notes", "") or "",
        "ingredient_cost": round(cost, 2),
        "warning": warning,
        "expanded": expanded,
    }


def compute_recipe_theory(session, dish_id, tenant_id: Optional[str] = None
                          ) -> Tuple[float, List[Dict]]:
    """整单理论成本（单份）+ 逐组件明细；子配方经 expand_recipe 展开。"""
    ingredients: List[Dict] = []
    total = 0.0
    for row in component_rows(session, dish_id):
        ctype = component_type_of(row)
        if ctype == "dish":
            entry = _dish_component_entry(session, row, tenant_id)
        else:
            entry = _sku_component_entry(session, row)
        total += float(entry.get("ingredient_cost") or 0.0)
        ingredients.append(entry)
    return round(total, 2), ingredients
