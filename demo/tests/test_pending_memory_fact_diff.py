# -*- coding: utf-8 -*-
"""W-10 字段级 diff（只读端点旧值投射）验收。

缺陷：人工闸审核界面缺「字段级 diff」—— 审核者看不到 fact_kind / fact_key /
fact_value 的逐字段差异，只能整段接受/拒绝。`db._pending_row_to_dict` 早已透出三列，
但**旧值侧**（同 vendor + fact_key 的当前生效值）无处可取，界面只能显示候选值、
无法区分"新增"与"覆盖既有"。

本用例锁定只读端点 `GET /api/memory/pending` 的 `existing_fact_value` 投射：
1. 同键已有 active 事实 → 待审行带 existing_fact_value == 既有值（覆盖既有场景）；
2. 同键没有生效行 → existing_fact_value 为 None（新增场景，界面显示「新增」而非空串）；
3. 遗留纯文本候选（无结构化字段）→ 三列与旧值列均 None，既有流程不受影响；
4. 非 admin → 403（新投射不得放宽权限门）。

反证（判别力）：
- 摘掉 `api_memory._attach_existing_fact_values` 的调用 → 用例 1 必须失败；
- 把「取不到」写成空串而不是 None → 用例 2 必须失败。

全部离线假数据：独立临时 DB + 临时 RAG 目录，零外部调用、零 live 库写入。
"""

import importlib
import os
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）。
_TMP = tempfile.mkdtemp(prefix="pending_fact_diff_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_diff.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import pytest  # noqa: E402

import app.db as db  # noqa: E402

TENANT = "w10-tenant"
VENDOR = "祥興食品"
ADMIN = {"X-Role": "admin", "X-Tenant-Id": TENANT}
STAFF = {"X-Role": "staff", "X-Tenant-Id": TENANT}

KEY = "alias:黑牛"
OLD_VALUE = "本地牛腩"
NEW_VALUE = "黑牛牌黑胡椒牛油"


@pytest.fixture(autouse=True)
def _fresh_db():
    """每个用例一个干净的库（模块级 DB_PATH 是给 import 期用的兜底）。"""
    old = db.DB_PATH
    path = os.path.join(_TMP, "diff_%s.db" % uuid.uuid4().hex[:8])
    os.environ["DB_PATH"] = path
    importlib.reload(db)
    try:
        yield db
    finally:
        os.environ["DB_PATH"] = old
        db.DB_PATH = old
        db._make_engine()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


def _write_active_fact(value):
    """写一条带 fact_key 的 active 记忆（走生产写入路径，不手抄 INSERT）。"""
    db.upsert_vendor_memory(
        VENDOR, "版式：%s" % KEY, sample="- %s = 1" % value,
        tenant_id=TENANT, source_kind="extract", source_ref="[1494]",
        fact_key=KEY,
        fact_row={"fact_kind": "alias", "fact_value": value, "trust_level": 0.7,
                  "written_by": "owner@demo.hk"})


def _enqueue_candidate(pid_content=NEW_VALUE, structured=True):
    kwargs = {}
    if structured:
        kwargs = dict(fact_kind="alias", fact_key=KEY, fact_value=NEW_VALUE)
    pid, _is_new = db.enqueue_pending_memory(
        VENDOR, pid_content, tenant_id=TENANT, source_kind="extract",
        source_receipt_ids=[1494], **kwargs)
    return pid


def _list(client, headers):
    resp = client.get("/api/memory/pending?status=pending", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_existing_value_projected_for_superseding_candidate(client):
    """同键已有 active 事实 → 旧值侧等于既有值（界面据此渲染覆盖 diff）。"""
    _write_active_fact(OLD_VALUE)
    _enqueue_candidate()

    rows = _list(client, ADMIN)
    assert len(rows) == 1, rows
    row = rows[0]
    # 三列结构化载荷在返回体里（真实返回体为准，本轮不新增这些列）
    assert row["fact_kind"] == "alias"
    assert row["fact_key"] == KEY
    assert row["fact_value"] == NEW_VALUE
    # 旧值侧投射：同 vendor + fact_key 的当前 active 值
    assert row["existing_fact_value"] == OLD_VALUE, row


def test_new_fact_has_null_existing_value(client):
    """同键没有生效行 → existing_fact_value 为 None（界面显示「新增」而非空串）。"""
    _enqueue_candidate()

    rows = _list(client, ADMIN)
    assert len(rows) == 1, rows
    assert rows[0]["existing_fact_value"] is None, rows[0]
    # 字段必须存在（键在返回体里），只是取值为 None
    assert "existing_fact_value" in rows[0]


def test_legacy_text_candidate_unaffected(client):
    """遗留纯文本候选（无结构化字段）→ 三列 + 旧值列均 None，既有流程不受影响。"""
    _enqueue_candidate(pid_content="連續糾偏蒸馏出的自由文本候選", structured=False)

    rows = _list(client, ADMIN)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["fact_kind"] is None
    assert row["fact_key"] is None
    assert row["fact_value"] is None
    assert row["existing_fact_value"] is None
    # 既有契约字段仍在（新投射不得挤掉它们）
    for col in ("id", "vendor", "content", "status", "source_receipt_ids_json"):
        assert col in row


def test_other_vendor_same_key_not_used_as_old_value(client):
    """别的供应商的同名 fact_key 不得被当成旧值（否则 diff 指向错误对象）。"""
    db.upsert_vendor_memory(
        "另一家", "版式：%s" % KEY, sample="- X = 1", tenant_id=TENANT,
        source_kind="extract", source_ref="[1]", fact_key=KEY,
        fact_row={"fact_kind": "alias", "fact_value": "他商的值", "trust_level": 0.7})
    _enqueue_candidate()

    rows = _list(client, ADMIN)
    assert len(rows) == 1, rows
    assert rows[0]["existing_fact_value"] is None, rows[0]


def test_non_admin_still_forbidden(client):
    """非 admin → 403：新投射不得放宽既有权限门。"""
    _write_active_fact(OLD_VALUE)
    _enqueue_candidate()
    resp = client.get("/api/memory/pending?status=pending", headers=STAFF)
    assert resp.status_code == 403, resp.text
