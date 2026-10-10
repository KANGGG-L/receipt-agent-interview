# -*- coding: utf-8 -*-
"""阶段 0 工作项④：冻结周基线分母（零模型调用）。

对应 SPEC_PHASE0 §4.5 断言 1–5：
  1. 两次连续冻结 sha256 相等（确定性）
  2. sample_ids 数 == 33 且 confirmed=5 / draft=28（P10；items_gt=208 / unit_present_gt=141
     为 2026-10-11 重冻结实测钉值，语料变更须按冻结纪律重冻结）
  3. run_eval --baseline <冻结文件> --engine stub（GT 拷贝到 tmp evalset）→
     baseline_match is True 且 baseline_sha256 == 文件内 sha256
  4. 篡改一个 sample_id → sha256 自校验失败 → baseline_match=false（stdout 警告）
  5. --baseline-strict 下断言 4 场景 SystemExit

语料门禁（收尾波次④b 调整）：缺省**真跑** —— 本文件零模型、冻结落 tmp、stub 回读，
无缺省运行风险；语料缺失仍由 `evalset_dir` 夹具门禁（demo/conftest.py）skip 且理由
可见（「评测语料缺失：<路径>」）。原 `pytestmark = pytest.mark.eval` 已删除：
该标记曾让 7 例在缺省输出中被「9 skipped」掩盖，基线覆盖不可见（QA_PHASE0 §9-1）。
"""

import csv
import json
import os
import shutil
import sys

_DEMO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_DEMO_DIR, "scripts"), _DEMO_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("DB_PATH", "/tmp/phase0_spec_wt.db")
os.environ.setdefault("RAG_DIR", "/tmp/phase0_spec_wt_rag")

import pytest  # noqa: E402

import freeze_eval_baseline  # noqa: E402
import run_eval  # noqa: E402

# P10 钉值：manifest 33 张 test（34 行含表头）、confirmed 5 / draft 28；
# items_gt=208 / unit_present_gt=141 为 2026-10-11 重冻结实测钉值（2026-09-27 原钉
# 211/164；语料在 09-27 后被一次批量重写重置确权并降解 S090/S136 部分内容，确权已按
# live 黄金标记恢复、丢失明细不可复原，按冻结纪律换 frozen_at 重冻结并注明原因）
_FROZEN_FILE = os.path.join(_DEMO_DIR, "..", "ai_registry", "benchmarks",
                            "eval_baselines", "test_weekly_baseline.json")


def _freeze_to(tmp_path, evalset_dir):
    """在 tmp 落一份与语料当期一致的冻结文件（不触碰版本内冻结件）。"""
    payload = freeze_eval_baseline.freeze(evalset_dir, "test")
    out = os.path.join(str(tmp_path), "baseline.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return out, payload


def _copy_evalset_gt(tmp_path, evalset_dir, drop_sample_id=None):
    """只拷 manifest + expected/ 到 tmp（stub 回读 GT 不需要图片）。

    drop_sample_id：从 manifest 剔除一张（造 missing 场景）。
    """
    ev = os.path.join(str(tmp_path), "evalsets")
    os.makedirs(os.path.join(ev, "expected"), exist_ok=True)
    with open(os.path.join(evalset_dir, "manifest.csv"), encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    fields = list(rows[0].keys())
    if drop_sample_id:
        rows = [r for r in rows if r["sample_id"] != drop_sample_id]
    with open(os.path.join(ev, "manifest.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        src = os.path.join(evalset_dir, "expected", r["sample_id"] + ".json")
        if os.path.exists(src):
            shutil.copy(src, os.path.join(ev, "expected", r["sample_id"] + ".json"))
    return ev


# ------------------------------------------------------------------
# 断言 1：冻结确定性
# ------------------------------------------------------------------
def test_freeze_is_deterministic(tmp_path, evalset_dir):
    p1 = freeze_eval_baseline.freeze(evalset_dir, "test")
    p2 = freeze_eval_baseline.freeze(evalset_dir, "test")
    assert p1["sha256"] == p2["sha256"], "同语料同日两次冻结 sha256 必须相等"
    assert p1 == p2
    # 与版本内冻结件（若在位）一致：基线文件一次性冻结后不许改写。
    # 收尾波次④b 修正既有潜伏缺陷：原断言 frozen["sha256"] == p1["sha256"] 是
    # frozen_at 时间炸弹 —— freeze() 缺省取 date.today()，该断言只在冻结当日成立，
    # 其余日子必翻红，此前被 pytest.mark.eval 缺省 skip 掩盖（QA_PHASE0 §9-1 正是
    # "skip 掩盖基线覆盖"的教训）。frozen_at 是冻结时刻的属性、不是语料内容：
    # 内容键（去 frozen_at/sha256）逐键相等 + 各自 sha 自校验，语义等价且可日常跑。
    if os.path.exists(_FROZEN_FILE):
        with open(_FROZEN_FILE, encoding="utf-8") as f:
            frozen = json.load(f)
        _body = lambda p: {k: v for k, v in p.items() if k not in ("frozen_at", "sha256")}  # noqa: E731
        assert _body(frozen) == _body(p1), \
            "版本内冻结件与当期语料不一致 → 按冻结纪律换 frozen_at 重冻结并注明原因"
        assert frozen["sha256"] == freeze_eval_baseline.baseline_sha256(frozen), \
            "版本内冻结件自身 sha256 必须自洽"


# ------------------------------------------------------------------
# 断言 2：分母口径（P10）
# ------------------------------------------------------------------
def test_frozen_denominators_pin_p10(evalset_dir):
    payload = freeze_eval_baseline.freeze(evalset_dir, "test")
    assert len(payload["sample_ids"]) == 33
    d = payload["denominators"]
    assert d["samples"] == 33
    assert d["confirmed"] == 5
    assert d["draft"] == 28
    assert d["items_gt"] == 208
    assert d["unit_present_gt"] == 141
    # sample_ids 与 manifest test split 顺序一致
    with open(os.path.join(evalset_dir, "manifest.csv"), encoding="utf-8", newline="") as f:
        ids = [r["sample_id"] for r in csv.DictReader(f) if r.get("split") == "test"]
    assert payload["sample_ids"] == ids
    # sha256 自校验（不含 sha256 键的 canonical JSON）
    assert payload["sha256"] == freeze_eval_baseline.baseline_sha256(payload)


def test_frozen_file_self_check():
    """版本内冻结件（工作产物）sha256 自校验。"""
    assert os.path.exists(_FROZEN_FILE), "阶段 0 须产出 ai_registry/benchmarks/eval_baselines/test_weekly_baseline.json"
    with open(_FROZEN_FILE, encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["sha256"] == freeze_eval_baseline.baseline_sha256(payload)
    assert payload["split"] == "test" and payload["name"] == "evalset-test-weekly"


# ------------------------------------------------------------------
# 断言 3：run_eval --baseline 消费（stub、tmp evalset）
# ------------------------------------------------------------------
def test_run_eval_baseline_match_true(tmp_path, evalset_dir, capsys):
    baseline_path, payload = _freeze_to(tmp_path, evalset_dir)
    ev = _copy_evalset_gt(tmp_path, evalset_dir)
    report = run_eval.run_eval(split="test", prompt_version="v1_2_0_sku_clean",
                               engine="stub", evalset_dir=ev,
                               report_dir=str(tmp_path / "reports"),
                               require_confirmed=False, baseline=baseline_path)
    assert report["baseline_match"] is True
    assert report["baseline_sha256"] == payload["sha256"]
    assert report["baseline_missing_samples"] == []
    assert report["baseline_extra_samples"] == []
    assert report["baseline_file"] == os.path.abspath(baseline_path)


# ------------------------------------------------------------------
# 断言 4：篡改 → sha256 自校验失败 → match=false + stdout 警告
# ------------------------------------------------------------------
def test_tampered_baseline_match_false(tmp_path, evalset_dir, capsys):
    baseline_path, _payload = _freeze_to(tmp_path, evalset_dir)
    with open(baseline_path, encoding="utf-8") as f:
        payload = json.load(f)
    payload["sample_ids"][0] = "SXXX"          # 篡改一个 sample_id（不改 sha256）
    with open(baseline_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    ev = _copy_evalset_gt(tmp_path, evalset_dir)
    report = run_eval.run_eval(split="test", prompt_version="v1_2_0_sku_clean",
                               engine="stub", evalset_dir=ev,
                               report_dir=str(tmp_path / "reports"),
                               require_confirmed=False, baseline=baseline_path)
    assert report["baseline_match"] is False
    assert report["baseline_missing_samples"] == ["SXXX"]
    out = capsys.readouterr().out
    assert "篡改" in out, "sha256 自校验失败必须 stdout 警告「基线文件被篡改」"


def test_sample_set_drift_match_false(tmp_path, evalset_dir):
    """sha 未篡改但本次分母缺 1 张 → missing 非空 → match=false（差集口径）。"""
    baseline_path, _payload = _freeze_to(tmp_path, evalset_dir)
    ev = _copy_evalset_gt(tmp_path, evalset_dir, drop_sample_id="S154")
    report = run_eval.run_eval(split="test", prompt_version="v1_2_0_sku_clean",
                               engine="stub", evalset_dir=ev,
                               report_dir=str(tmp_path / "reports"),
                               require_confirmed=False, baseline=baseline_path)
    assert report["baseline_match"] is False
    assert report["baseline_missing_samples"] == ["S154"]
    assert report["baseline_extra_samples"] == []


# ------------------------------------------------------------------
# 断言 5：--baseline-strict 下 match=false → SystemExit
# ------------------------------------------------------------------
def test_baseline_strict_exits_on_mismatch(tmp_path, evalset_dir):
    baseline_path, _payload = _freeze_to(tmp_path, evalset_dir)
    with open(baseline_path, encoding="utf-8") as f:
        payload = json.load(f)
    payload["sample_ids"][0] = "SXXX"
    with open(baseline_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    ev = _copy_evalset_gt(tmp_path, evalset_dir)
    with pytest.raises(SystemExit) as ei:
        run_eval.run_eval(split="test", prompt_version="v1_2_0_sku_clean",
                          engine="stub", evalset_dir=ev,
                          report_dir=str(tmp_path / "reports"),
                          require_confirmed=False, baseline=baseline_path,
                          baseline_strict=True)
    assert "baseline" in str(ei.value).lower() or "基线" in str(ei.value)
