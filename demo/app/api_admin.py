# -*- coding: utf-8 -*-
"""admin 端点：引擎/模型配置 + 灰测范围 + AI 复盘。

- /api/admin/engine-config（GET/PUT）：换识别/审核模型、切灰测模式
- /api/admin/grey-test：灰测状态可视化
- /api/review：AI 复盘（价格异动/供应商洞察，owner 可用）
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app import db
from app.auth import require_admin, require_role

router = APIRouter()


class EngineConfigBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # 常规
    recognition_engine: str = None
    recognition_model: str = None
    audit_engine: str = None
    audit_model: str = None
    audit_enabled: bool = None
    openai_rec_base_url: str = None
    openai_rec_api_key: str = None
    openai_rec_model: str = None
    openai_aud_base_url: str = None
    openai_aud_api_key: str = None
    openai_aud_model: str = None
    # 常规解析 LLM
    parse_llm_enabled: bool = None
    parse_llm_engine: str = None
    parse_llm_model: str = None
    openai_parse_base_url: str = None
    openai_parse_api_key: str = None
    openai_parse_model: str = None
    # 分组测试（灰测）
    grey_enabled: bool = None
    grey_percent: int = None
    grey_assign_mode: str = None
    grey_supplier_ids: list[int] = None
    grey_recognition_engine: str = None
    grey_recognition_model: str = None
    grey_audit_enabled: bool = None
    grey_audit_engine: str = None
    grey_audit_model: str = None
    grey_openai_rec_base_url: str = None
    grey_openai_rec_api_key: str = None
    grey_openai_rec_model: str = None
    grey_openai_aud_base_url: str = None
    grey_openai_aud_api_key: str = None
    grey_openai_aud_model: str = None
    # 灰测组解析 LLM
    grey_parse_llm_enabled: bool = None
    grey_parse_llm_engine: str = None
    grey_parse_llm_model: str = None
    grey_openai_parse_base_url: str = None
    grey_openai_parse_api_key: str = None
    grey_openai_parse_model: str = None


def _quick_test_engine(engine_type: str, model_name: str, base_url: str, api_key: str, label: str):
    """轻量快速探测：校验引擎可用性与模型/Key合法性。"""
    import subprocess
    import requests
    
    if engine_type == "openai":
        if not base_url or not base_url.strip():
            return f"[{label}] Base URL 不能为空"
        if not api_key or not api_key.strip():
            return f"[{label}] API Key 不能为空"
        if not model_name or not model_name.strip():
            return f"[{label}] 模型名不能为空"
        url = base_url.strip().rstrip("/")
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {"model": model_name, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5}
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=12)
            if r.status_code != 200:
                return f"[{label}] OpenAI 接口返回 HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:
            return f"[{label}] 连接 OpenAI 网关失败: {str(e)}"
        return None

    if engine_type == "opencode":
        from app.llm import _get_opencode_bin
        bin_path = _get_opencode_bin()
        if not model_name or not model_name.strip():
            return f"[{label}] 请指定 opencode 模型名"
        # 针对 opencode run 执行 15s 快测
        cmd = [bin_path, "run", "-m", model_name.strip(), "--auto", "hi"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
            if res.returncode != 0:
                err_info = (res.stderr or res.stdout or "").strip()[-250:]
                return f"[{label}] opencode 报错 (rc={res.returncode}): {err_info}"
        except subprocess.TimeoutExpired:
            return f"[{label}] opencode 测试超时（>45s），请确认模型名是否有效"
        except Exception as e:
            return f"[{label}] opencode 执行异常: {str(e)}"
        return None

    if engine_type == "codebuddy":
        from app.llm import _get_codebuddy_bin
        bin_path = _get_codebuddy_bin()
        cmd = [bin_path, "--print"]
        if model_name and model_name.strip():
            cmd += ["--model", model_name.strip()]
        cmd.append("hi")
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
            if res.returncode != 0:
                return f"[{label}] codebuddy 报错 (rc={res.returncode}): {(res.stderr or '')[:200]}"
        except subprocess.TimeoutExpired:
            return f"[{label}] codebuddy 测试超时（>45s）"
        except Exception as e:
            return f"[{label}] codebuddy 执行异常: {str(e)}"
        return None

    return None


@router.post("/api/admin/test-engine-config")
def test_engine_config(body: EngineConfigBody, request: Request):
    """保存前自测校验：对启用的引擎（识别/解析/审核及灰测）发送轻量探测。"""
    require_admin(request)
    from app.models import EngineConfig, GreyAssignMode, EngineKind
    
    cfg = db.get_engine_config()
    updates = body.model_dump(exclude_none=True)
    if "grey_assign_mode" in updates:
        updates["grey_assign_mode"] = GreyAssignMode(updates["grey_assign_mode"])
    for key in ("recognition_engine", "audit_engine",
                "grey_recognition_engine", "grey_audit_engine",
                "parse_llm_engine", "grey_parse_llm_engine"):
        if key in updates:
            updates[key] = EngineKind(updates[key])
    if "grey_percent" in updates:
        updates["grey_percent"] = max(0, min(100, int(updates["grey_percent"])))
    test_cfg = EngineConfig(**{**cfg.model_dump(), **updates})

    # 1. 常规识别引擎测试
    rec_eng = test_cfg.recognition_engine.value if hasattr(test_cfg.recognition_engine, "value") else str(test_cfg.recognition_engine)
    err = _quick_test_engine(rec_eng, test_cfg.openai_rec_model if rec_eng == "openai" else test_cfg.recognition_model,
                             test_cfg.openai_rec_base_url, test_cfg.openai_rec_api_key, "识别引擎")
    if err:
        return JSONResponse(status_code=400, content={"status": "error", "msg": err})

    # 2. 常规解析引擎测试（开启时）
    if test_cfg.parse_llm_enabled:
        parse_eng = test_cfg.parse_llm_engine.value if hasattr(test_cfg.parse_llm_engine, "value") else str(test_cfg.parse_llm_engine)
        err = _quick_test_engine(parse_eng, test_cfg.openai_parse_model if parse_eng == "openai" else test_cfg.parse_llm_model,
                                 test_cfg.openai_parse_base_url, test_cfg.openai_parse_api_key, "解析 LLM")
        if err:
            return JSONResponse(status_code=400, content={"status": "error", "msg": err})

    # 3. 常规审核引擎测试（开启时）
    if test_cfg.audit_enabled:
        aud_eng = test_cfg.audit_engine.value if hasattr(test_cfg.audit_engine, "value") else str(test_cfg.audit_engine)
        err = _quick_test_engine(aud_eng, test_cfg.openai_aud_model if aud_eng == "openai" else test_cfg.audit_model,
                                 test_cfg.openai_aud_base_url, test_cfg.openai_aud_api_key, "审核引擎")
        if err:
            return JSONResponse(status_code=400, content={"status": "error", "msg": err})

    # 4. 灰测测试（开启时）
    if test_cfg.grey_enabled and test_cfg.grey_percent > 0:
        g_rec_eng = test_cfg.grey_recognition_engine.value if hasattr(test_cfg.grey_recognition_engine, "value") else str(test_cfg.grey_recognition_engine)
        err = _quick_test_engine(g_rec_eng, test_cfg.grey_openai_rec_model if g_rec_eng == "openai" else test_cfg.grey_recognition_model,
                                 test_cfg.grey_openai_rec_base_url, test_cfg.grey_openai_rec_api_key, "灰测识别")
        if err:
            return JSONResponse(status_code=400, content={"status": "error", "msg": err})

        if test_cfg.grey_parse_llm_enabled:
            gp_eng = test_cfg.grey_parse_llm_engine.value if hasattr(test_cfg.grey_parse_llm_engine, "value") else str(test_cfg.grey_parse_llm_engine)
            err = _quick_test_engine(gp_eng, test_cfg.grey_openai_parse_model if gp_eng == "openai" else test_cfg.grey_parse_llm_model,
                                     test_cfg.grey_openai_parse_base_url, test_cfg.grey_openai_parse_api_key, "灰测解析")
            if err:
                return JSONResponse(status_code=400, content={"status": "error", "msg": err})

        if test_cfg.grey_audit_enabled:
            ga_eng = test_cfg.grey_audit_engine.value if hasattr(test_cfg.grey_audit_engine, "value") else str(test_cfg.grey_audit_engine)
            err = _quick_test_engine(ga_eng, test_cfg.grey_openai_aud_model if ga_eng == "openai" else test_cfg.grey_audit_model,
                                     test_cfg.grey_openai_aud_base_url, test_cfg.grey_openai_aud_api_key, "灰测审核")
            if err:
                return JSONResponse(status_code=400, content={"status": "error", "msg": err})

    return {"status": "success", "msg": "全部生效引擎测试通过"}


@router.put("/api/admin/engine-config/promote")
def promote_grey_config(request: Request):
    """一键推全：灰测组 → 常规组；推全前保存回滚快照。
    同时将灰测组重置为默认（推全后灰测停用，下次从干净态重新启用）。
    """
    account = require_admin(request)
    who = account.get("email", "unknown")
    from app.models import EngineConfig
    from datetime import datetime
    cfg = db.get_engine_config()
    grey = db.get_engine_config()

    # 灰测未启用时视为无内容可推
    if not grey.grey_enabled:
        return JSONResponse(status_code=400, content={
            "status": "error",
            "msg": "灰测未启用，无内容可推全",
        })

    # 回滚快照：记录推全前常规组配置 + 操作信息
    prev = cfg.model_dump()
    snap = {
        "prev": {
            "recognition_engine": prev.get("recognition_engine"),
            "recognition_model": prev.get("recognition_model"),
            "audit_engine": prev.get("audit_engine"),
            "audit_model": prev.get("audit_model"),
            "audit_enabled": prev.get("audit_enabled"),
            "openai_rec_base_url": prev.get("openai_rec_base_url"),
            "openai_rec_api_key": prev.get("openai_rec_api_key"),
            "openai_rec_model": prev.get("openai_rec_model"),
            "openai_aud_base_url": prev.get("openai_aud_base_url"),
            "openai_aud_api_key": prev.get("openai_aud_api_key"),
            "openai_aud_model": prev.get("openai_aud_model"),
            "parse_llm_enabled": prev.get("parse_llm_enabled"),
            "parse_llm_engine": prev.get("parse_llm_engine"),
            "parse_llm_model": prev.get("parse_llm_model"),
            "openai_parse_base_url": prev.get("openai_parse_base_url"),
            "openai_parse_api_key": prev.get("openai_parse_api_key"),
            "openai_parse_model": prev.get("openai_parse_model"),
        },
        "meta": {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "operator": account.get("email", "unknown"),
            "action": "promote",
        },
    }

    # 构建新常规配置：从灰测组拉取对应字段
    new_dict = {
        **cfg.model_dump(),
        "recognition_engine": grey.grey_recognition_engine,
        "recognition_model": grey.grey_recognition_model,
        "audit_engine": grey.grey_audit_engine,
        "audit_model": grey.grey_audit_model,
        "audit_enabled": grey.grey_audit_enabled,
        "openai_rec_base_url": grey.grey_openai_rec_base_url,
        "openai_rec_api_key": grey.grey_openai_rec_api_key,
        "openai_rec_model": grey.grey_openai_rec_model,
        "openai_aud_base_url": grey.grey_openai_aud_base_url,
        "openai_aud_api_key": grey.grey_openai_aud_api_key,
        "openai_aud_model": grey.grey_openai_aud_model,
        "parse_llm_enabled": grey.grey_parse_llm_enabled,
        "parse_llm_engine": grey.grey_parse_llm_engine,
        "parse_llm_model": grey.grey_parse_llm_model,
        "openai_parse_base_url": grey.grey_openai_parse_base_url,
        "openai_parse_api_key": grey.grey_openai_parse_api_key,
        "openai_parse_model": grey.grey_openai_parse_model,
        "rollback_snapshot": snap,
    }

    new_cfg = EngineConfig(**new_dict)
    db.set_engine_config(new_cfg)

    # 把灰测组清空/重置回默认（关闭灰测，恢复默认识别/审核模型，关闭灰测审核/解析开关）
    from app.models import GreyAssignMode, EngineKind
    grey_defaults = {
        "grey_enabled": False,
        "grey_percent": 0,
        "grey_assign_mode": GreyAssignMode.RECEIPT,
        "grey_recognition_engine": EngineKind.OPENCODE,
        "grey_recognition_model": "opencode/mimo-v2.5-free",
        "grey_audit_enabled": True,
        "grey_audit_engine": EngineKind.OPENCODE,
        "grey_audit_model": "opencode/mimo-v2.5-free",
        "grey_openai_rec_base_url": "",
        "grey_openai_rec_api_key": "",
        "grey_openai_rec_model": "",
        "grey_openai_aud_base_url": "",
        "grey_openai_aud_api_key": "",
        "grey_openai_aud_model": "",
        "grey_parse_llm_enabled": False,
        "grey_parse_llm_engine": EngineKind.OPENCODE,
        "grey_parse_llm_model": "opencode/mimo-v2.5-free",
        "grey_openai_parse_base_url": "",
        "grey_openai_parse_api_key": "",
        "grey_openai_parse_model": "",
    }
    final_cfg = EngineConfig(**{**new_cfg.model_dump(), **grey_defaults})
    db.set_engine_config(final_cfg)

    db.append_system_audit_log(who, "promote_grey_config",
                               "recognition_engine",
                               str(cfg.recognition_engine), str(final_cfg.recognition_engine))

    return {"status": "success", "data": final_cfg.model_dump()}


@router.put("/api/admin/engine-config/rollback")
def rollback_engine_config(request: Request):
    """一键回滚：用最近一次推全快照覆盖回常规组并清空快照。"""
    account = require_admin(request)
    who = account.get("email", "unknown")
    from app.models import EngineConfig
    cfg = db.get_engine_config()
    snap = cfg.rollback_snapshot
    if not snap or "prev" not in snap:
        return JSONResponse(status_code=400, content={
            "status": "error",
            "msg": "没有可回滚的快照（从未执行过推全，或快照已被清空）",
        })
    prev = snap.get("prev", {})
    old_engine = cfg.recognition_engine
    new_dict = {
        **cfg.model_dump(),
        **prev,
        "rollback_snapshot": None,
    }
    new_cfg = EngineConfig(**new_dict)
    db.set_engine_config(new_cfg)
    db.append_system_audit_log(who, "rollback_engine_config",
                               "recognition_engine",
                               str(old_engine), str(new_cfg.recognition_engine))
    return {
        "status": "success",
        "data": new_cfg.model_dump(),
        "msg": "已回滚至上次推全前的配置",
    }


@router.get("/api/admin/engine-config/diff")
def diff_regular_vs_grey(request: Request):
    """常规 vs 灰测配置对比：字段级差异列表（敏感字段仅提示是否一致）。"""
    require_admin(request)
    cfg = db.get_engine_config()
    if not cfg.grey_enabled:
        return JSONResponse(status_code=400, content={
            "status": "error",
            "msg": "灰测未启用，无内容可对比",
        })

    pairs = [
        ("recognition_engine", "grey_recognition_engine", "识别引擎"),
        ("recognition_model", "grey_recognition_model", "识别模型"),
        ("audit_enabled", "grey_audit_enabled", "审核开关"),
        ("audit_engine", "grey_audit_engine", "审核引擎"),
        ("audit_model", "grey_audit_model", "审核模型"),
        ("parse_llm_enabled", "grey_parse_llm_enabled", "解析 LLM 开关"),
        ("parse_llm_engine", "grey_parse_llm_engine", "解析引擎"),
        ("parse_llm_model", "grey_parse_llm_model", "解析模型"),
    ]
    # OpenAI 参数区：敏感字段（api_key / base_url）不直接展示值，仅提示一致/不一致
    sensitive_pairs = [
        ("openai_rec_base_url", "grey_openai_rec_base_url", "识别 · OpenAI Base URL"),
        ("openai_rec_model", "grey_openai_rec_model", "识别 · OpenAI 模型名"),
        ("openai_aud_base_url", "grey_openai_aud_base_url", "审核 · OpenAI Base URL"),
        ("openai_aud_model", "grey_openai_aud_model", "审核 · OpenAI 模型名"),
        ("openai_parse_base_url", "grey_openai_parse_base_url", "解析 · OpenAI Base URL"),
        ("openai_parse_model", "grey_openai_parse_model", "解析 · OpenAI 模型名"),
    ]
    secret_pairs = [
        ("openai_rec_api_key", "grey_openai_rec_api_key", "识别 · OpenAI API Key"),
        ("openai_aud_api_key", "grey_openai_aud_api_key", "审核 · OpenAI API Key"),
        ("openai_parse_api_key", "grey_openai_parse_api_key", "解析 · OpenAI API Key"),
    ]

    def _norm(v):
        return v.value if hasattr(v, "value") else v

    diffs = []
    for reg_key, grey_key, label in pairs:
        reg = _norm(cfg.model_dump().get(reg_key))
        grey = _norm(cfg.model_dump().get(grey_key))
        if str(reg) != str(grey):
            diffs.append({
                "label": label,
                "regular": reg,
                "grey": grey,
                "sensitive": False,
                "identical": False,
            })
    for reg_key, grey_key, label in sensitive_pairs:
        reg = cfg.model_dump().get(reg_key)
        grey = cfg.model_dump().get(grey_key)
        identical = bool(reg) == bool(grey) and str(reg or "") == str(grey or "")
        if not identical:
            diffs.append({
                "label": label,
                "regular": "***" if reg else "",
                "grey": "***" if grey else "",
                "sensitive": True,
                "identical": identical,
            })
    for reg_key, grey_key, label in secret_pairs:
        reg = cfg.model_dump().get(reg_key)
        grey = cfg.model_dump().get(grey_key)
        identical = bool(reg) == bool(grey) and str(reg or "") == str(grey or "")
        diffs.append({
            "label": label,
            "regular": "********" if reg else "",
            "grey": "********" if grey else "",
            "sensitive": True,
            "identical": identical,
        })

    diffCount = sum(1 for d in diffs if not d.get("identical"))
    return {
        "status": "success",
        "data": {
            "diffs": diffs,
            "totalPairs": len(pairs) + len(sensitive_pairs) + len(secret_pairs),
            "diffCount": diffCount,
        },
    }


@router.get("/api/admin/engine-config")
def get_engine_config(request: Request):
    require_admin(request)
    return {"status": "success", "data": db.get_engine_config().model_dump()}


@router.put("/api/admin/engine-config")
def set_engine_config(body: EngineConfigBody, request: Request):
    require_admin(request)
    account = getattr(request.state, "account", {})
    who = account.get("email", "unknown")
    from app.models import EngineConfig, GreyAssignMode, EngineKind
    cfg = db.get_engine_config()
    updates = body.model_dump(exclude_none=True)
    if "grey_assign_mode" in updates:
        updates["grey_assign_mode"] = GreyAssignMode(updates["grey_assign_mode"])
    for key in ("recognition_engine", "audit_engine",
                "grey_recognition_engine", "grey_audit_engine",
                "parse_llm_engine", "grey_parse_llm_engine"):
        if key in updates:
            updates[key] = EngineKind(updates[key])
    if "grey_percent" in updates:
        updates["grey_percent"] = max(0, min(100, int(updates["grey_percent"])))
    old_engine = cfg.recognition_engine
    new_cfg = EngineConfig(**{**cfg.model_dump(), **updates})
    db.set_engine_config(new_cfg)
    changed_keys = list(updates.keys())
    db.append_system_audit_log(who, "set_engine_config",
                               "engine_config",
                               " ".join(changed_keys),
                               str(new_cfg.recognition_engine))
    return {"status": "success", "data": new_cfg.model_dump()}


@router.get("/api/admin/system-audit")
def get_system_audit(request: Request):
    """系统级审计日志读取（engine 配置变更等无 receipt_id 的操作）。"""
    require_admin(request)
    return {"status": "success", "data": db.read_system_audit_log()}


@router.get("/api/admin/grey-test")
def grey_test_status(request: Request):
    require_admin(request)
    cfg = db.get_engine_config()
    return {"status": "success", "data": {
        "current": cfg.model_dump(),
        "assign_modes": {
            "receipt": "按单据随机分配（每单独立 random < 概率%）",
            "supplier": "按供应商分配（同供应商一致命中，确定性）",
        },
        "note": "",
    }}


@router.get("/api/admin/trash")
def trash_list(request: Request):
    """回收站：软删除单据列表。"""
    require_admin(request)
    rows = db.list_trash_receipts()
    data = []
    for r in rows:
        data.append({
            "id": r.id,
            "supplier_name": r.supplier_name or "",
            "receipt_date": r.receipt_date or "",
            "total_amount": r.total_amount or 0.0,
            "status": r.status,
            "created_at": r.created_at or "",
            "deleted_at": r.deleted_at or "",
        })
    return {"status": "success", "data": data}


@router.post("/api/admin/trash/{receipt_id}/restore")
def restore_trash(receipt_id: int, request: Request):
    """从回收站恢复单据。"""
    require_admin(request)
    if not db.restore_receipt(receipt_id):
        return JSONResponse(content={"status": "error", "msg": "单据不存在或不在回收站"},
                            status_code=404)
    return {"status": "success", "receipt_id": receipt_id,
            "msg": f"单据 #{receipt_id} 已恢复"}


@router.get("/api/review")
def ai_review(request: Request):
    require_role("owner")(request)
    from app.services.inventory import cost_summary
    from app.chains import review_chain
    cost = cost_summary()
    cfg = db.get_engine_config()
    result = review_chain.run_review(cost.get("items", {}), config=cfg)
    return {"status": "success", "data": result}


@router.get("/api/cost")
def cost_summary(request: Request):
    require_role("owner")(request)
    from app.services.inventory import cost_summary as get_cost
    return {"status": "success", "data": get_cost()}


# -------------------------------------------------------------
# 阶段 1：AI 发现
# -------------------------------------------------------------
@router.get("/api/ai-insights")
def ai_insights(request: Request):
    require_role("owner")(request)
    from app.services.inventory import cost_summary as get_cost
    from app.chains import review_chain
    cost = get_cost()
    data = review_chain.weekly_insights(cost)
    return {"status": "success", "data": data}


# -------------------------------------------------------------
# 阶段 4：对话式查询 & 生成式 UI 卡片
# -------------------------------------------------------------
class ChatQueryBody(BaseModel):
    question: str = ""


@router.post("/api/chat/query")
def chat_query(body: ChatQueryBody, request: Request):
    """对话式查询 Agent：自然语言查询库存、价格走势与未付账单。"""
    require_role("staff")(request)
    from app.chains.query_chain import run_query
    res = run_query(body.question)
    return {"status": "success", "data": res}


@router.get("/api/insight-cards")
def get_insight_cards(request: Request):
    """生成式 UI 卡片：多步推理输出异动预警卡、谈判策略卡与健康度卡片。"""
    require_role("owner")(request)
    from app.services.inventory import cost_summary as get_cost
    from app.chains.review_chain import generate_insight_cards
    cost = get_cost()
    cards = generate_insight_cards(cost)
    return {"status": "success", "cards": cards}


# -------------------------------------------------------------
# Prompt 工程化管理与评测资产
# -------------------------------------------------------------
@router.get("/api/admin/prompts")
def get_prompt_registry(request: Request):
    """获取全项目 6 大组件的 Prompt 版本演进与多维效果评测基准数据。"""
    require_admin(request)
    from app.prompts import list_prompt_versions, get_benchmark_report
    return {
        "status": "success",
        "versions": list_prompt_versions(),
        "benchmark": get_benchmark_report(),
    }



