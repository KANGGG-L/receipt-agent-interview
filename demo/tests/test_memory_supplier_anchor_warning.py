# -*- coding: utf-8 -*-
"""I2：【观测性】supplier_id 锚点解析失败时留痕（行为不变，只加 warning）。

why: rag.retrieve_context 里的 P2-T1 主体锚点解析（db.resolve_supplier_id）原先写成
`except Exception: supplier_id = None` —— 退化为名字口径本身是可接受的降级，但**零痕迹**。
一旦锚点解析长期失败（厂家表改名 / 租户错位 / resolve_supplier_id 自身抛错），
记忆会一直按老口径命中而无人发现：这是本仓高发的第③类缺陷（异常被静默吞掉 →
功能静默失效）。本文件把「失败必须可见」变成 CI 红。

锁三件事（全程离线：独立临时 DB + 临时 RAG_DIR，零网络、零 live 库/向量库写入）：

1. 解析抛错时：(a) 不抛异常、(b) 注入结果与名字口径一致（**行为不变**，
   仍退化为名字口径、不返回空串）、(c) caplog 里出现含 vendor / tenant_id / 异常文本的
   warning。三条都断言，其中 (c) 是本任务的价值所在。
2. 自校准：解析**正常**（显式返回 None ＝ 本来就走名字口径）时，不得出现该 warning
   —— 证明这条 warning 是「故障专用」而不是无差别打日志（否则 (c) 会被恒真的噪音满足）。
3. 故障态与健康态返回的上下文**逐字节相同** —— 证明「留痕」没有顺手改动降级行为
   （不是从"返回空"变成"有返回"，也不是换了一套匹配口径）。

反证口径（实测记录在交付报告，不写进用例）：把 rag.py 里那段
`logger.warning(...)` 换回 `pass` → 第 1 条的 (c) 断言必须失败（其余断言仍通过，
说明本用例确实钉的是"有没有痕迹"而不是"有没有返回"）。
"""

import importlib
import logging
import os
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="memory_anchor_warn_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_anchor_warn.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402
import app.services.rag as rag  # noqa: E402

rag.PERSIST_DIR = os.environ["RAG_DIR"]
rag._vs = None
rag._vs_tenant = {}

_VENDOR = "錨點商戶T1"
_TENANT = "t1"
_NOTE = "版式：手写单"
_SAMPLE = "錨點商戶T1 明细：菜心 2斤 @10 = 20"


def _fresh_db():
    """每个用例独立数据库文件，避免跨用例串扰。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"aw_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    rag.PERSIST_DIR = os.environ["RAG_DIR"]
    rag._vs = None
    rag._vs_tenant = {}
    return db


def _seed(vendor=_VENDOR, tenant_id=_TENANT):
    """只落 DB 行（精确通道即可注入），不写向量 —— 本用例不关心相似度通道。"""
    mid, _is_new = db.upsert_vendor_memory(
        vendor, _NOTE, _SAMPLE, tenant_id=tenant_id, source_kind="manual")
    return mid


def _rag_record_texts(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "app.services.rag"]


# -------------------------------------------------------------
# 1. 解析抛错 → 留痕 + 行为不变（三条断言）
# -------------------------------------------------------------
def test_anchor_resolution_failure_logs_warning_and_keeps_name_fallback(monkeypatch, caplog):
    _db = _fresh_db()
    _seed()

    def _boom(vendor_name, tenant_id=None):
        raise RuntimeError("resolve_supplier_id boom")

    monkeypatch.setattr(_db, "resolve_supplier_id", _boom)

    with caplog.at_level(logging.WARNING, logger="app.services.rag"):
        ctx = rag.retrieve_context(_VENDOR, tenant_id=_TENANT)

    # (a) 不抛异常：解析故障不得阻断识别链路
    # (b) 仍返回名字口径的结果：有记忆就必须注得出来，且内容不缩水
    assert ctx.strip() != "", "解析失败必须退化为名字口径并照常注入，不是返回空"
    assert f"[{_VENDOR}]" in ctx
    assert _SAMPLE in ctx

    row = _db.list_vendor_memory(_VENDOR, tenant_id=_TENANT)[0]
    assert row["hit_count"] == 1, "退化路径仍要正常记账（证明注入真的发生了）"

    # (c) 失败必须可见：vendor / tenant_id / 异常文本三者都要能定位问题
    msgs = _rag_record_texts(caplog)
    hit = [m for m in msgs if "resolve_supplier_id" in m]
    assert hit, f"锚点解析失败必须打 warning，实际 rag 日志: {msgs}"
    joined = "\n".join(hit)
    assert _VENDOR in joined, "warning 必须带 vendor（否则不知道是哪个供应商在退化）"
    assert _TENANT in joined, "warning 必须带 tenant_id（否则分辨不出租户错位）"
    assert "boom" in joined, "warning 必须带异常文本（否则不知道失败原因）"


# -------------------------------------------------------------
# 2. 自校准：健康路径（解析成功但本就无锚点）不得出现该 warning
# -------------------------------------------------------------
def test_no_warning_when_anchor_resolution_succeeds_with_none(monkeypatch, caplog):
    _db = _fresh_db()
    _seed()

    monkeypatch.setattr(_db, "resolve_supplier_id", lambda vendor_name, tenant_id=None: None)

    with caplog.at_level(logging.WARNING, logger="app.services.rag"):
        ctx = rag.retrieve_context(_VENDOR, tenant_id=_TENANT)

    assert _SAMPLE in ctx
    msgs = _rag_record_texts(caplog)
    assert not [m for m in msgs if "resolve_supplier_id" in m], (
        "解析正常（无锚点）时不得打这条 warning —— 否则第 1 条的 (c) 会被噪音恒真满足: "
        f"{msgs}")


# -------------------------------------------------------------
# 3. 留痕不得改动降级行为：故障态与健康态结果逐字节相同
# -------------------------------------------------------------
def test_failure_path_result_is_identical_to_name_matching(monkeypatch, caplog):
    _db = _fresh_db()
    _seed()

    monkeypatch.setattr(_db, "resolve_supplier_id", lambda vendor_name, tenant_id=None: None)
    with caplog.at_level(logging.WARNING, logger="app.services.rag"):
        healthy = rag.retrieve_context(_VENDOR, tenant_id=_TENANT)

    def _boom(vendor_name, tenant_id=None):
        raise RuntimeError("resolve_supplier_id boom")

    monkeypatch.setattr(_db, "resolve_supplier_id", _boom)
    with caplog.at_level(logging.WARNING, logger="app.services.rag"):
        degraded = rag.retrieve_context(_VENDOR, tenant_id=_TENANT)

    assert degraded == healthy, "留痕只是加日志：降级结果必须与名字口径逐字节一致"
