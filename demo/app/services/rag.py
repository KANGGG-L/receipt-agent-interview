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


def _store():
    global _vs
    if _vs is None:
        _vs = Chroma(
            collection_name="vendor_memory",
            embedding_function=_embedding_provider(),
            persist_directory=PERSIST_DIR,
        )
    return _vs


def ingest_memory(vendor: str, items_text: str, notes: str = ""):
    """人工 approve 后回写 VendorMemory（只认正向信号）。

    语料结构：供应商 + 单位/别称线索 + 已确认明细（few-shot 来源）。
    """
    docs = [
        Document(
            page_content=notes or "",
            metadata={"vendor": vendor, "kind": "notes"},
        ),
        Document(
            page_content=f"供应商 {vendor} 的已确认进货明细：\n{items_text}",
            metadata={"vendor": vendor, "kind": "sample"},
        ),
    ]
    _store().add_documents(docs)
    return vendor


def retrieve_context(vendor: str, top_k: int = 3) -> str:
    """识别前检索 vendor_context：先精确匹配该供应商，再按相似度兜底。"""
    # 1) 精确匹配：该供应商自己的历史记忆最可信
    mem = db.get_vendor_memory(vendor)
    exact = ""
    if mem:
        exact = mem.notes or ""
        if mem.sample:
            exact = (exact + "\n" + mem.sample).strip()

    # 2) 相似度兜底：其他供应商的近似单据（冷启动/名字微变）
    try:
        results = _store().similarity_search(
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
