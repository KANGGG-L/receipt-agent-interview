# -*- coding: utf-8 -*-
from __future__ import annotations
"""识别管线编排：LangChain 模型封装 + 确定性流程（线性 + 重试阶梯）。

- 流程：extract（VLM 识别）→ contract gate（契约门禁）→ math gate（算术门禁）
        → audit（交叉审核）→ done
- 重试阶梯：识别失败/契约算术不过 → 带反馈重试，最多 MAX_RETRY 轮；仍败 → error
- 灰测：命中灰测组的单据走灰测组引擎/模型（分组测试）
- 每个决策写日志（状态快照 + 选择 + 理由），蒸馏成规则（对齐完整版蒸馏路径）

编排用普通 Python（线性流程 + 重试循环），不引入图编排框架——
此场景无并行/分支回环需求，简单函数比状态机更可读、易测。
"""

import time
from typing import Optional

from app.chains import audit_chain, extract_chain
from app.llm import build_recognition_model
from app.models import ReceiptData, EngineConfig
from app.services import math_engine

MAX_RETRY = 3


def _snapshot(log, action, reason, attempt, engine="", note=""):
    """决策履历条目（对齐完整版 OrchestratorDecisionLog）。"""
    log.append({
        "attempt": attempt,
        "engine": engine,
        "action": action,
        "reason": reason,
        "note": note,
        "ts": time.strftime("%H:%M:%S"),
    })


def _run_extract(image_path, vendor_hint, config, use_grey, retry_feedback, attempt):
    """单轮识别：VLM 读图 → 结构化（含可选解析 LLM）。"""
    model = build_recognition_model(
        config.recognition_model if config else None,
        cfg=config, use_grey=use_grey,
    )
    return extract_chain.extract_receipt(
        image_path, vendor_hint=vendor_hint,
        model=model, config=config,
        retry_feedback=retry_feedback,
        use_grey=use_grey,
    )


def _run_gates(data: ReceiptData):
    """契约门禁 + 算术门禁（确定性代码，零 token）。"""
    from app.services.contract import validate_contract
    data, err = validate_contract(data.model_dump())
    if err:
        return None, err
    problems = math_engine.validate_and_report(data)
    if problems:
        return None, "算术门禁: " + "; ".join(problems)
    return data, None


def run_pipeline(image_path: str, vendor_hint: str = "",
                 config: Optional[EngineConfig] = None,
                 supplier_name: str = "") -> dict:
    """完整识别管线入口（线性编排 + 重试阶梯）。

    supplier_name 供灰测按供应商分配（同供应商一致命中）。
    返回 dict 含 data / raw / log / contract_error 等（与旧接口兼容）。
    """
    from app.models import should_use_grey
    use_grey = should_use_grey(config, supplier_name) if config else False

    log = []
    if use_grey:
        _snapshot(log, "grey_assigned",
                  f"命中灰测组（分配模式: {config.grey_assign_mode.value}, 概率: {config.grey_percent}%）",
                  attempt=0, engine="grey")

    state = {
        "image_path": image_path,
        "vendor_hint": vendor_hint,
        "config": config,
        "use_grey": use_grey,
        "attempt": 0,
        "engine_name": "codebuddy",
        "log": log,
        "retry_feedback": "",
        "contract_error": "",
        "math_problems": [],
        "audit_result": {},
        "raw": "",
        "data": None,
        "elapsed_ms": 0,
        "last_error": "",
    }

    # ---- 重试阶梯：识别 + 门禁，最多 MAX_RETRY 轮 ----
    while state["attempt"] < MAX_RETRY:
        state["attempt"] += 1
        attempt = state["attempt"]

        result = _run_extract(
            image_path, vendor_hint, config, use_grey,
            state["retry_feedback"], attempt,
        )
        state["raw"] = result["raw"]
        state["elapsed_ms"] = result["elapsed_ms"]
        state["last_error"] = result["error"]

        if result["error"]:
            _snapshot(log, "extract_fail", f"识别失败(第{attempt}轮): {result['error']}",
                      attempt, engine=result["engine"])
            continue  # 识别失败 → 下一轮重试

        data, gate_err = _run_gates(result["data"])
        if gate_err:
            state["contract_error"] = gate_err
            state["retry_feedback"] = gate_err
            _snapshot(log, "gate_reject", f"门禁拒绝(第{attempt}轮): {gate_err}",
                      attempt, engine=result["engine"])
            continue  # 门禁不过 → 带反馈重试

        state["data"] = data
        state["contract_error"] = ""
        _snapshot(log, "extract_ok", f"识别成功(第{attempt}轮) engine={result['engine']}",
                  attempt, engine=result["engine"])
        _snapshot(log, "gates_pass", "契约+算术门禁通过（零 token）",
                  attempt, engine=result["engine"])
        break

    # ---- 结果处理 ----
    if state["data"] is None:
        state["status"] = "error"
        if state["contract_error"]:
            _snapshot(log, "error_exit", f"重试耗尽: {state['contract_error']}",
                      state["attempt"])
        else:
            _snapshot(log, "error_exit", f"重试耗尽: {state['last_error'] or '识别失败'}",
                      state["attempt"])
        return state

    # ---- 交叉审核（可选，失败不阻断）----
    audit = audit_chain.run_audit(
        image_path, state["data"], config=config, use_grey=use_grey,
    )
    state["audit_result"] = audit
    if audit.get("skipped"):
        _snapshot(log, "audit_skip", f"审核跳过: {audit.get('reason')}", state["attempt"])
    elif audit.get("overall_consistent"):
        _snapshot(log, "audit_pass", "交叉审核一致", state["attempt"])
    else:
        _snapshot(log, "audit_flag",
                  f"交叉审核发现 {len(audit.get('discrepancies', []))} 处分歧",
                  state["attempt"])

    state["status"] = "parsed"
    return state
