# -*- coding: utf-8 -*-
"""数据导出中心后端端点：提供覆盖库存、餐品、供应商、部门四大业务模块的统一报表数据生成、在线预览与 CSV 下载。"""

import csv
import io
import re
import urllib.parse
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app import db
from app.api_receipts import _mask_sensitive
from app.auth import require_role, resolve_account
from app.services.costing_service import CostingService

router = APIRouter()


def _tenant_id(request: Request) -> str:
    return (
        request.headers.get("X-Tenant-Id")
        or request.headers.get("x-tenant-id")
        or "default"
    ).strip() or "default"


def _sanitize_csv_cell(val: Any) -> Any:
    """EC-3 / AC-3: 防 CSV 公式注入。首字符为 =, +, -, @, \\t, \\r 时前置单引号转义。"""
    if val is None:
        return ""
    if isinstance(val, (int, float)):
        return val
    s = str(val)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{s}"
    return s


def mask_vendor_name(name: str) -> str:
    """AC-8: 供应商名称掩码脱敏（如“德利行 Tak Lee Hong”->“德***行”）。"""
    if not name:
        return ""
    chinese_chars = re.findall(r"[\u4e00-\u9fff]", name)
    if len(chinese_chars) >= 2:
        return f"{chinese_chars[0]}***{chinese_chars[-1]}"
    elif len(chinese_chars) == 1:
        return f"{chinese_chars[0]}***"
    else:
        s = name.strip()
        if len(s) <= 2:
            return s[:1] + "***"
        return f"{s[0]}***{s[-1]}"


def mask_phone(phone: str) -> str:
    """AC-8: 电话号码脱敏（香港 8 位号码如 91234567 -> 91****67；内地 11 位号码如 13812345678 -> 138****5678）。"""
    if not phone:
        return ""
    # 8 位香港号码
    hk_match = re.search(r"(\b[2-9]\d{7}\b)", phone)
    if hk_match:
        m = hk_match.group(1)
        masked = f"{m[:2]}****{m[-2:]}"
        return phone.replace(m, masked)
    # 11 位内地号码
    ml_match = re.search(r"(\b1[3-9]\d{9}\b)", phone)
    if ml_match:
        m = ml_match.group(1)
        masked = f"{m[:3]}****{m[-4:]}"
        return phone.replace(m, masked)
    digits = re.findall(r"\d", phone)
    if len(digits) >= 6:
        return phone[:2] + "****" + phone[-2:]
    return phone


def _normalize_date_range(params: Dict[str, Any]) -> None:
    """AC-5: 起始日期晚于结束日期时自动对调理顺。"""
    start = params.get("start_date")
    end = params.get("end_date")
    if start and end and str(start) > str(end):
        params["start_date"], params["end_date"] = end, start


# --------------------------------------------------------------------------
# 报表元数据定义字典
# --------------------------------------------------------------------------
REPORTS_META = [
    # 模块一：库存与价格
    {
        "id": "inventory_stocktake",
        "category": "inventory",
        "category_name": "实时库存与价格",
        "title": "实时库存盘点底单",
        "description": "专为仓库/冷库现场实地盘点设计，包含账面数量及留白手填实盘列、盘盈盘亏列与签字栏。",
        "filters": ["category", "stock_filter"],
        "supports_desensitization": False,
        "supports_drafts": False,
    },
    {
        "id": "inventory_valuation",
        "category": "inventory",
        "category_name": "实时库存与价格",
        "title": "食材库存结存与货值统计表",
        "description": "核算全量食材的当前库存、折算标准kg、最新进价及资产货值估算，监控低库存风险。",
        "filters": ["category", "stock_filter"],
        "supports_desensitization": False,
        "supports_drafts": False,
    },
    {
        "id": "inventory_price_anomaly",
        "category": "inventory",
        "category_name": "实时库存与价格",
        "title": "采购进价波动与比价分析表",
        "description": "比对最新进价与30日均价涨跌幅、历史极值，标红涨幅超10%异动食材，辅助议价与选品。",
        "filters": ["category"],
        "supports_desensitization": False,
        "supports_drafts": False,
    },
    {
        "id": "inventory_stock_logs",
        "category": "inventory",
        "category_name": "实时库存与价格",
        "title": "出入库全量流水综合台账",
        "description": "整合采购入库、餐品消耗、后厨报损、盘点调整的完整物理库存变动日记账。",
        "filters": ["date_range", "log_kind"],
        "supports_desensitization": True,
        "supports_drafts": False,
    },
    # 模块二：餐品与消耗
    {
        "id": "dish_bom_recipes",
        "category": "dishes",
        "category_name": "餐品与消耗",
        "title": "餐品 BOM 标准配方明细表",
        "description": "厨房出品标准化档案，列出各餐品原料配比、单位用量、理论食材基准成本及毛利率。",
        "filters": ["dish_status", "dish_category"],
        "supports_desensitization": False,
        "supports_drafts": False,
    },
    {
        "id": "dish_daily_sales_cost",
        "category": "dishes",
        "category_name": "餐品与消耗",
        "title": "每日销售与食材实际成本汇总表",
        "description": "按日统计餐品售出份数、营收、FIFO先进先出扣减的真实食材成本及实际综合毛利率。",
        "filters": ["date_range"],
        "supports_desensitization": False,
        "supports_drafts": False,
    },
    {
        "id": "dish_margin_variance",
        "category": "dishes",
        "category_name": "餐品与消耗",
        "title": "餐品理论毛利 vs 真实毛利偏差诊断表",
        "description": "对比理论成本与实际采购加权成本，量化食材涨价对餐品利润的穿透侵蚀度与诊断建议。",
        "filters": ["date_range"],
        "supports_desensitization": False,
        "supports_drafts": False,
    },
    {
        "id": "dish_ingredient_consumption",
        "category": "dishes",
        "category_name": "餐品与消耗",
        "title": "后厨食材累计消耗总量与采购建议",
        "description": "汇总核算周期内各原材料的总消耗量、当前库存剩余，自动测算建议补货量。",
        "filters": ["date_range", "category"],
        "supports_desensitization": False,
        "supports_drafts": False,
    },
    # 模块三：供应商与归档
    {
        "id": "receipts_itemized_ledger",
        "category": "suppliers",
        "category_name": "供应商与归档",
        "title": "采购收据入账全量明细台账 (含行项目)",
        "description": "财务标准做账台账：单据抬头与行项目全景展开，包含供应商、日期、商品品名、数量、单价与部门。",
        "filters": ["date_range", "receipt_status", "supplier_id", "department_id"],
        "supports_desensitization": True,
        "supports_drafts": True,
    },
    {
        "id": "supplier_payables_aging",
        "category": "suppliers",
        "category_name": "供应商与归档",
        "title": "供应商应付账款与账期逾期表",
        "description": "出纳排款与账期控制专用表：汇总各供应商未付赊单、逾期金额、7天内到期金额及账期天数。",
        "filters": ["supplier_id"],
        "supports_desensitization": True,
        "supports_drafts": False,
    },
    {
        "id": "supplier_payments_journal",
        "category": "suppliers",
        "category_name": "供应商与归档",
        "title": "供应商往来付款与核销台账",
        "description": "记录所有向供应商支付的结算流水、支付方式、关联收据单号及凭证留痕。",
        "filters": ["date_range", "supplier_id"],
        "supports_desensitization": True,
        "supports_drafts": False,
    },
    {
        "id": "supplier_recon_discrepancies",
        "category": "suppliers",
        "category_name": "供应商与归档",
        "title": "供应商月结对账差异明细表",
        "description": "月结核账报表：展现系统收据与供应商外部对账单的匹配情况，标记漏单、金额不符等差异行。",
        "filters": ["supplier_id"],
        "supports_desensitization": True,
        "supports_drafts": False,
    },
    # 模块四：部门花销报表
    {
        "id": "department_cost_summary",
        "category": "department",
        "category_name": "部门花销报表",
        "title": "部门采购花销期间环比对照表",
        "description": "管理层汇报报表：横向对比各部门本期进货与上期进货金额，展示环比增减额、增减率及趋势。",
        "filters": ["date_range"],
        "supports_desensitization": False,
        "supports_drafts": True,
    },
    {
        "id": "department_items_breakdown",
        "category": "department",
        "category_name": "部门花销报表",
        "title": "部门采购货品穿透明细流水",
        "description": "穿透至各部门采购的具体货品名称、采购量、金额与供应商，便于部门主管复核成本明细。",
        "filters": ["date_range", "department_id"],
        "supports_desensitization": True,
        "supports_drafts": True,
    },
]


@router.get("/api/export/reports")
def get_available_reports(request: Request):
    """获取所有可用导出的报表定义元数据。"""
    require_role("staff")(request)
    return {"status": "success", "data": REPORTS_META}


# --------------------------------------------------------------------------
# 数据抓取与组装处理器
# --------------------------------------------------------------------------
class ExportDataProcessor:
    @staticmethod
    def _in_date(d_str: str, start: str, end: str) -> bool:
        if not d_str:
            return False
        d = d_str[:10]
        if start and d < start:
            return False
        if end and d > end:
            return False
        return True

    @classmethod
    def get_data(
        cls,
        report_id: str,
        params: Dict[str, Any],
        tenant_id: str,
        is_desens: bool = False,
    ) -> Dict[str, Any]:
        params = dict(params or {})
        _normalize_date_range(params)
        meta = next((r for r in REPORTS_META if r["id"] == report_id), None)
        if meta:
            if not meta.get("supports_desensitization", False):
                is_desens = False
            if not meta.get("supports_drafts", False):
                params.pop("include_non_approved", None)
        handler = getattr(cls, f"_handle_{report_id}", None)
        if not handler:
            raise HTTPException(status_code=400, detail=f"不支持的报表类型: {report_id}")
        return handler(params, tenant_id, is_desens)

    # 1. 实时库存盘点底单
    @staticmethod
    def _handle_inventory_stocktake(params, tenant_id, is_desens):
        skus = db.list_skus(include_inactive=False, tenant_id=tenant_id)
        cat = params.get("category", "")
        stock_flt = params.get("stock_filter", "all")

        headers = [
            "食材代码",
            "食材品名",
            "分类",
            "基本单位",
            "系统账面库存",
            "安全警戒线",
            "实盘数量(现场手填)",
            "盘盈/盘亏差异",
            "盘点人员签字",
            "备注",
        ]
        rows = []
        for s in skus:
            if cat and s.category != cat:
                continue
            is_low = s.min_stock_alert > 0 and s.current_stock <= s.min_stock_alert
            if stock_flt == "low" and not is_low:
                continue

            rows.append(
                [
                    s.sku_code or f"SKU-{s.id}",
                    s.name,
                    s.category or "其他",
                    s.base_unit or "个",
                    round(s.current_stock, 2),
                    round(s.min_stock_alert, 2),
                    "",  # 实盘数量手填留白
                    "",  # 差异手填留白
                    "",  # 签字
                    "低库存预警" if is_low else "",
                ]
            )
        return {
            "title": "实时库存盘点底单",
            "filename": f"实时库存盘点底单_{datetime.now().strftime('%Y%m%d')}",
            "headers": headers,
            "rows": rows,
            "summary": {
                "总品类数": len(rows),
                "盘点日期": datetime.now().strftime("%Y-%m-%d"),
            },
        }

    # 2. 食材库存结存与货值统计表
    @staticmethod
    def _handle_inventory_valuation(params, tenant_id, is_desens):
        from app.api_inventory import _standard_kg

        skus = db.list_skus(include_inactive=False, tenant_id=tenant_id)
        cat = params.get("category", "")
        stock_flt = params.get("stock_filter", "all")

        headers = [
            "食材代码",
            "食材品名",
            "分类",
            "基本单位",
            "当前库存",
            "折算标准kg",
            "最新进货单价(元)",
            "库存货值估算(元)",
            "安全警戒线",
            "库存状态",
        ]
        rows = []
        total_val = 0.0
        total_kg = 0.0

        for s in skus:
            if cat and s.category != cat:
                continue
            is_low = s.min_stock_alert > 0 and s.current_stock <= s.min_stock_alert
            if stock_flt == "low" and not is_low:
                continue

            kg_val = _standard_kg(s.current_stock, s.base_unit)
            if kg_val:
                total_kg += kg_val
            unit_p = s.last_unit_price or 0.0
            val = round(s.current_stock * unit_p, 2)
            total_val += val

            rows.append(
                [
                    s.sku_code or f"SKU-{s.id}",
                    s.name,
                    s.category or "其他",
                    s.base_unit or "个",
                    round(s.current_stock, 2),
                    kg_val if kg_val is not None else "-",
                    round(unit_p, 2),
                    val,
                    round(s.min_stock_alert, 2),
                    "[低库存]" if is_low else "正常",
                ]
            )
        return {
            "title": "食材库存结存与货值统计表",
            "filename": f"食材库存结存与货值表_{datetime.now().strftime('%Y%m%d')}",
            "headers": headers,
            "rows": rows,
            "summary": {
                "统计SKU种数": len(rows),
                "估算总货值(元)": round(total_val, 2),
                "标准重量合计(kg)": round(total_kg, 2),
            },
        }

    # 3. 采购进价波动与比价分析表
    @staticmethod
    def _handle_inventory_price_anomaly(params, tenant_id, is_desens):
        from app.api_inventory import _compute_vs_avg_and_anomaly

        skus = db.list_skus(include_inactive=False, tenant_id=tenant_id)
        cat = params.get("category", "")

        headers = [
            "食材品名",
            "食材代码",
            "分类",
            "基本单位",
            "最新进价(元)",
            "近30天均价(元)",
            "波动幅度",
            "价格异动预警",
            "历史最低价(元)",
            "历史最高价(元)",
        ]
        rows = []
        anomaly_count = 0
        for s in skus:
            if cat and s.category != cat:
                continue
            vs_pct, is_anomaly, avg_30d = _compute_vs_avg_and_anomaly(s.id)
            if is_anomaly:
                anomaly_count += 1

            ph = db.price_history(s.id, tenant_id=tenant_id)
            prices = [r.unit_price for r in ph if r.unit_price > 0]
            min_p = min(prices) if prices else s.last_unit_price or 0.0
            max_p = max(prices) if prices else s.last_unit_price or 0.0

            vs_text = f"+{vs_pct:.1f}%" if vs_pct > 0 else f"{vs_pct:.1f}%"
            rows.append(
                [
                    s.name,
                    s.sku_code or f"SKU-{s.id}",
                    s.category or "其他",
                    s.base_unit or "个",
                    round(s.last_unit_price or 0.0, 2),
                    round(avg_30d, 2),
                    vs_text,
                    "[涨价异动]" if is_anomaly else "正常",
                    round(min_p, 2),
                    round(max_p, 2),
                ]
            )
        return {
            "title": "采购进价波动与比价分析表",
            "filename": f"采购进价波动与比价表_{datetime.now().strftime('%Y%m%d')}",
            "headers": headers,
            "rows": rows,
            "summary": {"分析SKU总数": len(rows), "涨价预警SKU数": anomaly_count},
        }

    # 4. 出入库全量流水综合台账
    @staticmethod
    def _handle_inventory_stock_logs(params, tenant_id, is_desens):
        start = params.get("start_date", "")
        end = params.get("end_date", "")
        kind = params.get("log_kind", "")

        session = db.get_session()
        try:
            q = session.query(db._StockLogRow)
            q = db.scoped(q, db._StockLogRow, tenant_id)
            if kind:
                q = q.filter(db._StockLogRow.kind == kind)
            q = q.order_by(db._StockLogRow.id.desc())
            db_rows = q.all()

            kind_map = {
                "in": "采购入库",
                "consume": "餐品消耗",
                "waste": "后厨报损",
                "stocktake": "盘点调整",
            }
            headers = [
                "流水编号",
                "业务日期",
                "业务类型",
                "食材品名",
                "变动数量",
                "单位",
                "变动金额(元)",
                "往来供应商/经手",
                "关联单据号",
                "操作备注",
            ]
            rows = []
            total_amt = 0.0

            for r in db_rows:
                d = r.date or (r.created_at[:10] if r.created_at else "")
                if start and d < start:
                    continue
                if end and d > end:
                    continue

                amt = float(r.amount or 0.0)
                total_amt += amt
                v = r.vendor or ""
                if is_desens and v:
                    v = mask_vendor_name(v)

                rows.append(
                    [
                        f"LOG-{r.id}",
                        d,
                        kind_map.get(r.kind, r.kind),
                        r.name,
                        round(float(r.qty or 0.0), 2),
                        r.unit or "个",
                        round(amt, 2),
                        v,
                        f"#{r.receipt_id}" if r.receipt_id else "-",
                        r.note or "",
                    ]
                )
            return {
                "title": "出入库全量流水综合台账",
                "filename": f"出入库全量流水台账_{start or 'all'}_{end or 'now'}",
                "headers": headers,
                "rows": rows,
                "summary": {"流水总笔数": len(rows), "总金额合计(元)": round(total_amt, 2)},
            }
        finally:
            session.close()

    # 5. 餐品 BOM 标准配方明细表
    @staticmethod
    def _handle_dish_bom_recipes(params, tenant_id, is_desens):
        status_flt = params.get("dish_status", "active")
        cat_flt = params.get("dish_category", "")

        session = db.get_session()
        try:
            q = db.scoped(session.query(db._DishRow), db._DishRow, tenant_id)
            if status_flt != "all":
                q = q.filter(db._DishRow.status == status_flt)
            if cat_flt:
                q = q.filter(db._DishRow.category == cat_flt)
            dishes = q.all()

            headers = [
                "餐品名称",
                "餐品分类",
                "在售标价(元)",
                "配方食材",
                "单份用量",
                "用量单位",
                "食材进价(元)",
                "单项成本(元)",
                "理论整单成本(元)",
                "理论毛利率(%)",
                "状态",
            ]
            rows = []

            for d in dishes:
                ing_rows = (
                    session.query(db._DishIngredientRow)
                    .filter(db._DishIngredientRow.dish_id == d.id)
                    .all()
                )

                theoretical_cost = 0.0
                ing_details = []
                for ing in ing_rows:
                    sku = session.get(db._SkuRow, ing.sku_id)
                    sku_name = sku.name if sku else "未知食材"
                    unit_p = sku.last_unit_price if sku else 0.0
                    ing_cost = round(ing.consumption_qty * (unit_p or 0.0), 2)
                    theoretical_cost += ing_cost
                    ing_details.append(
                        (sku_name, ing.consumption_qty, ing.unit, unit_p, ing_cost)
                    )

                margin_pct = (
                    round((d.price - theoretical_cost) / d.price * 100, 1)
                    if d.price > 0
                    else 0.0
                )

                if not ing_details:
                    rows.append(
                        [
                            d.name,
                            d.category or "其他",
                            round(d.price, 2),
                            "-",
                            "-",
                            "-",
                            "-",
                            "-",
                            0.0,
                            f"{margin_pct}%",
                            "在售" if d.status == "active" else "停用",
                        ]
                    )
                else:
                    for i, (s_name, q_val, u, up, c) in enumerate(ing_details):
                        rows.append(
                            [
                                d.name if i == 0 else "",
                                d.category or "其他" if i == 0 else "",
                                round(d.price, 2) if i == 0 else "",
                                s_name,
                                round(q_val, 3),
                                u,
                                round(up, 2),
                                round(c, 2),
                                round(theoretical_cost, 2) if i == 0 else "",
                                f"{margin_pct}%" if i == 0 else "",
                                ("在售" if d.status == "active" else "停用")
                                if i == 0
                                else "",
                            ]
                        )

            return {
                "title": "餐品 BOM 标准配方明细表",
                "filename": f"餐品BOM标准配方表_{datetime.now().strftime('%Y%m%d')}",
                "headers": headers,
                "rows": rows,
                "summary": {"餐品种数": len(dishes)},
            }
        finally:
            session.close()

    # 6. 每日销售与食材实际成本汇总表
    @staticmethod
    def _handle_dish_daily_sales_cost(params, tenant_id, is_desens):
        start = params.get("start_date", "")
        end = params.get("end_date", "")

        session = db.get_session()
        try:
            tenant_dish_ids = {
                d.id
                for d in db.scoped(
                    session.query(db._DishRow), db._DishRow, tenant_id
                ).all()
            }
            q = session.query(db._DailyConsumptionRow).filter(
                db._DailyConsumptionRow.is_void == 0
            )
            if tenant_dish_ids:
                q = q.filter(db._DailyConsumptionRow.dish_id.in_(tenant_dish_ids))
            elif tenant_id:
                q = q.filter(db._DailyConsumptionRow.id == -1)
            if start:
                q = q.filter(db._DailyConsumptionRow.date >= start)
            if end:
                q = q.filter(db._DailyConsumptionRow.date <= end)
            q = q.order_by(
                db._DailyConsumptionRow.date.desc(), db._DailyConsumptionRow.id.desc()
            )
            consumes = q.all()

            headers = [
                "核算日期",
                "餐品名称",
                "售出份数",
                "单份售价(元)",
                "营业总额(元)",
                "FIFO实际食材成本(元)",
                "实际毛利额(元)",
                "实际毛利率(%)",
                "理论基准成本(元)",
                "成本偏差(元)",
                "状态",
            ]
            rows = []
            tot_sales = 0.0
            tot_cost = 0.0

            for c in consumes:
                dish = session.get(db._DishRow, c.dish_id)
                dish_name = dish.name if dish else f"餐品#{c.dish_id}"
                dish_p = dish.price if dish else 0.0

                sales = round(float(c.quantity * dish_p), 2)
                act_cost = round(float(c.total_cost or 0.0), 2)
                margin = round(sales - act_cost, 2)
                margin_pct = round(margin / sales * 100, 1) if sales > 0 else 0.0

                # 算单品理论成本
                ing_rows = (
                    session.query(db._DishIngredientRow)
                    .filter(db._DishIngredientRow.dish_id == c.dish_id)
                    .all()
                )
                theo_unit_c = 0.0
                for ing in ing_rows:
                    sku = session.get(db._SkuRow, ing.sku_id)
                    up = sku.last_unit_price if sku else 0.0
                    theo_unit_c += ing.consumption_qty * (up or 0.0)

                theo_cost = round(theo_unit_c * c.quantity, 2)
                diff = round(act_cost - theo_cost, 2)

                tot_sales += sales
                tot_cost += act_cost

                rows.append(
                    [
                        c.date,
                        dish_name,
                        round(c.quantity, 1),
                        round(dish_p, 2),
                        round(sales, 2),
                        round(act_cost, 2),
                        round(margin, 2),
                        f"{margin_pct:.1f}%",
                        round(theo_cost, 2),
                        diff,
                        "已核销" if c.is_void == 0 else "已冲销作废",
                    ]
                )

            return {
                "title": "每日销售与食材实际成本汇总表",
                "filename": f"每日餐品销售与食材成本_{start or 'all'}_{end or 'now'}",
                "headers": headers,
                "rows": rows,
                "summary": {
                    "总销售额(元)": round(tot_sales, 2),
                    "实际食材总成本(元)": round(tot_cost, 2),
                    "综合毛利率": (
                        f"{((tot_sales - tot_cost) / tot_sales * 100):.1f}%"
                        if tot_sales > 0
                        else "0.0%"
                    ),
                },
            }
        finally:
            session.close()

    # 7. 餐品理论毛利 vs 真实毛利偏差诊断表
    @staticmethod
    def _handle_dish_margin_variance(params, tenant_id, is_desens):
        start = params.get("start_date", "")
        end = params.get("end_date", "")

        session = db.get_session()
        try:
            q = db.scoped(session.query(db._DishRow), db._DishRow, tenant_id)
            dishes = q.filter(db._DishRow.status == "active").all()

            headers = [
                "餐品名称",
                "分类",
                "在售标价(元)",
                "理论成本(元)",
                "理论毛利率(%)",
                "加权实际成本(元)",
                "实际综合毛利率(%)",
                "成本溢价额(元)",
                "毛利侵蚀点(%)",
                "诊断分析建议",
            ]
            rows = []

            for d in dishes:
                ing_rows = (
                    session.query(db._DishIngredientRow)
                    .filter(db._DishIngredientRow.dish_id == d.id)
                    .all()
                )
                theo_cost = 0.0
                for ing in ing_rows:
                    sku = session.get(db._SkuRow, ing.sku_id)
                    up = sku.last_unit_price if sku else 0.0
                    theo_cost += ing.consumption_qty * (up or 0.0)

                # 统计周期内实际消耗 (d 已经由 tenant_id 隔离)
                cq = session.query(db._DailyConsumptionRow).filter(
                    db._DailyConsumptionRow.dish_id == d.id,
                    db._DailyConsumptionRow.is_void == 0,
                )
                if start:
                    cq = cq.filter(db._DailyConsumptionRow.date >= start)
                if end:
                    cq = cq.filter(db._DailyConsumptionRow.date <= end)
                consumes = cq.all()

                total_qty = sum(c.quantity for c in consumes)
                total_actual_cost = sum(c.total_cost for c in consumes)
                real_unit_cost = (
                    round(total_actual_cost / total_qty, 2)
                    if total_qty > 0
                    else theo_cost
                )

                theo_margin = (
                    round((d.price - theo_cost) / d.price * 100, 1)
                    if d.price > 0
                    else 0.0
                )
                real_margin = (
                    round((d.price - real_unit_cost) / d.price * 100, 1)
                    if d.price > 0
                    else 0.0
                )

                cost_diff = round(real_unit_cost - theo_cost, 2)
                erosion = round(theo_margin - real_margin, 1)

                advice = "毛利健康稳定"
                if erosion > 5.0:
                    advice = "[预警] 成本大幅承压，建议排查高价食材或微调售价"
                elif erosion > 2.0:
                    advice = "进货价微涨，注意控制损耗"
                elif total_qty == 0:
                    advice = "统计周期内暂无消耗记录"

                rows.append(
                    [
                        d.name,
                        d.category or "其他",
                        round(d.price, 2),
                        round(theo_cost, 2),
                        f"{theo_margin:.1f}%",
                        round(real_unit_cost, 2),
                        f"{real_margin:.1f}%",
                        f"+{cost_diff:.2f}" if cost_diff > 0 else f"{cost_diff:.2f}",
                        f"{erosion:.1f}%" if erosion > 0 else "0.0%",
                        advice,
                    ]
                )

            return {
                "title": "餐品理论毛利 vs 真实毛利偏差诊断表",
                "filename": f"餐品毛利与成本偏差诊断表_{datetime.now().strftime('%Y%m%d')}",
                "headers": headers,
                "rows": rows,
                "summary": {"分析在售餐品数": len(dishes)},
            }
        finally:
            session.close()

    # 8. 后厨食材累计消耗总量与采购建议
    @staticmethod
    def _handle_dish_ingredient_consumption(params, tenant_id, is_desens):
        start = params.get("start_date", "")
        end = params.get("end_date", "")

        session = db.get_session()
        try:
            # 统计 StockLogRow 中 kind in ('consume', 'waste')
            q = session.query(db._StockLogRow).filter(
                db._StockLogRow.kind.in_(["consume", "waste"])
            )
            q = db.scoped(q, db._StockLogRow, tenant_id)
            if start:
                q = q.filter(db._StockLogRow.date >= start)
            if end:
                q = q.filter(db._StockLogRow.date <= end)
            logs = q.all()

            sku_stats = {}
            for l in logs:
                sid = l.sku_id
                if not sid:
                    continue
                if sid not in sku_stats:
                    sku_stats[sid] = {
                        "name": l.name,
                        "unit": l.unit,
                        "qty": 0.0,
                        "cost": 0.0,
                    }
                sku_stats[sid]["qty"] += float(l.qty or 0.0)
                sku_stats[sid]["cost"] += float(l.amount or 0.0)

            headers = [
                "食材品名",
                "分类",
                "单位",
                "周期累计消耗量",
                "当前系统库存",
                "安全库存线",
                "累计消耗成本(元)",
                "建议采购补货量",
            ]
            rows = []
            for sid, stat in sku_stats.items():
                sku = session.get(db._SkuRow, sid)
                cat = sku.category if sku else "其他"
                curr = sku.current_stock if sku else 0.0
                safe = sku.min_stock_alert if sku else 0.0

                # 简易补货建议算法：如果当前库存低于安全线，补至 1.5 倍安全线 + 近期消耗的 50%
                suggest = (
                    round(max(0.0, (safe * 1.5 + stat["qty"] * 0.5) - curr), 1)
                    if curr <= safe
                    else 0.0
                )

                rows.append(
                    [
                        stat["name"],
                        cat,
                        stat["unit"],
                        round(stat["qty"], 2),
                        round(curr, 2),
                        round(safe, 2),
                        round(stat["cost"], 2),
                        f"{suggest} {stat['unit']}" if suggest > 0 else "库存充足",
                    ]
                )

            return {
                "title": "后厨食材累计消耗总量与采购建议",
                "filename": f"食材累计消耗与采购建议_{start or 'all'}_{end or 'now'}",
                "headers": headers,
                "rows": rows,
                "summary": {"消耗食材种数": len(rows)},
            }
        finally:
            session.close()

    # 9. 采购收据入账全量明细台账 (含行项目)
    @staticmethod
    def _handle_receipts_itemized_ledger(params, tenant_id, is_desens):
        start = params.get("start_date", "")
        end = params.get("end_date", "")
        st = params.get("receipt_status", "")
        sup_id = params.get("supplier_id", "")
        dept_id = params.get("department_id", "")

        rows_db = db.list_receipt_rows(tenant_id=tenant_id)
        depts = {d.id: d.name for d in db.list_departments()}
        sup_phones = {s.name: s.contact_phone or "" for s in db.list_suppliers(tenant_id=tenant_id)}

        headers = [
            "收据ID",
            "开单日期",
            "供应商名称",
            "供应商电话",
            "单据总额(元)",
            "单据状态",
            "付款状态",
            "归属部门",
            "商品品名",
            "采购数量",
            "单位",
            "单价(元)",
            "行金额(元)",
        ]

        # 租户跨界安全检查：如果传入的 supplier_id 不属于当前租户，直接安全返回空
        sup_filter_name = None
        if sup_id:
            try:
                sup = db.get_supplier(int(sup_id), tenant_id=tenant_id)
                if not sup:
                    return {
                        "title": "采购收据入账全量明细台账 (含行项目)",
                        "filename": f"采购收据全量明细台账_{start or 'all'}_{end or 'now'}",
                        "headers": headers,
                        "rows": [],
                        "summary": {"总行数": 0},
                    }
                sup_filter_name = sup.name
            except (ValueError, TypeError):
                return {
                    "title": "采购收据入账全量明细台账 (含行项目)",
                    "filename": f"采购收据全量明细台账_{start or 'all'}_{end or 'now'}",
                    "headers": headers,
                    "rows": [],
                    "summary": {"总行数": 0},
                }

        if dept_id and str(dept_id) != "0":
            try:
                if int(dept_id) not in depts:
                    return {
                        "title": "采购收据入账全量明细台账 (含行项目)",
                        "filename": f"采购收据全量明细台账_{start or 'all'}_{end or 'now'}",
                        "headers": headers,
                        "rows": [],
                        "summary": {"总行数": 0},
                    }
            except (ValueError, TypeError):
                return {
                    "title": "采购收据入账全量明细台账 (含行项目)",
                    "filename": f"采购收据全量明细台账_{start or 'all'}_{end or 'now'}",
                    "headers": headers,
                    "rows": [],
                    "summary": {"总行数": 0},
                }

        out_rows = []
        status_map = {
            "approved": "已入账",
            "parsed": "待核对",
            "edited": "已修改",
            "flagged": "有问题",
            "error": "失败",
        }

        matching_receipts = []
        for r in rows_db:
            if st and r.status != st:
                continue
            d = r.receipt_date or ""
            if start and d < start:
                continue
            if end and d > end:
                continue
            if dept_id and str(r.department_id) != str(dept_id):
                continue
            if sup_filter_name and r.supplier_name != sup_filter_name:
                continue
            matching_receipts.append(r)

        items_by_receipt = db.get_receipt_items_multi([r.id for r in matching_receipts], tenant_id=tenant_id)

        for r in matching_receipts:
            d = r.receipt_date or ""
            sup_name = r.supplier_name or "未注明"
            sup_phone = sup_phones.get(sup_name, "")

            if is_desens:
                sup_name = mask_vendor_name(sup_name)
                sup_phone = mask_phone(sup_phone)

            items = items_by_receipt.get(r.id, [])
            dept_name = depts.get(r.department_id, "未分配")
            pay_str = "已付" if r.paid_at else "未付"

            if not items:
                out_rows.append(
                    [
                        f"#{r.id}",
                        d,
                        sup_name,
                        sup_phone,
                        round(r.total_amount or 0.0, 2),
                        status_map.get(r.status, r.status),
                        pay_str,
                        dept_name,
                        "-",
                        0,
                        "-",
                        0,
                        round(r.total_amount or 0.0, 2),
                    ]
                )
            else:
                for it in items:
                    it_name = it.get("name", "")
                    if is_desens:
                        it_name = _mask_sensitive(it_name)
                    q = it.get("quantity", it.get("qty", 0.0))
                    u = it.get("unit", "")
                    up = it.get("unit_price", 0.0)
                    amt = it.get("amount", 0.0)
                    # 明细行如果指定了单独成本中心，取明细行部门
                    row_dept = depts.get(it.get("cost_center_id"), dept_name)

                    out_rows.append(
                        [
                            f"#{r.id}",
                            d,
                            sup_name,
                            sup_phone,
                            round(r.total_amount or 0.0, 2),
                            status_map.get(r.status, r.status),
                            pay_str,
                            row_dept,
                            it_name,
                            round(float(q or 0.0), 2),
                            u,
                            round(float(up or 0.0), 2),
                            round(float(amt or 0.0), 2),
                        ]
                    )

        return {
            "title": "采购收据入账全量明细台账 (含行项目)",
            "filename": f"采购收据全量明细台账_{start or 'all'}_{end or 'now'}",
            "headers": headers,
            "rows": out_rows,
            "summary": {"总行数": len(out_rows)},
        }

    # 10. 供应商应付账款与账期逾期表
    @staticmethod
    def _handle_supplier_payables_aging(params, tenant_id, is_desens):
        sups = db.list_suppliers(include_inactive=False, tenant_id=tenant_id)
        target_sup = params.get("supplier_id")

        headers = [
            "供应商名称",
            "供应商编号",
            "联系电话",
            "约定账期(天)",
            "未付赊单总额(元)",
            "未付赊单笔数",
            "逾期金额(元)",
            "7天内到期金额(元)",
            "结算偏好",
            "风险状态",
        ]

        if target_sup:
            try:
                sup_check = db.get_supplier(int(target_sup), tenant_id=tenant_id)
                if not sup_check:
                    return {
                        "title": "供应商应付账款与账期逾期表",
                        "filename": f"供应商应付账款表_{datetime.now().strftime('%Y%m%d')}",
                        "headers": headers,
                        "rows": [],
                        "summary": {
                            "供应商总数": 0,
                            "未付总额合计(元)": 0.0,
                        },
                    }
            except (ValueError, TypeError):
                return {
                    "title": "供应商应付账款与账期逾期表",
                    "filename": f"供应商应付账款表_{datetime.now().strftime('%Y%m%d')}",
                    "headers": headers,
                    "rows": [],
                    "summary": {
                        "供应商总数": 0,
                        "未付总额合计(元)": 0.0,
                    },
                }

        rows = []
        tot_unpaid = 0.0

        for s in sups:
            if target_sup and str(s.id) != str(target_sup):
                continue
            stats = db.supplier_stats(s.id, tenant_id=tenant_id)
            unpaid = float(stats.get("unpaid_credit_total") or 0.0)
            tot_unpaid += unpaid

            name = s.name
            phone = s.contact_phone or ""
            if is_desens:
                name = mask_vendor_name(name)
                phone = mask_phone(phone)

            # 简易风险判定
            risk = "正常"
            if unpaid > 5000:
                risk = "[提示] 应付偏大"
            elif unpaid > 0:
                risk = "账期内挂账"
            else:
                risk = "结清"

            rows.append(
                [
                    name,
                    s.supplier_code or f"SUP-{s.id}",
                    phone,
                    s.payment_terms_days or 30,
                    round(unpaid, 2),
                    stats.get("unpaid_credit_count", 0),
                    0.0,  # 逾期金额
                    0.0,  # 7天内到期
                    s.settlement_pref or "月结",
                    risk,
                ]
            )

        return {
            "title": "供应商应付账款与账期逾期表",
            "filename": f"供应商应付账款表_{datetime.now().strftime('%Y%m%d')}",
            "headers": headers,
            "rows": rows,
            "summary": {
                "供应商总数": len(rows),
                "未付总额合计(元)": round(tot_unpaid, 2),
            },
        }

    # 11. 供应商往来付款流水台账
    @staticmethod
    def _handle_supplier_payments_journal(params, tenant_id, is_desens):
        start = params.get("start_date", "")
        end = params.get("end_date", "")
        sup_id = params.get("supplier_id")

        tenant_sups = {s.name for s in db.list_suppliers(tenant_id=tenant_id)}
        payments = [p for p in db.list_payments() if p.get("supplier_name") in tenant_sups]
        if sup_id:
            try:
                sup = db.get_supplier(int(sup_id), tenant_id=tenant_id)
                if not sup:
                    return {
                        "title": "供应商往来付款流水台账",
                        "filename": f"供应商往来付款台账_{start or 'all'}_{end or 'now'}",
                        "headers": headers,
                        "rows": [],
                        "summary": {"付款笔数": 0, "付款总额合计(元)": 0.0},
                    }
                payments = [p for p in payments if p["supplier_name"] == sup.name]
            except (ValueError, TypeError):
                return {
                    "title": "供应商往来付款流水台账",
                    "filename": f"供应商往来付款台账_{start or 'all'}_{end or 'now'}",
                    "headers": headers,
                    "rows": [],
                    "summary": {"付款笔数": 0, "付款总额合计(元)": 0.0},
                }

        headers = [
            "支付流水号",
            "付款日期",
            "供应商名称",
            "支付金额(元)",
            "付款方式",
            "核销单据",
            "电子凭证状态",
            "经办备注",
        ]
        rows = []
        tot_paid = 0.0

        for p in payments:
            d = p.get("paid_at", "")
            if start and d < start:
                continue
            if end and d > end:
                continue
            amt = float(p.get("amount", 0.0))
            tot_paid += amt

            s_name = p.get("supplier_name", "")
            if is_desens:
                s_name = mask_vendor_name(s_name)

            rc_ids = p.get("receipt_ids", [])
            rc_str = (
                ", ".join([f"#{x}" for x in rc_ids])
                if isinstance(rc_ids, list)
                else str(rc_ids)
            )

            rows.append(
                [
                    f"PAY-{p.get('id')}",
                    d,
                    s_name,
                    round(amt, 2),
                    p.get("method", "银行转账"),
                    rc_str,
                    "已上传" if p.get("voucher_path") else "无凭证",
                    p.get("notes", ""),
                ]
            )

        return {
            "title": "供应商往来付款流水台账",
            "filename": f"供应商往来付款台账_{start or 'all'}_{end or 'now'}",
            "headers": headers,
            "rows": rows,
            "summary": {"付款笔数": len(rows), "付款总额合计(元)": round(tot_paid, 2)},
        }

    # 12. 供应商月结对账差异明细表
    @staticmethod
    def _handle_supplier_recon_discrepancies(params, tenant_id, is_desens):
        from app.api_finance import RECON_TASKS

        headers = [
            "任务ID",
            "供应商名称",
            "对账期间",
            "行类型",
            "单据日期",
            "单据/凭证号",
            "金额(元)",
            "核对状态",
            "处理说明",
        ]
        rows = []
        sup_id = params.get("supplier_id")
        if sup_id:
            try:
                target_sup = db.get_supplier(int(sup_id), tenant_id=tenant_id)
                if not target_sup:
                    return {
                        "title": "供应商月结对账差异明细表",
                        "filename": f"月结对账差异明细_{datetime.now().strftime('%Y%m%d')}",
                        "headers": headers,
                        "rows": [],
                        "summary": {"对账记录行数": 0},
                    }
            except (ValueError, TypeError):
                return {
                    "title": "供应商月结对账差异明细表",
                    "filename": f"月结对账差异明细_{datetime.now().strftime('%Y%m%d')}",
                    "headers": headers,
                    "rows": [],
                    "summary": {"对账记录行数": 0},
                }

        for tid, t in RECON_TASKS.items():
            if sup_id and str(t.get("supplier_id")) != str(sup_id):
                continue
            sup = db.get_supplier(t["supplier_id"], tenant_id=tenant_id)
            if not sup:
                continue
            s_name = sup.name
            if is_desens:
                s_name = mask_vendor_name(s_name)

            lines = t.get("lines", [])
            for line in lines:
                rows.append(
                    [
                        tid,
                        s_name,
                        f"{t.get('period_start', '')}~{t.get('period_end', '')}",
                        "系统单据"
                        if line.get("side") == "restaurant"
                        else "外部对账单",
                        line.get("line_date", ""),
                        line.get("docket_no", ""),
                        round(float(line.get("amount", 0.0)), 2),
                        line.get("match_status_label", "待核对"),
                        line.get("resolution_note", ""),
                    ]
                )

        return {
            "title": "供应商月结对账差异明细表",
            "filename": f"月结对账差异明细_{datetime.now().strftime('%Y%m%d')}",
            "headers": headers,
            "rows": rows,
            "summary": {"对账记录行数": len(rows)},
        }

    # 13. 部门采购花销期间环比对照表
    @staticmethod
    def _handle_department_cost_summary(params, tenant_id, is_desens):
        from app.api_suppliers import _in_period, _prev_period, _trend_type

        month = params.get("month", "")
        start = params.get("start_date", "")
        end = params.get("end_date", "")
        inc_non = int(params.get("include_non_approved", 0))

        depts = db.list_departments()
        rows_db = db.list_receipt_rows(tenant_id=tenant_id)
        if inc_non == 0:
            rows_db = [r for r in rows_db if r.status == "approved"]

        prev_month, prev_start, prev_end = _prev_period(month, start, end)

        dept_totals = {
            d.id: {
                "name": d.name,
                "total": 0.0,
                "prev_total": 0.0,
                "active": d.active,
            }
            for d in depts
        }
        unalloc = {"name": "未分配", "total": 0.0, "prev_total": 0.0}

        month_total = 0.0
        prev_total_all = 0.0

        matching_receipts = []
        for r in rows_db:
            is_cur = _in_period(r.receipt_date or "", month, start, end)
            is_prv = (
                _in_period(r.receipt_date or "", prev_month, prev_start, prev_end)
                if (prev_month or prev_start)
                else False
            )
            if is_cur or is_prv:
                matching_receipts.append((r, is_cur, is_prv))

        items_by_receipt = db.get_receipt_items_multi([r.id for r, _, _ in matching_receipts], tenant_id=tenant_id)

        for r, is_cur, is_prv in matching_receipts:
            items = items_by_receipt.get(r.id, [])
            if items:
                for it in items:
                    dept_id = it.get("cost_center_id") or r.department_id
                    amt = float(it.get("amount") or 0.0)
                    if is_cur:
                        month_total += amt
                        if dept_id in dept_totals:
                            dept_totals[dept_id]["total"] += amt
                        else:
                            unalloc["total"] += amt
                    if is_prv:
                        prev_total_all += amt
                        if dept_id in dept_totals:
                            dept_totals[dept_id]["prev_total"] += amt
                        else:
                            unalloc["prev_total"] += amt
            else:
                amt = r.total_amount or 0.0
                if is_cur:
                    month_total += amt
                    if r.department_id in dept_totals:
                        dept_totals[r.department_id]["total"] += amt
                    else:
                        unalloc["total"] += amt
                if is_prv:
                    prev_total_all += amt
                    if r.department_id in dept_totals:
                        dept_totals[r.department_id]["prev_total"] += amt
                    else:
                        unalloc["prev_total"] += amt

        headers = [
            "核算部门",
            "本期采购额(元)",
            "上期对照额(元)",
            "环比增减额(元)",
            "环比变化率(%)",
            "趋势状态",
        ]
        out_rows = []

        trend_labels = {
            "up": "[上涨]",
            "down": "[下降]",
            "flat": "[持平]",
            "new": "[新增]",
        }

        all_dept_entries = list(dept_totals.values()) + [unalloc]
        month_total = round(sum(round(d["total"], 2) for d in all_dept_entries), 2)
        prev_total_all = round(sum(round(d["prev_total"], 2) for d in all_dept_entries), 2)

        for d in all_dept_entries:
            cur = round(d["total"], 2)
            prv = round(d["prev_total"], 2)
            delta = round(cur - prv, 2)
            pct = round((delta / prv * 100), 1) if prv > 0 else (0.0 if cur == 0 else 100.0)
            trend = _trend_type(cur, prv)

            out_rows.append(
                [
                    d["name"],
                    cur,
                    prv,
                    f"+{delta:.2f}" if delta > 0 else f"{delta:.2f}",
                    f"{pct:.1f}%",
                    trend_labels.get(trend, trend),
                ]
            )

        # 合计行：与各分项严格相等
        delta_tot = round(month_total - prev_total_all, 2)
        pct_tot = (
            round((delta_tot / prev_total_all * 100), 1)
            if prev_total_all > 0
            else (0.0 if month_total == 0 else 100.0)
        )
        out_rows.append(
            [
                "合计",
                round(month_total, 2),
                round(prev_total_all, 2),
                f"+{delta_tot:.2f}" if delta_tot > 0 else f"{delta_tot:.2f}",
                f"{pct_tot:.1f}%",
                "-",
            ]
        )

        return {
            "title": "部门采购花销期间环比对照表",
            "filename": f"部门采购花销对照表_{month or start or 'all'}",
            "headers": headers,
            "rows": out_rows,
            "summary": {
                "本期采购总计(元)": round(month_total, 2),
                "上期对照总计(元)": round(prev_total_all, 2),
                "变化率": f"{pct_tot:.1f}%",
            },
        }

    # 14. 部门采购货品穿透明细流水
    @staticmethod
    def _handle_department_items_breakdown(params, tenant_id, is_desens):
        from app.api_suppliers import _in_period

        month = params.get("month", "")
        start = params.get("start_date", "")
        end = params.get("end_date", "")
        target_dept = params.get("department_id")
        inc_non = int(params.get("include_non_approved", 0))

        depts = {d.id: d.name for d in db.list_departments()}

        if target_dept is not None and str(target_dept) not in ("", "0"):
            try:
                if int(target_dept) not in depts:
                    return {
                        "title": "部门采购货品穿透明细流水",
                        "filename": f"部门采购货品穿透明细_{start or month or 'all'}",
                        "headers": headers,
                        "rows": [],
                        "summary": {"明细笔数": 0, "金额总计(元)": 0.0},
                    }
            except (ValueError, TypeError):
                return {
                    "title": "部门采购货品穿透明细流水",
                    "filename": f"部门采购货品穿透明细_{start or month or 'all'}",
                    "headers": headers,
                    "rows": [],
                    "summary": {"明细笔数": 0, "金额总计(元)": 0.0},
                }

        rows_db = db.list_receipt_rows(tenant_id=tenant_id)
        if inc_non == 0:
            rows_db = [r for r in rows_db if r.status == "approved"]

        headers = [
            "部门名称",
            "开单日期",
            "收据单号",
            "供应商名称",
            "采购品项名称",
            "采购数量",
            "单位",
            "单价(元)",
            "行金额(元)",
        ]
        out_rows = []
        tot_amt = 0.0

        matching_receipts = [r for r in rows_db if _in_period(r.receipt_date or "", month, start, end)]
        items_by_receipt = db.get_receipt_items_multi([r.id for r in matching_receipts], tenant_id=tenant_id)

        for r in matching_receipts:
            sup_name = r.supplier_name or ""
            if is_desens:
                sup_name = mask_vendor_name(sup_name)

            for it in items_by_receipt.get(r.id, []):
                item_dept_id = it.get("cost_center_id")
                if item_dept_id is None:
                    item_dept_id = r.department_id

                if target_dept is not None and str(target_dept) != "":
                    if str(target_dept) == "0":
                        if item_dept_id is not None:
                            continue
                    elif str(item_dept_id) != str(target_dept):
                        continue

                d_name = depts.get(item_dept_id, "未分配")
                it_name = it.get("name", "")
                if is_desens:
                    it_name = _mask_sensitive(it_name)
                amt = float(it.get("amount") or 0.0)
                tot_amt += amt

                out_rows.append(
                    [
                        d_name,
                        r.receipt_date or "",
                        f"#{r.id}",
                        sup_name,
                        it_name,
                        round(float(it.get("quantity") or 0.0), 2),
                        it.get("unit", ""),
                        round(float(it.get("unit_price") or 0.0), 2),
                        round(amt, 2),
                    ]
                )

        return {
            "title": "部门采购货品穿透明细流水",
            "filename": f"部门采购货品穿透明细_{start or month or 'all'}",
            "headers": headers,
            "rows": out_rows,
            "summary": {"明细笔数": len(out_rows), "金额总计(元)": round(tot_amt, 2)},
        }


# --------------------------------------------------------------------------
# 请求模型与接口
# --------------------------------------------------------------------------
class ExportReq(BaseModel):
    report_id: str
    params: Optional[Dict[str, Any]] = {}
    desensitized: Optional[bool] = False


@router.post("/api/export/preview")
def preview_report(req: ExportReq, request: Request):
    """在线获取报表预览数据：前15行示例、列头、总行数及关键汇总指标。"""
    require_role("staff")(request)
    tenant_id = _tenant_id(request)

    # AC-12: 敏感报表权限控制
    account = resolve_account(request)
    role = account.get("role") or "staff"
    if req.report_id == "department_cost_summary" and role == "staff":
        raise HTTPException(
            status_code=403,
            detail="当前角色为店员，此报表需老板或管理人员权限查看",
        )

    data = ExportDataProcessor.get_data(
        req.report_id, req.params or {}, tenant_id, is_desens=bool(req.desensitized)
    )

    all_rows = data.get("rows", [])
    preview_rows = all_rows[:15]  # 最多返回15行预览

    return {
        "status": "success",
        "data": {
            "title": data.get("title"),
            "headers": data.get("headers", []),
            "preview_rows": preview_rows,
            "total_rows": len(all_rows),
            "summary": data.get("summary", {}),
        },
    }


@router.post("/api/export/download")
def download_report(req: ExportReq, request: Request):
    """生成并下载标准 CSV 格式报表（带 UTF-8 BOM，解决 Excel 打开乱码，防公式注入，流式输出）。"""
    require_role("staff")(request)
    tenant_id = _tenant_id(request)

    # AC-12: 敏感报表权限控制
    account = resolve_account(request)
    role = account.get("role") or "staff"
    if req.report_id == "department_cost_summary" and role == "staff":
        raise HTTPException(
            status_code=403,
            detail="当前角色为店员，此报表需老板或管理人员权限查看",
        )

    data = ExportDataProcessor.get_data(
        req.report_id, req.params or {}, tenant_id, is_desens=bool(req.desensitized)
    )

    filename = data.get("filename", "export_report") + ".csv"
    headers = data.get("headers", [])
    rows = data.get("rows", [])

    def row_generator():
        # AC-2: 数据流前 3 个字节为 UTF-8 BOM (\xef\xbb\xbf)
        yield "\ufeff"
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(headers)
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)

        for r in rows:
            # EC-3: 对单元格内容进行安全转义，防止 CSV 注入攻击
            writer.writerow([_sanitize_csv_cell(c) for c in r])
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    # AC-2: RFC 5987 标准对中文文件名进行安全编码
    encoded_filename = urllib.parse.quote(filename)

    return StreamingResponse(
        row_generator(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"
        },
    )
