# -*- coding: utf-8 -*-
"""WS5：价格异动处置闭环（POST /api/inventory/{sku_id}/price_anomaly_ack）+ 埋点。

why：前端此前的「忽略」只是内存 Set，刷新即复位，`price_anomaly_adopted` 永远
       采集不到。WS5 把处置落 `price_anomaly_ack`（同 SKU 不堆叠）并同步发
       内部埋点。这两件事都必须被钉住：状态持久化错了用户白点，埋点丢了整个
       「采纳率」指标就是死的。

覆盖：
1. 权限：owner 200 / staff 403；非法 action → 400；未知 SKU → 404
2. price_anomaly_ack 同 SKU（同租户）不堆叠：先 adopt 后 ignore 只留一行，
   且表上存在 (sku_id, tenant_id) 唯一索引
3. adopt / ignore 分别落 price_anomaly_adopted / price_anomaly_dismissed
   到 user_event，且带租户
4. 跨租户隔离：门店处置不影响默认租户视图，跨租户 SKU 视为不存在

全部离线假数据：模块级独立临时 DB + 临时 RAG 目录，零外部调用、零 live 写入。
"""

import importlib
import json
import os
import sqlite3
import sys
import tempfile
import uuid

_TMP = tempfile.mkdtemp(prefix="price_anomaly_ack_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_price_anomaly_ack.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402


def _fresh_db():
    """每个用例独立数据库文件，避免跨用例串扰。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"ack_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    return db


def _client():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    return TestClient(fastapi_app)


def _sku(_db, name, tenant="default"):
    sku_id, err = _db.create_sku(name, category="食材", base_unit="斤",
                                 tenant_id=tenant)
    assert sku_id, f"建 SKU 失败：{err}"
    return sku_id


def _headers(role="owner", tenant=None):
    h = {"X-Role": role}
    if tenant:
        h["X-Tenant-Id"] = tenant
    return h


def _ack(client, sku_id, action, role="owner", tenant=None):
    return client.post(f"/api/inventory/{sku_id}/price_anomaly_ack",
                       json={"action": action},
                       headers=_headers(role, tenant))


def _events(_db, event_type, tenant=None):
    """读 user_event 里指定类型的事件（含 properties 解析）。"""
    s = _db.get_session()
    try:
        q = s.query(_db._UserEventRow).filter(
            _db._UserEventRow.event_type == event_type)
        if tenant is not None:
            q = q.filter(_db._UserEventRow.tenant_id == tenant)
        out = []
        for r in q.all():
            try:
                props = json.loads(r.properties or "{}")
            except Exception:
                props = {}
            out.append({"tenant_id": r.tenant_id, "properties": props})
        return out
    finally:
        s.close()


# -------------------------------------------------------------
# 1. 权限 / 校验 / 未知 SKU
# -------------------------------------------------------------
def test_owner_ok_staff_403_invalid_action_400_and_404():
    _db = _fresh_db()
    client = _client()
    sku = _sku(_db, "處置菜心")

    staff = _ack(client, sku, "adopt", role="staff")
    assert staff.status_code == 403, staff.text

    for bad in ("explode", "", "IGNORE_ME"):
        ret = _ack(client, sku, bad)
        assert ret.status_code == 400, (bad, ret.status_code, ret.text)

    unknown = _ack(client, 999999, "adopt")
    assert unknown.status_code == 404, unknown.text


# -------------------------------------------------------------
# 2. 同 SKU 不堆叠 + 唯一索引形态
# -------------------------------------------------------------
def test_same_sku_does_not_stack_and_unique_index_exists():
    _db = _fresh_db()
    client = _client()
    sku = _sku(_db, "不堆疊腩肉")

    first = _ack(client, sku, "adopt")
    assert first.status_code == 200, first.text
    assert first.json()["data"]["action"] == "adopt"

    second = _ack(client, sku, "ignore")
    assert second.status_code == 200, second.text
    assert second.json()["data"]["action"] == "ignore"

    # 数据访问层：同一 SKU 只有一条处置行，action 为最新
    row = _db.get_price_anomaly_ack(sku)
    assert row is not None and row["action"] == "ignore", row
    acks = _db.list_price_anomaly_acks()
    assert list(acks) == [sku], acks

    conn = sqlite3.connect(_db.DB_PATH)
    cnt = conn.execute(
        "SELECT COUNT(*) FROM price_anomaly_ack WHERE sku_id = ?",
        (sku,)).fetchone()[0]
    # 表形态：存在 (sku_id, tenant_id) 唯一索引（DB 层兜底不堆叠）
    unique_idx = {r[1]: r[2] for r in
                  conn.execute("PRAGMA index_list(price_anomaly_ack)")}
    cols = [r[2] for r in conn.execute(
        "PRAGMA index_info(ux_price_anomaly_ack_sku_tenant)")]
    conn.close()
    assert cnt == 1, f"同 SKU 不得堆叠，实际 {cnt} 行"
    assert unique_idx.get("ux_price_anomaly_ack_sku_tenant") == 1, unique_idx
    assert cols == ["sku_id", "tenant_id"], cols


# -------------------------------------------------------------
# 3. 埋点：adopt → price_anomaly_adopted；ignore → price_anomaly_dismissed
# -------------------------------------------------------------
def test_adopt_and_ignore_emit_internal_events():
    _db = _fresh_db()
    client = _client()
    sku = _sku(_db, "埋點豆腐")

    assert _ack(client, sku, "adopt").status_code == 200
    adopted = _events(_db, "price_anomaly_adopted")
    assert len(adopted) == 1, adopted
    assert adopted[0]["tenant_id"] == "default", adopted
    assert adopted[0]["properties"]["sku_id"] == sku, adopted
    assert adopted[0]["properties"]["action"] == "adopt", adopted
    assert "threshold_pct" in adopted[0]["properties"], adopted

    assert _ack(client, sku, "ignore").status_code == 200
    dismissed = _events(_db, "price_anomaly_dismissed")
    assert len(dismissed) == 1, dismissed
    assert dismissed[0]["properties"]["action"] == "ignore", dismissed
    # 两种事件互不混淆：adopted 仍只有第一次那一条
    assert len(_events(_db, "price_anomaly_adopted")) == 1


# -------------------------------------------------------------
# 4. 跨租户隔离
# -------------------------------------------------------------
def test_cross_tenant_isolation():
    _db = _fresh_db()
    client = _client()
    sku_t2 = _sku(_db, "他租戶處置商品", tenant="t2")

    # 默认租户（owner）拿不到 t2 的 SKU：处置视为不存在
    assert _ack(client, sku_t2, "adopt").status_code == 404
    # t2 内正常处置
    ok = _ack(client, sku_t2, "adopt", tenant="t2")
    assert ok.status_code == 200, ok.text

    fake = uuid.uuid4().hex[:8]
    assert _db.get_price_anomaly_ack(sku_t2, tenant_id="t2") is not None
    assert _db.get_price_anomaly_ack(sku_t2, tenant_id="default") is None
    assert _db.get_price_anomaly_ack(sku_t2) is None, "默认租户不得看到他租户处置"
    assert sku_t2 not in _db.list_price_anomaly_acks()

    # 埋点租户随请求租户落库
    events = _events(_db, "price_anomaly_adopted", tenant="t2")
    assert len(events) == 1 and events[0]["properties"]["sku_id"] == sku_t2, events

    conn = sqlite3.connect(_db.DB_PATH)
    cnt = conn.execute(
        "SELECT COUNT(*) FROM price_anomaly_ack WHERE sku_id = ? AND"
        " tenant_id = ?", (sku_t2, "t2")).fetchone()[0]
    conn.close()
    assert cnt == 1, (cnt, fake)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
