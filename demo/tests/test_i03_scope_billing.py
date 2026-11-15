# -*- coding: utf-8 -*-
"""I03 技术聚合与计费身份分离：AC 三例（backlog I03 / C-IAM）。

- test_scope_query_cannot_expand：无平台技术聚合能力的 admin 不得借 query/缺省扩大到全租户；
- test_admin_technical_only_no_finance_path：平台技术端点无财务字段、无宿主绝对路径、
  且平台身份进不了商户财务端点；
- test_billing_role_independent：billing 角色显式授予，restaurant owner / 平台 admin 不继承。
"""

import json
import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

_TMP = tempfile.mkdtemp(prefix="test_i03_scope_")
_MODULE_DB_PATH = os.path.join(_TMP, "test_i03.db")
os.environ["DB_PATH"] = _MODULE_DB_PATH
os.environ["RAG_DIR"] = os.path.join(_TMP, "test_rag")
os.environ["AUTH_ENABLED"] = "1"
os.environ["IDENTITY_V2_ENABLED"] = "1"
os.environ.setdefault("DEMO_TOKEN_SECRET", "i03-test-secret-not-the-default-value")

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
for _p in (_DEMO_DIR, os.path.abspath(os.path.join(_DEMO_DIR, ".."))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import auth, db, identity_store  # noqa: E402
from app.main import app  # noqa: E402

# 全量收集下 app.db 可能已被字母序靠前的文件 import：模块级 DB_PATH 冻结在
# 首个 import 者的环境上，直接 _make_engine() 仍指向别人的库。这里显式把
# 模块指回本文件声明的环境库并重建引擎，否则下方模块级种子（身份/计费/
# tenant_b 数据）会落进别人的库，用例读不到（standalone 通过、全量失败的
# 经典形态）。
db.DB_PATH = os.path.abspath(os.environ["DB_PATH"])
db._make_engine()

# admin2：有 admin 角色令牌但无 platform_roles 行（无技术聚合能力）
auth.ACCOUNTS["admin2@example.test"] = ("i03-task-only", "admin", "default")
identity_store.set_platform_role("admin@demo.hk")
# 计费身份（显式授予）与餐厅身份互不继承
auth.ACCOUNTS["billing1@example.test"] = ("i03-task-only", "staff", "default")
identity_store.grant_billing_membership("billing1@example.test", "default", "billing_owner")
identity_store.grant_tenant_membership("boss@demo.hk", "default", "owner")

db.create_receipt(supplier_name="Vendor_B", status="parsed", tenant_id="tenant_b")

client = TestClient(app)


@pytest.fixture(autouse=True)
def _pin_module_db():
    """把每条用例钉回本模块声明的环境库 test_i03.db。

    模块级种子（上方身份/计费/tenant_b）落在 import 期的引擎上；全量收集下
    字母序靠后的文件会在模块级改写 os.environ["DB_PATH"]，conftest 的 S1 隔离
    判据据此把用例隔离到陌生临时库 → 种子「丢失」（standalone 通过、全量翻红的
    根因）。夹具兜底与该时序无关，恒指回本模块自己的库。
    """
    old = db.DB_PATH
    db.DB_PATH = _MODULE_DB_PATH
    db._make_engine()
    yield
    db.DB_PATH = old
    db._make_engine()


def _hdr(email, role, tenant="default"):
    return {"Authorization": "Bearer " + auth.issue_token(email, role, tenant)}


ADMIN_PLATFORM = _hdr("admin@demo.hk", "admin")
ADMIN_NOCAP = _hdr("admin2@example.test", "admin")


def test_scope_query_cannot_expand():
    # 无能力 admin 请求 tenant_id=all：RBAC 通过（require_admin 只看平台角色令牌），
    # 但 scope 锚定回自身租户——available_tenants 不得包含 tenant_b
    r = client.get("/api/analytics/recovery-summary?tenant_id=all", headers=ADMIN_NOCAP)
    assert r.status_code == 200, r.status_code
    body = r.json()
    assert "tenant_b" not in json.dumps(body), "scope expanded beyond caller tenant"
    # 持平台能力 admin：可见全租户列表（含 tenant_b 的技术标识）
    r2 = client.get("/api/analytics/recovery-summary?tenant_id=all", headers=ADMIN_PLATFORM)
    assert r2.status_code == 200, r2.status_code
    assert "tenant_b" in json.dumps(r2.json())


def test_admin_technical_only_no_finance_path():
    r = client.get("/api/analytics/recovery-summary", headers=ADMIN_PLATFORM)
    assert r.status_code == 200, r.status_code
    text = json.dumps(r.json())
    for forbidden_key in ('"total_amount"', '"unit_price"', '"paid_amount"', '"amount_minor"'):
        assert forbidden_key not in text, forbidden_key
    # 无宿主绝对路径泄漏（沙箱 TMPDIR/DB 路径不出现）
    assert _TMP not in text and ".db" not in text
    # 平台身份进不了商户财务/业务端点（D-02，与 I02 矩阵互证）
    for path in ("/api/payables", "/api/cost_report", "/api/receipts"):
        assert client.get(path, headers=ADMIN_PLATFORM).status_code == 403, path


def test_billing_role_independent():
    # 餐厅 owner：有商户业务能力，无任何 billing 能力
    owner_caps = identity_store.capabilities_for("boss@demo.hk", "default")
    assert "receipt_approve" in owner_caps
    assert not any(c.startswith("billing:") for c in owner_caps)
    # billing_owner：仅有 billing 能力，无商户业务能力（未授予 membership）
    b_caps = identity_store.capabilities_for("billing1@example.test", "default")
    assert b_caps == {"billing:billing_owner"}
    assert "receipt_approve" not in b_caps and "finance_read" not in b_caps
    # 平台 admin：平台能力不自动带 billing 能力
    a_caps = identity_store.capabilities_for("admin@demo.hk", "default")
    assert not any(c.startswith("billing:") for c in a_caps)
    # billing 身份令牌进不了商户业务端点（HTTP 语义）
    r = client.get("/api/receipts", headers=_hdr("billing1@example.test", "staff"))
    assert r.status_code == 403, r.status_code
