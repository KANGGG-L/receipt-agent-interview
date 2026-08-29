# -*- coding: utf-8 -*-
"""可复现评测 harness —— Gap A1 + A5a。

读 `demo/evalsets/manifest.csv` 的某个 split → 逐张跑抽取 → 与 expected/ GT 比对 →
输出 `EvalReport` 并落盘 `ai_registry/benchmarks/eval_runs/<ts>_<split>_<prompt_ver>.json`。

用法：
    python run_eval.py --split test --prompt v1_2_0_sku_clean
    python run_eval.py --split test --prompt v1_2_8_anti_injection --engine opencode \\
                       --model opencode/mimo-v2.5-free --limit 20
    python run_eval.py --split val --prompt v1_2_0_sku_clean --engine stub   # harness 自检（离线）

`--engine stub` 直接回读 expected 作为预测，**仅用于验证 harness 通路，严禁用于出分**；
报告里 engine 字段会如实写 "stub"。

契约：run_eval(split, prompt_version, engine, model) -> EvalReport
    EvalReport = {accuracy, cer, per_field_accuracy, edit_proxy, evidence_coverage,
                  avg_tokens, p50_latency_ms, gt_status_breakdown, sample_ids[]}
"""

import argparse
import csv
import json
import os
import re
import statistics
import sys
import time
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
DEMO_DIR = os.path.abspath(os.path.join(_HERE, ".."))
REPO_ROOT = os.path.abspath(os.path.join(DEMO_DIR, ".."))
for _p in (DEMO_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DEFAULT_EVALSET_DIR = os.path.join(DEMO_DIR, "evalsets")
DEFAULT_REPORT_DIR = os.path.join(REPO_ROOT, "ai_registry", "benchmarks", "eval_runs")
DEFAULT_PROMPT_VERSION = "v1_2_0_sku_clean"

# 比对字段：整单 4 项 + 明细行 5 项；item_count 单独统计（漏行/多行的关键信号）
TOP_FIELDS = ("vendor", "date", "total", "doc_form")
ITEM_FIELDS = ("name", "qty", "unit", "unit_price", "amount")


# ------------------------------------------------------------------
# 归一化
# ------------------------------------------------------------------
def _norm_text(s):
    """文本归一：去空白/常见标点 + 全角半角 + 大小写。"""
    s = str(s if s is not None else "").strip().lower()
    s = re.sub(r"[\s　]+", "", s)
    s = re.sub(r"[()（）\[\]【】,，.。:：\-_/\\|]", "", s)
    return s


def _norm_name(s):
    return _norm_text(s)


def _norm_date(s):
    """日期归一：接受 2026-08-01 / 2026/8/1 / 01-08-2026 等，统一成 YYYY-MM-DD。"""
    s = str(s if s is not None else "").strip()
    m = re.search(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", s)
    if m:
        return "%04d-%02d-%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return s


def _norm_num(v, ndigits=2):
    """数值归一：货币符号/千分位/单位后缀 → float，失败返回 None（None 视为缺失）。"""
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return round(float(v), ndigits)
    m = re.search(r"[-+]?\d[\d,]*\.?\d*", str(v).replace(" ", ""))
    if not m:
        return None
    try:
        return round(float(m.group(0).replace(",", "")), ndigits)
    except ValueError:
        return None


def normalize(payload):
    """把 GT / 预测统一成比对用的结构（兼容 supplier_name / total_amount / quantity 等别名）。"""
    if payload is None:
        return {"doc_form": "", "vendor": "", "date": "", "total": None, "items": []}
    if not isinstance(payload, dict):
        try:
            payload = dict(payload)
        except Exception:
            return {"doc_form": "", "vendor": "", "date": "", "total": None, "items": []}

    items = []
    for it in (payload.get("items") or []):
        if not isinstance(it, dict):
            continue
        name = it.get("name", it.get("item_name", ""))
        qty = it.get("qty", it.get("quantity"))
        items.append({
            "name": _norm_name(name),
            "qty": _norm_num(qty),
            "unit": _norm_text(it.get("unit", "")),
            "unit_price": _norm_num(it.get("unit_price")),
            "amount": _norm_num(it.get("amount")),
            "evidence": it.get("evidence"),
        })
    total = payload.get("total", payload.get("total_amount"))
    return {
        "doc_form": _norm_text(payload.get("doc_form", "")),
        "vendor": _norm_text(payload.get("vendor", payload.get("supplier_name", ""))),
        "date": _norm_date(payload.get("date", "")),
        "total": _norm_num(total),
        "items": items,
    }


# ------------------------------------------------------------------
# 编辑距离（CER）
# ------------------------------------------------------------------
def _levenshtein(a, b):
    """标准 DP 编辑距离（无第三方依赖）。"""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1]


# ------------------------------------------------------------------
# 证据覆盖率
# ------------------------------------------------------------------
def _evidence_of(item):
    """取明细行的字段级证据；dict 与对象（ReceiptItem）都兼容。"""
    if isinstance(item, dict):
        return item.get("evidence")
    return getattr(item, "evidence", None)


def _evidence_coverage(items):
    """有字段级证据的明细行占比（T7 前恒为 0，不阻断）。"""
    if not items:
        return 0.0
    hit = sum(1 for it in items if _evidence_of(it))
    return round(hit / float(len(items)), 4)


# ------------------------------------------------------------------
# 单张比对
# ------------------------------------------------------------------
def _align_items(exp_items, pred_items):
    """明细行对齐：先按归一化品名贪心配对，剩下按下标对齐。

    返回 [(exp_item|None, pred_item|None)]，未配上的一侧为 None（计为全错）。
    """
    pairs = []
    used = set()
    for e in exp_items:
        ename = e.get("name", "")
        matched = None
        for j, p in enumerate(pred_items):
            if j in used:
                continue
            if ename and p.get("name") == ename:
                matched = j
                break
        if matched is None:
            pairs.append((e, None))
        else:
            used.add(matched)
            pairs.append((e, pred_items[matched]))
    for j, p in enumerate(pred_items):
        if j not in used:
            pairs.append((None, p))
    return pairs


def compare(expected, predicted):
    """比对单张样本的预测与 GT。

    返回 {ok, field_total, field_matched, mismatched_fields, field_stats,
          cer, cer_dist, cer_chars, item_total, items_with_evidence}
    field_stats 为 {字段名: [total, matched]}，供整集层面精确聚合 per_field_accuracy。
    """
    exp = normalize(expected)
    pred = normalize(predicted)
    mismatched = []
    field_stats = {}

    def _cmp(field, a, b):
        stat = field_stats.setdefault(field, [0, 0])
        stat[0] += 1
        if a == b:
            stat[1] += 1
        else:
            mismatched.append(field)

    for field in TOP_FIELDS:
        _cmp(field, exp.get(field), pred.get(field))

    exp_items = exp["items"]
    pred_items = pred["items"]
    _cmp("item_count", len(exp_items), len(pred_items))

    dist = 0
    chars = 0
    for e, p in _align_items(exp_items, pred_items):
        for field in ITEM_FIELDS:
            _cmp("item.%s" % field, (e or {}).get(field), (p or {}).get(field))
        # CER 统计文本转录质量：品名逐行累加编辑距离
        e_name = (e or {}).get("name", "") or ""
        p_name = (p or {}).get("name", "") or ""
        dist += _levenshtein(e_name, p_name)
        chars += len(e_name)
    # 供应商名同样计入 CER
    dist += _levenshtein(exp.get("vendor", ""), pred.get("vendor", ""))
    chars += len(exp.get("vendor", ""))

    total = sum(v[0] for v in field_stats.values())
    matched = sum(v[1] for v in field_stats.values())
    return {
        "ok": not mismatched,
        "field_total": total,
        "field_matched": matched,
        "mismatched_fields": mismatched,
        "field_stats": field_stats,
        "cer": round(dist / float(chars), 4) if chars else 0.0,
        "cer_dist": dist,
        "cer_chars": chars,
        "item_total": len(pred_items),
        "items_with_evidence": sum(1 for it in pred_items if _evidence_of(it)),
    }


# ------------------------------------------------------------------
# manifest / GT 读取
# ------------------------------------------------------------------
def load_manifest(evalset_dir):
    path = os.path.join(evalset_dir, "manifest.csv")
    if not os.path.exists(path):
        raise SystemExit(
            "评测集 manifest 不存在：%s\n"
            "请先运行 build_evalset.py 构建（语料属业务数据，不入库）" % path)
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _expected_path(evalset_dir, sample_id):
    return os.path.join(evalset_dir, "expected", sample_id + ".json")


def _load_expected(evalset_dir, sample_id):
    path = _expected_path(evalset_dir, sample_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ------------------------------------------------------------------
# 预测器
# ------------------------------------------------------------------
def _stub_predictor(evalset_dir):
    """harness 自检用：直接回读 GT 作为预测，验证比对与聚合通路（离线、零成本）。"""
    def _predict(image_path, sample_id):
        return _load_expected(evalset_dir, sample_id), {
            "latency_ms": 0.0,
            "tokens": 0,
            "error": None if _load_expected(evalset_dir, sample_id) else "GT 缺失",
        }
    return _predict


def _real_predictor(engine, model, prompt_version):
    """真实识别链路：按 prompt_version 换 SYSTEM_PROMPT，按 engine/model 建模型。"""
    from app.chains import extract_chain
    from app.models import EngineConfig, EngineKind
    from app.prompts import get_prompt

    cfg = EngineConfig()
    if engine:
        try:
            cfg.recognition_engine = EngineKind(str(engine).lower())
        except ValueError:
            raise SystemExit("未知引擎：%s（可选：%s）"
                             % (engine, ", ".join(e.value for e in EngineKind)))
    if model:
        cfg.recognition_model = model
    # 单事实源（T12）：app.prompts 为 ai_registry 的 re-export 委托层，
    # 显式版本号直接落在 ai_registry/prompts/extract/<version>.py
    extract_chain.SYSTEM_PROMPT = get_prompt("extract", prompt_version)

    def _predict(image_path, sample_id):
        res = extract_chain.extract_receipt(image_path, config=cfg)
        data = res.get("data")
        pred = data.model_dump() if data is not None else None
        return pred, {
            "latency_ms": float(res.get("extract_ms") or res.get("elapsed_ms") or 0.0),
            "tokens": int((res.get("token_usage") or {}).get("total_tokens", 0) or 0),
            "error": res.get("error"),
        }
    return _predict


# ------------------------------------------------------------------
# 主流程
# ------------------------------------------------------------------
def run_eval(split, prompt_version=DEFAULT_PROMPT_VERSION, engine="opencode", model=None,
             limit=None, evalset_dir=None, report_dir=None, predictor=None,
             require_confirmed=False):
    """跑一个 split 的评测，返回 EvalReport（dict）。

    predictor（可选）：自定义预测器 `(image_path, sample_id) -> (payload|None, meta)`，
    供测试注入故障或离线自检；不传则按 engine 构建真实识别链路。

    require_confirmed（T4，默认关，向后兼容）：为 True 时，该 split 存在任何
    未 confirmed 的 GT（missing/draft）即拒绝出分（SystemExit），保证报告只由
    人工抽检确认过的 GT 产生。
    """
    evalset_dir = os.path.abspath(evalset_dir or DEFAULT_EVALSET_DIR)
    report_dir = os.path.abspath(report_dir or DEFAULT_REPORT_DIR)

    rows = [r for r in load_manifest(evalset_dir) if r.get("split") == split]
    if not rows:
        raise SystemExit("manifest 中没有 split=%s 的样本" % split)
    if limit:
        rows = rows[:int(limit)]

    if require_confirmed:
        unconfirmed = [r for r in rows
                       if (r.get("gt_status") or "missing") != "confirmed"
                       or _load_expected(evalset_dir, r["sample_id"]) is None]
        if unconfirmed:
            by_status = {}
            for r in unconfirmed:
                s = r.get("gt_status") or "missing"
                by_status[s] = by_status.get(s, 0) + 1
            raise SystemExit(
                "GT 未全部人工确认，--require-confirmed 拒绝出分：split=%s 共 %d 张未确认"
                "（%s），例如 %s。请先在 GT 抽检台（/evalset）逐张确认后再出分。"
                % (split, len(unconfirmed),
                   ", ".join("%s=%d" % kv for kv in sorted(by_status.items())),
                   ", ".join(r["sample_id"] for r in unconfirmed[:5])))

    if predictor is None:
        predictor = (_stub_predictor(evalset_dir) if engine == "stub"
                     else _real_predictor(engine, model, prompt_version))

    per_sample = []
    field_total = field_matched = 0
    cer_dist = cer_chars = 0
    items_total = items_with_evidence = 0
    field_stats = {}
    latencies, tokens_list = [], []
    gt_status_breakdown = {}
    scored = ok_count = error_count = 0

    for row in rows:
        sample_id = row["sample_id"]
        gt_status = row.get("gt_status", "missing") or "missing"
        expected = _load_expected(evalset_dir, sample_id)
        if expected is None:
            gt_status = "missing"
        gt_status_breakdown[gt_status] = gt_status_breakdown.get(gt_status, 0) + 1

        record = {
            "sample_id": sample_id,
            "doc_form": row.get("doc_form", ""),
            "gt_status": gt_status,
            "ok": False,
            "mismatched_fields": [],
            "latency_ms": 0.0,
            "tokens": 0,
            "error": "",
        }
        if expected is None:
            record["error"] = "GT 缺失（未生成或未确认）"
            per_sample.append(record)
            continue

        image_path = os.path.join(evalset_dir, row.get("image", ""))
        t0 = time.time()
        try:
            pred, meta = predictor(image_path, sample_id)
            elapsed_ms = float(meta.get("latency_ms") or 0.0) or \
                round((time.time() - t0) * 1000, 1)
            error = meta.get("error") or ("" if pred is not None else "抽取返回空")
        except Exception as e:
            pred, elapsed_ms, tokens, error = None, round((time.time() - t0) * 1000, 1), 0, str(e)[:200]
        else:
            tokens = int(meta.get("tokens") or 0)

        latencies.append(elapsed_ms)
        tokens_list.append(tokens)
        record["latency_ms"] = elapsed_ms
        record["tokens"] = tokens
        record["error"] = error or ""

        if pred is None:
            error_count += 1
            per_sample.append(record)
            continue

        scored += 1
        res = compare(expected, pred)
        field_total += res["field_total"]
        field_matched += res["field_matched"]
        items_total += res["item_total"]
        items_with_evidence += res["items_with_evidence"]
        cer_dist += res["cer_dist"]
        cer_chars += res["cer_chars"]
        for name, (t, m) in res["field_stats"].items():
            stat = field_stats.setdefault(name, [0, 0])
            stat[0] += t
            stat[1] += m
        if res["ok"]:
            ok_count += 1
        record["ok"] = res["ok"]
        record["mismatched_fields"] = res["mismatched_fields"]
        per_sample.append(record)

    per_field_accuracy = {
        name: round(stat[1] / float(stat[0]), 4)
        for name, stat in sorted(field_stats.items()) if stat[0]
    }

    report = {
        "split": split,
        "prompt_version": prompt_version,
        "engine": engine,
        "model": model or "",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "n_samples": len(rows),
        "n_scored": scored,
        "errors": error_count,
        "accuracy": round(ok_count / float(scored), 4) if scored else 0.0,
        "cer": round(cer_dist / float(cer_chars), 4) if cer_chars else 0.0,
        "per_field_accuracy": per_field_accuracy,
        "edit_proxy": round((field_total - field_matched) / float(scored), 4) if scored else 0.0,
        "evidence_coverage": round(items_with_evidence / float(items_total), 4) if items_total else 0.0,
        "avg_tokens": round(statistics.mean(tokens_list), 1) if tokens_list else 0.0,
        "p50_latency_ms": round(statistics.median(latencies), 1) if latencies else 0.0,
        "gt_status_breakdown": gt_status_breakdown,
        "sample_ids": [r["sample_id"] for r in rows],
        "per_sample": per_sample,
    }

    os.makedirs(report_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(report_dir, "%s_%s_%s.json" % (ts, split, prompt_version))
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    report["report_path"] = out_path
    return report


def _print_summary(report):
    print("=" * 68)
    print("EvalReport | split=%s | prompt=%s | engine=%s | model=%s"
          % (report["split"], report["prompt_version"], report["engine"],
             report["model"] or "-"))
    print("-" * 68)
    print("样本 %d（有效比对 %d，失败 %d）" %
          (report["n_samples"], report["n_scored"], report["errors"]))
    print("  accuracy          %.4f   （整单零编辑率）" % report["accuracy"])
    print("  cer               %.4f" % report["cer"])
    print("  edit_proxy        %.4f   （平均每单需修改字段数）" % report["edit_proxy"])
    print("  evidence_coverage %.4f" % report["evidence_coverage"])
    print("  avg_tokens        %.1f" % report["avg_tokens"])
    print("  p50_latency_ms    %.1f" % report["p50_latency_ms"])
    print("  gt_status         %s" % report["gt_status_breakdown"])
    print("  per_field         %s" % json.dumps(report["per_field_accuracy"], ensure_ascii=False))
    print("report → %s" % report["report_path"])
    print("=" * 68)


def main():
    ap = argparse.ArgumentParser(description="可复现评测 harness（train/val/test 三分法）")
    ap.add_argument("--split", required=True, choices=["train", "val", "test"], help="评测集分片")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT_VERSION, help="extract prompt 版本")
    ap.add_argument("--engine", default="opencode",
                    help="识别引擎：%s / stub（harness 自检，不出分）" % "/".join(
                        ["codebuddy", "opencode", "openai"]))
    ap.add_argument("--model", default=None, help="识别模型名（覆盖引擎默认）")
    ap.add_argument("--limit", type=int, default=None, help="只跑前 N 张（按 manifest 顺序）")
    ap.add_argument("--evalset-dir", default=None, help="评测集目录（默认 demo/evalsets）")
    ap.add_argument("--report-dir", default=None,
                    help="报告落盘目录（默认 ai_registry/benchmarks/eval_runs）")
    ap.add_argument("--require-confirmed", action="store_true", default=False,
                    help="GT 门禁：split 内存在未人工确认（missing/draft）的 GT 时拒绝出分")
    args = ap.parse_args()

    report = run_eval(split=args.split, prompt_version=args.prompt, engine=args.engine,
                      model=args.model, limit=args.limit,
                      evalset_dir=args.evalset_dir, report_dir=args.report_dir,
                      require_confirmed=args.require_confirmed)
    _print_summary(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
