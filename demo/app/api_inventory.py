# -*- coding: utf-8 -*-
"""库存端点：SKU 列表/CRUD/盘点/消耗/损耗/价格历史。"""

from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Optional

from app import db
from app.auth import require_role

router = APIRouter()


@router.get("/api/inventory")
def inventory_list(request: Request, q: str = "", category: str = "",
                   stock: str = "all", price: str = "all",
                   include_inactive: int = 0):
    require_role("owner")(request)
    skus = db.list_skus(include_inactive=bool(include_inactive))
    out = []
    low_count = 0
    anomaly_count = 0
    for s in skus:
        if q and q not in s.name:
            continue
        if category and category != s.category:
            continue
        is_low = s.min_stock_alert > 0 and s.current_stock <= s.min_stock_alert
        if stock == "low" and not is_low:
            continue
        price_anomaly = _is_anomaly(s)
        if price == "anomaly" and not price_anomaly:
            continue
        if is_low:
            low_count += 1
        if price_anomaly:
            anomaly_count += 1
        out.append({
            "id": s.id, "name": s.name, "category": s.category or "",
            "base_unit": s.base_unit or "", "current_stock": s.current_stock,
            "min_stock_alert": s.min_stock_alert, "last_unit_price": s.last_unit_price,
            "price_anomaly": price_anomaly, "vs_avg_pct": 0.0,
            "is_low_stock": is_low, "active": s.active, "sku_code": s.sku_code or "",
        })
    return {
        "status": "success",
        "data": out,
        "meta": {"total_active": len(out), "low_stock_count": low_count,
                 "price_anomaly_count": anomaly_count},
    }


def _is_anomaly(sku):
    """价格异动判定：与 30 天均价偏离 >15%。（简化：用最近一次入库价 vs 均价）"""
    rows = db.price_history(sku.id)
    prices = [r.unit_price for r in rows if r.unit_price > 0]
    if not prices or len(prices) < 2:
        return False
    avg = sum(prices) / len(prices)
    last = prices[-1]
    return abs(last - avg) / avg > 0.15


class SkuCreateBody(BaseModel):
    name: str
    category: str = ""
    base_unit: str = ""
    min_stock_alert: float = 0.0


@router.post("/api/inventory/skus")
def create_sku(body: SkuCreateBody, request: Request):
    require_role("staff")(request)
    sku_id, err = db.create_sku(body.name, body.category, body.base_unit,
                                body.min_stock_alert)
    if err:
        return {"status": "error", "code": err, "message": "同名 SKU 已存在"}
    return {"status": "success", "id": sku_id}


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
    summary = {
        "count": len(data),
        "latest_price": prices[-1] if prices else 0.0,
        "avg_30d": round(sum(prices) / len(prices), 2) if prices else 0.0,
        "min_price": min(prices) if prices else 0.0,
        "max_price": max(prices) if prices else 0.0,
        "vs_avg_pct": 0.0, "is_anomaly": False, "threshold_pct": 15.0,
    }
    return {"status": "success", "sku_name": sku.name, "sku_code": sku.sku_code,
            "base_unit": sku.base_unit, "data": data, "summary": summary}
