# -*- coding: utf-8 -*-
"""VendorMemory RAG：按供应商积累的识别上下文（护城河叙事）。

- 语料：供应商确认的单据 → 提取 layout/单位/别称线索，写入 Chroma
- 检索：识别前按「供应商名 + 语料相似度」top-k 注入 <vendor_context>
- 对齐完整版 D36：案例 RAG + 实体 RAG；写时只认正向信号（人工 approve 后回写）
- 嵌入档由 `RAG_EMBEDDING_PROVIDER` 决定（app/services/embeddings.py 唯一实现：
  hash 缺省 / siliconflow bge-m3 / dashscope），collection 名带该档 identity 后缀，
  切档即新建集合而非混写（维度不同，混写会报错或静默错配）
- 召回（P3-T6）：**混合召回**——精确/别名腿（scope 收窄后的该供应商记忆行）+ 关键词腿
  （BM25 近似，`services/memory_recall.py` 唯一实现）+ 向量腿（Chroma 相似度），三腿
  候选合并去重后由 `memory_recall.rerank` 按「腿基础分 + 该腿相关度 + 精确命中 + 治理
  信号（decay_score / hit_count / last_hit_at）」重排，权重可配可审计
  （settings 键 `memory_recall_weights`，缺省见 `memory_recall.DEFAULT_WEIGHTS`）
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
from app.services import embeddings
from app.services import memory_recall
from app.services import rag_paths

logger = logging.getLogger(__name__)

# 读路径各阶段「静默降级」的阶段名（get_retrieve_failure_counts 的键）。
# why 单列常量而不是各处写字符串字面量：日志与计数两处都按它聚合，抄两份字面量迟早
# 会出现「日志写 exact、计数写 exact_channel」这类对不上的形态（本仓①类缺陷）。
RETRIEVE_STAGE_BUDGET = "budget"
RETRIEVE_STAGE_BUDGET_SETTINGS = "budget_settings"
RETRIEVE_STAGE_ARCHIVE_THRESHOLD = "archive_threshold"
RETRIEVE_STAGE_DECAY_PARSE = "decay_parse"
RETRIEVE_STAGE_EXACT = "exact"
RETRIEVE_STAGE_KEYWORD = "keyword"
RETRIEVE_STAGE_FUZZY = "fuzzy"
RETRIEVE_STAGE_HIT_ACCOUNTING = "hit_accounting"
# P3-T6 新增：rerank 权重解析（读 settings 失败 → 回退缺省权重，必须可见）
RETRIEVE_STAGE_RECALL_WEIGHTS = "recall_weights"

# 读路径降级计数：{阶段名: 次数}。**进程级、不持久化、不随记忆开关清零**。
# why 需要它：检索的每一步历史上都是 `except Exception: pass` —— fail-open 本身是本
# 项目的刻意设计（检索故障不得阻断识别），代价却是运维**无法区分**「这个供应商真的
# 没有可用记忆」与「检索坏了」：两者返回的都是空串、且都可能零日志（本仓③类缺陷的
# 最大观测盲区）。计数 + warning 让这个区别可观测。
# 进程级是刻意的：不新增持久化依赖，进程重启即归零，只服务「本进程内最近是否在降级」
# 这一诊断问题，不是历史累计指标。
# 接线状态：本计数器**已接生产读路径**（`retrieve_context` 的每一处降级都调
# `_note_retrieve_failure`，本波 Y2 又新增 `recall_weights` / `keyword` 两个阶段），
# 但**对外读接口仍是待接线状态**：`get_retrieve_failure_counts()` 目前只有测试在用，
# 尚无生产消费方（残债 R10）。建议接线点（只读上报，不改变任何检索行为）：
# `api_admin.py` 的 `GET /api/admin/memory-metrics`（它已加载 `demo/scripts/memory_metrics.py`
# 这一份度量实现，把 retrieve_failures 并进返回体即可同时被看板看到）。
_RETRIEVE_FAILURE_COUNTS: dict = {}


def get_retrieve_failure_counts() -> dict:
    """读路径各阶段降级次数快照 {阶段名: 次数}。

    返回**副本**（调用方改它不影响内部计数，避免巡检脚本顺手清空现场）。

    明确契约：这是**进程级计数器，不持久化** —— 进程重启即归零，只反映本进程内累计的
    降级次数，不得当作历史累计或跨实例指标使用。阶段名取 RETRIEVE_STAGE_* 常量。
    """
    return dict(_RETRIEVE_FAILURE_COUNTS)


def _note_retrieve_failure(stage: str, exc: BaseException,
                           vendor=None, tenant_id=None) -> None:
    """读路径某阶段降级：计数 +1 并打 warning（阶段 / 供应商 / 租户 / 异常类型与文本）。

    why 单点实现：七处降级的「计数 + 日志」完全同构，抄七份模板必然出现「改一处漏六处」
    （本仓①类缺陷）。阶段名由调用方以 RETRIEVE_STAGE_* 常量传入。

    why 必须带 vendor / tenant_id / 异常类型与文本：只有阶段名定位不出是哪个供应商、
    哪个租户在降级、以及失败原因是什么；而异常**类型**常常是排查的第一分叉（TypeError /
    AttributeError 指向代码缺陷，ConnectionError / TimeoutError 指向外部不确定性），
    只看文本容易把代码缺陷当成网络抖动放过。预算 / 阈值两个阶段发生在拿到 vendor 之前，
    故允许为空（如实记 None，不编造上下文）。

    契约：本函数**只记录** —— 不抛异常、不改变调用方的降级路径（调用方仍 fail-open、
    返回值与加日志前逐值一致）。
    """
    _RETRIEVE_FAILURE_COUNTS[stage] = _RETRIEVE_FAILURE_COUNTS.get(stage, 0) + 1
    logger.warning(
        "[rag] retrieve stage '%s' degraded (fail-open, result unchanged): "
        "vendor=%r tenant_id=%r error=%s: %s",
        stage, vendor, tenant_id, type(exc).__name__, exc)


# 向量库落盘目录（P8a-R7）：解析口径的**唯一实现**在 `services/rag_paths.py`，
# 本处与巡检工具 `memory_consistency._resolve_rag_dir()` 都消费它，不再各写一份。
# why 必须以应用根为基准：旧实现 `os.environ.get("RAG_DIR", "./.rag_chroma")` 是 cwd
# 相对的 —— 生产从 `demo/` 起进程写 `demo/.rag_chroma`，而从仓库根起的脚本写
# `<repo>/.rag_chroma`，同一 memory_id 的向量因此可能落进另一个 store，巡检随即报
# 「DB 有行、向量缺失」（R7；同时是「测试从仓库根跑就写 live 向量库」H3 的同源根因）。
# 改为应用根基准后，任意 cwd 下 import 都指向同一绝对位置；`RAG_DIR` 显式覆盖仍优先
# （测试隔离完全依赖它）。
PERSIST_DIR = rag_paths.resolve_rag_dir()


class MemoryBudget:
    """Gap B4 读取预算（Facts 层）：控制注入识别 prompt 的记忆体积。

    token 口径：无 tokenizer 依赖（禁新增依赖），_approx_tokens(text) = len(text)，
    即 1 字符按 1 token 保守计（对 CJK 偏保守、对英文偏宽松，整体取安全侧）。

    T10 收口：三个阈值经 settings_service 实时读取（键 memory_budget_*，
    缺省 800/200/6），管理台改后无需重启；from_settings() 为运行时入口。
    """

    def __init__(self, facts_tokens: int = 800, per_item_tokens: int = 200,
                 max_items: int = 6):
        self.facts_tokens = int(facts_tokens)      # 注入总量上限
        self.per_item_tokens = int(per_item_tokens)  # 单条记忆截断上限
        self.max_items = int(max_items)            # 最多注入条数

    @classmethod
    def from_settings(cls) -> "MemoryBudget":
        """按 app_settings 当前值构造；读取失败回退缺省（800/200/6）。"""
        try:
            from app.services import settings_service as _ss
            return cls(
                facts_tokens=_ss.get_int("memory_budget_facts_tokens", 800),
                per_item_tokens=_ss.get_int("memory_budget_per_item_tokens", 200),
                max_items=_ss.get_int("memory_budget_max_items", 6),
            )
        except Exception as e:
            # 读设置失败 → 回退缺省预算（800/200/6）。回退本身可接受（配置系统故障不得
            # 改变识别行为、更不得阻断），但静默会把「预算键一直没生效、管理台调了没反应」
            # 永久藏起来 —— 故留痕（U2）。此阶段发生在取得 vendor 之前，故不带上下文。
            _note_retrieve_failure(RETRIEVE_STAGE_BUDGET_SETTINGS, e)
            return cls()


# 缺省形态实例（等价旧行为）；运行时注入路径一律用 MemoryBudget.from_settings()
DEFAULT_MEMORY_BUDGET = MemoryBudget()


def _approx_tokens(text: str) -> int:
    """保守 token 代理：1 字符 = 1 token（无 tokenizer 依赖，口径见 MemoryBudget）。"""
    return len(text or "")


def _embedding_provider() -> Embeddings:
    """当前生效嵌入档（唯一实现 = app.services.embeddings.get_embeddings()）。

    why 不再本地实现 char-ngram 哈希嵌入：本模块曾自带一份与 embeddings 模块重复的
    哈希实现，其 docstring 同时宣称「生产换 DashScope/OpenAI embedding
    （RAG_EMBEDDING_PROVIDER 开关）」—— 而该环境变量在全仓零读取（gap M7）：运维按
    文档改了键，实际仍在跑 char-ngram 哈希，且没有任何日志能看出来。档位解析 /
    客户端构造 / 可用性探测 / 回落现在全部收敛到 embeddings 模块一份，本函数只是
    接线点（同一算法两份实现＝本仓①类缺陷）。
    """
    return embeddings.get_embeddings()


_vs: Optional[Chroma] = None
_vs_tenant: dict[str, Chroma] = {}


def _tenant_collection_name(tenant_id: str, identity=None) -> str:
    """collection 名：基础名 + 当前生效嵌入档的 identity 后缀。

    why 必须带 identity：不同 provider 的向量维度与语义空间都不同（hash 256 维 /
    bge-m3 1024 维），写进同一个 collection 会直接维度报错或静默语义错配（gap M1）。
    后缀取自 `embeddings.EmbeddingIdentity.collection_suffix()`（provider:model:dim
    的集合名安全形态），本模块**不另抄一份档位→维度表**。

    基础名：tenant_id 为空 / default 时是全局桶 `vendor_memory`，否则
    `tenant_<tid>_vendor_memory`（tid 里非 `[a-zA-Z0-9_-]` 的字符替换为 `_` 并截断 64）。
    全局桶同样带后缀 —— 否则「默认租户切档」仍会把两种维度的向量写进同一集合，
    而这正是本波次要消除的错配面（存量向量已按 D5 清退，故历史名不带后缀亦无数据失联）。

    identity 入参：
    - 传 `EmbeddingIdentity` → 按其拼名（`_store` 走这条路，且传的是**实际生效档**）；
    - 缺省 None → 用 `embeddings.requested_identity()` 按**请求档**解析，不构造远端
      客户端、不联网。命名 / 巡检 / 测试用缺省形态即可。
    """
    if identity is None:
        identity = embeddings.requested_identity()
    suffix = identity.collection_suffix()
    tid = str(tenant_id or "default").strip() or "default"
    if tid == "default":
        return f"vendor_memory_{suffix}"
    # 安全：仅允许字母数字下划线与中划线，其余替换
    import re
    tid = re.sub(r"[^a-zA-Z0-9_-]", "_", tid)[:64]
    return f"tenant_{tid}_vendor_memory_{suffix}"


def _store(tenant_id: Optional[str] = None):
    """租户隔离的 Chroma 向量库。

    每个 tenant_id 拥有独立 collection，物理硬隔离（FR-9）。
    向后兼容：tenant_id 为空或 default 时回退到全局 vendor_memory。
    O1：collection 名带当前生效嵌入档的 identity 后缀（见 _tenant_collection_name）。
    **嵌入对象与集合名取自同一次 `_embedding_provider()` 调用** —— 只解析一次档位，
    杜绝「名字是 A 档、嵌入函数是 B 档」的错配（可用性探测失败会回落到别的档，
    名字必须跟着实际生效档走）。

    Q1（缓存命中不重解析档位）：档位解析与 Chroma 构造**只在「本租户尚未缓存」时发生**。
    why：旧实现把 `_embedding_provider()` 放在缓存判断之前，于是每次取记忆都会重走
    `embeddings.get_embeddings()`，而远端档在那里会做可用性探测（**真发一次 HTTP**）。
    实测探针：连续三次 `_store("t1")`（Chroma 已缓存）时 `requests.post` 累计 1→2→3
    —— 生产上每次检索都多一次外呼（延迟与失败面都被放大）。缓存命中的语义是「直接用
    已打开的那一份」，不需要也不应该再解析一次档位。
    不变量不变：缓存未命中时才解析，且**只解析一次**（嵌入对象与集合名同源）。
    """
    global _vs, _vs_tenant
    if tenant_id is None or str(tenant_id).strip() in ("", "default"):
        if _vs is None:
            provider = _embedding_provider()
            _vs = Chroma(
                collection_name=_tenant_collection_name(tenant_id, provider.identity),
                embedding_function=provider,
                persist_directory=PERSIST_DIR,
            )
        return _vs
    tid = str(tenant_id).strip()
    if tid not in _vs_tenant:
        provider = _embedding_provider()
        _vs_tenant[tid] = Chroma(
            collection_name=_tenant_collection_name(tid, provider.identity),
            embedding_function=provider,
            persist_directory=PERSIST_DIR,
        )
    return _vs_tenant[tid]


def _persist_memory_row(vendor, notes, sample, tid, kind, ref, mid, ts,
                        supplier_id=None, written_by=None):
    """落库拿治理主键（P0-T4 单点实现，两条写入链路共用）。

    返回 (memory_id, is_new)；**落库失败返回 None，调用方必须据此跳过向量写入**。
    why：旧实现在 DB 抛错时静默置 stored_new=True 继续往下走，把向量写进 Chroma，
    而那次写入用的 memory_id 从未落库 —— 制造出「向量存在、DB 无行」的孤儿：
    既不可按 memory_id 回溯删除（P1-T1 级联删的键就是它），也不参与治理过滤
    （allowed_mids 门），只能靠人工清库。live 实测 DB↔向量 memory_id 交集为 0、
    179 条向量全部被门拦掉，即由此而来。这里把失败显式升级为 logger.error + 拒绝
    写入：宁可丢向量（DB 行在，可重建），也不留孤儿。

    supplier_id（J3）：调用方已知的权威主体锚点，透传给 db.upsert_vendor_memory；
    为 None 时由 db 侧按 vendor 名解析（解析口径只有一份，见 _memory_anchor_for_write）。

    written_by（U1）：写入者标识，供审计追溯"这条记忆是谁写的"。这是审计列而非事实
    内容，故**凡调用方提供了就落库**（缺省 None = 不提供 → 该列保持 NULL，与新增形参
    前完全一致，遗留调用方 ingest_memory 即走这条）。
    why 走 `fact_row` 而不是给 db.upsert_vendor_memory 新增形参：written_by 已被 db 侧
    归入结构化载荷列（`_MEMORY_FACT_PAYLOAD_COLUMNS` 由 memory_facts.ROW_FIELDS 派生，
    written_by 在列内），载荷通道就是写它的既有机制；再开一个同名形参等于同一列两份
    写入实现（本仓高发的「同一根因多份实现只改一份」）。
    """
    # 只有真正有值时才走载荷通道：空值不写，让"未提供写入者"在库中保持 NULL
    # （NULL 表示"没有该字段"，而 "" 表示"有值且为空" —— 两者在读侧同义，
    #  但前者不改变遗留路径的既有落库形态）。
    payload = {"written_by": written_by} if str(written_by or "").strip() else None
    try:
        return db.upsert_vendor_memory(
            vendor, notes or "", sample or "", tenant_id=tid,
            source_kind=kind, source_ref=ref, memory_id=mid, created_at=ts,
            supplier_id=supplier_id, fact_row=payload)
    except Exception as e:
        logger.error(
            "[rag] vendor memory persist failed, vector write skipped "
            "(orphan vector avoided) vendor=%s tenant=%s memory_id=%s: %s",
            vendor, tid, mid, e)
        return None


def _vector_write_allowed(mid, tid, docs) -> bool:
    """向量写入前的完整性闸门（P0-T4 单点实现，两条写入链路共用）。

    判据：memory_id 与 tenant_id 均非空，且每条 doc 的 metadata 都带这两个键。
    why：memory_id 是「向量 ↔ DB 行」的唯一回溯键（级联删除、active 过滤都按它走），
    tenant_id 是租户隔离键。任一缺失，写进去的向量既不可回溯也不可删除 —— 存量
    179 条孤儿里 155 条连 memory_id 键都没有，正是缺这道闸。故任一项不满足即拒绝
    写入并记 error，不做「先写进去再说」的降级。
    """
    if not str(mid or "").strip():
        logger.error("[rag] vector write blocked: empty memory_id tenant=%s", tid)
        return False
    if not str(tid or "").strip():
        logger.error("[rag] vector write blocked: empty tenant_id memory_id=%s", mid)
        return False
    for d in docs or []:
        meta = getattr(d, "metadata", None) or {}
        if not str(meta.get("memory_id") or "").strip():
            logger.error(
                "[rag] vector write blocked: doc metadata missing memory_id "
                "(memory_id=%s tenant=%s)", mid, tid)
            return False
        if not str(meta.get("tenant_id") or "").strip():
            logger.error(
                "[rag] vector write blocked: doc metadata missing tenant_id "
                "(memory_id=%s tenant=%s)", mid, tid)
            return False
    return True


def _memory_vectors_present(vs, mid) -> bool:
    """只读查询：目标 collection 里是否已有该 memory_id 的向量。

    用 `Collection.get(where={"memory_id": mid})`（按 metadata 精确匹配）而不是相似度
    检索：后者要过嵌入函数、只返 top-k、漏检概率非零 —— 拿它判「向量在不在」会把
    「存在但没进 top-k」误判成缺失，进而重复写入。`get` 的语义恰是「有没有」。
    """
    res = vs.get(where={"memory_id": mid})
    return bool((res or {}).get("ids"))


def _sync_memory_vectors(tid, mid, vendor, docs, reusing: bool) -> None:
    """把记忆向量同步进目标租户 collection —— 两条写入链路共用的**唯一实现**。

    P8a-R8：`reusing=True`（DB 幂等去重命中、复用既有 memory_id）时**不得直接
    return 跳过向量写入**。why：首次写入可能落进另一个 store（P8a-R7 修的 cwd 相对
    路径就是实例），或当时 Chroma 写失败被降级成 warning；此后每次同内容调用都复用
    同一 memory_id，于是「DB 有 active 行、向量库无该 mid」的脱钩永久存在 —— 检索按
    mid 取不到该行、巡检报 missing_vector_mids，而写入路径永不自愈（live 实测缺失
    5 条）。故这里补「确保向量存在」：复用分支先只读查一次，缺则用**调用方同一套
    docs**补写并留痕。

    幂等：复用且向量已存在时直接返回，不重复写 —— 否则连续两次同内容调用会把向量
    数翻倍（用例 b 钉住这一点）。

    why 单点实现：新建/复用两条分支 × ingest_memory / approve_pending_to_memory 两条
    链路共四处，若各写一份「add_documents + try/except」，改一处漏三处正是本仓①类
    缺陷。调用方只负责**构造 docs 与过完整性闸门**，写入形态只有这一份。

    Chroma 故障只告警降级（DB 行已落库成功，向量可重建），**不得上抛** —— 否则
    approve 端点在单据已批准后 500。存在性查询本身抛错同理（走同一个 except）。
    """
    try:
        vs = _store(tid)
        if reusing and _memory_vectors_present(vs, mid):
            return
        vs.add_documents(docs)
        if reusing:
            logger.info(
                "[rag] idempotent reuse: vector missing for memory_id=%s, "
                "backfilled %d doc(s) vendor=%s tenant=%s",
                mid, len(docs), vendor, tid)
    except Exception as e:
        logger.warning(
            "[rag] memory vector sync degraded (DB row kept, vector missing) "
            "vendor=%s memory_id=%s reusing=%s: %s", vendor, mid, reusing, e)


def _find_memory_row(tenant_id, memory_id, vendor=None):
    """按 memory_id 取一条记忆行的治理字典（**含归档行**）；找不到返回 None。

    走 `db.list_vendor_memory(include_archived=True)` 而不是另写一条 SQL：行的取数与
    字段口径只有这一份（含 fact_kind / fact_value / supplier_id 等治理列），复制一份
    查询必然出现「治理字段两处口径分叉」（本仓①类缺陷）。vendor 只是收窄候选，最终
    判据是 memory_id 精确相等 —— 名字相关度门（`_vendor_related`）会放行互相包含的
    商号，故不能用"列表首条"当命中。
    """
    mid = str(memory_id or "").strip()
    if not mid:
        return None
    v = str(vendor).strip() if vendor and str(vendor).strip() else None
    try:
        rows = db.list_vendor_memory(v, tenant_id=tenant_id, include_archived=True)
    except Exception as e:
        logger.warning(
            "[rag] memory row lookup failed memory_id=%s vendor=%s tenant=%s: %s",
            mid, vendor, tenant_id, e)
        return None
    for r in rows or []:
        if str(r.get("memory_id") or "") == mid:
            return r
    return None


def ensure_memory_vectors(tenant_id, memory_id, vendor=None) -> bool:
    """R11 恢复路径：为一条记忆补建向量（缺则补写，已有则不动）。

    为什么需要它：归档此前只改 DB 行的 status、**不动向量层**（删除有级联、归档没有），
    而 R11 让归档路径**级联删掉**该 mid 的向量 —— 于是「恢复」若不补写，这条记忆的向量腿
    就永久缺失（DB 行 active、向量库无该 mid）：巡检报 `missing_vector_mids`，且没有任何
    写入路径会自愈（P8a-R8 修的是幂等复用分支的同一形态，只是那次不是由归档引起的）。

    为什么不是"第二份文档构造 / 向量写入实现"（本仓①/②类缺陷的防线）：
    - 文本来源 `_row_payload` —— 行 →「可注入文本」的唯一实现，注入 prompt 用的就是它，
      把它嵌入向量在语义上自洽；另写一份 notes/sample 拼接就是同口径的第二份实现；
    - 写入 `_sync_memory_vectors(reusing=True)` —— P8a·AK2 建的「确保向量存在」唯一实现
      （先只读查该 mid 在不在、缺才写、Chroma 故障只 warning 降级不上抛）。
    本函数只做「取行 → 渲染 → 过完整性闸门」（与既有两条写入链路同一套闸门）。

    metadata 的 `kind` 取 `memory_row`（表示"这枚向量是按记忆行渲染重建的"）。
    已核 grep：全仓没有按 doc metadata 的 kind 分支的消费方（向量腿只读 memory_id /
    vendor / page_content），故这个取值不会改变任何读侧行为。

    返回值：True = 该 mid 的向量当前在库中（含本次补写成功）；False = 未补写/未成功
    （memory_id 为空 / 行不存在 / 无可嵌入内容 / 完整性闸门拒绝 / 向量库不可用而降级）。
    **任何分支都不抛异常** —— 调用方（恢复动作）的状态变更已经提交，向量层故障不得把它
    升级成失败。降级逐处 warning 留痕，不静默。
    """
    tid = str(tenant_id or "default").strip() or "default"
    mid = str(memory_id or "").strip()
    if not mid:
        logger.warning(
            "[rag] ensure_memory_vectors skipped: empty memory_id tenant=%s", tid)
        return False
    row = _find_memory_row(tid, mid, vendor=vendor)
    if row is None:
        logger.warning(
            "[rag] ensure_memory_vectors skipped: memory row not found "
            "memory_id=%s vendor=%s tenant=%s", mid, vendor, tid)
        return False
    vname = str(row.get("vendor") or vendor or "")
    text, _is_structured = _row_payload(row, vname)
    if not str(text or "").strip():
        # 形态齐备却没有可注入内容（例如只有空 notes 的自由文本行）：写一枚空向量只会
        # 占位（检索侧还要靠 page_content 非空过滤），故不写 —— 但要留痕，不静默。
        logger.warning(
            "[rag] ensure_memory_vectors skipped: no injectable content "
            "memory_id=%s vendor=%s tenant=%s", mid, vname, tid)
        return False
    doc = Document(
        page_content=str(text),
        metadata={"vendor": vname, "kind": "memory_row", "tenant_id": tid,
                  "memory_id": mid,
                  "source_kind": str(row.get("source_kind") or "manual"),
                  "created_at": str(row.get("created_at")
                                    or datetime.now().isoformat())},
    )
    if not _vector_write_allowed(mid, tid, [doc]):
        return False
    _sync_memory_vectors(tid, mid, vname, [doc], reusing=True)
    try:
        present = _memory_vectors_present(_store(tid), mid)
    except Exception as e:
        logger.warning(
            "[rag] ensure_memory_vectors: presence recheck failed "
            "(state change kept) memory_id=%s vendor=%s tenant=%s: %s",
            mid, vname, tid, e)
        return False
    if present:
        logger.info(
            "[rag] R11 restore: vector ensured memory_id=%s vendor=%s tenant=%s",
            mid, vname, tid)
        return True
    logger.warning(
        "[rag] R11 restore degraded: vector still missing after ensure "
        "memory_id=%s vendor=%s tenant=%s", mid, vname, tid)
    return False


def ingest_memory(vendor: str, items_text: str, notes: str = "",
                  tenant_id: str = "default", receipt_id=None,
                  source_kind: Optional[str] = None, supplier_id=None,
                  written_by: Optional[str] = None):
    """人工 approve 后回写 VendorMemory（只认正向信号）。

    语料结构：供应商 + 单位/别称线索 + 已确认明细（few-shot 来源）。
    tenant_id 用于 Chroma 租户隔离（FR-9）。
    Gap B1：每条记忆独立成行并带治理元数据 —— memory_id / source_kind /
    source_ref（触发 receipt_id）/ created_at；Chroma metadata 同步携带，
    便于按 memory_id 回溯删除。
    source_kind 缺省推断：带 receipt_id 的调用视作 approve 路径，
    否则视作 manual（如 SKU 别名合并学习）。
    P0-T4：DB 落库失败 → logger.error + **跳过向量写入**（不再制造孤儿向量）；
    写入向量前过 `_vector_write_allowed` 完整性闸门（memory_id + tenant_id）。
    P8a-R8：命中幂等去重（复用既有 memory_id）时**不再直接 return** —— 交给
    `_sync_memory_vectors` 先查该 mid 是否已在目标 collection，缺则用同一套 docs
    补写并留痕（DB 有行 ⇒ 向量存在）；向量已在时不重复写，去重语义与向量条数均
    不受影响。
    J3 supplier_id：调用方已知的权威主体锚点（approve 回写传 `receipts.supplier_id`）。
    不传（None）时由 db 侧按 vendor 名解析 —— 解析口径只有一份，见
    `db._memory_anchor_for_write`。两边都拿不到就写 NULL 并告警，绝不编造。
    written_by（U1 审计链）：写入者标识，经 `_persist_memory_row` 落
    vendor_memory.written_by。approve 直写路径（api_receipts.approve_receipt）必须
    透传当前操作者，使「每条生效记忆可追溯到写入者」无缺口；不传（None/空串）时
    该列保持 NULL，与既有调用方（离线脚本/测试直调）行为完全一致。
    """
    tid = str(tenant_id or "default").strip() or "default"
    kind = str(source_kind or ("approve" if receipt_id is not None else "manual"))
    ref = json.dumps([str(receipt_id)]) if receipt_id is not None else "[]"
    mid = uuid.uuid4().hex
    ts = datetime.now().isoformat()
    # W-9：记忆语料 PII 最小化 —— 开关关闭（缺省）时逐字节原样返回，零行为变化；
    # 开启时在**落库前 + 写向量前**脱敏 notes / sample（单点实现，两条落点同源）。
    # 只作用于记忆语料，不改动单据本身的 receipts / receipt_items。
    notes = _mask_memory_text(notes, field="notes", vendor=vendor)
    items_text = _mask_memory_text(items_text, field="sample", vendor=vendor)
    # 先落库拿治理主键；同 (vendor, tenant) 内容完全相同的 active 行已存在时
    # 复用既有 memory_id（幂等去重）。落库失败则整条不写向量。
    persisted = _persist_memory_row(vendor, notes, items_text, tid, kind, ref, mid, ts,
                                    supplier_id=supplier_id, written_by=written_by)
    if persisted is None:
        return vendor  # DB 无行 -> 写向量即孤儿，直接放弃
    mid, stored_new = persisted
    docs = [
        Document(
            page_content=notes or "",
            metadata={"vendor": vendor, "kind": "notes", "tenant_id": tid,
                      "memory_id": mid, "source_kind": kind, "created_at": ts},
        ),
    ]
    # W11-2：明细为空时**不**构造 sample 向量。旧实现无条件拼
    # `供应商 {vendor} 的已确认进货明细：\n{items_text}`：items_text 为空时该前缀仍使
    # page_content 非空，读侧那层 `if not d.page_content.strip(): continue` 拦不住，
    # 于是向量腿会把这句无信息文本当候选注入 prompt —— 纯噪声先验。明细非空时逐字不变。
    # notes 文档维持既有语义（始终写、内容可为空串；空内容读侧会被 page_content 过滤掉，
    # 不构成噪声），本改动只收口 sample 一处。
    if str(items_text or "").strip():
        docs.append(
            Document(
                page_content=f"供应商 {vendor} 的已确认进货明细：\n{items_text}",
                metadata={"vendor": vendor, "kind": "sample", "tenant_id": tid,
                          "memory_id": mid, "source_kind": kind, "created_at": ts},
            )
        )
    # P3-2：对齐 ingest_feedback_memory 写法 —— DB 行已落库成功时，Chroma 故障
    # 只告警降级（向量缺失），不得向上抛错导致 approve 端点在单据已批准后 500。
    # P0-T4：写入前先过完整性闸门（memory_id + tenant_id 齐备才允许写）。
    if not _vector_write_allowed(mid, tid, docs):
        return vendor
    # P8a-R8：stored_new=False（幂等复用既有 memory_id）时不再直接 return ——
    # 复用分支由 _sync_memory_vectors 补「确保向量存在」（缺则补写并留痕）。
    _sync_memory_vectors(tid, mid, vendor, docs, reusing=not stored_new)
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
    # W11-1：两侧归一后都须 >=2 才允许模糊/别名放行。单字查询（实测 '記' / '行' / '菜'）
    # 会与大批他商名互为子串（'記' in '聯記號'）、或落进别名组而放行 —— 跨供应商记忆
    # 泄漏入口。此处单点收口，让子串 / 核心词根 / 别名组三条分支共享同一最小长度口径
    # （不在三处各写一遍该判据，避免同一根因多份实现分叉）；两侧均 >=2 时其余分支
    # 行为逐值不变（'德利行' vs '德利行 Tak Lee Hong' 等正常别名/后缀匹配不受影响）。
    if len(q) < 2 or len(d) < 2:
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


# 记忆总开关（C1）：settings 键 memory_enabled，缺省 True = 与接线前行为一致。
# 与 vendor_precheck 的开关同一读取纪律：读 db.get_app_setting 的**原始值**，
# 因为 get_bool 拿不到「未设置」这一档；解析复用 settings_service.coerce_bool_raw
# （唯一词表实现），避免在消费方再抄一份布尔词表。
MEMORY_ENABLED_KEY = "memory_enabled"


def memory_enabled() -> bool:
    """记忆注入总开关：settings 键 memory_enabled（缺省 True）。

    why 读 db.get_app_setting 原始值而不是 settings_service.get_bool：get_bool 在
    「键未设置」时返回 SETTINGS_DEFAULTS 的缺省值，与「管理台显式设为同值」不可区分
    —— 本键缺省恰好是 True，一旦走 get_bool，"未设置"与"显式开启"就完全同形，
    后续任何想表达"仅在显式设置时才生效"的语义都会落空（vendor_precheck 已踩过
    这个坑的镜像面：那里缺省 False，get_bool 会把环境变量兜底短路掉）。读原始值才能
    区分三态：None/空 = 未设置 → 缺省 True；有值 → 按布尔词解析（词表复用
    settings_service.coerce_bool_raw，不在本模块再抄一份）。

    读取失败（无 DB / 表缺失 / 解析入口不可用）一律视为**开启**：配置系统故障不得
    静默改变识别行为，否则一次 app_settings 读异常就会让全部记忆注入无声消失
    （本仓③类缺陷形态）。
    """
    try:
        raw = db.get_app_setting(MEMORY_ENABLED_KEY)
        from app.services import settings_service
        return settings_service.coerce_bool_raw(raw, True)
    except Exception as e:
        logger.warning("[rag] read %s failed, treat memory as enabled: %s",
                       MEMORY_ENABLED_KEY, e)
        return True


# 记忆语料 PII 脱敏开关（W-9）：settings 键 memory_pii_mask_enabled，缺省 False。
# 缺省关闭 = 与接线前行为逐字节一致（不改动既有写入内容）；开启后写入路径在**落库前 +
# 写向量前**对 notes/sample 做确定性脱敏。
MEMORY_PII_MASK_KEY = "memory_pii_mask_enabled"

# 脱敏工具实例（唯一实现 = ai_registry.tools.pii_masker.v1_0_0）；惰性构造一次并复用。
_pii_masker = None


def _pii_masker_tool():
    """PII 脱敏工具实例（**不另写一套正则实现**，直接复用 ai_registry 既有工具）。

    惰性 import + 缓存：脱敏只在开关开启时用到，未开启的部署（缺省）不加载 ai_registry，
    也不引入 import 期副作用（与 extract_chain / api_receipts 的 ai_registry 惰性加载同款）。
    """
    global _pii_masker
    if _pii_masker is None:
        from ai_registry.tools.pii_masker.v1_0_0 import PIIMaskerTool
        _pii_masker = PIIMaskerTool()
    return _pii_masker


def _pii_mask_enabled() -> bool:
    """记忆语料 PII 脱敏开关：settings 键 memory_pii_mask_enabled（缺省 False）。

    读取失败（无 DB / 表缺失 / settings 入口不可用）一律视为**关闭**：脱敏是合规加固，
    其缺省态就是「不改动既有写入内容」—— 配置系统故障时不得反过来擅自改写记忆语料
    （与 memory_enabled 故障时视为开启的方向相反，因为两者缺省态不同）。
    """
    try:
        from app.services import settings_service
        return settings_service.get_bool(MEMORY_PII_MASK_KEY, False)
    except Exception as e:
        logger.warning("[rag] read %s failed, treat PII mask as disabled: %s",
                       MEMORY_PII_MASK_KEY, e)
        return False


def _mask_memory_text(text, *, field: str, vendor=None) -> str:
    """记忆语料 PII 脱敏（写入侧**唯一实现**）：开关关闭时逐字节原样返回。

    契约：
    - 关闭（缺省）→ 返回入参的**逐字节等值**文本（含 None → ""，与既有 `notes or ""`
      落库形态一致），调用方按原样落库/写向量，零行为变化；
    - 开启 → 复用 `ai_registry.tools.pii_masker.v1_0_0` 做确定性脱敏（HKID / 私人手机 /
      银行卡三类），并按**命中的 PII 类型计数**打 logger.info 留痕（只记类型与条数，
      **不打印原文**，避免把 PII 从语料搬到日志）。
    计数用的是工具自带的已编译 pattern（`findall`），不是另写一份正则 —— 脱敏逻辑与
    类型口径都以工具为唯一事实源。
    只作用于**记忆语料**（notes/sample），不改动单据本身的 receipts / receipt_items。
    """
    s = text if isinstance(text, str) else ("" if text is None else str(text))
    if not _pii_mask_enabled():
        return s
    tool = _pii_masker_tool()
    hits = {
        "hkid": len(tool.hkid_pattern.findall(s)),
        "phone": len(tool.phone_pattern.findall(s)),
        "bank": len(tool.bank_pattern.findall(s)),
    }
    masked = tool.execute(s)
    hits = {k: v for k, v in hits.items() if v}
    if hits:
        logger.info("[rag] memory PII masked field=%s vendor=%r counts=%s",
                    field, vendor, hits)
    return masked


# -------------------------------------------------------------
# P2b（生成侧载荷）：结构化事实 → 可注入先验文本
# why 需要这一层：P3 已把结构化事实（fact_kind / fact_key / fact_value）真正落库，但读侧
# 一直只认 notes / sample 两段自由文本 —— 而 sample 历史上是"已确认整单明细"，把它倒进
# 生成 prompt 有两条实证风险：① anchoring（模型照抄上一单，把上单内容当本单结果）；
# ② 历史错误被反复复制（live 语料里冻存过一条**已不存在的重复错误行**：旧 sample 里
# `ANIKAR A雞粉` 出现两次，而该单当前明细无重复）。故把"默认载荷"换成结构化事实，
# 明细级对照留给复核侧（api_admin.py 的 /api/vendor_price_hint 通道）。
# -------------------------------------------------------------
# 四种事实 kind → 中文标签。kind 白名单的唯一权威定义在 `memory_facts.ALLOWED_KINDS`，
# 这里只负责"怎么显示"；键与那份 schema 对齐，不在本模块再抄一份白名单（本仓①类缺陷）。
_FACT_KIND_LABELS = {
    "alias": "别名",
    "unit": "单位习惯",
    "layout": "版式",
    "price_range": "价格区间",
}


def _is_structured_fact_row(row) -> bool:
    """该记忆行是否携带可注入的结构化事实载荷（判据：fact_kind 与 fact_value 都非空）。

    why 必须两者齐备：只有 kind 没有 value 的行（迁移中途 / 上游写了半条）不是"一条
    事实"——渲染出来是个没有内容的空标签；更糟的是它会让下游按"有结构化事实"处理，
    把同供应商可用的旧式自由文本一并挡掉，等于用一条空事实顶掉全部可用记忆。

    status='active' / 未过期 / 同键去重三道读侧治理门由 `db.list_vendor_memory` 的默认
    （注入口径）完成 —— 本函数只判形态，不重复实现治理门（同一判据两份实现＝本仓①类
    缺陷，两处口径分叉后"该停注的没停注"不会有任何报错）。
    """
    if str(row.get("fact_kind") or "").strip() == "":
        return False
    return str(row.get("fact_value") or "").strip() != ""


def _fact_key_subject(fact_key) -> str:
    """从 fact_key 里取"事实主语"（`<前缀>:<主语>` 形态的第二段）。

    写入侧（seed / 抽取）的键形态是 `alias:本吧` / `unit:本地牛腩` / `layout:doc_form` /
    `price_range:菜心`。取不出第二段时回落整键 —— 绝不因为键形态不合预期就把内容丢掉
    （静默丢内容正是本仓③类缺陷）。
    """
    s = str(fact_key or "").strip()
    if not s:
        return ""
    head, sep, tail = s.partition(":")
    if sep and tail.strip():
        return tail.strip()
    return s


def _render_fact_row(row) -> str:
    """一条结构化事实行 → 可注入的一行文本；非结构化行返回空串。

    why 渲染成"标签：主语 值"而不是直接吐 fact_value：下游是多模态识别模型，它需要知道
    "这个值是干什么用的"。裸值 `本地牛腩` 会被读成又一条待识别明细，而
    `别名：本吧 → 本地牛腩` 才是一条先验。四种 kind 的显示形态：
      alias       别名：<写法> → <标准名>
      unit        单位习惯：<标准名> = <单位>
      layout      版式：<doc_form>
      price_range 价格区间：<标准名> = <低-高>
    未知 kind 回落显示 kind 字面量（白名单外的 kind 不该出现，但真出现了也要看得见，
    而不是被静默丢弃）。
    """
    if not _is_structured_fact_row(row):
        return ""
    kind = str(row.get("fact_kind") or "").strip()
    value = str(row.get("fact_value") or "").strip()
    if kind == "unit":
        # 工作项①（收尾波次）：读侧覆盖存量 vendor_memory 行的原文值（免迁移）——
        # 渲染前过规范写法（「司馬斤」→「斤」），幂等，对已规范值 NOOP。缺省表
        # 缺失/坏 YAML 时 SystemExit（与写侧 memory_consolidate 同纪律，不静默降级）。
        from app.services.unit_normalization import canonical_unit
        value = canonical_unit(value)
    subject = _fact_key_subject(row.get("fact_key"))
    label = _FACT_KIND_LABELS.get(kind, kind)
    if kind == "alias":
        return f"{label}：{subject} → {value}" if subject else f"{label}：{value}"
    if kind == "layout":
        # layout 的键是常量 `layout:doc_form`，"主语"是一个列名而不是事实主体，
        # 渲染成 `版式：doc_form = 手寫長單` 会把列名当内容读，故只显示值。
        return f"{label}：{value}"
    if subject:
        return f"{label}：{subject} = {value}"
    return f"{label}：{value}"


def _decay_value(row) -> float:
    """治理信号：记忆行的 decay_score 数值形态（T8 非对称衰减维护的字段）。

    why 单点实现：它同时是「归档阈值门」（`_decay_ok`）与「rerank 打分分项」的输入，
    抄两份会出现「门按一份解析、排序按另一份解析」的分叉（本仓①类缺陷）。
    非法形态由调用方决定如何处置（门侧留痕放行、排序侧由 rerank 兜底），本函数只做转换。
    """
    return float(row.get("decay_score", 1.0))


def _row_payload(row, vendor: str):
    """记忆行 → (可注入文本, 是否结构化事实行)；无可注入内容返回 ("", is_structured)。

    why 单点实现：**同一根因三处使用** —— 精确腿、关键词腿、向量腿的 P2b 判据（结构化
    事实存在时旧式整单明细不进 prompt）都以此为准；抄三份必然出现"改一处漏两处"
    （本仓①类缺陷）。P2b 契约：结构化事实行渲染成 `别名：本吧 → 本地牛腩` 这类先验，
    旧式行保留 `[供应商]\\n notes + sample` 原形态。
    """
    if _is_structured_fact_row(row):
        line = _render_fact_row(row)
        if not line:
            return ("", True)   # 形态齐备却渲染为空：不得占位顶掉其它候选
        return (f"[{row.get('vendor') or vendor}]\n{line}", True)
    parts = []
    if row.get("notes"):
        parts.append(row["notes"])
    if row.get("sample"):
        parts.append(row["sample"])
    if not parts:
        return ("", False)
    return (f"[{row.get('vendor') or vendor}]\n" + "\n".join(parts), False)


def _recall_doc(row, text, leg, relevance, vendor_default, anon_idx=0):
    """记忆行 + 注入文本 → 混合召回候选（带治理信号，供 rerank 消费）。

    治理信号（decay_score / hit_count / last_hit_at）**从行上原样带过去**：它们是 R2
    要求"从只写列变成真消费"的三个字段，也是本函数存在的理由。数值/形态异常由
    `memory_recall` 侧兜底（hit_score / freshness 自己容错），decay_score 在这里容错 ——
    该行进池前**必已经过 `_decay_ok`**（同一行、同一字段），非法形态已在
    RETRIEVE_STAGE_DECAY_PARSE 留痕 + 计数，故此处按满分 1.0 参与排序而**不重复留痕**
    （同一根因两处留痕会让降级计数翻倍、口径失真）。
    """
    mid = str(row.get("memory_id") or "")
    doc_id = mid or f"__anon_{anon_idx}"   # 无 memory_id 的行也给去重键（合成键）
    try:
        decay = _decay_value(row)
    except (TypeError, ValueError):
        decay = 1.0   # 与归档门的 fail-open 同侧（见 docstring：留痕已在门那一侧发生）
    return memory_recall.RecallDoc(
        doc_id=doc_id,
        memory_id=mid,
        text=text,
        leg=leg,
        relevance=float(relevance or 0.0),
        decay_score=decay,
        hit_count=int(row.get("hit_count") or 0),
        last_hit_at=str(row.get("last_hit_at") or ""),
        vendor=str(row.get("vendor") or vendor_default or ""),
    )


def _build_recall_pool(vendor, tid, scoped_rows, query, top_k,
                       use_exact=True, use_keyword=True, use_vector=True):
    """混合召回候选池（P3-T6）：精确/别名腿 + 关键词腿 + 向量腿 → 合并去重前的原始候选。

    三条腿的职责与边界（这是本函数唯一的职责划分说明，三处调用共用）：
    - **精确/别名腿**：`scoped_rows`（scope 已由 `db.list_vendor_memory` 收窄）全部可注入行。
      它是权威命中腿，腿基础分最高。
    - **关键词腿（必选腿）**：在同一 scope 语料上做 BM25 近似（`memory_recall.bm25_scores`），
      只把**命中（分 > 0）**的行放进池子。why 必选：语料是短商品名 + 供应商名 + 单位，
      且缺省嵌入档是无语义能力的 char-ngram 哈希；BM25 的 idf 能压掉"每条记忆都有的
      '供应商 X 的已确认进货明细：'前缀"，让商品名/单位这类稀有 token 主导排序。
      **向量腿是 k 截断的**（Chroma 只返 top_k），关键词腿对全语料打分 —— 两者互补正是
      混合召回相对纯向量在召回上的增量来源（验收用例的命中率对比就测这一点）。
    - **向量腿**：Chroma 相似度检索；**scope 门 = `scoped_rows` 的 memory_id 集合**
      （读侧 scope 强制收窄，要求③：拿得到 supplier_id 时它就是锚点行，名字只作回退 ——
      越权 scope 的向量即使被相似度检索返回也不得进池）。相关度用**排名代理**
      `(k - rank)/k`：Chroma 的 L2 距离语义随嵌入档变化、不可跨档比较，故不把它当绝对
      相似度用（避免了"256 维档与 1024 维档给出不可比的数"）。

    P2b 判据（结构化事实存在 → 旧式整单明细不进 prompt）在本函数内**单点判定**，三条腿
    共用：`has_structured`（=存在非空渲染的结构化事实行）与 `structured_mid`（逐行是否
    结构化，供向量腿回流判定）都由本函数从 `scoped_rows` 现算，调用方不需要、也无法传入
    另一套口径（避免"同一根因两条腿两套判据"，本仓①类缺陷）。

    三个 `use_*` 形参的**唯一消费方**是验收用例 `tests/test_memory_hybrid_recall.py`：
    它用 `use_exact=False, use_keyword=False` 复现「纯向量腿」基线、用 `use_keyword=False`
    复现「改造前读路径（精确腿 + 向量腿）」基线、再用缺省值测「混合召回」，最后以"关掉
    关键词腿"作反证（要求⑤）。生产调用点（`retrieve_context`）不传任何 `use_*`，三条腿
    恒开 —— 它们不是产品开关，故不进管理台也不读配置（避免出现"配置项读了没用途"）。

    返回 (pool, stats)；stats 只用于日志与验收观测，不参与任何判据。
    """
    pool = []
    stats = {"structured": 0, "legacy": 0, "blocked_legacy": 0,
             "keyword_hits": 0, "vector_hits": 0, "has_structured": False}

    rendered = []   # [(row, 文本, 是否结构化)]
    for row in scoped_rows:
        text, is_structured = _row_payload(row, vendor)
        if is_structured and text:
            stats["has_structured"] = True
            stats["structured"] += 1
        rendered.append((row, text, is_structured))
    has_structured = stats["has_structured"]
    structured_mid = {}
    for row in scoped_rows:
        mid = str(row.get("memory_id") or "")
        if mid:
            structured_mid[mid] = _is_structured_fact_row(row)

    payloads = []
    for idx, (row, text, is_structured) in enumerate(rendered):
        if is_structured and not text:
            continue   # 形态齐备却渲染为空：不占位顶掉其它候选
        if not is_structured and has_structured:
            stats["blocked_legacy"] += 1
            continue
        payloads.append((idx, row, text, is_structured))
    stats["legacy"] = sum(1 for _i, _r, _t, s in payloads if not s)

    # 关键词腿：同一语料上的 BM25（打分一次，精确腿直接复用它作为"该腿相关度"，
    # 避免同一批文档被打两次分 —— 同一算法两处调用正是①类缺陷的温床）。
    kw = {}
    if use_keyword and payloads:
        try:
            kw = memory_recall.bm25_scores(
                query, [(str(i), text) for i, _row, text, _s in payloads])
        except Exception as e:
            _note_retrieve_failure(RETRIEVE_STAGE_KEYWORD, e, vendor, tid)

    for i, row, text, _is_structured in payloads:
        if use_exact:
            pool.append(_recall_doc(row, text, memory_recall.RECALL_LEG_EXACT,
                                    kw.get(str(i), 0.0), vendor, anon_idx=i))
    if use_keyword:
        for i, row, text, _is_structured in payloads:
            if kw.get(str(i), 0.0) <= 0.0:
                continue
            pool.append(_recall_doc(row, text, memory_recall.RECALL_LEG_KEYWORD,
                                    kw[str(i)], vendor, anon_idx=i))
            stats["keyword_hits"] += 1

    if use_vector:
        # scope 门（要求③）**唯一实现** = "该 memory_id 在 scope 行集合里"：只有 scope 行
        # 才带得到治理信号（row_by_mid 的键集恰为 scope_mids），故相似度检索返回的越权
        # 向量在这里被拦下（"向量检索返回" ≠ "可以进 prompt"）。why 不另写一个
        # `dmid in scope_mids` 判断：那是同一判据的第二份实现，两处口径分叉后
        # "该拦的没拦"不会有任何报错（本仓①类缺陷）。
        row_by_mid = {str(r.get("memory_id")): r for r in scoped_rows if r.get("memory_id")}
        try:
            results = _store(tid).similarity_search(query, k=top_k)
            for rank, d in enumerate(results):
                if not d.page_content.strip():
                    continue
                dmid = d.metadata.get("memory_id")
                row = row_by_mid.get(str(dmid)) if dmid else None
                if row is None:
                    continue   # 越权 scope / 不可回溯的向量一律不进池
                # P2b：该供应商已有结构化事实时，其**旧式整单明细**不得经向量通道回流
                # （向量 page_content 就是写库时的 content，ingest_memory 路径即整单明细）
                if has_structured and not structured_mid.get(str(dmid)):
                    continue
                doc = _recall_doc(
                    row, f"[{d.metadata.get('vendor')}] {d.page_content}",
                    memory_recall.RECALL_LEG_VECTOR,
                    max(0.0, 1.0 - rank / float(max(1, top_k or 1))), vendor)
                pool.append(doc)
                stats["vector_hits"] += 1
        except Exception as e:
            # 相似度兜底通道整体失败（向量库检索任一环）→ 该通道零候选。这是**最容易
            # 长期隐形**的一处：向量层出问题时返回值退化成纯精确/关键词腿的上下文，
            # 看不出任何异常（gap H2 那批「179 条向量全不参与检索却无人知」即此形态）。
            _note_retrieve_failure(RETRIEVE_STAGE_FUZZY, e, vendor, tid)
    return pool, stats


def retrieve_context(vendor: str, top_k: int = 3, *,
                     tenant_id: str,
                     budget: Optional[MemoryBudget] = None) -> str:
    """识别前检索 vendor_context：先精确匹配该供应商，再按相似度兜底。

    tenant_id：**必填**（keyword-only），用作 Chroma 租户硬隔离过滤；DB 侧按同一
    值过滤，保证读写同口径。刻意不给默认值 —— 之前缺省 "default" 导致生产读点
    「忘记传租户」时静默落回 default：写进 t2 的记忆在识别时读不到，而 default 桶
    的记忆会被注入给任意租户（Gap H1）。改必填后「忘记传」在开发期直接 TypeError。
    Gap B1：只注入 status='active' 的记忆（列语义闭环，淘汰动作本身归 T8）。
    P2-T1 主体锚点：先由 vendor 名解析 supplier_id（db.resolve_supplier_id，与写入侧
    同一实现），DB 侧优先按该 id 取行 —— 同一供应商的繁简/简称/后缀/中段英文注写法
    因此命中同一条记忆，且互相包含的不同商号（"大生"/"大生隆"）不再互相放行。
    解析不出 supplier_id 时按原来的 vendor 名口径匹配（行为不变）。**签名不变**：
    调用方仍只传 vendor + tenant_id，锚点解析是内部实现细节，不新增形参（避免出现
    "新增形参没人传值"的接口债）。
    Gap B4：注入前按 MemoryBudget 预算控制 —— 单条截断 per_item_tokens、
    最多 max_items 条、累计不超 facts_tokens；实际注入的记忆行回写命中
    记账（hit_count / last_hit_at）。
    冷启动供应商（无记忆且无同名 fuzzy 命中）返回空串，绝不携带他商记忆。
    C1 总开关：memory_enabled=False 时在入口直接返回空串 —— **不查记忆表、不查
    向量、不记 hit**（零开销），也不抛异常、不阻断识别。
    U2 可观测（本函数的每一处降级都可见）：预算 / 归档阈值 / 衰减分解析 / rerank 权重 /
    精确通道 / 关键词腿 / 相似度兜底 / 命中记账八处（另加 MemoryBudget.from_settings 的
    预算设置读取一处）的异常**一律 fail-open**（仍不抛、不阻断、返回值与加日志前逐值一致），
    但每次降级都会打 warning 并让 `get_retrieve_failure_counts()` 的对应阶段 +1。why：这些
    except 原先全是 `pass`，于是「检索坏了」与「真的没有可用记忆」在返回值上不可区分
    （都是空串）。**fail-open 的唯一例外是 scope 不可确定**（见下方 P3-T6 的第 3 点）。
    P2b（生成侧载荷，本段是该契约在注入内容上的**唯一**一次刻意变更）：精确/别名通道
    优先取**结构化事实行**并渲染成可注入先验（见 `_render_fact_row`），该供应商存在
    结构化事实时不再把旧式自由文本（notes/sample，含"已确认整单明细"）倒进 prompt；
    只有该供应商没有任何结构化事实时才回落旧式路径（存量记忆不会因本次改造而无人可用）。
    三条腿（精确 + 关键词 + 向量）共用同一条判据：结构化事实存在 → 旧式明细一律不进
    prompt（否则明细会经向量通道回流 —— 同一根因只改一处正是本仓①类缺陷）。
    P3-T6（混合召回 + 治理信号 rerank + 读侧 scope 收窄）：
      1. 召回＝三条腿合并去重（精确/别名腿、关键词腿 BM25、向量腿 Chroma），见
         `_build_recall_pool`；关键词腿是必选腿（语料是短商品名 + 供应商名 + 单位）。
      2. 排序＝`memory_recall.rerank`，排序分含「腿基础分 + 该腿相关度（关键词腿 BM25 /
         向量腿排名代理）+ 精确匹配 boost（供应商名归一相等）+ decay_score 加权 +
         hit_count 微权重 + last_hit_at 新鲜度」，权重可配（settings 键
         `memory_recall_weights`）可审计（DEBUG 日志打出权重与逐条分项）。
      3. 读侧 scope **强制收窄**：tenant 必填（见上）+ 拿得到 supplier_id 时按锚点取行
         （名字只作回退）。三条腿都只在该 scope 上召回，越权 scope 不进候选池；
         **scope 查询失败时返回空串（不退回名字口径、更不退回全量）** —— 这是本函数唯一
         的 fail-closed 分支，也是"找不到就退回全量"那类越权形态的反面。
    """
    # C1 总开关先于一切取数动作：关闭即返回空串，连 db.list_vendor_memory /
    # similarity_search / bump_vendor_memory_hit 都不发生（"关了还查库"等于没关）。
    if not memory_enabled():
        return ""
    if not vendor or not str(vendor).strip():
        return ""
    # 租户归一与写侧（ingest_memory / start_recognition_job）同口径：None/空串 →
    # default。不归一的话 None 会在 DB 侧被折成 default、在 _store 侧却走全局
    # collection，读写落到不同桶。
    tid = str(tenant_id or "default").strip() or "default"
    try:
        b = budget or MemoryBudget.from_settings()
    except Exception as e:
        # 预算解析整体失败（from_settings 自身兜底之外的残留路径）→ 用缺省预算继续。
        _note_retrieve_failure(RETRIEVE_STAGE_BUDGET, e, vendor, tid)
        b = budget or DEFAULT_MEMORY_BUDGET
    # T8 非对称淘汰：归档阈值以下（含）的记忆停止注入
    try:
        archive_th = db.memory_archive_threshold()
    except Exception as e:
        # 阈值读取失败 → 回退 0.5。注意这是**安全侧会失效**的降级：阈值取不到时，
        # 「该停注的劣化记忆」可能继续被注入，故必须留痕（U2）。
        _note_retrieve_failure(RETRIEVE_STAGE_ARCHIVE_THRESHOLD, e, vendor, tid)
        archive_th = 0.5

    def _decay_ok(row):
        try:
            return float(row.get("decay_score", 1.0)) > archive_th
        except (TypeError, ValueError) as e:
            # decay_score 形态非法（脏行 / 旧库遗留 / 上游改了行结构）→ 保守放行 True。
            # why 留痕：这是**治理门的 fail-open** —— 本该因劣化而停注的记忆会继续被注入，
            # 而静默会让「衰减机制在最脏的那批行上完全失效」无人发现（与 M2「成本静默清零
            # 让护栏变瞎」同族：给出的是"看起来正常"的放行，没人会去查）。
            _note_retrieve_failure(RETRIEVE_STAGE_DECAY_PARSE, e, vendor, tid)
            return True

    # P3-T6：混合召回的检索 query。**两条腿共用同一句**（关键词腿 BM25 的查询与向量腿的
    # 查询逐字相同），否则"混合召回比纯向量好"就无法归因到召回机制本身。
    query_text = f"供应商 {vendor} 的收据版式与单位习惯"
    # rerank 权重：可配（settings 键 memory_recall_weights）可审计（写进 DEBUG 日志）。
    # 读取失败 → 回退缺省权重并留痕（权重故障不得阻断识别，但也不能静默）。
    try:
        weights = memory_recall.RecallWeights.from_settings()
    except Exception as e:
        _note_retrieve_failure(RETRIEVE_STAGE_RECALL_WEIGHTS, e, vendor, tid)
        weights = memory_recall.DEFAULT_WEIGHTS

    # 0) P2-T1 主体锚点：先把 vendor 名解析成供应商档案 id（解析不出为 None）。
    #    why：名字口径靠子串/核心词猜，同族的真实商号（"大生"/"大生隆"）会互相放行，
    #    而同一供应商的不同写法（繁简/简称/后缀/中段英文注）又未必能靠名字命中；
    #    supplier_id 是档案主键，读写两侧都用它才是同一供应商。解析失败零副作用：
    #    返回 None 后整条链路退化为原有的名字口径（行为不变，不是降级开关）。
    try:
        supplier_id = db.resolve_supplier_id(vendor, tid)
    except Exception as e:
        # why 留痕：退化为名字口径本身是可接受的降级，但静默吞掉会让「锚点长期解析失败」
        # （厂家表改名 / 租户错位 / resolve_supplier_id 自身抛错）永久不可观测 —— 没人会发现
        # 记忆一直在按老口径命中。故先 warning 再置 None，行为不变（仍走名字口径、
        # 不抛错、不返回空），只是故障从此可见。
        logger.warning(
            "[rag] resolve_supplier_id failed, fallback to vendor-name matching: "
            "vendor=%r tenant_id=%r error=%s", vendor, tid, e,
        )
        supplier_id = None  # 解析故障不得阻断识别链路（退化为名字口径）

    # 1) 读侧 scope 强制收窄（要求③，**唯一 scope 实现** = db.list_vendor_memory）：
    #    tenant 是硬门（该函数内部 scoped 过滤，绝不跨租户）；拿得到 supplier_id 时优先
    #    按锚点取行，锚点零命中才回退名字口径。三条腿（精确/关键词/向量）都只在这个
    #    行集合上召回 —— 越权 scope 的行/向量一律不进候选池。
    #    why 不再单独查一次"本租户全部行"当向量腿的允许集：那样向量腿会按**名字**放行
    #    互相包含的不同商号（"大生" 与 "大生隆"），即 scope 没有真正收窄。
    #    降级语义：该查询失败 → 行集合为空 → 三条腿全无候选、返回空串（fail-closed）。
    #    这是刻意的：scope 不可确定时宁可"这次不注入"，也不按名字猜出他商记忆注入。
    scoped_rows = []
    try:
        for row in db.list_vendor_memory(vendor, tenant_id=tid,
                                         supplier_id=supplier_id):
            if not _decay_ok(row):
                continue  # T8：劣化/归档记忆停止注入
            scoped_rows.append(row)
    except Exception as e:
        # 精确/别名通道整体失败 → 零候选。why 必须留痕：这条通道失效与该供应商「真的
        # 没有记忆」在返回值上完全同形 —— 静默会让人永远以为是冷启动，而
        # db.list_vendor_memory 的解析/锚点/映射一旦长期抛错就再也查不出来。
        _note_retrieve_failure(RETRIEVE_STAGE_EXACT, e, vendor, tid)

    # P2b 判据（结构化事实存在 → 旧式整单明细不进 prompt）的三条腿共用口径由
    # `_build_recall_pool` 现算（收窄后的 scope 上判定 —— 他商的结构化行不影响本商判据）。

    # 2) 三腿候选池（精确 + 关键词 + 向量）→ 合并去重 → 治理信号 rerank
    pool, stats = _build_recall_pool(vendor, tid, scoped_rows, query_text, top_k)
    ranked = memory_recall.rerank(query_text, pool, weights=weights,
                                  query_vendor=vendor)
    has_structured = stats["has_structured"]

    if has_structured:
        if stats["blocked_legacy"]:
            # 结构化路径下旧式明细行被主动挡下，必须留痕：否则"某供应商的明细记忆突然
            # 不再注入"会被当成故障去查（实际是本机制的设计结果，不是丢数据）。
            logger.info(
                "[rag] 生成侧载荷走结构化事实路径：vendor=%r tenant=%r "
                "结构化 %d 条 / 挡下旧式自由文本 %d 条（整单明细不进生成 prompt）",
                vendor, tid, stats["structured"], stats["blocked_legacy"])
    elif stats["legacy"]:
        # 旧式路径同样留痕：否则"结构化事实机制没生效"与"这个供应商本来就只有旧式记忆"
        # 在日志上不可区分。
        logger.info(
            "[rag] 生成侧载荷走旧式自由文本路径（该供应商无结构化事实）："
            "vendor=%r tenant=%r %d 条", vendor, tid, stats["legacy"])

    if ranked:
        # 可审计（要求②）：权重与逐条候选的分数分项一次性打在 DEBUG。why DEBUG 而不是
        # INFO：这是排查排序问题时才需要的明细，不是日常运维噪音；但**不得没有** ——
        # 权重黑箱正是"治理信号说消费了其实看不出怎么消费的"的温床。
        logger.debug(
            "[rag] recall rerank vendor=%r tenant=%r weights=%s legs=%s scores=%s",
            vendor, tid, weights.as_dict(),
            {"keyword_hits": stats["keyword_hits"], "vector_hits": stats["vector_hits"]},
            json.dumps(memory_recall.explain(ranked[:b.max_items]),
                       ensure_ascii=False))

    # 3) 预算装配：按 rerank 分数降序取前若干条（单条截断 + 条数上限 + 总量上限）；
    #    被真正注入的行回写命中记账（要求④：直接复用 db.bump_vendor_memory_hit ——
    #    它同时自增 hit_count、刷新 last_hit_at 并按 T8 加 decay bonus，本模块**不另写
    #    一份记账实现**）。
    #    why 顺序由分数决定而不是"精确通道在前、fuzzy 接在后面"：rerank 已经把治理信号
    #    （decay_score / hit_count / last_hit_at）与关键词相关度折算成分数，预算该沿着
    #    "哪条记忆更值得注入"来截断，而不是沿着"它来自哪条腿"。
    used_texts = []
    used_tokens = 0
    seen_mids = set()
    hit_ids = []
    sep = "\n\n"
    for score in ranked:
        text = score.doc.text
        mid = score.doc.memory_id
        if len(used_texts) >= b.max_items:
            break
        if not text or not text.strip():
            continue
        if mid and mid in seen_mids:
            continue  # 同一记忆不重复注入（多腿合并后的第二道防线）
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
        except Exception as e:
            # 命中记账失败不阻断识别链路（注入内容此刻已定，记账只影响治理视图 /
            # 衰减判据）。但静默会让「hit_count 长期不涨」被误当成「记忆一直没命中」，
            # 故留痕 + 计数。
            _note_retrieve_failure(RETRIEVE_STAGE_HIT_ACCOUNTING, e, vendor, tid)
    return ctx


def ingest_feedback_memory(vendor: str, comment: str, quality_warnings=None,
                           tenant_id: str = "default", source_receipt_ids=None):
    """FR-9 三次连续点踩提炼（T8 人工闸语义）：蒸馏产物入待确认队列。

    Gap D1：不再自动写入 vendor_memory / Chroma —— 先落 pending_memory
    （status=pending，带 source_receipt_ids 可回溯），经人工 approve 后才走
    既有 upsert 链路生效（approve_pending_to_memory）。单次点踩依旧不沉淀。
    返回蒸馏内容文本（保持既有调用方契约）。
    """
    tid = str(tenant_id or "default").strip() or "default"
    qw = quality_warnings or []
    content = f"供应商 {vendor} 反馈纠偏：{comment}".strip()
    if qw:
        content += f"\n关联质量告警：{'; '.join(qw)}"
    try:
        db.enqueue_pending_memory(
            vendor, content, tenant_id=tid,
            source_kind="feedback_distilled",
            source_receipt_ids=source_receipt_ids or [])
    except Exception as e:
        logger.warning("[rag] enqueue pending memory failed vendor=%s: %s", vendor, e)
    return content


def _pending_memory_fact(vendor, tid, kind, ref, supplier_id,
                         fact_kind, fact_key, fact_value, written_by=""):
    """待审行的字段级载荷 → MemoryFact；载荷不齐或不合法时返回 None（回落自由文本）。

    为什么三者（kind / key / value）齐备才算结构化：pending 表刻意不给这五列设缺省，
    旧行（反馈蒸馏路径）与半成品载荷都可能是 NULL / 空串 —— 只给其中一两个就强行走
    save_memory_fact，会写出一条字段残缺的事实（例如有 kind 无 value），那比自由文本
    更糟：它看起来像一条合法结构化事实，却不参与任何按 kind 的判据。

    过 `validate_fact`（写入侧唯一闸门）：**本函数是写方，必须自己校验**，不能假设
    "入队时一定筛过"（旧 pending 行就可能带白名单外的 kind、或 supplier_id 为 NULL）。
    非法候选一律 logger.warning 后回落自由文本 —— 记录该记（不静默丢弃整条 approve），
    只是降级为不加锚点的自由文本，符合 I2「降级可接受、静默不行」。

    trust_level 刻意留 MemoryFact 缺省 1.0：人在人工闸点了"批准"，这条事实即人工确认
    来源（与 source_kind='extract' 不矛盾 —— 抽取只是它的**产生方式**，可信度由批准
    动作授予）。pending 表没有 trust_level 列，模型侧的置信度在这一跳已不可得，凭空
    填一个中间值反而是编造。

    written_by（U1 审计链）：**谁批准写入的**。它不是事实内容，而是审计列，故从调用方
    一路透传到这里（`approve_pending_to_memory` 的 `written_by` 形参，由 api_memory 的
    操作者身份填充）。缺省空串 = 旧调用方不传时保持既有行为（落空值），不编造"系统"。
    """
    k = str(fact_kind or "").strip()
    key = str(fact_key or "").strip()
    val = str(fact_value or "").strip()
    if not (k and key and val):
        return None
    if k == "unit":
        # 工作项①（收尾波次）：approve 期写入防线 —— 旧 pending 行可能仍存模型原文
        # （写侧规范上线前入队的），落库前再过一次规范写法（幂等，对已规范值 NOOP）。
        # 只改写法不改语义，人工闸的批准动作本身零改动。
        from app.services.unit_normalization import canonical_unit
        val = canonical_unit(val)
    from app.services.memory_facts import MemoryFact, validate_fact
    fact = MemoryFact(
        kind=k, fact_key=key, value=val,
        supplier_id=("" if supplier_id is None else str(supplier_id)),
        tenant_id=tid, source_kind=kind, source_ref=ref,
        written_by=str(written_by or "").strip(),
    )
    ok, reason = validate_fact(fact)
    if not ok:
        logger.warning(
            "[rag] pending 结构化载荷过不了写入闸门，回落自由文本落库 "
            "vendor=%s kind=%s key=%s：%s", vendor, k, key, reason)
        return None
    return fact


def _register_alias_from_approved_fact(fact, tid, vendor, mid, who):
    """L1：人工批准的 alias 事实 → 供应商作用域别名表登记（人工闸之后的确定性复利面）。

    铁律：本函数只在 approve（人工闸）成功落库后被调；save_edited / 抽取入队路径
    零接触。任何失败只留痕、绝不阻断 approve 主链路（vendor_memory 行已落，别名
    登记是增益不是前置）。零向量操作（D8）。

    关键参数决策：
    - `protect_live_target=False`（改指语义）：与 backfill/合并路径一致（upsert 缺省
      即改指）。别名事实经人工闸批准，是比"自动新建 SKU"更强的信号；同一写法此后被
      人工纠正到另一 SKU 时应改指。W1 的 `protect_live_target=_created_sku`
      （services/inventory.py）只防"本次自动新建的重复 SKU 自我推翻"，本钩子不新建
      SKU，无该风险。
    - `source_ref="memory:<mid>"`：落 `vendor_sku_alias.source_ref`，可回溯到批准的
      vendor_memory 行（W1 用 `receipt:<id>`，同格式约定）。
    - SKU 解析不做 `find_sku_by_name` 兜底：它是 `.first()`，重名时命中哪个取决于
      扫描序 —— 歧义判定必须用 `list_skus` 全量比对（唯一才登记）。
    """
    try:
        tid = str(tid or "default").strip() or "default"
        # 门 0：mid 为空则 skip —— 防落出 source_ref="memory:" 的残缺别名行。
        mid_s = str(mid or "").strip()
        if not mid_s:
            _audit_alias_skip(tid, None, vendor, "", "", "INVALID_MEMORY_ID",
                              None, 0, who)
            return
        # 门 1：只处理 alias 事实 —— 非 alias 四类事实与自由文本分支零影响。
        if fact is None or str(getattr(fact, "kind", "") or "").strip() != "alias":
            return
        # 门 2：raw = `alias:<写法>` 的写法段（别名表的 raw_name）。
        raw = _fact_key_subject(getattr(fact, "fact_key", None))
        # 门 3：value = 标准品名。MemoryFact 的领域字段名是 `value`（行字段名是
        # fact_value），两者都认 —— 按行字典构造的形态也不丢。
        value = str(getattr(fact, "value", None)
                    if getattr(fact, "value", None) is not None
                    else getattr(fact, "fact_value", None) or "").strip()
        sid_s = str(getattr(fact, "supplier_id", "") or "").strip()
        if not raw:
            _audit_alias_skip(tid, None, vendor, "", value, "INVALID_RAW_NAME",
                              mid_s, 0, who)
            return
        if not value:
            _audit_alias_skip(tid, None, vendor, raw, "", "INVALID_VALUE",
                              mid_s, 0, who)
            return
        # 门 4：不解析 vendor 名兜底 —— pending 行没带锚点说明候选产生时单据未锚定，
        # 名字模糊解析会把记忆侧的松口径（_vendor_related 放行形态）泄漏进严格按
        # supplier_id 收窄的别名表。
        try:
            sid = int(sid_s)
        except (TypeError, ValueError):
            _audit_alias_skip(tid, None, vendor, raw, value, "NO_SUPPLIER",
                              mid_s, 0, who)
            return
        # 门 4b：供应商锚点存在性/归属校验。upsert_vendor_sku_alias 只校验 SKU，
        # 不校验 supplier_id —— 不加这道门，死锚点/跨租户锚点会落出别名行且零留痕。
        if db.get_supplier(sid, tenant_id=tid) is None:
            _audit_alias_skip(tid, sid, vendor, raw, value, "SUPPLIER_NOT_FOUND",
                              mid_s, 0, who)
            return
        # 门 5：SKU 唯一性门（list_skus 缺省 active=1 同租户）。
        candidates = [s for s in db.list_skus(tenant_id=tid)
                      if (getattr(s, "name", "") or "") == value]
        if len(candidates) == 0:
            _audit_alias_skip(tid, sid, vendor, raw, value, "SKU_NOT_FOUND",
                              mid_s, 0, who)
            return
        if len(candidates) > 1:
            _audit_alias_skip(tid, sid, vendor, raw, value, "SKU_AMBIGUOUS",
                              mid_s, len(candidates), who)
            return
        # 延迟导入（同 services/inventory.py 风格），避免模块级新依赖。
        from app.services.receipt_utils import _normalize_sku_name
        alias_id, err = db.upsert_vendor_sku_alias(
            supplier_id=sid, sku_id=candidates[0].id, raw_name=raw,
            canonical_name=_normalize_sku_name(raw), unit="", tenant_id=tid,
            source_ref="memory:%s" % mid_s, protect_live_target=False)
        if err is None:
            logger.info(
                "[rag] 批准别名事实已登记 vendor_sku_alias id=%s supplier=%s "
                "raw=%r sku=%s", alias_id, sid, raw, candidates[0].id)
            _audit_alias_registered(tid, sid, vendor, raw, candidates[0].id,
                                    mid_s, who)
        else:
            # TARGET_PROTECTED 理论不可达（本钩子恒传 protect_live_target=False），
            # 出现按 warning 留痕；其余 err 与 skip 同口径留痕。
            if err == "TARGET_PROTECTED":
                logger.warning(
                    "[rag] 别名登记命中 TARGET_PROTECTED（理论不可达）vendor=%s "
                    "key=%s", vendor, getattr(fact, "fact_key", None))
            _audit_alias_skip(tid, sid, vendor, raw, value, err, mid_s,
                              len(candidates), who)
    except Exception as e:
        logger.error(
            "[rag] alias→vendor_sku_alias 登记失败（不阻断 approve）vendor=%s "
            "key=%s: %s", vendor, getattr(fact, "fact_key", None), e)


def _audit_alias_registered(tenant_id, supplier_id, vendor, raw, sku_id, mid, who):
    """登记成功的系统审计（失败只告警，不阻断 approve）。"""
    try:
        db.append_system_audit_log(who or "approve_pending_to_memory",
                                   "memory_alias_registered", "vendor_sku_alias",
                                   "", json.dumps({
                                       "tenant": tenant_id,
                                       "supplier_id": supplier_id,
                                       "raw_name": raw,
                                       "sku_id": sku_id,
                                       "memory_id": mid,
                                   }, ensure_ascii=False))
    except Exception as e:
        logger.warning("[rag] memory_alias_registered 审计失败: %s", e)


def _audit_alias_skip(tenant_id, supplier_id, vendor, raw, value, reason, mid,
                      candidates, who):
    """未登记（各前置门 / upsert 失败）的系统审计（失败只告警，不阻断 approve）。"""
    try:
        db.append_system_audit_log(who or "approve_pending_to_memory",
                                   "memory_alias_skip", "vendor_sku_alias",
                                   "", json.dumps({
                                       "tenant": tenant_id,
                                       "supplier_id": supplier_id,
                                       "raw_name": raw,
                                       "value": value,
                                       "reason": reason,
                                       "memory_id": mid,
                                       "candidates": candidates,
                                       "vendor": vendor,
                                   }, ensure_ascii=False))
    except Exception as e:
        logger.warning("[rag] memory_alias_skip 审计失败: %s", e)


def approve_pending_to_memory(vendor: str, content: str, tenant_id: str = "default",
                              source_receipt_ids=None, source_kind=None,
                              supplier_id=None, fact_kind=None, fact_key=None,
                              fact_value=None, written_by=None):
    """T8：人工 approve 后把待确认记忆落 vendor_memory + Chroma（治理元数据齐全）。

    来源不再硬编码（P3fix-a 审核 M2）：pending 行带着自己的 source_kind（反馈蒸馏 /
    LLM 抽取 …），由 approve 端点透传。why：source_kind 是 trust_level 与「低可信
    不得覆盖高可信」防投毒判定的输入（memory_consolidate.decide_action），把抽取候选
    记成用户反馈会让下游判据建立在错的来源上。缺省 None 仍回落 feedback_distilled
    （兼容 `ingest_feedback_memory` 与旧调用方的既有契约，行为零变化）。

    结构化载荷：待审行带齐 fact_kind / fact_key / fact_value 时走 `db.save_memory_fact`
    落库 —— 它按 (tenant, vendor, fact_key) 做 supersede 归并，并把五列载荷（含
    supplier_id 锚点、trust_level、valid_*）真正写进 vendor_memory；载荷不齐或过不了
    `validate_fact` 时回落既有自由文本路径（兼容旧 pending 行，且降级有 warning 留痕）。

    J3 锚点：pending_memory 表已补 supplier_id 列（K2），故本链路**直接透传**调用方给的
    权威 id —— 此前那段"由 db 侧按 vendor 名解析"的说明是 P3-T1 落地前的状态，已作废。

    U1 审计链（written_by）：调用方（api_memory 的 approve 端点）把**操作者身份**透传
    进来，本函数两条落库分支都写它 —— 结构化分支经 `MemoryFact.written_by` → 载荷列，
    自由文本分支经 `_persist_memory_row` 同名列落库。why 两条都要写：approve 的两种
    待审行（LLM 抽取候选 / 旧反馈蒸馏行）都会在治理视角里被追问"这条是谁批的"，只覆盖
    一条＝另一条路径的审计链仍是断的（本仓高发的「修 A 漏 B」）。缺省 None 表示调用方
    未提供（旧调用方）→ 落空值，行为与新增形参前一致，**不编造**"system"之类的写入者。

    P0-T4：与 ingest_memory 同口径 —— DB 落库失败 → logger.error + 跳过向量写入；
    写向量前过 `_vector_write_allowed`（两条链路共用同一实现，避免只修一处）；
    Chroma 故障只告警降级（DB 行为准）。
    P8a-R8：与 ingest_memory 同口径 —— 幂等去重命中（stored_new=False）时不再直接
    return，交由 `_sync_memory_vectors(reusing=True)` 先查后补（DB 有行 ⇒ 向量存在）。
    """
    tid = str(tenant_id or "default").strip() or "default"
    kind = str(source_kind or "").strip() or "feedback_distilled"
    ref = json.dumps([str(r) for r in (source_receipt_ids or [])])
    ts = datetime.now().isoformat()
    wb = str(written_by or "").strip()
    fact = _pending_memory_fact(vendor, tid, kind, ref, supplier_id,
                                fact_kind, fact_key, fact_value, written_by=wb)
    if fact is not None:
        try:
            saved = db.save_memory_fact(tid, vendor, fact.fact_key,
                                        notes=content or "", fact=fact)
        except Exception as e:
            # 落库失败不得让 approve 在单据已批准后 500；但也不能静默 —— DB 无行时
            # 写向量即孤儿，故这里整条放弃并记 error（与 _persist_memory_row 同口径）。
            logger.error(
                "[rag] approve save_memory_fact failed, vector write skipped "
                "(orphan vector avoided) vendor=%s tenant=%s: %s", vendor, tid, e)
            return content
        mid = str(saved.get("memory_id") or "")
        stored_new = bool(saved.get("is_new"))
        # L1：人工批准的 alias 事实 → 别名表登记（人工闸之后的确定性复利面）。
        # 放在 `if not mid` 孤儿守卫判定之前，但函数体第一步就是 mid 空门 ——
        # `save_memory_fact` 理论上可返回空 memory_id，空门防落出 source_ref="memory:"
        # 的残缺别名行；且本调用 try/except 全包、零返回值，不改变下方向量分支任何行为。
        _register_alias_from_approved_fact(fact, tid=tid, vendor=vendor, mid=mid,
                                           who=wb)
        doc_kind = fact.kind
        vector_content = content
    else:
        mid = uuid.uuid4().hex
        # W-9：自由文本分支的记忆语料 PII 最小化 —— 开关关闭（缺省）时逐字节原样返回；
        # 开启时在**落库前 + 写向量前**脱敏 content（DB notes 与向量 page_content 同源）。
        # 返回值仍是原始 content（非记忆语料，保持既有调用方契约）。
        vector_content = _mask_memory_text(content, field="notes", vendor=vendor)
        persisted = _persist_memory_row(
            vendor, vector_content, "", tid, kind, ref, mid, ts,
            supplier_id=supplier_id, written_by=wb)
        if persisted is None:
            return content  # DB 无行 -> 写向量即孤儿，直接放弃
        mid, stored_new = persisted
        doc_kind = kind
    if not mid:
        return content  # 无可回溯主键 -> 写向量即孤儿，直接放弃
    doc = Document(
        page_content=vector_content or "",
        metadata={"vendor": vendor, "kind": doc_kind, "tenant_id": tid,
                  "memory_id": mid, "source_kind": kind, "created_at": ts},
    )
    if not _vector_write_allowed(mid, tid, [doc]):
        return content
    # P8a-R8：stored_new=False（幂等去重命中、复用既有 memory_id）时不再直接
    # return —— 复用分支补「确保向量存在」，缺则按同一 doc 补写并留痕。
    _sync_memory_vectors(tid, mid, vendor, [doc], reusing=not stored_new)
    return content
