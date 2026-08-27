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

import json
import logging
import os
import threading
import time
from typing import Optional

from app.chains import audit_chain, extract_chain
from app.llm import build_recognition_model
from app.models import ReceiptData, EngineConfig
from app.services import math_engine
from app.services.rag import retrieve_context

MAX_RETRY = 3

# ---- 记忆落盘：文件层并发安全与路径 ----
_MEMORY_LOCK = threading.Lock()
_MEMORY_LOG_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../../artifacts/memory/parse_log.jsonl")
)
# 备用路径（容器内 demo 相对）
_MEMORY_LOG_PATH_ALT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../artifacts/memory/parse_log.jsonl")
)

def _memory_log_path() -> str:
    # 优先保证 artifacts/memory 目录存在，取可写路径
    for p in (_MEMORY_LOG_PATH, _MEMORY_LOG_PATH_ALT):
        d = os.path.dirname(p)
        try:
            os.makedirs(d, exist_ok=True)
            return p
        except Exception:
            continue
    return _MEMORY_LOG_PATH


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


def _run_extract(image_path, vendor_hint, config, use_grey, retry_feedback, attempt,
                 vendor_prior: str = "", on_event=None):
    """单轮识别：VLM 读图 → 结构化（含可选解析 LLM）。vendor_prior 为重试轮注入的历史先验。"""
    model = build_recognition_model(
        config.recognition_model if config else None,
        cfg=config, use_grey=use_grey,
    )
    return extract_chain.extract_receipt(
        image_path, vendor_hint=vendor_hint,
        model=model, config=config,
        retry_feedback=retry_feedback,
        use_grey=use_grey,
        vendor_prior=vendor_prior,
        on_event=on_event,
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


def _run_audit(image_path: str, data: ReceiptData, config, use_grey: bool) -> dict:
    """U-10: 交叉审核执行，异常/超时时优雅降级为 skipped。"""
    try:
        return audit_chain.run_audit(
            image_path, data, config=config, use_grey=use_grey,
        )
    except Exception as e:
        logging.getLogger("supervisor").warning(f"交叉审核执行失败: {e}")
        return {"skipped": True, "reason": f"audit_error: {e}"}


def _log_audit_decision(receipt_id, experiment_id, config, use_grey, audit):
    """U-10 / U-2: 记录交叉审核 AI 决策日志，透传有效 receipt_id。"""
    if receipt_id is None:
        return
    try:
        _ai_engine = str(getattr(config, "audit_engine", "") or "opencode")
        _ai_model = str(getattr(config, "audit_model", "") or "")
        if use_grey:
            _ai_engine = str(getattr(config, "grey_audit_engine", _ai_engine) or _ai_engine)
            _ai_model = str(getattr(config, "grey_audit_model", _ai_model) or _ai_model)
        from app import db as _db
        # 最严格：audit 亦记录 token 占位（text 审核零 token，VLM 审核可为实际值；当前 text 模式记 0）
        _audit_tu = audit.get("token_usage") or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        try:
            from app.llm import _normalize_token_usage
            _audit_tu = _normalize_token_usage(_audit_tu)
        except Exception:
            _audit_tu = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        try:
            from app.llm import _calc_cost_hkd
            _audit_cost = _calc_cost_hkd(_audit_tu)
        except Exception:
            _audit_cost = 0.0
        _audit_elapsed = {"audit": float(audit.get("audit_ms", 0) or 0), "total": float(audit.get("audit_ms", 0) or 0)}
        _db.log_ai_decision(
            receipt_id=int(receipt_id),
            supplier_id=None,
            experiment_id=int(experiment_id) if experiment_id else None,
            grp="treatment" if use_grey else "control",
            engine=_ai_engine,
            model=_ai_model,
            use_grey=1 if use_grey else 0,
            decision_type="audit",
            field_path="overall",
            ai_value={
                "overall_consistent": audit.get("overall_consistent"),
                "trust": audit.get("trust"),
                "discrepancies_count": len(audit.get("discrepancies", []) or []),
                "reason": audit.get("reason", ""),
                "skipped": bool(audit.get("skipped")),
            },
            confidence=audit.get("trust"),
            extra={
                "discrepancies": audit.get("discrepancies", []) or [],
                "corrected_suggestions": audit.get("corrected_suggestions", {}) or {},
                "tokens_prompt": int(_audit_tu.get("prompt_tokens", 0) or 0),
                "tokens_completion": int(_audit_tu.get("completion_tokens", 0) or 0),
                "tokens_total": int(_audit_tu.get("total_tokens", 0) or 0),
                "cost_hkd": float(_audit_cost or 0),
                "elapsed_ms": _audit_elapsed,
                "success": bool(audit.get("overall_consistent")) if audit.get("skipped") is False else False,
            },
        )
    except Exception as e:
        logging.getLogger("supervisor").warning(f"记录交叉审核 AI 决策失败: {e}")


def _log_extract_decision(receipt_id, experiment_id, config, use_grey,
                          attempt, engine, status, gate_err="",
                          token_usage=None, cost_hkd=None, elapsed_ms=None,
                          success=None, image_path="", supplier="", doc_form=""):
    """U-2：每轮 extract 决策落库（AI 决策履历断链修复）。

    - receipt_id=None（run_pipeline 直接调用/冒烟场景）→ 跳过写库不抛错
    - 写失败仅记 warning，绝不影响识别主链路（AC-E2）
    - ai_value 紧凑 JSON ≤500 字符，超长截断加 …(truncated) 尾标；
      gate_err 仅拒绝/失败轮携带，≤200 字摘要
    - 最严格记忆落盘：ai_value 与 extra 均追加 {tokens_prompt, tokens_completion, tokens_total, cost_hkd, elapsed_ms, success, image_path, supplier, doc_form}
      token 来自 ChatResult.generations[0].message.response_metadata['token_usage']（DashScope qwen3-vl-flash），本地 opencode/codebuddy 无 token 记 0 且 cost 0；
      cost 按 docs/04-AI技术选型与评测/02-L0-L9选型决策档案/L4-多模态VLM直识(定稿冠军).md:21 qwen3-vl-flash ¥0.0022/张 或 token 单价 ¥0.15/1M 输入 / ¥1.50/1M 输出 计算。
    """
    if receipt_id is None:
        return
    # 归一化 token
    tu = token_usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    if not isinstance(tu, dict):
        tu = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    def _to_int(v):
        try:
            return int(v or 0)
        except Exception:
            try:
                return int(float(v or 0))
            except Exception:
                return 0
    tokens_prompt = _to_int(tu.get("prompt_tokens", tu.get("input_tokens", 0)))
    tokens_completion = _to_int(tu.get("completion_tokens", tu.get("output_tokens", 0)))
    tokens_total = _to_int(tu.get("total_tokens", 0) or (tokens_prompt + tokens_completion))
    # 成本计算
    if cost_hkd is None:
        try:
            from app.llm import _calc_cost_hkd
            cost_hkd = _calc_cost_hkd({"prompt_tokens": tokens_prompt, "completion_tokens": tokens_completion, "total_tokens": tokens_total})
        except Exception:
            cost_hkd = round(tokens_total * 0.15 / 1_000_000, 6) if tokens_total else 0.0
    else:
        try:
            cost_hkd = round(float(cost_hkd or 0), 6)
        except Exception:
            cost_hkd = 0.0
    # elapsed 归一
    elapsed = elapsed_ms or {}
    if not isinstance(elapsed, dict):
        elapsed = {"total": float(elapsed or 0)}
    # success 推断
    if success is None:
        success = (status in ("extract_ok", "parse_ok"))
    payload = {
        "attempt": attempt,
        "engine": str(engine or "")[:60],
        "status": status,
        "use_grey": 1 if use_grey else 0,
        "tokens_total": tokens_total,
        "cost_hkd": cost_hkd,
        "elapsed_ms": elapsed,
        "success": bool(success),
    }
    if gate_err:
        payload["gate_err"] = str(gate_err)[:200]
    # 长度探测与 _safe_json 同参数（存储态）：≤500 传 dict（单次序列化，
    # 前端 JSON.parse 直接得对象）；超长则存截断字符串并加尾标（极端兜底）
    probe = json.dumps(payload, ensure_ascii=False)
    if len(probe) > 500:
        stored = json.dumps(payload, ensure_ascii=False,
                            separators=(",", ":"))[:500] + "…(truncated)"
    else:
        stored = payload
    # extra 结构化 JSON（DB 可查 + 文件可追溯）
    extra = {
        "tokens_prompt": tokens_prompt,
        "tokens_completion": tokens_completion,
        "tokens_total": tokens_total,
        "cost_hkd": cost_hkd,
        "elapsed_ms": elapsed,
        "success": bool(success),
        "image_path": str(image_path or "")[:500],
        "supplier": str(supplier or "")[:120],
        "doc_form": str(doc_form or "")[:60],
    }
    try:
        _model = str(getattr(config, "recognition_model", "") or "")
        if use_grey:
            _model = str(getattr(config, "grey_recognition_model", _model) or _model)
        from app import db as _db
        _db.log_ai_decision(
            receipt_id=int(receipt_id),
            supplier_id=None,
            experiment_id=int(experiment_id) if experiment_id else None,
            grp="treatment" if use_grey else "control",
            engine=str(engine or ""),
            model=_model,
            use_grey=1 if use_grey else 0,
            decision_type="extract",
            field_path="overall",
            ai_value=stored,
            extra=extra,
        )
    except Exception as e:
        logging.getLogger("supervisor").warning(f"记录 AI 决策失败: {e}")


def run_pipeline(image_path: str, vendor_hint: str = "",
                 config: Optional[EngineConfig] = None,
                 supplier_name: str = "",
                 receipt_id: Optional[int] = None,
                 experiment_id: Optional[int] = None,
                 on_event=None,
                 ) -> dict:
    """完整识别管线入口（线性编排 + 重试阶梯）。

    supplier_name 供灰测按供应商分配（同供应商一致命中）。
    on_event（可选）：实时事件回调，如首选引擎降级回退时立即通知调用方
    （异步 Job 层借此把回退状态透传给前端轮询，第一时间弹出提示）。
    返回 dict 含 data / raw / log / contract_error 等（与旧接口兼容）。
    """
    from app.models import should_use_grey
    use_grey = should_use_grey(config, supplier_name) if config else False

    pipeline_start = time.time()
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
        "vendor_context": "",  # VendorMemory 检索先验（跨轮保留，落库 rag_context_json）
        # 分段计时埋点（Layer 1.3）
        "extract_ms": 0.0,
        "parse_ms": 0.0,
        "rag_ms": 0.0,
        "audit_ms": 0.0,
        "retry_count": 0,
        "total_ms": 0.0,
        # token 记忆落盘（最严格）
        "tokens_prompt": 0,
        "tokens_completion": 0,
        "tokens_total": 0,
        "cost_hkd": 0.0,
        "success": False,
        "supplier": "",
        "doc_form": "",
    }

    # ---- 重试阶梯：识别 + 门禁，最多 MAX_RETRY 轮 ----
    while state["attempt"] < MAX_RETRY:
        state["attempt"] += 1
        attempt = state["attempt"]

        result = _run_extract(
            image_path, vendor_hint, config, use_grey,
            state["retry_feedback"], attempt,
            vendor_prior=state["vendor_context"],
            on_event=on_event,
        )
        state["raw"] = result["raw"]
        state["elapsed_ms"] = result["elapsed_ms"]
        state["last_error"] = result["error"]
        # 分段计时累加（跨重试轮汇总）
        state["extract_ms"] = round(state["extract_ms"] + result.get("extract_ms", 0) or 0, 1)
        state["rag_ms"] = round(state["rag_ms"] + result.get("rag_ms", 0) or 0, 1)
        state["parse_ms"] = round(
            state["parse_ms"] + max(0, (result.get("parse_llm") or {}).get("elapsed_ms", 0) or 0), 1)
        # token 汇总（跨重试轮累加，最严格落盘）
        _tu = result.get("token_usage") or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        try:
            from app.llm import _normalize_token_usage
            _tu = _normalize_token_usage(_tu)
        except Exception:
            pass
        _cost = result.get("cost_hkd", 0.0)
        try:
            _cost = float(_cost or 0)
        except Exception:
            _cost = 0.0
        state["engine_name"] = str(result.get("engine") or state.get("engine_name") or "opencode")
        state["tokens_prompt"] = int(state.get("tokens_prompt", 0) or 0) + int(_tu.get("prompt_tokens", 0) or 0)
        state["tokens_completion"] = int(state.get("tokens_completion", 0) or 0) + int(_tu.get("completion_tokens", 0) or 0)
        state["tokens_total"] = int(state.get("tokens_total", 0) or 0) + int(_tu.get("total_tokens", 0) or 0)
        state["cost_hkd"] = round(float(state.get("cost_hkd", 0) or 0) + float(_cost or 0), 6)
        # 预取 supplier/doc_form 供记忆落盘（来自本轮识别结果）
        try:
            _d = result.get("data")
            if _d is not None:
                state["supplier"] = str(getattr(_d, "vendor", "") or "")[:120]
                _df = getattr(_d, "doc_form", "")
                state["doc_form"] = str(_df.value if hasattr(_df, "value") else _df or "")[:60]
        except Exception:
            pass
        # VendorMemory 先验透传：extract 各通道（hint/parse/retry）合并的先验回写 state（跨轮保留）
        state["vendor_context"] = result.get("vendor_context") or state["vendor_context"]

        if result.get("fallback_triggered"):
            state["fallback_triggered"] = True
            state["fallback_reason"] = result.get("fallback_reason", "")
            state["fallback_from"] = result.get("fallback_from", "")
            state["fallback_engine"] = result.get("fallback_engine", "codebuddy")
            _snapshot(log, "engine_fallback", state["fallback_reason"], attempt, engine="codebuddy")
            if on_event is not None:
                try:
                    on_event({
                        "type": "engine_fallback",
                        "attempt": attempt,
                        "reason": state["fallback_reason"],
                        "from": state["fallback_from"],
                        "to": state["fallback_engine"],
                    })
                except Exception:
                    pass

        if result["error"]:
            _snapshot(log, "extract_fail", f"识别失败(第{attempt}轮): {result['error']}",
                      attempt, engine=result["engine"])
            _log_extract_decision(receipt_id, experiment_id, config, use_grey,
                                  attempt, result["engine"], "extract_fail",
                                  gate_err=result["error"],
                                  token_usage=_tu, cost_hkd=_cost,
                                  elapsed_ms={"extract": state["extract_ms"], "parse": state["parse_ms"], "rag": state["rag_ms"], "audit": state["audit_ms"], "total": 0},
                                  success=False, image_path=image_path,
                                  supplier=state.get("supplier", ""), doc_form=state.get("doc_form", ""))
            if result.get("fallback_failed") or result.get("fallback_triggered") or "fallback" in str(result.get("engine", "")):
                # 主模型与备用模型均失败时，不再盲目重试 3 轮，立即快速返回供前端自动切入手工输入界面
                state["fallback_failed"] = True
                state["fallback_triggered"] = True
                break
            continue  # 识别失败 → 下一轮重试

        # 首轮补检索（真飞轮）：无 hint 时 VLM 已识别出供应商 → 读 VendorMemory 补上下文，
        # 供后续 gate_reject 重试轮注入（异常置空，不阻断识别线程）
        if not state["vendor_context"] and result["data"] is not None:
            try:
                r_start = time.time()
                ctx = retrieve_context(
                    getattr(result["data"], "vendor", "") or "") or ""
                state["rag_ms"] = round(state["rag_ms"] + (time.time() - r_start) * 1000, 1)
                state["vendor_context"] = ctx
            except Exception:
                state["vendor_context"] = ""

        data, gate_err = _run_gates(result["data"])
        if gate_err:
            state["contract_error"] = gate_err
            # Layer 2.2：门禁不过先尝试「解析级廉价修正」（纯文本 parse LLM，不重读图）；
            # 修正后通过门禁则直接进入审核，避免整图重识别。仅当修正仍失败时回退到整图重试。
            # 优化：长单 7-15 行已限边 1000，audit_mode text 已 0.9ms；contract/math 失败快速反馈而非重调 VLM
            # （当前 math 3060 vs 1650 误读触发 3 次 VLM 重跑各 40s → 应纯文本快速反馈）
            c_start = time.time()
            corrected = extract_chain.correct_receipt_with_feedback(
                result["raw"], gate_err, config=config, use_grey=use_grey,
            )
            state["parse_ms"] = round(
                state["parse_ms"] + max(0, (time.time() - c_start) * 1000), 1)
            if corrected:
                corr_data, corr_err = extract_chain._parse_to_receipt(corrected)
                corr_gate = corr_err
                if corr_data is not None and not corr_err:
                    corr_data, corr_gate = _run_gates(corr_data)
                if corr_gate is None:
                    # 修正成功 → 直接采用，进入审核，不再整图重识别
                    state["raw"] = corrected
                    _snapshot(log, "parse_correct",
                              f"解析级修正通过门禁(第{attempt}轮): {gate_err}",
                              attempt, engine=result["engine"])
                    _log_extract_decision(receipt_id, experiment_id, config, use_grey,
                                           attempt, result["engine"], "parse_ok",
                                           token_usage=_tu, cost_hkd=_cost,
                                           elapsed_ms={"extract": state["extract_ms"], "parse": state["parse_ms"], "rag": state["rag_ms"], "audit": state["audit_ms"], "total": 0},
                                           success=True, image_path=image_path,
                                           supplier=state.get("supplier", ""), doc_form=state.get("doc_form", ""))
                    state["data"] = corr_data
                    state["contract_error"] = ""
                    # parse 修正成功同样视为成功，更新 supplier/doc_form
                    try:
                        state["supplier"] = str(getattr(corr_data, "vendor", "") or state.get("supplier", ""))[:120]
                        _dfc = getattr(corr_data, "doc_form", "")
                        state["doc_form"] = str(_dfc.value if hasattr(_dfc, "value") else _dfc or state.get("doc_form", ""))[:60]
                    except Exception:
                        pass
                    state["success"] = True
                    break
            # 修正未通过 → 快速反馈路径：contract/math 失败不再重调 VLM（避免 3*40s），直接快速反馈
            is_fast_gate = ("算术门禁" in gate_err or "契约" in gate_err
                            or "总额" in gate_err or "明细为空" in gate_err
                            or "契约校验失败" in gate_err)
            if is_fast_gate:
                # 快速反馈：记录 gate_reject_fast，保留已解析结构交人工快速复核（不阻断、不整图重识别）
                _snapshot(log, "gate_reject_fast",
                          f"门禁快速反馈(第{attempt}轮): {gate_err}（已尝试 parse 修正；保留已解析结构交人工快速复核）",
                          attempt, engine=result["engine"])
                _log_extract_decision(receipt_id, experiment_id, config, use_grey,
                                       attempt, result["engine"], "gate_reject_fast",
                                       gate_err=gate_err,
                                       token_usage=_tu, cost_hkd=_cost,
                                       elapsed_ms={"extract": state["extract_ms"], "parse": state["parse_ms"], "rag": state["rag_ms"], "audit": state["audit_ms"], "total": 0},
                                       success=True, image_path=image_path,
                                       supplier=state.get("supplier", ""), doc_form=state.get("doc_form", ""))
                # 关键：保留已解析结构供前端渲染与标红复核，不阻断流程
                fallback_data = corr_data if (corrected and 'corr_data' in locals() and corr_data is not None) else result.get("data")
                if fallback_data is not None:
                    state["data"] = fallback_data
                    state["math_problems"] = [gate_err]
                    state["success"] = True
                    try:
                        state["supplier"] = str(getattr(fallback_data, "vendor", "") or state.get("supplier", ""))[:120]
                        _df2 = getattr(fallback_data, "doc_form", "")
                        state["doc_form"] = str(_df2.value if hasattr(_df2, "value") else _df2 or state.get("doc_form", ""))[:60]
                    except Exception:
                        pass
                break
            # 非快速门禁类型（如极少数抽取失败）才带反馈进入下一轮整图重识别
            state["retry_feedback"] = gate_err
            _snapshot(log, "gate_reject", f"门禁拒绝(第{attempt}轮): {gate_err}",
                      attempt, engine=result["engine"])
            _log_extract_decision(receipt_id, experiment_id, config, use_grey,
                                   attempt, result["engine"], "gate_reject",
                                   gate_err=gate_err,
                                   token_usage=_tu, cost_hkd=_cost,
                                   elapsed_ms={"extract": state["extract_ms"], "parse": state["parse_ms"], "rag": state["rag_ms"], "audit": state["audit_ms"], "total": 0},
                                   success=False, image_path=image_path,
                                   supplier=state.get("supplier", ""), doc_form=state.get("doc_form", ""))
            continue  # 门禁不过 → 带反馈重试（仅非快速类型）

        state["data"] = data
        state["contract_error"] = ""
        # 更新 supplier/doc_form 到 state（成功路径最终落盘）
        try:
            state["supplier"] = str(getattr(data, "vendor", "") or state.get("supplier", ""))[:120]
            _df2 = getattr(data, "doc_form", "")
            state["doc_form"] = str(_df2.value if hasattr(_df2, "value") else _df2 or state.get("doc_form", ""))[:60]
        except Exception:
            pass
        state["success"] = True
        _snapshot(log, "extract_ok", f"识别成功(第{attempt}轮) engine={result['engine']}",
                  attempt, engine=result["engine"])
        _snapshot(log, "gates_pass", "契约+算术门禁通过（零 token）",
                  attempt, engine=result["engine"])
        _log_extract_decision(receipt_id, experiment_id, config, use_grey,
                              attempt, result["engine"], "extract_ok",
                              token_usage=_tu, cost_hkd=_cost,
                              elapsed_ms={"extract": state["extract_ms"], "parse": state["parse_ms"], "rag": state["rag_ms"], "audit": state["audit_ms"], "total": 0},
                              success=True, image_path=image_path,
                              supplier=state.get("supplier", ""), doc_form=state.get("doc_form", ""))
        break

    # 透传 receipt_id 到 state 供 _finalize 记忆落盘
    state["receipt_id"] = receipt_id
    # ---- 结果处理 ----
    if state["data"] is None:
        state["status"] = "error"
        state["success"] = False
        if state["contract_error"]:
            _snapshot(log, "error_exit", f"重试耗尽: {state['contract_error']}",
                      state["attempt"])
        else:
            _snapshot(log, "error_exit", f"重试耗尽: {state['last_error'] or '识别失败'}",
                      state["attempt"])
        return _finalize(state, pipeline_start)

    # ---- 交叉审核（可选，失败不阻断）----
    audit = _run_audit(
        image_path, state["data"], config=config, use_grey=use_grey,
    )
    state["audit_result"] = audit
    state["audit_ms"] = round(audit.get("audit_ms", 0) or 0, 1)
    if audit.get("skipped"):
        _snapshot(log, "audit_skip", f"审核跳过: {audit.get('reason')}", state["attempt"])
    elif audit.get("overall_consistent"):
        _snapshot(log, "audit_pass", "交叉审核一致", state["attempt"])
    else:
        _snapshot(log, "audit_flag",
                  f"交叉审核发现 {len(audit.get('discrepancies', []))} 处分歧",
                  state["attempt"])

    # ---- 阶段 2：写 AI 决策日志（audit 结果）----
    # 2.3 并行化：audit 决策写库与返回解耦，改为后台守护线程，避免拖慢管线返回。
    # _log_audit_decision 内部已 try/except，异常不会外溢；daemon 不阻塞进程退出。
    threading.Thread(
        target=_log_audit_decision,
        args=(receipt_id, experiment_id, config, use_grey, audit),
        daemon=True,
    ).start()

    state["status"] = "parsed"
    return _finalize(state, pipeline_start)


def _finalize(state: dict, pipeline_start: float) -> dict:
    """管线收尾：汇总分段计时并输出结构化 RECEIPT_LATENCY 日志（Layer 1.3）+ TOKENS + 记忆落盘。"""
    state["total_ms"] = round((time.time() - pipeline_start) * 1000, 1)
    state["retry_count"] = max(0, state["attempt"] - 1)
    # success 兜底（最严格：error 状态强制 false）
    if state.get("status") == "error":
        state["success"] = False
    elif "success" not in state:
        state["success"] = bool(state.get("data") is not None)
    # 汇总 elapsed_ms 字典
    elapsed_dict = {
        "extract": float(state.get("extract_ms", 0) or 0),
        "parse": float(state.get("parse_ms", 0) or 0),
        "rag": float(state.get("rag_ms", 0) or 0),
        "audit": float(state.get("audit_ms", 0) or 0),
        "total": float(state.get("total_ms", 0) or 0),
    }
    state["elapsed_ms_dict"] = elapsed_dict
    logging.getLogger("supervisor").info(
        "RECEIPT_LATENCY extract_ms=%.1f parse_ms=%.1f rag_ms=%.1f "
        "audit_ms=%.1f retry_count=%d total_ms=%.1f status=%s",
        state.get("extract_ms", 0) or 0,
        state.get("parse_ms", 0) or 0,
        state.get("rag_ms", 0) or 0,
        state.get("audit_ms", 0) or 0,
        state.get("retry_count", 0) or 0,
        state.get("total_ms", 0) or 0,
        state.get("status", ""),
    )
    # 默认部署 root logger 无 handler 且 level=WARNING，logging.info 不输出；
    # 额外 print 到 stdout，uvicorn 会捕获并重定向到其 access/log 流，保证可见。
    print(
        "RECEIPT_LATENCY extract_ms=%.1f parse_ms=%.1f rag_ms=%.1f "
        "audit_ms=%.1f retry_count=%d total_ms=%.1f status=%s" % (
            state.get("extract_ms", 0) or 0,
            state.get("parse_ms", 0) or 0,
            state.get("rag_ms", 0) or 0,
            state.get("audit_ms", 0) or 0,
            state.get("retry_count", 0) or 0,
            state.get("total_ms", 0) or 0,
            state.get("status", ""),
        ),
        flush=True,
    )
    # ---- TOKENS 结构化日志（与 RECEIPT_LATENCY 同时输出）----
    tokens_total = int(state.get("tokens_total", 0) or 0)
    tokens_prompt = int(state.get("tokens_prompt", 0) or 0)
    tokens_completion = int(state.get("tokens_completion", 0) or 0)
    cost_hkd = float(state.get("cost_hkd", 0) or 0)
    success = bool(state.get("success", False))
    # 尝试取真实 engine/model
    _cfg = state.get("config")
    try:
        _engine = str(state.get("engine_name") or getattr(_cfg, "recognition_engine", "") or "opencode")
        if hasattr(_engine, "value"):
            _engine = str(_engine.value)
    except Exception:
        _engine = str(state.get("engine_name") or "opencode")
    try:
        _model = str(getattr(_cfg, "recognition_model", "") or "")
        if state.get("use_grey"):
            _model = str(getattr(_cfg, "grey_recognition_model", _model) or _model)
    except Exception:
        _model = ""
    logging.getLogger("supervisor").info(
        "TOKENS prompt=%d completion=%d total=%d cost_hkd=%.6f success=%s engine=%s model=%s",
        tokens_prompt, tokens_completion, tokens_total, cost_hkd, success, _engine, _model,
    )
    print(
        "TOKENS prompt=%d completion=%d total=%d cost_hkd=%.6f success=%s engine=%s model=%s" % (
            tokens_prompt, tokens_completion, tokens_total, cost_hkd, success, _engine, _model,
        ),
        flush=True,
    )
    # ---- 文件层记忆落盘：artifacts/memory/parse_log.jsonl（并发安全）----
    try:
        with _MEMORY_LOCK:
            _path = _memory_log_path()
            os.makedirs(os.path.dirname(_path), exist_ok=True)
            rec = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "receipt_id": state.get("receipt_id"),
                "image_path": str(state.get("image_path", "") or "")[:500],
                "supplier": str(state.get("supplier", "") or "")[:120],
                "doc_form": str(state.get("doc_form", "") or "")[:60],
                "engine": str(_engine or "")[:60],
                "model": str(_model or "")[:120],
                "tokens_prompt": tokens_prompt,
                "tokens_completion": tokens_completion,
                "tokens_total": tokens_total,
                "cost_hkd": cost_hkd,
                "elapsed_ms": elapsed_dict,
                "success": success,
                "error_msg": str(state.get("contract_error") or state.get("last_error") or "")[:500],
                "use_grey": 1 if state.get("use_grey") else 0,
            }
            with open(_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        logging.getLogger("supervisor").warning(f"记忆落盘失败: {e}")
    return state
