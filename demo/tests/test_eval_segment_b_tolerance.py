# -*- coding: utf-8 -*-
"""工作项④a（收尾波次 SPEC_FINAL §4.1）：段 B 容差阈值 0.3/2 的独立判别测试。

背景（QA_PHASE0 §7/§9-2）：段 B 常量 NAME_FUZZY_REL_TOL=0.3 / NAME_FUZZY_MIN_EDIT=2
此前只在「段 B+段 C 同坏」组合突变下翻红 —— 段 B 单独无判别性覆盖。本文件对两个
下界（MIN_EDIT=2、REL_TOL=0.3）与一个上界（REL_TOL 放宽）各钉一例。

判别机制（关键设计，QA C4/§7 教训的落实）：
- 夹具 GT 侧两行共用**同一数字签名**（qty/unit_price/amount 逐值相等）→ 段 C 的
  「签名在剩余 exp 与 pred 中各自唯一」前提恒不成立（exp_sig_count=2）→ 段 C 永远
  无法救回段 B 拒配的行，结论只来自段 B；
- 意配对行的两侧数字**相等**（配对成功时 qty/unit_price/amount 各 TP=1、品名
  FP+FN；段 B 被突变破坏后 TP 归零 → 翻红）；
- 全部编辑距离为**实测钉值**（QA §3 两次首算翻车教训）：用 run_eval._levenshtein
  对夹具逐对实测后才写入断言，禁止按理论值断言。

阈值公式（run_eval.py:305-306）：tol = min(max(MIN_EDIT, floor(REL_TOL·len)), len-1)
  len=4  ：tol=2（MIN_EDIT→1/0 → tol=1 → dist=2 不配）
  len=12 ：tol=3（REL_TOL→0.2 → tol=2 → dist=3 不配；REL_TOL→0.5 → tol=6 → dist=5 反而配上）
"""

import os
import sys
import tempfile

# 隔离环境（必须在首次 import app.* 之前设置；纯函数比对，不碰 DB / 向量库）
_TMP = tempfile.mkdtemp(prefix="seg_b_tolerance_test_")
os.environ.setdefault("DB_PATH", os.path.join(_TMP, "seg_b.db"))
os.environ.setdefault("RAG_DIR", os.path.join(_TMP, "rag_chroma"))

_DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)
_SCRIPTS_DIR = os.path.join(_DEMO_DIR, "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import pytest  # noqa: E402

import run_eval  # noqa: E402

# ------------------------------------------------------------------
# 夹具与实测编辑距离钉值（2026-09-28 用 run_eval._levenshtein 实测后钉死）
# ------------------------------------------------------------------
# P1：短名 4 字级，dist(海鮮醬料, 海鮮辣油) 实测=2（料→辣、醬→油两处替换）
P1_GT_NAME, P1_PRED_NAME = "海鮮醬料", "海鮮辣油"
P1_GT_DECOY = "腐乳瓶裝"          # 第二 GT 行；dist(decoy, pred)=4 > tol=2，不配
# P2：长名 12 字级，dist 实测=3（位2/7/11 三处替换）= floor(0.3·12) 下界
P2_GT_NAME, P2_PRED_NAME = "特級頭抽醬油家庭號補充包", "特級雞抽醬油家裡號補充裝"
P2_GT_DECOY = "原味豆漿大瓶裝特惠組合"   # dist(decoy, pred)=12，不配
# N1：长名 12 字级，dist 实测=5（位3/5/7/9/11 五处替换）> tol=3，段 B 拒配
N1_GT_NAME, N1_PRED_NAME = "手工水餃家庭號量販包鮮肉", "手工水魚家裡號計販裝鮮鬆"
N1_GT_DECOY = "冷凍蔥油餅十片裝經典款"   # dist(decoy, pred)=12，不配

# 实测钉值断言：夹具本身必须与本文档声称的距离一致（防后人改夹具致判别机制失效）
def test_fixture_distances_measured():
    lev = run_eval._levenshtein
    assert lev(P1_GT_NAME, P1_PRED_NAME) == 2
    assert lev(P1_GT_DECOY, P1_PRED_NAME) == 4
    assert lev(P2_GT_NAME, P2_PRED_NAME) == 3
    assert lev(P2_GT_DECOY, P2_PRED_NAME) == 12
    assert lev(N1_GT_NAME, N1_PRED_NAME) == 5
    assert lev(N1_GT_DECOY, N1_PRED_NAME) == 12


def _gt_item(name):
    return {"name": name, "qty": 3.0, "unit": "罐", "unit_price": 5.0, "amount": 15.0}


def _sample(gt_names, pred_names):
    """单样本比对夹具：GT 2 行（意配对 + 同签名 decoy）、pred 意配对行。

    GT 两行数字签名相同 → 段 C 唯一性前提不成立（隔离纪律的机制化落实）；
    意配对行两侧数字相等 → 配对成功时数值字段各 TP=1。
    """
    expected = {
        "vendor": "某供應商", "date": "2026-08-01", "doc_form": "printed_delivery_note",
        "total": 30.0,
        "items": [_gt_item(n) for n in gt_names],
    }
    predicted = {
        "vendor": "某供應商", "date": "2026-08-01", "doc_form": "printed_delivery_note",
        "total": 30.0,
        "items": [_gt_item(n) for n in pred_names],
    }
    return expected, predicted


def _item_stats(expected, predicted, field):
    return list(run_eval.compare(expected, predicted)["field_stats"][field])


# 意配对成功时的钉值：数值字段 [total=2, matched=1, tp=1, fp=0, fn=1]
#（tp=1 来自意配对行；fn=1 来自同签名的 GT decoy 行）
_PAIRED_NUM_FIELD = [2, 1, 1, 0, 1]
# 配对成功时品名：意配对行名不同 → fp+fn；decoy 行 → fn
_PAIRED_NAME = [2, 0, 0, 1, 2]
# 段 B 拒配且段 C 不救：三行全未配 → fn=2（GT 两行）+ fp=1（pred 行）
_UNPAIRED_NUM_FIELD = [3, 0, 0, 1, 2]
_UNPAIRED_NAME = [3, 0, 0, 1, 2]


# ------------------------------------------------------------------
# P1（MIN_EDIT 下界判别）：len=4、dist=2=MIN_EDIT → 配；MIN_EDIT 2→1 → 不配
# ------------------------------------------------------------------
def test_p1_min_edit_lower_bound_pairs():
    expected, predicted = _sample([P1_GT_NAME, P1_GT_DECOY], [P1_PRED_NAME])
    # tol(4)=min(max(2,1),3)=2 ≥ dist=2 → 段 B 配对
    for f in ("item.qty", "item.unit_price", "item.amount"):
        assert _item_stats(expected, predicted, f) == _PAIRED_NUM_FIELD, f
    assert _item_stats(expected, predicted, "item.name") == _PAIRED_NAME


def test_p1_min_edit_mutation_breaks_pairing(monkeypatch):
    monkeypatch.setattr(run_eval, "NAME_FUZZY_MIN_EDIT", 1)
    expected, predicted = _sample([P1_GT_NAME, P1_GT_DECOY], [P1_PRED_NAME])
    # tol=1 < dist=2 → 段 B 不配；段 C 被同签名 decoy 阻断 → 数值字段 TP 归零
    for f in ("item.qty", "item.unit_price", "item.amount"):
        assert _item_stats(expected, predicted, f) == _UNPAIRED_NUM_FIELD, f


# ------------------------------------------------------------------
# P2（REL_TOL 下界判别）：len=12、dist=3=floor(0.3·12) → 配；REL_TOL 0.3→0.2 → 不配
# ------------------------------------------------------------------
def test_p2_rel_tol_lower_bound_pairs():
    expected, predicted = _sample([P2_GT_NAME, P2_GT_DECOY], [P2_PRED_NAME])
    # tol(12)=min(max(2,3),11)=3 ≥ dist=3 → 段 B 配对（MIN_EDIT=2 不参与：3>2）
    for f in ("item.qty", "item.unit_price", "item.amount"):
        assert _item_stats(expected, predicted, f) == _PAIRED_NUM_FIELD, f
    assert _item_stats(expected, predicted, "item.name") == _PAIRED_NAME


def test_p2_rel_tol_mutation_breaks_pairing(monkeypatch):
    monkeypatch.setattr(run_eval, "NAME_FUZZY_REL_TOL", 0.2)
    expected, predicted = _sample([P2_GT_NAME, P2_GT_DECOY], [P2_PRED_NAME])
    # tol=min(max(2,2),11)=2 < dist=3 → 段 B 不配（MIN_EDIT 下限救不了：仍 2<3）
    for f in ("item.qty", "item.unit_price", "item.amount"):
        assert _item_stats(expected, predicted, f) == _UNPAIRED_NUM_FIELD, f


# ------------------------------------------------------------------
# N1（REL_TOL 上界判别）：len=12、dist=5>tol=3 → 不配；REL_TOL 0.3→0.5 → 反而配上
# ------------------------------------------------------------------
def test_n1_rel_tol_upper_bound_rejects():
    expected, predicted = _sample([N1_GT_NAME, N1_GT_DECOY], [N1_PRED_NAME])
    # tol=3 < dist=5 → 段 B 拒配；段 C 被同签名 decoy 阻断 → 数值字段无 TP
    for f in ("item.qty", "item.unit_price", "item.amount"):
        assert _item_stats(expected, predicted, f) == _UNPAIRED_NUM_FIELD, f
    assert _item_stats(expected, predicted, "item.name") == _UNPAIRED_NAME


def test_n1_rel_tol_widened_mutation_pairs(monkeypatch):
    """上界突变：REL_TOL 0.3→0.5（floor=6 ≥ dist=5）→ 段 B 反而配上 → 翻红。"""
    monkeypatch.setattr(run_eval, "NAME_FUZZY_REL_TOL", 0.5)
    expected, predicted = _sample([N1_GT_NAME, N1_GT_DECOY], [N1_PRED_NAME])
    for f in ("item.qty", "item.unit_price", "item.amount"):
        assert _item_stats(expected, predicted, f) == _PAIRED_NUM_FIELD, f
