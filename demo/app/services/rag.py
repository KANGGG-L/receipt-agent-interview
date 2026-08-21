# -*- coding: utf-8 -*-
"""VendorMemory RAG：按供应商积累的识别上下文（护城河叙事）。

- 语料：供应商确认的单据 → 提取 layout/单位/别称线索，写入 Chroma
- 检索：识别前按「供应商名 + 语料相似度」top-k 注入 <vendor_context>
- 对齐完整版 D36：案例 RAG + 实体 RAG；写时只认正向信号（人工 approve 后回写）
"""

import os
from typing import Optional

from langchain_community.vectorstores import Chroma
from langchain_core.embeddings import Embeddings
from langchain_core.documents import Document

from app import db

PERSIST_DIR = os.environ.get("RAG_DIR", "./.rag_chroma")


class _CharNGramEmbeddings(Embeddings):
    """离线确定性嵌入（无 API 依赖）：char-n-gram 哈希 + L2 归一。

    Demo 用；生产换 DashScope/OpenAI embedding（RAG_EMBEDDING_PROVIDER 开关）。
    """

    def __init__(self, dim: int = 256):
        self.dim = dim

    def embed_documents(self, texts):
        return [self._embed(t) for t in texts]

    def embed_query(self, text):
        return self._embed(text)

    def _embed(self, text):
        import hashlib
        import unicodedata
        norm = unicodedata.normalize("NFKC", str(text or "")).casefold().replace(" ", "")
        vec = [0.0] * self.dim
        for n in (2, 3):
            if len(norm) < n:
                bucket = int(hashlib.sha256(norm.encode()).hexdigest(), 16) % self.dim
                vec[bucket] += 1.0
                continue
            for i in range(len(norm) - n + 1):
                gram = norm[i:i + n]
                bucket = int(hashlib.sha256(gram.encode()).hexdigest(), 16) % self.dim
                vec[bucket] += 1.0
        norm_factor = sum(c * c for c in vec) ** 0.5
        if norm_factor > 0:
            vec = [c / norm_factor for c in vec]
        return vec


def _embedding_provider() -> Embeddings:
    return _CharNGramEmbeddings()


_vs: Optional[Chroma] = None
_vs_tenant: dict[str, Chroma] = {}


def _tenant_collection_name(tenant_id: str) -> str:
    tid = str(tenant_id or "default").strip() or "default"
    # 安全：仅允许字母数字下划线与中划线，其余替换
    import re
    tid = re.sub(r"[^a-zA-Z0-9_-]", "_", tid)[:64]
    return f"tenant_{tid}_vendor_memory"


def _store(tenant_id: Optional[str] = None):
    """租户隔离的 Chroma 向量库。

    每个 tenant_id 拥有独立 collection，物理硬隔离（FR-9）。
    向后兼容：tenant_id 为空或 default 时回退到全局 vendor_memory。
    """
    global _vs, _vs_tenant
    if tenant_id is None or str(tenant_id).strip() in ("", "default"):
        if _vs is None:
            _vs = Chroma(
                collection_name="vendor_memory",
                embedding_function=_embedding_provider(),
                persist_directory=PERSIST_DIR,
            )
        return _vs
    tid = str(tenant_id).strip()
    if tid not in _vs_tenant:
        _vs_tenant[tid] = Chroma(
            collection_name=_tenant_collection_name(tid),
            embedding_function=_embedding_provider(),
            persist_directory=PERSIST_DIR,
        )
    return _vs_tenant[tid]


def ingest_memory(vendor: str, items_text: str, notes: str = "", tenant_id: str = "default"):
    """人工 approve 后回写 VendorMemory（只认正向信号）。

    语料结构：供应商 + 单位/别称线索 + 已确认明细（few-shot 来源）。
    tenant_id 用于 Chroma 租户隔离（FR-9）。
    """
    docs = [
        Document(
            page_content=notes or "",
            metadata={"vendor": vendor, "kind": "notes", "tenant_id": tenant_id or "default"},
        ),
        Document(
            page_content=f"供应商 {vendor} 的已确认进货明细：\n{items_text}",
            metadata={"vendor": vendor, "kind": "sample", "tenant_id": tenant_id or "default"},
        ),
    ]
    _store(tenant_id).add_documents(docs)
    # 同时落库 vendor_memory 表（精确匹配用）
    try:
        db.upsert_vendor_memory(vendor, notes or "", items_text or "")
    except Exception:
        pass
    return vendor


def retrieve_context(vendor: str, top_k: int = 3, tenant_id: str = "default") -> str:
    """识别前检索 vendor_context：先精确匹配该供应商，再按相似度兜底。

    tenant_id 用作 Chroma 租户硬隔离过滤。
    """
    # 1) 精确匹配：该供应商自己的历史记忆最可信
    mem = db.get_vendor_memory(vendor)
    exact = ""
    if mem:
        # db.get_vendor_memory 返回 dict（vendor, notes, sample）
        notes = mem.get("notes") if isinstance(mem, dict) else getattr(mem, "notes", "")
        sample = mem.get("sample") if isinstance(mem, dict) else getattr(mem, "sample", "")
        exact = notes or ""
        if sample:
            exact = (exact + "\n" + sample).strip()

    # 2) 相似度兜底：其他供应商的近似单据（冷启动/名字微变），按租户隔离
    try:
        results = _store(tenant_id).similarity_search(
            f"供应商 {vendor} 的收据版式与单位习惯", k=top_k
        )
        fuzzy = "\n\n".join(
            f"[{d.metadata.get('vendor')}] {d.page_content}" for d in results
            if d.page_content.strip()
        )
    except Exception:
        fuzzy = ""

    ctx = exact
    if fuzzy and fuzzy not in ctx:
        ctx = (ctx + "\n\n" + fuzzy).strip() if ctx else fuzzy
    return ctx or ""


def ingest_feedback_memory(vendor: str, comment: str, quality_warnings=None, tenant_id: str = "default"):
    """FR-9 三次连续点踩提炼：把连续纠偏的反馈沉淀为供应商记忆。

    仅当同供应商同租户最近 3 次反馈均为点踩时调用，写入 Chroma 租户隔离集合，
    避免错误记忆自我强化（单次点踩不沉淀）。
    """
    qw = quality_warnings or []
    content = f"供应商 {vendor} 反馈纠偏：{comment}".strip()
    if qw:
        content += f"\n关联质量告警：{'; '.join(qw)}"
    doc = Document(
        page_content=content,
        metadata={"vendor": vendor, "kind": "feedback_distilled", "tenant_id": tenant_id or "default"},
    )
    try:
        _store(tenant_id).add_documents([doc])
    except Exception:
        pass
    # 同时更新 vendor_memory 备注（累积提炼），用于精确检索
    try:
        existing = db.get_vendor_memory(vendor)
        prev_notes = (existing.get("notes") if existing else "") or ""
        merged = (prev_notes + "\n" + content).strip()[-4000:]
        db.upsert_vendor_memory(vendor, merged, "")
    except Exception:
        pass
    return content
