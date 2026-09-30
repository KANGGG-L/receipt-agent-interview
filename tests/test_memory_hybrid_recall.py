# -*- coding: utf-8 -*-
"""P3-T6（P7·Y2）：混合召回（关键词腿 + 向量腿）+ 治理信号 rerank + 读侧 scope 强制收窄。

本文件钉住五条契约（逐条对应任务要求），每条都给出可复算的判据：

1. **关键词腿是必选腿，且带来可测的召回提升**（要求①⑤）。验收基准 = 多候选 query 集上
   的 hit@k：query 形如「供应商 商品名 单位」（正是本项目记忆语料的形态：短商品名 +
   供应商名 + 单位），每条 query 的**相关记忆**＝该供应商名下唯一包含该签名品名的那条。
   三种配置对比（同一 query 集、同一 top_k，唯一变量是"哪些腿进池"）：

   - 纯向量腿 `use_exact=False, use_keyword=False`（Chroma 只返 top_k，k 之外的命中
     永远拿不回来）；
   - 关掉关键词腿 `use_keyword=False`（精确/别名腿 + 向量腿：全部 scope 行都在池里，
     但**没有查询相关的文本相关度**，排序退化为向量腿排名 + 腿内原序）；
   - 混合召回（缺省：三条腿）。

   实测（32 query，top_k=3，本机 4 次运行；见用例打印）：纯向量腿 **65.6%~68.8%** /
   关掉关键词腿 **71.9%~81.3%** / 混合召回 **100.00%（4/4 次相同）** —— 增益 +18.8~28.1pp。
   含向量腿的两个数字受 Chroma 近似索引影响有 ±1~2 条 query 的波动（跨进程重建索引后
   近邻次序可能变），故断言取「混合 ≥ 95%」、「混合 - 关掉关键词腿 ≥ 10pp」、「两条基线
   都 < 95%」，**不给向量腿钉死绝对值**；混合召回那一侧是 BM25 + 腿基础分主导，稳定不动。

2. **rerank 真消费治理信号**（要求②，也是 R2 的处置）。判据＝把 decay_score / hit_count /
   last_hit_at 三个分项权重归零后，排序必须变（`test_rerank_consumes_governance_signals`）；
   并核对 `explain()` 逐条给出每个分项的实际贡献（不黑箱）。权重可配：settings 键
   `memory_recall_weights`（合法 JSON 覆盖、非法 JSON 回退缺省且留 warning）。

3. **读侧 scope 强制收窄**（要求③）：跨租户同名供应商不可召回；同租户内名字互相包含的
   两家（"德利" / "德利行"）在有锚点时按锚点过滤 —— 并给出**反向证据**：把锚点解析打断
   （`resolve_supplier_id → None`）后，名字口径会把另一家的记忆放行，证明拦住它的确实是
   锚点而不是别的偶然因素。

4. **命中记账复用既有实现**（要求④）：被注入的行 hit_count +1、last_hit_at 刷新；且把
   `db.bump_vendor_memory_hit` 换成 no-op 后 hit_count 不再增长 —— 证明 rag 侧没有第二条
   记账实现（同一根因两份实现是本仓①类缺陷）。

5. **多腿合并去重 + 确定性**：同一条记忆被三条腿命中时只留一条（保留精确腿的注入文本、
   `legs` 累计三条腿、relevance 取最大）；同一输入两次调用必得同一结果。

隔离：DB 与 RAG_DIR 由 tests/conftest.py 的 autouse 夹具指向 tmp；本模块再声明一次 env
（必须在 import app.* 之前）作为双保险；嵌入档锁 hash，全程零网络。

反证口径（`/tmp` 突变体式，仓库文件全程只读，实测记录在交付报告里）：
- 反证 A（关掉关键词腿）：把 `_build_recall_pool` 的 `use_keyword` 缺省改成 False（生产
  调用点就会失去关键词腿）→ `test_hybrid_recall_beats_pure_vector_on_multi_candidate_query_set`
  必须失败（命中率掉到基线线以下）。
- 反证 B（scope 不收窄）：把向量腿的 scope 门 `str(dmid) not in scope_mids` 改成放行 →
  `test_supplier_anchor_blocks_name_related_other_supplier` 必须失败。
"""

import importlib
import json
import logging
import os
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="rag_hybrid_recall_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_hybrid.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["RAG_EMBEDDING_PROVIDER"] = "hash"

_DEMO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo")
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402
import app.services.rag as rag  # noqa: E402
from app.services import memory_recall as mr  # noqa: E402

# 验收基准的 query 集规模（4 供应商 × 每供应商 8 条记忆 × 每记忆 10 行明细）
BENCH_VENDORS = ("德利行", "祥興凍肉", "金百加咖啡", "聯豐食品")
BENCH_MEMS_PER_VENDOR = 8
BENCH_ITEMS_PER_MEM = 10
TOP_K = 3
# 验收线：混合召回必须 ≥ 此值；关掉关键词腿的两种基线都必须**掉到此值以下**（本文件的
# 核心判别式）。取 95% 而不是 90%：混合召回实测 100%（确定性），而两条基线实测
# 68.75% / 81.25%（后者含向量腿排名的影响），阈值抬高反而让反证判据的余量更大。
ACCEPT_MIN_HIT_RATE = 0.95
# 混合召回相对"关掉关键词腿"的最小必须增益（百分点）——比单看"是否过线"更抗抖动
MIN_GAIN_OVER_NO_KEYWORD = 0.10

_ITEMS = ("菜心", "白菜", "急凍牛肋條", "本地牛腩", "雞蛋", "絲苗米", "麵粉", "沙拉油",
          "生抽", "蠔油", "凍蝦", "薯仔", "洋蔥", "紅蘿蔔", "豆腐", "生菜")
_UNITS = ("斤", "磅", "箱", "包", "桶", "打")
# 签名品名的后缀（保证同供应商内唯一；用不含 emoji/特殊符号的汉字）
_SIG_SUFFIX = ("甲乙", "丙丁", "戊己", "庚辛", "壬癸", "子丑", "寅卯", "辰巳")


def _fresh_db():
    """每个用例独立数据库文件 + 重置 Chroma 进程内缓存，避免跨用例串扰。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"y2_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    rag.PERSIST_DIR = os.environ["RAG_DIR"]
    rag._vs = None
    rag._vs_tenant = {}
    return db


def _rows(vendor, tenant):
    return db.list_vendor_memory(vendor, tenant_id=tenant)


def _rank(vendor, tenant, query, top_k=TOP_K, **legs):
    """按生产实现取候选池并 rerank，返回 (top_k 的 memory_id 列表, stats)。"""
    rows = _rows(vendor, tenant)
    pool, stats = rag._build_recall_pool(vendor, tenant, rows, query, top_k, **legs)
    ranked = mr.rerank(query, pool, top_k=top_k, query_vendor=vendor)
    return [s.doc.memory_id for s in ranked], stats


def _build_bench_corpus(tenant):
    """构造多候选语料（写入走生产路径 rag.ingest_memory），返回 query 集。

    query 集元素 = (vendor, query, relevant_mid)。相关记忆的判据：该记忆的明细里含有
    **只属于它**的签名品名，故"命中"不依赖任何主观标注。
    """
    cases = []
    for vi, vendor in enumerate(BENCH_VENDORS):
        for mi in range(BENCH_MEMS_PER_VENDOR):
            sig = "%s%s" % (_ITEMS[(vi * 5 + mi) % len(_ITEMS)], _SIG_SUFFIX[mi])
            sig_unit = _UNITS[(vi + mi) % len(_UNITS)]
            lines = []
            for k in range(BENCH_ITEMS_PER_MEM):
                lines.append("- %s %d%s @%.2f = %.2f" % (
                    _ITEMS[(vi * 3 + mi * 2 + k) % len(_ITEMS)], (k % 5) + 1,
                    _UNITS[(k + mi) % len(_UNITS)], 8.0 + k, (8.0 + k) * ((k % 5) + 1)))
            lines.append("- %s %d%s @%.2f = %.2f" % (sig, mi + 1, sig_unit,
                                                     20.0 + mi, 20.0 + mi))
            # notes 逐条不同：upsert 的幂等键是 (vendor, tenant, notes, sample)
            rag.ingest_memory(vendor, "\n".join(lines),
                              notes="版式：printed_delivery_note_%d" % mi,
                              tenant_id=tenant, receipt_id=9000 + mi)
            cases.append((vendor, "%s %s %s" % (vendor, sig, sig_unit), sig))
    # 签名 → memory_id（用 sample 里的签名品名反查，不依赖任何内部返回）
    out = []
    for vendor, query, sig in cases:
        rel = [r["memory_id"] for r in _rows(vendor, tenant) if sig in (r["sample"] or "")]
        assert len(rel) == 1, "前提：签名品名 %r 必须唯一指向一条记忆，实际 %r" % (sig, rel)
        out.append((vendor, query, rel[0]))
    return out


def _hit_rate(cases, tenant, **legs):
    hit = 0
    for vendor, query, rel_mid in cases:
        got, _stats = _rank(vendor, tenant, query, TOP_K, **legs)
        hit += 1 if rel_mid in got else 0
    return hit / float(len(cases))


# -------------------------------------------------------------
# 1. 验收基准：混合召回 vs 纯向量腿 / 基线读路径（要求①⑤）
# -------------------------------------------------------------
def test_hybrid_recall_beats_pure_vector_on_multi_candidate_query_set():
    """同一 query 集上：混合召回 ≥ 95%，且严格高于"关掉关键词腿"的两种基线。

    这条同时是**判别式用例**：关掉关键词腿（反证 A）后 `hybrid >= 0.95` 必然失败 ——
    本断言里就写了 `baseline < 0.95`，两者不可能在关键词腿缺席时同时成立。
    """
    _db = _fresh_db()
    tenant = "y2acc"
    cases = _build_bench_corpus(tenant)
    assert len(cases) == len(BENCH_VENDORS) * BENCH_MEMS_PER_VENDOR

    pure_vector = _hit_rate(cases, tenant, use_exact=False, use_keyword=False)
    baseline = _hit_rate(cases, tenant, use_exact=True, use_keyword=False)
    # 生产缺省（不传任何 use_*，与 retrieve_context 的调用形态逐字一致）——
    # 这一条钉住"关键词腿恒开"：若有人把缺省改成关（假开关），本行必须掉下来。
    hybrid = _hit_rate(cases, tenant)
    hybrid_explicit = _hit_rate(cases, tenant, use_exact=True, use_keyword=True)
    print("[Y2 验收] query 集 %d 条，top_k=%d：纯向量腿 %.2f%% / 关掉关键词腿(精确+向量) "
          "%.2f%% / 混合召回(生产缺省) %.2f%%" % (len(cases), TOP_K, pure_vector * 100,
                                                 baseline * 100, hybrid * 100))

    assert hybrid == hybrid_explicit, (
        "生产缺省配置必须就是混合召回（三条腿恒开）：缺省 %.2f%% vs 显式 %.2f%%"
        % (hybrid * 100, hybrid_explicit * 100))
    assert hybrid >= ACCEPT_MIN_HIT_RATE, (
        "混合召回的 hit@%d 必须 >= %.0f%%，实际 %.2f%%（关键词腿是否真的进池了？）"
        % (TOP_K, ACCEPT_MIN_HIT_RATE * 100, hybrid * 100))
    assert hybrid > baseline, (
        "混合召回必须严格优于关掉关键词腿的基线：hybrid=%.2f%% baseline=%.2f%%"
        % (hybrid * 100, baseline * 100))
    assert hybrid - baseline >= MIN_GAIN_OVER_NO_KEYWORD, (
        "关键词腿必须带来 >= %.0f 个百分点的增益，实际 %.2fpp"
        % (MIN_GAIN_OVER_NO_KEYWORD * 100, (hybrid - baseline) * 100))
    assert pure_vector < ACCEPT_MIN_HIT_RATE, (
        "纯向量腿必须拿不到验收线（否则本用例证明不了关键词腿的必要性）：%.2f%%"
        % (pure_vector * 100))
    assert baseline < ACCEPT_MIN_HIT_RATE, (
        "反证判据：关掉关键词腿必须掉到验收线以下，实际 %.2f%% —— 若它也 >= %.0f%%，"
        "说明命中不是关键词腿带来的，本用例的结论不成立"
        % (baseline * 100, ACCEPT_MIN_HIT_RATE * 100))


# -------------------------------------------------------------
# 2. 治理信号 rerank（要求②）：三个治理分项必须真的影响排序
# -------------------------------------------------------------
def _gov_doc(doc_id, decay, hit_count, last_hit_at):
    """同文本、同腿、同相关度的两条候选，唯一差异是治理信号。"""
    return mr.RecallDoc(doc_id=doc_id, memory_id=doc_id, text="版式：printed",
                        leg=mr.RECALL_LEG_EXACT, relevance=0.5, decay_score=decay,
                        hit_count=hit_count, last_hit_at=last_hit_at,
                        vendor="德利行")


def test_rerank_consumes_governance_signals():
    """三条治理信号（decay_score / hit_count / last_hit_at）必须真的决定顺序。

    构造：两条候选除治理信号外**逐字段相同**（同文本/同腿/同相关度），且**分值低的先入池**
    —— 于是"分值高的排到前面"只可能由治理分项造成；再把三个治理权重归零，顺序必须退回
    插入序（＝治理信号不再被消费时的样子）。这条同时证明它们不是"写着好看"的装饰项。
    """
    from datetime import datetime
    now = datetime(2026, 9, 23, 12, 0, 0)
    stale = _gov_doc("mid-stale", decay=0.2, hit_count=0, last_hit_at="")
    fresh = _gov_doc("mid-fresh", decay=0.95, hit_count=9,
                     last_hit_at="2026-09-23T11:30:00")

    order = [s.doc.doc_id for s in mr.rerank("德利行 菜心 斤", [stale, fresh], now=now)]
    assert order == ["mid-fresh", "mid-stale"], (
        "治理信号未被消费：高分记忆必须排到前面，实际 %r" % (order,))

    # 可审计：每个分项的实际贡献都要能读出来（不黑箱）
    detail = mr.explain(mr.rerank("德利行 菜心 斤", [stale, fresh], top_k=1, now=now))[0]
    assert set(detail["components"]) == {"leg", "relevance", "exact_match",
                                        "decay", "hit", "recency"}, detail
    assert detail["components"]["decay"] > 0 and detail["components"]["hit"] > 0
    assert detail["components"]["recency"] > 0, "新鲜度必须由 last_hit_at 换算出来"

    # 反证（in-test）：把三个治理分项权重归零 → 顺序退回插入序（治理信号不再被消费）
    zeroed = mr.RecallWeights(decay=0.0, hit=0.0, recency=0.0)
    order2 = [s.doc.doc_id for s in
              mr.rerank("德利行 菜心 斤", [stale, fresh], weights=zeroed, now=now)]
    assert order2 == ["mid-stale", "mid-fresh"], (
        "治理权重归零后顺序应退回插入序（证明前面那个顺序确实来自治理分项）：%r" % (order2,))


def test_recall_weights_are_configurable_and_fall_back_visibly(caplog):
    """权重可配（settings 键）且非法输入回退缺省并留痕 —— 不得静默改排序口径。"""
    from app.services import settings_service
    default = mr.DEFAULT_WEIGHTS
    assert set(default.as_dict()) == {"leg_exact", "leg_keyword", "leg_vector",
                                      "relevance", "exact_match", "decay", "hit",
                                      "recency"}

    settings_service.set_value(mr.WEIGHTS_SETTINGS_KEY,
                               json.dumps({"hit": 0.5, "leg_vector": 0.9, "unknown": 3}))
    w = mr.RecallWeights.from_settings()
    assert w.hit == 0.5 and w.leg_vector == 0.9
    assert w.leg_exact == default.leg_exact, "未覆盖的字段必须保留缺省"

    settings_service.set_value(mr.WEIGHTS_SETTINGS_KEY, "{不是 JSON")
    with caplog.at_level(logging.WARNING, logger="app.services.memory_recall"):
        w2 = mr.RecallWeights.from_settings()
    assert w2 == default, "非法 JSON 必须整体回退缺省权重"
    assert any("不是合法 JSON" in r.getMessage() for r in caplog.records), \
        "回退必须留 warning（否则'管理台改了权重没生效'永远查不出来）"


# -------------------------------------------------------------
# 3. 读侧 scope 强制收窄（要求③）
# -------------------------------------------------------------
def test_other_tenants_same_named_supplier_memory_is_not_recallable():
    """另一个租户的**同名供应商**记忆不可召回（tenant 是硬门，向量桶也物理隔离）。"""
    _db = _fresh_db()
    rag.ingest_memory("金百加咖啡", "- 菜心 1斤 @10 = 10 MARKER_A_ONLY",
                      notes="版式：printed", tenant_id="y2_tenant_a")
    rag.ingest_memory("金百加咖啡", "- 白菜 1斤 @5 = 5 MARKER_B_ONLY",
                      notes="版式：printed", tenant_id="y2_tenant_b")

    ctx_a = rag.retrieve_context("金百加咖啡", tenant_id="y2_tenant_a")
    assert "MARKER_A_ONLY" in ctx_a, ctx_a
    assert "MARKER_B_ONLY" not in ctx_a, "跨租户同名供应商的记忆被召回了：%r" % ctx_a

    # 正向对照（排除"B 的记忆压根没写进去"）
    ctx_b = rag.retrieve_context("金百加咖啡", tenant_id="y2_tenant_b")
    assert "MARKER_B_ONLY" in ctx_b and "MARKER_A_ONLY" not in ctx_b, ctx_b


def test_supplier_anchor_blocks_name_related_other_supplier(monkeypatch):
    """拿得到 supplier_id 时必须按它过滤；名字只作回退（要求③）。

    "德利" 与 "德利行" 互为子串（`_vendor_related` 判 True，即名字口径会互相放行），
    故这是锚点过滤最直接的判别场景。反向证据：把锚点解析打断后，另一家的记忆**确实**
    经名字口径漏进来 —— 证明拦住它的是锚点，而不是"别的偶然因素"。
    """
    _db = _fresh_db()
    tenant = "y2anchor"
    sid_a, err_a = db.create_supplier("德利", tenant_id=tenant)
    sid_b, err_b = db.create_supplier("德利行", tenant_id=tenant)
    assert sid_a and sid_b, (err_a, err_b)
    assert sid_a != sid_b, "前提：两家必须是不同档案，否则本用例测的不是跨锚点隔离"
    assert rag._vendor_related("德利", "德利行") is True, \
        "前提：名字口径必须会互相放行（否则锚点是多余的）"

    rag.ingest_memory("德利", "- 菜心 1斤 @10 = 10 MARKER_ANCHOR_A",
                      notes="版式：printed", tenant_id=tenant, supplier_id=sid_a)
    rag.ingest_memory("德利行", "- 白菜 1斤 @5 = 5 MARKER_ANCHOR_B",
                      notes="版式：printed", tenant_id=tenant, supplier_id=sid_b)

    ctx = rag.retrieve_context("德利", tenant_id=tenant)
    assert "MARKER_ANCHOR_A" in ctx, ctx
    assert "MARKER_ANCHOR_B" not in ctx, (
        "锚点够用时仍召回了名字相关的另一家（越权 scope）：%r" % ctx)

    # 反向证据：锚点解析被打断 → 退回名字口径 → 另一家的记忆漏进来
    monkeypatch.setattr(db, "resolve_supplier_id", lambda *a, **k: None)
    ctx_fallback = rag.retrieve_context("德利", tenant_id=tenant)
    assert "MARKER_ANCHOR_B" in ctx_fallback, (
        "反向证据不成立：名字口径本应放行'德利行'，实际 %r —— 说明上面的隔离"
        "不是由锚点造成的" % ctx_fallback)


def test_vector_leg_scope_gate_blocks_out_of_scope_vector(monkeypatch):
    """向量腿的 scope 门：相似度检索返回的**越权 memory_id** 不得进候选池（要求③）。

    做法：把 similarity_search 换成"故意返回另一家的向量"，断言那家的文本不进上下文。
    """
    _db = _fresh_db()
    tenant = "y2vec"
    rag.ingest_memory("德利行", "- 菜心 1斤 @10 = 10 MARKER_OWN",
                      notes="版式：printed", tenant_id=tenant)
    rag.ingest_memory("祥興凍肉", "- 凍蝦 1箱 @200 = 200 MARKER_FOREIGN",
                      notes="版式：printed", tenant_id=tenant)

    foreign_mid = [r["memory_id"] for r in _rows("祥興凍肉", tenant)][0]
    real_store = rag._store

    class _FakeStore:
        def similarity_search(self, query, k=3):
            return [rag.Document(page_content="- 凍蝦 1箱 @200 = 200 MARKER_FOREIGN",
                                 metadata={"vendor": "祥興凍肉", "tenant_id": tenant,
                                           "memory_id": foreign_mid})]

    monkeypatch.setattr(rag, "_store", lambda tid=None: _FakeStore())
    ctx = rag.retrieve_context("德利行", tenant_id=tenant)
    assert "MARKER_OWN" in ctx, ctx
    assert "MARKER_FOREIGN" not in ctx, (
        "向量腿放行了 scope 之外的向量（相似度检索返回≠可以进 prompt）：%r" % ctx)
    monkeypatch.setattr(rag, "_store", real_store)


# -------------------------------------------------------------
# 4. 命中记账（要求④）：复用既有实现，不重复写一份
# -------------------------------------------------------------
def test_injected_rows_are_accounted_through_existing_db_helper(monkeypatch):
    """被注入的行 hit_count +1 且 last_hit_at 刷新；记账只走 db.bump_vendor_memory_hit。"""
    _db = _fresh_db()
    tenant = "y2hit"
    rag.ingest_memory("德利行", "- 菜心 1斤 @10 = 10", notes="版式：printed",
                      tenant_id=tenant)
    assert rag.retrieve_context("德利行", tenant_id=tenant).strip() != ""

    row = _rows("德利行", tenant)[0]
    assert row["hit_count"] == 1, "被注入的行必须累加 hit_count：%r" % (row,)
    assert row["last_hit_at"], "被注入的行必须刷新 last_hit_at：%r" % (row,)

    # 复用（不重复实现）：把既有记账入口换成 no-op → 再检索一次，hit_count 不再增长。
    # 若 rag 里另有一条自写的 UPDATE/计数路径，这里就会失败。
    monkeypatch.setattr(db, "bump_vendor_memory_hit", lambda ids: 0)
    rag.retrieve_context("德利行", tenant_id=tenant)
    assert _rows("德利行", tenant)[0]["hit_count"] == 1, (
        "hit_count 仍在增长：说明命中记账存在第二条实现（未复用 db.bump_vendor_memory_hit）")


# -------------------------------------------------------------
# 5. 多腿合并去重 + 确定性
# -------------------------------------------------------------
def test_multi_leg_merge_dedupes_and_is_deterministic():
    """同一记忆被三条腿命中时只留一条（保留精确腿文本、legs 三条、relevance 取最大）。"""
    legs = (mr.RECALL_LEG_EXACT, mr.RECALL_LEG_KEYWORD, mr.RECALL_LEG_VECTOR)
    docs = [mr.RecallDoc(doc_id="mid-1", memory_id="mid-1", text="文本-%s" % leg,
                         leg=leg, relevance=r, vendor="德利行")
            for leg, r in zip(legs, (0.2, 0.8, 0.5))]
    merged = mr.merge_candidates(docs)
    assert len(merged) == 1, "同一 memory_id 必须只留一条：%r" % (merged,)
    assert merged[0].leg == mr.RECALL_LEG_EXACT, "留下的应是腿基础分最高的那条（注入形态）"
    assert merged[0].text == "文本-exact"
    assert set(merged[0].legs) == set(legs), "legs 必须累计三条腿（审计：为什么召回它）"
    assert merged[0].relevance == 0.8, "relevance 取各腿最大值"

    # 确定性：同一输入两次必得同一结果（验收数字可复算）
    a = mr.bm25_scores("德利行 菜心 斤", [("1", "- 菜心 1斤 @10"), ("2", "- 白菜 1斤 @5")])
    b = mr.bm25_scores("德利行 菜心 斤", [("1", "- 菜心 1斤 @10"), ("2", "- 白菜 1斤 @5")])
    assert a == b
    r1 = [(s.doc.doc_id, round(s.total, 9)) for s in mr.rerank("德利行 菜心", merged)]
    r2 = [(s.doc.doc_id, round(s.total, 9)) for s in mr.rerank("德利行 菜心", merged)]
    assert r1 == r2


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v", "-s"]))
