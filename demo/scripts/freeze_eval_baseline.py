# -*- coding: utf-8 -*-
"""冻结周基线分母（阶段 0 工作项④）——零模型调用，只读 manifest + expected/，写一个 JSON。

why：33 张 test 分母上 ±1 张 ≈ ±0.5pp，无统计意义（EXPLORATION_REPORT §4.3）。
周环比对比一律以冻结的 sample_ids 为固定分母；本脚本把分母一次性落盘（含各计分
分母的当场统计值与 canonical sha256 自校验），run_eval --baseline 消费：
sample 集合或文件内容漂移即 baseline_match=false。

用法：
    python demo/scripts/freeze_eval_baseline.py --evalset-dir demo/evalsets \\
        --split test --out ai_registry/benchmarks/eval_baselines/test_weekly_baseline.json

冻结纪律：基线文件一次性冻结后**不许改写**；重冻结必须换 frozen_at 并在 note 里
写明原因。denominators 各值由本脚本当场统计，禁止手填。
"""

import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import date

_HERE = os.path.dirname(os.path.abspath(__file__))
DEMO_DIR = os.path.abspath(os.path.join(_HERE, ".."))
REPO_ROOT = os.path.abspath(os.path.join(DEMO_DIR, ".."))
DEFAULT_EVALSET_DIR = os.path.join(DEMO_DIR, "evalsets")
DEFAULT_OUT = os.path.join(REPO_ROOT, "ai_registry", "benchmarks", "eval_baselines",
                           "test_weekly_baseline.json")

_NOTE = ("周基线分母冻结：周环比对比一律以此 sample_ids 为固定分母；"
         "33 张上 ±1 张 ≈ ±0.5pp 无统计意义（EXPLORATION_REPORT §4.3）。"
         "2026-10-11 重冻结：语料确权状态曾被 10-01 前后的一次批量重写重置"
         "（confirmed 5→0），已按 live 库黄金标记（receipts 1455-1459 "
         "is_golden_sample=1）恢复确权；S090 丢失 3 行明细、S136 丢失 unit 字段"
         "（items_gt 211→208、unit_present_gt 164→141），原始内容无备份不可复原，"
         "如实按当前语料钉值（详见 qa-round6/r7b-report.md）")


def baseline_sha256(payload):
    """canonical sha256：对不含 sha256 键的 JSON（ensure_ascii=False、sort_keys=True、
    indent=2）的 UTF-8 字节计算；与 run_eval._baseline_sha256 同口径。"""
    body = {k: v for k, v in payload.items() if k != "sha256"}
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def count_gt_items(gt):
    """GT 明细分母计数（收尾波次工作项④c 抽取，行为中性重构）：
    返回 (items_gt, unit_present_gt) —— items_gt = GT 明细行总数；
    unit_present_gt = 其中 unit 非空的行数（item.unit F1 的潜在分母）。

    why 抽成函数：run_eval 主循环要对 readable GT 用**同一函数**累计 run 侧计数，
    与冻结件 denominators 显式逐键比对（sha256 只保护文件完整性，不保护分母数值
    本身 —— 篡改分母并重算 sha 也能过自校验，必须数值显式比对兜底）。
    freeze 缺 GT 样本计入 samples 但不计入 items_gt（见 freeze 循环 continue）；
    run 侧同样只对 readable GT 计数 —— 两口径一致。
    """
    items = (gt or {}).get("items") or []
    items_gt = len(items)
    unit_present_gt = sum(1 for it in items if str((it or {}).get("unit") or "").strip())
    return items_gt, unit_present_gt


def freeze(evalset_dir, split, frozen_at=None, note=None):
    """统计 split 的分母并产出冻结 payload（dict）。确定性：同语料同日两次冻结相等。"""
    manifest_path = os.path.join(evalset_dir, "manifest.csv")
    if not os.path.exists(manifest_path):
        raise SystemExit("评测集 manifest 不存在：%s（语料属业务数据，不入库）" % manifest_path)
    with open(manifest_path, encoding="utf-8", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r.get("split") == split]
    if not rows:
        raise SystemExit("manifest 中没有 split=%s 的样本：%s" % (split, manifest_path))

    sample_ids = [r["sample_id"] for r in rows]
    confirmed = sum(1 for r in rows if (r.get("gt_status") or "missing") == "confirmed")
    draft = sum(1 for r in rows if (r.get("gt_status") or "missing") == "draft")

    # GT 明细分母当场统计（不得手填），计数逻辑在 count_gt_items（唯一实现）
    items_gt = 0
    unit_present_gt = 0
    missing_gt = []
    for sid in sample_ids:
        path = os.path.join(evalset_dir, "expected", sid + ".json")
        if not os.path.exists(path):
            missing_gt.append(sid)
            continue
        with open(path, encoding="utf-8") as f:
            gt = json.load(f)
        n_items, n_unit = count_gt_items(gt)
        items_gt += n_items
        unit_present_gt += n_unit

    payload = {
        "name": "evalset-%s-weekly" % split,
        "frozen_at": frozen_at or date.today().isoformat(),
        "split": split,
        "sample_ids": sample_ids,
        "denominators": {
            "samples": len(sample_ids),
            "confirmed": confirmed,
            "draft": draft,
            "items_gt": items_gt,
            "unit_present_gt": unit_present_gt,
        },
        "note": note or _NOTE,
    }
    payload["sha256"] = baseline_sha256(payload)
    if missing_gt:
        # GT 缺失的样本仍进分母（missing 本身就是要被周环比暴露的状态），但 stdout 提示
        print("!! 警告：%d 张样本缺 expected GT：%s"
              % (len(missing_gt), ", ".join(missing_gt[:5])))
    return payload


def main():
    ap = argparse.ArgumentParser(description="冻结周基线分母（零模型调用，只读语料）")
    ap.add_argument("--evalset-dir", default=DEFAULT_EVALSET_DIR, help="评测集目录")
    ap.add_argument("--split", default="test", choices=["train", "val", "test"])
    ap.add_argument("--out", default=DEFAULT_OUT, help="落盘路径（JSON）")
    ap.add_argument("--allow-shrink", default=None, metavar="原因",
                    help="新语料的样本数 / GT 明细行数比旧基线少时，必须用本参数写明原因才允许重冻结"
                         "（防止 GT 在不知情时丢失后被悄悄冻成新基线）")
    ap.add_argument("--note", default=None, help="写进基线 note 的重冻结原因（重冻结时必填）")
    args = ap.parse_args()

    out_path = os.path.abspath(args.out)
    previous = None
    if os.path.exists(out_path):
        # 重冻结流程：先备份 GT 与现有基线，再比对新旧分母，GT 变少且没写原因就拒绝
        with open(out_path, encoding="utf-8") as f:
            previous = json.load(f)
        if not args.note:
            raise SystemExit("基线已存在，重冻结必须用 --note 写明原因（纪律：不许悄悄改写基线）")
        from eval_assets_backup import backup_eval_assets
        info = backup_eval_assets(evalset_dir=args.evalset_dir)
        print("重冻结前已备份 GT 与基线 → %s（%d 个文件）" % (info["dir"], info["files"]))
    payload = freeze(args.evalset_dir, args.split,
                     note=(args.note + "（旧基线 frozen_at=%s）" % previous.get("frozen_at")) if previous else None)
    if previous:
        old_d = previous.get("denominators") or {}
        new_d = payload["denominators"]
        shrunk = [k for k in ("samples", "items_gt", "unit_present_gt")
                  if new_d.get(k, 0) < old_d.get(k, 0)]
        if shrunk and not args.allow_shrink:
            raise SystemExit(
                "拒绝重冻结：以下分母比旧基线少了 %s（旧 %s → 新 %s）。这通常意味着 GT 文件被改坏或丢失，"
                "请先从备份恢复；确认是有意删减时加 --allow-shrink \"原因\"。"
                % (", ".join(shrunk), {k: old_d.get(k) for k in shrunk}, {k: new_d.get(k) for k in shrunk}))
        if shrunk:
            payload["note"] += "；GT 分母有意减少（%s）：%s" % (", ".join(shrunk), args.allow_shrink)
            payload["sha256"] = baseline_sha256({k: v for k, v in payload.items() if k != "sha256"})
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    d = payload["denominators"]
    print("冻结完成 → %s" % out_path)
    print("  split=%s frozen_at=%s sha256=%s" %
          (payload["split"], payload["frozen_at"], payload["sha256"]))
    print("  samples=%d confirmed=%d draft=%d items_gt=%d unit_present_gt=%d"
          % (d["samples"], d["confirmed"], d["draft"], d["items_gt"], d["unit_present_gt"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
