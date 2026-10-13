# -*- coding: utf-8 -*-
"""AH3：待确认列表缺省只列 `status='pending'`，Q2 的 rejected 审计行不得混进来。

背景（P3x2 审核 mustFix 3 当场复核）：`db.list_pending_memory` 原写作 `if status:` ——
不传 status 时**完全不过滤**，于是 `pending_memory` 表里那族 `status='rejected'` 的
落选审计行（候选没过 `validate_fact` / 与既有同值事实被判 NOOP，按设计"不进队列"）
会混进「待确认记忆」列表；前端角标与待审界面据此计数就会把"被闸门拒绝的行"显示成
"等人审的行"（角标虚高、审核者看到永远批不掉的条目）。这与本仓自己的 docstring
（`enqueue_pending_memory` 写明"人工闸列表与角标只列 pending"）相反。

本文件把 AH3 的契约钉成四条判据：

1. 缺省视图（不传 status）**逐 id 等于** pending 行集合 —— 不是"少了一些"而是"正好是"；
2. 显式 `status='rejected'` 仍能读到审计行（**不得**为了隔离而砍掉按状态查询能力）；
3. `status=None` / `status=""` 与"不传参数"行为一致（防"签名默认值"与"显式 None"
   分成两条行为，也覆盖前端拼出 `?status=` 空串的形态）；
4. 角标口径：缺省视图条数 == pending 行数，且 rejected 行不计入（同时给出全量基数，
   证明这不是"表里本来就只有 2 行"的恒真断言）。

判别力（反证三个 md5 见交付报告，不写进用例）：把 `list_pending_memory` 的
`q.filter(_PendingMemoryRow.status == target_status)` 改回旧的 `if status:`（＝不传即不过滤）
→ 判据 1、3 的 **db 层**断言必须失败。注意端点层此时仍会通过（它在 AH3 里显式传了
'pending'）—— 这正是判据 1、3 要在"端点 + db"两层各断言一次的原因：只钉端点会漏掉
db 契约面这一半，而这一半正是审核说的"契约面不安全"。

环境隔离：模块级把 DB_PATH / RAG_DIR 指向临时目录（必须在首次 import app.* 之前 ——
`import app.main` 会在 import 期执行 `db._make_engine()` 的迁移块，不带 DB_PATH 会打到
live 库）；造行一律走生产写入函数 `db.enqueue_pending_memory`，取数一律走 HTTP 端点，
不直接写 SQL。全程离线。
"""

import importlib
import os
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="pending_queue_status_isolation_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_pending_queue.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import pytest  # noqa: E402

import app.db as db  # noqa: E402

TENANT = "ah3-tenant"
VENDOR = "九龍豆品廠"
ADMIN = {"X-Role": "admin", "X-Tenant-Id": TENANT}

# 前端角标的取数面（判据 4 的静态锚点）
_MAIN_JS = os.path.join(_DEMO_DIR, "static", "js", "main.js")
_BADGE_URL = "/api/memory/pending?status=pending"


@pytest.fixture(autouse=True)
def _fresh_db():
    """每个用例一个干净的库（模块级 DB_PATH 是给 import 期用的兜底）。"""
    old = db.DB_PATH
    path = os.path.join(_TMP, f"ah3_{uuid.uuid4().hex[:8]}.db")
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


def _seed():
    """造 2 条待审行 + 2 条落选审计行（同一租户），返回 (pending_ids, rejected_ids)。

    刻意两种状态**数量相等**：这样"角标条数 == pending 行数"这条断言在实现漏过滤时
    会因为拿到 4 条而不是 2 条而失败（数量相等使断言有判别力）。
    """
    pending_ids, rejected_ids = set(), set()
    for i, content in enumerate(("alias：本吧 → 本地牛腩", "unit：箱 → 24 包")):
        pid, is_new = db.enqueue_pending_memory(
            VENDOR, content, tenant_id=TENANT, source_kind="extract")
        assert is_new, (i, pid)
        pending_ids.add(pid)
    for content, reason in (("被拒候选 A", "候选[0] kind 非法"),
                            ("被拒候选 B", "已存在同值事实（同 key 同值）")):
        pid, is_new = db.enqueue_pending_memory(
            VENDOR, content, tenant_id=TENANT, status="rejected",
            reject_reason=reason)
        assert is_new, (content, pid)
        rejected_ids.add(pid)
    assert not (pending_ids & rejected_ids), "两类行必须是不同的行"
    return pending_ids, rejected_ids


def _view(client, status=None):
    """经 `GET /api/memory/pending` 取数：status=None 表示**不传该参数**（缺省视图）。"""
    url = "/api/memory/pending" if status is None \
        else f"/api/memory/pending?status={status}"
    resp = client.get(url, headers=ADMIN)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


# -------------------------------------------------------------
# 1. 缺省视图只含待审行（AH3 的核心判据）
# -------------------------------------------------------------
def test_default_view_lists_exactly_pending_rows(client):
    """不传 status → 逐 id 等于 pending 行集合；rejected 审计行一条都不许出现。

    两个层次各断言一次：HTTP 端点的缺省视图（角标取数面）与 db 函数的缺省调用。
    why 两层都要：只断言端点时，即便 db 层的缺省口径被改回"不过滤"，端点里显式
    传的 'pending' 仍会把结果挡住 —— 断言会漏掉 db 契约面这一半（本仓高发的
    "修一半"）。db 层这一条才是把缺省口径钉死的判别式断言。
    """
    pending_ids, rejected_ids = _seed()

    rows = _view(client)
    assert {r["id"] for r in rows} == pending_ids, \
        f"端点缺省视图必须是待审队列本身，实得 {[(r['id'], r['status']) for r in rows]}"
    assert rejected_ids, "前置条件：库里确实有 rejected 审计行（否则本断言恒真）"
    assert {r["id"] for r in rows} & rejected_ids == set()
    assert all(r["status"] == "pending" for r in rows)

    db_rows = db.list_pending_memory(tenant_id=TENANT)
    assert {r["id"] for r in db_rows} == pending_ids, \
        f"db 层缺省调用同样只列 pending，实得 {[(r['id'], r['status']) for r in db_rows]}"
    assert all(r["status"] == "pending" for r in db_rows)


# -------------------------------------------------------------
# 2. 按状态查询能力不得被砍掉（隔离 ≠ 删除功能）
# -------------------------------------------------------------
def test_explicit_rejected_status_still_reads_audit_rows(client):
    """显式 `?status=rejected` 仍能读到落选审计行及其理由（Q2 的能力不受影响）。"""
    _seed()
    rows = _view(client, "rejected")
    assert rows, "审计行必须仍可显式读取"
    assert all(r["status"] == "rejected" for r in rows)
    assert all(r["reject_reason"] for r in rows), \
        "审计行的理由必须仍可读（这正是 Q2 建这一族行的目的）"


def test_other_status_filter_still_works(client):
    """`?status=approved` 等其它状态过滤同样保留（初始无已批准行 → 空列表而非全量）。"""
    _seed()
    assert _view(client, "approved") == []


# -------------------------------------------------------------
# 3. status=None / "" 与"不传参数"行为一致（签名默认值陷阱）
# -------------------------------------------------------------
def test_none_and_empty_status_match_omitted(client):
    """避免"缺省值改了、显式传 None 却走另一条分支"这类半修：三者必须逐 id 相同。

    只断言"三者彼此相等"是不够的（旧实现下它们会一起退化成"全量"而依然彼此相等），
    故同时断言三者都等于 pending 行集合 —— 这才是判"缺省口径确实生效"的硬判据。
    """
    pending_ids, rejected_ids = _seed()

    omitted = [r["id"] for r in _view(client)]
    empty_param = [r["id"] for r in _view(client, "")]
    db_none = [r["id"] for r in db.list_pending_memory(status=None, tenant_id=TENANT)]
    db_omitted = [r["id"] for r in db.list_pending_memory(tenant_id=TENANT)]
    db_empty = [r["id"] for r in db.list_pending_memory(status="", tenant_id=TENANT)]

    assert set(omitted) == pending_ids
    assert set(empty_param) == set(omitted), "空串查询参数必须与不传等价"
    assert db_none == db_omitted == db_empty, \
        f"显式 None/空串/不传必须同口径：{db_none} / {db_omitted} / {db_empty}"
    for label, ids in (("status=None", db_none), ("不传", db_omitted), ("status=''", db_empty)):
        assert set(ids) == pending_ids, \
            f"{label} 必须落在待审队列上（实得 {ids}，rejected={sorted(rejected_ids)}）"


# -------------------------------------------------------------
# 4. 角标计数口径：角标数 == pending 行数，rejected 不计入
# -------------------------------------------------------------
def test_badge_count_equals_pending_rows_only(client):
    """前端角标取数的两条硬判据：条数 == pending 行数；rejected 贡献 0。"""
    pending_ids, rejected_ids = _seed()

    rows = _view(client)                       # 角标取数形态（不传 status）
    assert len(rows) == len(pending_ids) == 2, \
        f"角标条数必须等于待审行数 2，实得 {len(rows)}"
    # 非恒真的基数证据：全量（两种状态之和）会是 4 —— 角标若漏过滤就会显示 4
    total = len(_view(client, "pending")) + len(_view(client, "rejected"))
    assert total == len(pending_ids) + len(rejected_ids) == 4
    assert len(rows) != total, "角标条数与全量条数必须不同（否则本断言没有判别力）"

    # 角标真正的请求 URL 必须显式带 status=pending（与缺省视图同口径，双保险）
    with open(_MAIN_JS, encoding="utf-8") as f:
        main_js = f.read()
    # F4 S4-11 起角标刷新随域迁至 admin_observation.js；判据＝联合全文含显式 status=pending。
    with open(os.path.join(_DEMO_DIR, "static", "js", "admin_observation.js"), "r", encoding="utf-8") as f:
        main_js += f.read()
    assert _BADGE_URL in main_js, \
        "角标取数必须显式带 ?status=pending（缺省视图已隔离，此处防止改成无参取数）"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
