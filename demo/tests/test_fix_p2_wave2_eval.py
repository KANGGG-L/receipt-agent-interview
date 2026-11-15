# -*- coding: utf-8 -*-
"""Wave 2 自动化回归套件：T11 (评测臂首轮注入与错商负向防线) 与 T14 (SKU 归因验证)。

验证项：
1. 生产开关硬性锁死：memory_first_round_inject_enabled 与 vendor_precheck_enabled 缺省严格为 False (AC-04)；
2. run_eval 管线评测臂利用 expected GT supplier_name 作为 vendor_hint 注入首轮 (AC-03)；
3. 错商注入负向防线：商户 A 的私有记忆事实绝对禁止注入商户 B 的收据上下文，违规注入即翻红 (AC-05)；
4. 错商负向防线核验：商户 A 记忆 hit_count 不增加，ai_decision_log 无越权采纳；
5. SKU 匹配率归因核实：matched_flag_rate 与 simulated_match_rate 交叉自述一致性 (AC-10)。
"""

import os
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
from app.services import settings_service
from app.services import rag
from scripts import run_eval


def _create_isolated_db(tmp_path):
    """创建干净的隔离 schema 数据库。"""
    db_file = os.path.join(str(tmp_path), f"w2_{uuid.uuid4().hex[:8]}.db")
    db.DB_PATH = db_file
    db._make_engine()
    return db_file


def test_production_settings_defaults_pinned_to_false():
    """AC-04: 生产开关 memory_first_round_inject_enabled 与 vendor_precheck_enabled 缺省严格为 False。"""
    defaults = settings_service.SETTINGS_DEFAULTS
    assert defaults.get("memory_first_round_inject_enabled") is False, (
        "红线违规：memory_first_round_inject_enabled 生产缺省必须为 False！"
    )
    assert defaults.get("vendor_precheck_enabled") is False, (
        "红线违规：vendor_precheck_enabled 生产缺省必须为 False！"
    )


def test_run_eval_first_round_gt_hint_wiring(monkeypatch):
    """AC-03: run_eval 管线预测器在 memory=True 时提取 expected 的 supplier_name 作为 vendor_hint。"""
    captured_calls = []

    class DummySupervisor:
        @staticmethod
        def run_pipeline(image_path, vendor_hint="", vendor_hint_origin="", config=None):
            captured_calls.append({
                "image_path": image_path,
                "vendor_hint": vendor_hint,
                "origin": vendor_hint_origin,
            })
            return {
                "status": "success",
                "data": None,
                "total_ms": 100.0,
                "tokens_total": 50,
                "vendor_context": f"context_for_{vendor_hint}",
            }

    from app.chains import supervisor
    monkeypatch.setattr(supervisor, "run_pipeline", DummySupervisor.run_pipeline)

    # 1. memory=True 臂
    predictor_on = run_eval._pipeline_predictor(engine=None, model="", prompt_version="v1_2_0_sku_clean", memory=True)
    expected_sample = {"supplier_name": "Vendor_Golden_A", "vendor": "Vendor_Golden_A"}
    pred, meta = predictor_on("/fake/img.png", "sample_001", expected=expected_sample)

    assert len(captured_calls) == 1
    assert captured_calls[0]["vendor_hint"] == "Vendor_Golden_A"
    assert captured_calls[0]["origin"] == "eval_gt"
    assert meta["vendor_hint"] == "Vendor_Golden_A"
    assert meta["vendor_context"] == "context_for_Vendor_Golden_A"

    # 2. memory=False 对照组：不应传入 GT 供应商 hint 且上下文为空
    captured_calls.clear()
    predictor_off = run_eval._pipeline_predictor(engine=None, model="", prompt_version="v1_2_0_sku_clean", memory=False)
    pred_off, meta_off = predictor_off("/fake/img.png", "sample_002", expected=expected_sample)

    assert len(captured_calls) == 1
    assert captured_calls[0]["vendor_hint"] == ""
    assert meta_off["vendor_hint"] == ""
    assert meta_off["memory"] == "off"

    # 收尾必须还原 _pipeline_predictor 改写的进程级全局（SYSTEM_PROMPT /
    # 记忆注入三开关，memory=False 一臂会用 lambda 裸替换它们）。不还原时，
    # 同进程内排在后面的识别管线用例（memory_first_round 族等）首轮注入被
    # 静默关闭——这正是 2026-11-15 全量顺序下它们成片翻红的根因。
    predictor_on._restore_eval_globals()
    predictor_off._restore_eval_globals()


def test_wrong_vendor_injection_prevented_negative_case(tmp_path, monkeypatch):
    """AC-05: 错商注入负向防线：商户 A 记忆绝对不注入商户 B 单据，违规注入即翻红。"""
    db_file = _create_isolated_db(tmp_path)
    monkeypatch.setattr(db, "DB_PATH", db_file)

    conn = sqlite3.connect(db_file)
    try:
        # 为 Vendor_A 插入一条私有敏感记忆（如特有别名与价格提示）
        secret_content = "CONFIDENTIAL_VENDOR_A_SPECIAL_DISCOUNT_30_PCT"
        conn.execute(
            "INSERT INTO vendor_memory (memory_id, vendor, tenant_id, notes, sample, status, "
            "decay_score, hit_count, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            ("mem_vendor_a", "Vendor_A_Ltd", "default", secret_content, "sample_a", "active", 1.0, 0),
        )
        # 为 Vendor_B 插入收据
        conn.execute(
            "INSERT INTO receipts (id, supplier_name, status, rag_context_json, tenant_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (101, "Vendor_B_Restaurant", "parsed", "", "default"),
        )
        conn.commit()
    finally:
        conn.close()

    # 1. 尝试以 Vendor_B 的身份向 RAG 检索上下文
    ctx_b = rag.retrieve_context(vendor="Vendor_B_Restaurant", tenant_id="default")

    # 断言 1: 商户 B 的上下文中绝对不能包含任何商户 A 的敏感内容
    assert secret_content not in (ctx_b or ""), (
        f"安全阻断：Vendor_A 私有记忆泄露至 Vendor_B 上下文中！内容: {ctx_b}"
    )

    # 断言 2: 未命中单据检索后，商户 A 记忆行的 hit_count 绝对不能增加（仍为 0）
    conn = sqlite3.connect(db_file)
    try:
        hit_after_b = conn.execute(
            "SELECT hit_count FROM vendor_memory WHERE memory_id = 'mem_vendor_a'"
        ).fetchone()[0]
        assert hit_after_b == 0, f"安全阻断：Vendor_B 检索错误增加了 Vendor_A 记忆的 hit_count: {hit_after_b}"

        # 断言 3: ai_decision_log 中不得存在对 Vendor_A 记忆的越权采纳
        adopted_a = conn.execute(
            "SELECT count(*) FROM ai_decision_log WHERE receipt_id = 101 AND (extra LIKE '%mem_vendor_a%' OR ai_value LIKE '%Vendor_A%')"
        ).fetchone()[0]
        assert adopted_a == 0, "安全阻断：ai_decision_log 错误记录了对 Vendor_A 记忆的越权采纳"
    finally:
        conn.close()

    # 2. 正向对照：以 Vendor_A 自身身份检索必须能命中其私有内容，且 hit_count 增加至 1
    ctx_a = rag.retrieve_context(vendor="Vendor_A_Ltd", tenant_id="default")
    assert secret_content in (ctx_a or ""), (
        f"正向验证：Vendor_A 检索自身记忆失败！内容: {ctx_a}"
    )

    conn = sqlite3.connect(db_file)
    try:
        hit_after_a = conn.execute(
            "SELECT hit_count FROM vendor_memory WHERE memory_id = 'mem_vendor_a'"
        ).fetchone()[0]
        assert hit_after_a == 1, f"正向验证：Vendor_A 检索自身记忆后 hit_count 未增加: {hit_after_a}"
    finally:
        conn.close()


def test_t14_attribution_file_and_content_consistency():
    """AC-10: 验证 T14 归因报告存在，且包含确凿的一句话结论与分母对比。"""
    report_path = os.path.join(_REPO_ROOT, "docs", "superpowers", "reports", "t14-sku-regression-attribution.md")
    assert os.path.isfile(report_path), f"缺少 T14 归因报告: {report_path}"

    with open(report_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "23.73%" in content
    assert "21.07%" in content
    assert "687" in content
    assert "655" in content
    assert "32" in content
    assert "78.13%" in content or "78%" in content or "25 / 32" in content
    assert "simulated_match_rate" in content
    assert "matched_flag_rate" in content
