# -*- coding: utf-8 -*-
"""工作项②（收尾波次 SPEC_FINAL §2）：approve/抽取期锚点解析接记忆先验档（E2E 注记②）。

病灶（E2E 注记②实证）：`_extract_and_enqueue_memory_facts` 的抽取锚点只有
row.supplier_id → 按名 `db.resolve_supplier_id` 两档，vendor 按名解析不出档案时
抽取整段丢失（只留"缺 supplier_id"运行记录）。本波在按名解析落空后追加记忆先验档
（`db.resolve_supplier_id_from_memory`，与 parse 期/入账期第四档同款唯一实现，零改动
复用），失败分支语义不变。

覆盖（每条都可"回滚实现即失败"）：
- E1 无档案无记忆行：仍走失败分支 —— 返回 None、零 pending、运行记录 error 含
  「缺 supplier_id」（既有行为不变，防误伤）；
- E2 命中档：无供应商档案 + 同 vendor 名唯一 active 记忆行（锚 sid）→ 候选带
  supplier_id=sid 入 pending、运行记录 supplier_id=sid、caplog 出现「档=memory_prior」；
- E3 不猜：两条 active 记忆行锚不同 sid → 仍走 E1 失败分支，零 pending；
- E4 租户门：记忆行在 tenant_b、单据 tenant=default → 不锚（走 E1 失败分支）。

候选由 extract_candidate_facts(model=stub) 注入（monkeypatch `_build_default_model`，
与 test_pending_reject_reason 同款）；全程零模型调用、零 live 触碰（模块级临时
DB/RAG，逐用例独立库）。
"""

import importlib
import json
import re
import logging
import os
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="extract_anchor_prior_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_prior.db")
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
from app.services.supplier_normalizer import canonical_supplier_key  # noqa: E402

TENANT = "prior-tenant"
VENDOR = "新協興"          # vendor 名（无同档案；档案用另一名字，键不碰撞）
ARCHIVE = "供應商己"        # 供应商档案名 ≠ vendor 名
WHO = "qa@x"

# AI 侧数量键是 `qty`、用户侧是 `quantity`；两侧只差品名一处（其余字段刻意等价）。
AI_ITEMS = [{"name": "写法甲", "qty": 2, "unit": "斤", "unit_price": 8.0, "amount": 16.0}]
USER_ITEMS = [{"name": "本地牛腩", "quantity": 2.0, "unit": "斤",
               "unit_price": 8.0, "amount": 16.0}]

# stub 模型回包：一条合法 alias 候选（经 _to_facts / validate_fact 双闸后入队）
_PAYLOAD = [{"kind": "alias", "fact_key": "写法甲", "value": "本地牛腩",
             "confidence": 0.7}]

class _Msg:
    def __init__(self, content):
        self.content = content


# Canary 数组通道握手锚点（与 canary_guard.CANARY_ARRAY_INSTRUCTION_TEMPLATE 同约定）：
# 注入指令要求「数组最后一个元素是 {"__guard_token": token} 握手对象」。
_CANARY_RE = re.compile(r'"__guard_token"\s*:\s*"(CANARY_[0-9A-Fa-f]+)"')


class _StubModel:
    def invoke(self, messages):
        # 真实模型契约 = 从注入的锚定指令里回带握手元素；缺失即被 fail-closed 门
        # 拒绝（verify_canary_in_json_array → []）。stub 必须遵守同一契约：从消息里
        # 提取本轮动态 token 并作为数组末元素回带。若产品侧根本没注入指令（token=None），
        # stub 不伪造握手 → 校验失败 → 用例失败，判别力保留。
        token = None
        for msg in reversed(list(messages)):
            m = _CANARY_RE.search(getattr(msg, "content", "") or "")
            if m:
                token = m.group(1)
                break
        payload = list(_PAYLOAD)
        if token is not None:
            payload.append({"__guard_token": token})
        return _Msg(json.dumps(payload, ensure_ascii=False))


@pytest.fixture(autouse=True)
def _fresh_db():
    """每个用例一个干净的库（模块级 DB_PATH 是给 import 期用的兜底）。"""
    old = db.DB_PATH
    path = os.path.join(_TMP, f"prior_{uuid.uuid4().hex[:8]}.db")
    os.environ["DB_PATH"] = path
    importlib.reload(db)
    assert api_receipts.db.DB_PATH == path
    try:
        yield db
    finally:
        os.environ["DB_PATH"] = old
        db.DB_PATH = old
        db._make_engine()


def _enable_switch():
    db.set_app_setting(api_receipts.MEMORY_EXTRACT_SETTINGS_KEY, "true")


def _mk_receipt(tenant_id=TENANT):
    """无锚点单据（supplier_id=None）+ 一处品名修正（保证 diffs 非空）。"""
    rid = db.create_receipt(supplier_name=VENDOR, status="edited", tenant_id=tenant_id)
    db.update_receipt(
        rid, receipt_date="2026-08-01", total_amount=16.0, supplier_id=None,
        ai_prefill_json=json.dumps(
            {"vendor": VENDOR, "date": "2026-08-01", "total": 16.0,
             "items": AI_ITEMS}, ensure_ascii=False))
    return rid


def _seed_memory_prior(supplier_id, tenant_id=TENANT, notes="先验构造",
                       fact_key="alias:写法甲"):
    """落一条带锚点的 alias active 记忆行（vendor 名=VENDOR，构造记忆先验候选）。

    注意：同 vendor 同 fact_key 的第二次写入会 supersede 旧行 —— 构造多候选时必须
    换 fact_key（l0l1 A4b 同款教训），notes 去重键也要不同。
    """
    from app.services.memory_facts import MemoryFact
    fact = MemoryFact(kind="alias", fact_key=fact_key, value="本地牛腩",
                      supplier_id=str(supplier_id), tenant_id=tenant_id,
                      source_kind="extract")
    return db.save_memory_fact(tenant_id, VENDOR, fact_key, notes=notes,
                               sample="", source_kind="extract", fact=fact)


def _mk_archive(name=ARCHIVE, tenant_id=TENANT):
    sid, err = db.create_supplier(name, tenant_id=tenant_id)
    assert err is None, f"建供应商档案失败: {err}"
    return int(sid)


def _stub_model(monkeypatch):
    monkeypatch.setattr(mx, "_build_default_model", lambda cfg=None, **_kw: _StubModel())


def _call(rid, tenant_id=TENANT):
    return api_receipts._extract_and_enqueue_memory_facts(
        rid, db.get_receipt_row(rid, tenant_id=tenant_id), tenant_id,
        AI_ITEMS, USER_ITEMS, trigger="approve")


def _pending_rows(tenant_id=TENANT):
    return db.list_pending_memory(status="pending", tenant_id=tenant_id)


def _latest_run(tenant_id=TENANT):
    runs = db.list_memory_extract_runs(tenant_id=tenant_id, limit=1)
    assert runs, "运行记录必须存在（成功/失败都写）"
    return runs[0]


# -------------------------------------------------------------
# E1：无档案无记忆行 → 既有失败分支不变（防误伤）
# -------------------------------------------------------------
def test_e1_no_archive_no_memory_still_failure_branch(monkeypatch):
    _enable_switch()
    _stub_model(monkeypatch)
    # 夹具前提：vendor 名与任何档案名/归一键都不匹配（本用例压根无档案）
    rid = _mk_receipt()

    stats = _call(rid)

    assert stats is None, "无锚点必须返回 None（跳过抽取）"
    assert _pending_rows() == [], "失败分支零 pending"
    run = _latest_run()
    assert "缺 supplier_id" in str(run.get("error") or ""), run


# -------------------------------------------------------------
# E2：唯一候选记忆行 → 抽取候选带锚点入 pending
# -------------------------------------------------------------
def test_e2_unique_memory_prior_anchors_extract(monkeypatch, caplog):
    _enable_switch()
    _stub_model(monkeypatch)
    sid = _mk_archive()
    # 夹具前提：档案名与 vendor 名不碰撞（否则按名解析②档直接命中，先验档不执行）
    assert canonical_supplier_key(ARCHIVE) != canonical_supplier_key(VENDOR)
    assert db.resolve_supplier_id(VENDOR, tenant_id=TENANT) in (None, ""), \
        "夹具前提：按名解析必须落空"
    _seed_memory_prior(sid)

    with caplog.at_level(logging.INFO, logger="api_receipts"):
        stats = _call(rid := _mk_receipt())

    assert stats is not None and stats.get("enqueued") == 1, stats
    rows = _pending_rows()
    assert len(rows) == 1
    assert int(rows[0]["supplier_id"]) == sid, \
        "抽取候选必须携带记忆先验档锚出的 supplier_id 入 pending"
    run = _latest_run()
    assert int(run.get("supplier_id") or 0) == sid, \
        "运行记录 supplier_id 必须是先验档锚出的档案"
    assert run.get("error") in (None, ""), run
    assert any("档=memory_prior" in r.message for r in caplog.records), \
        "先验档命中必须留痕 档=memory_prior（与 parse 期回退锚定同款日志）"


# -------------------------------------------------------------
# E3：两条记忆行锚不同 sid → 不猜，失败分支
# -------------------------------------------------------------
def test_e3_multi_candidates_not_anchored(monkeypatch):
    _enable_switch()
    _stub_model(monkeypatch)
    sid1 = _mk_archive("供應商庚")
    sid2 = _mk_archive("供應商辛")
    # 两行必须用不同 fact_key + notes（同 fact_key 会 supersede；去重键含 notes），
    # 才能并存为两候选
    _seed_memory_prior(sid1, notes="先验甲", fact_key="alias:写法甲")
    _seed_memory_prior(sid2, notes="先验乙", fact_key="alias:写法乙")
    assert len(db.list_vendor_memory(vendor=VENDOR, tenant_id=TENANT)) == 2, \
        "夹具前提：两行 active 记忆并存"
    rid = _mk_receipt()

    stats = _call(rid)

    assert stats is None, "多候选不猜，走失败分支"
    assert _pending_rows() == [], "多候选时零 pending"
    assert "缺 supplier_id" in str(_latest_run().get("error") or "")


# -------------------------------------------------------------
# E4：租户门 —— 记忆行在别的租户 → 不锚
# -------------------------------------------------------------
def test_e4_tenant_gate_blocks_cross_tenant_prior(monkeypatch):
    _enable_switch()
    _stub_model(monkeypatch)
    sid_b = _mk_archive("供應商壬", tenant_id="tenant_b")
    _seed_memory_prior(sid_b, tenant_id="tenant_b")
    rid = _mk_receipt(tenant_id="default")

    stats = _call(rid, tenant_id="default")

    assert stats is None, "跨租户记忆行不得锚定（租户门）"
    assert _pending_rows(tenant_id="default") == []
    assert "缺 supplier_id" in str(_latest_run(tenant_id="default").get("error") or "")
