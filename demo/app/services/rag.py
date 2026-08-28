# -*- coding: utf-8 -*-
"""VendorMemory RAG：按供应商积累的识别上下文（护城河叙事）。

- 语料：供应商确认的单据 → 提取 layout/单位/别称线索，写入 Chroma
- 检索：识别前按「供应商名 + 语料相似度」top-k 注入 <vendor_context>
- 对齐完整版 D36：案例 RAG + 实体 RAG；写时只认正向信号（人工 approve 后回写）
"""

import json
import logging
import os
import uuid
from datetime import datetime
from typing import Optional

from langchain_community.vectorstores import Chroma
from langchain_core.embeddings import Embeddings
from langchain_core.documents import Document

from app import db

logger = logging.getLogger(__name__)

PERSIST_DIR = os.environ.get("RAG_DIR", "./.rag_chroma")


class MemoryBudget:
    """Gap B4 读取预算（Facts 层）：控制注入识别 prompt 的记忆体积。

    token 口径：无 tokenizer 依赖（禁新增依赖），_approx_tokens(text) = len(text)，
    即 1 字符按 1 token 保守计（对 CJK 偏保守、对英文偏宽松，整体取安全侧）。

    TODO(T10)：三个阈值迁移到 app_settings 配置化（settings_service 在 Wave D
    T10 建立），本 Wave 允许模块级常量 DEFAULT_MEMORY_BUDGET。
    """

    def __init__(self, facts_tokens: int = 800, per_item_tokens: int = 200,
                 max_items: int = 6):
        self.facts_tokens = int(facts_tokens)      # 注入总量上限
        self.per_item_tokens = int(per_item_tokens)  # 单条记忆截断上限
        self.max_items = int(max_items)            # 最多注入条数


DEFAULT_MEMORY_BUDGET = MemoryBudget()  # TODO(T10): 阈值走 app_settings 配置化


def _approx_tokens(text: str) -> int:
    """保守 token 代理：1 字符 = 1 token（无 tokenizer 依赖，口径见 MemoryBudget）。"""
    return len(text or "")


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


def ingest_memory(vendor: str, items_text: str, notes: str = "",
                  tenant_id: str = "default", receipt_id=None,
                  source_kind: Optional[str] = None):
    """人工 approve 后回写 VendorMemory（只认正向信号）。

    语料结构：供应商 + 单位/别称线索 + 已确认明细（few-shot 来源）。
    tenant_id 用于 Chroma 租户隔离（FR-9）。
    Gap B1：每条记忆独立成行并带治理元数据 —— memory_id / source_kind /
    source_ref（触发 receipt_id）/ created_at；Chroma metadata 同步携带，
    便于按 memory_id 回溯删除。
    source_kind 缺省推断：带 receipt_id 的调用视作 approve 路径，
    否则视作 manual（如 SKU 别名合并学习）。
    """
    tid = str(tenant_id or "default").strip() or "default"
    kind = str(source_kind or ("approve" if receipt_id is not None else "manual"))
    ref = json.dumps([str(receipt_id)]) if receipt_id is not None else "[]"
    mid = uuid.uuid4().hex
    ts = datetime.now().isoformat()
    # 先落库拿治理主键；同 (vendor, tenant) 内容完全相同的 active 行已存在时
    # 复用既有 memory_id 并跳过 Chroma 重复写入（幂等）。落库失败不阻断 Chroma。
    stored_new = True
    try:
        mid, stored_new = db.upsert_vendor_memory(
            vendor, notes or "", items_text or "", tenant_id=tid,
            source_kind=kind, source_ref=ref, memory_id=mid, created_at=ts)
    except Exception:
        stored_new = True
    if not stored_new:
        return vendor
    docs = [
        Document(
            page_content=notes or "",
            metadata={"vendor": vendor, "kind": "notes", "tenant_id": tid,
                      "memory_id": mid, "source_kind": kind, "created_at": ts},
        ),
        Document(
            page_content=f"供应商 {vendor} 的已确认进货明细：\n{items_text}",
            metadata={"vendor": vendor, "kind": "sample", "tenant_id": tid,
                      "memory_id": mid, "source_kind": kind, "created_at": ts},
        ),
    ]
    # P3-2：对齐 ingest_feedback_memory 写法 —— DB 行已落库成功时，Chroma 故障
    # 只告警降级（向量缺失），不得向上抛错导致 approve 端点在单据已批准后 500。
    try:
        _store(tid).add_documents(docs)
    except Exception as e:
        logger.warning(
            "[rag] ingest_memory add_documents degraded (DB row kept, "
            "vector missing) vendor=%s memory_id=%s: %s", vendor, mid, e)
    return vendor


# 繁简字符快速转换映射表（针对香港餐饮常见供应商字词）
_T2S_MAP = str.maketrans({
    '興': '兴', '糧': '粮', '發': '发', '記': '记', '聯': '联', '號': '号',
    '華': '华', '寶': '宝', '豐': '丰', '榮': '荣', '達': '达', '順': '顺',
    '億': '亿', '誠': '诚', '匯': '汇', '廣': '广', '慶': '庆', '業': '业',
    '實': '实', '總': '总', '標': '标', '龍': '龙', '雞': '鸡', '鴨': '鸭',
    '魚': '鱼', '麵': '面', '飯': '饭', '蛋': '蛋', '肉': '肉', '菜': '菜',
    '菇': '菇', '筍': '笋', '椒': '椒', '姜': '姜', '蒜': '蒜', '蔥': '葱',
    '進': '进', '貨': '货', '買': '买', '賣': '卖', '門': '门', '車': '车',
    '東': '东', '南': '南', '西': '西', '北': '北', '海': '海', '鮮': '鲜',
    '凍': '冻', '莊': '庄', '頭': '头', '條': '条', '隻': '只', '紥': '扎',
    '兩': '两', '廳': '厅', '館': '馆', '舖': '铺', '鋪': '铺', '市': '市',
    '場': '场', '檔': '档', '區': '区', '灣': '湾', '環': '环', '角': '角',
})

# 常见香港餐饮知名供应商别名组 (Canonical Alias Groups)
_VENDOR_ALIAS_GROUPS = [
    {"德利行", "takleehong", "tak lee hong", "德利行粮油", "德利行粮油批发", "德利行批发", "德利行有限公司"},
    {"祥兴", "祥興", "xiangxing", "cheunghing", "cheung hing", "祥兴快餐用品", "祥興快餐用品", "祥兴餐具", "祥兴餐具批发"},
    {"金百加", "kampery", "kamperky", "金百加发展", "金百加發展", "金百加发展有限公司", "金百加红茶", "金百加紅茶"},
    {"联丰", "聯豐", "luen fung", "luenfung", "lian feng", "lianfeng", "联丰食品", "聯豐食品", "联丰行", "聯豐行"},
    {"大生", "tai sang", "taisang", "da sheng", "dasheng", "大生行", "大生粮油", "大生糧油", "大生批发", "大生批發"},
    {"联记", "聯記", "luenkee", "luen kee", "联记号", "聯記號"},
    {"广昌泰", "廣昌泰", "kwongcheongthye", "kwong cheong thye"},
    {"九龙酱油", "九龍醬油", "kowloonsoysauce", "kowloon soy sauce"},
]

# 常见公司实体/业务后缀（用于归一化剥离对比）
_VENDOR_SUFFIXES = [
    "有限公司", "有限责任公司", "股份有限公司", "公司", "批發", "批发",
    "商行", "貿易", "贸易", "發展", "发展", "快餐用品", "用品", "餐具",
    "co.,ltd.", "co., ltd.", "co.ltd", "coltd", "ltd.", "ltd", "limited", "company", "co."
]


def _norm_vendor_name(name: str) -> str:
    """供应商名归一：NFKC + 繁转简 + 标点剥离 + casefold + 去空白（fuzzy 相关性门用）。"""
    import unicodedata
    import re
    s = unicodedata.normalize("NFKC", str(name or "")).translate(_T2S_MAP).casefold()
    s = re.sub(r"[()（）\[\]【】\-_/\\,.:;\"'·\s]+", "", s)
    return s.strip()


def _vendor_core_token(norm_name: str) -> str:
    """剥离常见公司与行业后缀，提取核心字根。"""
    s = norm_name
    for suf in _VENDOR_SUFFIXES:
        norm_suf = _norm_vendor_name(suf)
        if norm_suf and s.endswith(norm_suf) and len(s) > len(norm_suf) + 1:
            s = s[:-len(norm_suf)]
    return s


def _vendor_related(query_vendor: str, doc_vendor: str) -> bool:
    """AC4-a 供应商相关性门：查询名与记忆来源名归一后互为包含或共享别名组才放行（防他商记忆泄漏）。"""
    q = _norm_vendor_name(query_vendor)
    d = _norm_vendor_name(doc_vendor)
    if not q or not d:
        return False
    if q in d or d in q:
        return True

    # 核心词根匹配（如 "德利行" 与 "德利行takleehong" 或 "祥兴" 与 "祥兴快餐用品"）
    q_core = _vendor_core_token(q)
    d_core = _vendor_core_token(d)
    if q_core and d_core and (q_core in d or d_core in q or q_core in d_core or d_core in q_core):
        if len(q_core) >= 2 or len(d_core) >= 2:
            return True

    # 别名组匹配
    for group in _VENDOR_ALIAS_GROUPS:
        norm_group = {_norm_vendor_name(alias) for alias in group}
        q_in_group = any(q == a or a in q or q in a for a in norm_group)
        d_in_group = any(d == a or a in d or d in a for a in norm_group)
        if q_in_group and d_in_group:
            return True

    return False


def retrieve_context(vendor: str, top_k: int = 3, tenant_id: str = "default",
                     budget: Optional[MemoryBudget] = None) -> str:
    """识别前检索 vendor_context：先精确匹配该供应商，再按相似度兜底。

    tenant_id 用作 Chroma 租户硬隔离过滤；DB 侧按 tenant_id 过滤同口径。
    Gap B1：只注入 status='active' 的记忆（列语义闭环，淘汰动作本身归 T8）。
    Gap B4：注入前按 MemoryBudget 预算控制 —— 单条截断 per_item_tokens、
    最多 max_items 条、累计不超 facts_tokens；实际注入的记忆行回写命中
    记账（hit_count / last_hit_at）。
    冷启动供应商（无记忆且无同名 fuzzy 命中）返回空串，绝不携带他商记忆。
    """
    if not vendor or not str(vendor).strip():
        return ""
    b = budget or DEFAULT_MEMORY_BUDGET

    # 候选条目：(展示文本, 可回溯的 memory_id 或 None)
    candidates = []

    # 1) 精确/别名匹配：该供应商自己的历史记忆最可信（active 行，最新在前）
    try:
        for row in db.list_vendor_memory(vendor, tenant_id=tenant_id):
            parts = []
            if row.get("notes"):
                parts.append(row["notes"])
            if row.get("sample"):
                parts.append(row["sample"])
            if not parts:
                continue
            text = f"[{row.get('vendor') or vendor}]\n" + "\n".join(parts)
            candidates.append((text, row.get("memory_id")))
    except Exception:
        pass

    # 2) 相似度兜底：其他供应商的近似单据（冷启动/名字微变），按租户隔离
    #    相关性门：fuzzy 结果按来源供应商名与查询名归一匹配，不匹配的丢弃
    try:
        results = _store(tenant_id).similarity_search(
            f"供应商 {vendor} 的收据版式与单位习惯", k=top_k
        )
        for d in results:
            if not d.page_content.strip():
                continue
            if not _vendor_related(vendor, d.metadata.get("vendor")):
                continue
            candidates.append(
                (f"[{d.metadata.get('vendor')}] {d.page_content}",
                 d.metadata.get("memory_id")))
    except Exception:
        pass

    # 3) 预算装配：单条截断 + 条数上限 + 总量上限；命中行回写记账
    used_texts = []
    used_tokens = 0
    seen_mids = set()
    hit_ids = []
    sep = "\n\n"
    for text, mid in candidates:
        if len(used_texts) >= b.max_items:
            break
        if not text or not text.strip():
            continue
        if mid and mid in seen_mids:
            continue  # 同一记忆不重复注入（精确 + fuzzy 双通道去重）
        # 单条截断：_approx_tokens 口径下字符数即 token 数
        item = text[:b.per_item_tokens]
        extra = _approx_tokens(sep) if used_texts else 0  # 分隔符开销计入预算
        if used_tokens + extra + _approx_tokens(item) > b.facts_tokens:
            continue  # 本条超预算：跳过，尝试更短的后续条目
        used_texts.append(item)
        used_tokens += extra + _approx_tokens(item)
        if mid:
            seen_mids.add(mid)
            hit_ids.append(mid)

    ctx = sep.join(used_texts)
    if hit_ids:
        try:
            db.bump_vendor_memory_hit(hit_ids)
        except Exception:
            pass  # 命中记账失败不阻断识别链路
    return ctx


def ingest_feedback_memory(vendor: str, comment: str, quality_warnings=None,
                           tenant_id: str = "default", source_receipt_ids=None):
    """FR-9 三次连续点踩提炼：把连续纠偏的反馈沉淀为供应商记忆。

    仅当同供应商同租户最近 3 次反馈均为点踩时调用，写入 Chroma 租户隔离集合，
    避免错误记忆自我强化（单次点踩不沉淀）。
    Gap B1：source_kind='feedback_distilled'，source_ref 记录触发点踩的
    receipt_id 列表 JSON（可回溯）。
    Gap B4：替换旧 notes[-4000:] 累积拼接 —— 每次沉淀独立成行（追加式），
    体积由读取侧 MemoryBudget 控制取条数与长度，不再无限累积。
    """
    tid = str(tenant_id or "default").strip() or "default"
    qw = quality_warnings or []
    content = f"供应商 {vendor} 反馈纠偏：{comment}".strip()
    if qw:
        content += f"\n关联质量告警：{'; '.join(qw)}"
    ref = json.dumps([str(r) for r in (source_receipt_ids or [])])
    mid = uuid.uuid4().hex
    ts = datetime.now().isoformat()
    stored_new = True
    try:
        mid, stored_new = db.upsert_vendor_memory(
            vendor, content, "", tenant_id=tid,
            source_kind="feedback_distilled", source_ref=ref,
            memory_id=mid, created_at=ts)
    except Exception:
        stored_new = True
    if not stored_new:
        return content  # 内容完全相同的沉淀已存在（幂等），不重复入向量库
    doc = Document(
        page_content=content,
        metadata={"vendor": vendor, "kind": "feedback_distilled", "tenant_id": tid,
                  "memory_id": mid, "source_kind": "feedback_distilled",
                  "created_at": ts},
    )
    try:
        _store(tid).add_documents([doc])
    except Exception:
        pass
    return content
