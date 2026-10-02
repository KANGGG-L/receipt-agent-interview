# -*- coding: utf-8 -*-
"""库存端点：SKU 列表/CRUD/盘点/消耗/损耗/价格历史。"""

import logging
import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

from app import db
from app.auth import require_role, resolve_tenant
from app.services.costing_service import CostingService

logger = logging.getLogger("api_inventory")

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


def _compute_vs_avg_and_anomaly(sku_id, threshold_pct=None, tenant_id=None):
    """计算 vs_avg 涨幅与是否异动。返回 (vs_avg_pct, is_anomaly, avg_30d)。

    口径统一走 services.price_anomaly（U-02），阈值取 settings 实时读取
    （WS3：threshold 支持租户维度——租户覆盖 → 全局默认 → 缺省）。
    """
    from app.services.price_anomaly import compute_vs_avg_and_anomaly
    vs, is_anomaly, avg_30d, _latest = compute_vs_avg_and_anomaly(
        sku_id, threshold_pct, tenant_id=tenant_id)
    return vs, is_anomaly, avg_30d


def _threshold_pct(tenant_id=None):
    """本租户生效的价格异动阈值（百分数）。供 inventory meta 与走势 summary 同源复用。"""
    from app.services.price_anomaly import get_threshold_pct
    return get_threshold_pct(tenant_id)


def _serialize_active_batches(session, sku_id):
    """WS4-6：SKU 的在库批次列表（供价格走势 Modal 的「SKU 概览」展示）。

    口径与 `CostingService.current_avg_cost` 完全同源：复用
    `CostingService.get_active_batches`，即「is_closed == 0 且 remaining_qty > 0」
    的批次，按 `inbound_date ASC, id ASC`（严格 FIFO 序）返回。

    无批次时返回**空数组**（不是 None），且**不**在此处自行开 / 关 session ——
    由调用方复用同一次 session，保证 batches 与 avg_cost 落在同一快照上。
    """
    return [
        {
            "batch_id": b.id,
            "inbound_date": b.inbound_date or "",
            "unit_cost": b.unit_cost,
            "remaining_qty": b.remaining_qty,
            "unit": b.unit or "",
            "receipt_id": b.receipt_id,
        }
        for b in CostingService.get_active_batches(session, int(sku_id))
    ]


@router.get("/api/inventory")
def inventory_list(request: Request, q: str = "", category: str = "",
                   stock: str = "all", price: str = "all",
                   include_inactive: int = 0):
    # 只读列表对店员开放（收据页 SKU/单位数据源需要）；入库/调整等写操作仍按各自权限
    require_role("staff")(request)
    tenant_id = _tenant_id(request)
    skus = db.list_skus(include_inactive=bool(include_inactive),
                        tenant_id=tenant_id)
    # WS5：本租户价格异动处置映射（{sku_id: {...}}），按行回带 ack_action 供前端
    # 渲染「已忽略 / 已采纳」，并在 meta 里给出整表映射供看板雷达同源判定。
    ack_map = db.list_price_anomaly_acks(tenant_id=tenant_id)
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
        vs_avg_pct, price_anomaly, _ = _compute_vs_avg_and_anomaly(
            s.id, tenant_id=tenant_id)
        if price == "anomaly" and not price_anomaly:
            continue
        if is_low:
            low_count += 1
        if price_anomaly:
            anomaly_count += 1
        kg_val = _standard_kg(s.current_stock, s.base_unit)
        ack = ack_map.get(int(s.id)) or {}
        out.append({
            "id": s.id, "name": s.name, "category": s.category or "",
            "base_unit": s.base_unit or "", "current_stock": s.current_stock,
            "standard_kg": kg_val,
            "min_stock_alert": s.min_stock_alert, "last_unit_price": s.last_unit_price,
            "price_anomaly": price_anomaly, "vs_avg_pct": vs_avg_pct,
            "is_low_stock": is_low, "active": s.active, "sku_code": s.sku_code or "",
            # WS5：处置状态（None = 未处置；"ignore" / "adopt"）
            "ack_action": ack.get("action") or None,
        })
    return {
        "status": "success",
        "data": out,
        "meta": {"total_active": len(out), "low_stock_count": low_count,
                 "price_anomaly_count": anomaly_count,
                 # WS3：本租户生效阈值（百分数），供前端展示与走势图例/阈值线同源
                 "price_anomaly_threshold_pct": _threshold_pct(tenant_id),
                 # WS5：整表处置映射（键为 sku_id 字符串），供看板雷达同源判定忽略/采纳
                 "price_anomaly_ack_map": {str(k): v for k, v in ack_map.items()}},
    }


def _is_anomaly(sku, tenant_id=None):
    """价格异动判定：较 30 日均价涨幅 >阈值 即标红（阈值取本租户生效值，仅向上告警）。

    基准 = SKU 级（最新进价 vs 近 30 日均价），与收据行级（较上一笔进价）不同基准。
    """
    _, is_anomaly, _ = _compute_vs_avg_and_anomaly(sku.id, tenant_id=tenant_id)
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


@router.get("/api/inventory/skus/{sku_id}")
def get_inventory_sku(sku_id: int, request: Request):
    """单 SKU 详情：身份字段 + 结存 + 派生加权成本 + 价格趋势（WS4-2）。

    权限：`require_role("staff")`（Spec §4 定为 Staff/Owner —— 只读详情对店员开放）。
    租户隔离：SKU 不存在或跨租户 → 404（沿用本文件按 id 取单条端点的既有做法）。

    字段口径：
    - `standard_kg` 复用 `_standard_kg`：仅重量单位折算，计件 / 体积 / 空单位返回 None。
    - `avg_cost` 走 4-1 的派生函数 `CostingService.current_avg_cost`（批次池单一事实源）；
      该函数需 session，故此处自开 session 并确保 finally 关闭。
    - `batches`（WS4-6）返回在库批次（未关闭且 remaining_qty > 0），FIFO 序；
      与 `avg_cost` 在**同一次 session** 内取，两者同源同快照；无批次时为空数组。
    - `threshold_pct` 走 `get_threshold_pct(tenant_id)`（与 WS3 同源，不硬编码）。
    - `vs_avg_pct` / `avg_30d` / `is_anomaly` 复用 `_compute_vs_avg_and_anomaly`；
      `latest_price` 取价格历史中最新一笔有效单价（>0），无历史则 0.0。
    """
    require_role("staff")(request)
    tenant_id = _tenant_id(request)
    sku = db.get_sku(sku_id, tenant_id=tenant_id)
    if sku is None:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "code": "NOT_FOUND", "msg": "SKU 不存在"},
        )

    vs_avg_pct, is_anomaly, avg_30d = _compute_vs_avg_and_anomaly(
        sku.id, tenant_id=tenant_id)
    rows = db.price_history(sku.id, tenant_id=tenant_id)
    prices = [r.unit_price for r in rows if r.unit_price and r.unit_price > 0]
    latest_price = round(prices[-1], 2) if prices else 0.0

    session = db.get_session()
    try:
        # WS4-6：avg_cost 与 batches 在**同一次 session** 内取，保证两者同源同快照
        # （否则两次读取之间若有入库/消耗提交，加权成本与批次列表会互相打架）。
        avg_cost = CostingService.current_avg_cost(session, sku.id)
        batches = _serialize_active_batches(session, sku.id)
    finally:
        session.close()

    is_low = sku.min_stock_alert > 0 and sku.current_stock <= sku.min_stock_alert
    return {
        "status": "success",
        "data": {
            "id": sku.id,
            "name": sku.name,
            "sku_code": sku.sku_code or "",
            "category": sku.category or "",
            "base_unit": sku.base_unit or "",
            "current_stock": sku.current_stock,
            "standard_kg": _standard_kg(sku.current_stock, sku.base_unit),
            "min_stock_alert": sku.min_stock_alert,
            "last_unit_price": sku.last_unit_price,
            "active": sku.active,
            "is_low_stock": is_low,
            "avg_cost": avg_cost,
            "vs_avg_pct": vs_avg_pct,
            "avg_30d": avg_30d,
            "is_anomaly": is_anomaly,
            "latest_price": latest_price,
            "threshold_pct": _threshold_pct(tenant_id),
            "batches": batches,
        },
    }


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
    vs_avg_pct, is_anomaly, avg_30d = _compute_vs_avg_and_anomaly(
        sku_id, tenant_id=tenant_id)
    # 兼容空序列：avg_30d 由 helper 已算出；但 vs=0 时不标红
    if not prices:
        vs_avg_pct = 0.0
        is_anomaly = False
        avg_30d = 0.0
    # WS3：threshold_pct 与 is_anomaly 必须**同源同值** —— 都由本租户生效阈值派生，
    # 删除此前硬编码的 10.0。
    threshold_pct = _threshold_pct(tenant_id)
    summary = {
        "count": len(data),
        "latest_price": prices[-1] if prices else 0.0,
        "avg_30d": avg_30d if prices else 0.0,
        "min_price": min(prices) if prices else 0.0,
        "max_price": max(prices) if prices else 0.0,
        "vs_avg_pct": vs_avg_pct, "is_anomaly": is_anomaly,
        "threshold_pct": threshold_pct,
    }
    return {"status": "success", "sku_name": sku.name, "sku_code": sku.sku_code,
            "base_unit": sku.base_unit, "data": data, "summary": summary}


# -------------------------------------------------------------
# WS4：库存流水台账（Append-Only 只读）
# -------------------------------------------------------------
_VALID_LOG_KINDS = ("in", "consume", "waste", "stocktake")


@router.get("/api/inventory/logs")
def inventory_logs(request: Request, sku_id: Optional[int] = None, kind: str = "",
                   start_date: str = "", end_date: str = "", limit: int = 200):
    """库存流水台账：按 sku_id / kind / 日期区间 / limit 过滤 inventory_log。

    权限：`require_role("staff")`（只读，Spec §4 定为 Staff/Owner）。
    数据源 `db._StockLogRow`，租户过滤走 `db.scoped`（与其余接口同源租户口径），
    按 `id` 倒序（新在上）。**只读**：不提供任何写入口。

    - `kind` 仅接受 in/consume/waste/stocktake，非法值 → 400。
    - `limit` 默认 200，收敛到 [1, 1000]（越界/非法回落 200 或封顶 1000）。
    - 日期过滤按 `date`（空则回落 `created_at[:10]`），闭区间 [start_date, end_date]，
      与 api_export 的 inventory_stock_logs 报表口径一致。
    返回 `{"status":"success","data":[...],"meta":{"count":N}}`。
    """
    require_role("staff")(request)
    tenant_id = _tenant_id(request)

    kind = (kind or "").strip()
    if kind and kind not in _VALID_LOG_KINDS:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "code": "INVALID_KIND",
                     "msg": "kind 取值必须为 in/consume/waste/stocktake 之一"},
        )

    try:
        limit_val = int(limit)
    except (TypeError, ValueError):
        limit_val = 200
    if limit_val <= 0:
        limit_val = 200
    limit_val = min(limit_val, 1000)

    start = (start_date or "").strip()
    end = (end_date or "").strip()

    session = db.get_session()
    try:
        q = db.scoped(session.query(db._StockLogRow), db._StockLogRow, tenant_id)
        if sku_id is not None:
            q = q.filter(db._StockLogRow.sku_id == int(sku_id))
        if kind:
            q = q.filter(db._StockLogRow.kind == kind)
        q = q.order_by(db._StockLogRow.id.desc())
        rows = q.all()
    finally:
        session.close()

    data = []
    for r in rows:
        d = r.date or (r.created_at[:10] if r.created_at else "")
        if start and d < start:
            continue
        if end and d > end:
            continue
        data.append({
            "id": r.id, "sku_id": r.sku_id, "name": r.name,
            "qty": r.qty, "unit": r.unit, "amount": r.amount,
            "vendor": r.vendor, "date": d, "kind": r.kind,
            "note": r.note, "receipt_id": r.receipt_id,
            "created_at": r.created_at,
        })
        if len(data) >= limit_val:
            break

    return {"status": "success", "data": data, "meta": {"count": len(data)}}


# -------------------------------------------------------------
# WS3：owner 门店级价格预警阈值自助端点（读写本租户覆盖值）
# -------------------------------------------------------------
class PriceThresholdBody(BaseModel):
    threshold_pct: float


@router.get("/api/inventory/price_threshold")
def get_price_threshold(request: Request):
    """读取本租户生效阈值 + 全局默认值（供 owner 门店配置控件回填）。

    权限：require_role("owner")（admin 按 ROLE_RANK 亦放行；staff 403）。
    """
    require_role("owner")(request)
    tenant_id = _tenant_id(request)
    from app.services.price_anomaly import get_threshold_pct
    return {
        "status": "success",
        "data": {
            "threshold_pct": get_threshold_pct(tenant_id),
            "global_default_pct": get_threshold_pct(),
        },
    }


@router.put("/api/inventory/price_threshold")
def set_price_threshold(body: PriceThresholdBody, request: Request):
    """写入本租户价格预警阈值覆盖值（落 app_settings + 系统审计留痕 old→new 与租户）。

    校验：0 < threshold_pct <= 100，越界返回 400。权限：require_role("owner")。
    返回写入后的**本租户生效值**。
    """
    require_role("owner")(request)
    tenant_id = _tenant_id(request)
    value = body.threshold_pct
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not (0 < value <= 100):
        return JSONResponse(
            status_code=400,
            content={"status": "error", "code": "INVALID_THRESHOLD",
                     "msg": "阈值必须为正数且不超过 100（百分数）"},
        )
    from app.services import settings_service
    from app.services.price_anomaly import get_threshold_pct
    old = get_threshold_pct(tenant_id)
    new = settings_service.set_value(
        "price_anomaly_threshold_pct", float(value), tenant_id=tenant_id)
    account = getattr(request.state, "account", {}) or {}
    who = account.get("email", "owner") if isinstance(account, dict) else "owner"
    try:
        db.append_system_audit_log(
            who, "price_threshold_update",
            f"price_anomaly_threshold_pct (tenant={tenant_id})",
            old, new)
    except Exception as e:
        logging.getLogger("api_inventory").warning(
            "阈值审计写入失败(who=%s tenant=%s): %s", who, tenant_id, e)
    return {"status": "success",
            "data": {"threshold_pct": new,
                     "global_default_pct": get_threshold_pct()}}


# -------------------------------------------------------------
# WS5：价格异动处置闭环（ignore / adopt 的持久化 + 内部埋点）
# -------------------------------------------------------------
_VALID_ACK_ACTIONS = ("ignore", "adopt")


class PriceAnomalyAckBody(BaseModel):
    action: str


@router.post("/api/inventory/{sku_id}/price_anomaly_ack")
def ack_price_anomaly(sku_id: int, body: PriceAnomalyAckBody, request: Request):
    """对某 SKU 的价格异动作处置（ignore = 忽略，adopt = 采纳/发起跟进）。

    语义：处置状态持久化到 `price_anomaly_ack`（同一 SKU 重复处置是更新而非堆叠），
    刷新页面后仍保留；并落 `threshold_pct` / `snapshot_vs_pct` 记录**处置当时**所用
    阈值与涨幅（阈值可被门店覆盖实时改动，事后回算会变，故留痕供回溯）。

    权限：`require_role("owner")`（处置属经营决策）。
    body：`{"action": "ignore"|"adopt"}`；其它取值 → 400。
    埋点：`adopt` → `price_anomaly_adopted`；`ignore` → `price_anomaly_dismissed`，
    走既有内部埋点通道 `receipt_utils._track_internal`（失败静默，不阻塞处置）。
    """
    require_role("owner")(request)
    tenant_id = _tenant_id(request)
    action = str(body.action or "").strip().lower()
    if action not in _VALID_ACK_ACTIONS:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "code": "INVALID_ACTION",
                     "msg": "action 取值必须为 ignore 或 adopt"},
        )
    sku = db.get_sku(sku_id, tenant_id=tenant_id)
    if sku is None:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "code": "NOT_FOUND", "msg": "SKU 不存在"},
        )

    threshold_pct = _threshold_pct(tenant_id)
    vs_avg_pct, _is_anomaly_now, _avg_30d = _compute_vs_avg_and_anomaly(
        sku_id, tenant_id=tenant_id)
    row = db.upsert_price_anomaly_ack(
        sku_id, action, threshold_pct, vs_avg_pct, tenant_id=tenant_id)

    # 内部埋点：按既有机制（_track_internal）发事件；白名单别名见 parsing_logs。
    event_type = "price_anomaly_adopted" if action == "adopt" else "price_anomaly_dismissed"
    try:
        from app.services.receipt_utils import _track_internal
        _track_internal(event_type, None, {
            "sku_id": int(sku_id),
            "action": action,
            "threshold_pct": threshold_pct,
            "snapshot_vs_pct": vs_avg_pct,
        }, tenant_id=tenant_id)
    except Exception as e:  # 埋点失败不得影响处置成功
        logger.warning("价格异动处置埋点失败(sku=%s action=%s): %s", sku_id, action, e)

    return {"status": "success", "data": row,
            "msg": "已采纳并纳入跟进" if action == "adopt" else "已忽略该价格异动"}


@router.delete("/api/inventory/{sku_id}/price_anomaly_ack")
def revoke_price_anomaly_ack(sku_id: int, request: Request):
    """撤销某 SKU 的价格异动处置（回到未处置态）。权限：`require_role("owner")`。

    幂等：无处置行时返回 `cleared=0` 且仍是 200（撤销一个未处置的 SKU 不是错误）。
    """
    require_role("owner")(request)
    tenant_id = _tenant_id(request)
    if db.get_sku(sku_id, tenant_id=tenant_id) is None:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "code": "NOT_FOUND", "msg": "SKU 不存在"},
        )
    cleared = db.clear_price_anomaly_ack(sku_id, tenant_id=tenant_id)
    return {"status": "success", "data": {"cleared": cleared},
            "msg": "已撤销处置" if cleared else "该食材当前无处置记录"}
