# -*- coding: utf-8 -*-
"""记忆治理端点（T8 Gap D1：规则级记忆写入人工闸 + P1-T1 删除权闭环）。

- GET    /api/memory/pending               列表（缺省只列 status='pending' 的人审队列；
                                           要 audit 的 rejected 行须显式 ?status=rejected，admin）
- POST   /api/memory/pending/{id}/approve  批准：经既有 upsert 链路落
                                           vendor_memory + Chroma（治理元数据齐全）
- POST   /api/memory/pending/{id}/reject   拒绝：不入任何记忆库
- DELETE /api/memory/{memory_id}           删除单条记忆（DB 行 + 向量级联，admin）
- DELETE /api/memory/vendor/{vendor_name}  按供应商批量删除该租户全部记忆（admin）
- POST   /api/memory/{memory_id}/revive    恢复已归档记忆（P1-T2，admin）

流转仅 pending → approved/rejected；已审过的再操作返回 409。
删除是硬删（不可逆），跨租户或目标不存在一律 404（不区分"没权限看"与"不存在"，
避免用 404/403 的差异探测他租户的 memory_id 是否存在）。
归档恢复是删除的反面：归档只改 status、向量仍在，故无需级联动作；恢复同样按租户
过滤，跨租户/不存在一律 404。
"""

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app import db
from app.auth import require_admin, resolve_tenant
from app.services import memory_store
from app.services.memory_store import MemoryVectorDeleteError

logger = logging.getLogger(__name__)

router = APIRouter()


def _tenant_id(request: Request) -> str:
    """Gap E2 租户键：委托 app.auth.resolve_tenant（生产态由身份派生，开发态读 X-Tenant-Id）。"""
    return resolve_tenant(request)


def _actor(request: Request) -> str:
    """操作者标识（审计用）：取已解析身份里的 email，匿名兜底 admin。"""
    account = getattr(request.state, "account", {})
    return account.get("email", "admin") if isinstance(account, dict) else "admin"


def _safe_audit(who, action, field, old, new):
    """写系统审计日志；失败只告警，不影响已完成的业务动作。"""
    try:
        db.append_system_audit_log(who, action, field, old, new)
    except Exception as e:
        logger.warning("审计写入失败(action=%s field=%s who=%s): %s",
                       action, field, who, e)


def _attach_existing_fact_values(rows, tenant_id):
    """为带 fact_key 的待审行附「当前生效值」，供审核界面渲染字段级 diff 的旧值侧。

    审核者要判断"这条候选到底改了什么"，必须同时看到候选值与该键**当前**生效的值；
    只看候选（乃至整段 content）无法区分"新增"与"覆盖既有"。旧值取自同
    (vendor, supplier_id) 下 active 记忆里同 fact_key 的那条。

    why 复用 db.list_vendor_memory 而不是在端点里另写查询：读取口径（active + 同键
    去重 + 锚点/名字收窄）只有 db 一份实现，端点自拼 SQL 等于给这份口径开第二份实现
    （本仓高发）。why 不改 db.list_pending_memory 的返回体：那会让所有既有消费方共担
    一次跨表读取的成本，而只有审核界面需要旧值 —— 投射放在取数最外层。

    取不到（该键尚无生效行 / 无 vendor）时置 None：界面显示「新增」而不是空串，
    避免把"没有旧值"渲染成"旧值为空"。取数失败只告警，绝不让列表整体失败。
    """
    cache = {}
    for row in rows:
        row["existing_fact_value"] = None
        key = str(row.get("fact_key") or "").strip()
        vendor = str(row.get("vendor") or "").strip()
        if not key or not vendor:
            continue
        sig = (vendor, row.get("supplier_id"))
        if sig not in cache:
            try:
                existing = db.list_vendor_memory(
                    vendor=vendor, tenant_id=tenant_id,
                    supplier_id=row.get("supplier_id"))
            except Exception as e:
                logger.warning(
                    "[memory] 旧值投射取数失败（不影响列表）：vendor=%s %s: %s",
                    vendor, type(e).__name__, e)
                existing = []
            by_key = {}
            for m in existing:
                # 只认同名 vendor：list_vendor_memory 会并入"名字相关"的行，拿别的
                # 供应商的同名 key 当旧值会把 diff 指向错误的对象。
                if str(m.get("vendor") or "") != vendor:
                    continue
                ek = str(m.get("fact_key") or "").strip()
                if ek and ek not in by_key:
                    by_key[ek] = m.get("fact_value")
            cache[sig] = by_key
        row["existing_fact_value"] = cache[sig].get(key)


@router.get("/api/memory/pending")
def list_pending(request: Request, status: str = ""):
    """待确认记忆列表（id 倒序）。status 可选：pending|approved|rejected。

    AH3 契约：缺省（不传 / 空串）**只列 `status='pending'`** —— Q2 新增的
    `status='rejected'` 落选审计行不得混进这个列表，否则前端角标会把"被闸门拒绝的行"
    当成"等人审的行"（角标虚高、审核者看到永远批不掉的条目）。要看审计行必须显式
    `?status=rejected`。

    这里**显式**传 'pending' 而不依赖 `db.list_pending_memory` 的缺省值：本端点是角标
    与待审界面的取数面，写死口径后即便将来 db 层缺省口径变化，这层隔离也不会被静默撤销。
    """
    require_admin(request)
    tenant_id = _tenant_id(request)
    rows = db.list_pending_memory(status=status or "pending", tenant_id=tenant_id)
    # W-10：为结构化候选附上旧值侧（同 vendor+fact_key 的当前 active 值），供审核界面
    # 渲染字段级 diff。只读投射，不改 db.list_pending_memory 的返回体与既有流程。
    _attach_existing_fact_values(rows, tenant_id)
    return {"status": "success", "data": rows}


@router.post("/api/memory/pending/{pending_id}/approve")
def approve_pending(pending_id: int, request: Request):
    """批准待确认记忆：按待审行自身的来源与载荷落 vendor_memory + Chroma。

    待审行带字段级载荷（fact_kind/fact_key/fact_value）时走结构化写入（supersede
    归并 + 五列真实落库）；不带（旧反馈蒸馏行）时保持自由文本路径。
    U1 审计链：两条路径都带上本次操作者（written_by=who），使落库行能回答"这条是谁
    批准写入的"。
    """
    require_admin(request)
    tenant_id = _tenant_id(request)
    row = db.get_pending_memory(pending_id, tenant_id=tenant_id)
    if row is None:
        return JSONResponse(content={"status": "error", "msg": "未找到该待确认记忆"},
                            status_code=404)
    if row["status"] != "pending":
        return JSONResponse(
            content={"status": "error", "code": "ALREADY_REVIEWED",
                     "msg": f"该记忆已审核（当前状态：{row['status']}），不可重复操作"},
            status_code=409)
    who = _actor(request)
    # D8：provenance 载体解析失败不得静默清空。refs 会经 approve 落成
    # vendor_memory.source_ref —— 那是"这条记忆由哪几张单据得来"的唯一回溯载体，
    # 静默清空等于把损坏改写成"无来源"，事后无从追溯。降级行为本身不变（仍按空
    # 列表继续 approve，**不因 provenance 损坏而阻断批准**），但必须留 warning：
    # 记下行 id 与被截断的原始文本，使损坏可被事后排查。
    raw_refs = row.get("source_receipt_ids_json")
    refs_text = str(raw_refs)[:200] if raw_refs is not None else ""
    try:
        refs = json.loads(raw_refs or "[]")
        if not isinstance(refs, list):
            # 合法 JSON 但不是数组（如 `{"a":1}` / `"777"`）同属 provenance 丢失，
            # 与解析异常是同一根因的两个分支，一并留痕。
            logger.warning(
                "[memory] pending #%s 的 provenance 载体不是 JSON 数组，降级为无来源：%s",
                pending_id, refs_text)
            refs = []
    except Exception as e:
        logger.warning(
            "[memory] pending #%s 的 provenance 载体解析失败，降级为无来源"
            "（不阻断批准）：%s (%s)", pending_id, refs_text, e)
        refs = []
    from app.services.rag import approve_pending_to_memory
    try:
        # P3fix-a 审核 M2：把待审行自己的来源与字段级载荷透传给落库链路。
        # 不透传的后果不是"功能失效"而是"数据被编"：抽取候选会被记成用户反馈蒸馏
        # （source_kind 是防投毒判定的输入），且结构化五列永远 NULL（上一波接好的
        # 结构化链在最后一步丢干净）。行字典已由 _pending_row_to_dict 透出这五个键。
        approve_pending_to_memory(
            row["vendor"], row["content"], tenant_id=row["tenant_id"],
            source_receipt_ids=refs,
            source_kind=row.get("source_kind"),
            supplier_id=row.get("supplier_id"),
            fact_kind=row.get("fact_kind"),
            fact_key=row.get("fact_key"),
            fact_value=row.get("fact_value"),
            # U1 审计链：who 在本函数里本来已经取到（:81），此前只用于状态流转与系统
            # 审计日志，落库的记忆行上却没有写入者 —— 记忆治理/合规要回答"这条是谁批的"
            # 时无从追溯（谁能写、依据什么）。此处透传，由 rag 两条落库分支写进
            # vendor_memory.written_by。
            written_by=who)
    except Exception as e:
        logger.warning("[memory] approve ingest failed id=%s: %s", pending_id, e)
        return JSONResponse(
            content={"status": "error", "msg": f"记忆落库失败：{e}"},
            status_code=500)
    ok = db.review_pending_memory(pending_id, "approved", who, tenant_id=tenant_id)
    if not ok:
        return JSONResponse(content={"status": "error", "msg": "状态流转失败，请重试"},
                            status_code=409)
    _safe_audit(who, "pending_memory_approve", f"pending_memory:{pending_id}",
                "pending", "approved")
    return {"status": "success", "data": db.get_pending_memory(pending_id,
                                                               tenant_id=tenant_id)}


@router.post("/api/memory/pending/{pending_id}/reject")
def reject_pending(pending_id: int, request: Request):
    """拒绝待确认记忆：仅流转状态，不写任何记忆库。"""
    require_admin(request)
    tenant_id = _tenant_id(request)
    row = db.get_pending_memory(pending_id, tenant_id=tenant_id)
    if row is None:
        return JSONResponse(content={"status": "error", "msg": "未找到该待确认记忆"},
                            status_code=404)
    if row["status"] != "pending":
        return JSONResponse(
            content={"status": "error", "code": "ALREADY_REVIEWED",
                     "msg": f"该记忆已审核（当前状态：{row['status']}），不可重复操作"},
            status_code=409)
    who = _actor(request)
    ok = db.review_pending_memory(pending_id, "rejected", who, tenant_id=tenant_id)
    if not ok:
        return JSONResponse(content={"status": "error", "msg": "状态流转失败，请重试"},
                            status_code=409)
    _safe_audit(who, "pending_memory_reject", f"pending_memory:{pending_id}",
                "pending", "rejected")
    return {"status": "success", "data": db.get_pending_memory(pending_id,
                                                               tenant_id=tenant_id)}


@router.delete("/api/memory/vendor/{vendor_name}")
def delete_memory_by_vendor(vendor_name: str, request: Request):
    """P1-T1 按供应商批量删除该租户的全部记忆（含归档行），含向量级联与审计。

    生产语料里的供应商名带中文/空格/括号，前端必须对路径段做 URL 编码；
    名字里若含 "/" 则无法作为单个路径段传入（此类供应商请改用单条删除）。

    FP-2b：**候选记忆一并删**（pending 行没有 memory_id，单条删除端点不适用）——
    pending 计数与 vendor_memory 是否有行无关，故在 affected 判定之前执行；
    `affected==0 且 pending_deleted==0` 才 404（删的是"记忆+候选"整体语义）。

    FP-2c：向量级联失败 → 500 VECTOR_DELETE_FAILED。vector-first 排序下 DB 行
    尚未删除，失败态是"两侧介质完好、可重试"，因此**不写删除审计**（业务未发生），
    只留一条 vector_delete_failed 审计供排查。
    """
    require_admin(request)
    tenant_id = _tenant_id(request)
    who = _actor(request)
    try:
        result = memory_store.delete_memory(vendor=vendor_name, tenant_id=tenant_id)
    except MemoryVectorDeleteError as e:
        logger.warning("[memory] 按供应商删除失败（向量级联，未生效）vendor=%s: %s",
                       vendor_name, e)
        _safe_audit(who, "vector_delete_failed",
                    f"vendor_memory:vendor={vendor_name}", "", str(e)[:500])
        return JSONResponse(
            content={"status": "error", "code": "VECTOR_DELETE_FAILED",
                     "msg": "向量级联删除失败，本次删除未生效，可重试"},
            status_code=500)
    pending_deleted = db.delete_pending_memory(vendor=vendor_name,
                                               tenant_id=tenant_id)
    if result["affected"] == 0 and pending_deleted == 0:
        return JSONResponse(
            content={"status": "error", "msg": "该供应商在当前租户下没有可删除的记忆"},
            status_code=404)
    _safe_audit(who, "vendor_memory_delete_by_vendor",
                f"vendor_memory:vendor={vendor_name}", "",
                json.dumps({"deleted": result["affected"],
                            "pending_deleted": pending_deleted},
                           ensure_ascii=False))
    result["pending_deleted"] = pending_deleted
    return {"status": "success", "data": result}


@router.delete("/api/memory/{memory_id}")
def delete_memory_by_id(memory_id: str, request: Request):
    """P1-T1 删除单条记忆（DB 行 + 向量级联 + 系统审计）。

    跨租户/不存在都返回 404：memory_id 全局唯一，若对"别人的 memory_id"回 403、
    对"不存在"回 404，就等于给出一个探测他租户记忆是否存在的接口。
    """
    require_admin(request)
    tenant_id = _tenant_id(request)
    who = _actor(request)
    # FP-2c：向量级联失败 → 500（DB 行未删、可重试），不写删除审计（业务未发生）。
    try:
        result = memory_store.delete_memory(memory_id=memory_id, tenant_id=tenant_id)
    except MemoryVectorDeleteError as e:
        logger.warning("[memory] 单条删除失败（向量级联，未生效）memory_id=%s: %s",
                       memory_id, e)
        _safe_audit(who, "vector_delete_failed",
                    f"vendor_memory:{memory_id}", "", str(e)[:500])
        return JSONResponse(
            content={"status": "error", "code": "VECTOR_DELETE_FAILED",
                     "msg": "向量级联删除失败，本次删除未生效，可重试"},
            status_code=500)
    if result["affected"] == 0:
        return JSONResponse(content={"status": "error", "msg": "未找到该记忆"},
                            status_code=404)
    _safe_audit(who, "vendor_memory_delete", f"vendor_memory:{memory_id}", "",
                f"deleted:{result['affected']}")
    return {"status": "success", "data": result}


@router.post("/api/memory/{memory_id}/revive")
def revive_memory(memory_id: str, request: Request):
    """P1-T2 恢复已归档记忆：status 回 active + decay_score 重置为起始分（admin）。

    为什么需要这条端点：归档是单向扣分的结果（apply_vendor_memory_override_penalty
    低于阈值即归档），而检索默认只列 active —— 没有恢复入口时，一次误判或一次合理
    的业务变更就把该供应商的记忆永久踢出注入面（Gap 分析 H2/M2「无删除也无法更正」
    的另一半：只有惩罚、没有回退）。

    跨租户/不存在一律 404（与删除端点同口径）：memory_id 全局唯一，若对"别人的
    memory_id"回 403、对"不存在"回 404，就等于给出一个探测他租户记忆是否存在的接口。
    """
    require_admin(request)
    tenant_id = _tenant_id(request)
    who = _actor(request)
    result = db.revive_vendor_memory(memory_id=memory_id, tenant_id=tenant_id)
    if not result.get("found"):
        return JSONResponse(content={"status": "error", "msg": "未找到该记忆"},
                            status_code=404)
    if not result.get("revived"):
        # 本来就没归档：409 而不是 404 —— 目标明明可见，谎报"未找到"会让运维以为
        # memory_id 写错了而去查库（与 pending 端点 ALREADY_REVIEWED 同风格）。
        return JSONResponse(
            content={"status": "error", "code": "ALREADY_ACTIVE",
                     "msg": f"该记忆当前状态为 {result.get('status') or 'active'}，无需恢复"},
            status_code=409)
    _safe_audit(who, "vendor_memory_revive", f"vendor_memory:{memory_id}",
                "archived", "active")
    return {"status": "success", "data": result}
