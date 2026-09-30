# -*- coding: utf-8 -*-
"""C3：记忆度量看板只读端点 GET /api/admin/memory-metrics 的回归测试。

why：管理台看板与 CLI 基线脚本（demo/scripts/memory_metrics.py）必须给出**同一组数字**，
否则「看板 23.7% 而脚本 17.0%」这类口径分叉没有任何人能察觉（本仓①类缺陷形态）。
本文件因此把「端点输出 == SSOT 脚本 collect() 输出」写成断言，而不是各自校验一遍公式。

覆盖：
1. 口径一致性：端点 payload["metrics"] 与脚本 collect() 逐字段相等（同库、同一次调用口径）。
2. 分母锁死：命中率的分母是**全部单据**（不是"有记忆的供应商数"、也不是分子），
   用一份种子数据算出独立期望值，防止有人把分子当分母。
3. 分母为 0（空库）时，端点仍返回分母字段（=0），让前端能显示「无数据」而不是 0% 冒充。
4. 只读：端点调用前后临时库 md5 不变（mode=ro），live 库与两个 live 向量库 md5 不变。
5. 权限：匿名 401；staff / owner 403（本端点仅 admin）；admin 200。
6. 失败不抛 500：取数异常返回 HTTP 200 + status=error + metrics=None（前端据此不显示任何比率）。
7. memory_enabled 状态与 C1 的唯一实现 rag.memory_enabled() 一致（含显式关闭态）。

反证口径（交付说明里给实测记录，不写进用例）：把端点里 `metrics["memory_hit"]["rate_pct"]`
改成用分子顶替分母（例如直接赋 1.0 或换成 total/with_ctx），第 1、2 条用例必须失败。

隔离：本模块 DB_PATH / RAG_DIR 全部指向临时目录（在首次 import app.* 之前设置），
零网络、零 live 库写入、零 live 向量库写入。
"""

import hashlib
import importlib
import importlib.util
import os
import sqlite3
import sys
import tempfile
import uuid

import pytest

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="memory_metrics_dash_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_memory_dash.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
_REPO_ROOT = os.path.abspath(os.path.join(_DEMO_DIR, ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402
import app.services.rag as rag  # noqa: E402

rag.PERSIST_DIR = os.environ["RAG_DIR"]

_SCRIPT = os.path.join(_DEMO_DIR, "scripts", "memory_metrics.py")
_LIVE_DB = os.path.join(_DEMO_DIR, "receipt_demo.db")
_LIVE_RAG_DIRS = [os.path.join(_DEMO_DIR, ".rag_chroma"),
                  os.path.join(_REPO_ROOT, ".rag_chroma")]


# -------------------------------------------------------------
# 环境与取数辅助
# -------------------------------------------------------------
def _fresh_db():
    """每个用例独立数据库文件，避免跨用例串扰（含 app_settings 的开关值）。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, "dash_%s.db" % uuid.uuid4().hex[:8])
    importlib.reload(db)
    return db


def _client():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    return TestClient(fastapi_app)


def _seed(_db, receipts=(), items=(), decisions=(), vendors=()):
    """按真实 schema 写种子数据（手抄 DDL 会让"列名不匹配"这类缺陷逃过测试）。

    receipts  = [(id, supplier_name, status, rag_context_json)]
    items     = [(id, receipt_id, name, matched)]
    decisions = [(id, adopted)]      # decision_type 固定 extract，is_hallucination=0
    vendors   = [vendor_name]        # vendor_memory 的 active 行
    """
    conn = sqlite3.connect(_db.DB_PATH)
    try:
        if receipts:
            conn.executemany(
                "INSERT INTO receipts (id, supplier_name, status, rag_context_json) "
                "VALUES (?, ?, ?, ?)", list(receipts))
        if items:
            conn.executemany(
                "INSERT INTO receipt_items (id, receipt_id, name, matched) "
                "VALUES (?, ?, ?, ?)", list(items))
        if decisions:
            conn.executemany(
                "INSERT INTO ai_decision_log (id, decision_type, adopted, is_hallucination) "
                "VALUES (?, 'extract', ?, 0)", list(decisions))
        if vendors:
            conn.executemany(
                "INSERT INTO vendor_memory (memory_id, vendor, tenant_id, notes, sample, status) "
                "VALUES (?, ?, 'default', '', '', 'active')",
                [("mid-%d" % i, v) for i, v in enumerate(vendors)])
        conn.commit()
    finally:
        conn.close()


def _get_metrics(client):
    return client.get("/api/admin/memory-metrics", headers={"X-Role": "admin"})


def _ssot_collect(db_path):
    """按文件路径加载 SSOT 脚本并取数（每次新模块实例，确保 DB 不被上一次污染）。"""
    spec = importlib.util.spec_from_file_location(
        "mm_probe_%s" % uuid.uuid4().hex[:8], _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.DB = db_path
    return mod.collect()


def _md5(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def _rag_fingerprint():
    """live 向量库指纹：目录不存在记 "ABSENT"，否则记 (文件数, sqlite md5)。"""
    out = []
    for d in _LIVE_RAG_DIRS:
        if not os.path.isdir(d):
            out.append((d, "ABSENT"))
            continue
        files = []
        for root, _dirs, names in os.walk(d):
            for n in sorted(names):
                files.append(os.path.join(root, n))
        sqlite = os.path.join(d, "chroma.sqlite3")
        digest = _md5(sqlite) if os.path.isfile(sqlite) else "-"
        out.append((d, (len(files), digest)))
    return out


# 一份覆盖四组指标的种子数据（独立期望值见下一条用例）
_SEED_RECEIPTS = [
    (1, "德利行", "edited", '{"vendor": "德利行"}'),   # 有记忆 + 被编辑
    (2, "德利行", "parsed", "[{\"a\": 1}]"),           # 有记忆 + 未编辑
    (3, "新商號", "edited", ""),                       # 无记忆 + 被编辑
    (4, "新商號", "approved", "{}"),                   # 无记忆 + 未编辑
]
_SEED_ITEMS = [(1, 1, "菜心", 1), (2, 1, "腩肉", 0), (3, 2, "豆腐", 1)]
_SEED_DECISIONS = [(1, 1), (2, 0), (3, 1), (4, 0), (5, 1)]
_SEED_VENDORS = ["德利行"]


# -------------------------------------------------------------
# 1. 口径一致性：端点 == SSOT 脚本（同一库、逐字段相等）
# -------------------------------------------------------------
def test_endpoint_metrics_equal_ssot_module_output():
    _db = _fresh_db()
    _seed(_db, _SEED_RECEIPTS, _SEED_ITEMS, _SEED_DECISIONS, _SEED_VENDORS)
    client = _client()

    ret = _get_metrics(client)
    assert ret.status_code == 200, ret.text
    body = ret.json()
    assert body["status"] == "success", body
    expected = _ssot_collect(_db.DB_PATH)

    # 分母是 SSOT 的一部分口径，必须一并相等
    assert body["metrics"] == expected, (body["metrics"], expected)
    for key in ("memory_hit", "sku_match", "edit_rate_ab", "decision_log"):
        assert key in body["metrics"], "缺口径字段: %s" % key


# -------------------------------------------------------------
# 2. 分母锁死：用独立期望值钉住「分母是什么」
# -------------------------------------------------------------
def test_metric_denominators_are_pinned():
    _db = _fresh_db()
    _seed(_db, _SEED_RECEIPTS, _SEED_ITEMS, _SEED_DECISIONS, _SEED_VENDORS)
    client = _client()
    m = _get_metrics(client).json()["metrics"]

    # 命中率：分母=全部单据 4（不是"有记忆供应商"1，也不是分子 2）
    assert m["memory_hit"]["total_receipts"] == 4, m["memory_hit"]
    assert m["memory_hit"]["with_memory_context"] == 2, m["memory_hit"]
    assert m["memory_hit"]["rate_pct"] == 50.0, m["memory_hit"]

    # SKU 匹配率：分母=明细行 3，分子=matched 2
    assert m["sku_match"]["items"] == 3 and m["sku_match"]["matched"] == 2, m["sku_match"]
    assert m["sku_match"]["rate_pct"] == 66.67, m["sku_match"]

    # 修改率 A/B：有记忆组 2 单（1 编辑）vs 无记忆组 2 单（1 编辑）
    ab = m["edit_rate_ab"]
    assert ab["has_memory"]["receipts"] == 2 and ab["has_memory"]["edited"] == 1, ab
    assert ab["no_memory"]["receipts"] == 2 and ab["no_memory"]["edited"] == 1, ab
    assert ab["has_memory"]["edited_rate_pct"] == 50.0, ab
    assert ab["no_memory"]["edited_rate_pct"] == 50.0, ab
    assert ab["delta_pp"] == 0.0, ab

    # 未采纳率：分母=决策总数 5，分子=adopted=0 的 2 条
    assert m["decision_log"]["decisions"] == 5, m["decision_log"]
    assert m["decision_log"]["not_adopted"] == 2, m["decision_log"]
    assert m["decision_log"]["not_adopted_rate_pct"] == 40.0, m["decision_log"]


# -------------------------------------------------------------
# 3. 空库：分母为 0 也必须原样返回（前端据此显示「无数据」）
# -------------------------------------------------------------
def test_empty_db_returns_zero_denominators_for_no_data_rendering():
    _db = _fresh_db()
    client = _client()
    body = _get_metrics(client).json()
    assert body["status"] == "success", body
    m = body["metrics"]
    # 分母字段必须在且为 0 —— 前端唯一的「无数据」判据，缺了就只能显示 0% 冒充
    assert m["memory_hit"]["total_receipts"] == 0, m["memory_hit"]
    assert m["sku_match"]["items"] == 0, m["sku_match"]
    assert m["decision_log"]["decisions"] == 0, m["decision_log"]
    assert "has_memory" not in m["edit_rate_ab"], m["edit_rate_ab"]


# -------------------------------------------------------------
# 4. 只读：临时库 md5 不变 + live 库/向量库 md5 不变
# -------------------------------------------------------------
def test_endpoint_is_read_only():
    _db = _fresh_db()
    _seed(_db, _SEED_RECEIPTS, _SEED_ITEMS, _SEED_DECISIONS, _SEED_VENDORS)
    client = _client()
    _get_metrics(client)                       # 预热（首次请求可能触发懒加载）
    before_live = _md5(_LIVE_DB) if os.path.isfile(_LIVE_DB) else "ABSENT"
    before_tmp = _md5(_db.DB_PATH)
    before_rag = _rag_fingerprint()

    for _ in range(3):
        assert _get_metrics(client).status_code == 200

    assert _md5(_db.DB_PATH) == before_tmp, "端点写了自己的库（应为 mode=ro 只读）"
    live_after = _md5(_LIVE_DB) if os.path.isfile(_LIVE_DB) else "ABSENT"
    assert live_after == before_live, "端点动了 live 库"
    assert _rag_fingerprint() == before_rag, "端点动了 live 向量库"


# -------------------------------------------------------------
# 5. 权限：仅 admin
# -------------------------------------------------------------
def test_only_admin_can_read():
    _db = _fresh_db()
    client = _client()

    anon = client.get("/api/admin/memory-metrics")
    assert anon.status_code == 401, anon.text

    for role in ("staff", "owner"):
        ret = client.get("/api/admin/memory-metrics", headers={"X-Role": role})
        assert ret.status_code == 403, (role, ret.status_code, ret.text)

    ok = client.get("/api/admin/memory-metrics", headers={"X-Role": "admin"})
    assert ok.status_code == 200, ok.text


# -------------------------------------------------------------
# 6. 失败不抛 500：取数异常 → 200 + error 字段 + metrics=None
# -------------------------------------------------------------
def test_collect_failure_returns_error_field_not_500(monkeypatch):
    _db = _fresh_db()
    client = _client()

    from app import api_admin

    def _boom():
        raise RuntimeError("模拟度量脚本不可加载")

    monkeypatch.setattr(api_admin, "_load_memory_metrics_module", _boom)
    ret = _get_metrics(client)
    assert ret.status_code == 200, ret.text
    body = ret.json()
    assert body["status"] == "error", body
    assert "msg" in body and body["msg"], body
    # metrics=None 是「一个比率都不许显示」的契约（前端据此显示错误态而非 0%）
    assert body["metrics"] is None, body


def test_unreadable_db_returns_error_field_not_500(tmp_path):
    """库文件不可读（指向不存在的路径）同样走 error 分支，不抛 500。"""
    _db = _fresh_db()
    client = _client()
    _db.DB_PATH = os.path.join(str(tmp_path), "does_not_exist.db")
    ret = _get_metrics(client)
    assert ret.status_code == 200, ret.text
    assert ret.json()["status"] == "error", ret.json()


# -------------------------------------------------------------
# 7. memory_enabled 与 C1 的唯一实现一致（true / false 两态）
# -------------------------------------------------------------
def test_memory_enabled_reflects_rag_switch():
    _db = _fresh_db()
    client = _client()

    # 未设置 → 缺省开启，且必须与 rag.memory_enabled() 同值
    assert _get_metrics(client).json()["memory_enabled"] is True
    assert rag.memory_enabled() is True

    from app.services import settings_service
    settings_service.set_value("memory_enabled", False)
    body = _get_metrics(client).json()
    assert body["memory_enabled"] is False, body
    assert body["memory_enabled"] == rag.memory_enabled()

    settings_service.set_value("memory_enabled", True)
    assert _get_metrics(client).json()["memory_enabled"] is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
