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
# 上限护栏：即便管理台把重试上限填得很大，也不允许无限重跑（防长期空转 / 成本失控）。
MAX_RETRY_LIMIT = 10
# 引擎异常归类中"重跑同一配置不可能成功"的确定性类别（归类见 llm._engine_error_category）：
#   auth  鉴权失败（HTTP 401/403）——同一 key 重试仍是 401/403；
#   param 参数错误（HTTP 400）——模型名 / base_url 不被接受，重试同一配置仍是 400。
# 上游 upstream（5xx / 50507）**不在其列**：它是服务商侧瞬时故障，正是重试阶梯要覆盖的场景。
_NON_RETRYABLE_ENGINE_CATEGORIES = frozenset({"auth", "param"})

# 成本兜底单价（元/百万 token，仅输入侧）：只在 _calc_cost_hkd 与 _resolve_token_price
# 双双失败时使用，属「最后一道估算」，不是档位表口径。使用它时一律落 cost_estimated 标记，
# 避免该数字被当成精确成本喂给 guardian 的成本护栏与大盘。
_DEFAULT_COST_IN_PRICE = 2.2


def resolve_max_retry(config=None, leg: str = "rec", use_grey: bool = False) -> int:
    """解析本次管线的整图重跑上限：按 leg 与 use_grey 解析对应分腿重跑上限。
    leg: 'rec' | 'aud' | 'parse'
    默认值：识别 3，审核 1，解析 1。
    运行期硬钳制在 [1, MAX_RETRY_LIMIT] 区间（0 和 1 均表示尝试 1 次，不重跑）。
    """
    from app.models import resolve_max_retry as _resolve_max_retry
    return _resolve_max_retry(config, leg=leg, use_grey=use_grey)


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


# 瞬时故障（网络不通 / 上游 5xx / 读超时）最多重试 1 次，即总共 2 次调用；
# 剩余预算不足以再跑一轮时（见 _budget_remaining）不再开新一轮。
MAX_TRANSIENT_RETRIES = 1
MIN_ROUND_SECONDS = 8.0

ENGINE_NOT_CONFIGURED_MSG = "识别服务还没设置好，请直接手工录入，原图已经保留"
_USER_ERROR_BY_CATEGORY = {
    "auth": "识别服务的密钥不对或已过期，请联系老板在设置里更新，现在可以先直接手工录入，原图已经保留",
    "param": "识别服务的设置有问题，请联系老板检查，现在可以先直接手工录入，原图已经保留",
    "timeout": "识别服务这次响应太慢，请再试一次，或直接手工录入，原图已经保留",
    "network": "识别服务暂时连不上，请稍后再试，或直接手工录入，原图已经保留",
    "upstream": "识别服务那边暂时出了问题，请稍后再试，或直接手工录入，原图已经保留",
    "decode": "这张图片打不开，请重新拍一张，或直接手工录入，原图已经保留",
}
PIPELINE_TIMEOUT_MSG = "这张单识别用的时间太长，已经先停下了，请直接手工录入，原图已经保留"


def pipeline_budget_seconds() -> float:
    """单张识别的总时长预算（秒）：settings 键 pipeline_budget_seconds 可调，钳在 20-120。"""
    from app.models import PIPELINE_BUDGET_SECONDS
    try:
        from app.services import settings_service
        val = float(settings_service.get_float("pipeline_budget_seconds", PIPELINE_BUDGET_SECONDS))
    except Exception:
        val = float(PIPELINE_BUDGET_SECONDS)
    return max(20.0, min(120.0, val))


def _budget_remaining(state: dict) -> float:
    return float(state.get("deadline") or 0) - time.time()


def _round_elapsed(state: dict, result: dict, extra_parse_ms: float = 0.0) -> dict:
    """决策履历的耗时：extract/parse/rag 是本轮单轮耗时，*_cumulative 是截至本轮的累计。"""
    parse_round = max(0, (result.get("parse_llm") or {}).get("elapsed_ms", 0) or 0) + (extra_parse_ms or 0)
    return {
        "extract": round(float(result.get("extract_ms", 0) or 0), 1),
        "parse": round(float(parse_round), 1),
        "rag": round(float(result.get("rag_ms", 0) or 0), 1),
        "audit": state["audit_ms"],
        "total": 0,
        "extract_cumulative": state["extract_ms"],
        "parse_cumulative": state["parse_ms"],
        "rag_cumulative": state["rag_ms"],
    }


def _add_leg_cost(state: dict, token_usage, cost_cny: float, estimated: bool = False, reason: str = "") -> float:
    """把某一条腿（预判/修正/审核）的 token 与成本累计进单张成本，返回折算后的港币成本。"""
    rate = float(state.get("fx_rate_cny_hkd") or 1.0)
    tu = token_usage if isinstance(token_usage, dict) else {}
    try:
        from app.llm import _normalize_token_usage
        tu = _normalize_token_usage(tu)
    except Exception:
        tu = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    state["tokens_prompt"] = int(state.get("tokens_prompt", 0) or 0) + int(tu.get("prompt_tokens", 0) or 0)
    state["tokens_completion"] = int(state.get("tokens_completion", 0) or 0) + int(tu.get("completion_tokens", 0) or 0)
    state["tokens_total"] = int(state.get("tokens_total", 0) or 0) + int(tu.get("total_tokens", 0) or 0)
    state["cost_cny"] = round(float(state.get("cost_cny", 0) or 0) + float(cost_cny or 0), 6)
    hkd = round(float(cost_cny or 0) * rate, 6)
    state["cost_hkd"] = round(float(state.get("cost_hkd", 0) or 0) + hkd, 6)
    if estimated:
        state["cost_estimated"] = True
        state["cost_estimated_reason"] = state.get("cost_estimated_reason") or reason or "leg_cost_estimated"
    return hkd


_ERROR_CODE_BY_CATEGORY = {
    "auth": "ENGINE_AUTH_FAILED", "param": "ENGINE_PARAM_ERROR", "timeout": "ENGINE_TIMEOUT",
    "network": "ENGINE_NETWORK_ERROR", "upstream": "ENGINE_UPSTREAM_ERROR",
}


def _default_error_code(state: dict) -> str:
    cat = str(state.get("error_category") or "")
    if cat in _ERROR_CODE_BY_CATEGORY:
        return _ERROR_CODE_BY_CATEGORY[cat]
    if state.get("deterministic_error"):
        return "IMAGE_DECODE_FAILED"
    if state.get("output_reject_fast"):
        return "RECOGNITION_UNREADABLE"
    return "RECOGNITION_FAILED"


def _apply_field_warnings(state: dict, data) -> None:
    """契约宽松校验留下的字段存疑提示：明细保留，但单据状态必须是“带警告”。"""
    warns = list(getattr(data, "field_warnings", []) or [])
    state["field_warnings"] = warns
    if warns:
        state["gate_warnings"] = list(dict.fromkeys(list(state.get("gate_warnings") or []) + warns))


def _user_error_message(state: dict) -> str:
    """失败原因翻成店员看得懂的话：发生了什么 + 下一步怎么做。"""
    code = state.get("error_code") or ""
    if code == "ENGINE_NOT_CONFIGURED":
        return ENGINE_NOT_CONFIGURED_MSG
    if code == "PIPELINE_TIMEOUT":
        return PIPELINE_TIMEOUT_MSG
    cat = str(state.get("error_category") or "")
    if state.get("deterministic_error") and not cat:
        cat = "decode"
    if cat in _USER_ERROR_BY_CATEGORY:
        return _USER_ERROR_BY_CATEGORY[cat]
    from app.services.contract import humanize_contract_error
    return humanize_contract_error(state.get("contract_error") or state.get("last_error"))


def _engine_build_failure_result(exc, config, use_grey) -> dict:
    """识别模型构建阶段的异常（没配引擎、地址不合规等）转成确定性失败，管线照常写履历与日志。"""
    from app.llm import EngineNotConfiguredError, _engine_error_category
    not_configured = isinstance(exc, EngineNotConfiguredError)
    detail = f"{'ENGINE_NOT_CONFIGURED: ' if not_configured else 'ENGINE_BUILD_FAILED: '}{exc}"
    zero = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    return {
        "data": None, "raw": "", "error": detail,
        "error_code": "ENGINE_NOT_CONFIGURED" if not_configured else "ENGINE_BUILD_FAILED",
        "error_category": _engine_error_category(exc) or "param",
        "deterministic_error": True,
        "elapsed_ms": 0.0, "extract_ms": 0.0, "rag_ms": 0.0,
        "engine": "unconfigured" if not_configured else "build_failed",
        "vendor_context": "",
        "parse_llm": {"enabled": False, "model": "", "elapsed_ms": 0},
        "token_usage": dict(zero), "vlm_token_usage": dict(zero), "parse_token_usage": dict(zero),
        "cost_hkd": 0.0, "adapted_params": [],
        "cost_estimated": 0, "cost_estimated_reason": "",
    }

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


def first_round_inject_enabled() -> bool:
    """首轮记忆注入开关（W-1 / Gap H4；settings 实时读取，缺省 False = 零行为变化）。"""
    try:
        from app.services import settings_service
        return bool(settings_service.get_bool("memory_first_round_inject_enabled", False))
    except Exception:
        return False


def _resolve_first_round_vendor(image_path: str, config=None) -> str:
    """首轮供应商来源 #2：调用既有 vendor_precheck.predict_vendor（W-1）。

    仅在 vendor_precheck 自身开关（vendor_precheck_enabled）也为开时才发模型调用；
    关闭 / 预判失败 / 解析不出 vendor 一律返回空串（不猜、不阻断识别主链路）。
    """
    try:
        from app.services import vendor_precheck
        if not vendor_precheck.is_enabled():
            return ""
        return str(vendor_precheck.predict_vendor(image_path, cfg=config) or "").strip()
    except Exception:
        return ""

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
    """记忆落盘路径：默认仓库内 artifacts/memory/parse_log.jsonl。

    why 支持 ARTIFACTS_DIR 重定向（Q3-M4）：本函数在**每次落盘时**被调用，读环境变量
    即可让测试（两处 conftest 的 _isolate_artifacts_dir）与容器化部署把产物写到临时
    目录，避免「跑一次测试就往仓库内 artifacts/ 追加一行」；按 __file__ 算出的路径
    不受 DB / RAG / 图片隔离影响，需单独有一个可覆盖点。落盘时读取意味着不依赖模块
    是否已 import，对「用例内才 import supervisor」的时序同样生效（与 RAG_DIR 同款）。
    显式重定向时只认 override、不回落仓库内目录 —— 否则重定向目录不可写时隔离会静默
    失效，又写回仓库。
    """
    override = os.environ.get("ARTIFACTS_DIR", "").strip()
    if override:
        p = os.path.join(os.path.abspath(override), "memory", "parse_log.jsonl")
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
        except Exception:
            pass
        return p
    # 优先保证 artifacts/memory 目录存在，取可写路径
    for p in (_MEMORY_LOG_PATH, _MEMORY_LOG_PATH_ALT):
        d = os.path.dirname(p)
        try:
            os.makedirs(d, exist_ok=True)
            return p
        except Exception:
            continue
    return _MEMORY_LOG_PATH


PARSE_LOG_MAX_BYTES = 20 * 1024 * 1024
PARSE_LOG_KEEP_ROTATED = 30


def _rotate_parse_log_if_needed(path: str) -> None:
    """按日期或体积轮转：跨天或超过 20MB 就把当前文件改名归档，只保留最近 30 份归档。"""
    if not os.path.exists(path):
        return
    try:
        st = os.stat(path)
    except OSError:
        return
    file_day = time.strftime("%Y%m%d", time.localtime(st.st_mtime))
    today = time.strftime("%Y%m%d")
    if file_day == today and st.st_size < PARSE_LOG_MAX_BYTES:
        return
    base, ext = os.path.splitext(path)
    stamp = file_day if file_day != today else time.strftime("%Y%m%d%H%M%S")
    target = f"{base}.{stamp}{ext}"
    n = 1
    while os.path.exists(target):
        target = f"{base}.{stamp}.{n}{ext}"
        n += 1
    os.replace(path, target)
    import glob
    archives = sorted(glob.glob(f"{base}.*{ext}"))
    for old in archives[:-PARSE_LOG_KEEP_ROTATED]:
        try:
            os.remove(old)
        except OSError:
            pass


def append_parse_log(rec: dict) -> None:
    """parse_log 追加一行：进程内线程锁 + 跨进程文件锁（多 worker 部署）+ 轮转。

    parse_log 只是排障用的流水；管理端的指标统计以数据库为准，不再读这个文件。
    """
    with _MEMORY_LOCK:
        path = _memory_log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        lock_path = path + ".lock"
        with open(lock_path, "a") as lock_f:
            try:
                import fcntl
                fcntl.flock(lock_f, fcntl.LOCK_EX)
            except Exception:
                pass
            try:
                _rotate_parse_log_if_needed(path)
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            finally:
                try:
                    import fcntl
                    fcntl.flock(lock_f, fcntl.LOCK_UN)
                except Exception:
                    pass


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
                 vendor_prior: str = "", on_event=None, tenant_id: str = "default"):
    """单轮识别：VLM 读图 → 结构化（含可选解析 LLM）。vendor_prior 为重试轮注入的历史先验。

    tenant_id（Gap H1）：本次识别所属租户，透传给 extract_receipt 的 hint 通道检索，
    保证读侧与写侧（approve 回写）用同一租户键。
    """
    try:
        model = build_recognition_model(
            config.recognition_model if config else None,
            cfg=config, use_grey=use_grey,
        )
    except Exception as build_exc:
        logging.getLogger("supervisor").warning("识别模型构建失败: %s", build_exc)
        return _engine_build_failure_result(build_exc, config, use_grey)
    return extract_chain.extract_receipt(
        image_path, vendor_hint=vendor_hint,
        model=model, config=config,
        retry_feedback=retry_feedback,
        use_grey=use_grey,
        vendor_prior=vendor_prior,
        tenant_id=tenant_id,
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


def _run_gates_ex(data: ReceiptData, receipt_id=None, attempt=1):
    """契约门禁 + 算术门禁（确定性代码，零 token）。返回 (data, err, gate_issues)。

    gate_issues 为结构化差错清单 [{code,row_index,item_name,field,expected,actual,diff,hint}]；
    err 里的文字全部是人话（hint 拼接），前缀“算术门禁:”仅供日志分类使用，不直接给店员看。
    """
    from app.services.contract import validate_contract_tiered, humanize_contract_error
    checked, err, _warns = validate_contract_tiered(data.model_dump())
    _track_guard("contract_guard_checked", err, receipt_id, attempt)
    if err:
        return None, err, []
    issues = math_engine.validate_and_issues(checked)
    problems = [iss["hint"] for iss in issues]
    math_err = ("算术门禁: " + "；".join(problems)) if problems else None
    _track_guard("math_guard_checked", math_err, receipt_id, attempt)
    if problems:
        return checked, math_err, issues
    return checked, None, []


def _run_gates(data: ReceiptData, receipt_id=None, attempt=1):
    """兼容入口：返回 (data, err)，失败时 data 为 None。"""
    checked, err, _issues = _run_gates_ex(data, receipt_id, attempt)
    return (None, err) if err else (checked, None)


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


def _audit_model_and_prices(config, use_grey):
    model = str(getattr(config, "audit_model", "") or "")
    if use_grey:
        model = str(getattr(config, "grey_audit_model", model) or model)
    p_in = getattr(config, "price_input_grey_aud" if use_grey else "price_input_aud", None)
    p_cached = getattr(config, "price_cached_input_grey_aud" if use_grey else "price_cached_input_aud", None)
    p_out = getattr(config, "price_output_grey_aud" if use_grey else "price_output_aud", None)
    return model, p_in, p_cached, p_out


def _account_audit_cost(state: dict, audit: dict, config, use_grey) -> None:
    """审核腿成本并入单张成本。规则审核（code_engine）是真实 0，不标估算；VLM 审核按 token 计价。"""
    if audit.get("engine") == "code_engine" or audit.get("mode") == "text":
        return
    tu = audit.get("token_usage")
    if not isinstance(tu, dict) or not tu:
        return
    try:
        from app.llm import _calc_cost_hkd, _normalize_token_usage
        tu_n = _normalize_token_usage(tu)
        model, p_in, p_cached, p_out = _audit_model_and_prices(config, use_grey)
        cost_cny = _calc_cost_hkd(tu_n, model=model, custom_in_price=p_in,
                                  custom_cached_price=p_cached, custom_out_price=p_out)
        est = int(tu_n.get("total_tokens", 0) or 0) == 0
        _add_leg_cost(state, tu_n, cost_cny, est, "audit_no_token_usage")
    except Exception as e:
        logging.getLogger("supervisor").warning("审核成本累计失败，按估算处理: %s", e)
        state["cost_estimated"] = True
        state["cost_estimated_reason"] = state.get("cost_estimated_reason") or "audit_cost_failed"


def _log_audit_decision(receipt_id, experiment_id, config, use_grey, audit, fx_rate=1.0):
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
            # 传当时生效的审核模型（_ai_model 已含灰测分支）及自定义单价用于档位解析：
            _aud_in = getattr(config, "price_input_grey_aud" if use_grey else "price_input_aud", None)
            _aud_cached = getattr(config, "price_cached_input_grey_aud" if use_grey else "price_cached_input_aud", None)
            _aud_out = getattr(config, "price_output_grey_aud" if use_grey else "price_output_aud", None)
            _audit_cost = _calc_cost_hkd(
                _audit_tu,
                model=_ai_model,
                custom_in_price=_aud_in,
                custom_cached_price=_aud_cached,
                custom_out_price=_aud_out,
            )
        except Exception as e:
            _audit_cost = 0.0
            _audit_estimated = True
            _audit_reason = _audit_reason or "cost_calc_failed"
            logging.getLogger("supervisor").warning(
                "审核成本计算失败(收据=%s model=%s tokens=%s)，记 0 并标记为估算值: %s",
                receipt_id, _ai_model, _audit_tu, e)
        _audit_cost = round(float(_audit_cost or 0) * float(fx_rate or 1.0), 6)
        _rule_audit = audit.get("engine") == "code_engine" or audit.get("mode") == "text"
        if not _audit_estimated and not _rule_audit and int(_audit_tu.get("total_tokens", 0) or 0) == 0:
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
                "tokens_cached": int(_audit_tu.get("cached_tokens", 0) or 0),
                "tokens_total": int(_audit_tu.get("total_tokens", 0) or 0),
                "cost_hkd": float(_audit_cost or 0),
                "cost_estimated": 1 if _audit_estimated else 0,
                "cost_estimated_reason": _audit_reason,
                "elapsed_ms": _audit_elapsed,
                "success": bool(audit.get("overall_consistent")) if audit.get("skipped") is False else False,
                "max_tokens": getattr(config, "grey_openai_aud_max_tokens" if use_grey else "openai_aud_max_tokens", 4096) if config else 4096,
                "temperature": 0.0,
                **({"adapted_params": list(audit["adapted_params"])} if audit.get("adapted_params") else {}),
            },
        )
    except Exception as e:
        logging.getLogger("supervisor").warning(f"记录交叉审核 AI 决策失败: {e}")


def _log_extract_decision(receipt_id, experiment_id, config, use_grey,
                          attempt, engine, status, gate_err="",
                          token_usage=None, cost_hkd=None, elapsed_ms=None,
                          success=None, image_path="", supplier="", doc_form="",
                          cost_estimated_in=None, cost_estimated_reason_in="",
                          image_guard=None, rag_hint_source="",
                          vendor_hint_origin="", adapted_params=None):
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
    - image_guard（P2-1）：上传入口在 preprocess 之前对原图算出的图内隐藏文字检测
      结果（{flagged, reasons, metrics}）。非空时写入 extra 的 image_guard /
      image_guard_flagged（供大盘统计命中率），不改变识别主链路任何行为。
    - rag_hint_source（FP-1）：run 级 hint 来源标记（"first_round"= W-1 首轮注入）。
      非空时写入 extra["rag_hint_source"]；空串不写键（extra 字节与既有路径一致）。
    - vendor_hint_origin（可观测性，additive）：调用方传入的显式 vendor_hint 的来源
      （"user"=用户手填 / "precheck"=上传端点预判填充）。非空时写入
      extra["vendor_hint_origin"]；空串不写键（extra 字节与既有路径一致）。
      与 rag_hint_source 互补：后者只标记 W-1 首轮注入，前者区分「显式 hint」内部
      的两个来源 —— 下游因此能回答"这次识别吃到的 hint 是人给的还是机器猜的"。
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
    tokens_cached = _to_int(tu.get("cached_tokens", 0), "cached_tokens")
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
            _rec_in = getattr(config, "price_input_grey_rec" if use_grey else "price_input_rec", None)
            _rec_cached = getattr(config, "price_cached_input_grey_rec" if use_grey else "price_cached_input_rec", None)
            _rec_out = getattr(config, "price_output_grey_rec" if use_grey else "price_output_rec", None)
            cost_hkd = _calc_cost_hkd(
                {"prompt_tokens": tokens_prompt, "completion_tokens": tokens_completion, "cached_tokens": tokens_cached, "total_tokens": tokens_total},
                model=_rec_model,
                custom_in_price=_rec_in,
                custom_cached_price=_rec_cached,
                custom_out_price=_rec_out,
            )
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
        "tokens_cached": tokens_cached,
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
        "max_tokens": getattr(config, "grey_openai_rec_max_tokens" if use_grey else "openai_rec_max_tokens", 4096) if config else 4096,
        "temperature": getattr(config, "grey_openai_rec_temperature" if use_grey else "openai_rec_temperature", 0.0) if config else 0.0,
        "image_detail": getattr(config, "grey_openai_rec_image_detail" if use_grey else "openai_rec_image_detail", "high") if config else "high",
    }
    # P2-1 图内隐藏文字检测：flags 落 extra，使大盘可按 image_guard_flagged 统计命中率。
    # 沿用既有 extra 风格（嵌套 dict，如 elapsed_ms）；非 dict 一律不写，避免噪声。
    if isinstance(image_guard, dict):
        extra["image_guard_flagged"] = bool(image_guard.get("flagged"))
        extra["image_guard"] = {
            "flagged": bool(image_guard.get("flagged")),
            "reasons": [str(r)[:200] for r in (image_guard.get("reasons") or [])][:5],
            "metrics": image_guard.get("metrics") or {},
        }
    # FP-1（rag_hint_source 持久化）：W-1 首轮注入的来源标记落 extra。**纯增量**：
    # 空串不写键 —— 开关关/显式 hint 路径的 extra 序列化字节与改动前逐字节一致（C3 红线）。
    if str(rag_hint_source or "").strip():
        extra["rag_hint_source"] = str(rag_hint_source)
    # 可观测性（vendor_hint_origin）：显式 hint 的来源（"user"/"precheck"）落 extra。
    # 同款纯增量手法：空串不写键，无 hint 路径的 extra 字节与改动前逐字节一致。
    if str(vendor_hint_origin or "").strip():
        extra["vendor_hint_origin"] = str(vendor_hint_origin)
    if adapted_params:
        extra["adapted_params"] = list(adapted_params)
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
                 image_guard=None,
                 tenant_id: str = "default",
                 vendor_hint_origin: str = "",
                 ) -> dict:
    """完整识别管线入口：在总时长预算内执行 _run_pipeline_impl。

    预算（约 45 秒，pipeline_budget_seconds）通过线程级截止时间生效：每次引擎调用的
    超时都被截到剩余预算以内，预算用完不再开新一轮。
    """
    from app import llm as _llm
    deadline = time.time() + pipeline_budget_seconds()
    _llm.set_pipeline_deadline(deadline)
    try:
        return _run_pipeline_impl(
            image_path, vendor_hint=vendor_hint, config=config, supplier_name=supplier_name,
            receipt_id=receipt_id, experiment_id=experiment_id, on_event=on_event,
            image_guard=image_guard, tenant_id=tenant_id,
            vendor_hint_origin=vendor_hint_origin, _deadline=deadline)
    finally:
        _llm.set_pipeline_deadline(None)


def run_pipeline_guarded(*args, **kwargs) -> dict:
    """带整体看门狗的管线入口（识别 Job 使用）。

    管线在工作线程里跑；超过 预算 + 宽限 仍未返回就放弃等待，写一条超时决策履历并返回
    error 结果，单据随即转人工，不会一直停在“识别中”。迟到的管线结果会被丢弃
    （abandoned 标记），不会再写库覆盖。
    """
    from app.models import PIPELINE_WATCHDOG_GRACE_SECONDS
    holder: dict = {}
    abandoned = threading.Event()
    bound_db = getattr(db._job_db_ctx, "path", None) if hasattr(db, "_job_db_ctx") else None
    bound_job = getattr(db._job_db_ctx, "job_id", "") if hasattr(db, "_job_db_ctx") else ""

    def _worker():
        try:
            if bound_db:
                db.bind_job_db_path(bound_db, bound_job)
            holder["result"] = run_pipeline(*args, **kwargs)
        except BaseException as exc:
            holder["error"] = exc
        finally:
            try:
                db.unbind_job_db_path()
            except Exception:
                pass
            if abandoned.is_set():
                logging.getLogger("supervisor").warning("看门狗已放弃的识别管线迟到结束，结果已丢弃")

    worker = threading.Thread(target=_worker, daemon=True, name="pipeline-worker")
    worker.start()
    limit = pipeline_budget_seconds() + PIPELINE_WATCHDOG_GRACE_SECONDS
    worker.join(timeout=limit)
    if worker.is_alive():
        abandoned.set()
        return _watchdog_timeout_result(args, kwargs, limit)
    if "error" in holder:
        raise holder["error"]
    return holder["result"]


def _watchdog_timeout_result(args, kwargs, limit_seconds) -> dict:
    image_path = args[0] if args else kwargs.get("image_path", "")
    receipt_id = kwargs.get("receipt_id")
    config = kwargs.get("config")
    log: list = []
    _snapshot(log, "pipeline_timeout",
              f"整体看门狗触发：{int(limit_seconds)} 秒内管线未返回，已放弃等待并转人工",
              attempt=0, engine="watchdog")
    try:
        _log_extract_decision(
            receipt_id, kwargs.get("experiment_id"), config, False, 0, "watchdog",
            "pipeline_timeout", gate_err=f"管线 {int(limit_seconds)} 秒未返回",
            success=False, image_path=image_path,
            cost_estimated_in=True, cost_estimated_reason_in="pipeline_timeout")
    except Exception:
        pass
    state = {
        "data": None, "raw": "", "status": "error", "success": False, "log": log,
        "contract_error": "", "last_error": f"管线超过 {int(limit_seconds)} 秒仍未返回（看门狗放弃等待）",
        "error_code": "PIPELINE_TIMEOUT", "error_category": "timeout",
        "attempt": 0, "image_path": image_path, "receipt_id": receipt_id,
        "config": config, "use_grey": False, "tenant_id": kwargs.get("tenant_id", "default"),
        "gate_issues": [], "gate_warnings": [], "math_problems": [],
        "cost_estimated": True, "cost_estimated_reason": "pipeline_timeout",
    }
    state["user_error"] = PIPELINE_TIMEOUT_MSG
    try:
        _finalize(state, time.time() - limit_seconds)
    except Exception:
        pass
    return state


def _run_pipeline_impl(image_path: str, vendor_hint: str = "",
                 config: Optional[EngineConfig] = None,
                 supplier_name: str = "",
                 receipt_id: Optional[int] = None,
                 experiment_id: Optional[int] = None,
                 on_event=None,
                 image_guard=None,
                 tenant_id: str = "default",
                 vendor_hint_origin: str = "",
                 _deadline: Optional[float] = None,
                 ) -> dict:
    """完整识别管线实现（线性编排 + 重试阶梯）。

    supplier_name 供灰测按供应商分配（同供应商一致命中）。
    tenant_id（Gap H1）：本单所属租户，透传到两处 VendorMemory 检索（hint 通道与
    首轮补检索），使读侧与 approve 写侧同租户。缺省 "default" 仅为兼容离线脚本
    与既有测试对 run_pipeline 的直接调用；生产入口
    receipt_utils.start_recognition_job 一律传 Job 捕获的真实租户。
    on_event（可选）：实时事件回调，如首选引擎降级回退时立即通知调用方
    （异步 Job 层借此把回退状态透传给前端轮询，第一时间弹出提示）。
    image_guard（可选，P2-1）：上传入口对原图算出的图内隐藏文字检测结果
    （{flagged, reasons, metrics}），随每轮 extract 决策写入 ai_decision_log.extra；
    缺省 None 时行为与接线前完全一致（只告警、不影响识别成败）。
    vendor_hint_origin（可选，可观测性 additive）：调用方传入的显式 vendor_hint 的
    来源（"user"=用户手填 / "precheck"=上传端点预判填充），随 state → Job result /
    extract 决策 extra 透传；缺省空串时无 extra 键、result 字段为空串（行为零变化）。
    返回 dict 含 data / raw / log / contract_error 等（与旧接口兼容）。
    """
    from app.models import should_use_grey
    _supplier_id = None
    if config is not None and supplier_name and getattr(config, "grey_supplier_ids", None):
        try:
            _supplier_id = db.resolve_supplier_id(supplier_name, tenant_id=tenant_id)
        except Exception:
            _supplier_id = None
    use_grey = should_use_grey(config, supplier_name, supplier_id=_supplier_id) if config else False

    pipeline_start = time.time()
    try:
        from app.services.llm_logger import set_llm_context
        set_llm_context(
            receipt_id=receipt_id,
            tenant_id=tenant_id,
            leg="extract",
            attempt=1,
            image_path=image_path,
        )
    except Exception:
        pass
    log = []
    if use_grey:
        _snapshot(log, "grey_assigned",
                  f"命中灰测组（分配模式: {config.grey_assign_mode.value}, 概率: {config.grey_percent}%）",
                  attempt=0, engine="grey")

    state = {
        "image_path": image_path,
        "vendor_hint": vendor_hint,
        "deadline": _deadline or (pipeline_start + pipeline_budget_seconds()),
        # P2-1：图内隐藏文字检测结果（由上传入口透传，随每轮 extract 决策落 extra）
        "image_guard": image_guard,
        "config": config,
        "use_grey": use_grey,
        "attempt": 0,
        "engine_name": "openai",
        "log": log,
        "contract_error": "",
        "math_problems": [],
        # S2-12：结构化门禁结果 [{code,row_index,item_name,field,expected,actual,diff,hint}]
        "gate_issues": [],
        "field_warnings": [],
        "transient_failures": 0,
        "cost_cny": 0.0,
        "error_code": "",
        "error_category": "",
        "user_error": "",
        # S2-08：契约致命失败时保留的已解析字段，Job 层落库供人工预填
        "salvage": None,
        "audit_skipped": False,
        "tenant_id": tenant_id,
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
        # W-1 / Gap H4 可观测标记：首轮注入发生时为 "first_round"，否则空串（与重试轮注入区分）
        "rag_hint_source": "",
        # 可观测性（additive）：调用方传入的显式 vendor_hint 的来源（"user"=用户手填 /
        # "precheck"=上传端点预判填充），随 Job result 与 extract 决策 extra 透传；
        # 无显式 hint 时为空串（不写 extra 键）。W-1 首轮注入的来源仍由 rag_hint_source
        # 标记，两者互补、互不覆盖。
        "vendor_hint_origin": str(vendor_hint_origin or "").strip(),
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

    # 汇率与预判成本：模型单价按人民币计，进账折港币；上传阶段的供应商预判（若发生过）
    # 用量按图片路径暂存，这里并入同一张单的成本。
    from app.services import fx as _fx
    _fx_rate = _fx.cny_to_hkd_rate(tenant_id)
    state["fx_rate_cny_hkd"] = _fx_rate
    try:
        from app.services import vendor_precheck as _vp
        _pre = _vp.pop_precheck_usage(image_path)
        if _pre:
            _add_leg_cost(state, _pre.get("token_usage"), float(_pre.get("cost_cny") or 0),
                          bool(_pre.get("cost_estimated")), "precheck_no_token_usage")
    except Exception:
        pass

    # P2-1：把上传入口透传的图内隐藏文字检测结果注入每轮 extract 决策落库
    # （extra.image_guard / extra.image_guard_flagged），避免 5 个调用点各抄一遍。
    def _log_decision(*args, **kwargs):
        kwargs.setdefault("image_guard", state.get("image_guard"))
        # FP-1：run 级 hint 来源标记随每轮决策落 extra（语义：标记的是"本 run 的
        # hint 来源"——首轮注入派生的 hint 在重试轮仍然有效，非"第几轮注入的"）。
        kwargs.setdefault("rag_hint_source", state.get("rag_hint_source") or "")
        # 可观测性：显式 hint 的来源（"user"/"precheck"）随每轮决策落 extra，同款
        # run 级透传手法；空串路径 extra 不含该键（字节一致）。
        kwargs.setdefault("vendor_hint_origin", state.get("vendor_hint_origin") or "")
        if "adapted_params" not in kwargs and "result" in locals() and isinstance(result, dict):
            kwargs["adapted_params"] = result.get("adapted_params")
        return _log_extract_decision(*args, **kwargs)

    # ---- W-1 / Gap H4：首轮记忆注入（缺省关 = 与现状逐字节一致）----
    # 无显式 vendor_hint 时，在首轮 extract **之前**尽力确定供应商，作为本单的
    # vendor_hint 交给既有 hint 通道「检索 + 注入」——使首轮即可吃到记忆（此前只有
    # 跑在首轮模型调用之后的「首轮补检索」，先验只能喂重试轮）。
    # 供应商来源：#1 调用方 vendor_hint（非空则不进入本分支）；#2 vendor_precheck
    # （仅其自身开关为开时才发模型调用）；两者都不行则不猜（避免错误供应商污染）。
    # 走既有 hint 通道，rag_context_json 以 [prior:hint] 落库，与重试轮 [prior:retry] 可区分。
    if first_round_inject_enabled() and not vendor_hint:
        _fr_vendor = _resolve_first_round_vendor(image_path, config)
        if _fr_vendor:
            vendor_hint = _fr_vendor
            state["vendor_hint"] = _fr_vendor
            state["rag_hint_source"] = "first_round"
            _snapshot(log, "first_round_inject",
                      f"首轮注入记忆先验（供应商预判={_fr_vendor}）", 0)

    # ---- 重试阶梯：识别 + 门禁，最多 max_rounds 轮（默认 MAX_RETRY，见文件头策略注释）----
    max_rounds = resolve_max_retry(config, leg="rec", use_grey=use_grey)
    while state["attempt"] < max_rounds:
        state["attempt"] += 1
        attempt = state["attempt"]
        try:
            from app.services.llm_logger import set_llm_leg_and_attempt
            set_llm_leg_and_attempt(leg="extract", attempt=attempt)
        except Exception:
            pass

        result = _run_extract(
            image_path, vendor_hint, config, use_grey,
            "", attempt,
            vendor_prior=state["vendor_context"],
            on_event=on_event,
            tenant_id=tenant_id,
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
        _cost_cny = _cost
        _cost = round(_cost_cny * _fx_rate, 6)
        state["engine_name"] = str(result.get("engine") or state.get("engine_name") or "openai")
        # Job/前端可见：本轮成本是否为估算值（供大盘区分「真实 0 成本」与「算不出来」）
        if _cost_est_in:
            state["cost_estimated"] = True
            state["cost_estimated_reason"] = _cost_why_in or "upstream_cost_fallback"
        state["tokens_prompt"] = int(state.get("tokens_prompt", 0) or 0) + int(_tu.get("prompt_tokens", 0) or 0)
        state["tokens_completion"] = int(state.get("tokens_completion", 0) or 0) + int(_tu.get("completion_tokens", 0) or 0)
        state["tokens_total"] = int(state.get("tokens_total", 0) or 0) + int(_tu.get("total_tokens", 0) or 0)
        state["cost_hkd"] = round(float(state.get("cost_hkd", 0) or 0) + float(_cost or 0), 6)
        state["cost_cny"] = round(float(state.get("cost_cny", 0) or 0) + _cost_cny, 6)
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

        if result["error"]:
            state["error_category"] = str(result.get("error_category") or "")
            if result.get("error_code"):
                state["error_code"] = str(result["error_code"])
            _snapshot(log, "extract_fail", f"识别失败(第{attempt}轮): {result['error']}",
                      attempt, engine=result["engine"])
            state.update(_log_decision(receipt_id, experiment_id, config, use_grey,
                                      attempt, result["engine"], "extract_fail",
                                      gate_err=result["error"],
                                      token_usage=_tu, cost_hkd=_cost,
                                      elapsed_ms=_round_elapsed(state, result),
                                      success=False, image_path=image_path,
                                      supplier=state.get("supplier", ""), doc_form=state.get("doc_form", ""),
                                      cost_estimated_in=_cost_est_in,
                                      cost_estimated_reason_in=_cost_why_in))
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
                # 逐字相同（error 分支不带任何新反馈、失败轮不补 RAG 先验），
                # temperature≈0 下近确定性复现，整图重跑无意义。快速反馈交人工复核，
                # 不再白烧 1-2 轮 VLM（此前会跑满上限才失败）。
                state["output_reject_fast"] = True
                state["salvage"] = result.get("salvage")
                _snapshot(log, "output_reject_fast",
                          f"输出质量快速反馈(第{attempt}轮): {result['error']}"
                          f"（同一 prompt 重跑无意义，已保留原始输出交人工复核）",
                          attempt, engine=result["engine"])
                break
            # 引擎瞬时故障（网络不通 / 上游 5xx / 读超时）：只重试 1 次，且剩余预算够跑一轮才重试。
            state["transient_failures"] += 1
            if state["transient_failures"] > MAX_TRANSIENT_RETRIES:
                _snapshot(log, "retry_stop",
                          f"瞬时故障已重试 {MAX_TRANSIENT_RETRIES} 次仍失败，不再重试: {result['error']}",
                          attempt, engine=result["engine"])
                break
            if _budget_remaining(state) < MIN_ROUND_SECONDS:
                state["budget_exhausted"] = True
                _snapshot(log, "budget_stop",
                          f"总时长预算只剩 {max(0, int(_budget_remaining(state)))} 秒，不再开新一轮",
                          attempt, engine=result["engine"])
                break
            continue

        # 首轮补检索（真飞轮）：无 hint 时 VLM 已识别出供应商 → 读 VendorMemory 补上下文，
        # 供后续 gate_reject 重试轮注入（异常置空，不阻断识别线程）
        # Gap H1：必须带本单的 tenant_id，否则会读到 default 桶（跨租户串味 / 读不到本租户记忆）
        if not state["vendor_context"] and result["data"] is not None:
            try:
                r_start = time.time()
                ctx = retrieve_context(
                    getattr(result["data"], "vendor", "") or "",
                    tenant_id=tenant_id) or ""
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

        data, gate_err, gate_issues = _run_gates_ex(result["data"], receipt_id, attempt)
        if gate_err:
            state["contract_error"] = gate_err
            state["gate_issues"] = gate_issues
            # 门禁不过的处置顺序（README §3.2 / §5.2 与此一致）：
            #   1. 解析级廉价修正：纯文本解析模型按门禁反馈改一次，不重读图（解析模型关闭时跳过）；
            #   2. 修正仍不过 → 保留已解析明细带警告交人工，不做整图重识别：
            #      一次整图识别通常要 30 秒以上，放不进 45 秒总预算，且同一张图重读很难改变结论。
            c_start = time.time()
            corr_usage: dict = {}
            corrected = extract_chain.correct_receipt_with_feedback(
                result["raw"], gate_err, config=config, use_grey=use_grey, usage_out=corr_usage,
            )
            _corr_ms = max(0, (time.time() - c_start) * 1000)
            state["parse_ms"] = round(state["parse_ms"] + _corr_ms, 1)
            if corr_usage:
                _corr_hkd = _add_leg_cost(state, corr_usage.get("token_usage"),
                                          float(corr_usage.get("cost_cny") or 0),
                                          bool(corr_usage.get("cost_estimated")),
                                          corr_usage.get("cost_estimated_reason") or "")
                _cost = round(float(_cost or 0) + _corr_hkd, 6)
            corr_data = None
            if corrected:
                corr_data, corr_err = extract_chain._parse_to_receipt(corrected)
                corr_gate = corr_err
                corr_issues: list = []
                if corr_data is not None and not corr_err:
                    corr_data, corr_gate, corr_issues = _run_gates_ex(corr_data, receipt_id, attempt)
                if corr_gate is None:
                    state["raw"] = corrected
                    _snapshot(log, "parse_correct",
                              f"解析级修正通过门禁(第{attempt}轮): {gate_err}",
                              attempt, engine=result["engine"])
                    state.update(_log_decision(receipt_id, experiment_id, config, use_grey,
                                               attempt, result["engine"], "parse_ok",
                                               token_usage=_tu, cost_hkd=_cost,
                                               elapsed_ms=_round_elapsed(state, result, _corr_ms),
                                               success=True, image_path=image_path,
                                               supplier=state.get("supplier", ""), doc_form=state.get("doc_form", ""),
                                               cost_estimated_in=_cost_est_in,
                                               cost_estimated_reason_in=_cost_why_in))
                    state["data"] = corr_data
                    state["contract_error"] = ""
                    state["gate_issues"] = []
                    _apply_field_warnings(state, corr_data)
                    try:
                        state["supplier"] = str(getattr(corr_data, "vendor", "") or state.get("supplier", ""))[:120]
                        _dfc = getattr(corr_data, "doc_form", "")
                        state["doc_form"] = str(_dfc.value if hasattr(_dfc, "value") else _dfc or state.get("doc_form", ""))[:60]
                    except Exception:
                        pass
                    state["success"] = True
                    break
                if corr_data is not None:
                    state["gate_issues"] = corr_issues
            _snapshot(log, "gate_reject_fast",
                      f"门禁快速反馈(第{attempt}轮): {gate_err}（已尝试 parse 修正；保留已解析结构交人工快速复核）",
                      attempt, engine=result["engine"])
            state.update(_log_decision(receipt_id, experiment_id, config, use_grey,
                                       attempt, result["engine"], "gate_reject_fast",
                                       gate_err=gate_err,
                                       token_usage=_tu, cost_hkd=_cost,
                                       elapsed_ms=_round_elapsed(state, result, _corr_ms),
                                       image_path=image_path,
                                       supplier=state.get("supplier", ""), doc_form=state.get("doc_form", ""),
                                       cost_estimated_in=_cost_est_in,
                                       cost_estimated_reason_in=_cost_why_in))
            # P14/P15：门禁未通过时不得静默当「解析成功」——留痕门禁警告，
            # 最终状态由收尾处依 gate_warnings 置为 parsed_with_warnings（不再置 success=True）。
            state["gate_warnings"] = [str(gate_err)]
            kept_data = corr_data if corr_data is not None else result.get("data")
            if kept_data is not None:
                state["data"] = kept_data
                _hints = [i["hint"] for i in gate_issues] or [str(gate_err)]
                _fw = list(getattr(kept_data, "field_warnings", []) or [])
                state["math_problems"] = list(dict.fromkeys(_hints + _fw))
                try:
                    state["supplier"] = str(getattr(kept_data, "vendor", "") or state.get("supplier", ""))[:120]
                    _df2 = getattr(kept_data, "doc_form", "")
                    state["doc_form"] = str(_df2.value if hasattr(_df2, "value") else _df2 or state.get("doc_form", ""))[:60]
                except Exception:
                    pass
            break

        state["data"] = data
        state["contract_error"] = ""
        _apply_field_warnings(state, data)
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
        state.update(_log_decision(receipt_id, experiment_id, config, use_grey,
                                  attempt, result["engine"], "extract_ok",
                                  token_usage=_tu, cost_hkd=_cost,
                                  elapsed_ms=_round_elapsed(state, result),
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
        if not state.get("error_code"):
            state["error_code"] = _default_error_code(state)
        state["user_error"] = _user_error_message(state)
        return _finalize(state, pipeline_start)

    # ---- 交叉审核（可选，失败不阻断）----
    try:
        from app.services.llm_logger import set_llm_leg_and_attempt
        set_llm_leg_and_attempt(leg="audit", attempt=state["attempt"])
    except Exception:
        pass
    audit = _run_audit(
        image_path, state["data"], config=config, use_grey=use_grey,
    )
    state["audit_result"] = audit
    state["audit_ms"] = round(audit.get("audit_ms", 0) or 0, 1)
    _account_audit_cost(state, audit, config, use_grey)
    if audit.get("skipped"):
        state["audit_skipped"] = True
        _snapshot(log, "audit_skip", f"审核跳过: {audit.get('reason')}", state["attempt"])
        if audit.get("canary_blocked"):
            # 审核腿握手失败疑似上游被篡改：单据置 flagged 交人工，并记安全事件。
            state["flagged_reason"] = "audit_canary_blocked"
            try:
                from app.services.security_guard import log_security_event
                log_security_event(
                    "CANARY_VERIFICATION_FAILED", "high", "supervisor.audit",
                    {"receipt_id": receipt_id, "leg": "audit", "reason": str(audit.get("reason"))[:200]},
                    tenant_id=tenant_id)
            except Exception as sec_err:
                logging.getLogger("supervisor").warning("记录审核 Canary 安全事件失败: %s", sec_err)
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
        args=(receipt_id, experiment_id, config, use_grey, audit, state.get("fx_rate_cny_hkd", 1.0)),
        daemon=True,
    ).start()

    # P14/P15：门禁快速反馈虽保留了识别结构（供人工复核），但状态必须显式标注
    # 「带警告」，避免下游（落库/列表/详情）把它当成干净的 parsed。
    state["status"] = "parsed_with_warnings" if state.get("gate_warnings") else "parsed"
    if state.get("flagged_reason"):
        state["status"] = "flagged"
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
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "tenant_id": str(state.get("tenant_id") or "default"),
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
            "error_code": str(state.get("error_code") or ""),
            "use_grey": 1 if state.get("use_grey") else 0,
        }
        append_parse_log(rec)
    except Exception as e:
        logging.getLogger("supervisor").warning(f"记忆落盘失败: {e}")
    # ---- T6 Gap A5：线上失败信号回流为评测候选（低置信/门禁拒绝/审核分歧，
    # 幂等去重在 db 层；user_edit 在 api_receipts.save_edited 挂钩。不阻断主链路）----
    try:
        _reflow_from_state(state)
    except Exception as e:
        logging.getLogger("supervisor").warning(f"评测候选回流判定失败(不阻断): {e}")
    return state
