# -*- coding: utf-8 -*-
from __future__ import annotations
"""识别管线编排：LangChain 模型封装 + 确定性流程（线性 + 重试阶梯）。

- 流程：extract（VLM 识别）→ contract gate（契约门禁）→ math gate（算术门禁）
        → audit（交叉审核）→ done
- 重试阶梯：仅"引擎瞬时故障"整图重跑（上限 MAX_RETRY，可经 EngineConfig.max_retry_rounds 调整）；
  门禁快速反馈 / 输出质量失败 / 确定性失败均在当轮短路，不白跑后续轮次；仍败 → error
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
from app import db

# =================================================================
# 重试阶梯策略（T11 / P10）
# -----------------------------------------------------------------
# MAX_RETRY＝整图重跑的最大轮数（含首轮），默认值。逐类说明"谁才会真正用到
# 第 2/3 轮"，以免后人误调（下调前务必先看这条注释与 resolve_max_retry）：
#
#   1. 引擎瞬时故障（上游 5xx / DashScope 50507 / 调用超时）→ **重跑有意义**。
#      这是本额度唯一"设计内"的消耗者：服务商侧抖动换一轮确实可能成功
#      （实测 SF 每 3 次有 2 次撞超时，见问题清单 P9）。
#   2. 契约/算术等门禁失败（快速反馈类）→ 走下方 gate_reject_fast 分支 break，
#      不做整图重跑，**不消耗额度**（纯文本解析级修正已给过答案）。
#   3. 输出质量失败（JSON 不可解析 / 契约不过，见 _is_output_quality_error）→
#      下一轮 prompt 与首轮逐字相同（error 分支不设 retry_feedback、失败轮不补
#      RAG 先验），temperature≈0 下近确定性复现，重跑无法改变结果 →
#      走 output_reject_fast 短路，**不消耗额度**。
#   4. 确定性失败（图片不可解码 / 鉴权或参数配置错误）→ 任何引擎都不可能成功 →
#      走 deterministic_error 短路，**不消耗额度**。
#
# 结论：MAX_RETRY 保持 3。经上述三点收敛后，只剩"上游间歇故障"会消耗第 2/3 轮，
# 而这一类正是需要重试的；把它下调到 1 会让上游抖动的单据直接失败。
MAX_RETRY = 3
# 上限护栏：即便管理台把 max_retry_rounds 填得很大，也不允许无限重跑（防长期空转 / 成本失控）。
MAX_RETRY_LIMIT = 5
# 引擎异常归类中"重跑同一配置不可能成功"的确定性类别（归类见 llm._engine_error_category）：
#   auth  鉴权失败（HTTP 401/403）——同一 key 重试仍是 401/403；
#   param 参数错误（HTTP 400）——模型名 / base_url 不被接受，重试同一配置仍是 400。
# 上游 upstream（5xx / 50507）**不在其列**：它是服务商侧瞬时故障，正是重试阶梯要覆盖的场景。
_NON_RETRYABLE_ENGINE_CATEGORIES = frozenset({"auth", "param"})

# 成本兜底单价（元/百万 token，仅输入侧）：只在 _calc_cost_hkd 与 _resolve_token_price
# 双双失败时使用，属「最后一道估算」，不是档位表口径。使用它时一律落 cost_estimated 标记，
# 避免该数字被当成精确成本喂给 guardian 的成本护栏与大盘。
_DEFAULT_COST_IN_PRICE = 2.2


def resolve_max_retry(config=None) -> int:
    """解析本次管线的整图重跑上限：EngineConfig.max_retry_rounds 优先，缺省 MAX_RETRY。

    why 钳制到 [1, MAX_RETRY_LIMIT] 而非直接采用：该值来自 DB / 管理台，可能是
    0、负数或非整数——0 会让 while 只跑 0 轮（data 恒为 None、单据直接失败），
    离谱大值则会无限重跑烧钱。类型非法一律回落 MAX_RETRY。
    """
    v = getattr(config, "max_retry_rounds", None)
    try:
        iv = int(v)
    except (TypeError, ValueError):
        return MAX_RETRY
    if iv < 1:
        return 1
    return min(iv, MAX_RETRY_LIMIT)


def _is_fast_feedback_gate(gate_err: str) -> bool:
    """门禁错误是否属"快速反馈类"（契约 / 算术等确定性结构问题）。

    why 集中为单一函数：这些失败一文本地修正就能出结论（不重读原图），整图重跑
    等于把一次 40s 级 VLM 调用浪费在同一个确定性结论上。命中即 gate_reject_fast，
    保留已解析结构供人工复核，不再进下一轮。
    """
    if not gate_err:
        return False
    return ("算术门禁" in gate_err or "契约" in gate_err
            or "总额" in gate_err or "明细为空" in gate_err)


def _is_output_quality_error(result: dict) -> bool:
    """extract 返回的失败是否属"输出质量"（JSON / 契约不过），而非引擎传输失败。

    判据：引擎调用失败的三条返回路径都带 error_category（upstream/auth/param/decode，
    未归类时为空串）；而 _parse_to_receipt 之后的 JSON / 契约失败走 extract_receipt
    的最终返回，不带该键。故"有 error 但无 error_category"＝输出质量失败。

    why 要单独区分：此类失败时 supervisor 下一轮传给模型的 prompt 与首轮**逐字相同**
    （error 分支从不设 retry_feedback，失败轮也不会补 RAG 先验），在 temperature≈0
    下近乎确定性复现——整图重跑既改变不了结果，又白烧 1-2 轮 VLM。
    """
    if not result.get("error"):
        return False
    return "error_category" not in result


def _is_deterministic_engine_error(result: dict) -> bool:
    """引擎失败是否属确定性（换引擎 / 重跑同一配置都不可能成功）。

    两类：显式 deterministic_error 标记（图片解码失败，extract_chain 置位）；
    以及 auth / param 归类的配置型失败（P9 归类，见 _NON_RETRYABLE_ENGINE_CATEGORIES）。
    上游 upstream 归类与未归类异常（本地 CLI 抖动）不算确定性，仍需重试。
    """
    if result.get("deterministic_error"):
        return True
    return str(result.get("error_category") or "") in _NON_RETRYABLE_ENGINE_CATEGORIES

# T6 Gap A5：低置信回流阈值（低于该值的识别结果回流为评测候选）。
# T10 收口：本常量仅作 settings 缺省值，运行时经 settings_service 键
# 'eval_candidate_low_confidence' 实时读取（eval_candidate_low_confidence_threshold()）。
EVAL_CANDIDATE_LOW_CONFIDENCE = 0.6


def eval_candidate_low_confidence_threshold() -> float:
    """低置信回流阈值（settings 实时读取，缺省 EVAL_CANDIDATE_LOW_CONFIDENCE）。"""
    try:
        from app.services import settings_service
        return settings_service.get_float(
            "eval_candidate_low_confidence", EVAL_CANDIDATE_LOW_CONFIDENCE)
    except Exception:
        return EVAL_CANDIDATE_LOW_CONFIDENCE

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


def _track_guard(event_type, err, receipt_id, attempt):
    """门禁轻量埋点（规范事件 #5/#14）：只记摘要，不重复存 token/cost。"""
    try:
        db.log_user_event(
            account_id="system", session_id="",
            event_type=event_type,
            receipt_id=int(receipt_id) if receipt_id else None,
            properties={"is_valid": err is None,
                        "reject_reason": (err or "")[:200],
                        "attempt": int(attempt or 1)},
        )
    except Exception:
        pass


def _run_gates(data: ReceiptData, receipt_id=None, attempt=1):
    """契约门禁 + 算术门禁（确定性代码，零 token）。"""
    from app.services.contract import validate_contract
    data, err = validate_contract(data.model_dump())
    _track_guard("contract_guard_checked", err, receipt_id, attempt)
    if err:
        return None, err
    problems = math_engine.validate_and_report(data)
    math_err = ("算术门禁: " + "; ".join(problems)) if problems else None
    _track_guard("math_guard_checked", math_err, receipt_id, attempt)
    if problems:
        return None, math_err
    return data, None


# -----------------------------------------------------------------
# T6 Gap A5：线上失败信号 -> 评测候选（回流钩子，不阻断主链路）
# -----------------------------------------------------------------
def _candidate_gt_from_data(data):
    """从识别结果提取 AI 候选 GT（run_eval compare 可消费的别名形态）。

    鸭子类型兼容 ReceiptData 与测试替身；任何异常返回 {}（不阻断）。
    gt_source_model 由调用方补登（记录产出该候选的 AI 引擎名）。
    """
    if data is None:
        return {}
    try:
        items = []
        for it in (getattr(data, "items", None) or []):
            items.append({
                "name": str(getattr(it, "name", "") or ""),
                "quantity": float(getattr(it, "qty", 0) or 0),
                "unit": str(getattr(it, "unit", "") or ""),
                "unit_price": float(getattr(it, "unit_price", 0) or 0),
                "amount": float(getattr(it, "amount", 0) or 0),
            })
        doc_form = getattr(data, "doc_form", "")
        doc_form = doc_form.value if hasattr(doc_form, "value") else str(doc_form or "")
        return {
            "supplier_name": str(getattr(data, "vendor", "") or ""),
            "date": str(getattr(data, "date", "") or ""),
            "total_amount": float(getattr(data, "total", 0) or 0),
            "items": items,
            "doc_form": doc_form,
        }
    except Exception:
        return {}


def maybe_create_eval_candidate(receipt_id, reason, doc_form="", confidence=None,
                                ai_candidate=None, note="", tenant_id=None):
    """单条回流钩子：失败仅 logger.warning，绝不抛出（AC：不阻断识别主链路）。

    receipt_id=None（run_pipeline 直接调用/冒烟场景）跳过；
    幂等由 db.create_eval_candidate 保证（同单同 reason 不重复建）。
    返回 (candidate_id, created)；任何异常返回 (None, False)。
    """
    if receipt_id is None:
        return None, False
    try:
        from app import db as _db
        if tenant_id is None:
            rc = _db.get_receipt_row(int(receipt_id))
            tenant_id = getattr(rc, "tenant_id", None) if rc is not None else None
        cid, created = _db.create_eval_candidate(
            receipt_id=int(receipt_id), reason=reason, tenant_id=tenant_id,
            doc_form=doc_form, confidence=confidence,
            ai_candidate=ai_candidate, note=note)
        if created:
            logging.getLogger("supervisor").info(
                "EVAL_CANDIDATE created id=%s receipt=%s reason=%s",
                cid, receipt_id, reason)
        elif cid is None:
            # L3 候选池卫生：create_eval_candidate 返回 (None, False) 即超限拒绝
            logging.getLogger("supervisor").warning(
                "评测候选池已达上限，候选未创建(不阻断主链路): reason=%s receipt=%s",
                reason, receipt_id)
        return cid, created
    except Exception as e:
        logging.getLogger("supervisor").warning(
            f"评测候选回流失败(不阻断主链路): reason={reason} receipt={receipt_id}: {e}")
        return None, False


# L3/T6 候选池卫生：audit_discrepancy 严重度门槛。
# 差异条数达到该值视为严重；不足时仅在含总额类差异（supplier/total/amount
# 关键词）时才回流建候选，单条轻微差异（如币种缺失）不建候选。
# T10 收口：本常量仅作 settings 缺省值，运行时经 settings_service 键
# 'audit_discrepancy_severe_min_count' 实时读取。
AUDIT_DISCREPANCY_SEVERE_MIN_COUNT = 2
_AUDIT_DISCREPANCY_TOTAL_KEYWORDS = ("supplier", "total", "amount")


def _audit_discrepancy_severe_min_count() -> int:
    try:
        from app.services import settings_service
        return settings_service.get_int(
            "audit_discrepancy_severe_min_count", AUDIT_DISCREPANCY_SEVERE_MIN_COUNT)
    except Exception:
        return AUDIT_DISCREPANCY_SEVERE_MIN_COUNT


def _audit_discrepancy_severe(discrepancies) -> bool:
    """审核分歧严重度判定（L3）：条数达标 或 含总额类差异（supplier/total/amount）。

    discrepancies 元素为 {"field": ..., "issue": ...}（兼容 dict 与其他形态，
    非 dict 退化为整串文本匹配）。任何异常按不严重处理（不建候选，不阻断）。
    """
    try:
        if len(discrepancies) >= _audit_discrepancy_severe_min_count():
            return True
        for d in discrepancies:
            if isinstance(d, dict):
                text = " ".join(str(d.get(k, "") or "") for k in ("field", "issue"))
            else:
                text = str(d or "")
            tl = text.lower()
            if any(k in tl for k in _AUDIT_DISCREPANCY_TOTAL_KEYWORDS):
                return True
    except Exception:
        return False
    return False


def _reflow_from_state(state: dict):
    """管线收尾回流判定（T6 Gap A5）：三类信号各查一次，幂等去重交给 db 层。

    - low_confidence: data.confidence 非空且 < EVAL_CANDIDATE_LOW_CONFIDENCE
    - gate_reject:    state.contract_error 非空（门禁拒绝/快速反馈最终态）
    - audit_discrepancy: audit.discrepancies 非空且达严重度门槛（L3 候选池卫生）
    user_edit（人工保存差异）在 api_receipts.save_edited 路径挂钩。
    """
    rid = state.get("receipt_id")
    if rid is None:
        return
    data = state.get("data")
    gt = _candidate_gt_from_data(data)
    engine = str(state.get("engine_name") or "")
    doc_form = str(state.get("doc_form") or "")
    if gt:
        gt["gt_source_model"] = engine
    conf = getattr(data, "confidence", None) if data is not None else None
    # 1) 低置信
    if conf is not None:
        try:
            _low_conf_th = eval_candidate_low_confidence_threshold()
            if float(conf) < _low_conf_th:
                maybe_create_eval_candidate(
                    rid, "low_confidence", doc_form=doc_form, confidence=conf,
                    ai_candidate=gt,
                    note="confidence=%s < %s" % (conf, _low_conf_th))
        except (TypeError, ValueError):
            pass
    # 2) 门禁拒绝（contract_error 保留最终门禁错误摘要）
    gate_err = str(state.get("contract_error") or "")
    if gate_err:
        maybe_create_eval_candidate(
            rid, "gate_reject", doc_form=doc_form, confidence=conf,
            ai_candidate=gt, note=gate_err[:200])
    # 3) 审核分歧（L3 严重度门槛：单条轻微差异不足以回流建候选）
    audit = state.get("audit_result") or {}
    discrepancies = audit.get("discrepancies") if isinstance(audit, dict) else None
    if isinstance(discrepancies, list) and discrepancies \
            and _audit_discrepancy_severe(discrepancies):
        maybe_create_eval_candidate(
            rid, "audit_discrepancy", doc_form=doc_form, confidence=conf,
            ai_candidate=gt,
            note="discrepancies=%d" % len(discrepancies))


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
        _ai_engine = str(getattr(config, "audit_engine", "") or "openai")
        _ai_model = str(getattr(config, "audit_model", "") or "")
        if use_grey:
            _ai_engine = str(getattr(config, "grey_audit_engine", _ai_engine) or _ai_engine)
            _ai_model = str(getattr(config, "grey_audit_model", _ai_model) or _ai_model)
        from app import db as _db
        # 最严格：audit 亦记录 token 占位（text 审核零 token，VLM 审核可为实际值；当前 text 模式记 0）
        _audit_tu = audit.get("token_usage") or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        _audit_estimated = False
        _audit_reason = ""
        try:
            from app.llm import _normalize_token_usage
            _audit_tu = _normalize_token_usage(_audit_tu)
        except Exception as e:
            _audit_tu = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            _audit_estimated = True
            _audit_reason = "token_usage_unparsed"
            logging.getLogger("supervisor").warning(
                "审核 token 归一失败(收据=%s 原值=%r)，记 0 并标记成本为估算值: %s",
                receipt_id, audit.get("token_usage"), e)
        try:
            from app.llm import _calc_cost_hkd
            # 传当时生效的审核模型（_ai_model 已含灰测分支）用于档位解析：
            # 不传会按默认识别引擎 omni 档计，对 SF GLM 审核腿高估约 2.2 倍。
            _audit_cost = _calc_cost_hkd(_audit_tu, model=_ai_model)
        except Exception as e:
            _audit_cost = 0.0
            _audit_estimated = True
            _audit_reason = _audit_reason or "cost_calc_failed"
            logging.getLogger("supervisor").warning(
                "审核成本计算失败(收据=%s model=%s tokens=%s)，记 0 并标记为估算值: %s",
                receipt_id, _ai_model, _audit_tu, e)
        if not _audit_estimated and int(_audit_tu.get("total_tokens", 0) or 0) == 0:
            # 当前审核为 text 模式，本就无 token -> 属「无测量值」，与识别腿口径一致
            _audit_estimated = True
            _audit_reason = _audit_reason or "no_token_usage"
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
                "cost_estimated": 1 if _audit_estimated else 0,
                "cost_estimated_reason": _audit_reason,
                "elapsed_ms": _audit_elapsed,
                "success": bool(audit.get("overall_consistent")) if audit.get("skipped") is False else False,
            },
        )
    except Exception as e:
        logging.getLogger("supervisor").warning(f"记录交叉审核 AI 决策失败: {e}")


def _log_extract_decision(receipt_id, experiment_id, config, use_grey,
                          attempt, engine, status, gate_err="",
                          token_usage=None, cost_hkd=None, elapsed_ms=None,
                          success=None, image_path="", supplier="", doc_form="",
                          cost_estimated_in=None, cost_estimated_reason_in=""):
    """U-2：每轮 extract 决策落库（AI 决策履历断链修复）。

    - receipt_id=None（run_pipeline 直接调用/冒烟场景）→ 跳过写库不抛错
    - 写失败仅记 warning，绝不影响识别主链路（AC-E2）
    - ai_value 紧凑 JSON ≤500 字符，超长截断加 …(truncated) 尾标；
      gate_err 仅拒绝/失败轮携带，≤200 字摘要
    - 最严格记忆落盘：ai_value 与 extra 均追加 {tokens_prompt, tokens_completion, tokens_total, cost_hkd, elapsed_ms, success, image_path, supplier, doc_form}
      token 来自 ChatResult.generations[0].message.response_metadata['token_usage']；
      cost 按 llm._calc_cost_hkd 的按模型分档单价计算（见 llm._REC_TOKEN_PRICE_TIERS：
      omni ¥2.2/¥13.3、vl-flash ¥0.15/¥1.50、GLM-4.5V ¥1.0/¥6.0 每百万 token），
      传入 config 上当时生效的识别模型名，避免被按默认 omni 档静默高估。
    """
    if receipt_id is None:
        # 始终返回 dict：调用方用 state.update(...) 透传成本可信度标记，返回 None 会炸
        return {}
    # 归一化 token
    tu = token_usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    # 上游给了值却解析不出来的字段：静默变 0 会让成本护栏失去判别力，必须留痕
    _token_unparsed = []
    if not isinstance(tu, dict):
        _token_unparsed.append("token_usage_not_dict")
        tu = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def _to_int(v, label=""):
        try:
            return int(v or 0)
        except Exception:
            try:
                return int(float(v or 0))
            except Exception:
                if v not in (None, "", 0):
                    _token_unparsed.append(label or type(v).__name__)
                return 0

    tokens_prompt = _to_int(tu.get("prompt_tokens", tu.get("input_tokens", 0)), "prompt_tokens")
    tokens_completion = _to_int(tu.get("completion_tokens", tu.get("output_tokens", 0)), "completion_tokens")
    tokens_total = _to_int(tu.get("total_tokens", 0) or (tokens_prompt + tokens_completion), "total_tokens")
    # 成本可信度标记：True = 这个 cost_hkd 不是按「真实模型单价 × 真实 token」算出来的，
    # 不可用于护栏/大盘的精确判定。供 guardian 与大盘区分「真实 0 成本」（本地 CLI 无 token）
    # 与「拿不到 token / 算不出来」这两种此刻数值相同、含义完全不同的情况。
    _cost_estimated = False
    _cost_reason = ""
    # 当时生效的识别模型名（与下方决策落库的 model 字段同源）。
    # why：_calc_cost_hkd 不传 model 会一律按默认 omni 档计，用 vl-flash 时高估约 11 倍。
    _rec_model = str(getattr(config, "recognition_model", "") or "")
    if use_grey:
        _rec_model = str(getattr(config, "grey_recognition_model", _rec_model) or _rec_model)
    # 成本计算
    if cost_hkd is None:
        try:
            from app.llm import _calc_cost_hkd
            cost_hkd = _calc_cost_hkd(
                {"prompt_tokens": tokens_prompt, "completion_tokens": tokens_completion, "total_tokens": tokens_total},
                model=_rec_model)
        except Exception as e:
            _cost_estimated = True
            _cost_reason = "cost_calc_failed"
            logging.getLogger("supervisor").warning(
                "成本计算失败(收据=%s engine=%s model=%s tokens_total=%s)，转兜底估算: %s",
                receipt_id, engine, _rec_model, tokens_total, e)
            # 兜底估算：只按该模型档位的输入单价计（对输出占比高的模型会低估），
            # _resolve_token_price 也失败时用档位表默认输入单价兜底 —— 两者都不可信，
            # 故一律打 cost_estimated 标记，不冒充精确值。
            try:
                from app.llm import _resolve_token_price
                _in_price = _resolve_token_price(_rec_model)[0]
            except Exception as e2:
                _in_price = _DEFAULT_COST_IN_PRICE
                _cost_reason = "price_table_failed"
                logging.getLogger("supervisor").warning(
                    "成本单价表解析失败(model=%s)，回落到默认输入单价 %s（仅估算兜底）: %s",
                    _rec_model, _DEFAULT_COST_IN_PRICE, e2)
            cost_hkd = round(tokens_total * _in_price / 1_000_000, 6) if tokens_total else 0.0
    else:
        try:
            cost_hkd = round(float(cost_hkd or 0), 6)
        except Exception as e:
            cost_hkd = 0.0
            _cost_estimated = True
            _cost_reason = "cost_coerce_failed"
            logging.getLogger("supervisor").warning(
                "成本值归一失败(收据=%s 原值=%r)，记 0 并标记为估算值: %s", receipt_id, cost_hkd, e)
    # 上游（extract_chain）已判定为估算的成本：一律继承，不因本地算出一个非 0 值就丢掉该标记
    if cost_estimated_in and not _cost_estimated:
        _cost_estimated = True
        _cost_reason = cost_estimated_reason_in or "upstream_cost_fallback"
    # 交叉校验：有 token 却算出 0 成本 -> 成本链路在更上游（extract_chain）被静默吞掉了。
    # 这是 cost_estimated 最关键的触发条件：它让「真实成本上涨」不会以 0 的形式冒充正常值。
    if not _cost_estimated and tokens_total > 0 and not cost_hkd:
        _cost_estimated = True
        _cost_reason = "cost_zero_with_tokens"
        logging.getLogger("supervisor").warning(
            "有 token(%s) 但成本为 0(收据=%s engine=%s model=%s) -> 成本链路异常，"
            "该值标记为估算值，不得用于护栏判定",
            tokens_total, receipt_id, engine, _rec_model)
    # 没有任何 token 测量值 -> 成本不可验证。本地 CLI 引擎无 token 计费也落在这里：
    # 它的「0 成本」是「没有测量值」而非「测量结果为 0」，同样不该被当成精确成本。
    if not _cost_estimated and tokens_total == 0:
        _cost_estimated = True
        _cost_reason = "no_token_usage"
        logging.getLogger("supervisor").warning(
            "无可用 token 用量(收据=%s engine=%s 原值=%r 未解析字段=%s) -> 成本记 0 并标记为估算值",
            receipt_id, engine, token_usage, _token_unparsed or ["none"])
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
        # 成本可信度：1 = cost_hkd 为估算/不可用（拿不到 token、成本链路异常、走了兜底单价），
        # 0 = 按真实模型单价 × 真实 token 算出。guardian 的成本护栏与大盘据此区分
        # 「真实 0 成本」（本地 CLI 无 token）与「算不出来」，两者数值相同、含义不同。
        "cost_estimated": 1 if _cost_estimated else 0,
        "cost_estimated_reason": _cost_reason,
        "elapsed_ms": elapsed,
        "success": bool(success),
        "image_path": str(image_path or "")[:500],
        "supplier": str(supplier or "")[:120],
        "doc_form": str(doc_form or "")[:60],
    }
    try:
        _model = _rec_model
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
    # 回传成本可信度，供调用方透传到 state（Job 与前端据此显示「成本为估算值」）
    return {"cost_estimated": bool(_cost_estimated),
            "cost_estimated_reason": _cost_reason}


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
        "engine_name": "openai",
        "log": log,
        "retry_feedback": "",
        "contract_error": "",
        "math_problems": [],
        # P14/P15：门禁快速反馈留下的警告（非空表示"带警告通过"）
        "gate_warnings": [],
        # T11：输出质量失败（JSON/契约不过）的快速反馈标记，非空表示已短路不重跑
        "output_reject_fast": False,
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
        # 成本可信度（见 _log_extract_decision 的 cost_estimated）：随管线透传给 Job/前端
        "cost_estimated": False,
        "cost_estimated_reason": "",
        "success": False,
        "supplier": "",
        "doc_form": "",
    }

    # ---- 重试阶梯：识别 + 门禁，最多 max_rounds 轮（默认 MAX_RETRY，见文件头策略注释）----
    max_rounds = resolve_max_retry(config)
    while state["attempt"] < max_rounds:
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
        # 上游（extract_chain）成本链路的估算标记：它已经在更上游判过「这个成本是不是按真实档位算的」，
        # 这里原样透传下去，避免「上游标了、下游又丢掉」的两次失真。
        _cost_est_in = result.get("cost_estimated")
        _cost_why_in = str(result.get("cost_estimated_reason") or "")
        try:
            _cost = float(_cost or 0)
        except Exception as e:
            _cost = 0.0
            _cost_est_in = True
            _cost_why_in = "cost_coerce_failed"
            logging.getLogger("supervisor").warning(
                "上游成本值无法转为数值(原值=%r)，记 0 并标记为估算值: %s",
                result.get("cost_hkd"), e)
        state["engine_name"] = str(result.get("engine") or state.get("engine_name") or "openai")
        # Job/前端可见：本轮成本是否为估算值（供大盘区分「真实 0 成本」与「算不出来」）
        if _cost_est_in:
            state["cost_estimated"] = True
            state["cost_estimated_reason"] = _cost_why_in or "upstream_cost_fallback"
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
            state["fallback_engine"] = result.get("fallback_engine", "dashscope")
            _snapshot(log, "engine_fallback", state["fallback_reason"], attempt,
                      engine=state["fallback_engine"])
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
            state.update(_log_extract_decision(receipt_id, experiment_id, config, use_grey,
                                      attempt, result["engine"], "extract_fail",
                                      gate_err=result["error"],
                                      token_usage=_tu, cost_hkd=_cost,
                                      elapsed_ms={"extract": state["extract_ms"], "parse": state["parse_ms"], "rag": state["rag_ms"], "audit": state["audit_ms"], "total": 0},
                                      success=False, image_path=image_path,
                                      supplier=state.get("supplier", ""), doc_form=state.get("doc_form", ""),
                                      cost_estimated_in=_cost_est_in,
                                      cost_estimated_reason_in=_cost_why_in))
            if result.get("fallback_failed") or result.get("fallback_triggered") or "fallback" in str(result.get("engine", "")):
                # 主模型与备用模型均失败时，不再盲目重试，立即快速返回供前端自动切入手工输入界面。
                # 先于确定性判定：保持既有语义（Job 层 fallback_failed 透传、前端据此提示）。
                state["fallback_failed"] = True
                state["fallback_triggered"] = True
                break
            if _is_deterministic_engine_error(result):
                # T11：确定性失败（图片解码失败 N4；或 auth/param 配置型失败 P9）——
                # 换引擎/重跑都不可能成功，立即结束重试阶梯，避免白跑剩余轮次、打重复日志。
                # last_error 已在上方记录可读原因，最终仍走 "data is None" 既有 error
                # 收口路径（status=error / success=False），Job 层照常取到错误文案。
                state["deterministic_error"] = True
                _snapshot(log, "deterministic_fail",
                          f"确定性失败，不重试(第{attempt}轮): {result['error']}",
                          attempt, engine=result["engine"])
                break
            if _is_output_quality_error(result):
                # T11：输出质量失败（JSON 不可解析 / 契约不过）——下一轮 prompt 与首轮
                # 逐字相同（error 分支不设 retry_feedback、失败轮不补 RAG 先验），
                # temperature≈0 下近确定性复现，整图重跑无意义。快速反馈交人工复核，
                # 不再白烧 1-2 轮 VLM（此前会跑满上限才失败）。
                state["output_reject_fast"] = True
                _snapshot(log, "output_reject_fast",
                          f"输出质量快速反馈(第{attempt}轮): {result['error']}"
                          f"（同一 prompt 重跑无意义，已保留原始输出交人工复核）",
                          attempt, engine=result["engine"])
                break
            continue  # 引擎瞬时故障（上游 5xx / 超时等）→ 下一轮重试

        # 首轮补检索（真飞轮）：无 hint 时 VLM 已识别出供应商 → 读 VendorMemory 补上下文，
        # 供后续 gate_reject 重试轮注入（异常置空，不阻断识别线程）
        if not state["vendor_context"] and result["data"] is not None:
            try:
                r_start = time.time()
                ctx = retrieve_context(
                    getattr(result["data"], "vendor", "") or "") or ""
                state["rag_ms"] = round(state["rag_ms"] + (time.time() - r_start) * 1000, 1)
                state["vendor_context"] = ctx
                if ctx:
                    try:
                        db.log_user_event(
                            account_id="system", session_id="",
                            event_type="rag_hit",
                            receipt_id=int(receipt_id) if receipt_id else None,
                            properties={"context_len": len(str(ctx))})
                    except Exception:
                        pass
            except Exception:
                state["vendor_context"] = ""

        data, gate_err = _run_gates(result["data"], receipt_id, attempt)
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
                    corr_data, corr_gate = _run_gates(corr_data, receipt_id, attempt)
                if corr_gate is None:
                    # 修正成功 → 直接采用，进入审核，不再整图重识别
                    state["raw"] = corrected
                    _snapshot(log, "parse_correct",
                              f"解析级修正通过门禁(第{attempt}轮): {gate_err}",
                              attempt, engine=result["engine"])
                    state.update(_log_extract_decision(receipt_id, experiment_id, config, use_grey,
                                               attempt, result["engine"], "parse_ok",
                                               token_usage=_tu, cost_hkd=_cost,
                                               elapsed_ms={"extract": state["extract_ms"], "parse": state["parse_ms"], "rag": state["rag_ms"], "audit": state["audit_ms"], "total": 0},
                                               success=True, image_path=image_path,
                                               supplier=state.get("supplier", ""), doc_form=state.get("doc_form", ""),
                                               cost_estimated_in=_cost_est_in,
                                               cost_estimated_reason_in=_cost_why_in))
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
            # 修正未通过 → 快速反馈路径：contract/math 失败不再重调 VLM（避免整图重跑），直接快速反馈
            if _is_fast_feedback_gate(gate_err):
                # 快速反馈：记录 gate_reject_fast，保留已解析结构交人工快速复核（不阻断、不整图重识别）
                _snapshot(log, "gate_reject_fast",
                          f"门禁快速反馈(第{attempt}轮): {gate_err}（已尝试 parse 修正；保留已解析结构交人工快速复核）",
                          attempt, engine=result["engine"])
                state.update(_log_extract_decision(receipt_id, experiment_id, config, use_grey,
                                           attempt, result["engine"], "gate_reject_fast",
                                           gate_err=gate_err,
                                           token_usage=_tu, cost_hkd=_cost,
                                           elapsed_ms={"extract": state["extract_ms"], "parse": state["parse_ms"], "rag": state["rag_ms"], "audit": state["audit_ms"], "total": 0},
                                           image_path=image_path,
                                           supplier=state.get("supplier", ""), doc_form=state.get("doc_form", ""),
                                           cost_estimated_in=_cost_est_in,
                                           cost_estimated_reason_in=_cost_why_in))
                # P14/P15：门禁未通过时不得静默当「解析成功」——留痕门禁警告，
                # 最终状态由收尾处依 gate_warnings 置为 parsed_with_warnings（不再置 success=True）。
                state["gate_warnings"] = [str(gate_err)]
                # 关键：保留已解析结构供前端渲染与标红复核，不阻断流程
                fallback_data = corr_data if (corrected and 'corr_data' in locals() and corr_data is not None) else result.get("data")
                if fallback_data is not None:
                    state["data"] = fallback_data
                    state["math_problems"] = [gate_err]
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
            state.update(_log_extract_decision(receipt_id, experiment_id, config, use_grey,
                                       attempt, result["engine"], "gate_reject",
                                       gate_err=gate_err,
                                       token_usage=_tu, cost_hkd=_cost,
                                       elapsed_ms={"extract": state["extract_ms"], "parse": state["parse_ms"], "rag": state["rag_ms"], "audit": state["audit_ms"], "total": 0},
                                       success=False, image_path=image_path,
                                       supplier=state.get("supplier", ""), doc_form=state.get("doc_form", ""),
                                       cost_estimated_in=_cost_est_in,
                                       cost_estimated_reason_in=_cost_why_in))
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
        state.update(_log_extract_decision(receipt_id, experiment_id, config, use_grey,
                                  attempt, result["engine"], "extract_ok",
                                  token_usage=_tu, cost_hkd=_cost,
                                  elapsed_ms={"extract": state["extract_ms"], "parse": state["parse_ms"], "rag": state["rag_ms"], "audit": state["audit_ms"], "total": 0},
                                  success=True, image_path=image_path,
                                  supplier=state.get("supplier", ""), doc_form=state.get("doc_form", ""),
                                  cost_estimated_in=_cost_est_in,
                                  cost_estimated_reason_in=_cost_why_in))
        break

    # 透传 receipt_id 到 state 供 _finalize 记忆落盘
    state["receipt_id"] = receipt_id
    # ---- 结果处理 ----
    if state["data"] is None:
        state["status"] = "error"
        state["success"] = False
        if state.get("deterministic_error"):
            # N4：确定性失败已主动结束阶梯（非耗尽重试），日志文案如实区分，
            # 避免把"本地图片解码失败"误报成"重试耗尽"而误导排查方向。
            _snapshot(log, "error_exit", f"确定性失败(不重试): {state['last_error'] or '识别失败'}",
                      state["attempt"])
        elif state.get("output_reject_fast"):
            # T11：输出质量失败已主动短路（重跑同一 prompt 无意义），文案同样不得
            # 写成"重试耗尽"，否则会让人以为还有可恢复空间。
            _snapshot(log, "error_exit",
                      f"输出质量快速反馈(不重试): {state['last_error'] or '识别失败'}",
                      state["attempt"])
        elif state["contract_error"]:
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

    # P14/P15：门禁快速反馈虽保留了识别结构（供人工复核），但状态必须显式标注
    # 「带警告」，避免下游（落库/列表/详情）把它当成干净的 parsed。
    state["status"] = "parsed_with_warnings" if state.get("gate_warnings") else "parsed"
    return _finalize(state, pipeline_start)


def _finalize(state: dict, pipeline_start: float) -> dict:
    """管线收尾：汇总分段计时并输出结构化 RECEIPT_LATENCY 日志（Layer 1.3）+ TOKENS + 记忆落盘。"""
    state["total_ms"] = round((time.time() - pipeline_start) * 1000, 1)
    state["retry_count"] = max(0, state["attempt"] - 1)
    # success 兜底（最严格：error 状态强制 false）
    if state.get("status") == "error":
        state["success"] = False
    elif state.get("status") == "parsed_with_warnings":
        # P14/P15：门禁未通过的"带警告通过"不算 clean success —— 数据虽已落库
        # 供人工复核，但日志/看板不应把它统计为一次干净的成功解析。
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
        _engine = str(state.get("engine_name") or getattr(_cfg, "recognition_engine", "") or "openai")
        if hasattr(_engine, "value"):
            _engine = str(_engine.value)
    except Exception:
        _engine = str(state.get("engine_name") or "openai")
    try:
        _model = str(getattr(_cfg, "recognition_model", "") or "")
        if state.get("use_grey"):
            _model = str(getattr(_cfg, "grey_recognition_model", _model) or _model)
    except Exception:
        _model = ""
    cost_estimated = bool(state.get("cost_estimated"))
    cost_estimated_reason = str(state.get("cost_estimated_reason") or "")
    logging.getLogger("supervisor").info(
        "TOKENS prompt=%d completion=%d total=%d cost_hkd=%.6f cost_estimated=%s(%s) success=%s engine=%s model=%s",
        tokens_prompt, tokens_completion, tokens_total, cost_hkd,
        cost_estimated, cost_estimated_reason or "-", success, _engine, _model,
    )
    print(
        "TOKENS prompt=%d completion=%d total=%d cost_hkd=%.6f cost_estimated=%s(%s) success=%s engine=%s model=%s" % (
            tokens_prompt, tokens_completion, tokens_total, cost_hkd,
            cost_estimated, cost_estimated_reason or "-", success, _engine, _model,
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
                "cost_estimated": 1 if cost_estimated else 0,
                "cost_estimated_reason": cost_estimated_reason,
                "elapsed_ms": elapsed_dict,
                "success": success,
                "error_msg": str(state.get("contract_error") or state.get("last_error") or "")[:500],
                "use_grey": 1 if state.get("use_grey") else 0,
            }
            with open(_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        logging.getLogger("supervisor").warning(f"记忆落盘失败: {e}")
    # ---- T6 Gap A5：线上失败信号回流为评测候选（低置信/门禁拒绝/审核分歧，
    # 幂等去重在 db 层；user_edit 在 api_receipts.save_edited 挂钩。不阻断主链路）----
    try:
        _reflow_from_state(state)
    except Exception as e:
        logging.getLogger("supervisor").warning(f"评测候选回流判定失败(不阻断): {e}")
    return state
