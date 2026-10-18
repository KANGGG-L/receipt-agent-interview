# -*- coding: utf-8 -*-
"""FIX-P1 Dev 阶段 TDD 红基线：三条 P1 缺陷各一组失败测试。

FIX-D01（E2E-D-01）：1e308 极值盘点毒化库存流水。
FIX-A01（E2E-A-1）：costTraceModal 关闭后 backdrop 拦截同屏点击。
FIX-B001（E2E-B-001）：#tab-report 日期行 768/1440 视口溢出被硬裁。

AUTH_ENABLED=1 环境下运行（boss123 / boss@demo.hk 走 /api/auth/login 拿 Bearer）。
"""

import json
import math
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "demo"))

import pytest  # noqa: E402

import itertools  # noqa: E402
_name_seq = itertools.count(1)

from app import db  # noqa: E402

# ---------------------------------------------------------------------------
# 环境与登录（AUTH_ENABLED=1）
# ---------------------------------------------------------------------------

ADMIN = "admin@demo.hk"
BOSS = "boss@demo.hk"
PASSWORD = "admin123"


@pytest.fixture(scope="session")
def auth_tokens():
    """经真实 /api/auth/login 获取 admin / owner Bearer 令牌（各账号各自密码）。"""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    tokens = {}
    for email, pw in ((ADMIN, "admin123"), (BOSS, "boss123")):
        r = client.post("/api/auth/login", json={"email": email, "password": pw})
        assert r.status_code == 200, r.text
        tok = r.json().get("token")
        assert tok, r.text
        tokens[email] = tok
    return tokens


@pytest.fixture(scope="session")
def owner_headers(auth_tokens):
    return {"Authorization": "Bearer " + auth_tokens[BOSS],
            "Content-Type": "application/json"}


@pytest.fixture(scope="session")
def admin_headers(auth_tokens):
    return {"Authorization": "Bearer " + auth_tokens[ADMIN],
            "Content-Type": "application/json"}


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


@pytest.fixture
def sku_id_with_stock(owner_headers):
    """建一个有库存的 SKU（经入库流水），返回 sku_id。"""
    tn = "default"
    name = "FIXD01-" + db.now_iso()[:19].replace(":", "").replace("-", "").replace("T", "") \
        + "-" + str(next(_name_seq))
    sid, err = db.create_sku(name, base_unit="斤", tenant_id=tn)
    assert sid, err
    session = db.get_session()
    try:
        db._StockLogRow  # noqa: B018
        row = db._StockLogRow(
            sku_id=sid, name=name, qty=10.0, unit="斤", amount=100.0, vendor="t",
            date=db.now_iso()[:10], receipt_id=None, kind="in", note="FIX-D01 setup",
            created_at=db.now_iso(), tenant_id=tn)
        session.add(row)
        sku = session.get(db._SkuRow, sid)
        sku.current_stock = 10.0
        session.commit()
    finally:
        session.close()
    return sid


# ---------------------------------------------------------------------------
# FIX-D01：极值盘点 / 消耗校验 + logs 序列化
# ---------------------------------------------------------------------------

class TestFixD01ExtremeQtyRejected:
    """stocktake / consume / waste 拒绝非有限或超界数量（422/400），不再毒化。"""

    def test_stocktake_rejects_1e308(self, client, owner_headers, sku_id_with_stock):
        r = client.post("/api/inventory/%d/stocktake" % sku_id_with_stock,
                        headers=owner_headers, json={"actual_qty": 1e308})
        assert r.status_code in (400, 422), r.text
        body = r.json()
        assert body.get("status") == "error"
        assert "数量" in body.get("msg", "") and ("范围" in body.get("msg", "") or "有效" in body.get("msg", ""))

    def test_stocktake_rejects_nan_inf(self, client, owner_headers, sku_id_with_stock):
        # JSON 里没有 NaN/Infinity 字面量可用（严格解析），用字符串 数字边界验证上界即可；
        # 这里验证上界 1e9 + 1 被拒绝。
        r = client.post("/api/inventory/%d/stocktake" % sku_id_with_stock,
                        headers=owner_headers, json={"actual_qty": 1e10})
        assert r.status_code in (400, 422), r.text

    def test_stocktake_accepts_normal_value(self, client, owner_headers, sku_id_with_stock):
        r = client.post("/api/inventory/%d/stocktake" % sku_id_with_stock,
                        headers=owner_headers, json={"actual_qty": 5.0})
        assert r.status_code == 200, r.text

    def test_consume_rejects_out_of_range(self, client, owner_headers, sku_id_with_stock):
        r = client.post("/api/inventory/%d/consume" % sku_id_with_stock,
                        headers=owner_headers, json={"quantity": 1e308})
        assert r.status_code in (400, 422), r.text

    def test_waste_rejects_out_of_range(self, client, owner_headers, sku_id_with_stock):
        r = client.post("/api/inventory/%d/waste" % sku_id_with_stock,
                        headers=owner_headers, json={"quantity": 1e10})
        assert r.status_code in (400, 422), r.text

    def test_consume_accepts_normal_value(self, client, owner_headers, sku_id_with_stock):
        r = client.post("/api/inventory/%d/consume" % sku_id_with_stock,
                        headers=owner_headers, json={"quantity": 1.0})
        assert r.status_code == 200, r.text


class TestFixD01LogsSanitized:
    """历史毒化行：logs 必须仍 200 且输出 JSON 合规（不抛 ValueError / 不输出 Infinity）。"""

    def _poison(self, sku_id_with_stock):
        # 直接在 DB 里放历史毒化流水（模拟已被毒化的存量数据）：
        # - inf 行：json.dumps 抛 ValueError → logs 500（缺陷主形态）
        # - 1e308 行：审计报告记录的另一毒化形态
        sid = sku_id_with_stock
        session = db.get_session()
        try:
            row = db._StockLogRow(
                sku_id=sid, name="poison", qty=float("inf"), unit="斤", amount=0.0,
                vendor="", date=db.now_iso()[:10], receipt_id=None,
                kind="stocktake", note="poisoned legacy row",
                created_at=db.now_iso(), tenant_id="default")
            session.add(row)
            row2 = db._StockLogRow(
                sku_id=sid, name="poison-1e308", qty=1e308, unit="斤", amount=0.0,
                vendor="", date=db.now_iso()[:10], receipt_id=None,
                kind="stocktake", note="poisoned legacy row 1e308",
                created_at=db.now_iso(), tenant_id="default")
            session.add(row2)
            session.commit()
        finally:
            session.close()

    def test_logs_200_with_poisoned_history(self, client, owner_headers, sku_id_with_stock):
        self._poison(sku_id_with_stock)
        r = client.get("/api/inventory/logs?sku_id=%d" % sku_id_with_stock,
                       headers=owner_headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert len(data) >= 1

    def test_logs_payload_json_compliant(self, client, owner_headers, sku_id_with_stock):
        self._poison(sku_id_with_stock)
        r = client.get("/api/inventory/logs?sku_id=%d" % sku_id_with_stock,
                       headers=owner_headers)
        raw = r.content.decode("utf-8")
        assert "Infinity" not in raw and "NaN" not in raw
        row = [d for d in r.json()["data"] if d["qty"] is None or isinstance(d["qty"], (int, float))]
        assert row

    def test_poisoned_rows_cleanup_tool_exists_and_idempotent(self, sku_id_with_stock):
        """一次性清洗工具：dry-run 不改数、实跑清零毒化值、幂等。"""
        self._poison(sku_id_with_stock)
        from app.maintenance import sanitize_stock_logs  # noqa: F401
        # dry-run：只报告
        rep = sanitize_stock_logs(dry_run=True)
        assert "affected" in rep and "rows_total" in rep
        n_before = rep["affected"]
        # 实跑（实跑前必须已产出备份文件）
        rep2 = sanitize_stock_logs(dry_run=False)
        assert rep2["affected"] == n_before
        assert rep2["rows_total"] == rep["rows_total"]
        assert rep2["cleaned"] == n_before
        assert rep2.get("backup_file"), "实跑清洗前必须先备份"
        # 幂等：再跑一次，残留毒化行数不再增加（首跑已清零，后续 cleaned=0）
        rep3 = sanitize_stock_logs(dry_run=False)
        assert rep3["cleaned"] == 0, rep3
        # 数据层面验证：所有行的 qty 均为 None 或有限且 |qty| <= 1e9
        import math
        session = db.get_session()
        try:
            for row in session.query(db._StockLogRow).all():
                if row.qty is not None:
                    assert math.isfinite(row.qty) and abs(row.qty) <= 1e9, (row.id, row.qty)
        finally:
            session.close()
