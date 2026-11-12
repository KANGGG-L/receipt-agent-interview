# -*- coding: utf-8 -*-
"""VendorMemory P2 Wave 3: 飞轮抽取常开与四件配套回归测试套件 (T12).

涵盖规格与验收标准：
- AC-06: memory_extract_enabled 缺省为 True，可由 settings 关闭
- AC-07: 质量闸拦截边缘 alias、伪区间 price_range、无差异 unit；--fail-on-low 退出码为 0
- AC-08: 真实成本计量与 leg="memory_extract" 链路上下文
- 配套③: 管理台可回关验证
- 配套④: 来源诚实性 source_kind='extract'
- 重放工具: memory_extract_replay 幂等重放
- 突变体反证: 绕过质量闸写入 LOW 事实时 memory_fact_quality_report 必须翻红退出码 1
"""

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from unittest.mock import MagicMock, patch
import pytest

_DEMO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

from app.services import settings_service, memory_facts, memory_extract
from app.services.memory_facts import MemoryFact
import app.api_receipts as api_receipts
import app.db as db
from app.services.llm_logger import get_llm_context


@pytest.fixture
def isolated_env():
    """每个测试独立的临时数据库与环境。"""
    tmp = tempfile.mkdtemp(prefix="vm_w3_test_")
    db_file = os.path.join(tmp, "test_wave3.db")
    rag_dir = os.path.join(tmp, "test_rag")
    old_db = os.environ.get("DB_PATH")
    old_rag = os.environ.get("RAG_DIR")
    old_db_path = getattr(db, "DB_PATH", None)

    os.environ["DB_PATH"] = db_file
    os.environ["RAG_DIR"] = rag_dir
    db.DB_PATH = db_file
    db._make_engine()

    yield {"tmp": tmp, "db_file": db_file, "rag_dir": rag_dir}

    if old_db_path is not None:
        db.DB_PATH = old_db_path
        db._make_engine()
    if old_db is not None:
        os.environ["DB_PATH"] = old_db
    else:
        os.environ.pop("DB_PATH", None)
    if old_rag is not None:
        os.environ["RAG_DIR"] = old_rag
    else:
        os.environ.pop("RAG_DIR", None)
    shutil.rmtree(tmp, ignore_errors=True)


def test_t12_switch_default_and_toggle(isolated_env):
    """验证 memory_extract_enabled 缺省为 True，且可由 settings 动态回关。"""
    assert settings_service.SETTINGS_DEFAULTS["memory_extract_enabled"] is True
    # 缺省状态下返回 True
    assert api_receipts._memory_extract_enabled() is True

    # 动态设置为 False（模拟管理台 PUT /api/admin/settings 关闭）
    db.set_app_setting("memory_extract_enabled", "false")
    assert api_receipts._memory_extract_enabled() is False

    # 重新恢复开启
    db.set_app_setting("memory_extract_enabled", "true")
    assert api_receipts._memory_extract_enabled() is True


def test_t12_quality_gate_alias():
    """验证质量闸对 alias 边缘与正常映射的判定。"""
    # 1. 归一后完全相同 -> LOW
    f_same = MemoryFact(
        kind=memory_facts.KIND_ALIAS,
        fact_key="alias:大天鵝鴨五花肉",
        value="大天鵝鴨五花肉",
        supplier_id="sup_1",
        tenant_id="default"
    )
    ok, reason = api_receipts._check_memory_fact_quality(f_same)
    assert not ok
    assert "alias:归一后相同" in reason

    # 2. 尾部微小残差 <= 16 -> LOW
    f_tail = MemoryFact(
        kind=memory_facts.KIND_ALIAS,
        fact_key="alias:大天鵝鴨五花肉 (G80g*24)",
        value="大天鵝鴨五花肉 (G80g*24) x2",
        supplier_id="sup_1",
        tenant_id="default"
    )
    ok, reason = api_receipts._check_memory_fact_quality(f_tail)
    assert not ok
    assert "alias:归一后仅首/尾续接" in reason

    # 3. 头部微小前缀 <= 16 -> LOW
    f_head = MemoryFact(
        kind=memory_facts.KIND_ALIAS,
        fact_key="alias:好味豬油也 (420g X 12)",
        value="老泥：好味豬油也 (420g X 12)",
        supplier_id="sup_1",
        tenant_id="default"
    )
    ok, reason = api_receipts._check_memory_fact_quality(f_head)
    assert not ok
    assert "alias:归一后仅首/尾续接" in reason

    # 4. 词干中部异体（繁简异体或真实映射）-> OK
    f_stem = MemoryFact(
        kind=memory_facts.KIND_ALIAS,
        fact_key="alias:有機菜心",
        value="有机菜心",
        supplier_id="sup_1",
        tenant_id="default"
    )
    ok, reason = api_receipts._check_memory_fact_quality(f_stem)
    assert ok


def test_t12_quality_gate_price_range():
    """验证质量闸对 price_range 伪区间与正常区间的判定。"""
    # 1. min == max 单点 -> LOW
    f_point = MemoryFact(
        kind=memory_facts.KIND_PRICE_RANGE,
        fact_key="急凍牛肋條",
        value="40.00-40.00",
        supplier_id="sup_1",
        tenant_id="default"
    )
    ok, reason = api_receipts._check_memory_fact_quality(f_point)
    assert not ok
    assert "min==max" in reason

    # 2. 区间颠倒 -> LOW
    f_rev = MemoryFact(
        kind=memory_facts.KIND_PRICE_RANGE,
        fact_key="急凍牛肋條",
        value="50.00-40.00",
        supplier_id="sup_1",
        tenant_id="default"
    )
    ok, reason = api_receipts._check_memory_fact_quality(f_rev)
    assert not ok
    assert "小于下界" in reason

    # 3. 宽度比 < 0.05 极窄区间 -> LOW
    f_narrow = MemoryFact(
        kind=memory_facts.KIND_PRICE_RANGE,
        fact_key="急凍牛肋條",
        value="40.00-40.50",
        supplier_id="sup_1",
        tenant_id="default"
    )
    ok, reason = api_receipts._check_memory_fact_quality(f_narrow)
    assert not ok
    assert "区间宽度/中位价" in reason

    # 4. 正常价格区间 -> OK
    f_normal = MemoryFact(
        kind=memory_facts.KIND_PRICE_RANGE,
        fact_key="急凍牛肋條",
        value="40.00-48.00",
        supplier_id="sup_1",
        tenant_id="default"
    )
    ok, reason = api_receipts._check_memory_fact_quality(f_normal)
    assert ok


def test_t12_quality_gate_unit(isolated_env):
    """验证质量闸对 unit 无差异习惯的判定。"""
    # 创建基准 SKU: 鸡蛋 base_unit=箱
    db.create_sku("鲜鸡蛋", category="蛋类", base_unit="箱", tenant_id="default")

    # 1. 候选单位与 base_unit 相同 -> LOW
    f_same_unit = MemoryFact(
        kind=memory_facts.KIND_UNIT,
        fact_key="unit:鲜鸡蛋",
        value="箱",
        supplier_id="sup_1",
        tenant_id="default"
    )
    ok, reason = api_receipts._check_memory_fact_quality(f_same_unit, tenant_id="default")
    assert not ok
    assert "与 SKU 基准单位「箱」相同" in reason

    # 2. 候选单位与 base_unit 不同（供应商特有习惯：按「托」计价）-> OK
    f_diff_unit = MemoryFact(
        kind=memory_facts.KIND_UNIT,
        fact_key="unit:鲜鸡蛋",
        value="托",
        supplier_id="sup_1",
        tenant_id="default"
    )
    ok, reason = api_receipts._check_memory_fact_quality(f_diff_unit, tenant_id="default")
    assert ok


def test_t12_extract_enqueues_pending_and_blocks_low(isolated_env):
    """验证抽取流程将有效候选入 pending_memory，并阻断 LOW 事实入待审队列。"""
    # 模拟供应商与 SKU
    sup_id, _ = db.create_supplier("測試供應商A", tenant_id="default")
    db.create_sku("测试商品X", base_unit="件", tenant_id="default")

    # 创建收据
    rid = db.create_receipt_record(
        file_path="/tmp/fake.jpg",
        supplier_name="測試供應商A",
        supplier_id=sup_id,
        date="2026-10-22",
        total_amount=100.0,
        currency="HKD",
        status="parsed",
        tenant_id="default"
    )
    row = db.get_receipt_row(rid, tenant_id="default")

    ai_items = [{"name": "测试商品X", "unit": "件", "unit_price": 50.0, "qty": 2.0}]
    user_items = [{"name": "测试商品X", "unit": "箱", "unit_price": 55.0, "quantity": 2.0}]

    # 构造两条候选：一条 OK（unit: 箱 vs 基准 件），一条 LOW（price_range: 55.00-55.00）
    ok_fact = MemoryFact(
        kind=memory_facts.KIND_UNIT,
        fact_key="unit:测试商品X",
        value="箱",
        supplier_id=str(sup_id),
        tenant_id="default",
        source_kind=memory_facts.SOURCE_EXTRACT
    )
    low_fact = MemoryFact(
        kind=memory_facts.KIND_PRICE_RANGE,
        fact_key="测试商品X",
        value="55.00-55.00",
        supplier_id=str(sup_id),
        tenant_id="default",
        source_kind=memory_facts.SOURCE_EXTRACT
    )

    with patch("app.services.memory_extract.compute_field_diffs", return_value=[{"field": "unit"}]), \
         patch("app.services.memory_extract.extract_candidate_facts", return_value=[ok_fact, low_fact]):
        stats = api_receipts._extract_and_enqueue_memory_facts(
            rid, row, "default", ai_items, user_items, trigger="save_edited"
        )

    assert stats is not None
    assert stats["candidates"] == 2
    assert stats["rejected"] == 1  # low_fact 被质量闸拦截
    assert stats["enqueued"] == 1  # ok_fact 成功入队

    # 查待审队列：只有 ok_fact 处于 pending
    pending = db.list_pending_memory(status="pending", tenant_id="default")
    assert len(pending) == 1
    assert pending[0]["fact_kind"] == "unit"
    assert pending[0]["fact_value"] == "箱"
    assert pending[0]["source_kind"] == "extract"  # 配套④：来源诚实性

    # 查被拒记录：low_fact 作为 status='rejected' 落库审计
    rejected = db.list_pending_memory(status="rejected", tenant_id="default")
    assert len(rejected) >= 1
    r_item = [r for r in rejected if r.get("fact_kind") == "price_range"][0]
    assert r_item["reject_reason"] == "quality_gate: low_information"
    assert r_item["source_kind"] == "extract"

    # 查 LLM 上下文是否正确记录了 leg="memory_extract"
    ctx = get_llm_context()
    assert ctx["leg"] == "memory_extract"
    assert ctx["receipt_id"] == rid


def test_t12_switch_off_bypasses_extraction(isolated_env):
    """验证管理台关闭 memory_extract_enabled 时，保存不触发抽取。"""
    sup_id, _ = db.create_supplier("測試供應商B", tenant_id="default")
    rid = db.create_receipt_record(
        file_path="/tmp/fake2.jpg",
        supplier_name="測試供應商B",
        supplier_id=sup_id,
        date="2026-10-22",
        total_amount=50.0,
        currency="HKD",
        status="parsed",
        tenant_id="default"
    )
    row = db.get_receipt_row(rid, tenant_id="default")
    db.set_app_setting("memory_extract_enabled", "false")

    ai_items = [{"name": "A", "unit_price": 10.0}]
    user_items = [{"name": "A", "unit_price": 20.0}]

    with patch("app.services.memory_extract.extract_candidate_facts") as mock_extract:
        res = api_receipts._extract_and_enqueue_memory_facts(
            rid, row, "default", ai_items, user_items, trigger="save_edited"
        )
        assert res is None
        mock_extract.assert_not_called()


def test_t12_mutation_counterproof_quality_gate(isolated_env):
    """突变体对抗：若质量闸失效并让 LOW 事实进入 active 记忆，quality_report 必须翻红退出码 1。"""
    # 正常运行 quality report，应退出 0
    cmd_base = [
        sys.executable,
        os.path.join(_DEMO_DIR, "scripts", "memory_fact_quality_report.py"),
        "--db-path", isolated_env["db_file"],
        "--fail-on-low"
    ]
    res_clean = subprocess.run(cmd_base, capture_output=True, text=True)
    assert res_clean.returncode == 0, f"Clean DB should pass: {res_clean.stdout}"

    # 突变：直接在 vendor_memory 插入一条单点伪区间 40.00-40.00
    conn = sqlite3.connect(isolated_env["db_file"])
    conn.execute(
        "INSERT INTO vendor_memory (memory_id, vendor, fact_kind, fact_key, fact_value, notes, status, tenant_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("mutant_1", "測試供應商M", "price_range", "急凍牛肋條", "40.00-40.00", "急凍牛肋條 = 40.00-40.00", "active", "default")
    )
    conn.commit()
    conn.close()

    # 再次运行 quality report，必须退出码 1
    res_mutant = subprocess.run(cmd_base, capture_output=True, text=True)
    assert res_mutant.returncode == 1, (
        f"Mutant DB with LOW fact must exit 1, but got {res_mutant.returncode}: {res_mutant.stdout}"
    )
    assert "LOW" in res_mutant.stdout
    assert "min==max" in res_mutant.stdout


def test_t12_replay_idempotent(isolated_env):
    """验证 memory_extract_replay 重放工具的幂等性。"""
    from scripts import memory_extract_replay

    sup_id, _ = db.create_supplier("重放供應商", tenant_id="default")
    db.create_sku("重放商品", base_unit="件", tenant_id="default")

    rid = db.create_receipt_record(
        file_path="/tmp/replay.jpg",
        supplier_name="重放供應商",
        supplier_id=sup_id,
        date="2026-10-22",
        total_amount=88.0,
        currency="HKD",
        status="parsed",
        tenant_id="default",
        ai_prefill_json='{"items": [{"name": "重放商品", "unit": "件", "unit_price": 44.0, "qty": 2.0}]}'
    )
    # 用户 items 单位改为 箱（与 AI 存在差异）
    db.set_receipt_items(rid, [{"name": "重放商品", "unit": "箱", "unit_price": 44.0, "quantity": 2.0}], tenant_id="default")

    valid_fact = MemoryFact(
        kind=memory_facts.KIND_UNIT,
        fact_key="unit:重放商品",
        value="箱",
        supplier_id=str(sup_id),
        tenant_id="default",
        source_kind=memory_facts.SOURCE_EXTRACT
    )

    with patch("app.services.memory_extract.compute_field_diffs", return_value=[{"field": "unit"}]), \
         patch("app.services.memory_extract.extract_candidate_facts", return_value=[valid_fact]):
        res1 = memory_extract_replay.replay_receipt(rid, "default", force_switch=True)
        pending1 = db.list_pending_memory(status="pending", tenant_id="default")
        assert len(pending1) == 1

        # 第二次重放
        res2 = memory_extract_replay.replay_receipt(rid, "default", force_switch=True)
        pending2 = db.list_pending_memory(status="pending", tenant_id="default")
        # 幂等性断言：待审队列不重复增加行
        assert len(pending2) == 1

        # 运行记录正常增加
        runs = db.list_memory_extract_runs(receipt_id=rid, tenant_id="default")
        assert len(runs) == 2

