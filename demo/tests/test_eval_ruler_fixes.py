# -*- coding: utf-8 -*-
"""阶段 0 工作项②：评测器三修（E1 枚举假错 / E2 对齐级联 / E5a vendor 包含式）。

全部为「口径分」修复——只修尺子不修模型。T-E2-1 / T-E5-1 各带「修复前基线值断言」：
在测试内用旧对齐/旧判等算法的等价实现当场重算修复前结果（0.0），与新口径形成
前后对比证据链（对应 PLAN「修复 diff 报告逐条列出翻转实例」的最低实现）。
期望值全部手算（算式列在各用例注释）。
"""

import enum
import json
import os
import sys

_DEMO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_DEMO_DIR, "scripts"), _DEMO_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
# T-E1-2 要 import app.models（真实 DocForm），先落 env 隔离
os.environ.setdefault("DB_PATH", "/tmp/phase0_spec_wt.db")
os.environ.setdefault("RAG_DIR", "/tmp/phase0_spec_wt_rag")

import run_eval  # noqa: E402


def _pf1(res):
    return run_eval._per_field_f1_from_stats(res["field_stats"])


def _payload(vendor="新協興食品貿易有限公司", date="2024-08-02", total=2520,
             items=None, **kw):
    p = {
        "supplier_name": vendor,
        "date": date,
        "total_amount": total,
        "doc_form": "printed_delivery_note",
        "payment_marked": True,
        "currency": "HKD",
        "adjustment_notes": [],
        "items": items if items is not None else [
            {"name": "白菜", "qty": 2.0, "unit": "斤", "unit_price": 10.0, "amount": 20.0},
        ],
    }
    p.update(kw)
    return p


# ==================================================================
# E1：DocForm 枚举转型（_norm_text 首行枚举转型）
# ==================================================================
def test_te1_1_fake_str_enum_normalized():
    """本地 str-Enum 复刻 pydantic v2 model_dump() 的成员返回形态。"""
    class FakeDocForm(str, enum.Enum):
        PRINTED = "printed_delivery_note"

    # 先钉住缺陷形态：str(成员) 会得到 "FakeDocForm.PRINTED"（修复前的假错来源）
    assert str(FakeDocForm.PRINTED) == "FakeDocForm.PRINTED"
    pred = {"doc_form": FakeDocForm.PRINTED}
    gt = {"doc_form": "printed_delivery_note"}
    res = run_eval.compare(gt, pred)
    assert res["field_stats"]["doc_form"] == [1, 1], "枚举成员必须归一成 value 后判等"
    assert _pf1(res)["doc_form"]["f1"] == 1.0


def test_te1_2_real_docform_enum_normalized():
    """真实链路：supervisor.data.model_dump()（pydantic>=2, mode=python）返回
    DocForm 枚举成员；修复前 _norm_text 把它 str() 成 "DocForm.PRINTED" 恒错。"""
    from app.models import DocForm

    pred = _payload(doc_form=DocForm.PRINTED)
    gt = _payload()
    res = run_eval.compare(gt, pred)
    assert res["field_stats"]["doc_form"] == [1, 1]
    assert _pf1(res)["doc_form"]["f1"] == 1.0


def test_te1_3_wrong_docform_still_wrong():
    """负例：真错仍错，不许被枚举转型掩盖。"""
    res = run_eval.compare(_payload(), _payload(doc_form="credit_note"))
    pf1 = _pf1(res)
    # 手算：doc_form 双 present 不等 → FP=1 且 FN=1，TP=0 → P=R=0/(0+1)=0 → F1=0.0
    assert pf1["doc_form"] == {"tp": 0, "fp": 1, "fn": 1,
                               "precision": 0.0, "recall": 0.0, "f1": 0.0}


# ==================================================================
# E2：品名对齐三段式（解级联放大）
# ==================================================================
def _old_align_items(exp_items, pred_items):
    """修复前（段 A-only）对齐算法的逐行拷贝——仅作基线证据，不进产品代码。"""
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


def _row_level_qty_f1(pairs):
    """对「江X魚」这一错读行统计 qty 的 TP/FP/FN（按模块 docstring 权威表）。"""
    tp = fp = fn = 0
    for e, p in pairs:
        is_misread = ((e or {}).get("name") in ("江鱸魚", "江團魚")
                      or (p or {}).get("name") in ("江團魚",))
        if not is_misread:
            continue
        if e is not None and p is not None:
            if e.get("qty") == p.get("qty"):
                tp += 1
            else:
                fp += 1
                fn += 1
        elif e is not None:
            fn += 1        # 未配对 GT 行：qty present → FN
        elif p is not None:
            fp += 1        # 未配对预测行：qty present → FP
    return tp, fp, fn


def test_te2_1_s030_cascade_fixed():
    """S030 合成复刻：江鱸魚误读成江團魚，qty/price/amount 全对。

    修复前基线（旧段 A-only 对齐）：江X魚行不配对 → 该行 qty TP=0、FP=1、FN=1
      → P = 0/(0+1) = 0, R = 0/(0+1) = 0, F1 = 0.0（错读行数值字段全被级联计错）
    修复后：编辑距离 1 ≤ max(2, floor(0.3*3)=0)=2 → 配对成功。
    """
    gt = _payload(items=[
        {"name": "江鱸魚", "qty": 41, "unit": "斤", "unit_price": 40, "amount": 1640},
        {"name": "半殼生蠔", "qty": 80, "unit": "隻", "unit_price": 11, "amount": 880},
    ])
    pred = _payload(items=[
        {"name": "江團魚", "qty": 41, "unit": "斤", "unit_price": 40, "amount": 1640},
        {"name": "半殼生蠔", "qty": 80, "unit": "隻", "unit_price": 11, "amount": 880},
    ])
    exp_n = run_eval.normalize(gt)["items"]
    pred_n = run_eval.normalize(pred)["items"]

    # ---- 修复前基线断言（[口径分]）：旧对齐下错读行 qty F1 = 0.0 ----
    old_pairs = _old_align_items(exp_n, pred_n)
    tp, fp, fn = _row_level_qty_f1(old_pairs)
    assert (tp, fp, fn) == (0, 1, 1), "旧对齐：错读行未配对 → FN+FP"
    assert run_eval._prf(tp, fp, fn)[2] == 0.0, "修复前错读行 qty F1 基线值 = 0.0"

    # ---- 修复后：两行均配对（江鱸魚 vs 江團魚 编辑距离 1）----
    new_pairs = run_eval._align_items(exp_n, pred_n)
    assert all(e is not None and p is not None for e, p in new_pairs), "两行必须都配对"
    res = run_eval.compare(gt, pred)
    pf1 = _pf1(res)
    # 手算：qty/unit_price/amount 双行配对且全等 → tp=2, fp=fn=0
    #   P = 2/2 = 1.0, R = 2/2 = 1.0, F1 = 1.0（修复前整行级联计错）
    for f in ("item.qty", "item.unit_price", "item.amount"):
        assert pf1[f] == {"tp": 2, "fp": 0, "fn": 0,
                          "precision": 1.0, "recall": 1.0, "f1": 1.0}, f
    # 手算 item.name：半殼生蠔行严格相等 → TP=1；江X魚行配对但不等 → FP=1 且 FN=1
    #   P = 1/2 = 0.5, R = 1/2 = 0.5, F1 = 2*0.25/1 = 0.5（真错仍计，不许全单洗白）
    assert pf1["item.name"] == {"tp": 1, "fp": 1, "fn": 1,
                                "precision": 0.5, "recall": 0.5, "f1": 0.5}


def test_te2_2_numeric_signature_fallback():
    """段 C 数字签名兜底：品名编辑距离远超阈值但 (qty,price,amount) 全同。"""
    gt = _payload(items=[{"name": "長名商品型號ABCDE", "qty": 5, "unit": "箱",
                          "unit_price": 30, "amount": 150}])
    pred = _payload(items=[{"name": "完全不同的寫法", "qty": 5, "unit": "箱",
                            "unit_price": 30, "amount": 150}])
    res = run_eval.compare(gt, pred)
    pf1 = _pf1(res)
    # 手算：品名 11 字 vs 7 字，编辑距离远超 min(max(2,3),10)=3 → 段 B 不配；
    # 数字签名 (5,30,150) 两侧各唯一 → 段 C 配对。
    #   qty/unit_price/amount: tp=1, fp=fn=0 → F1=1.0
    #   name: 配对但不等 → FP+FN → F1=0.0
    for f in ("item.qty", "item.unit_price", "item.amount"):
        assert pf1[f]["f1"] == 1.0 and pf1[f]["tp"] == 1, f
    assert pf1["item.name"] == {"tp": 0, "fp": 1, "fn": 1,
                                "precision": 0.0, "recall": 0.0, "f1": 0.0}
    assert pf1["item.unit"]["tp"] == 1, "unit 箱=箱 随段 C 配对计 TP"


def test_te2_3_no_overmatching_of_true_error():
    """负例·不许过度配对：白菜 vs 蘿蔔编辑距离 2 = 整个 2 字名换掉（相对容差 100%），
    段 B 阈值上限 len-1 挡住；数字又全不同 → 不配对，维持旧行为 FN+FP。"""
    gt = _payload(items=[{"name": "白菜", "qty": 2, "unit": "斤",
                          "unit_price": 10, "amount": 20}])
    pred = _payload(items=[{"name": "蘿蔔", "qty": 3, "unit": "斤",
                            "unit_price": 15, "amount": 45}])
    # 先钉距离事实：dist(白菜, 蘿蔔) == 2 == len(白菜)（整个名字都不同）
    assert run_eval._levenshtein("白菜", "蘿蔔") == 2
    res = run_eval.compare(gt, pred)
    pf1 = _pf1(res)
    # 手算（未配对）：qty 双侧 present → FN=1 且 FP=1 → P=R=0 → F1=0.0；
    # unit 同理（旧口径下未配对行不计等价，防容差吃掉真错）
    for f in ("item.qty", "item.unit"):
        assert pf1[f] == {"tp": 0, "fp": 1, "fn": 1,
                          "precision": 0.0, "recall": 0.0, "f1": 0.0}, f


def test_te2_4_duplicate_signature_not_paired():
    """负例·签名并列不配：GT 两行同签名 (2,10,20)，pred 一行 → exp 侧 sig 不唯一
    → 段 C 不配（确定性、宁漏勿错）。"""
    gt = _payload(items=[
        {"name": "白菜", "qty": 2, "unit": "斤", "unit_price": 10, "amount": 20},
        {"name": "小蔥", "qty": 2, "unit": "斤", "unit_price": 10, "amount": 20},
    ])
    pred = _payload(items=[{"name": "xyz商品長名稱", "qty": 2, "unit": "斤",
                            "unit_price": 10, "amount": 20}])
    res = run_eval.compare(gt, pred)
    pf1 = _pf1(res)
    # 手算：未配对 2 GT 行 → qty FN=2；未配对 1 pred 行 → FP=1。
    #   P = 0/(0+1) = 0, R = 0/(0+2) = 0, F1 = 0.0
    assert pf1["item.qty"] == {"tp": 0, "fp": 1, "fn": 2,
                               "precision": 0.0, "recall": 0.0, "f1": 0.0}


def test_te2_5_empty_name_rows_pair_via_signature():
    """空名行：GT name="" 但数字齐全 → 段 B 跳过（空名不参与模糊配）、走段 C；
    pred 也空名同数字 → 配对成功，name 双缺失不计实例。"""
    items = [{"name": "", "qty": 3, "unit": "斤", "unit_price": 5, "amount": 15}]
    res = run_eval.compare(_payload(items=items), _payload(items=items))
    pf1 = _pf1(res)
    assert pf1["item.qty"]["tp"] == 1 and pf1["item.qty"]["f1"] == 1.0
    assert pf1["item.unit"]["tp"] == 1 and pf1["item.amount"]["tp"] == 1
    assert "item.name" not in pf1, "双缺失品名不产生实例"


# ==================================================================
# E5a：vendor 包含式
# ==================================================================
_GT_VENDOR = "Vitasoy International Holdings Ltd. 維他奶國際集團有限公司"
_PRED_VENDOR = "維他奶國際集團有限公司"


def test_te5_1_vendor_containment_fixed():
    """ERRORS E5 原型：预测只出中文段 vs GT 全称。

    修复前基线（旧 a==b）：归一后 'vitasoyinternationalholdingsltd維他奶國際集團有限公司'
    != '維他奶國際集團有限公司' → 单实例 FP=1 且 FN=1 → P=R=0 → F1 = 0.0。
    """
    a, b = run_eval._norm_text(_GT_VENDOR), run_eval._norm_text(_PRED_VENDOR)
    # ---- 修复前基线断言（[口径分]）：旧严格判等在此必错 ----
    assert a != b, "旧口径（严格相等）把该实例判错 → FP+FN → F1=0.0"
    assert run_eval._prf(0, 1, 1)[2] == 0.0, "修复前 vendor F1 基线值 = 0.0"

    # ---- 修复后：包含关系成立且短边长度 11 ≥ 4 → 等价 ----
    assert len(b) >= run_eval.VENDOR_CONTAIN_MIN_LEN
    res = run_eval.compare(_payload(vendor=_GT_VENDOR), _payload(vendor=_PRED_VENDOR))
    assert res["field_stats"]["vendor"] == [1, 1]
    assert _pf1(res)["vendor"]["f1"] == 1.0


def test_te5_2_short_string_no_free_ride():
    """负例·短串不许蹭分：'公司' 是全称的子串但长度 2 < 4 → 不等 → FP+FN。"""
    assert _PRED_VENDOR and "公司" in run_eval._norm_text(_GT_VENDOR)
    res = run_eval.compare(_payload(vendor=_GT_VENDOR), _payload(vendor="公司"))
    pf1 = _pf1(res)
    assert pf1["vendor"] == {"tp": 0, "fp": 1, "fn": 1,
                             "precision": 0.0, "recall": 0.0, "f1": 0.0}


def test_te5_3_true_vendor_error_still_wrong():
    """真错仍错：新怡興 vs 新協興 互不包含 → FP+FN。"""
    res = run_eval.compare(_payload(vendor="新協興食品貿易有限公司"),
                           _payload(vendor="新怡興"))
    pf1 = _pf1(res)
    assert pf1["vendor"] == {"tp": 0, "fp": 1, "fn": 1,
                             "precision": 0.0, "recall": 0.0, "f1": 0.0}


def test_te5_4_date_and_total_unchanged_strict():
    """防误伤面扩大：同夹具下 date/total 仍严格相等判分（不受 vendor 包含式影响）。"""
    gt = _payload(vendor=_GT_VENDOR, date="2024-08-02", total=2520)
    pred = _payload(vendor=_PRED_VENDOR, date="2024-08-03", total=999)
    res = run_eval.compare(gt, pred)
    assert res["field_stats"]["date"] == [1, 0], "date 仍严格判等 → 计错"
    assert res["field_stats"]["total"] == [1, 0], "total 仍严格判等 → 计错"
    assert res["field_stats"]["vendor"] == [1, 1], "vendor 包含式等价 → 计对"
    assert "date" in res["mismatched_fields"] and "total" in res["mismatched_fields"]
    assert "vendor" not in res["mismatched_fields"]
