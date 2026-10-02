# -*- coding: utf-8 -*-
"""GT 一致性校验器测试（SPEC_PHASE1 §2.5 合成夹具 + §2.6 真实语料门禁）。

夹具期望值全部手算钉死（禁恒真）。与规格字面的一处偏差（G-A0）：规格示例
amount=30.005/30.02 在规则 A 自身容差（max(0.01, 0.005*|amount|) = 0.15）下**两者都**
落在容差内，无法构成判别边界（0.005*30 > 0.02）；本夹具按规格的手算意图（前者不命中、
后者命中）把边界值等比缩到容差可分辨的量级（amount≈2，容差=0.01）。
"""

import csv
import json
import os
import sys

import pytest

import validate_gt_consistency as v

DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
EVALSET_DIR = os.path.join(DEMO_DIR, "evalsets")

_MANIFEST_HEADERS = ["sample_id", "image", "split", "doc_form", "layout_type",
                     "supplier_id", "gt_status", "gt_source_model", "src_sha1", "short_side"]


def _write_sample(evalset_dir, sample_id, gt_status, items, split="test"):
    """写一份最小 manifest 行 + expected JSON。"""
    manifest = os.path.join(evalset_dir, "manifest.csv")
    if not os.path.exists(manifest):
        with open(manifest, "w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow(_MANIFEST_HEADERS)
    with open(manifest, "a", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow([sample_id, "receipts/%s.png" % sample_id, split,
                                "printed_delivery_note", "table", "V001", gt_status,
                                "fixture", "sha", "0"])
    data = {"supplier_name": "夹具供应商", "items": items, "gt_status": gt_status}
    with open(os.path.join(evalset_dir, "expected", "%s.json" % sample_id), "w",
              encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _item(name="菜心", qty=1, unit_price=10, amount=10):
    return {"name": name, "qty": qty, "unit": "斤", "unit_price": unit_price, "amount": amount}


def _run(evalset_dir, out_dir, split="test"):
    rows, status_counter, per_sample, manifest_count = v.scan_samples(evalset_dir, split)
    status = v.write_outputs(out_dir, rows, status_counter, per_sample,
                             manifest_count, evalset_dir, split)
    return rows, status


# -------------------------------------------------------------
# 单行判别式（直接测 eval_row，手算钉值）
# -------------------------------------------------------------
def test_rule_a_arithmetic_conflict():
    rule_ids, severity, evidence = v.eval_row(_item(qty=3, unit_price=10, amount=25))
    assert rule_ids == ["A"] and severity == "medium"
    assert "3 × 10 = 30 ≠ 25" in evidence and "Δ=-5" in evidence


def test_rule_a_tolerance_boundary():
    # G-A0（缩比说明见模块 docstring）：qty=1,price=2 → 期望 2.00，容差 max(0.01, 0.01)=0.01
    rule_ids, _, _ = v.eval_row(_item(qty=1, unit_price=2, amount=2.005))
    assert rule_ids == []            # Δ=0.005 ≤ 容差 → 不命中
    rule_ids, _, _ = v.eval_row(_item(qty=1, unit_price=2, amount=2.02))
    assert rule_ids == ["A"]         # Δ=0.02 > 容差 → 命中


def test_rule_a_needs_all_three():
    assert v.eval_row({"name": "菜心", "qty": None, "unit_price": 10, "amount": 99})[0] == []
    assert v.eval_row({"name": "菜心", "qty": 3, "unit_price": None, "amount": 99})[0] == []


def test_rule_b_double_zero_placeholder():
    rule_ids, severity, evidence = v.eval_row(
        _item(name="七月烤魚[將軍澳]-1994", qty=0, unit_price=0, amount=0))
    assert "B" in rule_ids and severity == "high" and "同名占位" in evidence


def test_rule_b_single_zero_not_flagged():
    """G-B0：单零（qty=0 赠品行形态）不得误伤——判别式放宽会打乱 59/26/21 基线。"""
    assert v.eval_row(_item(qty=0, unit_price=8, amount=0))[0] == []


def test_rule_c_invoice_residue_case_insensitive():
    assert v.eval_row(_item(name="INV/202402000013"))[0] == ["C"]
    assert v.eval_row(_item(name="inv/2024x"))[0] == ["C"]
    assert v.eval_row(_item(name=" INVOICE 號 "))[0] == []   # 无 `/` 不命中（strip 后判）


# -------------------------------------------------------------
# 聚合语义（多规则去重 / confirmed 豁免 / 确定性）
# -------------------------------------------------------------
def test_multi_rule_row_dedup_and_dual_counting(tmp_path):
    """G-多规则去重：一行 A+C → CSV 一行、rule_ids="A,C"、severity=high；
    by_rule A、C 各 +1 而 violating_rows 只 +1。"""
    evalset_dir = tmp_path / "evalsets"
    (evalset_dir / "expected").mkdir(parents=True)
    # |1*1 - 5| = 4 > max(0.01, 0.025) → A；INV/ 前缀 → C
    _write_sample(str(evalset_dir), "S901", "draft",
                  [_item(name="INV/1", qty=1, unit_price=1, amount=5)])
    rows, status = _run(str(evalset_dir), str(tmp_path / "out"))
    assert len(rows) == 1
    assert rows[0]["rule_ids"] == "A,C" and rows[0]["severity"] == "high"
    assert status["totals"]["by_rule"] == {"A": 1, "B": 0, "C": 1}
    assert status["totals"]["violating_rows"] == 1


def test_confirmed_sample_excluded_from_checklist_but_recorded(tmp_path):
    """G-确认豁免：confirmed 样本不进清单，status.json 仍记录其历史违例数。"""
    evalset_dir = tmp_path / "evalsets"
    (evalset_dir / "expected").mkdir(parents=True)
    _write_sample(str(evalset_dir), "S902", "confirmed",
                  [_item(name="INV/2", qty=1, unit_price=1, amount=5)])
    _write_sample(str(evalset_dir), "S903", "draft",
                  [_item(name="INV/3", qty=1, unit_price=1, amount=5)])
    rows, status = _run(str(evalset_dir), str(tmp_path / "out"))
    sample_ids = {r["sample_id"] for r in rows}
    assert "S902" not in sample_ids and "S903" in sample_ids
    per = {p["sample_id"]: p for p in status["per_sample"]}
    assert per["S902"]["gt_status"] == "confirmed" and per["S902"]["violating_rows"] == 1
    assert status["totals"]["samples_to_review"] == 1   # 2 样本 1 confirmed
    assert status["totals"]["confirmed"] == 1


def test_deterministic_outputs_byte_identical(tmp_path):
    """G-确定性：两次运行三件产物逐字节相等（禁时间戳/随机进产物）。"""
    evalset_dir = tmp_path / "evalsets"
    (evalset_dir / "expected").mkdir(parents=True)
    _write_sample(str(evalset_dir), "S904", "draft",
                  [_item(name="INV/4", qty=1, unit_price=1, amount=5),
                   _item(name="七月烤魚[將軍澳]-1994", qty=0, unit_price=0, amount=0),
                   _item(name="菜心", qty=1, unit_price=10, amount=10)])
    out1, out2 = tmp_path / "out1", tmp_path / "out2"
    _run(str(evalset_dir), str(out1))
    _run(str(evalset_dir), str(out2))
    for name in ("gt_review_checklist.csv", "gt_review_checklist.md", "gt_review_status.json"):
        assert (out1 / name).read_bytes() == (out2 / name).read_bytes(), name


def test_row_sort_severity_then_sample_then_rowno(tmp_path):
    evalset_dir = tmp_path / "evalsets"
    (evalset_dir / "expected").mkdir(parents=True)
    # S905 只有一条 medium（A）；S904 有 high（B）+ medium（A）→ S904 的 high 行置顶
    _write_sample(str(evalset_dir), "S905", "draft",
                  [_item(name="贵菜", qty=1, unit_price=2, amount=5)])
    _write_sample(str(evalset_dir), "S904", "draft",
                  [_item(name="七月烤魚[將軍澳]-1994", qty=0, unit_price=0, amount=0),
                   _item(name="贵菜", qty=1, unit_price=2, amount=5)])
    rows, _ = _run(str(evalset_dir), str(tmp_path / "out"))
    assert [(r["sample_id"], r["severity"]) for r in rows] == [
        ("S904", "high"),   # high 先
        ("S904", "medium"), ("S905", "medium"),  # 同严重度按 sample_id、再按 row_no
    ]


# -------------------------------------------------------------
# 真实语料（§2.6 R1/R2/R3；eval 门禁：语料缺失自动 skip）
# -------------------------------------------------------------
@pytest.mark.eval
def test_real_corpus_totals(tmp_path):
    rows, status = _run(EVALSET_DIR, str(tmp_path / "out"))
    t = status["totals"]
    # R1（Q1/Q2 校准基线，逐值断言）
    assert t["by_rule"] == {"A": 59, "B": 26, "C": 21}
    assert t["violating_rows"] == 59
    assert t["samples_to_review"] == 28
    assert t["confirmed"] == 5 and t["draft"] == 28 and t["samples"] == 33
    # R2：per-sample 极值（含 confirmed 样本的历史违例数）
    per = {p["sample_id"]: p for p in status["per_sample"]}
    assert set(per) == {"S003", "S005", "S047", "S049", "S094"}
    assert (per["S047"]["violating_rows"], per["S047"]["by_rule"]) == (21, {"A": 21, "B": 0, "C": 21})
    assert (per["S049"]["violating_rows"], per["S049"]["by_rule"]) == (26, {"A": 26, "B": 26, "C": 0})
    assert per["S003"]["violating_rows"] == 7 and per["S003"]["gt_status"] == "confirmed"
    assert per["S005"]["violating_rows"] == 3 and per["S005"]["gt_status"] == "confirmed"
    assert per["S094"]["violating_rows"] == 2


@pytest.mark.eval
def test_real_corpus_checklist_csv_and_md(tmp_path):
    rows, status = _run(EVALSET_DIR, str(tmp_path / "out"))
    # R3 操作化：confirmed 样本（S003=7 行、S005=3 行）按 §2.3 不进清单，
    # 故 CSV 违例行数 = 59 − 10 = 49（规格 R3 字面的「CSV 行数 = 59」以全部样本为
    # 口径才成立，见任务回复偏差声明）；同时断言清单与全语料口径的差恰为 confirmed 样本。
    confirmed_violating = sum(p["violating_rows"] for p in status["per_sample"]
                              if p["gt_status"] == "confirmed")
    assert len(rows) == status["totals"]["violating_rows"] - confirmed_violating
    assert len(rows) == 49
    assert {r["sample_id"] for r in rows} == {"S047", "S049", "S094"}
    md = (tmp_path / "out" / "gt_review_checklist.md").read_text(encoding="utf-8")
    assert "## S047（draft，21 行违例，优先级 high）" in md
    assert "## S049（draft，26 行违例，优先级 high）" in md
    assert "- [ ] 已复核 S047" in md and "- [ ] 已复核 S049" in md
    assert "违例行数 59" in md    # summary 用全语料口径
