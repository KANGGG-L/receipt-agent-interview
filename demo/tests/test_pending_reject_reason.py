# -*- coding: utf-8 -*-
"""Q2：`pending_memory.reject_reason` 必须真的被写入（此前是只建列、零写入方）。

背景（P2d-metrics 审核 M2，继承 K2/P3a）：K2 给 `pending_memory` 建了 `reject_reason` 列，
但**没有任何写入方**；与之相反，三处文档写着理由会落库（`memory_facts.validate_fact`
的调用方、`memory_consolidate.decide_action`、`db` 的列注释）。写侧闸门手上的理由当时只
`log.warning` 丢弃 —— 于是"上次为什么没进来"在库里永远查不到，审核者只能翻日志。

本用例走**端到端 HTTP 链路**（`POST /api/save_edited` → 抽取 → 落 `pending_memory` →
`GET /api/memory/pending`），断言的是硬判据：**接口返回的 `reject_reason` 非 NULL**。
三条"候选没进队列"的分支各一条用例（同一判据的三种取值都覆盖，避免"修一半"）：

1. 抽取侧闸门（`memory_extract._to_facts`）：模型给出白名单外的 kind —— **生产上真正会
   命中的路径**（候选对象在那里就被丢弃，写侧闸门看不到它）；
2. 写入侧闸门（`_extract_and_enqueue_memory_facts` 复核 `validate_fact`）：防御性分支，
   生产上被上游筛过因而通常不可达，用 stub 强制命中，并用 reason 前缀区分它确实来自写侧；
3. `decide_action` 判 NOOP（落选）：既有同值事实 —— **生产上会命中的路径**。

同时断言这些行**不占用人工闸**（`?status=pending` 里看不到）：被拒候选的载荷过不了
`validate_fact`，若以 pending 入队，审核者点"批准"时 approve 链会回落自由文本路径，
把非法候选写成一条自由文本记忆（人工闸给闸门开后门）。审计行的消费方是
`db._pending_row_to_dict` → `GET /api/memory/pending?status=rejected`（见 db 的列注释）。
AH3 起该端点**缺省只列 pending**，读审计行必须显式传 `status=rejected` —— 本文件原先
用"不传 status 拿全表"的写法读审计行，那条路径已按 AH3 撤销，故三处取审计行改为显式传参。

判别力（反证三个 md5 见交付报告，不写进用例）：
- 把 `_record_refused_candidate` 的 `reject_reason=` 改成 `None`（＝去掉理由的写入）
  → 三个用例的非 NULL 断言必须全部失败；
- 把三条 `_record_refused_candidate` 调用删掉（＝去掉接线）→ 连"审计行存在"的断言也失败。

环境隔离：模块级把 DB_PATH / RAG_DIR 指向临时目录（必须在首次 import app.* 之前 ——
`import app.main` 会在 import 期执行 `db._make_engine()` 的迁移块，不带 DB_PATH 会打到
live 库）。模型一律注入 stub（monkeypatch `memory_extract` 的构建点），全程不联网。
"""

import importlib
import json
import os
import sys
import tempfile
import uuid
from types import SimpleNamespace

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="pending_reject_reason_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_reject.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import pytest  # noqa: E402

import app.api_receipts as api_receipts  # noqa: E402
import app.db as db  # noqa: E402
import app.services.memory_extract as mx  # noqa: E402
from app.services.memory_facts import SOURCE_MANUAL, MemoryFact  # noqa: E402

TENANT = "q2-tenant"
VENDOR = "祥興食品"
STAFF = {"X-Role": "staff", "X-Tenant-Id": TENANT}
ADMIN = {"X-Role": "admin", "X-Tenant-Id": TENANT}

# AI 侧数量键是 `qty`、用户侧是 `quantity`；两侧只差品名一处（其余字段刻意等价）。
AI_ITEMS = [{"name": "黑牛", "qty": 2, "unit": "斤", "unit_price": 8.0, "amount": 16.0}]
USER_ITEMS = [{"name": "黑牛牌黑胡椒牛油", "quantity": 2.0, "unit": "斤",
               "unit_price": 8.0, "amount": 16.0}]

_GOOD_PAYLOAD = [{"kind": "alias", "fact_key": "黑牛", "value": "黑牛牌黑胡椒牛油",
                  "confidence": 0.7}]
# 白名单外的 kind：两处闸门（抽取侧 / 写入侧）都会拒，理由里都含 "kind 非法"。
_BAD_PAYLOAD = [{"kind": "sku", "fact_key": "黑牛", "value": "牛腩", "confidence": 0.7}]

_BASE_BODY = {
    "supplier_name": VENDOR,
    "date": "2026-08-01",
    "total_amount": 16.0,
    "settlement_type": "cash",
    "items": [dict(USER_ITEMS[0])],
    "version": 1,
    "source": "manual",
}


class _Msg:
    """最小 AIMessage 替身：真实通道正文在 `.content`。"""

    def __init__(self, content):
        self.content = content


class _StubModel:
    def __init__(self, output):
        self.output = output
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        return self.output


def _patch_model(monkeypatch, payload):
    """注入返回固定候选的 stub 模型（统计构建次数与调用次数）。"""
    model = _StubModel(_Msg(json.dumps(payload, ensure_ascii=False)))
    state = SimpleNamespace(build_calls=0)

    def _fake_build(cfg=None, **_kw):
        state.build_calls += 1
        return model

    monkeypatch.setattr(mx, "_build_default_model", _fake_build)
    return SimpleNamespace(model=model, state=state)


@pytest.fixture(autouse=True)
def _fresh_db():
    """每个用例一个干净的库（模块级 DB_PATH 是给 import 期用的兜底）。"""
    old = db.DB_PATH
    path = os.path.join(_TMP, f"q2_{uuid.uuid4().hex[:8]}.db")
    os.environ["DB_PATH"] = path
    importlib.reload(db)
    # api_receipts 持有的是同一个 db 模块对象，reload 后其 DB_PATH 即新库
    assert api_receipts.db.DB_PATH == path
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


def _enable_switch(value="true"):
    db.set_app_setting(api_receipts.MEMORY_EXTRACT_SETTINGS_KEY, value)


def _mk_receipt(with_anchor=True):
    sid = None
    if with_anchor:
        sid, _ = db.create_supplier(VENDOR, tenant_id=TENANT)
    rid = db.create_receipt(supplier_name=VENDOR, status="edited", tenant_id=TENANT)
    db.update_receipt(
        rid, receipt_date="2026-08-01", total_amount=16.0, supplier_id=sid,
        ai_prefill_json=json.dumps(
            {"vendor": VENDOR, "date": "2026-08-01", "total": 16.0, "items": AI_ITEMS},
            ensure_ascii=False))
    return rid, sid


def _save(client, rid):
    body = dict(_BASE_BODY)
    body["receipt_id"] = rid
    body["version"] = db.get_receipt_row(rid).version
    return client.post("/api/save_edited", headers=STAFF, json=body)


def _pending_via_api(client, status=""):
    """经 `GET /api/memory/pending` 取行（硬判据走接口，不直接读 db 模块）。

    status 必须显式给（'pending' / 'rejected'）：AH3 之后不传 status 的缺省视图
    只含人审行，取审计行要用 `status='rejected'`。
    """
    assert status, "AH3 起缺省视图只含 pending，取任何状态都必须显式传参"
    url = f"/api/memory/pending?status={status}"
    resp = client.get(url, headers=ADMIN)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _default_view_via_api(client):
    """不传 status 的缺省视图（角标/待审界面的取数形态）。"""
    resp = client.get("/api/memory/pending", headers=ADMIN)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _only_rejected(rows):
    rejected = [r for r in rows if r["status"] == "rejected"]
    assert len(rejected) == 1, rows
    return rejected[0]


# -------------------------------------------------------------
# 1. 抽取侧闸门（生产上真正会命中的路径）
# -------------------------------------------------------------
def test_extract_side_gate_rejection_is_persisted(client, monkeypatch):
    stats = _patch_model(monkeypatch, _BAD_PAYLOAD)
    _enable_switch()
    rid, _ = _mk_receipt()

    resp = _save(client, rid)
    assert resp.status_code == 200, resp.text
    assert stats.model.calls == 1, "有字段差异必须调一次模型"

    row = _only_rejected(_pending_via_api(client, "rejected"))
    assert row["reject_reason"], "抽取侧闸门的理由必须落库（非 NULL）"
    assert "kind 非法" in row["reject_reason"]
    assert row["reject_reason"].startswith("候选["), \
        "抽取侧理由带 候选[idx] 前缀（与写侧闸门的理由区分）"
    assert row["vendor"] == VENDOR
    assert row["source_kind"] == "extract"
    assert str(rid) in row["source_receipt_ids_json"], "触发单据必须可回溯"
    # 抽取侧只有理由字符串、拿不到归一后的 fact → 载荷列留 NULL（不编造载荷）
    assert row["fact_kind"] is None
    # 不占用人工闸：被拒候选绝不能出现在可批准队列里
    assert _pending_via_api(client, "pending") == []


# -------------------------------------------------------------
# 2. 写入侧闸门（防御性分支，stub 强制命中）
# -------------------------------------------------------------
def test_write_side_gate_rejection_is_persisted(client, monkeypatch):
    _enable_switch()
    rid, sid = _mk_receipt()
    # 绕过抽取侧闸门，直接把非法事实交给写入侧（上游已筛过，故这里平时不可达）
    bad = MemoryFact(kind="sku", fact_key="黑牛", value="牛腩",
                     supplier_id=str(sid), tenant_id=TENANT)
    monkeypatch.setattr(mx, "extract_candidate_facts", lambda *a, **k: [bad])
    monkeypatch.setattr(mx, "compute_field_diffs", lambda ai, user: [{"row": 0}])

    resp = _save(client, rid)
    assert resp.status_code == 200, resp.text

    row = _only_rejected(_pending_via_api(client, "rejected"))
    assert row["reject_reason"], "写入侧闸门的理由必须落库（非 NULL）"
    assert "kind 非法" in row["reject_reason"]
    assert not row["reject_reason"].startswith("候选["), \
        "该理由来自写入侧闸门本身（不经 _to_facts 的 候选[idx] 包装）"
    # 写侧手上还有 fact 载荷：非法候选长什么样要能逐字段看到
    assert row["fact_kind"] == "sku" and row["fact_key"] == "黑牛"
    assert row["supplier_id"] == sid, "载荷必须带主体锚点（非 NULL）"
    assert _pending_via_api(client, "pending") == []


# -------------------------------------------------------------
# 3. NOOP / 落选（生产上真正会命中的路径）
# -------------------------------------------------------------
def test_noop_candidate_reason_is_persisted(client, monkeypatch):
    stats = _patch_model(monkeypatch, _GOOD_PAYLOAD)
    _enable_switch()
    rid, sid = _mk_receipt()
    # 既有同 (tenant, supplier, key) 同值事实 → decide_action 判 NOOP（不入可批准队列）
    db.save_memory_fact(TENANT, VENDOR, "黑牛", fact=MemoryFact(
        kind="alias", fact_key="黑牛", value="黑牛牌黑胡椒牛油",
        supplier_id=str(sid), tenant_id=TENANT, source_kind=SOURCE_MANUAL))

    resp = _save(client, rid)
    assert resp.status_code == 200, resp.text
    assert stats.model.calls == 1, "既有事实不影响「是否调模型」（差异仍在）"

    row = _only_rejected(_pending_via_api(client, "rejected"))
    assert row["reject_reason"], "落选（NOOP）的理由同样必须落库（非 NULL）"
    assert "已存在同值事实" in row["reject_reason"], row["reject_reason"]
    # 落选行仍带载荷与可读的一行（复用 _pending_fact_content，不另拼格式）
    assert row["content"] == "alias：黑牛 → 黑牛牌黑胡椒牛油", row["content"]
    assert row["fact_key"] == "黑牛" and row["supplier_id"] == sid
    assert _pending_via_api(client, "pending") == [], \
        "NOOP 不得进人工闸（同值事实入队只会把闸门刷成噪声）"


# -------------------------------------------------------------
# 4. 正常候选不受影响：仍进人工闸且 reject_reason 为 NULL
# -------------------------------------------------------------
def test_accepted_candidate_has_no_reject_reason(client, monkeypatch):
    _patch_model(monkeypatch, _GOOD_PAYLOAD)
    _enable_switch()
    rid, sid = _mk_receipt()

    assert _save(client, rid).status_code == 200

    pending = _pending_via_api(client, "pending")
    assert len(pending) == 1, pending
    assert pending[0]["status"] == "pending"
    assert pending[0]["reject_reason"] is None, "正常待审行不得凭空带拒绝理由"
    assert pending[0]["fact_kind"] == "alias" and pending[0]["supplier_id"] == sid
    # 也没有多出审计行（入队路径与拒绝路径互斥）
    assert _pending_via_api(client, "rejected") == []
    # AH3：不传 status 的缺省视图（角标取数形态）必须与人审队列逐字一致 —— 审计行不得混入
    assert _default_view_via_api(client) == pending


# -------------------------------------------------------------
# 5. db 层：status 只接受 pending / rejected（approved 必须经人工流转）
# -------------------------------------------------------------
def test_enqueue_rejects_illegal_status(client):
    with pytest.raises(ValueError):
        db.enqueue_pending_memory(VENDOR, "x", tenant_id=TENANT, status="approved")
    pid, is_new = db.enqueue_pending_memory(
        VENDOR, "被拒候選", tenant_id=TENANT, reject_reason="闸门理由", status="rejected")
    assert is_new is True
    row = db.get_pending_memory(pid, tenant_id=TENANT)
    assert row["status"] == "rejected" and row["reject_reason"] == "闸门理由"
    # 幂等按 (vendor, tenant, content, status)：同一理由反复发生只增触发来源，不堆行
    pid2, again = db.enqueue_pending_memory(
        VENDOR, "被拒候選", tenant_id=TENANT, reject_reason="闸门理由",
        source_receipt_ids=[42], status="rejected")
    assert again is False and pid2 == pid
    assert "42" in db.get_pending_memory(pid, tenant_id=TENANT)["source_receipt_ids_json"]
    # 人工批准/拒绝仍走 review_pending_memory（被拒行不可再流转）
    assert db.review_pending_memory(pid, "approved", "someone", tenant_id=TENANT) is False
