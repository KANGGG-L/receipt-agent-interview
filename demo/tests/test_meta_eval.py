# -*- coding: utf-8 -*-
"""T5 元评测（Gap A3 + A4）：评估器可信度自检测试。

覆盖：
1. meta_eval_set.json 结构校验：>=20 条、必填字段（id/source/input/expected_verdict/why）、
   verdict 枚举合法（absolutely_correct / absolutely_wrong）
2. mock（规则式）judge 离线跑元评测：对「绝对正确」样本判对率 100%，
   对「绝对错误」样本判错率 100%，evaluator_trustworthy=True
3. 注入劣化 judge（恒判对 / 恒判错 / 随机）时 evaluator_trustworthy 必须为 False
4. 报告落盘结构完整

judge 是可注入函数：真实审核模型的元评测跑分属运维动作
（python run_meta_eval.py --judge audit），测试只离线自检结构与规则 judge。
"""

import json
import os
import sys

import pytest

import run_meta_eval as meta_eval
from run_meta_eval import (
    META_EVAL_MIN_ITEMS,
    VERDICT_CORRECT,
    VERDICT_ENUM,
    VERDICT_WRONG,
    load_meta_eval_set,
    run_meta_eval,
    validate_meta_eval_set,
    write_report,
)


# -------------------------------------------------------------
# 结构校验
# -------------------------------------------------------------
def test_meta_eval_set_min_items():
    """元评测集 >= 20 条已确证样本（Gap A4 验收线）。"""
    items = load_meta_eval_set()
    assert len(items) >= META_EVAL_MIN_ITEMS, (
        f"元评测集样本不足: {len(items)} < {META_EVAL_MIN_ITEMS}"
    )


def test_meta_eval_set_schema():
    """每条含 id/source/input/expected_verdict/why，verdict 枚举合法，id 唯一。"""
    items = load_meta_eval_set()
    ids = set()
    for item in items:
        for field in ("id", "source", "expected_verdict", "why"):
            assert str(item.get(field) or "").strip(), f"样本缺字段 {field}: {item}"
        assert isinstance(item.get("input"), dict), f"{item['id']} input 必须是对象"
        assert "receipt" in item["input"], f"{item['id']} input 缺 receipt"
        assert item["expected_verdict"] in VERDICT_ENUM, (
            f"{item['id']} verdict 非法: {item['expected_verdict']}"
        )
        assert item["why"].strip(), f"{item['id']} 缺判定依据 why"
        assert item["id"] not in ids, f"样本 id 重复: {item['id']}"
        ids.add(item["id"])


def test_meta_eval_set_binary_balance():
    """两类样本都非空（否则 100% 率无意义）。"""
    items = load_meta_eval_set()
    kinds = {it["expected_verdict"] for it in items}
    assert VERDICT_CORRECT in kinds and VERDICT_WRONG in kinds


def test_validate_meta_eval_set_rejects_bad_structures():
    """结构校验器能拒绝：条数不足 / 非法 verdict / 缺字段 / 重复 id。"""
    good = load_meta_eval_set()[0]
    assert validate_meta_eval_set([good] * 3) != []          # 条数不足
    bad_verdict = dict(good, id="X1", expected_verdict="maybe")
    assert any("expected_verdict" in p for p in validate_meta_eval_set([good] * 20 + [bad_verdict]))
    missing = {k: v for k, v in good.items() if k != "why"}
    assert any("why" in p for p in validate_meta_eval_set([good] * 20 + [missing]))
    dup = dict(good)
    assert any("重复" in p for p in validate_meta_eval_set([good] * 20 + [dup]))


# -------------------------------------------------------------
# mock（规则式）judge：离线元评测全绿
# -------------------------------------------------------------
def test_mock_judge_trustworthy_true():
    """规则 judge 对绝对正确样本判对率 100%、对绝对错误样本判错率 100%。"""
    items = load_meta_eval_set()
    report = run_meta_eval(meta_eval.mock_judge, items)
    misses = [r for r in report["per_item"] if not r["hit"]]
    assert report["correct_rate"] == 1.0, (
        f"判对率 {report['correct_rate']} != 1.0，miss: {[r['id'] for r in misses]}"
    )
    assert report["wrong_rate"] == 1.0, (
        f"判错率 {report['wrong_rate']} != 1.0，miss: {[r['id'] for r in misses]}"
    )
    assert report["evaluator_trustworthy"] is True
    assert report["n_absolutely_correct"] > 0 and report["n_absolutely_wrong"] > 0


def test_judge_signature_takes_only_input_not_item():
    """防偷看（签名级）：judge 只收 item["input"]（receipt_input），
    签名上收不到 expected_verdict / why 等答案字段，元评测不可能自证。"""
    import inspect
    for fn in (meta_eval.mock_judge, meta_eval.audit_judge):
        params = list(inspect.signature(fn).parameters)
        assert params and params[0] != "item", \
            f"{fn.__name__} 首参不得收整条 item（只能收 item['input']）"
        assert not any("expected" in p or "verdict" in p or "why" in p
                       for p in params), \
            f"{fn.__name__} 签名暴露答案字段: {params}"


def test_run_meta_eval_passes_only_input_to_judge():
    """防偷看（行为级）：run_meta_eval 实际传给 judge 的就是 item['input']，
    其中不含 expected_verdict。"""
    items = load_meta_eval_set()[:3]
    seen = []

    def _spy(receipt_input):
        seen.append(receipt_input)
        return {"verdict": VERDICT_CORRECT, "why": "spy"}

    run_meta_eval(_spy, items)
    assert len(seen) == len(items)
    for got, item in zip(seen, items):
        assert got == item["input"], "judge 必须只收到 item['input']"
        assert "expected_verdict" not in got and "why" not in got


# -------------------------------------------------------------
# 注入劣化 judge：必须返回 trustworthy=False
# -------------------------------------------------------------
def _always(verdict):
    def _judge(receipt_input):
        return {"verdict": verdict, "why": "degraded judge"}
    return _judge


def test_degraded_judge_always_correct_is_untrustworthy():
    """恒判对的 judge 会漏掉全部绝对错误样本 → 判错率 0 → 不可信。"""
    report = run_meta_eval(_always(VERDICT_CORRECT), load_meta_eval_set())
    assert report["wrong_rate"] == 0.0
    assert report["evaluator_trustworthy"] is False


def test_degraded_judge_always_wrong_is_untrustworthy():
    """恒判错的 judge 会冤枉全部绝对正确样本 → 判对率 0 → 不可信。"""
    report = run_meta_eval(_always(VERDICT_WRONG), load_meta_eval_set())
    assert report["correct_rate"] == 0.0
    assert report["evaluator_trustworthy"] is False


def test_degraded_judge_unparseable_verdict_is_untrustworthy():
    """judge 返回 None（不可解析/模型失败）计为未命中 → 不可信。"""
    report = run_meta_eval(_always(None), load_meta_eval_set())
    assert report["evaluator_trustworthy"] is False
    assert report["miss_count"] == report["total"]


# -------------------------------------------------------------
# 评估器确定性约定：审核腿 temperature 必须 0（Gap A4）
# -------------------------------------------------------------
def test_audit_leg_temperature_is_zero():
    """build_audit_model 产出的审核模型 temperature 强制 0；识别腿不受污染。"""
    from app.llm import build_audit_model, build_recognition_model

    aud = build_audit_model()
    rec = build_recognition_model()
    assert getattr(aud, "temperature", None) == 0.0, "审核腿（评估器）temperature 必须 0"
    # 缓存按 side 隔离：识别腿保持类默认采样参数，不被审核腿的 0 覆盖
    assert rec is not aud
    assert getattr(rec, "temperature", None) != 0.0


# -------------------------------------------------------------
# 报告落盘
# -------------------------------------------------------------
def test_write_report(tmp_path):
    report = run_meta_eval(meta_eval.mock_judge, load_meta_eval_set()[:5])
    path = write_report(report, report_dir=str(tmp_path), judge_name="mock")
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    assert doc["judge"] == "mock"
    assert "evaluator_trustworthy" in doc and "per_item" in doc
    assert doc["total"] == 5


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
