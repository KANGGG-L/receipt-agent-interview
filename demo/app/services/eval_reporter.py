# -*- coding: utf-8 -*-
"""E02：离线配对 A/B 评测器（同一批样本，两臂只差「是否注入记忆」）。

为什么单独成模块（而不是继续散在 run_eval.py 的 `_run_memory_ab`）：
`_run_memory_ab` 只是把两次整批报告相减，既无法证明两臂输入只差记忆注入，也不会把
失败样本留在漏斗里，更没有配对 CI / p95 记账。历史结论因此可能同时被三类问题污染：
① 注入是假的（两臂配置/提示/记忆快照不同，或 on 臂额外拿到 GT 供应商答案）；
② 失败被静默丢弃（分母随失败样本缩小，收益看起来变大）；
③ 未终态样本被截尾后仍报 SLO。本模块把这三件事变成可执行判据：

- **配对性**：每个样本的两臂必须共享同一 prompt/model/config/memory_snapshot/GT 条件；
  两臂 inputs 的差异集合必须恰为 `{"memory_injected"}`，否则整批 `batch_valid=false`。
- **失败入漏斗**：每臂的失败都进 `failures` 与 `failure_funnel`，`n_pairs` 只统计两臂
  都可评分者；未配对样本不参与估值，但必须可见。
- **未终态截尾阻断 SLO**：非终态状态（parsing/running/...）单独计数并 `slo_blocked=true`；
  延迟 p95 只在终态观测上计算，且未终态存在时不得声称 SLO 通过。
- **未测不估值**：没有任何配对样本（或整批 invalid）时，均值/CI 一律 None，不写 0.0。
- **不写生产 defaults**：本模块只做纯计算，不 import app.db / settings_service，也不写
  app_settings；CLI 侧同样只落评测报告。

字段级评分复用 `scripts/run_eval.compare`（per-field / CER / 行对齐的唯一实现），
避免评测器与线上评分器各写一份口径。`run_eval` 采用惰性 import：本模块在 app 层被
导入时不会拉起 scripts 目录下的 argparse/评测依赖。

输入 schema（离线 fixture 与测试共用）：
```
{
  "meta": {"prompt_version": "...", "config_fingerprint": "...",
           "memory_snapshot_id": "...", "slo_p95_ms": 12000},
  "samples": [{
    "sample_id": "S1",
    "shared_inputs": {"prompt_version": "...", "model": "...",
                      "config_fingerprint": "...", "memory_snapshot_id": "...",
                      "gt_source": "...", "gt_condition": "...", "input_hash": "..."},
    "expected": { ...GT... },
    "arms": {
      "on":  {"memory_injected": true, "status": "done", "latency_ms": 900,
              "retry_count": 0, "queue_wait_ms": 0, "queue_depth": 0,
              "predicted": { ... }, "error": ""},
      "off": {"memory_injected": false, ...}
    }
  }]
}
```
两臂各自还应有 `inputs`（= shared_inputs + memory_injected）。若省略 `inputs`，本模块会用
`shared_inputs` 补齐并把 memory_injected 放进去；但 **on 臂不得携带任何 shared_inputs
之外的键**（例如 `gt_answer_hint`），否则判 invalid —— 这正是"目标给 on 额外答案"的形态。
"""

import hashlib
import json
import math
import os
import random
import sys
from datetime import datetime

# 终态：可进入延迟/重试/队列记账与评分（成功或明确失败）。
SUCCESS_STATUSES = frozenset({"done", "succeeded", "success"})
FAILURE_STATUSES = frozenset({"error", "failed"})
TERMINAL_STATUSES = SUCCESS_STATUSES | FAILURE_STATUSES

DEFAULT_SLO_P95_MS = 12000
DEFAULT_BOOTSTRAP_ITERS = 1000
DEFAULT_BOOTSTRAP_SEED = 20261026
# 欠采样/CI 的最小样本：任务卡要求 n 可裁决，1 样本 2 臂的 p95 不能当作 SLO 达成。
DEFAULT_MIN_TERMINAL_ARMS = 30
DEFAULT_MIN_CI_PAIRS = 30

# 参与配对统计的指标；fer/cer 越低越好，accuracy 越高越好。
_METRICS = (
    ("fer", "lower_is_better"),
    ("accuracy", "higher_is_better"),
    ("cer", "lower_is_better"),
)


# -------------------------------------------------------------
# 基础工具（无第三方依赖、确定性）
# -------------------------------------------------------------
def _canonical(value):
    """稳定序列化：用于"两臂输入是否相同"的比较与指纹计算。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _num(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _p95(values):
    """nearest-rank p95；空集返回 None（无观测 ≠ 0ms）。"""
    vals = sorted(float(v) for v in values if v is not None)
    if not vals:
        return None
    rank = max(0, int(math.ceil(0.95 * len(vals))) - 1)
    return round(vals[rank], 1)


def _mean(values):
    vals = [float(v) for v in values if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def _bootstrap_ci(values, iters=DEFAULT_BOOTSTRAP_ITERS, seed=DEFAULT_BOOTSTRAP_SEED,
                  min_pairs=DEFAULT_MIN_CI_PAIRS):
    """配对差值的确定性 bootstrap 95% CI（百分位法）。

    返回 (ci|None, ci_unavailable|"", ci_method)。
    - n < min_pairs（默认 30）：返回 None + 明确原因，不拿 4 个样本的区间冒充高精度；
    - 全部差值相同（零方差）：bootstrap 只会得到 [x, x]，同样是假精度，标 zero_variance；
    - 只有 n 足够且非零方差才输出百分位 CI。
    why 固定 seed：评测报告必须可复算；随机种子写进报告便于复现。
    """
    vals = [float(v) for v in values if v is not None]
    if len(vals) < int(min_pairs):
        return None, "n_pairs=%d < min_pairs=%d" % (len(vals), int(min_pairs)), "unavailable"
    if len({round(v, 12) for v in vals}) <= 1:
        return None, "zero_variance（全部配对差值相同）", "unavailable"
    rng = random.Random(seed)
    n = len(vals)
    means = []
    for _ in range(int(iters)):
        means.append(sum(vals[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    lo_idx = max(0, int(0.025 * (iters - 1)))
    hi_idx = min(len(means) - 1, int(0.975 * (iters - 1)))
    return [round(means[lo_idx], 4), round(means[hi_idx], 4)], "", "bootstrap_percentile"


def _read_defaults(reader):
    """读取生产 defaults 快照；未注入 reader 时返回 None（不 import DB，不污染离线评测）。"""
    if not callable(reader):
        return None
    try:
        return reader()
    except Exception:
        return None


def _load_run_eval():
    """取评分唯一实现 `scripts/run_eval`（惰性 import，避免 app 层 import 期副作用）。

    两条路径都失败时，把 `<demo>/scripts` 加入 sys.path 再导入 —— 从 `demo/app` 侧
    被调用时（例如服务内触发离线评测）通常没有预置 scripts 路径。
    """
    try:
        from scripts import run_eval
        return run_eval
    except Exception:
        pass
    try:
        import run_eval
        return run_eval
    except Exception:
        scripts_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import run_eval
        return run_eval


# -------------------------------------------------------------
# 单臂评分 / 行对齐 / FER 参考分母
# -------------------------------------------------------------
def _score_arm(expected, predicted):
    """用 run_eval.compare 评一臂，产出与"每单差异"直接可比的字段。

    FER 的参考分母显式给出：`compared_field_instances`（整单+明细字段计分实例数），
    分子是 `mismatched_fields`（未命中字段数）。这样 FER 不会因"分母口径没写"被读成
    任意百分比，也不会把 item_count 等非抽取字段混进分子。
    """
    run_eval = _load_run_eval()
    res = run_eval.compare(expected, predicted)
    field_total = int(res.get("field_total") or 0)
    mismatched = max(field_total - int(res.get("field_matched") or 0), 0)
    return {
        "ok": bool(res.get("ok")),
        "field_total": field_total,
        "field_mismatched": mismatched,
        "fer": round(mismatched / float(field_total), 4) if field_total else None,
        "accuracy": 1.0 if res.get("ok") else 0.0,
        "cer": float(res.get("cer") or 0.0),
        "mismatched_fields": list(res.get("mismatched_fields") or []),
        "items_predicted": int(res.get("item_total") or 0),
        "fer_reference": {
            "numerator": "mismatched_fields",
            "denominator": "compared_field_instances",
            "numerator_value": mismatched,
            "denominator_value": field_total,
            "formula": "mismatched_fields / compared_field_instances",
        },
    }


def _line_alignment(expected, predicted):
    """逐行对齐（复用 run_eval._align_items），产出可核对的 FER 行级参考。

    `rate` 分母是 GT 行数；GT 为空时返回 None（不是 0%）。未配对 GT / 预测分别计数，
    避免把"漏行"与"多抽行"混成一个数字。
    """
    run_eval = _load_run_eval()
    exp = run_eval.normalize(expected)
    pred = run_eval.normalize(predicted)
    pairs = run_eval._align_items(exp.get("items") or [], pred.get("items") or [])
    gt_lines = len(exp.get("items") or [])
    pred_lines = len(pred.get("items") or [])
    aligned = sum(1 for e, p in pairs if e is not None and p is not None)
    unmatched_gt = sum(1 for e, p in pairs if e is not None and p is None)
    unmatched_pred = sum(1 for e, p in pairs if e is None and p is not None)
    return {
        "gt_lines": gt_lines,
        "pred_lines": pred_lines,
        "aligned_lines": aligned,
        "unmatched_gt_lines": unmatched_gt,
        "unmatched_pred_lines": unmatched_pred,
        "alignment_rate": round(aligned / float(gt_lines), 4) if gt_lines else None,
    }


# -------------------------------------------------------------
# 配对性校验
# -------------------------------------------------------------
def _arm_inputs(sample, arm_name, arm):
    """取一臂的输入快照；缺 `inputs` 时用 shared_inputs + memory_injected 补齐。

    注意：补齐只补 memory_injected，**绝不**把 expected/GT 答案塞进任一臂。任何额外键
    都必须来自 fixture 的显式声明，并会在下面的差异集合里被抓到。
    """
    shared = _as_dict(sample.get("shared_inputs"))
    inputs = arm.get("inputs")
    if isinstance(inputs, dict):
        merged = dict(inputs)
    else:
        merged = dict(shared)
        merged["memory_injected"] = bool(arm.get("memory_injected"))
    return merged


def _input_diff_keys(on_inputs, off_inputs):
    keys = set(on_inputs) | set(off_inputs)
    return sorted(k for k in keys
                  if _canonical(on_inputs.get(k)) != _canonical(off_inputs.get(k)))


def _asset_signature(shared_inputs):
    """资产/配置指纹：两臂共享，且整批必须一致；缺关键键返回 None 由调用方判 invalid。"""
    keys = ("prompt_version", "model", "config_fingerprint",
            "memory_snapshot_id", "gt_source", "gt_condition")
    if any(k not in shared_inputs for k in keys[:4]):
        return None
    return tuple(_canonical(shared_inputs.get(k)) for k in keys)


def _validate_pairing(sample, on_arm, off_arm, shared_inputs):
    """返回该样本的配对性问题列表（空 = 合法）。"""
    problems = []
    on_inputs = _arm_inputs(sample, "on", on_arm)
    off_inputs = _arm_inputs(sample, "off", off_arm)
    diff = _input_diff_keys(on_inputs, off_inputs)
    if diff != ["memory_injected"]:
        problems.append(
            "两臂输入差异超出 memory 注入（%s）；on 不得拿到额外答案/配置，否则收益混杂"
            % (",".join(diff) or "无差异"))
    if on_inputs.get("memory_injected") is not True:
        problems.append("on 臂 memory_injected 必须为 true")
    if off_inputs.get("memory_injected") is not False:
        problems.append("off 臂 memory_injected 必须为 false")

    # shared_inputs 是两臂共同事实源；任一臂擅自覆盖（除 memory_injected）都算漂移。
    for name, inputs in (("on", on_inputs), ("off", off_inputs)):
        for key, value in shared_inputs.items():
            if key == "memory_injected":
                continue
            if _canonical(inputs.get(key)) != _canonical(value):
                problems.append("%s 臂覆盖了 shared_inputs.%s（配置漂移）" % (name, key))
    return problems


# -------------------------------------------------------------
# 单臂失败判定（失败必须入漏斗，不丢）
# -------------------------------------------------------------
def _arm_failure(sample_id, arm_name, arm, expected):
    """返回 (失败理由|None, 是否终态)。失败理由非空即不可评分。"""
    if not isinstance(arm, dict):
        return ("缺 %s 臂" % arm_name, False)
    status = str(arm.get("status") or "").strip()
    if status not in TERMINAL_STATUSES:
        return ("未终态（status=%s）" % (status or "missing"), False)
    if status in FAILURE_STATUSES:
        return (str(arm.get("error") or "臂状态=%s" % status), True)
    if expected is None:
        return ("GT 缺失（无法比对）", True)
    if arm.get("predicted") is None:
        return (str(arm.get("error") or "成功态但预测为空"), True)
    return (None, True)


# -------------------------------------------------------------
# 主入口
# -------------------------------------------------------------
def run_paired_eval(samples, slo_p95_ms=DEFAULT_SLO_P95_MS,
                    bootstrap_iters=DEFAULT_BOOTSTRAP_ITERS,
                    bootstrap_seed=DEFAULT_BOOTSTRAP_SEED,
                    min_terminal_arms=DEFAULT_MIN_TERMINAL_ARMS,
                    min_ci_pairs=DEFAULT_MIN_CI_PAIRS,
                    defaults_reader=None):
    """跑离线配对 A/B，返回可复算报告（纯函数，不写库/不改默认值）。

    参数 `samples` 见模块 docstring；`slo_p95_ms` 是延迟 SLO 阈值（默认 12s，E03 门槛）。
    `min_terminal_arms`：p95 SLO 的最小终态臂数（默认 30，显式可配）；不足时
    `p95_undersampled=true` 且 `slo_pass=false`。
    `min_ci_pairs`：CI 最小配对数（默认 30）；不足或差值零方差时 `ci95=None` 并给出
    `ci_unavailable`，不输出零宽点估计冒充高精度。
    `defaults_reader`：可选 `callable() -> dict`，评测前后各读一次生产 defaults 快照，
    用于**真实比对**（未注入则不 import DB，`production_defaults_modified=None`）。
    返回 dict 的关键键：batch_valid / estimable / n_pairs / stats / per_sample_diff /
    failures / failure_funnel / latency / retry_queue / line_alignment / fer_reference /
    slo / production_defaults_*。
    """
    before_defaults = _read_defaults(defaults_reader)
    samples = list(samples or [])
    invalid_reasons = []
    if not samples:
        invalid_reasons.append("无样本：不允许出估值")

    per_sample_diff = []
    failures = []
    paired = []
    terminal_latencies = []
    retry_values = []
    queue_values = []
    queue_depth_max = 0
    signatures = []
    asset_info = {}
    terminal_arm_count = 0

    for idx, sample in enumerate(samples):
        sample = _as_dict(sample)
        sample_id = str(sample.get("sample_id") or "sample_%d" % (idx + 1))
        shared_inputs = _as_dict(sample.get("shared_inputs"))
        arms = _as_dict(sample.get("arms"))
        on_arm = arms.get("on")
        off_arm = arms.get("off")
        expected = sample.get("expected")

        if not shared_inputs:
            invalid_reasons.append("%s：缺 shared_inputs（无法证明两臂同配置）" % sample_id)
        signature = _asset_signature(shared_inputs)
        if signature is None:
            invalid_reasons.append(
                "%s：shared_inputs 缺 prompt_version/model/config_fingerprint/memory_snapshot_id"
                % sample_id)
        else:
            signatures.append(signature)
            if not asset_info:
                asset_info = {k: shared_inputs.get(k) for k in (
                    "prompt_version", "model", "config_fingerprint",
                    "memory_snapshot_id", "gt_source", "gt_condition")}
        if isinstance(on_arm, dict) and isinstance(off_arm, dict):
            invalid_reasons.extend("%s：%s" % (sample_id, p)
                                   for p in _validate_pairing(
                                       sample, on_arm, off_arm, shared_inputs))

        # 失败逐臂入漏斗（即使另一臂成功；也不因整批 invalid 而丢弃）
        on_reason, on_terminal = _arm_failure(sample_id, "on", on_arm, expected)
        off_reason, off_terminal = _arm_failure(sample_id, "off", off_arm, expected)
        for name, arm, reason, terminal in (
                ("on", on_arm, on_reason, on_terminal),
                ("off", off_arm, off_reason, off_terminal)):
            if isinstance(arm, dict) and terminal:
                terminal_arm_count += 1
                terminal_latencies.append(arm.get("latency_ms"))
                retry_values.append(int(_num(arm.get("retry_count"), 0)))
                queue_values.append(_num(arm.get("queue_wait_ms"), 0.0))
                queue_depth_max = max(queue_depth_max,
                                      int(_num(arm.get("queue_depth"), 0)))
            if reason:
                failures.append({
                    "sample_id": sample_id,
                    "arm": name,
                    "status": (arm or {}).get("status") if isinstance(arm, dict) else "missing",
                    "reason": reason,
                    # 终态失败（error/failed/空预测）与未终态截尾分开计数：
                    # 前者是"跑了但失败"，后者是"还没跑完"，SLO 口径完全不同。
                    "terminal": bool(terminal),
                })

        if on_reason or off_reason:
            per_sample_diff.append({
                "sample_id": sample_id,
                "scored": False,
                "on": {"status": (on_arm or {}).get("status") if isinstance(on_arm, dict) else "missing"},
                "off": {"status": (off_arm or {}).get("status") if isinstance(off_arm, dict) else "missing"},
                "failure_reasons": {"on": on_reason, "off": off_reason},
                "delta": None,
            })
            continue

        on_score = _score_arm(expected, on_arm.get("predicted"))
        off_score = _score_arm(expected, off_arm.get("predicted"))
        on_line = _line_alignment(expected, on_arm.get("predicted"))
        off_line = _line_alignment(expected, off_arm.get("predicted"))
        delta = {}
        for metric, _direction in _METRICS:
            a, b = on_score.get(metric), off_score.get(metric)
            delta[metric] = (None if a is None or b is None else round(a - b, 4))
        paired.append({
            "sample_id": sample_id,
            "on": {k: on_score[k] for k in ("ok", "fer", "accuracy", "cer",
                                            "field_total", "field_mismatched")},
            "off": {k: off_score[k] for k in ("ok", "fer", "accuracy", "cer",
                                              "field_total", "field_mismatched")},
            "delta": delta,
            "line_alignment": {"on": on_line, "off": off_line},
        })
        per_sample_diff.append({
            "sample_id": sample_id,
            "scored": True,
            "on": dict(paired[-1]["on"], line_alignment=on_line,
                       latency_ms=_num(on_arm.get("latency_ms"), 0.0),
                       retry_count=int(_num(on_arm.get("retry_count"), 0)),
                       queue_wait_ms=_num(on_arm.get("queue_wait_ms"), 0.0)),
            "off": dict(paired[-1]["off"], line_alignment=off_line,
                        latency_ms=_num(off_arm.get("latency_ms"), 0.0),
                        retry_count=int(_num(off_arm.get("retry_count"), 0)),
                        queue_wait_ms=_num(off_arm.get("queue_wait_ms"), 0.0)),
            "delta": delta,
            "failure_reasons": {"on": None, "off": None},
        })

    # 资产/配置漂移：整批 invalid（不是"逐样本剔除后继续算"——那会把不同资产混成一个均值）
    if len(set(signatures)) > 1:
        invalid_reasons.append(
            "资产/配置漂移：整批出现 %d 种 (prompt/model/config/memory_snapshot/GT) 组合"
            % len(set(signatures)))
    batch_valid = not invalid_reasons
    n_pairs = len(paired)

    stats = {}
    for metric, direction in _METRICS:
        on_vals = [p["on"].get(metric) for p in paired if p["on"].get(metric) is not None]
        off_vals = [p["off"].get(metric) for p in paired if p["off"].get(metric) is not None]
        deltas = [p["delta"].get(metric) for p in paired if p["delta"].get(metric) is not None]
        estimable = bool(batch_valid and n_pairs > 0)
        if estimable:
            ci, ci_unavailable, ci_method = _bootstrap_ci(
                deltas, bootstrap_iters, bootstrap_seed, min_ci_pairs)
        else:
            ci, ci_unavailable, ci_method = None, "not_estimable", "unavailable"
        stats[metric] = {
            "direction": direction,
            "n_pairs": n_pairs,
            "mean_on": _mean(on_vals) if estimable else None,
            "mean_off": _mean(off_vals) if estimable else None,
            "mean_delta_on_minus_off": _mean(deltas) if estimable else None,
            "ci95": ci,
            "ci_method": ci_method,
            "ci_unavailable": ci_unavailable,
            "min_pairs_for_ci": int(min_ci_pairs),
            "estimable": estimable,
        }

    latency = {
        "n_terminal_arms": len([v for v in terminal_latencies if v is not None]),
        "p50_ms": None,
        "p95_ms": _p95(terminal_latencies),
        "max_ms": (round(max(terminal_latencies), 1)
                   if any(v is not None for v in terminal_latencies) else None),
    }
    if latency["n_terminal_arms"]:
        vals = sorted(float(v) for v in terminal_latencies if v is not None)
        mid = len(vals) // 2
        latency["p50_ms"] = round(
            vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0, 1)

    retry_queue = {
        "retry_total": sum(retry_values),
        "retry_p95": _p95(retry_values),
        "queue_wait_total_ms": round(sum(queue_values), 1) if queue_values else 0.0,
        "queue_wait_p95_ms": _p95(queue_values),
        "max_queue_depth": queue_depth_max,
        "n_arms_accounted": len(retry_values),
    }

    # 行对齐与 FER 参考（两臂分别汇总；报告的是"被评分样本"上的口径）
    line_summary = {}
    fer_reference = {}
    for arm_key in ("on", "off"):
        rows = [p["line_alignment"][arm_key] for p in paired]
        gt = sum(r["gt_lines"] for r in rows)
        aligned = sum(r["aligned_lines"] for r in rows)
        line_summary[arm_key] = {
            "gt_lines": gt,
            "pred_lines": sum(r["pred_lines"] for r in rows),
            "aligned_lines": aligned,
            "unmatched_gt_lines": sum(r["unmatched_gt_lines"] for r in rows),
            "unmatched_pred_lines": sum(r["unmatched_pred_lines"] for r in rows),
            "alignment_rate": round(aligned / float(gt), 4) if gt else None,
            "n_samples": len(rows),
        }
        field_total = sum(p[arm_key]["field_total"] for p in paired)
        mismatched = sum(p[arm_key]["field_mismatched"] for p in paired)
        fer_reference[arm_key] = {
            "field_total": field_total,
            "field_mismatched": mismatched,
            "fer": round(mismatched / float(field_total), 4) if field_total else None,
            "formula": "mismatched_fields / compared_field_instances",
        }

    # failed 与 nonterminal 语义分开：failed=终态失败（跑了但错/空），
    # nonterminal=未终态截尾（还没跑完，不能既算作失败又算作截尾）。
    nonterminal = [f for f in failures if not f.get("terminal")]
    terminal_failed = [f for f in failures if f.get("terminal")]
    min_arms = max(1, int(min_terminal_arms))
    undersampled = terminal_arm_count < min_arms
    latency["min_terminal_arms"] = min_arms
    latency["p95_undersampled"] = undersampled

    slo_blocked_reasons = []
    if not batch_valid:
        slo_blocked_reasons.append("整批 invalid（资产/配置漂移或配对性不成立），不得声称 SLO")
    if nonterminal:
        slo_blocked_reasons.append("存在 %d 个未终态臂：截尾样本阻断 SLO" % len(nonterminal))
    if undersampled:
        slo_blocked_reasons.append(
            "终态臂数 %d < 最小 %d：p95 欠采样，不得声称 SLO" % (terminal_arm_count, min_arms))
    if latency["p95_ms"] is None:
        slo_blocked_reasons.append("无终态延迟观测：未测不估值")
    if n_pairs == 0:
        # 没有任何配对样本 = 没有可裁决的收益；延迟即便有观测也不能单独声称 SLO 达成。
        slo_blocked_reasons.append("无配对样本：未测不估值")
    slo_blocked = bool(slo_blocked_reasons)
    slo_pass = bool(batch_valid and not slo_blocked and not undersampled
                    and latency["p95_ms"] is not None
                    and latency["p95_ms"] <= float(slo_p95_ms))

    failure_funnel = {
        "attempted_arms": len(samples) * 2,
        "terminal_arms": terminal_arm_count,
        "scored_arms": n_pairs * 2,
        "paired_samples": n_pairs,
        "unpaired_samples": len(samples) - n_pairs,
        "terminal_failed_arms": len(terminal_failed),
        "failed_arms": len(terminal_failed),
        "nonterminal_arms": len(nonterminal),
        "failure_reasons_preserved": len(failures),
    }
    after_defaults = _read_defaults(defaults_reader)
    defaults_verified = before_defaults is not None and after_defaults is not None
    defaults_modified = ((before_defaults != after_defaults)
                         if defaults_verified else None)

    return {
        "schema_version": "paired_ab_v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "n_samples": len(samples),
        "n_pairs": n_pairs,
        "batch_valid": batch_valid,
        "invalid_reasons": invalid_reasons,
        "estimable": bool(batch_valid and n_pairs > 0),
        "asset": asset_info,
        "asset_fingerprint": _fingerprint(asset_info) if asset_info else None,
        "stats": stats,
        "per_sample_diff": per_sample_diff,
        "failures": failures,
        "failure_funnel": failure_funnel,
        "latency": latency,
        "retry_queue": retry_queue,
        "line_alignment": line_summary,
        "fer_reference": fer_reference,
        "slo": {
            "slo_p95_ms": float(slo_p95_ms),
            "p95_ms": latency["p95_ms"],
            "slo_blocked": slo_blocked,
            "slo_blocked_reasons": slo_blocked_reasons,
            "slo_pass": slo_pass,
        },
        "criteria": {
            "same_samples": "两臂共用同一 sample 列表与 expected（GT）对象",
            "paired_variation": "两臂 inputs 差异集合必须恰为 {'memory_injected'}；"
                                "on 臂额外携带 GT 答案/提示即整批 invalid",
            "gt_condition": "GT 供应商条件在两臂相同（shared_inputs.gt_condition），"
                            "使用方式显式记录；不得只给 on 臂答案",
            "failure_accounting": "每臂失败都进 failures 与 failure_funnel；n_pairs 只含两臂可评分样本",
            "slo": "非终态（parsing/running/...）单独计数并阻断 SLO；p95 仅在终态观测上计算；"
                   "终态臂数 < min_terminal_arms 时 p95_undersampled=true 且不得通过",
            "estimation": "batch_valid=false 或 n_pairs=0 时均值/CI 为 None，不写 0.0；"
                          "n_pairs<min_ci_pairs 或差值零方差时 ci95=None 并标 ci_unavailable",
            "defaults": "可选注入 defaults_reader 做前后快照真实比对；未注入时 "
                        "production_defaults_modified=None（未验证），不硬编码 False",
        },
        "production_defaults_modified": defaults_modified,
        "production_defaults_verified": defaults_verified,
        "production_defaults_before": before_defaults,
        "production_defaults_after": after_defaults,
        "bootstrap": {"iters": int(bootstrap_iters), "seed": int(bootstrap_seed),
                      "min_ci_pairs": int(min_ci_pairs)},
        "sampling": {"min_terminal_arms": min_arms,
                     "terminal_arms": terminal_arm_count,
                     "p95_undersampled": undersampled},
    }
