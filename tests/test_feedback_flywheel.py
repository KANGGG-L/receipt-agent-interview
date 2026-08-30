# -*- coding: utf-8 -*-
"""
F-P0-1 反馈飞轮专项测试：三次连续点踩 -> 待确认记忆队列（T8 人工闸语义）。

覆盖：
1. 阈值常量 FEEDBACK_DISTILL_THRESHOLD=3（models.py，禁止散落硬编码）
2. 同供应商同租户连续 3 次点踩触发 should_distill_vendor_memory
3. 少于阈值 / 点赞打断 / 租户不同 均不触发
4. ingest_feedback_memory 进入 pending_memory 待确认队列（租户隔离），
   未 approve 不参与检索；Chroma 租户集合物理隔离结构不变
5. API POST /api/receipt/{id}/feedback 第 3 次点踩返回 distilled=True
   且落入 pending（未 approve 不注入识别）

T8 语义变更：旧口径「第 3 次点踩自动写入生效」已废弃——规则级记忆写入
必须经人工 approve（Gap D1），本文件按新口径断言。
"""

import json
import os
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="feedback_flywheel_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_feedback.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")

_DEMO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo")
sys.path.insert(0, _DEMO_DIR)

import importlib  # noqa: E402

import app.db as db  # noqa: E402
from app.models import FEEDBACK_DISTILL_THRESHOLD, ReceiptFeedback  # noqa: E402

# 若本进程此前已加载过 rag（如与其他测试合跑），把持久目录与缓存重置到隔离目录
import app.services.rag as rag  # noqa: E402

rag.PERSIST_DIR = os.environ["RAG_DIR"]
rag._vs = None
rag._vs_tenant = {}


def _fresh_db():
    """每个用例独立数据库文件，避免供应商反馈串扰。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"fb_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    return db


def _mk_receipt(_db, vendor):
    return _db.create_receipt(supplier_name=vendor, status="parsed")


def _dislike(_db, vendor, tenant_id="default", comment="纠偏"):
    rid = _mk_receipt(_db, vendor)
    return _db.upsert_receipt_feedback(
        receipt_id=rid, like=-1, comment=comment,
        item_index=None, tenant_id=tenant_id, vendor=vendor,
        quality_warnings=[],
    )


def _like(_db, vendor, tenant_id="default"):
    rid = _mk_receipt(_db, vendor)
    return _db.upsert_receipt_feedback(
        receipt_id=rid, like=1, comment="",
        item_index=None, tenant_id=tenant_id, vendor=vendor,
        quality_warnings=[],
    )


# -------------------------------------------------------------
# 1. 阈值常量抽取
# -------------------------------------------------------------
def test_threshold_constant_is_three():
    assert FEEDBACK_DISTILL_THRESHOLD == 3
    fb = ReceiptFeedback(receipt_id=1, like=-1, comment="x")
    assert fb.like == -1


# -------------------------------------------------------------
# 2. 连续 3 次点踩触发
# -------------------------------------------------------------
def test_three_consecutive_dislikes_trigger_distill():
    _db = _fresh_db()
    vendor = "祥興食品"
    for i in range(FEEDBACK_DISTILL_THRESHOLD - 1):
        _dislike(_db, vendor, comment=f"第{i}次纠偏")
        assert _db.should_distill_vendor_memory(vendor) is False, \
            f"未达阈值 {FEEDBACK_DISTILL_THRESHOLD} 不应触发"
    _dislike(_db, vendor, comment="第3次纠偏")
    assert _db.should_distill_vendor_memory(vendor) is True, \
        "连续 3 次点踩应触发沉淀判定"


# -------------------------------------------------------------
# 3. 点赞打断 / 租户隔离不触发
# -------------------------------------------------------------
def test_like_interrupts_streak():
    _db = _fresh_db()
    vendor = "打斷供應商"
    _dislike(_db, vendor)
    _dislike(_db, vendor)
    _like(_db, vendor)          # 打断连续性
    _dislike(_db, vendor)
    assert _db.should_distill_vendor_memory(vendor) is False, \
        "最近窗口内含点赞不应触发"


def test_tenant_isolation_no_cross_trigger():
    _db = _fresh_db()
    vendor = "跨租户供應商"
    for _ in range(FEEDBACK_DISTILL_THRESHOLD):
        _dislike(_db, vendor, tenant_id="tenant_A")
    assert _db.should_distill_vendor_memory(vendor, tenant_id="tenant_A") is True
    assert _db.should_distill_vendor_memory(vendor, tenant_id="tenant_B") is False, \
        "租户 B 无反馈不应被租户 A 的点踩触发"
    assert _db.should_distill_vendor_memory(vendor, tenant_id="default") is False


# -------------------------------------------------------------
# 4. 蒸馏进待确认队列（T8 人工闸）+ 租户隔离
# -------------------------------------------------------------
def test_distill_enqueues_pending_not_injected_until_approved():
    _db = _fresh_db()
    vendor = "沉澱測試商戶"
    for i in range(FEEDBACK_DISTILL_THRESHOLD):
        _dislike(_db, vendor, comment=f"單價錯第{i}次")

    assert _db.should_distill_vendor_memory(vendor) is True

    tenant_a, tenant_b = "flywheel_A", "flywheel_B"
    content = rag.ingest_feedback_memory(
        vendor, "連續三次單價糾偏：金額錯", ["模糊"], tenant_a)

    assert "單價" in content or "糾偏" in content

    # T8：蒸馏产物进入 pending_memory 待确认队列，而非直接生效
    pend_a = _db.list_pending_memory(tenant_id=tenant_a)
    assert len(pend_a) == 1, "三次点踩应产生且仅产生一条待确认记忆"
    assert pend_a[0]["status"] == "pending"
    assert pend_a[0]["vendor"] == vendor
    assert pend_a[0]["source_kind"] == "feedback_distilled"

    # 未 approve 不参与检索（人工闸核心断言）
    assert rag.retrieve_context(vendor, tenant_id=tenant_a).strip() == "", \
        "未 approve 的待确认记忆不得注入识别上下文"

    # 租户隔离：租户 B 既无待确认记录，也不得召回租户 A 的内容
    assert _db.list_pending_memory(tenant_id=tenant_b) == []

    # collection 物理隔离：两个租户是不同 Chroma 实例、不同 collection 名
    store_a = rag._store(tenant_a)
    store_b = rag._store(tenant_b)
    assert store_a is not store_b
    assert rag._tenant_collection_name(tenant_a) != rag._tenant_collection_name(tenant_b)
    assert rag._tenant_collection_name(tenant_a).startswith("tenant_flywheel_A_")
    assert rag._tenant_collection_name(tenant_b).startswith("tenant_flywheel_B_")


# -------------------------------------------------------------
# 5. API 层：第 3 次点踩 distilled=True
# -------------------------------------------------------------
def test_api_third_dislike_returns_distilled():
    _db = _fresh_db()

    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    client = TestClient(fastapi_app)
    vendor = "API沉澱商戶"
    headers = {"X-Role": "staff"}

    rids = []
    for _ in range(FEEDBACK_DISTILL_THRESHOLD):
        rid = _db.create_receipt(supplier_name=vendor, status="parsed")
        _db.set_receipt_items(rid, [{
            "name": "菜心", "raw_name": "菜心", "quantity": 2, "unit": "斤",
            "raw_unit": "斤", "unit_price": 10, "amount": 20,
            "sku_id": None, "cost_center_id": None, "confidence": 0.9,
            "matched": 0, "price_anomaly": 0, "price_anomaly_direction": "",
            "price_diff_percent": 0.0, "unit_conversion_warning": "",
            "fuzzy_candidates": [], "entity_candidates": [],
        }])
        rids.append(rid)

    distilled_flags = []
    last_logs = []
    for i, rid in enumerate(rids):
        resp = client.post(
            f"/api/receipt/{rid}/feedback",
            json={"like": -1, "comment": f"金額錯第{i}次", "item_index": 0},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "success"
        assert body["feedback"]["like"] == -1
        distilled_flags.append(body.get("distilled") is True)
        last_logs = json.loads(_db.get_receipt_row(rid).audit_logs_json or "[]")

    assert distilled_flags == [False, False, True], \
        f"仅第 {FEEDBACK_DISTILL_THRESHOLD} 次连续点踩应 distilled=True: {distilled_flags}"

    # T8：distilled=True 的语义是「进入待确认队列」，而非直接生效
    pend = _db.list_pending_memory(tenant_id="default")
    assert len(pend) == 1, "第 3 次点踩应产生一条待确认记忆"
    assert pend[0]["status"] == "pending"
    assert pend[0]["vendor"] == vendor
    # 未 approve 不注入检索（人工闸）
    import app.services.rag as _rag
    assert _rag.retrieve_context(vendor, tenant_id="default").strip() == ""

    # 审计履历包含 feedback_distilled
    assert any(l.get("action") == "feedback_distilled" for l in last_logs), \
        "第 3 次点踩应写入 feedback_distilled 审计"

    # comment 超长被 400 拦截（前端 maxlength=2000 的后端兜底）
    rid_x = _db.create_receipt(supplier_name=vendor, status="parsed")
    resp_long = client.post(
        f"/api/receipt/{rid_x}/feedback",
        json={"like": 1, "comment": "a" * 2001},
        headers=headers,
    )
    assert resp_long.status_code == 400


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
