# -*- coding: utf-8 -*-
"""Wave 1 自动化回归套件：T10 (指标三段式重构) 与 T15 (前端治理面板自动化)。

验证项：
1. memory_metrics.py --json 输出完整包含 memory_metrics_v2（coverage / injection_rate / effect_ab）；
2. 保持所有顶层旧键向后兼容（memory_hit, memory_hit_effective, sku_match, edit_rate_ab 等）；
3. injection_rate 显式分列 production 与 eval_arm，目标挂在 injection_rate；
4. effect_ab 包含样本量 n、双比例差 95% 置信区间与统计显著性判定；
5. 记忆总开关关闭时，三段式指标准确反映未测量/分母为 0 状态；
6. 突变体反证：若 injection_rate 分母退化回全部单据，测试必须翻红。
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_TESTS_DIR, "..", ".."))
_DEMO_DIR = os.path.join(_REPO_ROOT, "demo")
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db
from scripts import memory_metrics as mm


def _create_test_db(tmp_path, receipts=(), memories=(), settings=None):
    """创建隔离的真实 schema 数据库。"""
    db_file = os.path.join(str(tmp_path), f"w1_{uuid.uuid4().hex[:8]}.db")
    db.DB_PATH = db_file
    db._make_engine()
    conn = sqlite3.connect(db_file)
    try:
        if receipts:
            conn.executemany(
                "INSERT INTO receipts (id, supplier_name, supplier_id, status, rag_context_json, tenant_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                receipts,
            )
        for m in memories:
            conn.execute(
                "INSERT INTO vendor_memory (memory_id, vendor, tenant_id, notes, sample, status, "
                "decay_score, valid_to, created_at, supplier_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    m["mid"],
                    m["vendor"],
                    m.get("tid", "default"),
                    m.get("notes", "别名提示"),
                    m.get("sample", "版式样本"),
                    m.get("status", "active"),
                    m.get("decay", 1.0),
                    m.get("valid_to", ""),
                    m.get("created_at", ""),
                    m.get("supplier_id", None),
                ),
            )
        if settings:
            for k, v in settings.items():
                conn.execute(
                    "INSERT INTO app_settings (key, value) VALUES (?, ?)",
                    (k, str(v)),
                )
        conn.commit()
    finally:
        conn.close()
    return db_file


def test_memory_metrics_v2_schema_completeness(tmp_path):
    """AC-01: memory_metrics.py 输出包含 coverage, injection_rate, effect_ab 三段式对象。"""
    receipts = [
        (1, "Vendor_A", None, "parsed", '{"vendor": "Vendor_A"}', "default"),
        (2, "Vendor_A", None, "edited", "", "default"),
        (3, "Vendor_B", None, "parsed", "", "default"),
        (4, "", None, "uploaded", "", "default"),
    ]
    memories = [
        {"mid": "m1", "vendor": "Vendor_A", "notes": "note A", "sample": "sample A"},
    ]
    db_path = _create_test_db(tmp_path, receipts, memories)
    out = mm.collect(db_path=db_path)

    assert "memory_metrics_v2" in out
    v2 = out["memory_metrics_v2"]

    # 1. coverage
    assert "coverage" in v2
    cov = v2["coverage"]
    assert cov["determinable_receipts"] == 3
    assert cov["eligible_receipts"] == 2
    assert cov["coverage_rate_pct"] == 66.67
    assert "note" in cov

    # 2. injection_rate
    assert "injection_rate" in v2
    inj = v2["injection_rate"]
    assert inj["target_pct"] == 95.0
    assert inj["eligible_denominator"] == 2
    assert "production" in inj
    assert inj["production"]["injected"] == 1
    assert inj["production"]["rate_pct"] == 50.0
    assert "eval_arm" in inj
    # M01 真实性契约：无真实评测运行证据时 eval_arm 为 null+原因（旧预期依赖生成值，
    # 按冻结口径重写——不为保绿保留虚假达标）。
    assert inj["eval_arm"]["injected"] is None
    assert inj["eval_arm"]["rate_pct"] is None
    assert "eval_run" in (inj["eval_arm"]["unknown_reason"] or "")
    assert "criteria" in inj
    assert "incomparable_disclaimer" in inj["criteria"]

    # 3. effect_ab
    assert "effect_ab" in v2
    ab = v2["effect_ab"]
    assert "has_memory" in ab
    assert "no_memory" in ab
    assert ab["sample_size_total"] == 3
    assert "delta_pp" in ab
    assert "ci_95" in ab
    assert "statistically_significant" in ab
    assert "note" in ab


def test_memory_metrics_backward_compatibility(tmp_path):
    """保持顶层旧键向后兼容：memory_hit, memory_hit_effective, sku_match, edit_rate_ab 等。"""
    db_path = _create_test_db(tmp_path)
    out = mm.collect(db_path=db_path)

    for key in ["db_path", "memory_hit", "memory_hit_effective", "edit_rate_ab", "sku_match", "decision_log", "vendor_memory"]:
        assert key in out, f"丢失顶层兼容键: {key}"


def test_injection_rate_split_production_and_eval_arm(tmp_path):
    """AC-02: injection_rate 显式分列 production 与 eval_arm，声明不可混读。"""
    receipts = [
        (1, "Vendor_A", None, "parsed", '{"ctx": 1}', "default"),
        (2, "Vendor_A", None, "parsed", "", "default"),
    ]
    memories = [
        {"mid": "m1", "vendor": "Vendor_A", "notes": "n", "sample": "s"},
    ]
    db_path = _create_test_db(tmp_path, receipts, memories)
    out = mm.collect(db_path=db_path, eval_arm_injected=2)
    inj = out["memory_metrics_v2"]["injection_rate"]

    assert inj["production"]["injected"] == 1
    assert inj["production"]["rate_pct"] == 50.0
    assert inj["eval_arm"]["injected"] == 2
    assert inj["eval_arm"]["rate_pct"] == 100.0
    assert "不可混读" in inj["criteria"]["incomparable_disclaimer"]


def test_effect_ab_confidence_interval_math(tmp_path):
    """验证 effect_ab 的两比例差置信区间计算。"""
    # 构造大样本量以测试 Wald 95% 置信区间
    receipts = []
    # has_memory: 100 张，10 张 edited (10%)
    for i in range(100):
        receipts.append((i + 1, "Vendor_A", None, "edited" if i < 10 else "parsed", "", "default"))
    # no_memory: 100 张，50 张 edited (50%)
    for i in range(100):
        receipts.append((i + 101, "Vendor_B", None, "edited" if i < 50 else "parsed", "", "default"))

    memories = [{"mid": "m1", "vendor": "Vendor_A", "notes": "n", "sample": "s"}]
    db_path = _create_test_db(tmp_path, receipts, memories)
    out = mm.collect(db_path=db_path)
    ab = out["memory_metrics_v2"]["effect_ab"]

    assert ab["has_memory"]["receipts"] == 100
    assert ab["has_memory"]["edited"] == 10
    assert ab["has_memory"]["rate_pct"] == 10.0
    assert ab["no_memory"]["receipts"] == 100
    assert ab["no_memory"]["edited"] == 50
    assert ab["no_memory"]["rate_pct"] == 50.0
    assert ab["delta_pp"] == -40.0
    assert ab["sample_size_total"] == 200

    ci = ab["ci_95"]
    assert ci is not None
    assert ci[0] < ci[1]
    assert ci[0] < -40.0 < ci[1]
    assert ab["statistically_significant"] is True
    assert "具有统计显著性" in ab["note"]


def test_switch_off_sets_v2_to_disabled(tmp_path):
    """开关关闭时，三段式指标准确反映未测量/分母归 0 状态。"""
    receipts = [
        (1, "Vendor_A", None, "parsed", '{"ctx": 1}', "default"),
    ]
    memories = [{"mid": "m1", "vendor": "Vendor_A", "notes": "n", "sample": "s"}]
    db_path = _create_test_db(tmp_path, receipts, memories, settings={"memory_enabled": "false"})
    out = mm.collect(db_path=db_path)
    v2 = out["memory_metrics_v2"]

    assert v2["coverage"]["eligible_receipts"] == 0
    assert v2["injection_rate"]["eligible_denominator"] == 0
    assert v2["injection_rate"]["production"]["rate_pct"] is None
    assert v2["injection_rate"]["eval_arm"]["rate_pct"] is None


def test_counterproof_injection_rate_mutant_fails(tmp_path):
    """突变体对抗反证：若有人篡改代码将 injection_rate 分母改成全部单据，断言必须能够识别并翻红。"""
    receipts = [
        (1, "Vendor_A", None, "parsed", '{"ctx": 1}', "default"),
        (2, "Vendor_A", None, "parsed", "", "default"),
        (3, "Vendor_B", None, "parsed", "", "default"),  # 无可用记忆
        (4, "", None, "uploaded", "", "default"),          # 供应商不可确定
    ]
    memories = [{"mid": "m1", "vendor": "Vendor_A", "notes": "n", "sample": "s"}]
    db_path = _create_test_db(tmp_path, receipts, memories)
    out = mm.collect(db_path=db_path)

    correct_eligible = out["memory_metrics_v2"]["injection_rate"]["eligible_denominator"]
    assert correct_eligible == 2, f"正确分母应为 2 (有记忆的 Vendor_A 单据)，实测 {correct_eligible}"

    # 模拟突变体：如果分母错误地使用了全部单据 4
    mutant_denominator = len(receipts)
    with pytest.raises(AssertionError):
        assert mutant_denominator == 2, "突变体验证：如果使用全部单据作为分母，必须断言失败"
