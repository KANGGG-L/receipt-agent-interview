# -*- coding: utf-8 -*-
"""数据导出中心后端端点：按 3 个使用者组（盘点补货/经营毛利/台账合规）提供 8 张标准报表的数据生成、在线预览与 CSV 下载。"""

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
from app.auth import ROLE_RANK, _ROLE_LABEL, require_role, resolve_account, resolve_tenant
from app.i18n_text import localize
from app.services.costing_service import CostingService
from app.services.recipe_cost import compute_recipe_theory

router = APIRouter()


def _tenant_id(request: Request) -> str:
    """Gap E2 租户键：委托 app.auth.resolve_tenant（生产态由身份派生，开发态读 X-Tenant-Id）。"""
    return resolve_tenant(request)


def _sku_category_filter(tenant_id, cat, skus):
    """返回 (mode, category_id, id_map)。未迁移保持调用方的精确字符串比较。"""
    if not cat:
        return "all", None, {}
    if not db.category_schema_ready():
        return "legacy", None, {}
    cid = db.resolve_category_filter("sku", tenant_id, cat)
    if cid is None:
        return "none", None, {}
    return "id", cid, db.read_category_id_map("skus", [s.id for s in skus])


def _sku_category_kept(mode, cid, id_map, sku, cat):
    if mode == "all":
        return True
    if mode == "legacy":
        return sku.category == cat
    if mode == "none":
        return False
    return id_map.get(sku.id) == cid


def _dish_category_filter(session, tenant_id, dish_cat, dish_ids):
    if not dish_cat:
        return "all", None, {}
    if not db._category_schema_ready_session(session):
        return "legacy", None, {}
    norm = db._normalize_category_label("dish", dish_cat)
    found = db._category_find_by_key(
        session, tenant_id, "dish", db._category_master_key(norm))
    if found is None:
        return "none", None, {}
    return "id", int(found["id"]), db.read_category_id_map(
        "dishes", list(dish_ids), session=session)


def _dish_category_kept(mode, cid, id_map, row_id, normalized_label, filter_text):
    if not filter_text or mode == "all":
        return True
    if mode == "legacy":
        return normalized_label == filter_text
    if mode == "none":
        return False
    return id_map.get(row_id) == cid


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
# B3 信息架构：类目从 4 业务域改为 3 个使用者组（用户按"我是谁、我要干什么"找表）：
#   stocktake 盘点与补货（库管）、business 经营与毛利（owner）、ledger 台账与合规（出纳/审计）。
# 分组、描述、筛选器、权限标记（min_role）全部只在本清单声明，前端不抄写第二份。
# 报表体系 14 → 8：3 张供应商应付类报表因数据模型不支撑整体移出（将来以应付管理
# 模块回归）；dish_bom_recipes 移出数据中心（BOM 配方归餐品管理维护档案）；
# dish_daily_sales_cost + dish_margin_variance 合并为 dish_cost_margin；
# department_cost_summary + department_items_breakdown 合并为 spend_summary。
REPORTS_META = [
    # 使用者组一：盘点与补货（库管阿叔）
    {
        "id": "inventory_stocktake",
        "category": "stocktake",
        "category_name": "盘点与补货",
        "title": "实时库存盘点底单",
        "description": "专为仓库/冷库现场实地盘点设计，包含账面数量及留白手填实盘列、盘盈盘亏列与签字栏。",
        "filters": ["category", "stock_filter"],
        "supports_desensitization": False,
        "supports_drafts": False,
    },
    {
        "id": "dish_ingredient_consumption",
        "min_role": "owner",
        "category": "stocktake",
        "category_name": "盘点与补货",
        "title": "采购补货建议清单",
        "description": "汇总核算周期内各原材料的总消耗量（含折算kg口径）与当前库存剩余，自动测算建议补货量。",
        "filters": ["date_range", "category", "only_suggested"],
        "supports_desensitization": False,
        "supports_drafts": False,
    },
    # 使用者组二：经营与毛利（owner）
    {
        "id": "inventory_valuation",
        "min_role": "owner",
        "category": "business",
        "category_name": "经营与毛利",
        "title": "库存货值结存表",
        "description": "核算全量食材的当前库存、折算标准kg、最新进价及资产货值估算，监控低库存风险。",
        "filters": ["category", "stock_filter", "value_min"],
        "supports_desensitization": False,
        "supports_drafts": False,
    },
    {
        "id": "inventory_price_anomaly",
        "min_role": "owner",
        "category": "business",
        "category_name": "经营与毛利",
        "title": "采购进价波动与比价表",
        "description": "比对最新进价与30日均价涨跌幅、历史极值，标红涨幅超{threshold}异动食材，辅助议价与选品。",
        "filters": ["category", "direction", "threshold_override", "window_days"],
        "supports_desensitization": False,
        "supports_drafts": False,
    },
    {
        "id": "dish_cost_margin",
        "min_role": "owner",
        "category": "business",
        "category_name": "经营与毛利",
        "title": "菜品成本与毛利表",
        "description": "理论成本 vs FIFO实际成本同表对照，附偏差归因建议；按日逐单或按期间汇总两种粒度切换。",
        "filters": ["date_range", "cost_granularity", "margin_threshold", "dish_status", "dish_category"],
        "supports_desensitization": False,
        "supports_drafts": False,
    },
    {
        "id": "spend_summary",
        "category": "business",
        "category_name": "经营与毛利",
        "title": "期间花销汇总表",
        "description": "月度汇报报表：按部门或供应商透视本期采购与上期对照，展示环比增减与趋势状态。",
        "filters": ["date_range", "spend_pivot", "pivot_focus", "amount_range"],
        "supports_desensitization": True,
        "supports_drafts": True,
        "min_role": "owner",
    },
    # 使用者组三：台账与合规（出纳/审计）
    {
        "id": "inventory_stock_logs",
        "min_role": "owner",
        "category": "ledger",
        "category_name": "台账与合规",
        "title": "出入库全量流水台账",
        "description": "整合采购入库、餐品消耗、后厨报损、盘点调整的完整物理库存变动日记账。",
        "filters": ["date_range", "log_kind", "sku_id", "receipt_ref", "vendor_keyword"],
        "supports_desensitization": True,
        "supports_drafts": False,
    },
    {
        "id": "receipts_itemized_ledger",
        "min_role": "owner",
        "category": "ledger",
        "category_name": "台账与合规",
        "title": "采购入账明细台账",
        "description": "财务标准做账台账：单据抬头与行项目全景展开，包含供应商、日期、商品品名、数量、单价与部门。",
        "filters": ["date_range", "receipt_status", "supplier_id", "department_id", "keyword", "paid_status", "amount_range", "currency"],
        "supports_desensitization": True,
        "supports_drafts": True,
    },
]


@router.get("/api/export/reports")
def get_available_reports(request: Request):
    """获取所有可用导出的报表定义元数据。

    价格异动报表的 description 由**本租户生效阈值**动态渲染（WS6：消除硬编码 10%，
    与 api_inventory 的 price_anomaly_threshold_pct 同源；阈值可由 admin 全局默认 /
    owner 门店覆盖更改，写死数字会立刻失真）。
    """
    require_role("staff")(request)
    tenant_id = _tenant_id(request)
    return {"status": "success", "data": _reports_meta_for_tenant(tenant_id)}


def _reports_meta_for_tenant(tenant_id=None):
    """REPORTS_META 的租户视图：仅把价格异动报表描述里的 {threshold} 占位替换为
    本租户生效阈值（单一事实源仍在 REPORTS_META 的模板文案里，不另抄一份）。"""
    import copy

    try:
        from app.services.price_anomaly import get_threshold_pct
        pct = get_threshold_pct(tenant_id)
    except Exception:
        pct = None
    pct_text = f"{pct:g}%" if isinstance(pct, (int, float)) else "预警阈值"
    meta = copy.deepcopy(REPORTS_META)
    for r in meta:
        if isinstance(r.get("description"), str) and "{threshold}" in r["description"]:
            r["description"] = r["description"].replace("{threshold}", pct_text)
    return meta


def _enforce_report_role_gate(report_id: str, request: Request) -> None:
    """AC-12 / A2: 敏感报表角色门禁（preview 与 download 共用同一实现）。

    门禁标记只声明在 REPORTS_META 的 `min_role` 字段（单一事实源），不再按
    report_id 在端点里硬编码；meta 缺省（无 min_role）即对 staff 开放。
    """
    meta = next((r for r in REPORTS_META if r["id"] == report_id), None)
    min_role = (meta or {}).get("min_role")
    if not min_role:
        return
    account = resolve_account(request)
    role = account.get("role") or "staff"
    if ROLE_RANK.get(role, 0) >= ROLE_RANK.get(min_role, 99):
        return
    # 人话文案：对 staff 语境固定说「老板或管理人员」（AC-12 既有口径，勿改成角色枚举罗列）
    raise HTTPException(
        status_code=403,
        detail=f"当前角色为{_ROLE_LABEL.get(role, role)}，此报表需老板或管理人员权限查看",
    )


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
        cat_mode, cat_id, cat_map = _sku_category_filter(tenant_id, cat, skus)
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
            if not _sku_category_kept(cat_mode, cat_id, cat_map, s, cat):
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
        cat_mode, cat_id, cat_map = _sku_category_filter(tenant_id, cat, skus)
        stock_flt = params.get("stock_filter", "all")
        # FILTERS-§7: 货值下限（行级过滤；summary 保持全量口径）
        try:
            value_min = float(params.get("value_min"))
        except (TypeError, ValueError):
            value_min = None

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
            if not _sku_category_kept(cat_mode, cat_id, cat_map, s, cat):
                continue
            is_low = s.min_stock_alert > 0 and s.current_stock <= s.min_stock_alert
            if stock_flt == "low" and not is_low:
                continue

            kg_val = _standard_kg(s.current_stock, s.base_unit, tenant_id=tenant_id)
            if kg_val:
                total_kg += kg_val
            unit_p = s.last_unit_price or 0.0
            val = round(s.current_stock * unit_p, 2)
            total_val += val

            # FILTERS-§7: 行级过滤在合计累加之后，summary 保持全量口径
            if value_min is not None and val < value_min:
                continue

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
            "title": "库存货值结存表",
            "filename": f"库存货值结存表_{datetime.now().strftime('%Y%m%d')}",
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
        from app.api_inventory import _compute_vs_avg_and_anomaly, _threshold_pct

        skus = db.list_skus(include_inactive=False, tenant_id=tenant_id)
        cat = params.get("category", "")
        cat_mode, cat_id, cat_map = _sku_category_filter(tenant_id, cat, skus)
        # FILTERS-§4: 涨跌方向 / 阈值临时覆盖 / 均价窗口天数
        direction = params.get("direction", "all")
        if direction not in ("all", "up", "down"):
            direction = "all"

        def _to_float_or_none(v):
            try:
                f = float(v)
                return f if f == f else None
            except (TypeError, ValueError):
                return None

        # 阈值覆盖仅作用于本次请求（SSOT 只读，不写回配置）
        threshold_override = _to_float_or_none(params.get("threshold_pct"))
        if threshold_override is not None:
            threshold_override = max(0.0, min(100.0, threshold_override))

        try:
            window_days = int(params.get("window_days", 30))
        except (TypeError, ValueError):
            window_days = 30
        if window_days not in (7, 30, 60):
            window_days = 30

        headers = [
            "食材品名",
            "食材代码",
            "分类",
            "基本单位",
            "最新进价(元)",
            f"近{window_days}天均价(元)",
            "波动幅度",
            "价格异动预警",
            "历史最低价(元)",
            "历史最高价(元)",
        ]
        rows = []
        anomaly_count = 0
        for s in skus:
            if not _sku_category_kept(cat_mode, cat_id, cat_map, s, cat):
                continue
            vs_pct, is_anomaly, avg_nd = _compute_vs_avg_and_anomaly(
                s.id,
                threshold_pct=threshold_override,
                tenant_id=tenant_id,
                days=window_days,
            )
            # FILTERS-§4: 涨跌方向
            if direction == "up" and vs_pct <= 0:
                continue
            if direction == "down" and vs_pct >= 0:
                continue
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
                    round(avg_nd, 2),
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
            "summary": {
                "分析SKU总数": len(rows),
                "涨价预警SKU数": anomaly_count,
                # 覆盖生效提示：与租户 SSOT 不同时明示，避免误读
                "均价窗口(天)": window_days,
                **(
                    {"生效阈值(%)": threshold_override}
                    if threshold_override is not None
                    and threshold_override != _threshold_pct(tenant_id)
                    else {}
                ),
            },
        }

    # 4. 出入库全量流水综合台账
    @staticmethod
    def _handle_inventory_stock_logs(params, tenant_id, is_desens):
        start = params.get("start_date", "")
        end = params.get("end_date", "")
        kind = params.get("log_kind", "")
        # WS4-4：新增 sku_id 过滤（走 db.scoped 同源租户口径；与台账接口同源）。
        sku_id_flt = params.get("sku_id", "")
        # FILTERS-§6: 按关联收据号 / 供应商名过滤（审计追责：这笔变动来自哪张单）
        receipt_ref = str(params.get("receipt_ref", "")).strip()
        vendor_flt = str(params.get("vendor", "")).strip()

        session = db.get_session()
        try:
            q = session.query(db._StockLogRow)
            q = db.scoped(q, db._StockLogRow, tenant_id)
            if kind:
                q = q.filter(db._StockLogRow.kind == kind)
            if sku_id_flt not in (None, ""):
                try:
                    q = q.filter(db._StockLogRow.sku_id == int(sku_id_flt))
                except (TypeError, ValueError):
                    # 非法 sku_id：不静默全量，落到空结果（避免把"筛选条件写错"
                    # 当成"无条件"从而导出全表）。
                    q = q.filter(db._StockLogRow.sku_id == -1)
            if receipt_ref:
                # 兼容 `#1502` / `1502` 两种输入：剥离非数字字符后精确匹配
                digits = "".join(ch for ch in receipt_ref if ch.isdigit())
                if digits:
                    q = q.filter(db._StockLogRow.receipt_id == int(digits))
                else:
                    q = q.filter(db._StockLogRow.id == -1)
            if vendor_flt:
                q = q.filter(db._StockLogRow.vendor.like(f"%{vendor_flt}%"))
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
                "title": "出入库全量流水台账",
                "filename": f"出入库全量流水台账_{start or 'all'}_{end or 'now'}",
                "headers": headers,
                "rows": rows,
                "summary": {"流水总笔数": len(rows), "总金额合计(元)": round(total_amt, 2)},
            }
        finally:
            session.close()

    # 5. 菜品成本与毛利表（B1 合并：原 dish_daily_sales_cost + dish_margin_variance）
    # 同一数据链路的两段展示（日度逐单 / 期间按餐品汇总）合成一张表，
    # 列取两者并集，粒度经 params.cost_granularity 切换（daily | period）。
    @staticmethod
    def _handle_dish_cost_margin(params, tenant_id, is_desens):
        start = params.get("start_date", "")
        end = params.get("end_date", "")
        granularity = params.get("cost_granularity", "daily")
        if granularity not in ("daily", "period"):
            granularity = "daily"
        # FILTERS-§2: 毛利率阈值 / 菜品状态 / 菜品分类
        dish_status = params.get("dish_status", "all")
        if dish_status not in ("all", "active", "inactive"):
            dish_status = "all"
        dish_cat = str(params.get("dish_category", "")).strip()

        def _to_float_or_none(v):
            try:
                f = float(v)
                return f if f == f else None
            except (TypeError, ValueError):
                return None

        margin_pct_max = _to_float_or_none(params.get("margin_pct_max"))
        if margin_pct_max is not None:
            margin_pct_max = max(0.0, min(100.0, margin_pct_max))

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
            consumes = q.order_by(
                db._DailyConsumptionRow.date.desc(), db._DailyConsumptionRow.id.desc()
            ).all()
            dish_mode, dish_cid, dish_map = _dish_category_filter(
                session, tenant_id, dish_cat, tenant_dish_ids)

            headers = [
                "核算周期",
                "餐品名称",
                "分类",
                "售出份数",
                "在售标价(元)",
                "营业总额(元)",
                "FIFO实际食材成本(元)",
                "实际毛利额(元)",
                "实际毛利率(%)",
                "理论基准成本(元)",
                "理论毛利率(%)",
                "成本偏差(元)",
                "毛利侵蚀点(%)",
                "诊断分析建议",
                "状态",
            ]
            rows = []
            tot_sales = 0.0
            tot_cost = 0.0

            def _advice(erosion, has_consumption):
                # 偏差归因口径沿用原 dish_margin_variance 诊断建议
                if not has_consumption:
                    return "统计周期内暂无消耗记录"
                if erosion > 5.0:
                    return "[预警] 成本大幅承压，建议排查高价食材或微调售价"
                if erosion > 2.0:
                    return "进货价微涨，注意控制损耗"
                return "毛利健康稳定"

            def _erosion_text(erosion):
                return f"{erosion:.1f}%" if erosion > 0 else "0.0%"

            if granularity == "daily":
                # 按日粒度：逐单展示（原 dish_daily_sales_cost 口径）
                for c in consumes:
                    dish = session.get(db._DishRow, c.dish_id)
                    dish_name = dish.name if dish else f"餐品#{c.dish_id}"
                    dish_cat_row = (dish.category if dish else "") or "默认"
                    if dish_cat_row == "其他":
                        dish_cat_row = "默认"
                    dish_p = dish.price if dish else 0.0

                    # FILTERS-§2: 菜品状态 / 分类
                    if dish_status != "all":
                        row_status = (dish.status if dish else "") or ""
                        if row_status != dish_status:
                            continue
                    if not _dish_category_kept(
                            dish_mode, dish_cid, dish_map, c.dish_id,
                            dish_cat_row, dish_cat):
                        continue

                    sales = round(float(c.quantity * dish_p), 2)
                    act_cost = round(float(c.total_cost or 0.0), 2)
                    margin = round(sales - act_cost, 2)
                    margin_pct = round(margin / sales * 100, 1) if sales > 0 else 0.0

                    # 单份理论成本（与详情/其它报表共用 recipe_cost 唯一实现）
                    theo_unit_c, _ing = compute_recipe_theory(
                        session, c.dish_id, tenant_id)
                    theo_cost = round(theo_unit_c * c.quantity, 2)
                    theo_margin = (
                        round((dish_p - theo_unit_c) / dish_p * 100, 1)
                        if dish_p > 0
                        else 0.0
                    )
                    diff = round(act_cost - theo_cost, 2)
                    erosion = round(theo_margin - margin_pct, 1)

                    tot_sales += sales
                    tot_cost += act_cost

                    rows.append(
                        [
                            c.date,
                            dish_name,
                            dish_cat_row,
                            round(c.quantity, 1),
                            round(dish_p, 2),
                            sales,
                            act_cost,
                            margin,
                            f"{margin_pct:.1f}%",
                            theo_cost,
                            f"{theo_margin:.1f}%",
                            diff,
                            _erosion_text(erosion),
                            _advice(erosion, True),
                            "已核销",
                        ]
                    )
            else:
                # 期间粒度：按餐品聚合周期内消耗（原 dish_margin_variance 口径，
                # 含周期内无消耗的在售餐品，便于 owner 识别"零销量但压成本"的菜）
                period_label = f"{start or '全部'} ~ {end or '至今'}"
                active_dishes_q = db.scoped(
                    session.query(db._DishRow), db._DishRow, tenant_id
                )
                # FILTERS-§2: 菜品状态（原硬编码 active，现按参数；分类在循环内过滤）
                if dish_status != "all":
                    active_dishes_q = active_dishes_q.filter(
                        db._DishRow.status == dish_status
                    )
                active_dishes = active_dishes_q.all()
                for d in active_dishes:
                    d_cat_row = (d.category or "").strip() or "默认"
                    if d_cat_row == "其他":
                        d_cat_row = "默认"
                    if not _dish_category_kept(
                            dish_mode, dish_cid, dish_map, d.id,
                            d_cat_row, dish_cat):
                        continue
                    cq = [
                        c
                        for c in consumes
                        if c.dish_id == d.id
                    ]

                    total_qty = sum(c.quantity for c in cq)
                    total_sales = round(sum(float(c.quantity) * d.price for c in cq), 2)
                    total_actual = round(sum(float(c.total_cost or 0.0) for c in cq), 2)

                    # 理论成本口径与详情/其它报表一致（recipe_cost 唯一实现）
                    theo_unit_c, _ing = compute_recipe_theory(session, d.id, tenant_id)
                    theo_cost_total = round(theo_unit_c * total_qty, 2)

                    real_unit_cost = (
                        round(total_actual / total_qty, 2) if total_qty > 0 else theo_unit_c
                    )
                    margin = round(total_sales - total_actual, 2)
                    real_margin = (
                        round((d.price - real_unit_cost) / d.price * 100, 1)
                        if d.price > 0
                        else 0.0
                    )
                    theo_margin = (
                        round((d.price - theo_unit_c) / d.price * 100, 1)
                        if d.price > 0
                        else 0.0
                    )

                    cost_diff = round(real_unit_cost - theo_unit_c, 2)
                    erosion = round(theo_margin - real_margin, 1)

                    tot_sales += total_sales
                    tot_cost += total_actual

                    rows.append(
                        [
                            period_label,
                            d.name,
                            d_cat_row,
                            round(total_qty, 1),
                            round(d.price, 2),
                            total_sales,
                            total_actual,
                            margin,
                            f"{real_margin:.1f}%",
                            theo_cost_total,
                            f"{theo_margin:.1f}%",
                            f"+{cost_diff:.2f}" if cost_diff > 0 else f"{cost_diff:.2f}",
                            _erosion_text(erosion),
                            _advice(erosion, total_qty > 0),
                            "期间汇总",
                        ]
                    )

            # FILTERS-§2: 毛利率阈值行级过滤（列 8 = 实际毛利率(%) "12.3%" 格式）；
            # summary 的 tot_sales/tot_cost 在上方按全量累计，保持全量口径
            if margin_pct_max is not None:
                def _row_margin_pct(row):
                    try:
                        return float(str(row[8]).replace("%", ""))
                    except (ValueError, TypeError, IndexError):
                        return None

                def _margin_kept(row):
                    m = _row_margin_pct(row)
                    return m is not None and m <= margin_pct_max

                rows = [r for r in rows if _margin_kept(r)]

            return {
                "title": "菜品成本与毛利表",
                "filename": f"菜品成本与毛利表_{granularity}_{start or 'all'}_{end or 'now'}",
                "headers": headers,
                "rows": rows,
                "summary": {
                    "核算粒度": "按日逐单" if granularity == "daily" else "按期间汇总",
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

    # 6. 采购补货建议清单（原"后厨食材累计消耗总量与采购建议"）
    @staticmethod
    def _handle_dish_ingredient_consumption(params, tenant_id, is_desens):
        from app.services.unit_factors import unit_to_kg_factor

        start = params.get("start_date", "")
        end = params.get("end_date", "")
        # FILTERS-§3: 仅看建议补货 > 0（输出直接可当下单清单）
        only_suggested = str(params.get("only_suggested", "")).lower() in ("1", "true", "yes")

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
                        "kg": 0.0,
                        "cost": 0.0,
                    }
                sku_stats[sid]["qty"] += float(l.qty or 0.0)
                # B4: 折算口径与成本层同源（unit_factors SSOT）；仅重量单位折算，
                # 计件/体积/空单位不假装折算（factor 为 None 时不计 kg 合计）。
                factor = unit_to_kg_factor(l.unit, tenant_id)
                if factor is not None:
                    sku_stats[sid]["kg"] += float(l.qty or 0.0) * factor
                sku_stats[sid]["cost"] += float(l.amount or 0.0)

            headers = [
                "食材品名",
                "分类",
                "单位",
                "周期累计消耗量",
                "折算消耗(kg)",
                "当前系统库存",
                "安全库存线",
                "累计消耗成本(元)",
                "建议采购补货量",
            ]
            rows = []
            total_kg = 0.0
            for sid, stat in sku_stats.items():
                sku = session.get(db._SkuRow, sid)
                cat = sku.category if sku else "其他"
                curr = sku.current_stock if sku else 0.0
                safe = sku.min_stock_alert if sku else 0.0
                total_kg += stat["kg"]

                # 简易补货建议算法：如果当前库存低于安全线，补至 1.5 倍安全线 + 近期消耗的 50%
                suggest = (
                    round(max(0.0, (safe * 1.5 + stat["qty"] * 0.5) - curr), 1)
                    if curr <= safe
                    else 0.0
                )
                # FILTERS-§3: 过滤发生在 total_kg 累加之后，summary 保持全量口径
                if only_suggested and suggest <= 0:
                    continue

                rows.append(
                    [
                        stat["name"],
                        cat,
                        stat["unit"],
                        round(stat["qty"], 2),
                        round(stat["kg"], 2) if stat["kg"] > 0 else "-",
                        round(curr, 2),
                        round(safe, 2),
                        round(stat["cost"], 2),
                        f"{suggest} {stat['unit']}" if suggest > 0 else "库存充足",
                    ]
                )

            return {
                "title": "采购补货建议清单",
                "filename": f"采购补货建议清单_{start or 'all'}_{end or 'now'}",
                "headers": headers,
                "rows": rows,
                "summary": {
                    # 全量口径（only_suggested 过滤行不改汇总统计）
                    "消耗食材种数": len(sku_stats),
                    "折算消耗合计(kg)": round(total_kg, 2),
                },
            }
        finally:
            session.close()

    # 7. 采购入账明细台账 (含行项目)
    @staticmethod
    def _handle_receipts_itemized_ledger(params, tenant_id, is_desens):
        start = params.get("start_date", "")
        end = params.get("end_date", "")
        st = params.get("receipt_status", "")
        sup_id = params.get("supplier_id", "")
        dept_id = params.get("department_id", "")
        # FILTERS-§1: keyword（单号/供应商/品名）、paid_status、金额区间、币种
        keyword = str(params.get("keyword", "")).strip().lower()
        paid_status = params.get("paid_status", "all")
        if paid_status not in ("all", "unpaid", "paid", "overdue"):
            paid_status = "all"
        currency_flt = str(params.get("currency", "")).strip().upper()

        def _to_float_or_none(v):
            try:
                f = float(v)
                return f if f == f else None  # 剔除 NaN
            except (TypeError, ValueError):
                return None

        amount_min = _to_float_or_none(params.get("amount_min"))
        amount_max = _to_float_or_none(params.get("amount_max"))

        today_str = datetime.now().strftime("%Y-%m-%d")

        def _paid_status_of(r):
            if r.paid_at:
                return "paid"
            exp = str(getattr(r, "expected_pay_date", "") or "")
            if exp and exp < today_str:
                return "overdue"
            return "unpaid"

        rows_db = db.list_receipt_rows(tenant_id=tenant_id)
        depts = {d.id: d.name for d in db.list_departments(tenant_id=tenant_id)}
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
                        "title": "采购入账明细台账",
                        "filename": f"采购入账明细台账_{start or 'all'}_{end or 'now'}",
                        "headers": headers,
                        "rows": [],
                        "summary": {"总行数": 0},
                    }
                sup_filter_name = sup.name
            except (ValueError, TypeError):
                return {
                    "title": "采购入账明细台账",
                    "filename": f"采购入账明细台账_{start or 'all'}_{end or 'now'}",
                    "headers": headers,
                    "rows": [],
                    "summary": {"总行数": 0},
                }

        if dept_id and str(dept_id) != "0":
            try:
                if int(dept_id) not in depts:
                    return {
                        "title": "采购入账明细台账",
                        "filename": f"采购入账明细台账_{start or 'all'}_{end or 'now'}",
                        "headers": headers,
                        "rows": [],
                        "summary": {"总行数": 0},
                    }
            except (ValueError, TypeError):
                return {
                    "title": "采购入账明细台账",
                    "filename": f"采购入账明细台账_{start or 'all'}_{end or 'now'}",
                    "headers": headers,
                    "rows": [],
                    "summary": {"总行数": 0},
                }

        out_rows = []
        status_map = {
            "approved": "已入账",
            "parsed": "待核对",
            "parsed_with_warnings": "待核对(门禁警告)",
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
            # FILTERS-§1: 付清状态 / 金额区间 / 币种（单据级过滤）
            # FILTERS-1: unpaid means paid_at is empty (spec), so overdue is a
            # subset of unpaid, not a disjoint enum. paid = paid_at non-empty,
            # overdue = not paid and past due date.
            if paid_status == "paid" and not r.paid_at:
                continue
            if paid_status == "unpaid" and r.paid_at:
                continue
            if paid_status == "overdue" and _paid_status_of(r) != "overdue":
                continue
            amt = float(r.total_amount or 0.0)
            if amount_min is not None and amt < amount_min:
                continue
            if amount_max is not None and amt > amount_max:
                continue
            if currency_flt and str(getattr(r, "currency", "") or "").upper() != currency_flt:
                continue
            matching_receipts.append(r)

        items_by_receipt = db.get_receipt_items_multi([r.id for r in matching_receipts], tenant_id=tenant_id)

        for r in matching_receipts:
            d = r.receipt_date or ""
            sup_name = r.supplier_name or "未注明"
            sup_phone = sup_phones.get(sup_name, "")

            # FILTERS-§1: keyword 命中判定——单号 / 供应商名（用原文匹配，脱敏只影响输出）。
            # 单号兼容 `#1502` / `1502` 两种输入（与 stock_logs.receipt_ref 同口径：
            # 剥离 # 前缀后再比对数字 id），避免"照抄显示号搜索"落空。
            if keyword:
                kw_id = keyword.lstrip("#")
                receipt_hit = (
                    keyword in str(r.id)
                    or (kw_id and kw_id.isdigit() and kw_id in str(r.id))
                    or keyword in str(r.supplier_name or "").lower()
                )

            if is_desens:
                sup_name = mask_vendor_name(sup_name)
                sup_phone = mask_phone(sup_phone)

            items = items_by_receipt.get(r.id, [])
            if keyword and not receipt_hit:
                items = [
                    it for it in items
                    if keyword in str(it.get("name", "")).lower()
                ]
                if not items:
                    continue
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
            "title": "采购入账明细台账",
            "filename": f"采购入账明细台账_{start or 'all'}_{end or 'now'}",
            "headers": headers,
            "rows": out_rows,
            "summary": {"总行数": len(out_rows)},
        }

    # 8. 期间花销汇总表（B1 合并：原 department_cost_summary + department_items_breakdown）
    # 部门与供应商从"报表"降级为透视维度（params.spend_pivot），环比口径沿用原
    # department_cost_summary；本期/上期聚合、趋势状态与合计行同源 _trend_type。
    @staticmethod
    def _handle_spend_summary(params, tenant_id, is_desens):
        from app.api_suppliers import _in_period, _prev_period, _trend_type

        month = params.get("month", "")
        start = params.get("start_date", "")
        end = params.get("end_date", "")
        pivot = params.get("spend_pivot", "department")
        if pivot not in ("department", "supplier"):
            pivot = "department"
        inc_non = int(params.get("include_non_approved", 0))

        from app.services import cost_report as cr

        prev_month, prev_start, prev_end = _prev_period(month, start, end)
        has_prev = bool(prev_month or prev_start)
        dept_list = db.list_departments(tenant_id=tenant_id)

        # 维度桶：部门（含未分配）或供应商（未注明单列一桶）
        if pivot == "department":
            buckets = {
                d.id: {"label": d.name, "total": 0.0, "prev_total": 0.0, "count": 0}
                for d in dept_list
            }
            unalloc_label = "未分配"
        else:
            buckets = {
                s.name: {"label": s.name, "total": 0.0, "prev_total": 0.0, "count": 0}
                for s in db.list_suppliers(tenant_id=tenant_id)
            }
            unalloc_label = "未注明供应商"
        unalloc = {"label": unalloc_label, "total": 0.0, "prev_total": 0.0, "count": 0}

        # 与 /api/cost_report 同一口径（services/cost_report.py）：已入账单据总额，
        # 外币折港币，按明细比例分摊；本期笔数 = 本期有金额落在该桶的单据张数。
        built = cr.build_allocations(
            tenant_id,
            lambda d: _in_period(d, month, start, end),
            (lambda d: _in_period(d, prev_month, prev_start, prev_end)) if has_prev else (lambda d: False),
            include_non_approved=bool(inc_non),
            valid_dept_ids={d.id for d in dept_list})

        for a in built["allocs"]:
            r = a["receipt"]
            counted = set()
            for part in a["alloc"]["parts"]:
                if pivot == "department":
                    b = buckets.get(part["dept_id"], unalloc)
                else:
                    b = buckets.get(r.supplier_name or "", unalloc)
                if a["is_cur"]:
                    b["total"] += part["amount_hkd"]
                    if id(b) not in counted:
                        b["count"] += 1
                        counted.add(id(b))
                if a["is_prv"]:
                    b["prev_total"] += part["amount_hkd"]

        dim_label = "部门" if pivot == "department" else "供应商"
        headers = [
            "花销维度",
            "核算对象",
            "本期采购额(元)",
            "上期对照额(元)",
            "环比增减额(元)",
            "环比变化率(%)",
            "本期笔数",
            "趋势状态",
        ]
        out_rows = []

        trend_labels = {
            "up": "[上涨]",
            "down": "[下降]",
            "flat": "[持平]",
            "new": "[新增]",
        }

        def _fmt_delta(delta):
            return f"+{delta:.2f}" if delta > 0 else f"{delta:.2f}"

        # FILTERS-§5: 维度聚焦 + 金额下限。聚焦时只保留该维度桶（合计同步重算，
        # 不是全量合计砍掉）；聚焦目标不属于本租户时安全返回空。
        focus_label = None
        focus_invalid = False
        if pivot == "department":
            dept_focus = str(params.get("department_id", "") or "")
            if dept_focus and dept_focus != "0":
                try:
                    focus_label = next(
                        (d.name for d in db.list_departments(tenant_id=tenant_id)
                         if str(d.id) == dept_focus), None)
                except (TypeError, ValueError):
                    focus_label = None
                # FILTERS-5: invalid department focus (not in tenant) must
                # return an empty bucket, not degrade into an unfiltered table.
                if focus_label is None:
                    focus_invalid = True
        else:
            sup_focus = str(params.get("supplier_id", "") or "")
            if sup_focus:
                sup = None
                try:
                    sup = db.get_supplier(int(sup_focus), tenant_id=tenant_id)
                except (TypeError, ValueError):
                    sup = None
                # Same empty-bucket contract as invalid department focus;
                # never fall back to matching an empty supplier name.
                if sup is None:
                    focus_invalid = True
                else:
                    focus_label = sup.name

        def _to_float_or_none(v):
            try:
                f = float(v)
                return f if f == f else None
            except (TypeError, ValueError):
                return None

        amount_min = _to_float_or_none(params.get("amount_min"))

        all_entries = list(buckets.values()) + [unalloc]
        if focus_label is not None:
            all_entries = [e for e in all_entries if e["label"] == focus_label]
        elif focus_invalid:
            all_entries = []
        if amount_min is not None:
            all_entries = [e for e in all_entries if e["total"] >= amount_min]
        month_total = round(sum(round(e["total"], 2) for e in all_entries), 2)
        prev_total_all = round(sum(round(e["prev_total"], 2) for e in all_entries), 2)
        total_count = sum(e["count"] for e in all_entries)

        for e in all_entries:
            cur = round(e["total"], 2)
            prv = round(e["prev_total"], 2)
            delta = round(cur - prv, 2)
            pct = round((delta / prv * 100), 1) if prv > 0 else (0.0 if cur == 0 else 100.0)
            trend = _trend_type(cur, prv)
            label = e["label"]
            if pivot == "supplier" and is_desens and label != unalloc_label:
                label = mask_vendor_name(label)

            out_rows.append(
                [
                    dim_label,
                    label,
                    cur,
                    prv,
                    _fmt_delta(delta),
                    f"{pct:.1f}%",
                    e["count"],
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
                dim_label,
                "合计",
                round(month_total, 2),
                round(prev_total_all, 2),
                _fmt_delta(delta_tot),
                f"{pct_tot:.1f}%",
                total_count,
                "-",
            ]
        )

        return {
            "title": "期间花销汇总表",
            "filename": f"期间花销汇总表_{pivot}_{month or start or 'all'}",
            "headers": headers,
            "rows": out_rows,
            "summary": {
                "透视维度": "按部门" if pivot == "department" else "按供应商",
                "本期采购总计(元)": round(month_total, 2),
                "上期对照总计(元)": round(prev_total_all, 2),
                "变化率": f"{pct_tot:.1f}%",
            },
        }


# --------------------------------------------------------------------------
# 请求模型与接口
# --------------------------------------------------------------------------
class ExportReq(BaseModel):
    report_id: str
    params: Optional[Dict[str, Any]] = {}
    desensitized: Optional[bool] = False
    # 界面语言（zh-Hans / zh-Hant-HK）。只影响 CSV 表头与下载文件名；行数据原样输出，
    # 不做简繁转换（供应商名、品名属于业务数据，导出后可能回灌或对账）。缺省为简体原文。
    lang: Optional[str] = None


@router.post("/api/export/preview")
def preview_report(req: ExportReq, request: Request):
    """在线获取报表预览数据：全量行、列头、总行数及关键汇总指标。"""
    require_role("staff")(request)
    tenant_id = _tenant_id(request)

    # AC-12: 敏感报表权限控制（门禁标记声明在 REPORTS_META，与 download 同一实现）
    _enforce_report_role_gate(req.report_id, request)

    data = ExportDataProcessor.get_data(
        req.report_id, req.params or {}, tenant_id, is_desens=bool(req.desensitized)
    )

    all_rows = data.get("rows", [])
    # 预览与导出同口径：preview 返回全量行（行数见 total_rows），不截断。

    return {
        "status": "success",
        "data": {
            "title": data.get("title"),
            "headers": data.get("headers", []),
            "preview_rows": all_rows,
            "total_rows": len(all_rows),
            "summary": data.get("summary", {}),
        },
    }


@router.post("/api/export/download")
def download_report(req: ExportReq, request: Request):
    """生成并下载标准 CSV 格式报表（带 UTF-8 BOM，解决 Excel 打开乱码，防公式注入，流式输出）。"""
    require_role("staff")(request)
    tenant_id = _tenant_id(request)

    # AC-12: 敏感报表权限控制（与 preview 共用门禁，避免两处硬编码漂移）
    _enforce_report_role_gate(req.report_id, request)

    data = ExportDataProcessor.get_data(
        req.report_id, req.params or {}, tenant_id, is_desens=bool(req.desensitized)
    )

    filename = localize(data.get("filename", "export_report"), req.lang) + ".csv"
    headers = [localize(h, req.lang) for h in data.get("headers", [])]
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
