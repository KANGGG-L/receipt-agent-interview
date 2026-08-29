# -*- coding: utf-8 -*-
"""L3/T6 候选池卫生测试。

覆盖：
1. pending 候选总量上限（EVAL_CANDIDATE_MAX_PENDING）：超限时 create_eval_candidate
   返回既有错误形态 (None, False)，supervisor 钩子侧 warning（不阻断、不抛异常）；
2. 原图去重：同 sha1 同 reason 已有 pending 候选则跳过不重复建（复用 manifest
   src_sha1 思路，sha1 记入 ai_candidate_json.src_sha1）；不同 reason 不受影响；
3. audit_discrepancy 严重度门槛：少于 2 条且不含总额类差异（supplier/total/amount
   关键词）不建候选；含总额类差异或多条差异仍建候选。

全部离线：PIL 合成小图，独立 DB_PATH（/tmp tempfile），零外部调用。
"""

import logging
import os
import shutil
import sys
import tempfile

os.environ.setdefault(
    "DB_PATH", os.path.join(tempfile.mkdtemp(prefix="eval_cand_hygiene_"), "test.db"))
os.environ.setdefault("AUTH_ENABLED", "0")

DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_DIR = os.path.join(DEMO_DIR, "scripts")
for _p in (SCRIPTS_DIR, DEMO_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

from app import db
from app.chains import supervisor


def _make_png(path, size=(1100, 800)):
    from PIL import Image
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    Image.new("RGB", size, "white").save(str(path))
    return str(path)


@pytest.fixture(autouse=True)
def _clean_eval_candidates():
    """每用例清空 eval_candidate 表：上限与 sha1 去重都是全局口径，须隔离开。"""
    if db._EvalCandidateRow is not None:
        s = db.get_session()
        try:
            s.query(db._EvalCandidateRow).delete()
            s.commit()
        finally:
            s.close()
    yield
    if db._EvalCandidateRow is not None:
        s = db.get_session()
        try:
            s.query(db._EvalCandidateRow).delete()
            s.commit()
        finally:
            s.close()


@pytest.fixture
def receipt_ids(tmp_path):
    """三张互不相同内容的单据图（唯一底色）+ 独立 receipt 行。"""
    from PIL import Image
    ids = []
    for i in range(3):
        p = str(tmp_path / ("img_%d.png" % i))
        Image.new("RGB", (1100, 800), (240 + i, 240, 240)).save(p)
        rid = db.create_receipt(status="parsed")
        db.update_receipt(rid, image_path=p)
        ids.append(rid)
    return ids


def _candidate_for(rid, reason):
    rows = db.list_eval_candidates(reason=reason)
    for r in rows:
        if r.receipt_id == rid:
            return r
    return None


# ------------------------------------------------------------------
# 1. pending 总量上限
# ------------------------------------------------------------------
def test_pending_cap_rejects_and_warns(receipt_ids, caplog, monkeypatch):
    monkeypatch.setattr(db, "EVAL_CANDIDATE_MAX_PENDING", 2)
    rid1, rid2, rid3 = receipt_ids
    cid1, created1 = db.create_eval_candidate(rid1, "low_confidence")
    cid2, created2 = db.create_eval_candidate(rid2, "low_confidence")
    assert created1 and created2

    with caplog.at_level(logging.WARNING, logger="supervisor"):
        cid3, created3 = db.create_eval_candidate(rid3, "low_confidence")
        # supervisor 钩子侧：超限拒绝必须 warning 人话告警（不阻断）
        supervisor.maybe_create_eval_candidate(rid3, "low_confidence")
    assert (cid3, created3) == (None, False), "超限必须返回既有错误形态 (None, False)"
    assert _candidate_for(rid3, "low_confidence") is None
    warns = [r.message for r in caplog.records if "上限" in r.message]
    assert warns, "supervisor 钩子侧超限拒绝必须产生 warning"


def test_cap_counts_pending_only(receipt_ids, monkeypatch):
    """被 rejected/promoted 的候选不占 pending 上限名额。"""
    monkeypatch.setattr(db, "EVAL_CANDIDATE_MAX_PENDING", 1)
    rid1, rid2, rid3 = receipt_ids
    cid1, _ = db.create_eval_candidate(rid1, "gate_reject")
    db.set_eval_candidate_status(cid1, "rejected")
    cid2, created2 = db.create_eval_candidate(rid2, "gate_reject")
    assert created2, "rejected 候选不占 pending 名额，新建应成功"
    cid3, created3 = db.create_eval_candidate(rid3, "gate_reject")
    assert (cid3, created3) == (None, False), "第二个 pending 达上限应拒绝"


# ------------------------------------------------------------------
# 2. 原图 sha1 去重
# ------------------------------------------------------------------
def test_same_image_same_reason_dedup(tmp_path):
    """同 sha1（同字节图）同 reason 已有 pending → 跳过不重复建。"""
    shared = _make_png(str(tmp_path / "shared.png"))
    rid1 = db.create_receipt(status="parsed")
    db.update_receipt(rid1, image_path=str(tmp_path / "copy1.png"))
    shutil.copyfile(shared, str(tmp_path / "copy1.png"))
    rid2 = db.create_receipt(status="parsed")
    db.update_receipt(rid2, image_path=str(tmp_path / "copy2.png"))
    shutil.copyfile(shared, str(tmp_path / "copy2.png"))

    cid1, created1 = db.create_eval_candidate(rid1, "user_edit")
    assert created1
    cid2, created2 = db.create_eval_candidate(rid2, "user_edit")
    assert (cid2, created2) == (cid1, False), "同图同 reason 必须去重返回既有候选"
    assert _candidate_for(rid2, "user_edit") is None, "不得为重复图新建候选"

    # 不同 reason 不受同图去重影响
    cid3, created3 = db.create_eval_candidate(rid2, "gate_reject")
    assert created3 and cid3 != cid1

    # sha1 记入 ai_candidate_json（src_sha1 思路）
    row = db.get_eval_candidate(cid1)
    import json as _json
    payload = _json.loads(row.ai_candidate_json or "{}")
    assert payload.get("src_sha1"), "候选必须记录原图 src_sha1"


def test_different_image_no_dedup(receipt_ids):
    """不同图（不同 sha1）同 reason 各自建候选，幂等语义不变。"""
    rid1, rid2, _ = receipt_ids
    cid1, created1 = db.create_eval_candidate(rid1, "audit_discrepancy")
    cid2, created2 = db.create_eval_candidate(rid2, "audit_discrepancy")
    assert created1 and created2 and cid1 != cid2


# ------------------------------------------------------------------
# 3. audit_discrepancy 严重度门槛
# ------------------------------------------------------------------
def test_single_minor_discrepancy_not_created(receipt_ids):
    """单条轻微差异（币种缺失，无总额类关键词）不建候选。"""
    rid = receipt_ids[0]
    supervisor._reflow_from_state({
        "receipt_id": rid,
        "data": None,
        "audit_result": {"discrepancies": [{"field": "currency", "issue": "币种缺失"}]},
    })
    assert _candidate_for(rid, "audit_discrepancy") is None


def test_total_class_discrepancy_still_created(receipt_ids):
    """含总额类关键词（total/amount/supplier）的单条差异仍建候选。"""
    rid = receipt_ids[1]
    supervisor._reflow_from_state({
        "receipt_id": rid,
        "data": None,
        "audit_result": {"discrepancies": [
            {"field": "total", "issue": "总额与明细合计不符"},
        ]},
    })
    assert _candidate_for(rid, "audit_discrepancy") is not None


def test_multi_minor_discrepancies_still_created(receipt_ids):
    """达到严重条数（>=2）的轻微差异组合仍建候选。"""
    rid = receipt_ids[2]
    supervisor._reflow_from_state({
        "receipt_id": rid,
        "data": None,
        "audit_result": {"discrepancies": [
            {"field": "currency", "issue": "币种缺失"},
            {"field": "items[1].name", "issue": "第1行品名缺失"},
        ]},
    })
    assert _candidate_for(rid, "audit_discrepancy") is not None
