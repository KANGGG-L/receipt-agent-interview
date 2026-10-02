#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工具①：GT 一致性校验器（SPEC_PHASE1 §2）—— 服务 /evalset 抽检台的人工 GT 确认。

纯只读：只读 ``<evalset-dir>/manifest.csv`` 与 ``<evalset-dir>/expected/<sample_id>.json``，
零 DB 访问、零网络；只写 ``--out-dir`` 下的三件产物
（``gt_review_checklist.csv`` / ``gt_review_checklist.md`` / ``gt_review_status.json``）。

三条规则的**唯一权威判别式**（对每份 expected JSON 的 items 数组逐行判定，
行号 = 数组下标 + 1，1-based，与用户在 /evalset 抽检台看到的行序一致）：

| 规则 | 判别式 | 严重度 |
|---|---|---|
| A 算术矛盾 | ``qty``、``unit_price``、``amount`` 三者均非 None，且 ``abs(qty*unit_price - amount) > max(0.01, 0.005*abs(amount))`` | medium |
| B 同名占位 | ``qty == 0`` 且 ``unit_price == 0``（**双零**；qty=0 单条件是 27 行，判别式放宽会打乱 59/26/21 校准基线） | high |
| C 发票号残渣 | ``re.match(r'^INV/', name.strip(), re.IGNORECASE)`` | high |

一行可同时违反多条 → 清单按**行**去重，``rule_ids`` 逗号联合（如 ``"A,C"``），严重度取该行
最高。全语料去重并集实测 59 行（A=59 / B=26 / C=21 为规则触发行数，含多规则重复计——
两者之和不相等是预期行为）。豁免口径：无（规则 A 不豁免任何 doc_form——S047 是
monthly_statement，其 21 行 A 错恰是「复累计金额当明细」的信号）。

gt_status 进度跟踪（§2.4）：**manifest.csv 的 gt_status 列是唯一权威**，本工具只读它；
确认走既有 ``POST /api/evalset/sample/{id}/confirm``，进度 = 重跑本工具看 summary 的
confirmed 数递增。本工具禁止写 manifest / 调 confirm 接口 / 改 api_evalset.py。

确定性：产物不含任何时间戳/随机因素（进度审计用 git / 文件 mtime）；排序规则写死；
重复运行同内容同字节（进测试）。
"""

import argparse
import csv
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# -------------------------------------------------------------
# 三条规则（判别式只此一份；severity 档位 high > medium）
# -------------------------------------------------------------
SEVERITY_ORDER = {"high": 0, "medium": 1}

RULE_SEVERITY = {"A": "medium", "B": "high", "C": "high"}

_INV_RE = re.compile(r"^INV/", re.IGNORECASE)


def rule_a(qty, unit_price, amount):
    """A 算术矛盾：三者均非 None 且 |qty*unit_price - amount| 超容差。"""
    if qty is None or unit_price is None or amount is None:
        return False
    return abs(qty * unit_price - amount) > max(0.01, 0.005 * abs(amount))


def rule_b(qty, unit_price):
    """B 同名占位：双零（qty=0 ∧ unit_price=0）；单零（赠品行形态）不得误伤。"""
    return qty == 0 and unit_price == 0


def rule_c(name):
    """C 发票号残渣：品名 strip 后以 INV/ 开头（大小写不敏感）。"""
    return bool(_INV_RE.match((name or "").strip()))


def _fmt(v):
    """数值转人读文本：JSON 原样值（int/float）直接 str，不做浮点重构。"""
    return str(v)


def eval_row(item):
    """对一行 GT 明细判定。返回 (rule_ids 列表, severity, evidence)；无违例时 ([], None, '')。

    手算钉例（测试 G-* 夹具同源）：
    - qty=3, price=10, amount=25 → A 命中，Δ=-5，evidence 含 `3 × 10 = 30 ≠ 25`；
    - qty=0, price=0 → B 命中（high）；qty=0, price=8 → 不命中 B；
    - name=INV/202402000013 / inv/2024x → C 命中；name=INVOICE 號 → 不命中（无 `/`）。
    """
    qty = item.get("qty")
    unit_price = item.get("unit_price")
    amount = item.get("amount")
    name = item.get("name") or ""
    rule_ids = []
    evidences = []
    if rule_a(qty, unit_price, amount):
        rule_ids.append("A")
        expected_amount = round(qty * unit_price, 2)
        delta = round(amount - expected_amount, 2)
        evidences.append("%s × %s = %s ≠ %s (Δ=%s)" % (
            _fmt(qty), _fmt(unit_price), _fmt(expected_amount), _fmt(amount), _fmt(delta)))
    if rule_b(qty, unit_price):
        rule_ids.append("B")
        evidences.append("qty=0 ∧ unit_price=0（同名占位）")
    if rule_c(name):
        rule_ids.append("C")
        evidences.append("品名=发票号残渣")
    if not rule_ids:
        return ([], None, "")
    severity = "high" if any(RULE_SEVERITY[r] == "high" for r in rule_ids) else "medium"
    return (rule_ids, severity, "；".join(evidences))


def _round2(v):
    return round(v, 2) if isinstance(v, (int, float)) else v


def scan_samples(evalset_dir, split):
    """读 manifest（唯一权威）+ expected JSON，返回 (rows, totals, per_sample, manifest_count)。

    rows 只含 ``gt_status != confirmed`` 样本的违例行（进清单）；
    totals / per_sample 覆盖**全部**样本（confirmed 样本仍记录历史违例数，供复标审计）。
    """
    manifest_path = os.path.join(evalset_dir, "manifest.csv")
    with open(manifest_path, encoding="utf-8-sig", newline="") as f:
        manifest = [r for r in csv.DictReader(f) if r.get("split") == split]

    rows = []
    per_sample = []
    status_counter = {"confirmed": 0, "draft": 0}
    for mrow in manifest:
        sample_id = mrow["sample_id"]
        gt_status = mrow.get("gt_status") or "draft"
        status_counter[gt_status] = status_counter.get(gt_status, 0) + 1
        expected_path = os.path.join(evalset_dir, "expected", "%s.json" % sample_id)
        with open(expected_path, encoding="utf-8") as f:
            data = json.load(f)
        by_rule = {"A": 0, "B": 0, "C": 0}
        violating = 0
        row_severities = []
        for idx, item in enumerate(data.get("items") or []):
            rule_ids, severity, evidence = eval_row(item)
            if not rule_ids:
                continue
            violating += 1
            row_severities.append(severity)
            for r in rule_ids:
                by_rule[r] += 1
            if gt_status != "confirmed":
                rows.append({
                    "sample_id": sample_id,
                    "row_no": idx + 1,
                    "gt_status": gt_status,
                    "rule_ids": ",".join(rule_ids),
                    "severity": severity,
                    "item_name": item.get("name") or "",
                    "qty": item.get("qty"),
                    "unit_price": item.get("unit_price"),
                    "amount": item.get("amount"),
                    "expected_amount": _round2(item.get("qty") * item.get("unit_price"))
                    if item.get("qty") is not None and item.get("unit_price") is not None else "",
                    "delta": _round2(item.get("amount") - round(item.get("qty") * item.get("unit_price"), 2))
                    if item.get("qty") is not None and item.get("unit_price") is not None
                    and item.get("amount") is not None else "",
                    "evidence": evidence,
                })
        if violating:
            per_sample.append({
                "sample_id": sample_id,
                "gt_status": gt_status,
                "violating_rows": violating,
                "by_rule": by_rule,
                "priority": min(row_severities, key=lambda s: SEVERITY_ORDER[s]),
            })

    # 确定性排序：severity（high 先）→ sample_id → row_no
    rows.sort(key=lambda r: (SEVERITY_ORDER[r["severity"]], r["sample_id"], r["row_no"]))
    # per_sample：违例行数降序 → sample_id 升序
    per_sample.sort(key=lambda p: (-p["violating_rows"], p["sample_id"]))
    return rows, status_counter, per_sample, len(manifest)


CSV_HEADERS = ["sample_id", "row_no", "gt_status", "rule_ids", "severity", "item_name",
               "qty", "unit_price", "amount", "expected_amount", "delta", "evidence"]

_STATUS_NOTE = (
    "确认进度跟踪：gt_status 以 manifest 为唯一权威，本文件只读不改；"
    "确认走 /evalset 抽检台 confirm 接口。"
)
_DUAL_COUNTING_NOTE = (
    "计数口径：by_rule 是规则触发行数（一行可同时违反多条、多规则重复计），"
    "violating_rows 是去重行数——两者之和不相等是预期行为（如 S047 一行既 A 又 C）。"
)


def write_outputs(out_dir, rows, status_counter, per_sample, manifest_count, evalset_dir, split):
    os.makedirs(out_dir, exist_ok=True)
    confirmed = status_counter.get("confirmed", 0)
    draft = status_counter.get("draft", 0)
    to_review = manifest_count - confirmed
    violating_rows = sum(p["violating_rows"] for p in per_sample)
    by_rule_total = {"A": 0, "B": 0, "C": 0}
    for p in per_sample:
        for r, n in p["by_rule"].items():
            by_rule_total[r] += n

    # --- CSV（UTF-8 with BOM，Excel 直开不乱码）---
    csv_path = os.path.join(out_dir, "gt_review_checklist.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_HEADERS)
        for r in rows:
            w.writerow([r[h] for h in CSV_HEADERS])

    # --- MD（逐单据一节，节间违例行数降序 → sample_id 升序；复选框纯提示不回写状态）---
    md_path = os.path.join(out_dir, "gt_review_checklist.md")
    lines = [
        "# GT 复核清单（validate_gt_consistency.py 产物；只读，不回写任何状态）",
        "",
        "总样本 %d / confirmed %d / draft %d / 待复核样本数 %d / 违例行数 %d" % (
            manifest_count, confirmed, draft, to_review, violating_rows),
        "",
        _DUAL_COUNTING_NOTE,
        "",
    ]
    to_review_ids = {r["sample_id"] for r in rows}
    ordered = [p for p in per_sample if p["sample_id"] in to_review_ids]
    ordered.sort(key=lambda p: (-p["violating_rows"], p["sample_id"]))
    by_sample = {}
    for r in rows:
        by_sample.setdefault(r["sample_id"], []).append(r)
    for p in ordered:
        lines.append("## %s（%s，%d 行违例，优先级 %s）" % (
            p["sample_id"], p["gt_status"], p["violating_rows"], p["priority"]))
        lines.append("")
        for r in by_sample[p["sample_id"]]:
            lines.append("- 行 %d [%s][%s] %s — %s" % (
                r["row_no"], r["rule_ids"], r["severity"], r["item_name"], r["evidence"]))
        lines.append("")
        lines.append("- [ ] 已复核 %s" % p["sample_id"])
        lines.append("")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # --- status JSON（机器可读进度）---
    status = {
        "generated_from": {"evalset_dir": evalset_dir, "split": split},
        "totals": {
            "samples": manifest_count,
            "confirmed": confirmed,
            "draft": draft,
            "samples_to_review": to_review,
            "violating_rows": violating_rows,
            "by_rule": by_rule_total,
        },
        "per_sample": per_sample,
        "note": _STATUS_NOTE,
        "counting_note": _DUAL_COUNTING_NOTE,
    }
    json_path = os.path.join(out_dir, "gt_review_status.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return status


def main():
    ap = argparse.ArgumentParser(description="GT 一致性校验器：产出 GT 复核清单（纯只读）")
    ap.add_argument("--evalset-dir", default=os.path.join(REPO, "demo", "evalsets"),
                    help="评测语料目录（含 manifest.csv 与 expected/）")
    ap.add_argument("--split", default="test")
    ap.add_argument("--out-dir", default=os.path.join(REPO, "demo", "evalsets", "review"),
                    help="产物目录（默认 demo/evalsets/review/，与语料同目录同 gitignore 待遇）")
    ap.add_argument("--json", action="store_true", help="stdout 打印 gt_review_status 同款 JSON")
    args = ap.parse_args()

    rows, status_counter, per_sample, manifest_count = scan_samples(args.evalset_dir, args.split)
    status = write_outputs(args.out_dir, rows, status_counter, per_sample,
                           manifest_count, args.evalset_dir, args.split)
    if args.json:
        # --json 时 stdout 只输出机器可读 JSON（人读摘要走 stderr，便于管道消费）
        print(json.dumps(status, ensure_ascii=False, indent=2))
        print("清单违例行数 %d → %s" % (
            len(rows), os.path.join(args.out_dir, "gt_review_checklist.csv")), file=sys.stderr)
    else:
        print("清单违例行数 %d → %s" % (len(rows), os.path.join(args.out_dir, "gt_review_checklist.csv")))
        print("状态摘要：confirmed %d / 待复核 %d / 全语料违例行 %d（%s）" % (
            status["totals"]["confirmed"], status["totals"]["samples_to_review"],
            status["totals"]["violating_rows"], os.path.join(args.out_dir, "gt_review_status.json")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
