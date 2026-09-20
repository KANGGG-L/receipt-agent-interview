# -*- coding: utf-8 -*-
"""餐品管理与每日批量消耗扣减/冲销/成本走势端点。

接口列表：
1. GET /api/dishes: 餐品列表（含 BOM 配方、基准理论成本、当前毛利率）
2. GET /api/dishes/{id}: 单个餐品详情（含配方食材列表与关联 SKU 实时库存）
3. POST /api/dishes: 新建餐品及 BOM 配方
4. PUT /api/dishes/{id}: 更新餐品信息及配方
5. DELETE /api/dishes/{id}: 停用/删除餐品
6. GET /api/dishes/daily_consumption: 查询指定日期的餐品消耗记录、扣减明细与当日成本汇总
7. POST /api/dishes/daily_consumption/batch: 批量提交当日餐品消耗（原子 FIFO 扣减 + 生成流水）
8. POST /api/dishes/daily_consumption/{id}/void: 冲销/作废指定消耗记录（回滚批次剩余量与 SKU 库存）
9. GET /api/dishes/cost_analysis: 成本趋势分析（近 7/30 天真实成本变化曲线、总售出份数、总毛利）
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import OrderedDict, defaultdict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import or_

import logging

from app import db
from app.auth import require_role, resolve_account

logger = logging.getLogger("dishes")
from app.services.costing_service import CostingService, convert_unit_quantity, check_unit_compatibility
from app.services.recipe_expand import expand_recipe

router = APIRouter()


def _actor(request: Request) -> str:
    """操作人标识（BOM 版本快照 changed_by）：优先账号邮箱，其次角色名。"""
    try:
        acct = resolve_account(request) or {}
    except Exception:
        acct = {}
    return (acct.get("email") or acct.get("role")
            or request.headers.get("X-Role") or "unknown")


def _tenant_id(request: Request) -> str:
    """Gap E2 租户键：与 X-Role 同风格取请求头，缺省 default。"""
    return (request.headers.get("X-Tenant-Id")
            or request.headers.get("x-tenant-id") or "default").strip() or "default"


# -------------------------------------------------------------
# Pydantic 模型
# -------------------------------------------------------------
class DishIngredientItem(BaseModel):
    sku_id: Optional[int] = None
    component_type: Optional[str] = "sku"      # sku | dish（子配方）
    component_id: Optional[int] = None
    consumption_qty: float
    unit: str
    yield_rate: Optional[float] = 1.0          # 出成率；损耗率派生为 1 - yield_rate
    notes: Optional[str] = ""


class DishCreate(BaseModel):
    name: str
    category: Optional[str] = ""
    price: float = 0.0
    description: Optional[str] = ""
    status: Optional[str] = "active"
    ingredients: Optional[List[DishIngredientItem]] = []


class DishUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    price: Optional[float] = None
    description: Optional[str] = None
    status: Optional[str] = None
    ingredients: Optional[List[DishIngredientItem]] = None
    expected_version: Optional[int] = None     # 乐观锁：不一致返回 409 VERSION_CONFLICT


class DishRestoreBody(BaseModel):
    change_summary: Optional[str] = ""


class DailyConsumptionItem(BaseModel):
    dish_id: int
    quantity: float
    notes: Optional[str] = ""


class DailyConsumptionBatchCreate(BaseModel):
    date: Optional[str] = None
    notes: Optional[str] = ""
    items: List[DailyConsumptionItem]


# -------------------------------------------------------------
# 辅助函数
# -------------------------------------------------------------
def _dish_to_dict(session, dish_row, tenant_id: Optional[str] = None) -> Dict:
    """序列化餐品对象并计算理论成本与毛利率。

    成本口径走 `services.recipe_cost.compute_recipe_theory` 唯一实现
    （与导出报表、日消耗扣减共用），含单位换算、出成率与多级子配方展开。
    """
    from app.services.recipe_cost import compute_recipe_theory

    theoretical_cost, ingredients = compute_recipe_theory(
        session, dish_row.id, tenant_id)

    theoretical_cost = round(theoretical_cost, 2)
    price = round(dish_row.price or 0.0, 2)
    gross_profit = round(price - theoretical_cost, 2)
    gross_margin_rate = round((price - theoretical_cost) / price * 100, 2) if price > 0 else 0.0

    return {
        "id": dish_row.id,
        "name": dish_row.name,
        "category": dish_row.category or "",
        "price": price,
        "description": dish_row.description or "",
        "status": dish_row.status or "active",
        "version": int(dish_row.version or 1),
        "created_at": dish_row.created_at or "",
        "updated_at": dish_row.updated_at or "",
        "ingredients": ingredients,
        "theoretical_cost": theoretical_cost,
        "gross_profit": gross_profit,
        "gross_margin_rate": gross_margin_rate,
    }


def _component_field(ing, key, default=None):
    """组件字段读取：同时支持 Pydantic 模型对象与快照回滚传入的 dict。"""
    if isinstance(ing, dict):
        return ing.get(key, default)
    return getattr(ing, key, default)


def _normalize_component(ing):
    """归一组件三元组：兼容旧客户端（仅传 sku_id）默认 component_type='sku'。"""
    ctype = (_component_field(ing, "component_type") or "sku")
    ctype = str(ctype).strip() or "sku"
    cid = _component_field(ing, "component_id")
    if cid is None:
        cid = _component_field(ing, "sku_id")
    return ctype, cid


def _validate_recipe_payload(session, tenant_id, ingredients, dish_id=None):
    """建档/更新配方统一校验。

    返回 (error_body, status_code, normalized)：校验通过时 error_body 为 None
    且 normalized 为可直接落库的组件列表；失败时 normalized 为 None。
    覆盖：组件类型/重复、用量>0、出成率区间、SKU/子配方存在且属本租户、
    单位同量纲、多级无环且深度合规。入参兼容 Pydantic 模型与 dict（快照回滚）。
    """
    from app.services.recipe_expand import validate_recipe_components

    normalized = []
    seen = set()
    for ing in ingredients or []:
        ctype, cid = _normalize_component(ing)
        if ctype not in ("sku", "dish"):
            return {"status": "error", "code": "INVALID_COMPONENT_TYPE",
                    "msg": f"配方组件类型非法：{ctype}（仅支持食材 sku 或子配方 dish）"}, 400, None
        if cid is None:
            return {"status": "error", "code": "INVALID_COMPONENT",
                    "msg": "配方组件不能为空"}, 400, None
        key = (ctype, int(cid))
        if key in seen:
            return {"status": "error",
                    "msg": "配方中存在重复的组件（食材 SKU / 子配方）"}, 400, None
        seen.add(key)

        qty = _component_field(ing, "consumption_qty")
        if qty is None or float(qty) <= 0:
            return {"status": "error",
                    "msg": "食材单份消耗量必须大于 0"}, 400, None
        try:
            yr_raw = _component_field(ing, "yield_rate", 1.0)
            yr = 1.0 if yr_raw is None else float(yr_raw)
        except (TypeError, ValueError):
            return {"status": "error", "code": "INVALID_YIELD_RATE",
                    "msg": "出成率必须是 0 到 1 之间的数值"}, 400, None
        if not (0 < yr <= 1):
            return {"status": "error", "code": "INVALID_YIELD_RATE",
                    "msg": "出成率必须满足 0 < 出成率 <= 1（损耗率由 1 - 出成率 派生）"}, 400, None

        unit = (_component_field(ing, "unit") or "").strip()
        if ctype == "sku":
            sku = db.scoped(
                session.query(db._SkuRow).filter(db._SkuRow.id == int(cid)),
                db._SkuRow, tenant_id).first()
            if not sku:
                return {"status": "error", "code": "TENANT_SKU_NOT_FOUND",
                        "msg": f"配方中的食材 SKU #{cid} 不存在或无权使用"}, 400, None
            compat, _reason = check_unit_compatibility(unit, sku.base_unit)
            if not compat:
                return {"status": "error", "code": "UNIT_DIMENSION_MISMATCH",
                        "msg": f"食材「{sku.name}」的库存基准单位为「{sku.base_unit}」，"
                               f"与配方消耗单位「{unit}」无法换算。请使用相匹配的单位"
                               f"（例如均为重量或同为体积），或前往库存管理调整该食材的基本单位。"}, 400, None
        else:
            sub = session.get(db._DishRow, int(cid))
            if not sub or not db._tenant_ok(sub, tenant_id):
                return {"status": "error", "code": "TENANT_DISH_NOT_FOUND",
                        "msg": f"子配方 #{cid} 不存在或无权使用"}, 400, None

        normalized.append({
            "component_type": ctype,
            "component_id": int(cid),
            "consumption_qty": round(float(qty), 4),
            "unit": unit,
            "yield_rate": yr,
            "notes": (_component_field(ing, "notes") or "").strip(),
        })

    try:
        validate_recipe_components(session, dish_id, normalized, tenant_id)
    except ValueError as e:
        return {"status": "error", "code": "RECIPE_GRAPH_INVALID",
                "msg": str(e)}, 400, None
    return None, None, normalized


# -------------------------------------------------------------
# 餐品 CRUD 路由
# -------------------------------------------------------------
@router.get("/api/dishes")
def list_dishes(
    request: Request,
    q: str = "",
    category: str = "",
    status: str = "all",
):
    """查询餐品列表（含关联的 SKU BOM 配方、基准理论成本、当前毛利率）。"""
    require_role("staff")(request)
    session = db.get_session()
    try:
        query = db.scoped(session.query(db._DishRow), db._DishRow,
                          _tenant_id(request))
        if status and status != "all":
            query = query.filter(db._DishRow.status == status)
        if category:
            query = query.filter(db._DishRow.category == category)

        rows = query.order_by(db._DishRow.id.desc()).all()
        out = []
        q_clean = (q or "").strip().lower()

        for r in rows:
            if q_clean:
                name_clean = (r.name or "").lower()
                cat_clean = (r.category or "").lower()
                if q_clean not in name_clean and q_clean not in cat_clean:
                    continue
            out.append(_dish_to_dict(session, r, _tenant_id(request)))

        return {"status": "success", "data": out}
    finally:
        session.close()


@router.get("/api/dishes/cost_analysis")
def get_cost_analysis(
    request: Request,
    days: int = 30,
    dish_id: Optional[int] = None,
):
    """成本趋势分析（查看近 7/30 天各餐品由于食材价格波动引起的真实成本变化曲线、总售出份数、总毛利）。"""
    require_role("staff")(request)
    session = db.get_session()
    try:
        now_dt = datetime.now()
        start_dt = now_dt - timedelta(days=max(1, int(days)) - 1)
        start_date_str = start_dt.strftime("%Y-%m-%d")
        end_date_str = now_dt.strftime("%Y-%m-%d")

        query = (
            session.query(db._DailyConsumptionRow)
            .filter(
                db._DailyConsumptionRow.date >= start_date_str,
                db._DailyConsumptionRow.date <= end_date_str,
                db._DailyConsumptionRow.is_void == 0,
            )
        )
        if dish_id is not None:
            query = query.filter(db._DailyConsumptionRow.dish_id == int(dish_id))

        tenant_id = _tenant_id(request)
        query = _consumption_tenant_scope(query, tenant_id)
        consumptions = query.order_by(db._DailyConsumptionRow.date.asc(), db._DailyConsumptionRow.id.asc()).all()
        # 行级租户过滤：使用 _consumption_tenant_ok 严格阻断跨租户流水透视
        consumptions = [c for c in consumptions if _consumption_tenant_ok(session, c, tenant_id)]

        # 按 dish_id 聚合
        dish_map = {}
        # 预查所有相关的 dish (严格归属当前租户)
        dish_ids = list({c.dish_id for c in consumptions})
        all_dishes = session.query(db._DishRow).filter(db._DishRow.id.in_(dish_ids)).all() if dish_ids else []
        all_dishes = [d for d in all_dishes if db._tenant_ok(d, tenant_id)]
        dish_obj_map = {d.id: d for d in all_dishes}

        # 统计每个餐品的数据
        dish_stats = {}
        for d_id, d_obj in dish_obj_map.items():
            dish_dict = _dish_to_dict(session, d_obj, tenant_id)
            dish_stats[d_id] = {
                "id": d_id,
                "name": d_obj.name,
                "category": d_obj.category,
                "price": d_obj.price,
                "theoretical_cost": dish_dict["theoretical_cost"],
                "total_sold_quantity": 0.0,
                "total_cost": 0.0,
                "total_revenue": 0.0,
                "total_gross_profit": 0.0,
                "avg_unit_cost": 0.0,
                "avg_gross_margin_rate": 0.0,
                "cost_variance": 0.0,
                "history": [],
            }

        # 每日聚合记录
        dish_daily_map = defaultdict(lambda: {"quantity": 0.0, "total_cost": 0.0})

        for c in consumptions:
            if c.dish_id not in dish_stats:
                continue
            st = dish_stats[c.dish_id]
            st["total_sold_quantity"] = round(st["total_sold_quantity"] + c.quantity, 4)
            st["total_cost"] = round(st["total_cost"] + c.total_cost, 2)
            dish_price = st["price"]
            rev = round(c.quantity * dish_price, 2)
            st["total_revenue"] = round(st["total_revenue"] + rev, 2)

            key = (c.dish_id, c.date)
            dish_daily_map[key]["quantity"] = round(dish_daily_map[key]["quantity"] + c.quantity, 4)
            dish_daily_map[key]["total_cost"] = round(dish_daily_map[key]["total_cost"] + c.total_cost, 2)

        # 填充 history
        for (d_id, dt), vals in sorted(dish_daily_map.items(), key=lambda x: x[0][1]):
            st = dish_stats[d_id]
            qty = vals["quantity"]
            cost = vals["total_cost"]
            unit_cost = round(cost / qty, 2) if qty > 0 else 0.0
            rev = round(qty * st["price"], 2)
            gp = round(rev - cost, 2)
            gm = round(gp / rev * 100, 2) if rev > 0 else 0.0
            st["history"].append({
                "date": dt,
                "quantity": qty,
                "total_cost": cost,
                "unit_cost": unit_cost,
                "revenue": rev,
                "gross_profit": gp,
                "gross_margin_rate": gm,
            })

        # 计算综合平均指标
        total_sold = 0.0
        total_cost_all = 0.0
        total_rev_all = 0.0

        for st in dish_stats.values():
            if st["total_sold_quantity"] > 0:
                st["avg_unit_cost"] = round(st["total_cost"] / st["total_sold_quantity"], 2)
            st["total_gross_profit"] = round(st["total_revenue"] - st["total_cost"], 2)
            if st["total_revenue"] > 0:
                st["avg_gross_margin_rate"] = round(st["total_gross_profit"] / st["total_revenue"] * 100, 2)
            st["cost_variance"] = round(st["avg_unit_cost"] - st["theoretical_cost"], 2)

            total_sold = round(total_sold + st["total_sold_quantity"], 4)
            total_cost_all = round(total_cost_all + st["total_cost"], 2)
            total_rev_all = round(total_rev_all + st["total_revenue"], 2)

        total_gp_all = round(total_rev_all - total_cost_all, 2)
        overall_gm = round(total_gp_all / total_rev_all * 100, 2) if total_rev_all > 0 else 0.0

        has_zero_cost_batch = False
        estimated_batches_count = 0
        if consumptions:
            cons_ids = [c.id for c in consumptions]
            all_details = (
                session.query(db._DailyConsumptionDetailRow)
                .filter(db._DailyConsumptionDetailRow.consumption_id.in_(cons_ids))
                .all()
            )
            for dt in all_details:
                batch = session.get(db._InventoryBatchRow, dt.batch_id) if dt.batch_id else None
                is_est = batch.is_estimated if batch else (1 if not dt.batch_id else 0)
                if is_est == 1:
                    estimated_batches_count += 1
                if dt.unit_cost <= 0.0 or dt.total_cost <= 0.0:
                    has_zero_cost_batch = True

        data_integrity_status = "complete"
        data_integrity_msg = "所有核算食材均基于真实采购批次完成，数据完整"
        if has_zero_cost_batch:
            data_integrity_status = "zero_cost_alert"
            data_integrity_msg = "今日核算中包含未录入进货价的食材（暂估成本 $0）。当前毛利率可能偏高，请提醒老板尽快补录进货单据以还原真实利润。"
        elif estimated_batches_count > 0:
            data_integrity_status = "estimated_partial"
            data_integrity_msg = "部分食材因库存不足采用了历史参考价暂估核算。"

        summary = {
            "total_sold_quantity": total_sold,
            "total_cost": total_cost_all,
            "total_revenue": total_rev_all,
            "total_gross_profit": total_gp_all,
            "overall_gross_margin_rate": overall_gm,
            "active_dishes_count": len(dish_stats),
            "has_zero_cost_batch": has_zero_cost_batch,
            "estimated_batches_count": estimated_batches_count,
            "data_integrity_status": data_integrity_status,
            "data_integrity_msg": data_integrity_msg,
        }

        return {
            "status": "success",
            "data": {
                "days": int(days),
                "start_date": start_date_str,
                "end_date": end_date_str,
                "summary": summary,
                "dishes": list(dish_stats.values()),
            },
        }
    finally:
        session.close()


def _consumption_tenant_ok(session, cons, tenant_id) -> bool:
    """daily_dish_consumptions 行级租户归属校验。

    WS4：该表已补 tenant_id 列，写入侧显式落租户，归属优先按行自身列判定；
    仅历史空值行回退 join 关联数据（dish → SKU）判定，保证既有数据可见性不变：
    1. 首选行自身 tenant_id（权威归属）；
    2. 空值时回退关联 dish 的 tenant_id（消耗提交时 dish 已按租户校验）；
    3. dish 行缺失（被彻底删除）时，退回扣减明细关联 SKU 的 tenant_id；
    4. 皆无法归属时视为不匹配（宁可漏见，不跨租户泄漏）。
    tenant_id 为 None/空 不过滤（向后兼容）。
    """
    if tenant_id is None or not str(tenant_id).strip():
        return True
    row_tenant = getattr(cons, "tenant_id", None)
    if row_tenant:
        return str(row_tenant) == str(tenant_id)
    dish = session.get(db._DishRow, cons.dish_id)
    if dish is not None:
        return db._tenant_ok(dish, tenant_id)
    detail_rows = (
        session.query(db._DailyConsumptionDetailRow)
        .filter(db._DailyConsumptionDetailRow.consumption_id == cons.id)
        .all()
    )
    if not detail_rows:
        return False
    sku_rows = (
        session.query(db._SkuRow)
        .filter(db._SkuRow.id.in_({d.sku_id for d in detail_rows}))
        .all()
    )
    return any(db._tenant_ok(sku, tenant_id) for sku in sku_rows)


def _consumption_tenant_scope(query, tenant_id):
    """WS4 查询侧列过滤：命中本租户行，或 tenant_id 为空的历史行（交由行级回退判定）。"""
    if tenant_id is None or not str(tenant_id).strip():
        return query
    return query.filter(
        or_(
            db._DailyConsumptionRow.tenant_id == str(tenant_id),
            db._DailyConsumptionRow.tenant_id.is_(None),
            db._DailyConsumptionRow.tenant_id == "",
        )
    )


@router.get("/api/dishes/daily_consumption")
def get_daily_consumption(
    request: Request,
    date: Optional[str] = None,
):
    """查询指定日期的餐品消耗记录、扣减详情与当日成本汇总。"""
    require_role("staff")(request)
    target_date = (date or "").strip() or db.now_iso()[:10]
    tenant_id = _tenant_id(request)
    session = db.get_session()
    try:
        rows = (
            _consumption_tenant_scope(
                session.query(db._DailyConsumptionRow)
                .filter(db._DailyConsumptionRow.date == target_date),
                tenant_id)
            .order_by(db._DailyConsumptionRow.id.desc())
            .all()
        )
        # Gap E2 收尾 + WS4：按行归属租户过滤（行 tenant_id 优先，历史空值回退 join）
        rows = [r for r in rows if _consumption_tenant_ok(session, r, tenant_id)]

        consumptions = []
        total_quantity = 0.0
        total_cost = 0.0
        total_revenue = 0.0
        has_zero_cost_batch = False
        estimated_batches_count = 0

        for r in rows:
            dish = session.get(db._DishRow, r.dish_id)
            dish_name = dish.name if dish else f"未知餐品#{r.dish_id}"
            dish_price = dish.price if dish else 0.0
            dish_category = dish.category if dish else ""

            # 查询关联的扣减明细
            details_rows = (
                session.query(db._DailyConsumptionDetailRow)
                .filter(db._DailyConsumptionDetailRow.consumption_id == r.id)
                .all()
            )
            details = []
            for dt in details_rows:
                sku = session.get(db._SkuRow, dt.sku_id)
                batch = session.get(db._InventoryBatchRow, dt.batch_id) if dt.batch_id else None
                is_estimated = batch.is_estimated if batch else (1 if not dt.batch_id else 0)
                is_zero_cost = (dt.unit_cost <= 0.0 or dt.total_cost <= 0.0)
                if is_estimated == 1:
                    estimated_batches_count += 1
                if is_zero_cost:
                    has_zero_cost_batch = True

                details.append({
                    "id": dt.id,
                    "sku_id": dt.sku_id,
                    "sku_name": sku.name if sku else f"SKU#{dt.sku_id}",
                    "qty_consumed": dt.qty_consumed,
                    "unit": dt.unit,
                    "unit_cost": dt.unit_cost,
                    "total_cost": dt.total_cost,
                    "batch_id": dt.batch_id,
                    "batch_date": dt.batch_date,
                    "is_estimated": is_estimated,
                    "is_zero_cost": is_zero_cost,
                })

            item_rev = round(r.quantity * dish_price, 2)
            item_gp = round(item_rev - r.total_cost, 2)
            item_gm = round(item_gp / item_rev * 100, 2) if item_rev > 0 else 0.0

            consumptions.append({
                "id": r.id,
                "date": r.date,
                "dish_id": r.dish_id,
                "dish_name": dish_name,
                "dish_category": dish_category,
                "dish_price": dish_price,
                "quantity": r.quantity,
                "unit_cost": r.unit_cost,
                "total_cost": r.total_cost,
                "revenue": item_rev,
                "gross_profit": item_gp,
                "gross_margin_rate": item_gm,
                "notes": r.notes or "",
                "is_void": r.is_void,
                "created_at": r.created_at,
                "details": details,
            })

            if r.is_void == 0:
                total_quantity = round(total_quantity + r.quantity, 4)
                total_cost = round(total_cost + r.total_cost, 2)
                total_revenue = round(total_revenue + item_rev, 2)

        gross_profit = round(total_revenue - total_cost, 2)
        gross_margin_rate = round(gross_profit / total_revenue * 100, 2) if total_revenue > 0 else 0.0

        data_integrity_status = "complete"
        data_integrity_msg = "所有核算食材均基于真实采购批次完成，数据完整"
        if has_zero_cost_batch:
            data_integrity_status = "zero_cost_alert"
            data_integrity_msg = "今日核算中包含未录入进货价的食材（暂估成本 $0）。当前毛利率可能偏高，请提醒老板尽快补录进货单据以还原真实利润。"
        elif estimated_batches_count > 0:
            data_integrity_status = "estimated_partial"
            data_integrity_msg = "部分食材因库存不足采用了历史参考价暂估核算。"

        summary = {
            "total_quantity": total_quantity,
            "total_cost": total_cost,
            "total_revenue": total_revenue,
            "gross_profit": gross_profit,
            "gross_margin_rate": gross_margin_rate,
            "records_count": len(rows),
            "void_count": sum(1 for r in rows if r.is_void == 1),
            "has_zero_cost_batch": has_zero_cost_batch,
            "estimated_batches_count": estimated_batches_count,
            "data_integrity_status": data_integrity_status,
            "data_integrity_msg": data_integrity_msg,
        }

        return {
            "status": "success",
            "data": {
                "date": target_date,
                "consumptions": consumptions,
                "summary": summary,
            },
        }
    finally:
        session.close()


@router.get("/api/dishes/categories")
def list_dish_categories(request: Request):
    """查询当前租户餐品分类（distinct 非空）及每类引用餐品数。"""
    require_role("staff")(request)
    session = db.get_session()
    try:
        rows = db.scoped(
            session.query(db._DishRow),
            db._DishRow, _tenant_id(request)).all()
        counter = defaultdict(int)
        for r in rows:
            cat = (r.category or "").strip()
            if cat:
                counter[cat] += 1
        data = [
            {"name": name, "count": count}
            for name, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        return {"status": "success", "data": data}
    finally:
        session.close()


@router.delete("/api/dishes/categories/{name}")
def delete_dish_category(name: str, request: Request, replace_with: str = "其他"):
    """删除餐品分类：把引用该分类的餐品 category 改写为 replace_with（空字符串表示清空归类），返回受影响条数。"""
    require_role("owner")(request)
    session = db.get_session()
    try:
        cat = name.strip()
        if not cat:
            return JSONResponse(status_code=400, content={"status": "error", "msg": "分类名称不能为空"})
        replace = (replace_with or "").strip()
        if replace == cat:
            return JSONResponse(status_code=400, content={"status": "error", "msg": "请选择一个与被删除分类不同的替代分类"})
        rows = db.scoped(
            session.query(db._DishRow).filter(db._DishRow.category == cat),
            db._DishRow, _tenant_id(request)).all()
        if not rows:
            return JSONResponse(status_code=404, content={"status": "error", "msg": "分类不存在"})
        affected = 0
        for r in rows:
            r.category = replace
            r.updated_at = db.now_iso()
            affected += 1
        session.commit()
        msg = f"分类「{cat}」已删除，{affected} 道餐品的分类已改为「{replace}」" if replace else f"分类「{cat}」已删除，{affected} 道餐品已清空分类"
        return {
            "status": "success",
            "data": {"name": cat, "replace_with": replace, "affected": affected},
            "msg": msg,
        }
    except Exception as e:
        session.rollback()
        logger.exception("删除分类失败 name=%s", cat)
        return JSONResponse(status_code=500,
                            content={"status": "error", "msg": f"删除分类失败: {str(e)}"})
    finally:
        session.close()


@router.get("/api/dishes/{dish_id}")
def get_dish(dish_id: int, request: Request):
    """查询单个餐品详情（含配方食材列表与关联 SKU 实时库存）。"""
    require_role("staff")(request)
    session = db.get_session()
    try:
        dish = session.get(db._DishRow, int(dish_id))
        if not dish or not db._tenant_ok(dish, _tenant_id(request)):
            return JSONResponse(status_code=404, content={"status": "error", "msg": "餐品不存在"})
        return {"status": "success", "data": _dish_to_dict(session, dish, _tenant_id(request))}
    finally:
        session.close()


@router.post("/api/dishes")
def create_dish(body: DishCreate, request: Request):
    """新建餐品及其 BOM 配方。"""
    require_role("owner")(request)
    name = (body.name or "").strip()
    if not name:
        return {"status": "error", "msg": "餐品名称不能为空"}

    if body.price < 0:
        return {"status": "error", "msg": "餐品售价不能为负数"}

    session = db.get_session()
    try:
        tenant_id = _tenant_id(request)
        # 查重（按租户隔离：不同租户可各自建同名餐品）
        existing = db.scoped(
            session.query(db._DishRow).filter(db._DishRow.name == name),
            db._DishRow, tenant_id).first()
        if existing:
            return {"status": "error", "msg": f"餐品「{name}」已存在"}

        # 校验 ingredients（组件类型/用量/出成率/归属/单位/无环/深度）。
        # 新建时 dish_id=None：校验以「新餐品」为虚拟根（depth=1）对被引用子配方跑环/深度 DFS，
        # 与 update/restore 传 dish.id 的语义一致（越界/成环返回 400 RECIPE_GRAPH_INVALID）。
        err_body, err_status, normalized = _validate_recipe_payload(
            session, tenant_id, body.ingredients or [], None)
        if err_body:
            return JSONResponse(status_code=err_status, content=err_body)

        now_str = db.now_iso()
        dish = db._DishRow(
            name=name,
            category=(body.category or "").strip(),
            price=round(float(body.price), 2),
            description=(body.description or "").strip(),
            status=body.status or "active",
            version=1,
            created_at=now_str,
            updated_at=now_str,
            tenant_id=tenant_id,
        )
        session.add(dish)
        session.flush()

        for comp in normalized:
            ing_row = db._DishIngredientRow(
                dish_id=dish.id,
                sku_id=(comp["component_id"] if comp["component_type"] == "sku" else None),
                component_type=comp["component_type"],
                component_id=comp["component_id"],
                consumption_qty=comp["consumption_qty"],
                unit=comp["unit"],
                yield_rate=comp["yield_rate"],
                notes=comp["notes"],
            )
            session.add(ing_row)

        session.flush()
        # WS2：写入 v1 配方快照（版本可回溯）
        db.snapshot_dish_recipe(session, dish, changed_by=_actor(request),
                                summary="创建餐品（初始版本）")
        session.commit()
        session.refresh(dish)
        return {"status": "success", "data": _dish_to_dict(session, dish, _tenant_id(request))}
    except Exception as e:
        session.rollback()
        return {"status": "error", "msg": f"创建餐品失败: {str(e)}"}
    finally:
        session.close()


@router.put("/api/dishes/{dish_id}")
def update_dish(dish_id: int, body: DishUpdate, request: Request):
    """更新餐品基本信息及其 BOM 配方。"""
    require_role("owner")(request)
    session = db.get_session()
    try:
        dish = session.get(db._DishRow, int(dish_id))
        if not dish or not db._tenant_ok(dish, _tenant_id(request)):
            return JSONResponse(status_code=404, content={"status": "error", "msg": "餐品不存在"})

        tenant_id = _tenant_id(request)

        # WS2 乐观锁：expected_version 与当前 version 不一致即 409（防并发覆盖）
        if body.expected_version is not None \
                and int(body.expected_version) != int(dish.version or 1):
            return JSONResponse(
                status_code=409,
                content={
                    "status": "error",
                    "code": "VERSION_CONFLICT",
                    "msg": f"该餐品配方已被他人更新（当前版本 v{int(dish.version or 1)}，"
                           f"你提交的是 v{int(body.expected_version)}）。请刷新后重试。",
                    "current_version": int(dish.version or 1),
                }
            )

        if body.name is not None:
            new_name = body.name.strip()
            if not new_name:
                return {"status": "error", "msg": "餐品名称不能为空"}
            existing = db.scoped(
                session.query(db._DishRow).filter(
                    db._DishRow.name == new_name,
                    db._DishRow.id != dish.id),
                db._DishRow, _tenant_id(request)).first()
            if existing:
                return {"status": "error", "msg": f"餐品「{new_name}」已存在"}
            dish.name = new_name

        if body.category is not None:
            dish.category = body.category.strip()

        if body.price is not None:
            if body.price < 0:
                return {"status": "error", "msg": "餐品售价不能为负数"}
            dish.price = round(float(body.price), 2)

        if body.description is not None:
            dish.description = body.description.strip()

        if body.status is not None:
            dish.status = body.status

        # 更新配方（版本号自增 + 写快照，历史不可改写）
        if body.ingredients is not None:
            err_body, err_status, normalized = _validate_recipe_payload(
                session, tenant_id, body.ingredients, dish.id)
            if err_body:
                return JSONResponse(status_code=err_status, content=err_body)

            # 删除旧配方
            session.query(db._DishIngredientRow).filter(db._DishIngredientRow.dish_id == dish.id).delete()
            # 插入新配方
            for comp in normalized:
                ing_row = db._DishIngredientRow(
                    dish_id=dish.id,
                    sku_id=(comp["component_id"] if comp["component_type"] == "sku" else None),
                    component_type=comp["component_type"],
                    component_id=comp["component_id"],
                    consumption_qty=comp["consumption_qty"],
                    unit=comp["unit"],
                    yield_rate=comp["yield_rate"],
                    notes=comp["notes"],
                )
                session.add(ing_row)
            # 版本自增（BOM 版本化 + 乐观锁基线）
            dish.version = int(dish.version or 1) + 1

        dish.updated_at = db.now_iso()
        session.flush()
        if body.ingredients is not None:
            db.snapshot_dish_recipe(session, dish, changed_by=_actor(request),
                                    summary="更新餐品配方")
        session.commit()
        session.refresh(dish)
        return {"status": "success", "data": _dish_to_dict(session, dish, _tenant_id(request))}
    except Exception as e:
        session.rollback()
        return {"status": "error", "msg": f"更新餐品失败: {str(e)}"}
    finally:
        session.close()


@router.delete("/api/dishes/{dish_id}")
def delete_dish(dish_id: int, request: Request, hard: int = 0):
    """停用或彻底删除餐品。"""
    require_role("owner")(request)
    session = db.get_session()
    try:
        dish = session.get(db._DishRow, int(dish_id))
        if not dish or not db._tenant_ok(dish, _tenant_id(request)):
            return JSONResponse(status_code=404, content={"status": "error", "msg": "餐品不存在"})

        if hard == 1:
            session.query(db._DishIngredientRow).filter(db._DishIngredientRow.dish_id == dish.id).delete()
            session.delete(dish)
            session.commit()
            return {"status": "success", "action": "DELETED", "msg": "餐品已彻底删除"}
        else:
            dish.status = "inactive"
            dish.updated_at = db.now_iso()
            session.commit()
            return {"status": "success", "action": "DEACTIVATED", "msg": "餐品已停用"}
    finally:
        session.close()


# -------------------------------------------------------------
# WS2：BOM 版本历史 / 回滚
# -------------------------------------------------------------
@router.get("/api/dishes/{dish_id}/versions")
def list_dish_versions(dish_id: int, request: Request):
    """查询餐品 BOM 版本历史（元数据列表，按版本倒序）。"""
    require_role("staff")(request)
    tenant_id = _tenant_id(request)
    session = db.get_session()
    try:
        dish = session.get(db._DishRow, int(dish_id))
        if not dish or not db._tenant_ok(dish, tenant_id):
            return JSONResponse(status_code=404, content={"status": "error", "msg": "餐品不存在"})
        return {
            "status": "success",
            "data": {
                "dish_id": dish.id,
                "dish_name": dish.name,
                "current_version": int(dish.version or 1),
                "versions": db.list_dish_recipe_versions(dish.id, tenant_id),
            },
        }
    finally:
        session.close()


@router.get("/api/dishes/{dish_id}/versions/{version}")
def get_dish_version(dish_id: int, version: int, request: Request):
    """查询指定版本完整快照（菜品字段 + 配方）。"""
    require_role("staff")(request)
    tenant_id = _tenant_id(request)
    session = db.get_session()
    try:
        dish = session.get(db._DishRow, int(dish_id))
        if not dish or not db._tenant_ok(dish, tenant_id):
            return JSONResponse(status_code=404, content={"status": "error", "msg": "餐品不存在"})
        snapshot = db.get_dish_recipe_version(dish.id, int(version), tenant_id)
        if snapshot is None:
            return JSONResponse(status_code=404, content={"status": "error", "msg": f"版本 v{int(version)} 不存在"})
        return {"status": "success", "data": snapshot}
    finally:
        session.close()


@router.post("/api/dishes/{dish_id}/versions/{version}/restore")
def restore_dish_version(dish_id: int, version: int, request: Request,
                         body: Optional[DishRestoreBody] = None):
    """将历史快照恢复为**新版本**（不改写历史，版本号自增）。"""
    require_role("owner")(request)
    tenant_id = _tenant_id(request)
    session = db.get_session()
    try:
        dish = session.get(db._DishRow, int(dish_id))
        if not dish or not db._tenant_ok(dish, tenant_id):
            return JSONResponse(status_code=404, content={"status": "error", "msg": "餐品不存在"})
        snap = db.get_dish_recipe_version(dish.id, int(version), tenant_id)
        if snap is None:
            return JSONResponse(status_code=404, content={"status": "error", "msg": f"版本 v{int(version)} 不存在"})

        snapshot = snap.get("snapshot") or {}
        snap_dish = snapshot.get("dish") or {}
        comps = snapshot.get("ingredients") or []

        # 校验快照组件（子配方可能已被删除/改名 → 明确报错，不静默降级）
        err_body, err_status, normalized = _validate_recipe_payload(
            session, tenant_id, comps, dish.id)
        if err_body:
            return JSONResponse(status_code=err_status, content=err_body)

        # 回滚菜品字段（status 保持现状：不因回滚静默重新上架/停用）
        new_name = (snap_dish.get("name") or dish.name or "").strip()
        if new_name and new_name != dish.name:
            clash = db.scoped(
                session.query(db._DishRow).filter(
                    db._DishRow.name == new_name, db._DishRow.id != dish.id),
                db._DishRow, tenant_id).first()
            if clash:
                return JSONResponse(
                    status_code=409,
                    content={"status": "error", "code": "DISH_NAME_CONFLICT",
                             "msg": f"回滚目标名称「{new_name}」已被其他餐品占用"})
            dish.name = new_name
        if snap_dish.get("category") is not None:
            dish.category = (snap_dish.get("category") or "").strip()
        if snap_dish.get("price") is not None:
            dish.price = round(float(snap_dish.get("price") or 0.0), 2)
        if snap_dish.get("description") is not None:
            dish.description = (snap_dish.get("description") or "").strip()

        # 覆盖配方 + 版本自增 + 写新快照（历史不可改写）
        session.query(db._DishIngredientRow).filter(
            db._DishIngredientRow.dish_id == dish.id).delete()
        for comp in normalized:
            session.add(db._DishIngredientRow(
                dish_id=dish.id,
                sku_id=(comp["component_id"] if comp["component_type"] == "sku" else None),
                component_type=comp["component_type"],
                component_id=comp["component_id"],
                consumption_qty=comp["consumption_qty"],
                unit=comp["unit"],
                yield_rate=comp["yield_rate"],
                notes=comp["notes"],
            ))
        dish.version = int(dish.version or 1) + 1
        dish.updated_at = db.now_iso()
        summary = ((body.change_summary if body else "") or "").strip() \
            or f"回滚到 v{int(version)}"
        session.flush()
        db.snapshot_dish_recipe(session, dish, changed_by=_actor(request),
                                summary=summary)
        session.commit()
        session.refresh(dish)
        data = _dish_to_dict(session, dish, tenant_id)
        data["restored_from_version"] = int(version)
        return {"status": "success", "msg": f"已从 v{int(version)} 回滚并生成 v{dish.version}",
                "data": data}
    except Exception as e:
        session.rollback()
        return {"status": "error", "msg": f"回滚餐品配方失败: {str(e)}"}
    finally:
        session.close()


# -------------------------------------------------------------
# 每日消耗批量扣减与冲销
# -------------------------------------------------------------
@router.post("/api/dishes/daily_consumption/batch")
def submit_daily_consumption_batch(body: DailyConsumptionBatchCreate, request: Request):
    """批量提交当日餐品消耗（原子 FIFO 批次扣减、生成流水、扣减库存）。"""
    require_role("staff")(request)
    if not body.items:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "code": "EMPTY_CONSUMPTION_ITEMS",
                "msg": "请至少输入一种餐品的有效份数（份数大于 0）"
            }
        )

    date_str = (body.date or "").strip() or db.now_iso()[:10]
    tenant_id = _tenant_id(request)
    session = db.get_session()
    try:
        # 前置原子校验：所有餐品存在且份数必须大于 0
        for item in body.items:
            dish = session.get(db._DishRow, int(item.dish_id))
            if not dish or not db._tenant_ok(dish, tenant_id):
                return JSONResponse(
                    status_code=400,
                    content={
                        "status": "error",
                        "code": "DISH_NOT_FOUND",
                        "msg": f"找不到餐品 #{item.dish_id} 的信息，可能已被老板停用。请刷新页面重试。"
                    }
                )
            if item.quantity <= 0:
                return JSONResponse(
                    status_code=400,
                    content={
                        "status": "error",
                        "code": "INVALID_CONSUMPTION_QUANTITY",
                        "msg": f"餐品「{dish.name}」输入的售出份数必须大于 0，不能填负数或 0",
                        "detail": {
                            "dish_id": item.dish_id,
                            "rejected_value": item.quantity,
                            "user_action_guide": "请修改为实际售出的正数（如 1 或 0.5）后再点击提交"
                        }
                    }
                )

        # WS5：批内同一餐品先合并累加份数，避免同请求重复行
        merged_items: "OrderedDict[int, Dict]" = OrderedDict()
        for item in body.items:
            key = int(item.dish_id)
            if key in merged_items:
                merged_items[key]["quantity"] = round(
                    merged_items[key]["quantity"] + float(item.quantity), 4)
                if (item.notes or "").strip():
                    merged_items[key]["notes"] = (item.notes or "").strip()
            else:
                merged_items[key] = {
                    "dish_id": key,
                    "quantity": float(item.quantity),
                    "notes": (item.notes or "").strip(),
                }

        created_records = []
        result_items = []
        any_merged = False
        total_quantity = 0.0

        for key, mitem in merged_items.items():
            dish = session.get(db._DishRow, int(key))
            quantity = round(float(mitem["quantity"]), 4)

            # WS5：同 (日期, 餐品) 已有活跃记录则合并累加，不新建
            existing = (
                session.query(db._DailyConsumptionRow)
                .filter(
                    db._DailyConsumptionRow.date == date_str,
                    db._DailyConsumptionRow.dish_id == dish.id,
                    db._DailyConsumptionRow.is_void == 0,
                )
                .order_by(db._DailyConsumptionRow.id.asc())
                .first()
            )
            is_merge = existing is not None
            log_note = (f"餐品消耗(合并): {dish.name} x {quantity}份"
                        if is_merge else f"餐品消耗: {dish.name} x {quantity}份")

            # WS6：多级 BOM 展开为 {sku_id: {qty, unit}}，逐层应用 yield_rate；
            # 每个 SKU 聚合后只跑一次 FIFO（避免子配方重复扣减）。
            expanded = expand_recipe(session, dish.id, quantity, tenant_id)

            dish_total_cost = 0.0
            dish_details = []

            for sku_id, info in expanded.items():
                qty_needed = round(float(info.get("qty") or 0.0), 4)
                unit_needed = info.get("unit") or ""
                if qty_needed <= 0:
                    continue
                sku = db.scoped(
                    session.query(db._SkuRow).filter(db._SkuRow.id == int(sku_id)),
                    db._SkuRow,
                    tenant_id
                ).first()
                if not sku:
                    raise ValueError(
                        f"餐品「{dish.name}」配方展开出的食材 SKU #{sku_id} 不存在或无权使用")

                cost, details_list = CostingService.deduct_consumption_fifo(
                    session=session,
                    sku_id=int(sku_id),
                    qty_needed=qty_needed,
                    unit_needed=unit_needed,
                    date=date_str,
                )
                dish_total_cost += cost
                dish_details.extend(details_list)

                # 扣减 SKU 当前库存
                qty_in_sku_unit = convert_unit_quantity(qty_needed, unit_needed, sku.base_unit)
                sku.current_stock = round(sku.current_stock - qty_in_sku_unit, 4)

                # 写入 inventory_log
                stock_log = db._StockLogRow(
                    sku_id=sku.id,
                    name=sku.name,
                    qty=qty_in_sku_unit,
                    unit=sku.base_unit,
                    amount=round(cost, 2),
                    vendor="",
                    date=date_str,
                    receipt_id=None,
                    kind="consume",
                    note=log_note,
                    created_at=db.now_iso(),
                    tenant_id=tenant_id,
                )
                session.add(stock_log)

            dish_total_cost = round(dish_total_cost, 2)

            if is_merge:
                # 增量份数已按 FIFO 扣减，台账累加并重算均价；明细追挂到原记录
                existing.quantity = round(float(existing.quantity or 0.0) + quantity, 4)
                existing.total_cost = round(float(existing.total_cost or 0.0) + dish_total_cost, 2)
                existing.unit_cost = (
                    round(existing.total_cost / existing.quantity, 4)
                    if existing.quantity > 0 else 0.0)
                if mitem["notes"]:
                    existing.notes = mitem["notes"]
                cons_row = existing
                any_merged = True
            else:
                unit_cost = round(dish_total_cost / quantity, 4) if quantity > 0 else 0.0
                cons_row = db._DailyConsumptionRow(
                    date=date_str,
                    dish_id=dish.id,
                    quantity=quantity,
                    total_cost=dish_total_cost,
                    unit_cost=unit_cost,
                    notes=(mitem["notes"] or body.notes or "").strip(),
                    is_void=0,
                    created_at=db.now_iso(),
                    tenant_id=tenant_id,
                )
                session.add(cons_row)
                session.flush()

            for dt in dish_details:
                dt_row = db._DailyConsumptionDetailRow(
                    consumption_id=cons_row.id,
                    sku_id=dt["sku_id"],
                    qty_consumed=dt["qty_consumed"],
                    unit=dt["unit"],
                    unit_cost=dt["unit_cost"],
                    total_cost=dt["total_cost"],
                    batch_id=dt["batch_id"],
                    batch_date=dt["batch_date"],
                )
                session.add(dt_row)

            created_records.append(cons_row.id)
            total_quantity = round(total_quantity + float(cons_row.quantity or 0.0), 4)
            result_items.append({
                "dish_id": dish.id,
                "consumption_id": cons_row.id,
                "quantity": round(float(cons_row.quantity or 0.0), 4),
                "total_cost": round(float(cons_row.total_cost or 0.0), 2),
                "merged": is_merge,
            })

        session.commit()
        return {
            "status": "success",
            "msg": f"成功记录 {len(created_records)} 项餐品消耗",
            "data": {
                "date": date_str,
                "consumption_ids": created_records,
                "merged": any_merged,
                "quantity": total_quantity,
                "items": result_items,
            },
        }
    except ValueError as e:
        session.rollback()
        err_msg = str(e)
        if "跨量纲单位不可换算" in err_msg:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "code": "UNIT_CONVERSION_ERROR",
                    "msg": f"食材单位对不上（比如不能拿“份”换“斤”）。请统一改用重量或联系老板检查。详细：{err_msg}"
                }
            )
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "code": "CONSUMPTION_FAILED",
                "msg": err_msg
            }
        )
    except Exception as e:
        session.rollback()
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "code": "SERVER_ERROR",
                "msg": f"提交消耗失败: {str(e)}"
            }
        )
    finally:
        session.close()


@router.post("/api/dishes/daily_consumption/{consumption_id}/void")
def void_daily_consumption(consumption_id: int, request: Request):
    """冲销/作废指定消耗记录，自动回滚批次剩余量与 SKU 库存。"""
    require_role("owner")(request)
    tenant_id = _tenant_id(request)
    session = db.get_session()
    try:
        cons = session.get(db._DailyConsumptionRow, int(consumption_id))
        if not cons or not _consumption_tenant_ok(session, cons, tenant_id):
            return JSONResponse(status_code=404, content={"status": "error", "msg": "消耗记录不存在"})

        if cons.is_void == 1:
            return {"status": "error", "msg": "该消耗记录此前已冲销作废，请勿重复操作"}

        details = (
            session.query(db._DailyConsumptionDetailRow)
            .filter(db._DailyConsumptionDetailRow.consumption_id == cons.id)
            .all()
        )

        # 调用 CostingService 回滚批次与 SKU 库存
        ok = CostingService.void_consumption_fifo(session, cons.id)
        if not ok:
            return {"status": "error", "msg": "冲销回滚失败"}

        # 写入库存调整日志
        dish = session.get(db._DishRow, cons.dish_id)
        dish_name = dish.name if dish else f"餐品#{cons.dish_id}"

        for dt in details:
            sku = session.get(db._SkuRow, dt.sku_id)
            if sku:
                qty_in_sku_unit = convert_unit_quantity(dt.qty_consumed, dt.unit, sku.base_unit)
                stock_log = db._StockLogRow(
                    sku_id=sku.id,
                    name=sku.name,
                    qty=qty_in_sku_unit,
                    unit=sku.base_unit,
                    amount=round(dt.total_cost, 2),
                    vendor="",
                    date=db.now_iso()[:10],
                    receipt_id=None,
                    kind="adjust",
                    note=f"冲销作废餐品消耗 #{cons.id} ({dish_name}) 恢复库存",
                    created_at=db.now_iso(),
                    tenant_id=getattr(cons, "tenant_id", None) or _tenant_id(request),
                )
                session.add(stock_log)

        session.commit()
        return {"status": "success", "msg": "已成功冲销作废该消耗记录并回滚批次与库存"}
    except Exception as e:
        session.rollback()
        return {"status": "error", "msg": f"冲销失败: {str(e)}"}
    finally:
        session.close()
