# -*- coding: utf-8 -*-
"""D5 治理视图（收敛计划 P8 / 闭环 R1）：记忆只读列表 / 详情端点的回归测试。

why：这两个端点存在的意义就是「让治理者可看见」，因此用例必须钉住三件事 ——
① **看得见**：返回字段里真的包含 written_by（谁写的）/ fact_kind / trust_level /
   decay_score / valid_to 等治理元数据；缺任何一个字段，这条治理面就等于不存在
   （written_by 是 P4a 刚接的审计列，是「谁写的这条记忆」唯一的对外可见面）；
② **只看得见自己租户的**：跨租户不可见（H1「读侧不传租户」是本项目已现实发生过的
   越权形态，这里用另一个租户的 header 做反向验证）；
③ **取数口径只有一份**：端点结果必须与 `db.list_vendor_memory` 逐行相等，且端点函数体
   内不得自己拼取数语句（AST 结构断言）—— 否则就是本仓高发的①类缺陷「同一口径多份
   实现，只改一份」：两份实现的 tenant 过滤/名字相关度门只要漏一处，就是一条越权路径。

覆盖：字段完整性（含 written_by）/ vendor 过滤 / status 过滤 / supplier_id 锚点过滤 /
分页与越界参数 / 跨租户隔离 / 详情命中与 404 / 归档行可见（治理口径）/ 逐行等价
（单一实现）/ AST 结构断言（含扫描器自校准）/ 失败不抛 500 / 只读性（临时库 md5 与
live 库、live 向量库指纹前后一致）/ 权限（匿名 401、staff|owner 403、admin 200）。

隔离：本模块 DB_PATH / RAG_DIR 全部指向临时目录（在首次 import app.* 之前设置），
零网络、零 live 库写入、零 live 向量库写入。
"""

import ast
import hashlib
import importlib
import inspect
import os
import sqlite3
import sys
import tempfile
import textwrap
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="vendor_memory_governance_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_vendor_memory_governance.db")
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

_LIVE_DB = os.path.join(_DEMO_DIR, "receipt_demo.db")
_LIVE_RAG_DIRS = [os.path.join(_DEMO_DIR, ".rag_chroma"),
                  os.path.join(_REPO_ROOT, ".rag_chroma")]

# 治理详情必须能看到的字段（缺一即判失败 —— 「至少含」是端点契约）
_REQUIRED_FIELDS = (
    "memory_id", "vendor", "supplier_id", "fact_kind", "fact_key", "fact_value",
    "notes", "sample", "status", "trust_level", "decay_score", "hit_count",
    "last_hit_at", "valid_to", "source_kind", "written_by", "created_at",
)

_LIST_URL = "/api/admin/vendor-memory"


# -------------------------------------------------------------
# 环境与夹具
# -------------------------------------------------------------
def _fresh_db():
    """每个用例独立数据库文件，避免跨用例串扰。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, "gov_%s.db" % uuid.uuid4().hex[:8])
    importlib.reload(db)
    return db


def _client():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    return TestClient(fastapi_app)


def _write_memory(_db, vendor, fact_key, value, tenant="default", supplier_id=None,
                  kind="alias", trust=0.6, written_by="owner@demo.hk",
                  notes=None, sample=None, status=None, valid_to=""):
    """写一条带完整治理元数据的记忆（走生产写入路径 db.upsert_vendor_memory）。

    why 走 db 的写入函数而不是手抄 INSERT：字段映射只有 db 一份，手抄 INSERT 会让
    「列名/迁移不匹配」这类缺陷逃过用例（与 memory_metrics 用例同一条纪律）。
    notes/sample 缺省按 fact_key/value 派生：`upsert_vendor_memory` 对
    (vendor, tenant, notes, sample) 完全相同的 active 行做幂等去重，同一供应商写多行时
    若不区分内容，第二次写入会复用第一行的 memory_id，用例就只有一个行可断言。
    """
    mid, _is_new = _db.upsert_vendor_memory(
        vendor, notes if notes is not None else "版式：%s" % fact_key,
        sample if sample is not None else "- %s = 1" % value,
        tenant_id=tenant, source_kind="extract", source_ref="[1494]",
        supplier_id=supplier_id,
        # fact_key 是治理列（身份），只能走形参；塞进 fact_row 会被
        # _apply_payload_columns 按治理列跳过（本用例第一版就踩了这个坑）
        fact_key=fact_key,
        # valid_to 是治理列：只能走形参，塞进 fact_row 会被 _apply_payload_columns 跳过
        valid_to=valid_to,
        fact_row={"fact_kind": kind, "fact_value": value, "trust_level": trust,
                  "written_by": written_by})
    if status and status != "active":
        # 直接改状态：归档的生产路径是「扣分到阈值以下」（需连点 5 次踩），在夹具里
        # 复现它只会引入第二份归档实现；这里只造数据形态，不复刻机制。
        conn = sqlite3.connect(_db.DB_PATH)
        try:
            conn.execute("UPDATE vendor_memory SET status=? WHERE memory_id=?",
                         (status, mid))
            conn.commit()
        finally:
            conn.close()
    return mid


def _get_list(client, role="admin", tenant=None, **params):
    headers = {"X-Role": role}
    if tenant:
        headers["X-Tenant-Id"] = tenant
    return client.get(_LIST_URL, params=params, headers=headers)


def _get_detail(client, memory_id, role="admin", tenant=None):
    headers = {"X-Role": role}
    if tenant:
        headers["X-Tenant-Id"] = tenant
    return client.get("%s/%s" % (_LIST_URL, memory_id), headers=headers)


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


# -------------------------------------------------------------
# 「单一实现」结构断言（含自校准）
# -------------------------------------------------------------
# 端点函数体里出现这些调用，等于自己拼了一份取数逻辑
_QUERY_CALL_ATTRS = {"query", "text", "execute", "executemany", "from_statement",
                     "raw", "get_session"}


def _single_impl_violations(fn):
    """返回端点函数相对「取数口径单一实现」纪律的违规点（空列表 = 合规）。

    why 用 AST 而不是 grep 源码字符串：文档字符串里会提到「不写第二份取数语句」，
    字符串扫描会把注释/文案一起算进去，既会误报也容易被绕开；按调用名与 ORM 行模型
    的出现做结构判定，判据与实现方式绑定。
    """
    src = textwrap.dedent(inspect.getsource(fn))
    tree = ast.parse(src)
    called_attrs = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called_attrs.add(node.func.attr)
    reasons = []
    if "list_vendor_memory" not in called_attrs:
        reasons.append("未调用 db.list_vendor_memory（取数口径没有复用同一实现）")
    bad = sorted(called_attrs & _QUERY_CALL_ATTRS)
    if bad:
        reasons.append("出现自建取数/会话调用: %s" % bad)
    if "_VendorMemoryRow" in src:
        reasons.append("直接引用 ORM 行模型（等于自己拼取数语句）")
    return reasons


def _mutant_list_endpoint():
    """自校准用：模拟「端点自己拼取数语句」的违规形态（永不被调用）。"""
    s = db.get_session()
    return s.query(db._VendorMemoryRow).order_by("rowid DESC").all()


def test_single_impl_scanner_self_calibrated():
    """先证明扫描器能抓到违规形态，再拿它扫真实端点 —— 否则断言可能恒真。"""
    mutant_reasons = _single_impl_violations(_mutant_list_endpoint)
    assert mutant_reasons, "扫描器未能识别自建取数语句的违规形态（断言恒真，无守护力）"

    from app import api_admin
    assert _single_impl_violations(api_admin.list_vendor_memory_governance) == []
    assert _single_impl_violations(api_admin.get_vendor_memory_governance) == []


# -------------------------------------------------------------
# 1. 字段完整性：治理元数据真的看得见（含 written_by）
# -------------------------------------------------------------
def test_list_exposes_full_governance_fields():
    _db = _fresh_db()
    _write_memory(_db, "德利行", "alias:有機菜心", "有机菜心", trust=0.6,
                  written_by="owner@demo.hk")
    client = _client()

    ret = _get_list(client)
    assert ret.status_code == 200, ret.text
    body = ret.json()
    assert body["status"] == "success", body
    assert body["total"] == 1, body
    row = body["data"][0]
    for key in _REQUIRED_FIELDS:
        assert key in row, "治理视图缺少字段: %s（现有字段 %s）" % (key, sorted(row))
    assert row["vendor"] == "德利行"
    assert row["fact_kind"] == "alias"
    assert row["fact_value"] == "有机菜心"
    assert row["fact_key"] == "alias:有機菜心"
    assert row["trust_level"] == 0.6
    assert row["status"] == "active"
    assert row["written_by"] == "owner@demo.hk", "「谁写的这条记忆」必须可见"
    assert row["source_kind"] == "extract"
    assert isinstance(row["hit_count"], int)
    assert isinstance(row["decay_score"], float)


def test_detail_exposes_full_governance_fields():
    _db = _fresh_db()
    mid = _write_memory(_db, "鴻運餐飲", "layout:ncr", "ncr_handwritten", kind="layout",
                        trust=0.9, written_by="staff@demo.hk")
    client = _client()

    ret = _get_detail(client, mid)
    assert ret.status_code == 200, ret.text
    body = ret.json()
    assert body["status"] == "success", body
    row = body["data"]
    assert row["memory_id"] == mid
    for key in _REQUIRED_FIELDS:
        assert key in row, "治理详情缺少字段: %s" % key
    assert row["written_by"] == "staff@demo.hk"
    assert row["fact_kind"] == "layout"


# -------------------------------------------------------------
# 2. vendor / status / supplier_id 过滤
# -------------------------------------------------------------
def test_filter_by_vendor():
    _db = _fresh_db()
    _write_memory(_db, "德利行", "alias:a", "甲")
    _write_memory(_db, "鴻運餐飲", "alias:b", "乙")
    client = _client()

    body = _get_list(client, vendor="德利行").json()
    assert body["total"] == 1, body
    assert [r["vendor"] for r in body["data"]] == ["德利行"]

    body = _get_list(client, vendor="鴻運餐飲").json()
    assert [r["vendor"] for r in body["data"]] == ["鴻運餐飲"]

    # 不存在的供应商：空列表（不得退回全量 —— 那是本仓已出现 3 次的越权形态）
    body = _get_list(client, vendor="不存在商號").json()
    assert body["status"] == "success", body
    assert body["data"] == []
    assert body["total"] == 0


def test_filter_by_status():
    _db = _fresh_db()
    _write_memory(_db, "德利行", "alias:a", "甲")
    _write_memory(_db, "德利行", "alias:b", "乙", status="archived")
    _write_memory(_db, "德利行", "alias:c", "丙", status="superseded")
    client = _client()

    # 缺省 = 治理口径全状态可见（归档/被取代的行必须能被人看到）
    body = _get_list(client).json()
    assert body["total"] == 3, body

    body = _get_list(client, status="active").json()
    assert body["total"] == 1 and body["data"][0]["fact_value"] == "甲", body

    body = _get_list(client, status="archived").json()
    assert body["total"] == 1 and body["data"][0]["status"] == "archived", body

    body = _get_list(client, status="superseded").json()
    assert body["total"] == 1 and body["data"][0]["status"] == "superseded", body

    body = _get_list(client, status="没这个状态").json()
    assert body["total"] == 0 and body["data"] == [], body


def test_filter_by_supplier_anchor():
    _db = _fresh_db()
    sid_a, err = _db.create_supplier("德利行")
    assert sid_a, err
    sid_b, err = _db.create_supplier("鴻運餐飲")
    assert sid_b, err
    mid_a = _write_memory(_db, "德利行", "alias:a", "甲", supplier_id=sid_a)
    _write_memory(_db, "鴻運餐飲", "alias:b", "乙", supplier_id=sid_b)
    client = _client()

    # 真实调用形态：锚点名 + 供应商名一起给（前端选中供应商时两者都有）
    body = _get_list(client, vendor="德利行", supplier_id=sid_a).json()
    assert body["status"] == "success", body
    assert [r["memory_id"] for r in body["data"]] == [mid_a], body
    # 锚点保证「同一 id 才是同一供应商」，另一家的记忆不得混入
    assert all(r["supplier_id"] == sid_a for r in body["data"])


# -------------------------------------------------------------
# 3. 分页与越界参数
# -------------------------------------------------------------
def test_pagination_and_bounds():
    _db = _fresh_db()
    for i in range(5):
        _write_memory(_db, "德利行", "alias:%d" % i, "值%d" % i)
    client = _client()

    body = _get_list(client, limit=2, offset=0).json()
    assert body["total"] == 5 and len(body["data"]) == 2, body
    assert body["limit"] == 2 and body["offset"] == 0

    first_page = [r["memory_id"] for r in body["data"]]
    body2 = _get_list(client, limit=2, offset=2).json()
    second_page = [r["memory_id"] for r in body2["data"]]
    assert set(first_page).isdisjoint(second_page), (first_page, second_page)

    body3 = _get_list(client, limit=2, offset=4).json()
    assert len(body3["data"]) == 1, body3

    # 越界参数：400 + status=error + code，绝不 500
    for params in ({"limit": 0}, {"limit": -1}, {"limit": 9999}, {"offset": -1}):
        ret = _get_list(client, **params)
        assert ret.status_code == 400, (params, ret.status_code, ret.text)
        err = ret.json()
        assert err["status"] == "error" and err["code"] == "INVALID_PAGINATION", err


# -------------------------------------------------------------
# 4. 跨租户隔离（H1 同族缺陷的反向验证）
# -------------------------------------------------------------
def test_cross_tenant_not_visible():
    _db = _fresh_db()
    mid_a = _write_memory(_db, "德利行", "alias:a", "甲", tenant="tenantA")
    client = _client()

    body = _get_list(client, tenant="tenantA").json()
    assert [r["memory_id"] for r in body["data"]] == [mid_a], body

    # 另一个租户：列表为空、详情 404（文案与「不存在」相同，不给探测面）
    body = _get_list(client, tenant="tenantB").json()
    assert body["status"] == "success" and body["data"] == [], body
    assert body["total"] == 0, body

    ret = _get_detail(client, mid_a, tenant="tenantB")
    assert ret.status_code == 404, ret.text
    assert ret.json() == {"status": "error", "code": "NOT_FOUND",
                          "msg": "未找到该记忆"}, ret.json()


# -------------------------------------------------------------
# 5. 详情：命中 / 不存在 / 归档行可见
# -------------------------------------------------------------
def test_detail_unknown_memory_id_404():
    _fresh_db()
    client = _client()
    ret = _get_detail(client, "mid-not-exist")
    assert ret.status_code == 404, ret.text
    assert ret.json()["code"] == "NOT_FOUND", ret.json()


def test_detail_shows_archived_row():
    """治理口径：归档行同样可查 —— 「这条为什么不再生效」必须有人能回答。"""
    _db = _fresh_db()
    mid = _write_memory(_db, "德利行", "alias:a", "甲", status="archived",
                        valid_to="2026-01-01T00:00:00")
    client = _client()

    ret = _get_detail(client, mid)
    assert ret.status_code == 200, ret.text
    row = ret.json()["data"]
    assert row["status"] == "archived", row
    assert row["valid_to"] == "2026-01-01T00:00:00", row


# -------------------------------------------------------------
# 6. 单一实现：端点结果与 db.list_vendor_memory 逐行相等
# -------------------------------------------------------------
def test_endpoint_rows_equal_db_single_implementation():
    _db = _fresh_db()
    _write_memory(_db, "德利行", "alias:a", "甲")
    _write_memory(_db, "鴻運餐飲", "alias:b", "乙", status="archived")
    client = _client()

    expected = _db.list_vendor_memory(None, tenant_id="default", include_archived=True)
    body = _get_list(client).json()
    assert body["data"] == expected, (body["data"], expected)

    expected_active = [r for r in expected if r["status"] == "active"]
    body_active = _get_list(client, status="active").json()
    assert body_active["data"] == expected_active

    mid = expected[0]["memory_id"]
    assert _get_detail(client, mid).json()["data"] == expected[0]


# -------------------------------------------------------------
# 7. 失败语义：取数故障返回 500 + 统一错误信封（人话，不带内部异常原文）
# -------------------------------------------------------------
def test_list_failure_returns_error_envelope(monkeypatch):
    _fresh_db()
    client = _client()

    def _boom(*a, **kw):
        raise RuntimeError("模拟取数故障")

    monkeypatch.setattr(db, "list_vendor_memory", _boom)
    ret = _get_list(client)
    assert ret.status_code == 500, ret.text
    err = ret.json()
    assert err["status"] == "error" and err["code"] == "MEMORY_LIST_FAILED", err
    assert "模拟取数故障" not in ret.text, "不得把内部异常原文给用户"
    assert err["data"] is None, err

    ret = _get_detail(client, "mid-x")
    assert ret.status_code == 500, ret.text
    err = ret.json()
    assert err["status"] == "error" and err["code"] == "MEMORY_READ_FAILED", err


# -------------------------------------------------------------
# 8. 权限：仅 admin
# -------------------------------------------------------------
def test_admin_only():
    _db = _fresh_db()
    mid = _write_memory(_db, "德利行", "alias:a", "甲")
    client = _client()

    assert client.get(_LIST_URL).status_code == 401
    assert client.get("%s/%s" % (_LIST_URL, mid)).status_code == 401
    for role in ("staff", "owner"):
        assert _get_list(client, role=role).status_code == 403, role
        assert _get_detail(client, mid, role=role).status_code == 403, role
    assert _get_list(client).status_code == 200
    assert _get_detail(client, mid).status_code == 200


# -------------------------------------------------------------
# 9. 只读：临时库 md5 与 live 库 / live 向量库指纹前后一致
# -------------------------------------------------------------
def test_readonly_no_write_anywhere():
    _db = _fresh_db()
    mid = _write_memory(_db, "德利行", "alias:a", "甲")
    client = _client()

    tmp_before = _md5(_db.DB_PATH)
    live_before = _md5(_LIVE_DB)
    rag_before = _rag_fingerprint()

    assert _get_list(client).status_code == 200
    assert _get_detail(client, mid).status_code == 200

    assert _md5(_db.DB_PATH) == tmp_before, "只读端点改写了临时库"
    assert _md5(_LIVE_DB) == live_before, "只读端点改写了 live 库"
    assert _rag_fingerprint() == rag_before, "只读端点改写了 live 向量库"
