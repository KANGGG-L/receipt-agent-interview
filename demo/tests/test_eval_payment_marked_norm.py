# -*- coding: utf-8 -*-
"""payment_marked 布尔归一收敛为单一实现（D 项）。

原状：同一份模型输出被两侧用不同口径解释 ——
- 生成侧 `gen_gt_candidates._coerce_bool` 接受 `"y"/"n"`，把 `""` 折成 **False**；
- 评分侧 `run_eval._norm_payment_marked` 不接受 `"y"/"n"`（判为 None 跳过比对），
  `""` 也归 **None**。

后果：① 预测写 `"Y"` 时评分侧直接跳过比对 → payment_marked 实际从未被比过（指标静默缺失）；
② 候选留空/不可判读时被折成 False，等于凭空造出「未付款」的 GT 真值，再据此给预测打分。

口径（本次选定）：`""`/None/不可判 → **None（未知）**，不折算成 False。
依据：该字段会成为 GT 真值参与打分，折 False 是伪造负标签，而且恰好让 GT 落盘契约的
布尔校验（gen_gt_candidates.validate_gt / api_evalset 校验）通过 —— 属「用看起来合法的
值掩盖缺陷」；GT 契约仍要求布尔，故生成侧保留 None 让校验显式失败（候选不进语料）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/receipt_demo_eval_norm_test.db")

import pytest

import eval_norm

# (输入, 期望)："" 三类输入必测（""/"Y"/True），并覆盖 y/n 与显式假值
CASES = [
    (True, True),
    (False, False),
    (1, True),
    (0, False),
    ("true", True),
    ("Y", True),
    ("y", True),
    ("N", False),
    ("n", False),
    ("已付款", True),
    ("paid", True),
    ("未付款", False),
    ("unpaid", False),
    ("", None),          # 留空 = 未知，不是「未付款」
    (None, None),
    ("???", None),       # 不可判 = 未知
]


# ------------------------------------------------------------------
# 1. 单一实现：两侧都指向同一个函数对象
# ------------------------------------------------------------------
def test_both_sides_share_one_implementation():
    import gen_gt_candidates
    import run_eval

    assert gen_gt_candidates._coerce_bool is eval_norm.coerce_payment_marked
    assert run_eval._norm_payment_marked is eval_norm.coerce_payment_marked


def test_no_duplicate_literal_tables_left():
    """两侧不得再各自维护字面量表（防再次分叉）：必须是直接委托单一实现。"""
    demo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel in ("scripts/run_eval.py", "scripts/gen_gt_candidates.py"):
        with open(os.path.join(demo, rel), encoding="utf-8") as f:
            src = f.read()
        assert "eval_norm.coerce_payment_marked" in src, f"{rel} 未使用 eval_norm 单一实现"
        # 旧实现的私有字面量表特征：`str(v or "").strip().lower()` + 本地 true/false 元组
        assert 'str(v or "").strip().lower()' not in src, f"{rel} 仍保留本地布尔字面量表"


# ------------------------------------------------------------------
# 2. 口径：两侧对同一输入结论一致
# ------------------------------------------------------------------
@pytest.mark.parametrize("raw,expect", CASES)
def test_both_sides_agree(raw, expect):
    import gen_gt_candidates
    import run_eval

    assert eval_norm.coerce_payment_marked(raw) is expect
    assert gen_gt_candidates._coerce_bool(raw) is expect
    assert run_eval._norm_payment_marked(raw) is expect


def test_y_literal_is_comparable_not_skipped():
    """回归点：预测里的 "Y" 必须被真比对，而不是判为不可判而跳过。"""
    import run_eval

    pred = run_eval.normalize({"payment_marked": "Y"})
    assert pred["payment_marked"] is True

    exp = run_eval.normalize({"payment_marked": True})
    res = run_eval.compare(exp, pred)
    assert "payment_marked" in res["field_stats"], "GT 有该字段就必须比对"
    assert res["field_stats"]["payment_marked"] == [1, 1]
    assert res["mismatched_fields"] == []


# ------------------------------------------------------------------
# 3. 生成侧：不可判读不再折成 False（不伪造 GT 真值）
# ------------------------------------------------------------------
def _gt(payment_marked):
    return {"supplier_name": "X", "date": "2026-08-01", "total_amount": 20.0,
            "payment_marked": payment_marked,
            "items": [{"name": "白菜", "qty": 2.0, "unit": "斤",
                       "unit_price": 10.0, "amount": 20.0}]}


def test_gen_side_does_not_fabricate_false():
    import gen_gt_candidates

    gt = gen_gt_candidates.normalize_gt_items(_gt(""))
    assert gt["payment_marked"] is None, "留空不得折成 False（那是伪造「未付款」真值）"
    err = gen_gt_candidates.validate_gt(gt)
    assert err is not None, "不可判读的候选必须显式失败，不进语料"

    gt2 = gen_gt_candidates.normalize_gt_items(_gt("Y"))
    assert gt2["payment_marked"] is True
    assert gen_gt_candidates.validate_gt(gt2) is None

    gt3 = gen_gt_candidates.normalize_gt_items(_gt(False))
    assert gt3["payment_marked"] is False
    assert gen_gt_candidates.validate_gt(gt3) is None
