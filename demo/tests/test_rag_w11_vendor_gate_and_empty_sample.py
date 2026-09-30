# -*- coding: utf-8 -*-
"""W-11 收口用例：L1 短名子串门（W11-1）+ M9 空明细 sample 向量（W11-2）。

W11-1（L1·安全）：`rag._vendor_related` 归一后子串/别名放行此前无最小长度约束，1 字
供应商名可与大批他商名互为子串、或落进别名组而放行 —— 跨供应商记忆泄漏入口。本用例把
「单字/空串不得放行」固化成断言，同时钉住「正常别名/后缀匹配仍放行」，防止修复把合法
匹配一并判死（如「德利行」vs「德利行 Tak Lee Hong」）。

W11-2（M9·正确性）：`ingest_memory` 此前无条件构造 sample 向量；items_text 为空时前缀
`供应商 X 的已确认进货明细：` 仍使 page_content 非空（读侧 `if not d.page_content.strip()`
拦不住），向量腿会把这句无信息文本当候选注入 prompt（噪声先验）。本用例断言空明细不产生
sample 向量、非空明细仍产生，且 notes 文档与 DB 行不受影响。

反向证明（突变体）：把 W11-1 的最小长度门去掉 → `test_w11_1_single_char_and_empty_never_pass_gate`
翻红；把 W11-2 的 sample 构造改回无条件 → `test_w11_2_empty_items_writes_no_sample_vector`
翻红。脚本见 ~/harness-tmp/receipt-vendormemory-takeover/w11_mutation_counterproof.py。

全部离线假数据：独立临时 DB + 独立临时 RAG 目录；向量走缺省嵌入档（本地 char-ngram，
零网络）。
"""

import importlib
import os
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="rag_w11_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_w11.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402
import app.services.rag as rag  # noqa: E402


def _fresh_db():
    """每个用例独立数据库文件 + 独立向量目录，避免跨用例串扰。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"w11_{uuid.uuid4().hex[:8]}.db")
    os.environ["RAG_DIR"] = os.path.join(_TMP, f"rag_{uuid.uuid4().hex[:8]}")
    importlib.reload(db)
    rag.PERSIST_DIR = os.environ["RAG_DIR"]
    rag._vs = None
    rag._vs_tenant = {}
    return db


def _docs_of(tenant_id, mid):
    return rag._store(tenant_id)._collection.get(where={"memory_id": mid})


def _kinds(tenant_id, mid):
    got = _docs_of(tenant_id, mid)
    return [(m or {}).get("kind") for m in (got.get("metadatas") or [])]


# -------------------------------------------------------------
# W11-1：单字/空串不得放行
# -------------------------------------------------------------
def test_w11_1_single_char_and_empty_never_pass_gate():
    # 实测泄漏样本（gap 文档 §4.3 L1）：单字查询与真实供应商名互为子串
    for q, d in (("記", "聯記號"), ("行", "德利行 Tak Lee Hong"),
                 ("菜", "菜心供應商"), ("興", "祥興快餐用品")):
        assert rag._vendor_related(q, d) is False, (
            f"单字查询 {q!r} 竟放行他商 {d!r}：跨供应商记忆泄漏入口未被收口")
    # 空串（任一侧）不得放行
    assert rag._vendor_related("", "祥兴") is False
    assert rag._vendor_related("祥兴", "") is False
    assert rag._vendor_related("", "") is False


# W11-1 阳性对照：多字正常别名/后缀匹配必须仍放行（防过度收紧）
def test_w11_1_legit_alias_and_suffix_still_pass():
    assert rag._vendor_related("德利行", "德利行 Tak Lee Hong") is True
    assert rag._vendor_related("祥兴", "祥兴快餐用品") is True
    assert rag._vendor_related("德利", "德利行") is True       # 既有契约
    assert rag._vendor_related("永隆", "永隆行") is True       # 既有契约
    assert rag._vendor_related("全局品类库", "全局品类库") is True
    assert rag._vendor_related("德利行 Tak Lee Hong", "全局品类库") is False
    assert rag._vendor_related("永記 (Wing Kee) 食品", "永記食品") is False  # 既有契约


# -------------------------------------------------------------
# W11-2：空明细不得产生 sample 向量（噪声先验）
# -------------------------------------------------------------
def test_w11_2_empty_items_writes_no_sample_vector():
    _db = _fresh_db()
    vendor, tenant = "空明細商戶", "default"
    # 空串与纯空白都属"无明细"
    for items in ("", "   \n  "):
        rag.ingest_memory(vendor, items, notes="版式：手寫長單",
                          tenant_id=tenant, source_kind="manual")
    rows = _db.list_vendor_memory(vendor, tenant_id=tenant)
    assert rows, "前置失败：记忆未落库，后续断言无意义"
    mid = rows[0]["memory_id"]

    got = _docs_of(tenant, mid)
    kinds = [(m or {}).get("kind") for m in (got.get("metadatas") or [])]
    docs = got.get("documents") or []
    assert "sample" not in kinds, (
        "空明细仍写了 sample 向量（噪声先验入口）", kinds, docs)
    assert "notes" in kinds, ("notes 文档须维持既有语义（始终写）", kinds)
    assert not any("已确认进货明细" in (c or "") for c in docs), (
        "向量层残留'已确认进货明细'句 → 检索可能把它当候选注入", docs)


def test_w11_2_nonempty_items_still_writes_sample_vector():
    _db = _fresh_db()
    vendor, tenant = "有明細商戶", "default"
    rag.ingest_memory(vendor, "- 菜心 2斤 @10 = 20", notes="版式：手写单",
                      tenant_id=tenant, source_kind="manual")
    rows = _db.list_vendor_memory(vendor, tenant_id=tenant)
    assert rows, "前置失败：记忆未落库"
    mid = rows[0]["memory_id"]
    kinds = _kinds(tenant, mid)
    assert "sample" in kinds, ("非空明细必须仍写 sample 向量（不得改变既有行为）", kinds)
    assert "notes" in kinds
