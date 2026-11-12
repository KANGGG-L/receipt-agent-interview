# -*- coding: utf-8 -*-
"""P1 Wave 1 治理基建测试套件 (T6 扣分粒度收窄 + T8 主体名净化 + T7 写入留痕)。

红线约束：
1. 全局严禁使用 Emoji。统一采用 [OK], [FAIL], [INFO], [WARN]。
2. 严禁改动 live 数据库与 live 向量库，环境完全基于 /tmp 隔离沙箱。
3. 严格断言返回口径，杜绝 500 异常与越权泄露。
"""

import os
import sys
import tempfile
import uuid
import json
import logging
import pytest
from pydantic import ValidationError

_TMP = tempfile.mkdtemp(prefix="test_p1_w1_infra_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_p1_w1.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "test_rag")
os.environ["AUTH_ENABLED"] = "0"

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db
import app.services.rag as rag
import app.services.memory_store as memory_store
from app.api_receipts import SaveEditedBody, _extract_injected_memory_ids
from app.api_suppliers import SupplierBody, MergeBody
from fastapi.testclient import TestClient
from app.main import app as fastapi_app

rag.PERSIST_DIR = os.environ["RAG_DIR"]
client = TestClient(fastapi_app)

ADMIN_HEADERS = {"X-Role": "admin", "X-Tenant-Id": "default"}
STAFF_HEADERS = {"X-Role": "staff", "X-Tenant-Id": "default"}


def test_t6_targeted_decay_penalty_reduces_only_specified_ids():
    """T6 (G3, D3已决): 扣分粒度收窄至本次注入 mid 集合，同商未命中规则 decay_score 保持不变。"""
    vendor = "Vendor_Targeted_Test"
    tenant = "default"

    # 为同一商户创建规则 A 与规则 B
    mid_a, _ = db.upsert_vendor_memory(
        vendor=vendor, notes="规则A", sample="样本A", tenant_id=tenant, fact_key="alias:菜心"
    )
    mid_b, _ = db.upsert_vendor_memory(
        vendor=vendor, notes="规则B", sample="样本B", tenant_id=tenant, fact_key="price_range:牛肉"
    )

    # 初始 decay_score 均应为 0.7
    mems = db.list_vendor_memory(vendor=vendor, tenant_id=tenant, include_archived=False)
    mem_map = {m["memory_id"]: m["decay_score"] for m in mems}
    assert mem_map[mid_a] == 0.7
    assert mem_map[mid_b] == 0.7

    # 仅针对 mid_a 扣分
    affected = db.apply_vendor_memory_override_penalty(vendor, tenant_id=tenant, memory_ids=[mid_a])
    assert affected == 1

    # 验证 mid_a 扣分至 0.58，mid_b 严格保持 0.7
    mems_after = db.list_vendor_memory(vendor=vendor, tenant_id=tenant, include_archived=False)
    mem_map_after = {m["memory_id"]: m["decay_score"] for m in mems_after}
    assert round(mem_map_after[mid_a], 2) == 0.58
    assert round(mem_map_after[mid_b], 2) == 0.70


def test_t6_fallback_decay_penalty_when_no_memory_ids_provided(caplog):
    """T6 降级分支：未传入 memory_ids 时降级为整商 active 规则扣分，并记录 warning。"""
    vendor = "Vendor_Fallback_Test"
    tenant = "default"

    mid_a, _ = db.upsert_vendor_memory(
        vendor=vendor, notes="规则1", sample="样本1", tenant_id=tenant, fact_key="alias:芥兰"
    )
    mid_b, _ = db.upsert_vendor_memory(
        vendor=vendor, notes="规则2", sample="样本2", tenant_id=tenant, fact_key="alias:通菜"
    )

    with caplog.at_level(logging.WARNING):
        affected = db.apply_vendor_memory_override_penalty(vendor, tenant_id=tenant, memory_ids=None)

    assert affected == 2
    assert "[WARN] 记忆衰减扣分未提供 memory_ids 锚点，降级为整商 active 规则扣分" in caplog.text

    mems = db.list_vendor_memory(vendor=vendor, tenant_id=tenant, include_archived=False)
    mem_map = {m["memory_id"]: m["decay_score"] for m in mems}
    assert round(mem_map[mid_a], 2) == 0.58
    assert round(mem_map[mid_b], 2) == 0.58


def test_t6_extract_injected_memory_ids_protocols():
    """T6 单据 rag_context_json 提取算法验证 (JSON 协议、正则嵌入锚点、文本事实反查)。"""
    vendor = "Vendor_Extract_Test"
    tenant = "default"

    # 1. JSON 格式提取
    json_ctx = json.dumps({"injected_memory_ids": ["mid_json_1", "mid_json_2"]})
    res_json = _extract_injected_memory_ids(json_ctx, vendor, tenant)
    assert res_json == ["mid_json_1", "mid_json_2"]

    # 2. 正则嵌入提取
    raw_ctx = "识别提示 [prior:memory] <!-- mid:mid_regex_999 --> 别名：菜心"
    res_raw = _extract_injected_memory_ids(raw_ctx, vendor, tenant)
    assert res_raw == ["mid_regex_999"]

    # 3. 文本反查提取
    mid_c, _ = db.upsert_vendor_memory(
        vendor=vendor, notes="有机番茄", sample="", tenant_id=tenant, fact_key="alias:番茄"
    )
    text_ctx = "[prior:memory] 别名：番茄"
    res_text = _extract_injected_memory_ids(text_ctx, vendor, tenant)
    assert res_text is not None and mid_c in res_text

    # 4. 空文本返回 None
    assert _extract_injected_memory_ids("", vendor, tenant) is None
    assert _extract_injected_memory_ids(None, vendor, tenant) is None


def test_t8_save_edited_body_rejects_oversized_and_control_chars():
    """T8 (G6) SaveEditedBody 校验：超长（>200字符）与非法控制字符统一抛出可读验证错误。"""
    # 合法名称正常构造
    valid = SaveEditedBody(supplier_name="正常供应商有限责任公司 (A店)")
    assert valid.supplier_name == "正常供应商有限责任公司 (A店)"

    # 5,000,000 字符超长输入必须被拒绝
    oversized = "A" * 5000000
    with pytest.raises(ValidationError) as exc_info:
        SaveEditedBody(supplier_name=oversized)
    err_str = str(exc_info.value)
    assert "供应商名称不能超过200个字符" in err_str

    # 控制字符 (\x00, \x08 等) 必须被拒绝
    dirty = "非法\x00供应商\x08名字"
    with pytest.raises(ValidationError) as exc_info2:
        SaveEditedBody(supplier_name=dirty)
    assert "非法控制字符" in str(exc_info2.value)


def test_t8_supplier_body_and_merge_body_rejects_oversized_and_control_chars():
    """T8 SupplierBody 与 MergeBody 校验：超长与脏字符严格拦截。"""
    with pytest.raises(ValidationError) as exc1:
        SupplierBody(name="B" * 201)
    assert "供应商名称不能超过200个字符" in str(exc1.value)

    with pytest.raises(ValidationError) as exc2:
        SupplierBody(name="供应商\x1b名称")
    assert "非法控制字符" in str(exc2.value)

    with pytest.raises(ValidationError) as exc3:
        MergeBody(keep_id=1, drop_id=2, primary_name="C" * 250)
    assert "供应商名称不能超过200个字符" in str(exc3.value)

    with pytest.raises(ValidationError) as exc4:
        MergeBody(keep_id=1, drop_id=2, alias_name="别名\x7f")
    assert "非法控制字符" in str(exc4.value)


def test_t8_db_writing_side_defensive_truncation(caplog):
    """T8 写入侧防御性兜底：入库底层对超长主体名截断至 200 字符并记录 warning。"""
    long_name = "超长供应商主体名称" * 30  # 超过 200 字符
    assert len(long_name) > 200

    # 1. db.upsert_vendor_memory 截断
    with caplog.at_level(logging.WARNING):
        mid, _ = db.upsert_vendor_memory(vendor=long_name, notes="截断测试", sample="", tenant_id="default")
    assert "[WARN] 供应商名称超长截断" in caplog.text

    mems = db.list_vendor_memory(vendor=long_name[:200], tenant_id="default")
    assert any(m["memory_id"] == mid for m in mems)
    # 验证库中记录的 vendor 长度严格 <= 200
    row = next(m for m in mems if m["memory_id"] == mid)
    assert len(row["vendor"]) <= 200

    # 2. db.enqueue_pending_memory 截断
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        pid, _ = db.enqueue_pending_memory(vendor=long_name, content="候选内容", tenant_id="default")
    assert "[WARN] 供应商名称超长截断" in caplog.text
    prow = db.get_pending_memory(pid, tenant_id="default")
    assert len(prow["vendor"]) <= 200


def test_t7_write_failure_counter_and_dashboard_metrics():
    """T7 (G8) 写入侧留痕：进程级故障计数器打点与 GET /api/admin/memory-metrics 接通。"""
    counts_initial = memory_store.get_write_failure_counts()
    for stage in memory_store.ALL_WRITE_STAGES:
        assert stage in counts_initial
        assert isinstance(counts_initial[stage], int)

    # 模拟写入异常打点
    test_exc = RuntimeError("模拟写入层网络异常")
    memory_store._note_write_failure(memory_store.WRITE_STAGE_INGEST, test_exc, vendor="TestVendor")
    memory_store._note_write_failure(memory_store.WRITE_STAGE_VECTOR_SYNC, test_exc, vendor="TestVendor")

    counts_after = memory_store.get_write_failure_counts()
    assert counts_after[memory_store.WRITE_STAGE_INGEST] == counts_initial[memory_store.WRITE_STAGE_INGEST] + 1
    assert counts_after[memory_store.WRITE_STAGE_VECTOR_SYNC] == counts_initial[memory_store.WRITE_STAGE_VECTOR_SYNC] + 1

    # 请求大盘端点 GET /api/admin/memory-metrics
    resp = client.get("/api/admin/memory-metrics", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "write_failures" in data
    wf = data["write_failures"]
    assert wf.get("process_scoped") is True
    assert "counts" in wf
    assert wf["counts"][memory_store.WRITE_STAGE_INGEST] >= 1
    assert wf["counts"][memory_store.WRITE_STAGE_VECTOR_SYNC] >= 1
    assert "进程级计数器" in wf.get("note", "")
