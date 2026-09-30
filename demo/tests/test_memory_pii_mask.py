# -*- coding: utf-8 -*-
"""W-9（P1-4）记忆写入侧 PII 最小化：`memory_pii_mask_enabled` 开关 + 写入路径脱敏。

缺陷：`vendor_memory` 的 `notes` / `sample` 直接落 OCR 明细文本（可能含员工姓名 /
电话 / 证件号等个人数据），且被长期保存、反复注入 prompt —— 香港 PDPO / GDPR 要求
最小化，行业两家官方均明确「不能只依赖模型过滤」。

修复：写入路径（`rag.ingest_memory` 的 notes/sample、`rag.approve_pending_to_memory`
的自由文本分支）在**落库前 + 写向量前**对记忆语料做确定性脱敏，复用
`ai_registry.tools.pii_masker.v1_0_0`（不另写正则实现）。开关 `memory_pii_mask_enabled`
缺省 False = 逐字节零行为变化。

判别式断言（反证记录见交付报告，脚本见
~/harness-tmp/receipt-vendormemory-takeover/w9_mutation_counterproof.py）：
  a) 开关**关**：写入内容与向量文本与原始入参**逐字节相同**；
  b) 开关**开**：含电话 / 证件号的明细 → 落库 / 向量里**不再出现原文**、出现掩码；
  c) 非 PII 文本不被误伤（逐字节不变）；
  d) 单据原始数据（receipt_items）不受记忆脱敏影响。
去掉 `_mask_memory_text` 调用（突变体）→ (b) 必翻红。

全部离线假数据：独立临时 DB + 独立临时 RAG 目录；向量走缺省嵌入档（本地 char-ngram，
零网络）。
"""

import importlib
import os
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="memory_pii_mask_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_pii.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_REPO_ROOT = os.path.dirname(_DEMO_DIR)
for _p in (_DEMO_DIR, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import app.db as db  # noqa: E402
import app.services.rag as rag  # noqa: E402
from app.services import settings_service  # noqa: E402

# PII 样例：三类均被 ai_registry.tools.pii_masker.v1_0_0 覆盖（HKID / 私人手机 / 银行卡）
HKID = "A123456(7)"
PHONE = "6123 4567"
BANK = "1234 5678 9012"
PII_TEXT = f"送貨員 {PHONE} 身份證 {HKID} 銀行 {BANK}"
# 非 PII：真实明细行 + 版式线索（脱敏不得误伤）
NON_PII_ITEMS = "1. 菜心 2斤 @10 = 20"
NON_PII_NOTES = "版式：手寫長單"


def _fresh_db():
    """每个用例独立 DB + 独立向量目录，避免跨用例串扰。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"pii_{uuid.uuid4().hex[:8]}.db")
    os.environ["RAG_DIR"] = os.path.join(_TMP, f"rag_{uuid.uuid4().hex[:8]}")
    importlib.reload(db)
    rag.PERSIST_DIR = os.environ["RAG_DIR"]
    rag._vs = None
    rag._vs_tenant = {}
    return db


def _set_mask(enabled):
    db.set_app_setting(rag.MEMORY_PII_MASK_KEY, "true" if enabled else "false")


def _row(vendor, tenant="default"):
    rows = db.list_vendor_memory(vendor, tenant_id=tenant)
    assert rows, f"前置失败：{vendor!r} 的记忆未落库"
    return rows[0]


def _docs(tenant, mid):
    """该 memory_id 在向量库里的 (documents, metadatas)。"""
    got = rag._store(tenant)._collection.get(where={"memory_id": mid})
    return (got.get("documents") or []), (got.get("metadatas") or [])


# -------------------------------------------------------------
# a) 开关关：逐字节零行为变化
# -------------------------------------------------------------
def test_switch_off_is_byte_identical():
    _db = _fresh_db()
    _set_mask(False)
    vendor = "關閉商戶"
    rag.ingest_memory(vendor, PII_TEXT, notes=PII_TEXT, tenant_id="default",
                      source_kind="manual")
    row = _row(vendor)
    # 落库内容与入参逐字节相同（PII 原样保留 = 关闭态即接线前行为）
    assert row["notes"] == PII_TEXT, row["notes"]
    assert row["sample"] == PII_TEXT, row["sample"]

    docs, metas = _docs("default", row["memory_id"])
    notes_docs = [d for d, m in zip(docs, metas) if (m or {}).get("kind") == "notes"]
    sample_docs = [d for d, m in zip(docs, metas) if (m or {}).get("kind") == "sample"]
    assert notes_docs == [PII_TEXT], notes_docs
    assert sample_docs == [f"供应商 {vendor} 的已确认进货明细：\n{PII_TEXT}"], sample_docs


def test_default_is_disabled_and_does_not_alter_write():
    """未显式设置时开关缺省 False（注册在 SETTINGS_DEFAULTS），写入不被改动。"""
    _db = _fresh_db()
    assert settings_service.get_bool(rag.MEMORY_PII_MASK_KEY, False) is False
    vendor = "預設商戶"
    rag.ingest_memory(vendor, PII_TEXT, notes=PII_TEXT, tenant_id="default",
                      source_kind="manual")
    row = _row(vendor)
    assert row["notes"] == PII_TEXT and row["sample"] == PII_TEXT


# -------------------------------------------------------------
# b) 开关开：原文消失、掩码出现（落库 + 向量两侧）
# -------------------------------------------------------------
def test_switch_on_masks_pii_in_db_and_vector():
    _db = _fresh_db()
    _set_mask(True)
    vendor = "開啟商戶"
    rag.ingest_memory(vendor, PII_TEXT, notes=PII_TEXT, tenant_id="default",
                      source_kind="manual")
    row = _row(vendor)
    for raw in (PHONE, HKID, BANK):
        assert raw not in (row["notes"] or ""), f"落库 notes 仍含原文 {raw!r}"
        assert raw not in (row["sample"] or ""), f"落库 sample 仍含原文 {raw!r}"
    assert "****" in row["notes"], row["notes"]
    assert "HKID_MASKED" in row["notes"], row["notes"]

    docs, _metas = _docs("default", row["memory_id"])
    joined = "\n".join(docs)
    for raw in (PHONE, HKID, BANK):
        assert raw not in joined, f"向量文本仍含原文 {raw!r}"
    assert "HKID_MASKED" in joined and "****" in joined, joined


def test_approve_free_text_branch_masks_pii():
    """`approve_pending_to_memory` 的自由文本分支同样在落库/写向量前脱敏。"""
    _db = _fresh_db()
    _set_mask(True)
    vendor = "審批商戶"
    rag.approve_pending_to_memory(vendor, PII_TEXT, tenant_id="default",
                                  source_kind="feedback_distilled")
    row = _row(vendor)
    for raw in (PHONE, HKID, BANK):
        assert raw not in (row["notes"] or ""), f"approve 落库仍含原文 {raw!r}"
    docs, _metas = _docs("default", row["memory_id"])
    assert PHONE not in "\n".join(docs)
    assert HKID not in "\n".join(docs)


# -------------------------------------------------------------
# c) 非 PII 文本不被误伤
# -------------------------------------------------------------
def test_non_pii_text_not_touched_when_on():
    _db = _fresh_db()
    _set_mask(True)
    vendor = "非PII商戶"
    rag.ingest_memory(vendor, NON_PII_ITEMS, notes=NON_PII_NOTES, tenant_id="default",
                      source_kind="manual")
    row = _row(vendor)
    assert row["notes"] == NON_PII_NOTES, row["notes"]
    assert row["sample"] == NON_PII_ITEMS, row["sample"]


# -------------------------------------------------------------
# d) 单据原始数据不受记忆脱敏影响
# -------------------------------------------------------------
def test_receipt_items_untouched_by_memory_mask():
    _db = _fresh_db()
    _set_mask(True)
    vendor = "單據商戶"
    rid = _db.create_receipt(supplier_name=vendor, tenant_id="default")
    # 明细里刻意含同样的 PII 形态：脱敏只作用于记忆语料，不得改动原始凭证
    _db.set_receipt_items(rid, [
        {"name": f"送貨 {PHONE}", "quantity": 1.0, "unit": "斤",
         "unit_price": 1.0, "amount": 1.0},
    ])
    rag.ingest_memory(vendor, PII_TEXT, notes=PII_TEXT, tenant_id="default",
                      source_kind="manual")

    items = _db.get_receipt_items(rid, tenant_id="default")
    assert items, "前置失败：单据明细未写入"
    assert PHONE in items[0]["name"], (
        "单据原始明细被记忆脱敏改动了（receipt_items 是原始凭证，另有审计语义）")


if __name__ == "__main__":  # pragma: no cover
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
