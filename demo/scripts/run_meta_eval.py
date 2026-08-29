# -*- coding: utf-8 -*-
"""元评测 runner（Gap A3 + A4）：评估器可信度自检。

读 `ai_registry/benchmarks/meta_eval_set.json`（20+ 条已确证的「绝对正确/绝对错误」
二元样本，全部来源于 tests/test_hallucination_adversarial.py 与 tests/test_gap1..8.py
的既有断言，脱敏假数据）→ 用可注入的 judge 函数逐条判定 → 统计：

- 对「绝对正确」样本的判对率（must == 100%）
- 对「绝对错误」样本的判错率（must == 100%）
- 任一不达标 → evaluator_trustworthy=False（评估器不得上线）

报告落盘 `ai_registry/benchmarks/meta_eval_runs/<ts>.json`。

用法：
    python run_meta_eval.py                    # mock judge（离线确定性规则，默认）
    python run_meta_eval.py --judge audit      # 真实审核模型逐条评估（仅显式触发，运维动作）
    python run_meta_eval.py --judge mock --set /path/to/meta_eval_set.json

契约：run_meta_eval(judge, items) -> report dict
    report = {evaluator_trustworthy, total, n_correct, n_wrong,
              correct_rate, wrong_rate, per_item: [{id, expected, judged, hit, why_judged}]}
"""

import argparse
import json
import os
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
DEMO_DIR = os.path.abspath(os.path.join(_HERE, ".."))
REPO_ROOT = os.path.abspath(os.path.join(DEMO_DIR, ".."))
for _p in (DEMO_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.models import ReceiptData
from app.services.contract import validate_contract
from app.chains.audit_chain import run_text_audit

DEFAULT_SET_PATH = os.path.join(REPO_ROOT, "ai_registry", "benchmarks", "meta_eval_set.json")
DEFAULT_REPORT_DIR = os.path.join(REPO_ROOT, "ai_registry", "benchmarks", "meta_eval_runs")

VERDICT_CORRECT = "absolutely_correct"
VERDICT_WRONG = "absolutely_wrong"
VERDICT_ENUM = (VERDICT_CORRECT, VERDICT_WRONG)

# 元评测集最小条数（Gap A4：>=20 条已确证样本）
# TODO(T10): 若后续需要按环境调整，改为配置化开关
META_EVAL_MIN_ITEMS = 20

REQUIRED_ITEM_FIELDS = ("id", "source", "input", "expected_verdict", "why")


# -------------------------------------------------------------
# 元评测集加载与结构校验
# -------------------------------------------------------------
def load_meta_eval_set(path=None):
    """加载并校验元评测集；结构非法时抛 ValueError。"""
    path = path or DEFAULT_SET_PATH
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    items = doc.get("items") if isinstance(doc, dict) else doc
    problems = validate_meta_eval_set(items or [])
    if problems:
        raise ValueError("meta_eval_set 结构校验失败: " + "; ".join(problems))
    return items


def validate_meta_eval_set(items):
    """返回结构问题列表（空列表 = 合法）。"""
    problems = []
    if not isinstance(items, list):
        return ["items 必须是数组"]
    if len(items) < META_EVAL_MIN_ITEMS:
        problems.append(f"样本数 {len(items)} < 最低要求 {META_EVAL_MIN_ITEMS}")
    ids = set()
    for i, item in enumerate(items):
        for field in REQUIRED_ITEM_FIELDS:
            if field not in item or item[field] in (None, ""):
                problems.append(f"第{i+1}条缺少必填字段: {field}")
        verdict = item.get("expected_verdict")
        if verdict not in VERDICT_ENUM:
            problems.append(f"第{i+1}条 expected_verdict 非法: {verdict!r}（合法枚举: {list(VERDICT_ENUM)}）")
        if not isinstance(item.get("input"), dict) or "receipt" not in item.get("input", {}):
            problems.append(f"第{i+1}条 input 缺少 receipt 对象")
        item_id = item.get("id")
        if item_id in ids:
            problems.append(f"样本 id 重复: {item_id}")
        ids.add(item_id)
    return problems


# -------------------------------------------------------------
# Judge 实现（可注入；judge(item) -> {"verdict": ..., "why": ...}）
# -------------------------------------------------------------
def mock_judge(item):
    """离线规则式 judge（默认）：确定性评估器，只依据 receipt 内容判定。

    复用生产审核腿 text 模式的同一套确定性规则（契约门禁 + 算术门禁 +
    字段完整性 + 供应商名合理性）：契约非法或存在 discrepancy → 判错。
    """
    payload = item["input"]["receipt"]
    data, err = validate_contract(payload)
    if err:
        return {"verdict": VERDICT_WRONG, "why": f"契约门禁拒绝: {err}"}
    audit = run_text_audit(data)
    if audit.get("overall_consistent"):
        return {"verdict": VERDICT_CORRECT, "why": "确定性校验通过（契约/算术/完整性均无异常）"}
    issues = "；".join(d.get("issue", "") for d in audit.get("discrepancies", []))
    return {"verdict": VERDICT_WRONG, "why": issues or "存在 discrepancy"}


def audit_judge(item, cfg=None):
    """真实审核模型 judge（--judge audit 时显式触发，属运维动作）。

    用 build_audit_model(cfg) 构建审核腿模型（必须与识别腿异构，temperature=0），
    以文本方式（原图不在元评测链路内）送入 receipt JSON 求二元判定。
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.llm import build_audit_model
    from app.chains.audit_chain import AUDIT_SYSTEM, _parse_audit

    payload = item["input"]["receipt"]
    try:
        model = build_audit_model(cfg=cfg)
    except Exception as e:  # 模型不可用 → 本条判 skip（计为未命中）
        return {"verdict": None, "why": f"audit 模型构建失败: {e}"}
    prompt = [
        SystemMessage(content=AUDIT_SYSTEM),
        HumanMessage(content=
            "以下为单据的结构化识别结果 JSON，请判定其内容是否自洽（是否为可信的单据转录），"
            "输出 JSON：{\"overall_consistent\": true/false, \"reason\": \"...\"}\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)),
    ]
    try:
        result = model.invoke(prompt)
        raw = result.content if not isinstance(result, str) else result
        parsed = _parse_audit(raw)
    except Exception as e:
        return {"verdict": None, "why": f"audit 调用失败: {e}"}
    if parsed.get("skipped"):
        return {"verdict": None, "why": f"audit 输出不可解析: {parsed.get('reason')}"}
    verdict = VERDICT_CORRECT if parsed.get("overall_consistent") else VERDICT_WRONG
    return {"verdict": verdict, "why": parsed.get("reason", "")}


# -------------------------------------------------------------
# 元评测主流程
# -------------------------------------------------------------
def run_meta_eval(judge, items):
    """用注入的 judge 跑元评测，返回可信度报告。

    judge: callable(item) -> {"verdict": VERDICT_*|None, "why": str}
    """
    per_item = []
    n_correct_total = 0
    n_wrong_total = 0
    n_correct_hit = 0
    n_wrong_hit = 0
    for item in items:
        expected = item["expected_verdict"]
        judged = judge(item)
        verdict = judged.get("verdict")
        hit = verdict == expected
        if expected == VERDICT_CORRECT:
            n_correct_total += 1
            n_correct_hit += 1 if hit else 0
        else:
            n_wrong_total += 1
            n_wrong_hit += 1 if hit else 0
        per_item.append({
            "id": item["id"],
            "source": item["source"],
            "expected": expected,
            "judged": verdict,
            "hit": hit,
            "why_expected": item["why"],
            "why_judged": judged.get("why", ""),
        })
    correct_rate = (n_correct_hit / n_correct_total) if n_correct_total else 0.0
    wrong_rate = (n_wrong_hit / n_wrong_total) if n_wrong_total else 0.0
    # 准入规则：两类判定率都必须 100%，且两类样本都非空
    trustworthy = (
        bool(n_correct_total) and bool(n_wrong_total)
        and correct_rate == 1.0 and wrong_rate == 1.0
    )
    return {
        "evaluator_trustworthy": trustworthy,
        "total": len(items),
        "n_absolutely_correct": n_correct_total,
        "n_absolutely_wrong": n_wrong_total,
        "correct_rate": round(correct_rate, 4),
        "wrong_rate": round(wrong_rate, 4),
        "miss_count": sum(1 for r in per_item if not r["hit"]),
        "per_item": per_item,
    }


def write_report(report, report_dir=None, judge_name="mock"):
    """报告落盘 ai_registry/benchmarks/meta_eval_runs/<ts>.json，返回路径。"""
    report_dir = report_dir or DEFAULT_REPORT_DIR
    os.makedirs(report_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(report_dir, f"{ts}_meta_eval_{judge_name}.json")
    payload = dict(report)
    payload["judge"] = judge_name
    payload["generated_at"] = datetime.now().isoformat(timespec="seconds")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description="元评测：评估器可信度自检（Gap A3+A4）")
    parser.add_argument("--judge", choices=["mock", "audit"], default="mock",
                        help="mock=离线确定性规则 judge（默认）；audit=真实审核模型（显式触发）")
    parser.add_argument("--set", dest="set_path", default=None,
                        help="元评测集路径（默认 ai_registry/benchmarks/meta_eval_set.json）")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条（0=全部）")
    parser.add_argument("--report-dir", default=None, help="报告输出目录")
    parser.add_argument("--no-report", action="store_true", help="不落盘报告文件")
    args = parser.parse_args(argv)

    items = load_meta_eval_set(args.set_path)
    if args.limit and args.limit > 0:
        items = items[: args.limit]

    if args.judge == "audit":
        judge = audit_judge
    else:
        judge = mock_judge

    report = run_meta_eval(judge, items)
    print("=" * 60)
    print(f"judge={args.judge}  items={report['total']} "
          f"(absolutely_correct={report['n_absolutely_correct']}, "
          f"absolutely_wrong={report['n_absolutely_wrong']})")
    print(f"判对率(correct_rate)={report['correct_rate']}  "
          f"判错率(wrong_rate)={report['wrong_rate']}  miss={report['miss_count']}")
    for r in report["per_item"]:
        if not r["hit"]:
            print(f"  MISS {r['id']}: expected={r['expected']} judged={r['judged']} ({r['why_judged']})")
    print(f"evaluator_trustworthy={report['evaluator_trustworthy']}")
    print("=" * 60)
    if not args.no_report:
        path = write_report(report, report_dir=args.report_dir, judge_name=args.judge)
        print(f"report -> {path}")
    return 0 if report["evaluator_trustworthy"] else 1


if __name__ == "__main__":
    sys.exit(main())
