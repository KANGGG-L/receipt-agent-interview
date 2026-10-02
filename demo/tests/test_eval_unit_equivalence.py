# -*- coding: utf-8 -*-
"""阶段 0 工作项③：单位语义等价接进评分（[口径分]）判别式测试。

U-1..U-8 对应 SPEC_PHASE0 §4.5，期望值手算：
等价拼写/同系数换算 → TP；系数不同（斤 0.6048 vs 磅 0.4536）与真噪声 → FP+FN；
双缺失跳过、单侧缺失 FN、规格污染剥离、accuracy 位同步认等价。
另验 YAML 资产 fail-fast（缺失/坏 YAML → SystemExit 指明路径）与 PyYAML 依赖声明。
"""

import os
import sys

_DEMO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_DEMO_DIR, "scripts"), _DEMO_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("DB_PATH", "/tmp/phase0_spec_wt.db")
os.environ.setdefault("RAG_DIR", "/tmp/phase0_spec_wt_rag")

import pytest  # noqa: E402

import run_eval  # noqa: E402
import freeze_eval_baseline  # noqa: E402  (仅复用其目录常量定位 requirements)

_BASE = {
    "supplier_name": "新協興食品貿易有限公司",
    "date": "2024-08-02",
    "total_amount": 20.0,
    "doc_form": "printed_delivery_note",
    "payment_marked": False,
    "currency": "HKD",
    "adjustment_notes": [],
    "items": [{"name": "白菜", "qty": 2.0, "unit": "斤",
               "unit_price": 10.0, "amount": 20.0}],
}


def _compare_unit(gt_unit, pred_unit):
    """单明细行、只改 unit 的 compare。"""
    return run_eval.compare(_payload_with(gt_unit), _payload_with(pred_unit))


def _payload_with(unit):
    p = {k: (v if k != "items" else [dict(v[0], unit=unit)])
         for k, v in _BASE.items()}
    return p


def _pf1(res):
    return run_eval._per_field_f1_from_stats(res["field_stats"])


# ------------------------------------------------------------------
# U-1 等价拼写：樽=瓶=BTL、ctn=箱、LB=磅、枝=支 → 各自 TP
# ------------------------------------------------------------------
@pytest.mark.parametrize("pred_unit,gt_unit", [
    ("樽", "瓶"),
    ("BTL", "瓶"),
    ("ctn", "箱"),
    ("LB", "磅"),
    ("枝", "支"),
])
def test_u1_equivalent_spellings_are_tp(pred_unit, gt_unit):
    assert run_eval._units_equal(pred_unit, gt_unit), (pred_unit, gt_unit)
    res = _compare_unit(gt_unit, pred_unit)
    assert res["field_stats"]["item.unit"] == [1, 1]
    pf1 = _pf1(res)["item.unit"]
    # 手算：单实例等价 → tp=1, fp=fn=0 → P=R=F1=1.0
    assert pf1 == {"tp": 1, "fp": 0, "fn": 0,
                   "precision": 1.0, "recall": 1.0, "f1": 1.0}


# ------------------------------------------------------------------
# U-2 换算等价：司马斤=斤（0.6048）、公斤=kg（1.0）
# ------------------------------------------------------------------
@pytest.mark.parametrize("pred_unit,gt_unit", [("司马斤", "斤"), ("公斤", "kg")])
def test_u2_converter_same_factor_is_tp(pred_unit, gt_unit):
    assert run_eval._units_equal(pred_unit, gt_unit)
    res = _compare_unit(gt_unit, pred_unit)
    assert res["field_stats"]["item.unit"] == [1, 1]
    assert _pf1(res)["item.unit"]["f1"] == 1.0


# ------------------------------------------------------------------
# U-3 换算不等价（关键负例）：斤(0.6048) vs 磅(0.4536) 必须判错
# ------------------------------------------------------------------
def test_u3_different_factor_is_fp_fn():
    assert not run_eval._units_equal("斤", "磅")
    res = _compare_unit("磅", "斤")
    assert res["field_stats"]["item.unit"] == [1, 0], "accuracy 位同样判错"
    pf1 = _pf1(res)["item.unit"]
    # 手算：单实例双 present 不等 → FP=1 且 FN=1，TP=0 → P=R=0 → F1=0.0
    assert pf1 == {"tp": 0, "fp": 1, "fn": 1,
                   "precision": 0.0, "recall": 0.0, "f1": 0.0}


# ------------------------------------------------------------------
# U-4 规格污染剥离：箱（48罐） vs 箱 → TP
# ------------------------------------------------------------------
def test_u4_paren_spec_stripped():
    assert run_eval._norm_unit("箱（48罐）") == "箱"
    res = _compare_unit("箱", "箱（48罐）")
    assert res["field_stats"]["item.unit"] == [1, 1]
    assert _pf1(res)["item.unit"]["f1"] == 1.0


# ------------------------------------------------------------------
# U-5 真噪声仍判错（关键负例）
# ------------------------------------------------------------------
@pytest.mark.parametrize("pred_unit,gt_unit", [("D", "文"), ("Bit", "斤"), ("合1", "箱")])
def test_u5_true_noise_is_fp_fn(pred_unit, gt_unit):
    assert not run_eval._units_equal(pred_unit, gt_unit)
    res = _compare_unit(gt_unit, pred_unit)
    pf1 = _pf1(res)["item.unit"]
    # 手算：单实例不等 → FP=1 且 FN=1 → F1=0.0（防「换算表全等」假实现）
    assert pf1 == {"tp": 0, "fp": 1, "fn": 1,
                   "precision": 0.0, "recall": 0.0, "f1": 0.0}


# ------------------------------------------------------------------
# U-6 空不占分母：两侧 unit 均空 → 实例跳过
# ------------------------------------------------------------------
def test_u6_both_empty_skipped():
    res = _compare_unit("", "")
    assert "item.unit" not in _pf1(res), "双缺失不计 F1 实例"
    # accuracy 位维持旧语义（"" == "" 计 match），口径一字不动
    assert res["field_stats"]["item.unit"] == [1, 1]


# ------------------------------------------------------------------
# U-7 单侧缺失：GT 斤、pred 空 → FN（不因归一抛错变成 TP）
# ------------------------------------------------------------------
def test_u7_one_side_missing_is_fn():
    res = _compare_unit("斤", "")
    pf1 = _pf1(res)["item.unit"]
    # 手算：GT present、pred 缺失 → FN=1 → P=0/0→0, R=0/(0+1)=0 → F1=0.0
    assert pf1 == {"tp": 0, "fp": 0, "fn": 1,
                   "precision": 0.0, "recall": 0.0, "f1": 0.0}


# ------------------------------------------------------------------
# U-8 accuracy 位同步：樽 vs 瓶 在 accuracy 位同样认等价
# ------------------------------------------------------------------
def test_u8_accuracy_slot_also_equivalent():
    res = _compare_unit("瓶", "樽")
    assert res["field_stats"]["item.unit"] == [1, 1]
    assert "item.unit" not in res["mismatched_fields"]


# ------------------------------------------------------------------
# YAML 资产 fail-fast（§5 静默回归要求 3）+ 依赖声明
# ------------------------------------------------------------------
def test_missing_yaml_fails_fast(tmp_path):
    with pytest.raises(SystemExit) as ei:
        run_eval._load_unit_equivalence(str(tmp_path / "nope.yaml"))
    assert "nope.yaml" in str(ei.value), "必须指明文件路径"


def test_bad_yaml_fails_fast(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("groups: [unclosed", encoding="utf-8")
    with pytest.raises(SystemExit) as ei:
        run_eval._load_unit_equivalence(str(bad))
    assert "bad.yaml" in str(ei.value)


def test_cross_group_duplicate_member_fails_fast(tmp_path):
    dup = tmp_path / "dup.yaml"
    dup.write_text(
        "groups:\n  - [瓶, 樽]\n  - [樽, 箱]\n"
        "strip_paren_spec: true\nconvert_via_converter: true\n", encoding="utf-8")
    with pytest.raises(SystemExit) as ei:
        run_eval._load_unit_equivalence(str(dup))
    assert "樽" in str(ei.value), "跨组重复成员必须显式失败（等价口径不得含糊）"


def test_pyyaml_declared_in_requirements():
    req_path = os.path.join(os.path.dirname(freeze_eval_baseline.DEMO_DIR),
                            "demo", "requirements.txt")
    with open(req_path, encoding="utf-8") as f:
        content = f.read()
    assert "PyYAML" in content, "demo/requirements.txt 须声明 PyYAML>=6.0（P8）"
