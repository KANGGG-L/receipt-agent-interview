# -*- coding: utf-8 -*-
"""P3-T4（B6）运行记录落库测试：抽取的中间产物必须**落盘可查**，不再只活在内存里。

为什么单独建这个文件：`memory_extract.get_last_run()` / `get_last_error()` 是
`threading.local` 的内存态 —— 进程重启即丢，事后无法复盘"某次抽取为什么产出 0 条事实 /
是不是一直走回退文案 / 失败在哪一步"。本文件钉住三件事：
1. 新表 `memory_extract_runs` 幂等建出，字段齐全；
2. **两处触发点（save_edited / approve）各写一条**运行记录（本仓高发「修 A 漏 B」：
   两条入口是独立的，只接一条在功能上等于没接）；
3. 成功 / 抽取降级 / 无差异 / 无锚点 四种终态都留痕，且写记录失败**不得**影响主流程。

判别力（反证）：去掉任一触发点的 `trigger=` 声明 → 对应用例的入口断言失败；去掉唯一那处
`_persist_extract_run` 调用 → 两条触发用例同时失败。两者互补：
前者证明"每条入口各自被钉住"，后者证明"记录确实来自这处写入"。

环境隔离：模块级把 DB_PATH / RAG_DIR 指向临时目录（必须在 import app.* 之前 ——
`import app.main` 会在 import 期执行 db._make_engine() 的迁移块，直接对目标库做 ALTER）。
模型一律注入 stub（monkeypatch `memory_extract._build_default_model`），全程不联网。
"""

import json
import logging
import os
import sys
import tempfile
from types import SimpleNamespace

# 隔离环境：临时 DB / 向量目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="memory_extract_runs_")
os.environ["DB_PATH"] = os.path.join(_TMP, "runs.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")

_DEMO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo")
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import pytest  # noqa: E402

import app.api_receipts as api_receipts  # noqa: E402
import app.services.memory_extract as mx  # noqa: E402
from app import db  # noqa: E402

TENANT = "p8b-tenant"
OTHER_TENANT = "p8b-other"
VENDOR = "祥興食品"
STAFF = {"X-Role": "staff", "X-Tenant-Id": TENANT}
OWNER = {"X-Role": "owner", "X-Tenant-Id": TENANT}

AI_ITEMS = [{"name": "黑牛", "qty": 2, "unit": "斤", "unit_price": 8.0, "amount": 16.0}]
USER_ITEMS = [{"name": "黑牛牌黑胡椒牛油", "quantity": 2.0, "unit": "斤",
               "unit_price": 8.0, "amount": 16.0}]

CANDIDATE_PAYLOAD = [{"kind": "alias", "fact_key": "黑牛", "value": "黑牛牌黑胡椒牛油",
                      "confidence": 0.7}]

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
    """记录 invoke 次数的 stub 模型（不联网）。"""

    def __init__(self, output=None, exc=None):
        self.output = output
        self.exc = exc
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.output


def _patch_model(monkeypatch, output=None, exc=None):
    model = _StubModel(output=output if output is not None else _Msg(
        json.dumps(CANDIDATE_PAYLOAD, ensure_ascii=False)), exc=exc)
    state = SimpleNamespace(build_calls=0)

    def _fake_build(cfg=None, **_kw):
        state.build_calls += 1
        return model

    monkeypatch.setattr(mx, "_build_default_model", _fake_build)
    return SimpleNamespace(model=model, state=state)


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch, tmp_path):
    """每个用例一个干净的库（模块级 DB_PATH 是共享语义，必须按用例切到 tmp_path）。"""
    old = db.DB_PATH
    path = str(tmp_path / "p8b.db")
    monkeypatch.setenv("DB_PATH", path)
    db.DB_PATH = path
    db._make_engine()
    try:
        yield
    finally:
        db.DB_PATH = old
        db._make_engine()


@pytest.fixture
def client():
    """带 lifespan 的客户端（startup 自愈在非默认库下会整体跳过，不写共享语料）。"""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


def _enable_switch(value="true"):
    db.set_app_setting(api_receipts.MEMORY_EXTRACT_SETTINGS_KEY, value)


def _mk_receipt(status="edited", ai_items=None, final_items=None, with_anchor=True):
    sid = None
    if with_anchor:
        sid, _ = db.create_supplier(VENDOR, tenant_id=TENANT)
    rid = db.create_receipt(supplier_name=VENDOR, status=status, tenant_id=TENANT)
    db.update_receipt(
        rid, receipt_date="2026-08-01", total_amount=16.0, supplier_id=sid,
        ai_prefill_json=json.dumps(
            {"vendor": VENDOR, "date": "2026-08-01", "total": 16.0,
             "items": ai_items if ai_items is not None else AI_ITEMS},
            ensure_ascii=False))
    if final_items is not None:
        db.set_receipt_items(rid, final_items, tenant_id=TENANT)
    return rid, sid


def _save(client, rid, items=None, source="manual"):
    body = dict(_BASE_BODY)
    body["receipt_id"] = rid
    body["items"] = items if items is not None else [dict(USER_ITEMS[0])]
    body["source"] = source
    body["version"] = db.get_receipt_row(rid).version
    return client.post("/api/save_edited", headers=STAFF, json=body)


def _runs(tenant_id=TENANT, **kw):
    return db.list_memory_extract_runs(tenant_id=tenant_id, **kw)


# -------------------------------------------------------------
# 0. 表与字段（幂等建表）
# -------------------------------------------------------------
def test_table_schema_created_with_required_columns():
    """新表必须建出且含全部必需字段（缺一列＝该维度不可复盘）。"""
    import sqlite3
    con = sqlite3.connect(db.DB_PATH)
    try:
        names = [r[1] for r in con.execute("PRAGMA table_info(memory_extract_runs)")]
    finally:
        con.close()
    assert names, "memory_extract_runs 未建出"
    for col in ("run_id", "tenant_id", "receipt_id", "vendor", "supplier_id",
                "trigger", "diffs_count", "facts_produced", "facts_rejected",
                "reject_reasons_json", "prompt_source", "error", "created_at"):
        assert col in names, "缺列 %s（实得 %s）" % (col, names)


# -------------------------------------------------------------
# 1. 触发点一：save_edited 写运行记录
# -------------------------------------------------------------
def test_save_edited_writes_run_record(client, monkeypatch):
    """判别式（触发点一）：去掉 save_edited 调用点的 `trigger="save_edited"` →
    本用例的 trigger 断言失败；去掉唯一那处 `_persist_extract_run` → 本用例的
    "恰有一行"断言失败。"""
    stats = _patch_model(monkeypatch)
    _enable_switch()
    rid, sid = _mk_receipt()

    resp = _save(client, rid)
    assert resp.status_code == 200, resp.text

    runs = _runs()
    assert len(runs) == 1, runs
    r = runs[0]
    assert r["trigger"] == "save_edited", r
    assert r["receipt_id"] == rid
    assert r["vendor"] == VENDOR
    assert r["supplier_id"] == sid
    assert r["diffs_count"] >= 1, r
    assert r["facts_produced"] == len(CANDIDATE_PAYLOAD), r
    assert r["facts_rejected"] == 0, r
    # reject_reasons_json 必须"可读"（合法 JSON 数组，本行无丢弃故为空数组而非 NULL）
    assert json.loads(r["reject_reasons_json"]) == [], r
    assert r["error"] == "", r
    # prompt_source 必须反映本次实际用到的文案（回退率是治理信号，不能为空）
    assert r["prompt_source"] in (mx.PROMPT_SOURCE_REGISTRY,
                                  mx.PROMPT_SOURCE_BUILTIN_FALLBACK), r
    assert stats.model.calls == 1


# -------------------------------------------------------------
# 2. 触发点二：approve 写运行记录
# -------------------------------------------------------------
def test_approve_writes_run_record(client, monkeypatch):
    """判别式（触发点二）：去掉 approve 调用点的 `trigger="approve"` → 本用例失败。

    与触发点一的用例各自断言自己那条入口，不是同一条断言覆盖两处（否则"只接一条"能蒙混）。
    """
    _patch_model(monkeypatch)
    _enable_switch()
    rid, _ = _mk_receipt(with_anchor=False, final_items=[dict(USER_ITEMS[0])])

    resp = client.post("/api/receipt/%d/approve" % rid, headers=OWNER,
                       json={"version": db.get_receipt_row(rid).version})
    assert resp.status_code == 200, resp.text

    runs = _runs()
    assert len(runs) == 1, runs
    r = runs[0]
    assert r["trigger"] == "approve", r
    assert r["receipt_id"] == rid
    assert r["facts_produced"] == len(CANDIDATE_PAYLOAD), r
    assert r["error"] == "", r
    # approve 已自动建档，锚点必须解析出来（NULL 会让"这条候选归属谁"不可复盘）
    assert r["supplier_id"] is not None, r


# -------------------------------------------------------------
# 3. 失败与降级也要留痕（否则"失败"与"没什么可记的"在库里长得一样）
# -------------------------------------------------------------
def test_extract_exception_writes_run_record_with_error(client, monkeypatch):
    """抽取抛异常 → 主流程仍 200，且运行记录 error 非空、reject_reasons_json 非空。"""
    _patch_model(monkeypatch, exc=RuntimeError("引擎 500"))
    _enable_switch()
    rid, _ = _mk_receipt()

    resp = _save(client, rid)
    assert resp.status_code == 200, "抽取失败绝不能阻断保存"

    runs = _runs()
    assert len(runs) == 1, runs
    r = runs[0]
    assert r["error"], "失败运行必须留 error，不得静默"
    assert "引擎 500" in r["error"], r
    assert r["facts_produced"] == 0, r
    # 需求 ②：失败行 reject_reasons_json 不为空 —— 两列都空会让"失败了"与
    # "本来就没什么可记的"在库里无法区分（db.record_memory_extract_run 的归一）
    assert json.loads(r["reject_reasons_json"]), r


def test_rejected_candidate_reasons_are_persisted(client, monkeypatch):
    """候选被抽取侧闸门拒绝 → facts_rejected 计数 + 逐条理由可读（不只是打日志）。"""
    bad = [{"kind": "price", "fact_key": "菜心", "value": "6.0"}]   # kind 不在白名单
    _patch_model(monkeypatch, output=_Msg(json.dumps(bad, ensure_ascii=False)))
    _enable_switch()
    rid, _ = _mk_receipt()

    assert _save(client, rid).status_code == 200

    runs = _runs()
    assert len(runs) == 1, runs
    r = runs[0]
    assert r["facts_produced"] == 0, r
    assert r["facts_rejected"] == 1, r
    reasons = json.loads(r["reject_reasons_json"])
    assert reasons and "kind 非法" in reasons[0], r


def test_no_diff_writes_zero_run_and_does_not_call_model(client, monkeypatch):
    """无字段差异：不调模型，但仍写一行 diffs_count=0 的记录（"为什么没产出"可查）。"""
    stats = _patch_model(monkeypatch)
    _enable_switch()
    # AI 预填与提交值逐字段等价（AI 侧数量键是 qty、用户侧是 quantity，数值比较）
    same = [{"name": "黑牛牌黑胡椒牛油", "qty": 2, "unit": "斤",
             "unit_price": 8.0, "amount": 16.0}]
    rid, _ = _mk_receipt(ai_items=same)

    assert _save(client, rid).status_code == 200

    assert stats.state.build_calls == 0 and stats.model.calls == 0
    runs = _runs()
    assert len(runs) == 1, runs
    r = runs[0]
    assert r["diffs_count"] == 0 and r["facts_produced"] == 0, r
    assert r["error"] == "", r
    # 本次没取过 prompt：必须留空，不能沿用上一次调用的取值（伪造数据）
    assert r["prompt_source"] == mx.PROMPT_SOURCE_NONE, r


def test_unresolvable_anchor_writes_run_record_with_error(client, monkeypatch):
    """供应商解析不出锚点 → 写一行带 error 的记录（"被锚点卡住"要能复盘出来）。"""
    _patch_model(monkeypatch)
    _enable_switch()
    # 不建 supplier、也不给 supplier_id：resolve_supplier_id 必然解析不出
    rid, _ = _mk_receipt(with_anchor=False)

    assert _save(client, rid).status_code == 200

    runs = _runs()
    assert len(runs) == 1, runs
    r = runs[0]
    assert "supplier_id" in r["error"], r
    assert r["diffs_count"] == 0 and r["facts_produced"] == 0, r


def test_switch_off_writes_no_run(client, monkeypatch):
    """开关关闭 = 抽取根本未被请求：不写记录（否则生产上每次保存都留一行噪声）。"""
    _patch_model(monkeypatch)
    _enable_switch("false")
    rid, _ = _mk_receipt()
    assert _save(client, rid).status_code == 200
    assert _runs() == []


# -------------------------------------------------------------
# 4. 写记录失败不得影响主流程（需求 ③）
# -------------------------------------------------------------
def test_record_write_failure_does_not_break_main_flow(client, monkeypatch, caplog):
    """运行记录落库抛异常 → 保存仍 200，只留 warning（不得把成功的保存弄成 500）。"""
    _patch_model(monkeypatch)
    _enable_switch()
    rid, _ = _mk_receipt()

    def _boom(**_kw):
        raise RuntimeError("磁盘只读")

    monkeypatch.setattr(db, "record_memory_extract_run", _boom)

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="api_receipts"):
        resp = _save(client, rid)
    assert resp.status_code == 200, resp.text
    assert any("运行记录落库失败" in r.getMessage() for r in caplog.records), \
        [r.getMessage() for r in caplog.records]


# -------------------------------------------------------------
# 5. list 的租户过滤与倒序
# -------------------------------------------------------------
def test_list_filters_by_tenant_and_orders_desc():
    """tenant 过滤不跨租；默认按时间倒序（run_id 倒序兜底同秒）。"""
    r1 = db.record_memory_extract_run(tenant_id=TENANT, receipt_id=1, trigger="save_edited")
    r2 = db.record_memory_extract_run(tenant_id=TENANT, receipt_id=2, trigger="approve")
    r3 = db.record_memory_extract_run(tenant_id=OTHER_TENANT, receipt_id=3,
                                      trigger="save_edited")
    assert r1 and r2 and r3

    mine = _runs(TENANT)
    assert [r["run_id"] for r in mine] == [r2, r1], mine
    assert all(r["tenant_id"] == TENANT for r in mine), mine
    assert [r["run_id"] for r in _runs(OTHER_TENANT)] == [r3]
    # 额外过滤维度：单据 / 入口（复盘时按入口分桶看"哪条路径一直在失败"）
    assert [r["run_id"] for r in _runs(TENANT, trigger="approve")] == [r2]
    assert [r["run_id"] for r in _runs(TENANT, receipt_id=1)] == [r1]
    # limit 生效
    assert len(_runs(TENANT, limit=1)) == 1


def test_list_empty_tenant_returns_other_tenants_rows_not_leaked():
    """另一个租户的库查询不返回本租户行（反向断言，防过滤写反）。"""
    db.record_memory_extract_run(tenant_id=OTHER_TENANT, receipt_id=9)
    assert _runs(TENANT) == []
