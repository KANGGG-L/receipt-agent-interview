# -*- coding: utf-8 -*-
"""工作项④c（收尾波次 SPEC_FINAL §4.3）：基线 denominators 显式比对替代纯 sha256 间接保护。

背景（QA_PHASE0 §9-3）：run_eval 原先只比对 sha256 自校验 + sample_ids 差集，
`denominators` 数值本身不参与比对 —— 篡改分母并**重算 sha**（sha 自洽）仍能过校验。
本波给 run_eval 加 run 侧分母累计（经 `freeze_eval_baseline.count_gt_items` 唯一实现，
与冻结侧同源）+ 逐键显式比对；`--baseline-strict` 下分母不一致 SystemExit。

覆盖：
- D1 分母校验独立于 sha 生效：篡改 items_gt 211→999 并重算 sha（sha 自洽）→
  baseline_match is False、mismatches 含 items_gt、stdout 含「分母不一致」；
- D2 D1 场景 + baseline_strict=True → SystemExit 且报文含「基线分母不一致」；
- D3 合法基线 → mismatches==[]、match True（防恒假）、报告两键为实际分母；
- D4 重构守卫：count_gt_items 对 tmp GT 夹具的计数与手算一致；版本内冻结件
  sha256 == 文件内 sha256（重构行为中性：payload 结构与值零变化）。

复用 test_eval_baseline_freeze 的 _freeze_to/_copy_evalset_gt 手法（tmp 冻结 +
manifest/expected 拷贝）；零模型、语料缺失经 evalset_dir 门禁 skip。
"""

import csv
import json
import os
import shutil
import sys
import tempfile

_DEMO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_DEMO_DIR, "scripts"), _DEMO_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("DB_PATH", "/tmp/final_wave_denom.db")
os.environ.setdefault("RAG_DIR", "/tmp/final_wave_denom_rag")

import pytest  # noqa: E402

import freeze_eval_baseline  # noqa: E402
import run_eval  # noqa: E402

_FROZEN_FILE = os.path.join(_DEMO_DIR, "..", "ai_registry", "benchmarks",
                            "eval_baselines", "test_weekly_baseline.json")


def _freeze_to(tmp_path, evalset_dir):
    """在 tmp 落一份与语料当期一致的冻结文件（不触碰版本内冻结件）。"""
    payload = freeze_eval_baseline.freeze(evalset_dir, "test")
    out = os.path.join(str(tmp_path), "baseline.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return out, payload


def _copy_evalset_gt(tmp_path, evalset_dir):
    """只拷 manifest + expected/ 到 tmp（stub 回读 GT 不需要图片）。"""
    ev = os.path.join(str(tmp_path), "evalsets")
    os.makedirs(os.path.join(ev, "expected"), exist_ok=True)
    with open(os.path.join(evalset_dir, "manifest.csv"), encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    fields = list(rows[0].keys())
    with open(os.path.join(ev, "manifest.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        src = os.path.join(evalset_dir, "expected", r["sample_id"] + ".json")
        if os.path.exists(src):
            shutil.copy(src, os.path.join(ev, "expected", r["sample_id"] + ".json"))
    return ev


def _rewrite_baseline(path, mutate):
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    mutate(payload)
    # 重算 sha（sha 自洽）—— 这正是纯 sha256 间接保护防不住的场景
    payload["sha256"] = freeze_eval_baseline.baseline_sha256(payload)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _run(tmp_path, ev, baseline_path, strict=False):
    return run_eval.run_eval(split="test", prompt_version="v1_2_0_sku_clean",
                             engine="stub", evalset_dir=ev,
                             report_dir=str(tmp_path / "reports"),
                             require_confirmed=False, baseline=baseline_path,
                             baseline_strict=strict)


# ------------------------------------------------------------------
# D1：分母校验独立于 sha 生效（G3 场景的分母版）
# ------------------------------------------------------------------
def test_d1_tampered_denominators_with_valid_sha_detected(tmp_path, evalset_dir, capsys):
    baseline_path, payload = _freeze_to(tmp_path, evalset_dir)
    assert payload["denominators"]["items_gt"] == 211  # 夹具前提：P10 冻结钉值
    _rewrite_baseline(baseline_path,
                      lambda p: p["denominators"].__setitem__("items_gt", 999))
    ev = _copy_evalset_gt(tmp_path, evalset_dir)

    report = _run(tmp_path, ev, baseline_path)

    assert report["baseline_match"] is False
    mismatch_keys = [m["key"] for m in report["baseline_denominator_mismatches"]]
    assert "items_gt" in mismatch_keys
    mm = next(m for m in report["baseline_denominator_mismatches"]
              if m["key"] == "items_gt")
    assert mm["baseline"] == 999 and mm["actual"] == 211
    out = capsys.readouterr().out
    assert "分母不一致" in out, "分母显式比对失败必须 stdout 警告"


# ------------------------------------------------------------------
# D2：--baseline-strict 下分母不一致 → SystemExit 且报文含「基线分母不一致」
# ------------------------------------------------------------------
def test_d2_strict_mode_exits_on_denominator_mismatch(tmp_path, evalset_dir):
    baseline_path, _payload = _freeze_to(tmp_path, evalset_dir)
    _rewrite_baseline(baseline_path,
                      lambda p: p["denominators"].__setitem__("items_gt", 999))
    ev = _copy_evalset_gt(tmp_path, evalset_dir)

    with pytest.raises(SystemExit) as ei:
        _run(tmp_path, ev, baseline_path, strict=True)
    assert "基线分母不一致" in str(ei.value)


# ------------------------------------------------------------------
# D3：合法基线 → mismatches==[]、match True（防恒假）
# ------------------------------------------------------------------
def test_d3_legit_baseline_matches(tmp_path, evalset_dir):
    baseline_path, payload = _freeze_to(tmp_path, evalset_dir)
    ev = _copy_evalset_gt(tmp_path, evalset_dir)

    report = _run(tmp_path, ev, baseline_path)

    assert report["baseline_match"] is True
    assert report["baseline_denominator_mismatches"] == []
    assert report["baseline_denominators_baseline"] == payload["denominators"]
    actual = report["baseline_denominators_actual"]
    assert actual == payload["denominators"], \
        "合法基线下 run 侧四值必须与冻结分母逐键相等（同源实现的端到端守卫）"


# ------------------------------------------------------------------
# D4：重构守卫 —— count_gt_items 手算钉值 + 版本内冻结件 sha 不变
# ------------------------------------------------------------------
def test_d4_count_gt_items_hand_computed():
    gt = {"items": [{"name": "a", "unit": "斤"}, {"name": "b", "unit": "  "},
                    {"name": "c", "unit": "kg"}, {"name": "d"}, {"unit": "箱"}]}
    # 手算：5 行明细；unit 非空 = a(斤) + c(kg) + 第5行(箱) = 3
    #（"  " 纯空白剥后为空不计；缺 unit 键不计）
    assert freeze_eval_baseline.count_gt_items(gt) == (5, 3)
    assert freeze_eval_baseline.count_gt_items({}) == (0, 0)
    assert freeze_eval_baseline.count_gt_items(None) == (0, 0)
    assert freeze_eval_baseline.count_gt_items({"items": None}) == (0, 0)


def test_d4_frozen_file_sha_unchanged():
    """重构行为中性守卫：版本内冻结件 sha256 == 文件内 sha256（payload 结构与值零变化）。"""
    assert os.path.exists(_FROZEN_FILE)
    with open(_FROZEN_FILE, encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["sha256"] == freeze_eval_baseline.baseline_sha256(payload)
