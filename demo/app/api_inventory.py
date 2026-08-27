# -*- coding: utf-8 -*-
"""库存端点：SKU 列表/CRUD/盘点/消耗/损耗/价格历史。"""

import re

from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Optional

from app import db
from app.auth import require_role

# B-P0-1 流水号后缀正则（FR-7 品名归一）与归一辅助
_SERIAL_RE = re.compile(r"_\d{10}$")


def _canonical_name(raw: str) -> str:
    """FR-7/B-P0-1 库存侧归一：优先走 SmartSplitter v1_2_0 剥离 _\\d{10} 后缀，失败则正则兜底。"""
    name = (raw or "").strip()
    if not name:
        return name
    try:
        from ai_registry.tools.smart_splitter.v1_2_0_multi_pack import SmartSplitterTool
        return SmartSplitterTool().sanitize_name(name)
    except Exception:
        return _SERIAL_RE.sub("", name).strip()

router = APIRouter()

# 复用 currency_unit_converter 标准换算（司马斤 0.6048 kg）
try:
    from ai_registry.tools.currency_unit_converter.v1_0_0 import CurrencyUnitConverterTool
    _kg_converter = CurrencyUnitConverterTool()
except Exception:
    _kg_converter = None


def _standard_kg(current_stock, base_unit):
    """严格仅对重量单位折算 kg；计件单位返回 None（绝不假装折算）。"""
    if _kg_converter is None:
        return None
    u = (base_unit or "").strip()
    if not u:
        return None
    # 仅重量单位参与折算（保持与 converter UNIT_TO_KG 的严格语义一致）
    weight_units = set(_kg_converter.UNIT_TO_KG.keys())
    # 兼容部分写法（如 Kg 大小写已在 converter 内部处理，但这里按 lower 判断）
    if u not in weight_units and u.lower() not in [k.lower() for k in weight_units]:
        # 尝试 lower 匹配（如 KG / Kg）
        low = u.lower()
        matched = None
        for k in weight_units:
            if k.lower() == low:
                matched = k
                break
        if matched is None:
            return None
        u = matched
    try:
        kg_qty, _ = _kg_converter.convert_weight_to_standard_kg(float(current_stock or 0), u)
        return round(kg_qty, 4)
    except Exception:
        return None


def _compute_vs_avg_and_anomaly(sku_id, threshold_pct=None):
    """计算 vs_avg 涨幅与是否异动。返回 (vs_avg_pct, is_anomaly, avg_30d)。

    口径统一走 services.price_anomaly（U-02），阈值取 models.PRICE_ANOMALY_THRESHOLD_PCT。
    """
    from app.services.price_anomaly import compute_vs_avg_and_anomaly
    vs, is_anomaly, avg_30d, _latest = compute_vs_avg_and_anomaly(sku_id, threshold_pct)
    return vs, is_anomaly, avg_30d


@router.get("/api/inventory")
def inventory_list(request: Request, q: str = "", category: str = "",
                   stock: str = "all", price: str = "all",
                   include_inactive: int = 0):
    # 只读列表对店员开放（收据页 SKU/单位数据源需要）；入库/调整等写操作仍按各自权限
    require_role("staff")(request)
    skus = db.list_skus(include_inactive=bool(include_inactive))
    out = []
    low_count = 0
    anomaly_count = 0
    for s in skus:
        if q:
            q_clean = q.strip().lower()
            name_clean = (s.name or "").lower()
            if q_clean not in name_clean and name_clean not in q_clean and not any(ch in name_clean for ch in q_clean if len(ch.strip()) > 0):
                continue
        if category and category != s.category:
            continue
        is_low = s.min_stock_alert > 0 and s.current_stock <= s.min_stock_alert
        if stock == "low" and not is_low:
            continue
        vs_avg_pct, price_anomaly, _ = _compute_vs_avg_and_anomaly(s.id)
        if price == "anomaly" and not price_anomaly:
            continue
        if is_low:
            low_count += 1
        if price_anomaly:
            anomaly_count += 1
        kg_val = _standard_kg(s.current_stock, s.base_unit)
        out.append({
            "id": s.id, "name": s.name, "category": s.category or "",
            "base_unit": s.base_unit or "", "current_stock": s.current_stock,
            "standard_kg": kg_val,
            "min_stock_alert": s.min_stock_alert, "last_unit_price": s.last_unit_price,
            "price_anomaly": price_anomaly, "vs_avg_pct": vs_avg_pct,
            "is_low_stock": is_low, "active": s.active, "sku_code": s.sku_code or "",
        })
    return {
        "status": "success",
        "data": out,
        "meta": {"total_active": len(out), "low_stock_count": low_count,
                 "price_anomaly_count": anomaly_count},
    }


def _is_anomaly(sku):
    """价格异动判定：较 30 日均价涨幅 >10% 即标红（PRD 阈值 10%，价格下跌不标红）。"""
    _, is_anomaly, _ = _compute_vs_avg_and_anomaly(sku.id)
    return is_anomaly


class SkuCreateBody(BaseModel):
    name: str
    category: str = ""
    base_unit: str = ""
    min_stock_alert: float = 0.0


@router.post("/api/inventory/skus")
def create_sku(body: SkuCreateBody, request: Request):
    require_role("staff")(request)
    # B-P0-1: 创建前强制归一，阻止 _\d{10} 流水号污染 SKU 库导致库存爆炸
    canonical = _canonical_name(body.name)
    # 若归一后与存量 canonical 重名，视为冲突（幂等去重）
    existing = db.find_sku_by_name(canonical)
    if existing:
        # 若原始名与归一后不同，说明是流水号变体，直接返回已存在的主 SKU
        if canonical != body.name.strip():
            return {"status": "error", "code": "SKU_NAME_CONFLICT", "message": "同名 SKU 已存在（归一后冲突）", "canonical_name": canonical, "existing_id": existing.id}
    sku_id, err = db.create_sku(canonical, body.category, body.base_unit,
                                body.min_stock_alert)
    if err:
        return {"status": "error", "code": err, "message": "同名 SKU 已存在"}
    return {"status": "success", "id": sku_id, "canonical_name": canonical}


class SkuPatchBody(BaseModel):
    name: str = None
    category: str = None
    base_unit: str = None
    min_stock_alert: float = None
    active: int = None


@router.patch("/api/inventory/skus/{sku_id}")
def patch_sku(sku_id: int, body: SkuPatchBody, request: Request):
    require_role("owner")(request)
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "name" in fields and fields["name"]:
        # B-P0-1: 重命名时同样归一，防止通过改名注入流水号
        fields["name"] = _canonical_name(fields["name"])
    row, err = db.update_sku(sku_id, **fields)
    if err == "SKU_NAME_CONFLICT":
        return {"status": "error", "code": "SKU_NAME_CONFLICT", "message": "同名 SKU 已存在"}
    if err == "UNIT_CHANGE_BLOCKED":
        return {"status": "error", "code": "UNIT_CHANGE_BLOCKED", "message": "有库存不可改单位"}
    if row is None:
        return {"status": "error", "msg": "SKU 不存在"}
    return {"status": "success"}


@router.delete("/api/inventory/skus/{sku_id}")
def delete_sku_endpoint(sku_id: int, request: Request):
    """删除或停用 SKU：无流水则彻底删除，有流水则安全停用。"""
    require_role("owner")(request)
    ok, action = db.delete_sku(sku_id)
    if not ok:
        return {"status": "error", "code": "NOT_FOUND", "msg": "SKU 不存在"}
    msg = "SKU 已安全停用（因存在历史进货流水，保留历史记录）" if action == "DEACTIVATED" else "SKU 已彻底删除"
    return {"status": "success", "action": action, "msg": msg}


class SkuMergeBody(BaseModel):
    primary_sku_id: int
    secondary_sku_ids: list[int]
    sync_vendor_memory: bool = True


@router.post("/api/inventory/skus/merge")
def merge_skus_endpoint(body: SkuMergeBody, request: Request):
    """合并 SKU：迁移历史流水至主 SKU，停用副 SKU，并自动反哺供应商别名记忆。"""
    require_role("owner")(request)
    result, err = db.merge_skus(body.primary_sku_id, body.secondary_sku_ids)
    if err:
        return {"status": "error", "code": err, "msg": f"合并失败：{err}"}

    # 反哺 VendorMemory 记忆库（实现同义别名自学习）
    if body.sync_vendor_memory and result:
        from app.services.rag import ingest_memory
        primary_name = result.get("primary_name", "")
        merged_names = result.get("merged_names", [])
        if primary_name and merged_names:
            alias_note = f"SKU同义合并映射：[{', '.join(merged_names)}] 统一映射为标准品类 [{primary_name}]"
            ingest_memory("全局品类库", alias_note, notes=f"SKU别名合并学习：{primary_name}")

    return {"status": "success", "data": result, "msg": "SKU 合并成功并已沉淀别名记忆"}


class StocktakeBody(BaseModel):
    actual_qty: float
    note: str = ""


@router.post("/api/inventory/{sku_id}/stocktake")
def stocktake(sku_id: int, body: StocktakeBody, request: Request):
    require_role("owner")(request)
    db.stocktake_sku(sku_id, body.actual_qty, body.note)
    return {"status": "success", "msg": "盘点已记录"}


class ConsumeBody(BaseModel):
    quantity: float
    notes: str = ""


@router.post("/api/inventory/{sku_id}/consume")
def consume(sku_id: int, body: ConsumeBody, request: Request):
    require_role("owner")(request)
    sku = db.get_sku(sku_id)
    if sku is None:
        return {"status": "error", "msg": "SKU 不存在"}
    db.apply_stock_log(sku_id=sku.id, name=sku.name, qty=body.quantity,
                       unit=sku.base_unit, amount=0, vendor="", date="",
                       receipt_id=None, kind="consume", note=body.notes)
    return {"status": "success", "msg": "已消耗"}


@router.post("/api/inventory/{sku_id}/waste")
def waste(sku_id: int, body: ConsumeBody, request: Request):
    require_role("owner")(request)
    sku = db.get_sku(sku_id)
    if sku is None:
        return {"status": "error", "msg": "SKU 不存在"}
    db.apply_stock_log(sku_id=sku.id, name=sku.name, qty=body.quantity,
                       unit=sku.base_unit, amount=0, vendor="", date="",
                       receipt_id=None, kind="waste", note=body.notes)
    return {"status": "success", "msg": "已损耗"}


@router.get("/api/price_history/{sku_id}")
def price_history(sku_id: int, request: Request):
    require_role("owner")(request)
    sku = db.get_sku(sku_id)
    if sku is None:
        return {"status": "error", "msg": "SKU 不存在"}
    rows = db.price_history(sku_id)
    data = [{"date": r.date, "unit_price": r.unit_price, "qty": r.qty,
             "supplier_name": r.vendor, "receipt_id": r.receipt_id,
             "source": "receipt"} for r in rows]
    prices = [d["unit_price"] for d in data if d["unit_price"] > 0]
    vs_avg_pct, is_anomaly, avg_30d = _compute_vs_avg_and_anomaly(sku_id)
    # 兼容空序列：avg_30d 由 helper 已算出；但 vs=0 时不标红
    if not prices:
        vs_avg_pct = 0.0
        is_anomaly = False
        avg_30d = 0.0
    summary = {
        "count": len(data),
        "latest_price": prices[-1] if prices else 0.0,
        "avg_30d": avg_30d if prices else 0.0,
        "min_price": min(prices) if prices else 0.0,
        "max_price": max(prices) if prices else 0.0,
        "vs_avg_pct": vs_avg_pct, "is_anomaly": is_anomaly, "threshold_pct": 10.0,
    }
    return {"status": "success", "sku_name": sku.name, "sku_code": sku.sku_code,
            "base_unit": sku.base_unit, "data": data, "summary": summary}
