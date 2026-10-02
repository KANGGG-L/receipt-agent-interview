# -*- coding: utf-8 -*-
"""阶段 0 工作项①：run_eval per-field P/R/F1（micro 口径，[口径分]）判别式测试。

夹具 F1-A..F1-G 对应 SPEC_PHASE0 §2.4，期望值全部手算（算式列在各用例注释）。
判别力设计：F1-B 钉 P≠R（防 P=R 假实现）；F1-G 钉 micro≠macro（防样本级求平均）；
落盘断言验证 stub 自洽与报告键；兼容断言防 field_stats 前两位语义漂移。
"""

import csv
import json
import os
import sys

_DEMO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_DEMO_DIR, "scripts"), _DEMO_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("DB_PATH", "/tmp/phase0_spec_wt.db")
os.environ.setdefault("RAG_DIR", "/tmp/phase0_spec_wt_rag")

import run_eval  # noqa: E402

# 夹具基底：复用 test_eval_harness._GT_PAYLOAD 形状（2 明细行，GT schema v2 全集）
_GT_PAYLOAD = {
    "supplier_name": "Alpha Trading Co",
    "date": "2026-08-01",
    "total_amount": 120.0,
    "doc_form": "printed_delivery_note",
    "payment_marked": False,
    "payment_evidence": "",
    "currency": "HKD",
    "discount_amount": 0.0,
    "deposit_amount": 0.0,
    "delivery_fee": 0.0,
    "service_fee": 0.0,
    "tax_amount": 0.0,
    "rounding_adjustment": 0.0,
    "adjustment_notes": [],
    "items": [
        {"name": "白菜", "quantity": 2.0, "unit": "斤", "unit_price": 10.0, "amount": 20.0},
        {"name": "蘿蔔", "quantity": 5.0, "unit": "斤", "unit_price": 20.0, "amount": 100.0},
    ],
}


def _pf1(res):
    """compare 结果 → per_field_f1（走 run_eval 聚合同一实现）。"""
    return run_eval._per_field_f1_from_stats(res["field_stats"])


def _mini_evalset(tmp_path, payloads):
    """构造最小 evalset（manifest + expected/，无图；stub/注入预测器不需要图）。

    rows 顺序 = manifest 顺序；payloads 为 {sample_id: gt_payload}。
    """
    ev = os.path.join(str(tmp_path), "evalsets")
    os.makedirs(os.path.join(ev, "expected"), exist_ok=True)
    fields = ["sample_id", "image", "split", "doc_form", "layout_type", "supplier_id",
              "gt_status", "gt_source_model", "src_sha1", "short_side"]
    rows = []
    for i, (sid, payload) in enumerate(sorted(payloads.items())):
        with open(os.path.join(ev, "expected", sid + ".json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        rows.append({"sample_id": sid, "image": "receipts/%s.png" % sid, "split": "val",
                     "doc_form": "printed_delivery_note", "layout_type": "table",
                     "supplier_id": "V1", "gt_status": "confirmed", "gt_source_model": "",
                     "src_sha1": "", "short_side": "0"})
    with open(os.path.join(ev, "manifest.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return ev


# ------------------------------------------------------------------
# F1-A 完美：pred = GT 深拷贝
# ------------------------------------------------------------------
def test_f1_a_perfect_match_all_tp():
    pred = json.loads(json.dumps(_GT_PAYLOAD))
    res = run_eval.compare(_GT_PAYLOAD, pred)
    pf1 = _pf1(res)
    # 手算：GT 双行全字段对齐全等。item.* 每字段 2 个实例（双行）→ tp=2；
    # vendor/date/total/doc_form/payment_marked(False≠None→present)/currency 各 1 实例 → tp=1；
    # 费用全 0 → GT 无实例；adjustment_notes 空 → 无实例；item_count 不进 F1
    assert pf1["item.qty"] == {"tp": 2, "fp": 0, "fn": 0, "precision": 1.0, "recall": 1.0, "f1": 1.0}
    assert pf1["vendor"] == {"tp": 1, "fp": 0, "fn": 0, "precision": 1.0, "recall": 1.0, "f1": 1.0}
    assert pf1["payment_marked"]["tp"] == 1
    assert all(v["fp"] == 0 and v["fn"] == 0 and v["f1"] == 1.0 for v in pf1.values())
    assert "item_count" not in pf1, "item_count 非抽取字段，不进 F1"
    assert "delivery_fee" not in pf1, "GT 全零费用不产生实例"


# ------------------------------------------------------------------
# F1-B 漏一行：GT 2 行、pred 只留第 1 行
# ------------------------------------------------------------------
def test_f1_b_missing_row_p_ne_r():
    pred = json.loads(json.dumps(_GT_PAYLOAD))
    pred["items"] = pred["items"][:1]
    res = run_eval.compare(_GT_PAYLOAD, pred)
    pf1 = _pf1(res)
    # 手算（每个 item.* 字段同型）：第 1 行配对全等 → TP=1；第 2 行未配对 GT 行，
    # 5 字段全 present → FN=1；FP=0。
    #   P = tp/(tp+fp) = 1/1 = 1.0
    #   R = tp/(tp+fn) = 1/(1+1) = 0.5
    #   F1 = 2*P*R/(P+R) = 2*1*0.5/1.5 = 0.6667
    for f in ("item.name", "item.qty", "item.unit", "item.unit_price", "item.amount"):
        assert pf1[f] == {"tp": 1, "fp": 0, "fn": 1,
                          "precision": 1.0, "recall": 0.5, "f1": 0.6667}, f
    # P≠R 判别：防 P=R 的假实现（如把 FP 同时记成 FN 的对称污染）
    assert pf1["item.name"]["precision"] != pf1["item.name"]["recall"]


# ------------------------------------------------------------------
# F1-C 值错：total_amount 120 → 999
# ------------------------------------------------------------------
def test_f1_c_value_error_fp_and_fn():
    pred = json.loads(json.dumps(_GT_PAYLOAD))
    pred["total_amount"] = 999.0
    res = run_eval.compare(_GT_PAYLOAD, pred)
    pf1 = _pf1(res)
    # 手算：total 双 present 不等 → FP+1 且 FN+1，TP=0。
    #   P = 0/(0+1) = 0.0；R = 0/(0+1) = 0.0；F1 = 0.0
    assert pf1["total"] == {"tp": 0, "fp": 1, "fn": 1,
                            "precision": 0.0, "recall": 0.0, "f1": 0.0}
    # 其余字段不受牵连 → f1 全 1.0
    others = {k: v for k, v in pf1.items() if k != "total"}
    assert others and all(v["f1"] == 1.0 for v in others.values())


# ------------------------------------------------------------------
# F1-D 多一行：pred 多 1 行全字段行
# ------------------------------------------------------------------
def test_f1_d_extra_row_fp():
    pred = json.loads(json.dumps(_GT_PAYLOAD))
    pred["items"].append({"name": "額外品項測試", "quantity": 9.0,
                          "unit": "箱", "unit_price": 7.0, "amount": 63.0})
    res = run_eval.compare(_GT_PAYLOAD, pred)
    pf1 = _pf1(res)
    # 手算：GT 双行按名严格配对（段 A），额外行名字 6 字与 2 字品名编辑距离远超
    # 阈值 min(max(2,0),1)=1、数字签名 (9,7,63) 无 GT 侧对应 → 不配 → 未配对预测行
    # 每 present 字段 FP+1。
    #   item.name: TP=2, FP=1, FN=0 → P = 2/3 = 0.6667；R = 2/2 = 1.0
    #              F1 = 2*(2/3)*1/(2/3+1) = (4/3)/(5/3) = 0.8
    assert pf1["item.name"] == {"tp": 2, "fp": 1, "fn": 0,
                                "precision": 0.6667, "recall": 1.0, "f1": 0.8}
    for f in ("item.qty", "item.unit", "item.unit_price", "item.amount"):
        assert pf1[f] == {"tp": 2, "fp": 1, "fn": 0,
                          "precision": 0.6667, "recall": 1.0, "f1": 0.8}, f
    # 整单级字段不受多行影响
    assert pf1["vendor"] == {"tp": 1, "fp": 0, "fn": 0,
                             "precision": 1.0, "recall": 1.0, "f1": 1.0}


# ------------------------------------------------------------------
# F1-E 单字段缺失：pred 第 1 行 unit 置空串
# ------------------------------------------------------------------
def test_f1_e_single_field_missing_fn():
    pred = json.loads(json.dumps(_GT_PAYLOAD))
    pred["items"][0]["unit"] = ""
    res = run_eval.compare(_GT_PAYLOAD, pred)
    pf1 = _pf1(res)
    # 手算：第 1 行配对但 pred unit 缺失（空 = 缺失）→ FN=1；第 2 行 unit 斤=斤 → TP=1。
    #   P = 1/(1+0) = 1.0；R = 1/(1+1) = 0.5；F1 = 0.6667
    assert pf1["item.unit"] == {"tp": 1, "fp": 0, "fn": 1,
                                "precision": 1.0, "recall": 0.5, "f1": 0.6667}


# ------------------------------------------------------------------
# F1-F 双缺失跳过：GT 与 pred 第 2 行 unit 均空
# ------------------------------------------------------------------
def test_f1_f_both_missing_skipped():
    gt = json.loads(json.dumps(_GT_PAYLOAD))
    gt["items"][1]["unit"] = ""
    pred = json.loads(json.dumps(gt))
    res = run_eval.compare(gt, pred)
    pf1 = _pf1(res)
    # 手算：第 2 行双缺失 → 不计分；实例只来自第 1 行（斤=斤）→ tp=1，
    # 不因双空产生 tp=2
    assert pf1["item.unit"] == {"tp": 1, "fp": 0, "fn": 0,
                                "precision": 1.0, "recall": 1.0, "f1": 1.0}


# ------------------------------------------------------------------
# F1-G micro ≠ macro 守卫：两次单样本经 run_eval 聚合
# ------------------------------------------------------------------
def test_f1_g_micro_not_macro(tmp_path):
    """样本 A（2 行全对，qty tp=2）+ 样本 B（漏 1 行，qty tp=1 fn=1）→
    micro: tp=3, fn=1 → P=1.0, R=3/4=0.75, F1=2*1*0.75/1.75=0.8571；
    macro = (1.0 + 0.6667)/2 = 0.8333 → 必须**不**等于 micro。"""
    gt_a = json.loads(json.dumps(_GT_PAYLOAD))
    gt_b = json.loads(json.dumps(_GT_PAYLOAD))   # GT 仍 2 行
    pred_b = json.loads(json.dumps(_GT_PAYLOAD))
    pred_b["items"] = pred_b["items"][:1]        # 样本 B 的预测漏 1 行
    ev = _mini_evalset(tmp_path, {"SA1": gt_a, "SB1": gt_b})

    def _predictor(image_path, sample_id):
        payload = gt_a if sample_id == "SA1" else pred_b
        return json.loads(json.dumps(payload)), {"latency_ms": 0.0, "tokens": 0, "error": None}

    report = run_eval.run_eval(split="val", prompt_version="v1_2_0_sku_clean",
                               engine="stub", evalset_dir=ev,
                               report_dir=str(tmp_path / "reports"), predictor=_predictor)
    got = report["per_field_f1"]["item.qty"]
    assert got == {"tp": 3, "fp": 0, "fn": 1,
                   "precision": 1.0, "recall": 0.75, "f1": 0.8571}, got
    macro = round((1.0 + 0.6667) / 2.0, 4)
    assert got["f1"] != macro, "禁止 macro（样本级 F1 求平均稀释真实错误）"
    # 两组口径（工作项④ schema）同时在场
    assert set(report["field_f1_groups"]) == {"high_freq", "line_item"}


# ------------------------------------------------------------------
# 落盘 + stub 自洽 + 兼容断言
# ------------------------------------------------------------------
def test_stub_report_per_field_f1_self_consistent(tmp_path):
    """stub 回读 GT：落盘报告含 per_field_f1，每字段 tp=实例数、fp=fn=0、f1=1.0。"""
    ev = _mini_evalset(tmp_path, {"S1": _GT_PAYLOAD})
    report = run_eval.run_eval(split="val", prompt_version="v1_2_0_sku_clean",
                               engine="stub", evalset_dir=ev,
                               report_dir=str(tmp_path / "reports"))
    assert os.path.exists(report["report_path"])
    with open(report["report_path"], encoding="utf-8") as f:
        saved = json.load(f)
    assert "per_field_f1" in saved and "field_f1_groups" in saved
    for field, stat in saved["per_field_f1"].items():
        assert stat["tp"] > 0 and stat["fp"] == 0 and stat["fn"] == 0 \
            and stat["f1"] == 1.0, (field, stat)
    # FieldStat 落盘为普通 JSON 数组（子类不得污染报告序列化）
    assert all(isinstance(v, dict) for v in saved["per_field_f1"].values())
    # 两组口径 stub 自洽：high_freq 与 line_item 全 1.0
    assert saved["field_f1_groups"]["high_freq"]["f1"] == 1.0
    assert saved["field_f1_groups"]["line_item"]["f1"] == 1.0


def test_field_stats_first_two_slots_frozen():
    """兼容断言：field_stats["payment_marked"] == [1, 1] 断言形式必须继续成立
    （§5 既有测试零改动），前两位语义 [total, matched] 冻结。"""
    exp = json.loads(json.dumps(_GT_PAYLOAD))
    exp["payment_marked"] = True
    pred = json.loads(json.dumps(_GT_PAYLOAD))
    pred["payment_marked"] = True
    res = run_eval.compare(exp, pred)
    stat = res["field_stats"]["payment_marked"]
    assert len(stat) == 5, "扩位后应为 [total, matched, tp, fp, fn]"
    assert stat == [1, 1], "前两位与两位 list 判等兼容（既有断言形式）"
    assert stat[0] == 1 and stat[1] == 1
    assert stat[2] == 1 and stat[3] == 0 and stat[4] == 0
    # 整集聚合侧同样兼容：run_eval 聚合后的 per_field_accuracy 只依赖前两位
    assert stat[:2] == [1, 1]
