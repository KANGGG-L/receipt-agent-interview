# -*- coding: utf-8 -*-
"""多级 BOM 递归展开（餐品含子配方）。

对外唯一实现：
- expand_recipe(session, dish_id, portions, tenant_id) -> {sku_id: {"qty","unit"}}
  递归展开 dish -> component（sku | dish），逐层应用 yield_rate
  （有效领料量 = 配方用量 / yield_rate），同 SKU 多路径聚合。
- validate_recipe_components(session, dish_id, components, tenant_id)
  建档/更新时校验：组件类型合法、子配方存在且属本租户、无环、深度合规。

环检测（访问栈）+ 最大深度（MAX_DEPTH=5）+ 自引用禁止；违反抛 ValueError。
"""

from typing import Dict, List, Optional

from app.services.unit_factors import convert_unit_quantity

MAX_DEPTH = 5
VALID_COMPONENT_TYPES = ("sku", "dish")

# 新建餐品（此刻尚无 id）在图校验中的虚拟根哨兵：其 depth 视为 1，
# 被引用的子配方组件处于 depth=2，与 update/restore 传真实 dish_id 的语义一致。
_VIRTUAL_ROOT = object()


def component_rows(session, dish_id) -> List:
    """按 id 升序取某餐品的配方组件行。"""
    from app import db
    return (
        session.query(db._DishIngredientRow)
        .filter(db._DishIngredientRow.dish_id == int(dish_id))
        .order_by(db._DishIngredientRow.id.asc())
        .all()
    )


def component_type_of(row) -> str:
    """组件类型（sku | dish）；兼容旧行缺列场景，默认 sku。"""
    return (getattr(row, "component_type", None) or "sku")


def component_id_of(row) -> Optional[int]:
    """组件 id；兼容旧行（component_id 缺省时回退 sku_id）。"""
    cid = getattr(row, "component_id", None)
    if cid is None:
        cid = getattr(row, "sku_id", None)
    return cid


def yield_of(row) -> float:
    """出成率（0,1]；非法/缺失按 1.0 处理，避免污染成本口径。"""
    try:
        yr = float(getattr(row, "yield_rate", None))
    except (TypeError, ValueError):
        return 1.0
    return yr if yr > 0 else 1.0


def effective_qty_of(row) -> float:
    """有效领料量 = 配方用量 / yield_rate。"""
    return float(getattr(row, "consumption_qty", 0.0) or 0.0) / yield_of(row)


def dish_exists(session, dish_id, tenant_id=None):
    """取本租户餐品行；不存在或跨租户返回 None。"""
    from app import db
    row = session.get(db._DishRow, int(dish_id))
    if row is None:
        return None
    if not db._tenant_ok(row, tenant_id):
        return None
    return row


def expand_recipe(session, dish_id, portions: float = 1.0,
                  tenant_id: Optional[str] = None) -> Dict[int, Dict]:
    """递归展开餐品配方为 {sku_id: {"qty": float, "unit": str}}。

    子配方以「份」计量：子配方份数 = 组件用量 / yield_rate × 父级份数，
    子层食材再各自按自身 yield_rate 折算，逐层生效。
    """
    if dish_id is None:
        raise ValueError("dish_id is required")
    result: Dict[int, Dict] = {}

    def _add(sku_id, qty, unit):
        unit = (unit or "").strip()
        if sku_id in result:
            cur = result[sku_id]
            if cur["unit"] == unit:
                cur["qty"] = round(cur["qty"] + qty, 4)
            else:
                try:
                    cur["qty"] = round(
                        cur["qty"] + convert_unit_quantity(qty, unit, cur["unit"]), 4)
                except Exception:
                    # 跨量纲聚合（异常配方）：保守累加，由上层校验暴露问题
                    cur["qty"] = round(cur["qty"] + qty, 4)
        else:
            result[sku_id] = {"qty": round(float(qty), 4), "unit": unit}

    def _walk(d_id, portions_, depth, stack):
        if depth > MAX_DEPTH:
            raise ValueError(f"配方嵌套超过最大深度 {MAX_DEPTH} 层")
        if d_id in stack:
            raise ValueError(f"配方存在循环引用（餐品 #{d_id} 重复出现在展开路径）")
        stack.add(d_id)
        try:
            for row in component_rows(session, d_id):
                ctype = component_type_of(row)
                cid = component_id_of(row)
                if cid is None:
                    continue
                eff = effective_qty_of(row) * float(portions_)
                if ctype == "dish":
                    if dish_exists(session, cid, tenant_id) is None:
                        raise ValueError(f"子配方 #{cid} 不存在或无权使用")
                    _walk(int(cid), eff, depth + 1, stack)
                else:
                    _add(int(cid), eff, getattr(row, "unit", ""))
        finally:
            stack.discard(d_id)

    _walk(int(dish_id), float(portions), 1, set())
    return result


def validate_recipe_components(session, dish_id, components,
                               tenant_id: Optional[str] = None) -> None:
    """校验待写入配方组件：类型/用量/出成率/子配方归属/无环/深度。违反抛 ValueError。

    `dish_id` 为真实餐品时以其为 DFS 根；新建餐品（`dish_id=None`）以虚拟根代替，
    组件仍从 depth=2 开始计入，故深度判据（MAX_DEPTH=5）与 update/restore 完全一致。
    """
    def _get(c, key, default=None):
        if isinstance(c, dict):
            return c.get(key, default)
        return getattr(c, key, default)

    for c in components:
        ctype = (_get(c, "component_type") or "sku")
        cid = _get(c, "component_id")
        if ctype not in VALID_COMPONENT_TYPES:
            raise ValueError(f"组件类型非法：{ctype}（仅支持 sku / dish）")
        if cid is None:
            raise ValueError("配方组件不能为空")
        qty = _get(c, "consumption_qty")
        if qty is None or float(qty) <= 0:
            raise ValueError("组件单份用量必须大于 0")
        yr_raw = _get(c, "yield_rate", 1.0)
        yr = 1.0 if yr_raw is None else float(yr_raw)
        if not (0 < yr <= 1):
            raise ValueError("出成率必须满足 0 < 出成率 <= 1")
        if ctype == "dish":
            if dish_id is not None and int(cid) == int(dish_id):
                raise ValueError("配方不能引用自身作为子配方")
            if dish_exists(session, cid, tenant_id) is None:
                raise ValueError(f"子配方 #{cid} 不存在或无权使用")

    # 环/深度 DFS：dish_id 为真实餐品时以其为根（depth=1）；
    # 新建餐品（dish_id=None）以虚拟根为根（depth=1），直接组件处于 depth=2。
    # 两种情形的深度语义与错误语义完全一致，避免「创建路径跳过校验」。
    override: Dict = {}
    if dish_id is not None:
        override[int(dish_id)] = list(components)
        root = int(dish_id)
    else:
        override[_VIRTUAL_ROOT] = list(components)
        root = _VIRTUAL_ROOT

    def _children(d_id):
        if d_id in override:
            return [((_get(c, "component_type") or "sku"), _get(c, "component_id"))
                    for c in override[d_id]]
        return [(component_type_of(r), component_id_of(r))
                for r in component_rows(session, d_id)]

    def _dfs(d_id, depth, stack):
        if depth > MAX_DEPTH:
            raise ValueError(f"配方嵌套超过最大深度 {MAX_DEPTH} 层")
        if d_id in stack:
            raise ValueError(f"配方存在循环引用（餐品 #{d_id}）")
        stack.add(d_id)
        try:
            for ctype, cid in _children(d_id):
                if ctype == "dish" and cid is not None:
                    _dfs(int(cid), depth + 1, stack)
        finally:
            stack.discard(d_id)

    _dfs(root, 1, set())
