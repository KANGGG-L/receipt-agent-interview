# -*- coding: utf-8 -*-
"""P8b·AA2 失败可重跑测试：抽取失败后能按 receipt_id / 失败运行记录重放，且重放幂等。

为什么单独建这个文件：`memory_extract_runs`（AA1）把运行记录落了库，但"可查"不等于
"可重跑"。本文件钉住四件事：
1. 构造一条失败运行记录 → 重放后**成功**且**写入一条新的运行记录**（旧记录不动）；
2. **幂等**：连续重放两次，第二次新增候选为 0（去重走的是 `db.enqueue_pending_memory`
   的既有判据，不是本工具自己那套）；
3. **预演零写入**：缺省模式下源库的 pending 候选与运行记录都不变，但报告里的数字是真跑
   出来的（重放在源库副本上进行）；
4. **与生产走同一实现**：重放**必须**调用 `api_receipts._extract_and_enqueue_memory_facts`
   —— 用 spy 钉死，防止有人日后把管线抄一份进脚本（本仓高发缺陷①多份实现）。

判别力（反证）：把 `db.enqueue_pending_memory` 的去重判定去掉 → 幂等用例失败
（见交付报告的突变体 A/B 实测：第二次新增由 0 变 2）。把重放的委托改成自建管线 →
`test_replay_delegates_to_production_implementation` 失败。

环境隔离：模块级把 DB_PATH / RAG_DIR 指向临时目录（必须在 import app.* 之前 ——
`import app.main` 会在 import 期执行 db._make_engine() 的迁移块，直接对目标库做 ALTER）。
模型一律注入 stub（monkeypatch `memory_extract._build_default_model`），全程不联网。
"""

import json
import os
import sys
import tempfile
from types import SimpleNamespace

# 隔离环境：临时 DB / 向量目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="memory_extract_replay_")
os.environ["DB_PATH"] = os.path.join(_TMP, "replay.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")

_DEMO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo")
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)
_SCRIPTS_DIR = os.path.join(_DEMO_DIR, "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import pytest  # noqa: E402

import app.api_receipts as api_receipts  # noqa: E402
import app.services.memory_extract as mx  # noqa: E402
from app import db  # noqa: E402
import memory_extract_replay as replay_mod  # noqa: E402

TENANT = "aa2-tenant"
OTHER_TENANT = "aa2-other"
VENDOR = "祥興食品"

AI_ITEMS = [{"name": "黑牛", "qty": 2, "unit": "斤", "unit_price": 8.0, "amount": 16.0}]
USER_ITEMS = [{"name": "黑牛牌黑胡椒牛油", "quantity": 2.0, "unit": "斤",
               "unit_price": 8.0, "amount": 16.0}]
CANDIDATE_PAYLOAD = [{"kind": "alias", "fact_key": "黑牛", "value": "黑牛牌黑胡椒牛油",
                      "confidence": 0.7}]


class _Msg:
    def __init__(self, content):
        self.content = content


class _StubModel:
    def __init__(self, output):
        self.output = output
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        return self.output


def _patch_model(monkeypatch, payload=None):
    """注入确定性模型（不联网）。同一个 payload 让两次重放给出逐字相同的候选。"""
    model = _StubModel(_Msg(json.dumps(
        CANDIDATE_PAYLOAD if payload is None else payload, ensure_ascii=False)))

    def _fake_build(cfg=None, **_kw):
        return model

    monkeypatch.setattr(mx, "_build_default_model", _fake_build)
    return model


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch, tmp_path):
    """每个用例一个干净库（模块级 DB_PATH 是共享语义，必须按用例切到 tmp_path）。"""
    old = db.DB_PATH
    path = str(tmp_path / "aa2.db")
    monkeypatch.setenv("DB_PATH", path)
    db.DB_PATH = path
    db._make_engine()
    try:
        yield
    finally:
        db.DB_PATH = old
        db._make_engine()


def _mk_receipt(status="edited", ai_items=None, final_items=None, with_anchor=True,
                tenant_id=TENANT, vendor=VENDOR):
    sid = None
    if with_anchor:
        sid, _ = db.create_supplier(vendor, tenant_id=tenant_id)
    rid = db.create_receipt(supplier_name=vendor, status=status, tenant_id=tenant_id)
    db.update_receipt(
        rid, receipt_date="2026-08-01", total_amount=16.0, supplier_id=sid,
        ai_prefill_json=json.dumps(
            {"vendor": vendor, "date": "2026-08-01", "total": 16.0,
             "items": ai_items if ai_items is not None else AI_ITEMS},
            ensure_ascii=False))
    if final_items is not None:
        db.set_receipt_items(rid, final_items, tenant_id=tenant_id)
    return rid, sid


def _pending_ids(status="pending", tenant_id=TENANT):
    return {r["id"] for r in db.list_pending_memory(status=status, tenant_id=tenant_id)}


def _run_ids(tenant_id=TENANT, receipt_id=None):
    return {r["run_id"] for r in db.list_memory_extract_runs(
        tenant_id=tenant_id, receipt_id=receipt_id)}


# -------------------------------------------------------------
# 1. 失败运行记录 → 重放成功且写新的运行记录
# -------------------------------------------------------------
def test_replay_failed_run_succeeds_and_writes_new_run(monkeypatch):
    """重放成功的定义：新写一条 trigger=replay 且 error 为空的运行记录，旧失败记录保留。"""
    _patch_model(monkeypatch)
    rid, _ = _mk_receipt(final_items=[dict(USER_ITEMS[0])])
    failed_run = db.record_memory_extract_run(
        tenant_id=TENANT, receipt_id=rid, vendor=VENDOR, trigger="save_edited",
        error="引擎 500：上游不可用")
    assert failed_run

    report = replay_mod.replay([(rid, TENANT)], apply=True)

    assert report["targets"] == 1 and report["replayed"] == 1, report
    assert report["success"] == 1 and report["failed"] == 0, report["results"]
    res = report["results"][0]
    assert res["status"] == "replayed", res
    assert res["run_id"] and res["run_id"] != failed_run, res
    assert res["error"] == "", res
    assert res["candidates"] == len(CANDIDATE_PAYLOAD), res
    assert res["enqueued_new"] == len(CANDIDATE_PAYLOAD), res
    assert report["candidates_produced"] == len(CANDIDATE_PAYLOAD), report

    # 新旧两条运行记录都在（重放是"追加重放"，不是改写历史）
    assert _run_ids(TENANT, rid) == {failed_run, res["run_id"]}
    newest = db.list_memory_extract_runs(tenant_id=TENANT, receipt_id=rid, limit=1)[0]
    assert newest["trigger"] == replay_mod.TRIGGER_REPLAY, newest
    assert newest["error"] == "", newest


# -------------------------------------------------------------
# 2. 幂等：连续两次重放，第二次新增候选为 0
# -------------------------------------------------------------
def test_replay_twice_second_adds_zero_candidates(monkeypatch):
    """判别式（反证目标）：去掉 `db.enqueue_pending_memory` 的去重判定 → 本用例失败。

    同一个 stub 输出让两次重放产出逐字相同的候选与内容，因此第二次必须全部命中既有
    pending 行（并入触发来源、不新增行）。
    """
    _patch_model(monkeypatch)
    rid, _ = _mk_receipt(final_items=[dict(USER_ITEMS[0])])

    first = replay_mod.replay([(rid, TENANT)], apply=True)["results"][0]
    pending_after_first = _pending_ids()
    assert first["enqueued_new"] == len(CANDIDATE_PAYLOAD), first

    second = replay_mod.replay([(rid, TENANT)], apply=True)["results"][0]

    assert second["candidates"] == len(CANDIDATE_PAYLOAD), second
    assert second["enqueued_new"] == 0, second
    assert second["deduped"] == second["enqueued_pre_dedup"], second
    assert _pending_ids() == pending_after_first, "第二次重放不得新增 pending 行"
    # 运行记录**不**去重（按"次"计）：两次重放各留一条，便于复盘"反复重跑"这个信号
    assert len(_run_ids(TENANT, rid)) == 2


def test_replay_idempotent_on_failed_only_mode(monkeypatch):
    """--failed-only 的幂等同样成立（扫描入口走的是同一段重放实现）。"""
    _patch_model(monkeypatch)
    rid, _ = _mk_receipt(final_items=[dict(USER_ITEMS[0])])
    db.record_memory_extract_run(tenant_id=TENANT, receipt_id=rid, vendor=VENDOR,
                                 trigger="save_edited", error="超时")

    first = replay_mod.replay(None, apply=True, tenant_id=TENANT)
    assert first["scanned_failed_runs"] == 1 and first["targets"] == 1, first
    assert first["enqueued_new"] == len(CANDIDATE_PAYLOAD), first

    second = replay_mod.replay(None, apply=True, tenant_id=TENANT)
    assert second["enqueued_new"] == 0, second


# -------------------------------------------------------------
# 3. 预演：源库零写入，但读数是真跑出来的
# -------------------------------------------------------------
def test_dry_run_writes_nothing_to_source(monkeypatch):
    """缺省预演：源库 pending 与运行记录都不变；报告仍给出真实候选/入队读数。"""
    _patch_model(monkeypatch)
    rid, _ = _mk_receipt(final_items=[dict(USER_ITEMS[0])])
    db.record_memory_extract_run(tenant_id=TENANT, receipt_id=rid, vendor=VENDOR,
                                 trigger="save_edited", error="引擎 500")
    pending_before = _pending_ids()
    runs_before = _run_ids(TENANT, rid)

    report = replay_mod.replay(None, apply=False, tenant_id=TENANT)

    assert report["applied"] is False, report
    assert report["enqueued_new"] == len(CANDIDATE_PAYLOAD), report["results"]
    assert report["db_path"] != db.DB_PATH, "预演必须落在副本上"
    # 源库零写入（这正是"预演"的定义）
    assert _pending_ids() == pending_before
    assert _run_ids(TENANT, rid) == runs_before


def test_dry_run_does_not_claim_success_via_copy_leak(monkeypatch):
    """反向断言：预演报告里的 db_path 是副本，且副本存在过（不是"没跑"）。"""
    _patch_model(monkeypatch)
    rid, _ = _mk_receipt(final_items=[dict(USER_ITEMS[0])])
    report = replay_mod.replay([(rid, TENANT)], apply=False)
    assert report["db_path"] and report["db_path"] != db.DB_PATH
    assert report["success"] == 1, report["results"]


# -------------------------------------------------------------
# 4. 与生产走同一实现（防止日后被人抄成第二份管线）
# -------------------------------------------------------------
def test_replay_delegates_to_production_implementation(monkeypatch):
    """判别式：重放必须调用 `api_receipts._extract_and_enqueue_memory_facts`。

    本仓高发缺陷①是"同一根因多份实现只改一份"—— 若有人把管线抄进重放脚本，本用例失败。
    """
    _patch_model(monkeypatch)
    rid, _ = _mk_receipt(final_items=[dict(USER_ITEMS[0])])
    calls = []
    original = api_receipts._extract_and_enqueue_memory_facts

    def _spy(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(api_receipts, "_extract_and_enqueue_memory_facts", _spy)

    replay_mod.replay([(rid, TENANT)], apply=True)

    assert len(calls) == 1, calls
    args, kwargs = calls[0]
    # 与生产调用点同口径：单据 id / 行 / 租户 / AI 明细 / 用户明细 / trigger
    assert args[0] == rid and args[2] == TENANT, calls
    assert kwargs.get("trigger") == replay_mod.TRIGGER_REPLAY, calls


def test_replay_reconstructs_same_user_items_as_approve_path(monkeypatch):
    """重放的 user_items 与 approve 入口同源（`db.get_receipt_items`），否则 diff 会失配。"""
    _patch_model(monkeypatch)
    rid, _ = _mk_receipt(final_items=[dict(USER_ITEMS[0])])
    seen = {}
    original = api_receipts._extract_and_enqueue_memory_facts

    def _spy(*args, **kwargs):
        seen["user_items"] = args[4]
        return original(*args, **kwargs)

    monkeypatch.setattr(api_receipts, "_extract_and_enqueue_memory_facts", _spy)
    replay_mod.replay([(rid, TENANT)], apply=True)

    assert seen["user_items"] == db.get_receipt_items(rid, tenant_id=TENANT)


# -------------------------------------------------------------
# 5. 边界：单据不存在 / 开关跟随
# -------------------------------------------------------------
def test_missing_receipt_is_reported_not_silently_skipped(monkeypatch):
    _patch_model(monkeypatch)
    report = replay_mod.replay([(999999, TENANT)], apply=True)
    assert report["results"][0]["status"] == "not_found", report["results"]
    assert report["replayed"] == 0 and report["skipped"]["not_found"] == 1


def test_honor_switch_off_reports_skip(monkeypatch):
    """--honor-switch 且开关关闭 → 明确报 switch_off，不写任何运行记录。"""
    _patch_model(monkeypatch)
    rid, _ = _mk_receipt(final_items=[dict(USER_ITEMS[0])])
    db.set_app_setting(api_receipts.MEMORY_EXTRACT_SETTINGS_KEY, "false")

    report = replay_mod.replay([(rid, TENANT)], apply=True, force_switch=False)

    assert report["results"][0]["status"] == "switch_off", report["results"]
    assert _run_ids(TENANT, rid) == set()


def test_default_forces_switch_on_and_says_so(monkeypatch):
    """缺省强制开启（显式运维重跑）：开关关闭也能重放，且报告里写明是强制的。"""
    _patch_model(monkeypatch)
    rid, _ = _mk_receipt(final_items=[dict(USER_ITEMS[0])])
    db.set_app_setting(api_receipts.MEMORY_EXTRACT_SETTINGS_KEY, "false")

    report = replay_mod.replay([(rid, TENANT)], apply=True)

    assert report["force_switch"] is True and report["success"] == 1, report["results"]
    assert report["enqueued_new"] == len(CANDIDATE_PAYLOAD), report["results"]


def test_no_anchor_is_reported_as_failed_not_as_success(monkeypatch):
    """无锚点（供应商解析不出）→ 重放仍失败，且原因可读（不得冒充成功）。"""
    _patch_model(monkeypatch)
    rid, _ = _mk_receipt(with_anchor=False, final_items=[dict(USER_ITEMS[0])])

    report = replay_mod.replay([(rid, TENANT)], apply=True)

    res = report["results"][0]
    assert res["status"] == "failed", res
    assert "supplier_id" in res["error"], res
    assert report["failed"] == 1 and report["success"] == 0, report
    # 失败也要留痕（这正是"失败可重放"的前提：先能看见失败）
    assert len(_run_ids(TENANT, rid)) == 1


# -------------------------------------------------------------
# 6. 报告口径：丢弃原因分布
# -------------------------------------------------------------
def test_reject_reason_distribution_is_reported(monkeypatch):
    """非法候选 → 运行记录 facts_rejected 有数，报告给出可读的原因分布。"""
    _patch_model(monkeypatch, payload=[{"kind": "price", "fact_key": "菜心", "value": "6"}])
    rid, _ = _mk_receipt(final_items=[dict(USER_ITEMS[0])])

    report = replay_mod.replay([(rid, TENANT)], apply=True)

    assert report["candidates_produced"] == 0, report
    assert report["rejected"] == 1, report
    assert any("kind 非法" in k for k in report["reject_reason_distribution"]), \
        report["reject_reason_distribution"]


def test_no_diff_reports_no_diff_not_failure(monkeypatch):
    """无字段差异：不算失败（error 为空），报告单列为 no_diff。"""
    model = _patch_model(monkeypatch)
    same = [{"name": "黑牛牌黑胡椒牛油", "qty": 2, "unit": "斤",
             "unit_price": 8.0, "amount": 16.0}]
    rid, _ = _mk_receipt(ai_items=same,
                         final_items=[dict(USER_ITEMS[0])])

    report = replay_mod.replay([(rid, TENANT)], apply=True)

    assert model.calls == 0, "无差异不得调用模型"
    assert report["results"][0]["status"] == "no_diff", report["results"]
    assert report["failed"] == 0 and report["skipped"]["no_diff"] == 1, report


# -------------------------------------------------------------
# 7. 红线闸：--apply 打 live 需显式放行
# -------------------------------------------------------------
def test_apply_on_live_is_refused_without_allow_live(monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", replay_mod._LIVE_DB)
    with pytest.raises(SystemExit) as ei:
        replay_mod._assert_apply_target(True, allow_live=False)
    assert "live" in str(ei.value)
    # 预演与显式放行都不拦
    replay_mod._assert_apply_target(False, allow_live=False)
    replay_mod._assert_apply_target(True, allow_live=True)


# -------------------------------------------------------------
# 8. CLI 参数校验
# -------------------------------------------------------------
def test_cli_requires_exactly_one_mode():
    with pytest.raises(SystemExit):
        replay_mod.main(["--apply"])
    with pytest.raises(SystemExit):
        replay_mod.main(["--receipt-id", "1", "--failed-only"])


def test_cli_json_dry_run_end_to_end(monkeypatch, capsys):
    """CLI 直跑（预演 + --json）：输出可解析、且源库零写入。"""
    _patch_model(monkeypatch)
    rid, _ = _mk_receipt(final_items=[dict(USER_ITEMS[0])])

    rc = replay_mod.main(["--receipt-id", str(rid), "--tenant-id", TENANT, "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["applied"] is False and payload["success"] == 1, payload
    assert _pending_ids() == set()
