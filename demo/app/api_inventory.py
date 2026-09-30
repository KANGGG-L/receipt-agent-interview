# -*- coding: utf-8 -*-
"""库存端点：SKU 列表/CRUD/盘点/消耗/损耗/价格历史。"""

import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

from app import db
from app.auth import require_role, resolve_tenant
from app.services.costing_service import CostingService

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


def _tenant_id(request: Request) -> str:
    """Gap E2 租户键：委托 app.auth.resolve_tenant（生产态由身份派生，开发态读 X-Tenant-Id）。"""
    return resolve_tenant(request)

# 单位换算走 services.unit_factors 单一事实源（SSOT）：与成本层同源同系数。
from app.services import unit_factors


def _standard_kg(current_stock, base_unit):
    """严格仅对重量单位折算 kg；计件 / 体积 / 空单位返回 None（绝不假装折算）。

    折算系数取自 unit_factors（与成本层同源）。「斤」按本表唯一定义 0.5 kg 折算；
    港式司马斤请使用显式单位「司马斤 / 港斤」（0.6048）。
    """
    u = (base_unit or "").strip()
    if not u:
        return None
    factor = unit_factors.unit_to_kg_factor(u)
    if factor is None:
        return None
    return round(float(current_stock or 0) * factor, 4)


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
    skus = db.list_skus(include_inactive=bool(include_inactive),
                        tenant_id=_tenant_id(request))
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
    tenant_id = _tenant_id(request)
    # B-P0-1: 创建前强制归一，阻止 _\d{10} 流水号污染 SKU 库导致库存爆炸
    canonical = _canonical_name(body.name)
    # 若归一后与存量 canonical 重名，视为冲突（幂等去重）
    existing = db.find_sku_by_name(canonical, tenant_id=tenant_id)
    if existing:
        # 若原始名与归一后不同，说明是流水号变体，直接返回已存在的主 SKU
        if canonical != body.name.strip():
            return {"status": "error", "code": "SKU_NAME_CONFLICT", "message": "同名 SKU 已存在（归一后冲突）", "canonical_name": canonical, "existing_id": existing.id}
    sku_id, err = db.create_sku(canonical, body.category, body.base_unit,
                                body.min_stock_alert, tenant_id=tenant_id)
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
    if db.get_sku(sku_id, tenant_id=_tenant_id(request)) is None:
        return {"status": "error", "msg": "SKU 不存在"}
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "name" in fields and fields["name"]:
        # B-P0-1: 重命名时同样归一，防止通过改名注入流水号
        fields["name"] = _canonical_name(fields["name"])
    # P2-1: 重命名重名检查按租户 scope（上方已校验 SKU 归属本租户，口径一致）
    row, err = db.update_sku(sku_id, tenant_id=_tenant_id(request), **fields)
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
    if db.get_sku(sku_id, tenant_id=_tenant_id(request)) is None:
        return {"status": "error", "code": "NOT_FOUND", "msg": "SKU 不存在"}
    ok, action = db.delete_sku(sku_id, tenant_id=_tenant_id(request))
    if not ok:
        return {"status": "error", "code": "NOT_FOUND", "msg": "SKU 不存在"}
    msg = "SKU 已安全停用（因存在历史进货流水，保留历史记录）" if action == "DEACTIVATED" else "SKU 已彻底删除"
    return {"status": "success", "action": action, "msg": msg}


class SkuMergeBody(BaseModel):
    primary_sku_id: int
    secondary_sku_ids: list[int]


@router.post("/api/inventory/skus/merge")
def merge_skus_endpoint(body: SkuMergeBody, request: Request):
    """合并 SKU：迁移历史流水至主 SKU，停用副 SKU，并把供应商别名改指到主 SKU。

    M8 处置（K4，结论＝判死删除）：本端点原先在合并后额外调一次
    `ingest_memory("全局品类库", "SKU同义合并映射：…")` 反哺 VendorMemory。该写入
    **永远不可召回**且**无任何消费方**，故删除。三条证据（均可复算）：

    1. 召回门（前端不可达）：检索侧 `rag._vendor_related` 用「供应商名」判定相关
       性，对任何真实供应商名都返回 False —— 实测
       `_vendor_related('德利行 Tak Lee Hong', '全局品类库') = False`（只有查询名
       自身含"全局"才放行）。因此精确通道与 fuzzy 通道都取不到这条记忆。
    2. 无消费方（即便可达也不产生价值）：全仓读 `vendor_memory` 的只有
       `rag.retrieve_context` 与 `db.list_vendor_memory`；而 SKU 匹配链路
       （`_match_sku` → `find_vendor_sku_alias` / `find_sku_by_name` /
       `list_skus`）**从不读 vendor_memory**。所以就算把它接进识别 prompt，也不会
       改变任何一次 SKU 匹配结果。
    3. 租户错位：该调用未传 `tenant_id`（恒落 default 桶），与读侧自 H1 起「租户
       必填、读写同口径」相矛盾 —— 在一个租户维度的端点上，写 default 本身就是错的。

    接替者（谁承担了这件事）：合并结果由 `vendor_sku_alias` 表（P2-T2）承担 ——
    `db.merge_skus` 内部调 `db._repoint_vendor_sku_alias`（db.py:2305）把原先指向
    副 SKU 的别名全部改指到主 SKU，消费方是 `db.find_vendor_sku_alias`（`_match_sku`
    的第一优先通道，命中即累加 hit_count）。第二个合并入口（启动自愈的
    `deduplicate_skus_by_canonical`，db.py:2617）走同一钩子，两处口径一致。

    `sync_vendor_memory` 形参随之移除：它**只**控制上面那条死写入（别名改指在
    `db.merge_skus` 内部无条件执行，不受它控制），保留会变成"定义了没接线"的空开关。
    旧前端缓存仍可能发送该字段，Pydantic 默认忽略多余字段，不会 422。
    """
    require_role("owner")(request)
    tenant_id = _tenant_id(request)
    primary_sku = db.get_sku(body.primary_sku_id, tenant_id=tenant_id)
    if primary_sku is None:
        return {"status": "error", "code": "PRIMARY_NOT_FOUND", "msg": "主 SKU 不存在"}
    for sid in body.secondary_sku_ids:
        if db.get_sku(sid, tenant_id=tenant_id) is None:
            return {"status": "error", "code": "SECONDARY_NOT_FOUND",
                    "msg": f"副 SKU #{sid} 不存在"}
    result, err = db.merge_skus(body.primary_sku_id, body.secondary_sku_ids,
                                tenant_id=tenant_id)
    if err:
        return {"status": "error", "code": err, "msg": f"合并失败：{err}"}

    # 别名沉淀无需在此处补写：db.merge_skus 已通过 _repoint_vendor_sku_alias 把
    # 指向副 SKU 的别名改指到主 SKU（见 db.py:2305，别名表的唯一写入实现）。
    return {"status": "success", "data": result,
            "msg": "SKU 合并成功（历史流水与供应商别名已归集到主 SKU）"}


class StocktakeBody(BaseModel):
    actual_qty: float
    note: str = ""


@router.post("/api/inventory/{sku_id}/stocktake")
def stocktake(sku_id: int, body: StocktakeBody, request: Request):
    require_role("owner")(request)
    if body.actual_qty < 0:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "code": "INVALID_STOCKTAKE_QUANTITY",
                "msg": "盘点实际库存不能为负数",
            }
        )
    tenant_id = _tenant_id(request)
    session = db.get_session()
    try:
        sku = session.get(db._SkuRow, int(sku_id))
        if not sku or not db._tenant_ok(sku, tenant_id):
            return JSONResponse(status_code=404, content={"status": "error", "msg": "SKU 不存在"})

        diff = round(body.actual_qty - sku.current_stock, 4)
        now_date = db.now_iso()[:10]
        cost = 0.0

        if diff < 0:
            # 盘亏：走 FIFO 核销批次池
            cost, _ = CostingService.deduct_consumption_fifo(
                session=session,
                sku_id=sku.id,
                qty_needed=abs(diff),
                unit_needed=sku.base_unit,
                date=now_date,
            )
        elif diff > 0:
            # 盘盈：生成盘盈调整批次
            unit_cost = sku.last_unit_price if (sku.last_unit_price and sku.last_unit_price > 0) else 0.0
            if unit_cost == 0.0:
                last_b = session.query(db._InventoryBatchRow).filter(
                    db._InventoryBatchRow.sku_id == sku.id,
                    db._InventoryBatchRow.unit_cost > 0
                ).order_by(db._InventoryBatchRow.id.desc()).first()
                if last_b:
                    unit_cost = last_b.unit_cost
            CostingService.record_inbound_batch(
                session=session,
                sku_id=sku.id,
                qty=diff,
                unit_price=unit_cost,
                unit=sku.base_unit,
                date=now_date,
                receipt_id=None,
            )
            cost = round(diff * unit_cost, 2)

        stock_log = db._StockLogRow(
            sku_id=sku.id,
            name=sku.name,
            qty=diff,
            unit=sku.base_unit,
            amount=round(cost, 2),
            vendor="",
            date=now_date,
            receipt_id=None,
            kind="stocktake",
            note=body.note or ("盘点盘盈入库调整" if diff > 0 else "盘点盘亏核销"),
            created_at=db.now_iso(),
            tenant_id=tenant_id,
        )
        session.add(stock_log)
        sku.current_stock = round(body.actual_qty, 4)
        session.commit()
        return {
            "status": "success",
            "msg": "盘点已记录",
            "current_stock": sku.current_stock,
            "data": {"current_stock": sku.current_stock},
        }
    finally:
        session.close()


class ConsumeBody(BaseModel):
    quantity: float
    notes: str = ""


@router.post("/api/inventory/{sku_id}/consume")
def consume(sku_id: int, body: ConsumeBody, request: Request):
    require_role("owner")(request)
    if body.quantity <= 0:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "code": "INVALID_CONSUMPTION_QUANTITY",
                "msg": "消耗数量必须大于 0，不可输入负数或零",
            }
        )
    tenant_id = _tenant_id(request)
    session = db.get_session()
    try:
        sku = session.get(db._SkuRow, int(sku_id))
        if not sku or not db._tenant_ok(sku, tenant_id):
            return JSONResponse(status_code=404, content={"status": "error", "msg": "SKU 不存在"})

        now_date = db.now_iso()[:10]
        cost, details = CostingService.deduct_consumption_fifo(
            session=session,
            sku_id=sku.id,
            qty_needed=body.quantity,
            unit_needed=sku.base_unit,
            date=now_date,
        )
        sku.current_stock = round(sku.current_stock - body.quantity, 4)
        stock_log = db._StockLogRow(
            sku_id=sku.id,
            name=sku.name,
            qty=body.quantity,
            unit=sku.base_unit,
            amount=round(cost, 2),
            vendor="",
            date=now_date,
            receipt_id=None,
            kind="consume",
            note=body.notes or "手动消耗",
            created_at=db.now_iso(),
            tenant_id=tenant_id,
        )
        session.add(stock_log)
        session.commit()
        return {
            "status": "success",
            "msg": "已消耗",
            "cost": round(cost, 2),
            "current_stock": sku.current_stock,
            "data": {"current_stock": sku.current_stock, "cost": round(cost, 2)},
        }
    finally:
        session.close()


@router.post("/api/inventory/{sku_id}/waste")
def waste(sku_id: int, body: ConsumeBody, request: Request):
    require_role("owner")(request)
    if body.quantity <= 0:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "code": "INVALID_CONSUMPTION_QUANTITY",
                "msg": "报损数量必须大于 0，不可输入负数或零",
            }
        )
    tenant_id = _tenant_id(request)
    session = db.get_session()
    try:
        sku = session.get(db._SkuRow, int(sku_id))
        if not sku or not db._tenant_ok(sku, tenant_id):
            return JSONResponse(status_code=404, content={"status": "error", "msg": "SKU 不存在"})

        now_date = db.now_iso()[:10]
        cost, details = CostingService.deduct_consumption_fifo(
            session=session,
            sku_id=sku.id,
            qty_needed=body.quantity,
            unit_needed=sku.base_unit,
            date=now_date,
        )
        sku.current_stock = round(sku.current_stock - body.quantity, 4)
        stock_log = db._StockLogRow(
            sku_id=sku.id,
            name=sku.name,
            qty=body.quantity,
            unit=sku.base_unit,
            amount=round(cost, 2),
            vendor="",
            date=now_date,
            receipt_id=None,
            kind="waste",
            note=body.notes or "损耗",
            created_at=db.now_iso(),
            tenant_id=tenant_id,
        )
        session.add(stock_log)
        session.commit()
        return {
            "status": "success",
            "msg": "已损耗",
            "cost": round(cost, 2),
            "current_stock": sku.current_stock,
            "data": {"current_stock": sku.current_stock, "cost": round(cost, 2)},
        }
    finally:
        session.close()


@router.get("/api/price_history/{sku_id}")
def price_history(sku_id: int, request: Request):
    require_role("owner")(request)
    tenant_id = _tenant_id(request)
    sku = db.get_sku(sku_id, tenant_id=tenant_id)
    if sku is None:
        return {"status": "error", "msg": "SKU 不存在"}
    rows = db.price_history(sku_id, tenant_id=tenant_id)
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
