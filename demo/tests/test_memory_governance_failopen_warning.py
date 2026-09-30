# -*- coding: utf-8 -*-
"""D9（I2 口径）：db.py 记忆治理三处无痕 fail-open 补留痕 —— 行为一个不改。

why: 这三处的回退值本身都是**刻意兜底**（不是缺陷），问题在于**零痕迹**：

1. `memory_ttl_days()` 读 settings 失败 → `return 0`（不过期）。若不记日志，
   「TTL 配置读不出来」与「管理员本来就设了 0」在库里完全同形 —— 治理策略长期
   失效而无人可见。
2. `bump_vendor_memory_hit()` 读 `memory_decay_hit_bonus` 失败 → `bonus = 0.05`。
   若不记日志，表现只是「记忆衰减得比预期快」，没人会去想是档位配置没读到。
3. `list_vendor_memory._name_matched()` 导入 `rag._vendor_related` 失败 →
   `_vendor_related = None` → 模糊/别名通道**整条静默消失**（退化为纯精确匹配），
   记忆召不回而只表现为「注入变少」。这一处还要求日志把**后果**写出来，
   否则运维看到一行 warning 不知道意味着什么。

这是本仓高发的第③类缺陷（异常被静默吞掉 → 功能静默失效）。本文件把「失败必须
可见」变成 CI 红，并用**负向自校准**保证这些断言不是被噪音恒真满足的。

锁四件事（全程离线：独立临时 DB + 临时 RAG_DIR，零网络、零 live 库/向量库写入）：

1. caplog 通道自校准：先在 `app.db` logger 上打一条探针 warning，断言它确实被抓到
   —— 否则后面「没抓到 warning」到底是产品没打、还是 caplog 没接上，无法区分。
2. 三处失败各自：(a) 回退值与改动前**逐值相同**（0 / 0.05 / 纯精确匹配），
   (b) caplog 里出现含函数名、关键入参、异常类型与文本的 warning；
   **三条各自断言，不得只测一条**。
3. 每条都用**判别式构造**证明回退分支真的被执行了（不是恰好在缺省值上巧合通过）：
   - TTL：先把配置设成 30（健康路径读回 30），再打断 → 必须变回 0；
   - bonus：先把档位设成 0.5（健康路径封顶到 1.0），再打断 → 必须是 0.75；
   - 名字口径：先确认健康路径能召回「相关但非精确」的那条（2 条），再打断 →
     必须只剩精确那条（1 条）。
4. 负向自校准：三条的健康路径都不得出现各自那条 warning（否则第 2 条会被
   「无差别打日志」的噪音恒真满足）。

反证口径（实测记录在交付报告，不写进用例）：把任一处的 `logger.warning(...)`
换回 `pass` → 对应用例的 (b) 断言必须失败（(a) 行为断言仍通过，说明本用例确实钉的是
「有没有痕迹」而不是「有没有返回值」）。
"""

import importlib
import logging
import os
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="memory_governance_failopen_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_failopen.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402
import app.services.rag as rag  # noqa: E402
import app.services.settings_service as settings_service  # noqa: E402

rag.PERSIST_DIR = os.environ["RAG_DIR"]
rag._vs = None
rag._vs_tenant = {}

_TENANT = "t1"
_VENDOR_EXACT = "永隆"
_VENDOR_FUZZY = "永隆行"


def _fresh_db():
    """每个用例独立数据库文件，避免跨用例串扰（与既有记忆用例同款）。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"failopen_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    rag.PERSIST_DIR = os.environ["RAG_DIR"]
    rag._vs = None
    rag._vs_tenant = {}
    return db


def _db_warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "app.db"]


def _row(_db, memory_id):
    """按 memory_id 取回该行（同 vendor 多行时不能用 rows[0] 定位）。"""
    rows = _db.list_vendor_memory(
        _VENDOR_EXACT, tenant_id=_TENANT)
    hit = [r for r in rows if r["memory_id"] == memory_id]
    assert len(hit) == 1, f"memory_id={memory_id} 应恰好命中一行，实际 {len(hit)}"
    return hit[0]


def _boom_factory(text):
    def _boom(*args, **kwargs):
        raise RuntimeError(text)
    return _boom


# -------------------------------------------------------------
# 0. caplog 通道自校准（先证明「抓得到」，再谈「有没有」）
# -------------------------------------------------------------
def test_caplog_channel_self_calibration(caplog):
    """探针 warning 必须被抓到 —— 否则本文件所有「没抓到」的断言都不可信。"""
    with caplog.at_level(logging.WARNING, logger="app.db"):
        logging.getLogger("app.db").warning("calib-probe-D9")
    assert "calib-probe-D9" in _db_warnings(caplog), (
        "caplog 在 app.db logger 上抓不到探针 warning —— 说明通道没接上，"
        "本文件其它用例的「有/无 warning」断言全部失效")


# -------------------------------------------------------------
# 1. memory_ttl_days 读配置失败 → 回退 0 + 留痕
# -------------------------------------------------------------
def test_ttl_read_failure_logs_warning_and_falls_back_to_zero(monkeypatch, caplog):
    _db = _fresh_db()
    # 判别式：先把 TTL 设成 30，证明这一档确实会被读到（否则回退值 0 与缺省 0
    # 无法区分，断言会变成恒真）。
    _db.set_app_setting("memory_ttl_days", "30")
    assert _db.memory_ttl_days() == 30, "健康路径必须先读回配置值"

    monkeypatch.setattr(settings_service, "get_int", _boom_factory("ttl boom"))

    with caplog.at_level(logging.WARNING, logger="app.db"):
        got = _db.memory_ttl_days()

    # (a) 行为与改动前完全一致：回退值仍是 0（不过期），一个不改
    assert got == 0, "回退值必须仍是 0（I2 口径：降级可接受、静默不行）"

    # (b) 失败必须可见
    hit = [m for m in _db_warnings(caplog) if "memory_ttl_days" in m]
    assert hit, f"TTL 读取失败必须打 warning，实际 app.db 日志: {_db_warnings(caplog)}"
    joined = "\n".join(hit)
    assert "RuntimeError" in joined, "warning 必须带异常类型（否则不知道是导入失败还是取值失败）"
    assert "ttl boom" in joined, "warning 必须带异常文本（否则不知道失败原因）"


def test_ttl_healthy_path_has_no_warning(monkeypatch, caplog):
    """负向自校准：健康路径不得出现这条 warning。"""
    _db = _fresh_db()
    monkeypatch.setattr(settings_service, "get_int", lambda key, default=None: 30 if key == "memory_ttl_days" else default)
    with caplog.at_level(logging.WARNING, logger="app.db"):
        assert _db.memory_ttl_days() == 30
    assert not [m for m in _db_warnings(caplog) if "memory_ttl_days" in m], (
        f"读取正常时不得打这条 warning: {_db_warnings(caplog)}")


# -------------------------------------------------------------
# 2. bump_vendor_memory_hit 读 bonus 失败 → 回退 0.05 + 留痕
# -------------------------------------------------------------
def test_bump_hit_bonus_read_failure_logs_warning_and_keeps_default(monkeypatch, caplog):
    _db = _fresh_db()
    # 两行独立记忆：一行走健康路径、一行走回退路径，避免「先强化再强化」把分数
    # 顶到封顶 1.0 后两种路径读数相同（那样断言就失去判别力）。
    mid_healthy, _n1 = _db.upsert_vendor_memory(
        _VENDOR_EXACT, "版式：手写单", "永隆 明细：菜心 2斤 @10 = 20",
        tenant_id=_TENANT, source_kind="manual")
    mid_degraded, _n2 = _db.upsert_vendor_memory(
        _VENDOR_EXACT, "版式：打印单", "永隆 明细：菜心 3斤 @10 = 30",
        tenant_id=_TENANT, source_kind="manual")
    assert _row(_db, mid_healthy)["decay_score"] == 0.7, "起始分缺省 0.7（判别式基准）"

    # 判别式：档位设成 0.5 —— 健康路径 0.7+0.5 封顶到 1.0，回退路径只到 0.75。
    _db.set_app_setting("memory_decay_hit_bonus", "0.5")
    assert _db.bump_vendor_memory_hit([mid_healthy]) == 1
    assert _row(_db, mid_healthy)["decay_score"] == 1.0, (
        "健康路径必须先按配置档位 0.5 强化并封顶 1.0（证明配置档位确实被消费）")

    monkeypatch.setattr(settings_service, "get_float", _boom_factory("bonus boom"))

    with caplog.at_level(logging.WARNING, logger="app.db"):
        n = _db.bump_vendor_memory_hit([mid_degraded])

    # (a) 行为与改动前完全一致：仍按缺省 +0.05 强化，命中照常记账
    assert n == 1, "命中记账本身不得受影响"
    row = _row(_db, mid_degraded)
    assert row["hit_count"] == 1, "命中次数照常自增"
    assert round(row["decay_score"], 6) == 0.75, (
        f"回退值必须是缺省 +0.05（0.7+0.05=0.75，而非配置档位给的 1.0），"
        f"实际 {row['decay_score']}")

    # (b) 失败必须可见：函数名 / 关键入参（memory_ids）/ 异常类型与文本
    hit = [m for m in _db_warnings(caplog) if "memory_decay_hit_bonus" in m]
    assert hit, f"强化档位读取失败必须打 warning，实际 app.db 日志: {_db_warnings(caplog)}"
    joined = "\n".join(hit)
    assert "bump_vendor_memory_hit" in joined, "warning 必须带函数名"
    assert mid_degraded in joined, "warning 必须带 memory_ids（否则不知道哪几条记忆走了回退档）"
    assert "RuntimeError" in joined, "warning 必须带异常类型"
    assert "bonus boom" in joined, "warning 必须带异常文本"


def test_bump_hit_bonus_healthy_path_has_no_warning(caplog):
    """负向自校准：档位读取正常时不得出现这条 warning。"""
    _db = _fresh_db()
    mid, _is_new = _db.upsert_vendor_memory(
        _VENDOR_EXACT, "版式：手写单", "永隆 明细：菜心 2斤 @10 = 20",
        tenant_id=_TENANT, source_kind="manual")
    with caplog.at_level(logging.WARNING, logger="app.db"):
        assert _db.bump_vendor_memory_hit([mid]) == 1
    assert not [m for m in _db_warnings(caplog) if "memory_decay_hit_bonus" in m], (
        f"读取正常时不得打这条 warning: {_db_warnings(caplog)}")


# -------------------------------------------------------------
# 3. _name_matched 加载 _vendor_related 失败 → 退化为纯精确匹配 + 留痕（且写明后果）
# -------------------------------------------------------------
def _seed_name_pair(_db):
    _db.upsert_vendor_memory(
        _VENDOR_EXACT, "版式：手写单", "永隆 明细：菜心 2斤 @10 = 20",
        tenant_id=_TENANT, source_kind="manual")
    _db.upsert_vendor_memory(
        _VENDOR_FUZZY, "版式：打印单", "永隆行 明细：菜心 3斤 @10 = 30",
        tenant_id=_TENANT, source_kind="manual")


def test_name_matched_healthy_path_recalls_fuzzy(monkeypatch, caplog):
    """判别式基准：健康路径必须同时召回「精确」与「相关（模糊）」两条。

    若这一条不成立，下面「退化为纯精确匹配」的断言就是恒真的（模糊通道本来就不命中）。
    """
    _db = _fresh_db()
    _seed_name_pair(_db)
    assert rag._vendor_related(_VENDOR_EXACT, _VENDOR_FUZZY) is True, (
        "基准失效：这一对供应商名在相关度门里必须为 True，否则本组用例无法判别")
    with caplog.at_level(logging.WARNING, logger="app.db"):
        rows = _db.list_vendor_memory(_VENDOR_EXACT, tenant_id=_TENANT)
    assert len(rows) == 2, f"健康路径应召回精确+模糊两条，实际 {[r['vendor'] for r in rows]}"
    assert not [m for m in _db_warnings(caplog) if "_name_matched" in m], (
        f"健康路径不得打这条 warning: {_db_warnings(caplog)}")


def test_name_matched_import_failure_logs_warning_and_degrades_to_exact(monkeypatch, caplog):
    _db = _fresh_db()
    _seed_name_pair(_db)

    # 模拟「_vendor_related 不可用」：直接摘掉该符号 —— 局部导入会抛
    # ImportError，正是本仓 EngineKind 那类「符号拿不到 → 被 except 吞掉」的形态。
    monkeypatch.delattr(rag, "_vendor_related")

    with caplog.at_level(logging.WARNING, logger="app.db"):
        rows = _db.list_vendor_memory(_VENDOR_EXACT, tenant_id=_TENANT)

    # (a) 行为与改动前完全一致：退化为纯精确匹配（只返精确那条），不抛异常
    assert [r["vendor"] for r in rows] == [_VENDOR_EXACT], (
        f"回退行为必须仍是纯精确匹配，实际 {[r['vendor'] for r in rows]}")

    # (b) 失败必须可见：函数名 / 关键入参 / 异常类型与文本，**并且写明后果**
    hit = [m for m in _db_warnings(caplog) if "_name_matched" in m]
    assert hit, f"名字口径退化必须打 warning，实际 app.db 日志: {_db_warnings(caplog)}"
    joined = "\n".join(hit)
    assert "list_vendor_memory" in joined, "warning 必须带函数名（调用方入口）"
    assert _VENDOR_EXACT in joined, "warning 必须带 vendor（否则不知道是哪个供应商在退化）"
    assert _TENANT in joined, "warning 必须带 tenant_id（否则分辨不出租户错位）"
    assert "ImportError" in joined, "warning 必须带异常类型"
    assert "_vendor_related" in joined, "warning 必须带失败的符号名（否则没法排查）"
    assert "本次退化为纯精确匹配，模糊/别名不再命中" in joined, (
        "这一处必须写明后果，否则运维看到一行 warning 不知道意味着什么")
