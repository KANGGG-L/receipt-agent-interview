# -*- coding: utf-8 -*-
"""U2 回归：rag **读路径**的静默 except 改为可观测（留痕 + 进程级计数，且行为不变）。

背景（gap 分析 M6 的剩余部分）：`rag.retrieve_context` 的每一步降级原先都是
`except Exception: pass`。fail-open 本身是本项目的刻意设计（检索故障不得阻断识别链路，
render 热路径更不能因此变脆），代价却是**运维无法区分**「这个供应商真的没有可用记忆」
与「检索坏了」—— 两者返回的都是空串、且都可能零日志。这正是本仓高发的第③类缺陷
（异常被静默吞掉 → 功能静默失效）里最难看出来的形态：不是功能没了，而是功能没了还
长得跟「正常冷启动」一模一样。

本文件钉住的契约（逐条对应任务要求）：

1. 读路径的七处降级**都**留痕：预算解析（`budget`）、预算设置读取（`budget_settings`）、
   归档阈值读取（`archive_threshold`）、衰减分解析（`decay_parse`）、精确/别名通道
   （`exact`）、相似度兜底通道（`fuzzy`）、命中记账（`hit_accounting`）。每处三断言：
   (a) 不抛异常且返回降级结果、(b) caplog 里有对应 warning（带阶段 / vendor / tenant_id /
   异常类型与文本）、(c) `get_retrieve_failure_counts()` 对应阶段 +1。
2. **先做 caplog 自校准**：每个用例先往被测 `rag.logger` 注入一条已知日志、断言它被采到，
   证明「采集通道活着」——否则「有 warning」与「caplog 名字/传播配置错」区分不开，
   断言可能变成假失败（或让人误以为用例覆盖了留痕）。
3. **不得改动检索结果**：健康路径的注入结果用 golden 值钉死（逐字节），且健康路径必须
   零降级记录、零降级 warning（反向自校准：证明这些 warning 是「故障专用」而不是无差别
   打日志，否则第 1 条的 (b) 会被噪音恒真满足）。
4. 阶段名常量两两不同（防「日志写 exact、计数写 exact_channel」这类对不上，①类缺陷）。
5. `get_retrieve_failure_counts()` 返回**副本**，且这是**进程级、不持久化**的计数器。

隔离：DB 与 RAG_DIR 由 tests/conftest.py 的 autouse 夹具指向 tmp；嵌入档被 conftest 锁为
离线 hash，全程零网络。本文件只读 live（断言 `rag.PERSIST_DIR` 不指向 live 向量目录）。

反证口径（实测记录在交付报告，不写进用例）：
- 反证 A：把 `_note_retrieve_failure` 里的 `logger.warning(...)` 换成 `pass` → 第 1 条所有
  用例的 (b) 必须失败，而 (a)(c) 仍通过 —— 证明 (b) 钉的确实是「有没有痕迹」，不是
  「有没有返回值」、也不是计数器在冒充。
- 反证 B：只删掉 fuzzy 那一处 `_note_retrieve_failure(...)` 调用 → **只有** fuzzy 用例的
  (b)(c) 失败，其余六组仍全绿 —— 证明每个阶段是独立可定位的，不是一处日志满足全部断言。
"""

import logging
import os
import sys
import uuid

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
_DEMO_DIR = os.path.join(_REPO_ROOT, "demo")
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402
import app.services.rag as rag  # noqa: E402

_LIVE_DEMO_RAG_DIR = os.path.join(_DEMO_DIR, ".rag_chroma")

_VENDOR = "U2觀測商戶"
_TENANT = "u2t"
_NOTES = "版式：手寫單"
_SAMPLE = "明細：菜心 2斤 @10 = 20"

# golden：健康路径下该供应商的注入结果（精确通道单条、未超预算截断）。
# 用它把「留痕没有顺手改动检索结果」变成可判定的断言（任务要求 ④）。
_GOLDEN_CTX = f"[{_VENDOR}]\n{_NOTES}\n{_SAMPLE}"

_ALL_STAGES = (
    rag.RETRIEVE_STAGE_BUDGET,
    rag.RETRIEVE_STAGE_BUDGET_SETTINGS,
    rag.RETRIEVE_STAGE_ARCHIVE_THRESHOLD,
    rag.RETRIEVE_STAGE_DECAY_PARSE,
    rag.RETRIEVE_STAGE_EXACT,
    rag.RETRIEVE_STAGE_FUZZY,
    rag.RETRIEVE_STAGE_HIT_ACCOUNTING,
)


@pytest.fixture(autouse=True)
def _fresh_counters(monkeypatch):
    """逐用例重置进程级降级计数。

    why：`_RETRIEVE_FAILURE_COUNTS` 是**模块级 dict**（进程内累计，见其 docstring），
    不清会让「本用例是否 +1」的断言被前一个用例的残留污染。
    """
    monkeypatch.setattr(rag, "_RETRIEVE_FAILURE_COUNTS", {})


def _messages(caplog):
    """取被测模块 logger 产出的日志文本（按 logger 名过滤，排除其它模块的噪音）。

    按 `endswith("services.rag")` 而不是精确名：本仓 demo/ 无 __init__.py，
    `app.services.rag` 与 `demo.app.services.rag` 是同一份源码的两个模块实例、各持一个
    logger 名；只认一个名字会在别名加载时漏采。
    """
    return [r.getMessage() for r in caplog.records
            if r.name.endswith("services.rag")]


def _assert_capture_channel_alive(caplog):
    """caplog 自校准：先注入一条已知日志，证明采集通道真的活着。

    必须在 `caplog.at_level(...)` 上下文内调用。why：本文件的核心断言是「有 warning」，
    如果 caplog 没接上被测 logger（logger 名变了 / propagate 被关 / 级别不对），后续断言
    的结论就不可信 —— 先自校准再断言是本仓既定纪律。
    """
    marker = "u2-selfcal-" + uuid.uuid4().hex[:8]
    rag.logger.warning("capture channel self test %s", marker)
    assert any(marker in m for m in _messages(caplog)), (
        "自校准失败：caplog 未采集到 rag.logger 的日志，「必须留痕」的断言不可信")


def _seed(vendor=_VENDOR, tenant_id=_TENANT):
    """只落 DB 行（精确通道即可注入），不写向量 —— 本文件的判据与相似度通道无关。"""
    mid, _is_new = db.upsert_vendor_memory(
        vendor, _NOTES, _SAMPLE, tenant_id=tenant_id, source_kind="manual")
    return mid


def _raising(exc_type, text):
    """构造一个「调用即抛」的可调用对象（monkeypatch 替身用）。"""
    def _boom(*args, **kwargs):
        raise exc_type(text)
    return _boom


def _assert_stage_observable(caplog, stage, exc_type_name, exc_text,
                             vendor=_VENDOR, tenant_id=_TENANT):
    """(b)+(c) 的共用断言：warning 能定位到阶段/供应商/租户/异常，且计数 +1。

    why 抽成一处：七组用例的判据完全同构，抄七份必然出现「改一处漏六处」。
    """
    hit = [m for m in _messages(caplog) if "stage '%s'" % stage in m]
    assert hit, "阶段 %r 降级必须打 warning，实际 rag 日志: %s" % (
        stage, _messages(caplog))
    joined = "\n".join(hit)
    if vendor is not None:
        assert vendor in joined, "warning 必须带 vendor（否则不知道哪个供应商在降级）"
    if tenant_id is not None:
        assert tenant_id in joined, "warning 必须带 tenant_id（否则分辨不出租户错位）"
    assert exc_type_name in joined, "warning 必须带异常类型（代码缺陷 vs 外部不确定性）"
    assert exc_text in joined, "warning 必须带异常文本（否则不知道失败原因）"
    assert rag.get_retrieve_failure_counts().get(stage) == 1, (
        "阶段 %r 的降级计数未 +1，实际 %r" % (stage, rag.get_retrieve_failure_counts()))


# -------------------------------------------------------------
# 1. 精确/别名通道（exact）
# -------------------------------------------------------------
def test_exact_channel_failure_is_observable_and_fail_open(monkeypatch, caplog):
    """精确通道整体失败 → 空上下文（不抛），但必须留痕 + 计数。"""
    _seed()
    real_list = db.list_vendor_memory

    def _boom(vendor=None, tenant_id="default", include_archived=False,
              supplier_id=None):
        # 只打断精确通道（vendor 非空那次）；fuzzy 的 (None, tid) 那次照常返回，
        # 否则会把 fuzzy 阶段一起打挂、混淆阶段归属。
        if vendor is not None:
            raise RuntimeError("exact channel boom")
        return real_list(vendor, tenant_id=tenant_id,
                         include_archived=include_archived, supplier_id=supplier_id)

    monkeypatch.setattr(db, "list_vendor_memory", _boom)

    with caplog.at_level(logging.WARNING, logger="app.services.rag"):
        _assert_capture_channel_alive(caplog)
        ctx = rag.retrieve_context(_VENDOR, tenant_id=_TENANT)

    # (a) 不抛异常；该供应商此刻无可注入记忆（fuzzy 在空向量库上也无命中）→ 空串
    assert ctx == ""
    _assert_stage_observable(caplog, rag.RETRIEVE_STAGE_EXACT,
                             "RuntimeError", "exact channel boom")


# -------------------------------------------------------------
# 2. 相似度兜底通道（fuzzy）
# -------------------------------------------------------------
def test_fuzzy_channel_failure_is_observable_and_keeps_exact_result(monkeypatch, caplog):
    """兜底通道失败 → 精确通道的结果原样保留（不是整体返空），但必须留痕 + 计数。"""
    _seed()
    monkeypatch.setattr(rag, "_store", _raising(RuntimeError, "vector store boom"))

    with caplog.at_level(logging.WARNING, logger="app.services.rag"):
        _assert_capture_channel_alive(caplog)
        ctx = rag.retrieve_context(_VENDOR, tenant_id=_TENANT)

    # (a) 降级结果是「只剩精确通道」而不是空串 —— 证明留痕没有把兜底失败升级成整体失败
    assert ctx == _GOLDEN_CTX
    _assert_stage_observable(caplog, rag.RETRIEVE_STAGE_FUZZY,
                             "RuntimeError", "vector store boom")


# -------------------------------------------------------------
# 3. 命中记账（hit_accounting）
# -------------------------------------------------------------
def test_hit_accounting_failure_is_observable_and_keeps_context(monkeypatch, caplog):
    """记账失败不影响注入内容，但必须留痕 + 计数（否则「hit_count 不涨」会被误读）。"""
    _seed()
    monkeypatch.setattr(db, "bump_vendor_memory_hit",
                        _raising(RuntimeError, "hit accounting boom"))

    with caplog.at_level(logging.WARNING, logger="app.services.rag"):
        _assert_capture_channel_alive(caplog)
        ctx = rag.retrieve_context(_VENDOR, tenant_id=_TENANT)

    assert ctx == _GOLDEN_CTX
    _assert_stage_observable(caplog, rag.RETRIEVE_STAGE_HIT_ACCOUNTING,
                             "RuntimeError", "hit accounting boom")


# -------------------------------------------------------------
# 4. 预算解析（budget，retrieve_context 侧兜底）
# -------------------------------------------------------------
def test_budget_resolution_failure_is_observable_and_uses_default(monkeypatch, caplog):
    """预算解析整体失败 → 回退缺省预算继续注入，留痕 + 计数。"""
    _seed()

    def _boom_from_settings(cls):
        raise RuntimeError("budget boom")

    monkeypatch.setattr(rag.MemoryBudget, "from_settings",
                        classmethod(_boom_from_settings))

    with caplog.at_level(logging.WARNING, logger="app.services.rag"):
        _assert_capture_channel_alive(caplog)
        ctx = rag.retrieve_context(_VENDOR, tenant_id=_TENANT)

    # 缺省预算（800/200/6）足够容纳 golden 文本 → 注入结果不变
    assert ctx == _GOLDEN_CTX
    _assert_stage_observable(caplog, rag.RETRIEVE_STAGE_BUDGET,
                             "RuntimeError", "budget boom")


# -------------------------------------------------------------
# 5. 预算设置读取（budget_settings，MemoryBudget.from_settings 侧兜底）
# -------------------------------------------------------------
def test_budget_settings_read_failure_is_observable_and_falls_back(monkeypatch, caplog):
    """读 settings 失败 → 回退缺省 800/200/6（管理台调了没反应这件事必须可见）。"""
    _seed()
    from app.services import settings_service

    monkeypatch.setattr(settings_service, "get_int",
                        _raising(RuntimeError, "settings boom"))

    with caplog.at_level(logging.WARNING, logger="app.services.rag"):
        _assert_capture_channel_alive(caplog)
        ctx = rag.retrieve_context(_VENDOR, tenant_id=_TENANT)

    assert ctx == _GOLDEN_CTX
    # 该阶段发生在取得 vendor 之前，故 warning 里 vendor/tenant 如实为 None（不编造）
    hit = [m for m in _messages(caplog)
           if "stage '%s'" % rag.RETRIEVE_STAGE_BUDGET_SETTINGS in m]
    assert hit, "实际 rag 日志: %s" % (_messages(caplog),)
    joined = "\n".join(hit)
    assert "RuntimeError" in joined and "settings boom" in joined
    assert rag.get_retrieve_failure_counts().get(
        rag.RETRIEVE_STAGE_BUDGET_SETTINGS) == 1


# -------------------------------------------------------------
# 6. 归档阈值读取（archive_threshold）
# -------------------------------------------------------------
def test_archive_threshold_failure_is_observable_and_falls_back(monkeypatch, caplog):
    """阈值读取失败 → 回退 0.5（安全侧会失效的降级，必须可见）。"""
    _seed()
    monkeypatch.setattr(db, "memory_archive_threshold",
                        _raising(RuntimeError, "threshold boom"))

    with caplog.at_level(logging.WARNING, logger="app.services.rag"):
        _assert_capture_channel_alive(caplog)
        ctx = rag.retrieve_context(_VENDOR, tenant_id=_TENANT)

    assert ctx == _GOLDEN_CTX  # 起始分 0.7 > 回退阈值 0.5 → 仍注入
    _assert_stage_observable(caplog, rag.RETRIEVE_STAGE_ARCHIVE_THRESHOLD,
                             "RuntimeError", "threshold boom")


# -------------------------------------------------------------
# 7. 衰减分解析（decay_parse，治理门 fail-open）
# -------------------------------------------------------------
def test_decay_parse_failure_is_observable_and_fails_open(monkeypatch, caplog):
    """decay_score 形态非法 → 保守放行（仍注入）这一 fail-open 必须可见。"""
    bogus = {
        "vendor": _VENDOR, "notes": _NOTES, "sample": _SAMPLE,
        "memory_id": "mid-u2-decay", "decay_score": "not-a-number",
    }

    def _stub(vendor=None, tenant_id="default", include_archived=False,
              supplier_id=None):
        return [bogus] if vendor is not None else []

    monkeypatch.setattr(db, "list_vendor_memory", _stub)

    with caplog.at_level(logging.WARNING, logger="app.services.rag"):
        _assert_capture_channel_alive(caplog)
        ctx = rag.retrieve_context(_VENDOR, tenant_id=_TENANT)

    # fail-open 语义未变：脏行照样注入（留痕只加日志，不改判据）
    assert ctx == _GOLDEN_CTX
    _assert_stage_observable(caplog, rag.RETRIEVE_STAGE_DECAY_PARSE,
                             "ValueError", "'not-a-number'")


# -------------------------------------------------------------
# 8. 行为不变 + 反向自校准：健康路径零降级记录、零降级日志
# -------------------------------------------------------------
def test_normal_path_result_is_golden_and_records_no_degradation(caplog):
    """(4) 同一输入下的注入结果与加日志前逐值一致；健康路径不得出现降级痕迹。"""
    _seed()

    with caplog.at_level(logging.WARNING, logger="app.services.rag"):
        _assert_capture_channel_alive(caplog)
        ctx = rag.retrieve_context(_VENDOR, tenant_id=_TENANT)

    assert ctx == _GOLDEN_CTX, "留痕不得改动检索结果（golden 值漂移）"
    assert rag.get_retrieve_failure_counts() == {}, (
        "健康路径不得产生降级计数，实际 %r" % (rag.get_retrieve_failure_counts(),))
    degraded = [m for m in _messages(caplog) if "retrieve stage" in m]
    assert degraded == [], (
        "健康路径不得打降级 warning —— 否则「必须留痕」的断言会被噪音恒真满足: %s"
        % (degraded,))


# -------------------------------------------------------------
# 9. 计数器契约：快照副本 / 阶段名两两不同 / 与 live 向量目录无关
# -------------------------------------------------------------
def test_failure_counts_snapshot_is_a_copy(monkeypatch):
    """返回**副本**：调用方改它不得影响内部计数（巡检脚本不能顺手清空现场）。"""
    _seed()
    monkeypatch.setattr(db, "memory_archive_threshold",
                        _raising(RuntimeError, "threshold boom"))
    rag.retrieve_context(_VENDOR, tenant_id=_TENANT)

    snapshot = rag.get_retrieve_failure_counts()
    assert snapshot.get(rag.RETRIEVE_STAGE_ARCHIVE_THRESHOLD) == 1
    snapshot["poked-by-caller"] = 999
    assert "poked-by-caller" not in rag.get_retrieve_failure_counts()


def test_stage_names_are_pairwise_distinct():
    """阶段名两两不同：计数与日志靠它聚合，重名会让两个阶段混成一个（①类缺陷）。"""
    assert len(set(_ALL_STAGES)) == len(_ALL_STAGES), _ALL_STAGES


def test_retrieve_does_not_touch_live_vector_dir(caplog):
    """隔离守卫：本文件的检索只落在 RAG_DIR（tmp），绝不在 live 向量目录下建库。

    判据取「live 目录的存在性前后不变」而不是「live 目录必须不存在」：后者在用户本机
    已有向量库时会假失败（与本文件的判据无关），而「本用例没有创建它」才是真正要钉的。
    """
    assert os.path.abspath(rag.PERSIST_DIR) != os.path.abspath(_LIVE_DEMO_RAG_DIR), \
        "测试尚未隔离 RAG_DIR，会写 live 向量库"

    live_chroma = os.path.join(_LIVE_DEMO_RAG_DIR, "chroma.sqlite3")
    existed_before = os.path.exists(live_chroma)

    _seed()
    with caplog.at_level(logging.WARNING, logger="app.services.rag"):
        rag.retrieve_context(_VENDOR, tenant_id=_TENANT)

    assert os.path.exists(live_chroma) == existed_before, \
        "检索在本用例里碰了 live 向量库（%s 的存在性变了）" % live_chroma
