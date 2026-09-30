# -*- coding: utf-8 -*-
"""K3 触发接线测试：save_edited（复核保存）与 approve_receipt（入账）**两处**都必须触发
LLM 抽取，并把规则级候选送进 pending 人审队列。

为什么单独建这个文件：本仓高发「修 A 漏 B」——同一个根因有多处入口，只接了一处看起来
也"做完了"。抽取的触发点恰恰有两处（计划 §10.2 明确要求两处都要接），因此必须各有一个
用例钉住：去掉任一触发调用点，对应的用例必须失败（见模块末尾的反证说明）。

覆盖（对应任务 K3 的完成判定 ①-⑤）：
1. 两处触发各有用例（save_edited / approve），断言模型被调用**且**候选落到 pending；
2. 无字段差异时不调模型（用 stub 的构建次数与 invoke 次数双计数断言）；
3. 候选入 pending 且带 supplier_id / fact_kind / fact_key / fact_value（K2 新增的列）；
4. 四类规则级候选（alias/unit/layout/price_range）一律入闸（计划 C6「规则级一律人审」）；
5. NOOP（既有同值事实）不进人工闸 —— 否则人工闸会被"没有变更的行"刷爆；
6. 非法候选被**写入侧闸门**拒绝且不静默（计数 + 日志），不进人工闸；
7. Q2：三条"没进队列"的分支（抽取侧闸门 / 写入侧闸门 / NOOP）都会留一条
   status='rejected' 的审计行，理由落 `pending_memory.reject_reason`（端到端 HTTP 判据
   见 demo/tests/test_pending_reject_reason.py）；故"不入队"在这里一律判在**可批准队列**上
   （没有 status='pending' 的行），而不是"表里没有行"；
8. 抽取抛异常时主流程仍成功，且日志里有 error（caplog 断言，先自校准证明采集通道有效）；
9. 开关缺省关闭 = 与接线前行为逐字节一致（零行为变化）。

判别力（反证）：把任一触发调用点注释掉 → 对应用例必须失败（save_edited 触发对应第 1 组，
approve 触发对应第 2 组）。两条断言分别指向各自的触发点，不是同一条断言覆盖两处。

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
_TMP = tempfile.mkdtemp(prefix="memory_extract_triggers_")
os.environ["DB_PATH"] = os.path.join(_TMP, "triggers.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")

_DEMO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo")
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import pytest  # noqa: E402

import app.api_receipts as api_receipts  # noqa: E402
import app.services.memory_extract as mx  # noqa: E402
from app import db  # noqa: E402
from app.services import settings_service  # noqa: E402
from app.services.memory_facts import SOURCE_MANUAL, MemoryFact  # noqa: E402

TENANT = "k3-tenant"
VENDOR = "祥興食品"
STAFF = {"X-Role": "staff", "X-Tenant-Id": TENANT}
OWNER = {"X-Role": "owner", "X-Tenant-Id": TENANT}

# AI 侧数量键是 `qty`（ReceiptData 契约），用户侧是 `quantity`（receipt_items 契约）；
# 两侧只差品名一处，其余字段刻意保持等价（数值 2 与 2.0 不算修正）。
AI_ITEMS = [{"name": "黑牛", "qty": 2, "unit": "斤", "unit_price": 8.0, "amount": 16.0}]
USER_ITEMS = [{"name": "黑牛牌黑胡椒牛油", "quantity": 2.0, "unit": "斤",
               "unit_price": 8.0, "amount": 16.0}]

# 模型返回：一条 alias 候选（key/value 与上方的修正一致）
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
    """注入 stub 模型，并统计"模型被构建"的次数。

    `extract_candidate_facts` 在**有差异**时才调 `_build_default_model`，因此构建次数就是
    "有没有真的去调模型"的硬计数（无差异时构建次数必须为 0，而不是"调了但没结果"）。
    """
    model = _StubModel(output=output if output is not None else _Msg(
        json.dumps(CANDIDATE_PAYLOAD, ensure_ascii=False)), exc=exc)
    state = SimpleNamespace(build_calls=0)
    stats = SimpleNamespace(model=model, state=state)

    def _fake_build(cfg=None, **_kw):
        state.build_calls += 1
        return model

    monkeypatch.setattr(mx, "_build_default_model", _fake_build)
    return stats


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch, tmp_path):
    """每个用例一个干净的库。

    为什么不能只靠 conftest 的兜底：本文件在模块级声明了 DB_PATH（为了不碰 live 库），
    而兜底对"环境声明的库"是**尊重**的（模块级声明的既有语义＝全局共享），于是各用例会
    共享同一个库、上一条的 pending 行漏到下一条（实测）。故此处显式切到本轮 tmp_path
    的库，并在 env 上也声明（否则会被兜底判成泄漏而改写落点）。
    """
    old = db.DB_PATH
    path = str(tmp_path / "k3.db")
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
    """打开/关闭抽取开关（settings 键，走真实读取路径）。"""
    db.set_app_setting(api_receipts.MEMORY_EXTRACT_SETTINGS_KEY, value)


def _mk_receipt(status="edited", ai_items=None, final_items=None, with_anchor=True):
    """建一张带 AI 预填的单据；返回 (receipt_id, supplier_id)。"""
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


def _pending_rows():
    """本租户 pending_memory 的全部行（含 Q2 的 status='rejected' 审计行）。

    AH3 之后 `db.list_pending_memory` 缺省只列 `status='pending'`（人审队列语义），
    故"全表快照"必须按状态分别取再合并 —— 不再依赖已撤销的"不传 status 即不过滤"行为。
    """
    return (db.list_pending_memory(status="pending", tenant_id=TENANT)
            + db.list_pending_memory(status="rejected", tenant_id=TENANT))


def _reviewable_rows():
    """人工闸里**真正待批**的行。

    Q2 之后 pending_memory 里还有一族"没进队列"的审计行（status='rejected'，带
    reject_reason），故断言"不入队"时必须落在可批准队列上 —— 判据是"没有 status='pending'
    的行"，而不是"表里没有行"。为什么拒绝行不能是 pending：被拒候选的载荷过不了
    validate_fact，若以待审行入队，审核者点"批准"时 approve 链会回落自由文本路径，
    把非法候选写成一条自由文本记忆（详见 demo/tests/test_pending_reject_reason.py）。
    """
    return [r for r in _pending_rows() if r["status"] == "pending"]


def _refused_rows():
    """Q2 的拒绝/落选审计行（status='rejected'）。"""
    return [r for r in _pending_rows() if r["status"] == "rejected"]


# -------------------------------------------------------------
# 1. 触发点一：save_edited（复核保存）
# -------------------------------------------------------------
def test_save_edited_triggers_extraction_and_enqueues_pending(client, monkeypatch):
    """判别式（触发点一）：去掉 save_edited 里的 `_extract_and_enqueue_memory_facts` 调用
    → 本用例的 build_calls/invoke 计数与 pending 行数断言全部失败。"""
    stats = _patch_model(monkeypatch)
    _enable_switch()
    rid, sid = _mk_receipt()

    resp = _save(client, rid)
    assert resp.status_code == 200, resp.text

    # ① 真的调了模型（有字段差异）
    assert stats.state.build_calls == 1, "有字段差异必须构建模型"
    assert stats.model.calls == 1, "有字段差异必须调一次模型"
    # ② 候选落到 pending，且带 K2 的 fact 载荷
    rows = _pending_rows()
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["status"] == "pending"
    assert row["vendor"] == VENDOR
    assert row["source_kind"] == "extract"
    assert row["supplier_id"] == sid, "入队必须带主体锚点（不是 None）"
    assert row["fact_kind"] == "alias"
    assert row["fact_key"] == "黑牛"
    assert row["fact_value"] == "黑牛牌黑胡椒牛油"
    assert "alias：黑牛 → 黑牛牌黑胡椒牛油" in row["content"], row["content"]
    assert str(rid) in row["source_receipt_ids_json"]


def test_save_edited_without_diff_does_not_call_model(client, monkeypatch):
    """无字段差异 → 不建模型、不调模型、不入队（零成本零噪声）。"""
    stats = _patch_model(monkeypatch)
    _enable_switch()
    # AI 预填与提交值完全一致（用户没改任何字段）
    rid, _ = _mk_receipt(ai_items=[{"name": "黑牛牌黑胡椒牛油", "qty": 2, "unit": "斤",
                                    "unit_price": 8.0, "amount": 16.0}])
    resp = _save(client, rid)
    assert resp.status_code == 200, resp.text
    assert stats.state.build_calls == 0, "无差异不得构建模型"
    assert stats.model.calls == 0, "无差异不得调模型"
    assert _pending_rows() == []


# -------------------------------------------------------------
# 2. 触发点二：approve_receipt（入账）
# -------------------------------------------------------------
def test_approve_triggers_extraction_and_enqueues_pending(client, monkeypatch):
    """判别式（触发点二）：去掉 approve_receipt 里的 `_extract_and_enqueue_memory_facts`
    调用 → 本用例的计数与 pending 行断言全部失败。"""
    stats = _patch_model(monkeypatch)
    _enable_switch()
    # 用 with_anchor=False：approve 会按供应商名自动建档，锚点解析走"按名回退"那条路
    rid, _ = _mk_receipt(with_anchor=False, final_items=[dict(USER_ITEMS[0])])

    resp = client.post("/api/receipt/%d/approve" % rid, headers=OWNER,
                       json={"version": db.get_receipt_row(rid).version})
    assert resp.status_code == 200, resp.text

    assert stats.state.build_calls == 1, "入账同样必须触发抽取"
    assert stats.model.calls == 1
    rows = _pending_rows()
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["fact_kind"] == "alias" and row["fact_key"] == "黑牛"
    assert row["fact_value"] == "黑牛牌黑胡椒牛油"
    # 锚点必须解析出来（approve 已自动建档）：落 NULL 等于这条候选在读侧永远召不回
    assert row["supplier_id"] is not None, "入队载荷的 supplier_id 不得为 NULL"
    assert str(rid) in row["source_receipt_ids_json"]


def test_approve_without_diff_does_not_call_model(client, monkeypatch):
    """入账侧的无差异路径同样不调模型（两处触发都要有负向用例）。"""
    stats = _patch_model(monkeypatch)
    _enable_switch()
    ai_same = [{"name": "黑牛", "qty": 2, "unit": "斤", "unit_price": 8.0, "amount": 16.0}]
    final_same = [{"name": "黑牛", "quantity": 2.0, "unit": "斤",
                   "unit_price": 8.0, "amount": 16.0}]
    rid, _ = _mk_receipt(ai_items=ai_same, final_items=final_same)
    resp = client.post("/api/receipt/%d/approve" % rid, headers=OWNER,
                       json={"version": db.get_receipt_row(rid).version})
    assert resp.status_code == 200, resp.text
    assert stats.state.build_calls == 0 and stats.model.calls == 0
    assert _pending_rows() == []


# -------------------------------------------------------------
# 3. 规则级四类全入闸 + NOOP 不入队 + 非法不入队
# -------------------------------------------------------------
def test_all_rule_level_kinds_enter_the_gate(client, monkeypatch):
    """计划 C6：规则级（memory_facts 四类白名单）**一律人审**。

    抽取侧只产规则级事实（案例级走 approve 处的 ingest_memory 自由文本通道），故四类
    全都应落 pending —— 只放 alias/unit 会把 layout/price_range 静默丢掉。
    """
    payload = [
        {"kind": "alias", "fact_key": "黑牛", "value": "黑牛牌黑胡椒牛油"},
        {"kind": "unit", "fact_key": "菜心", "value": "斤"},
        {"kind": "layout", "fact_key": "版式", "value": "手写"},
        {"kind": "price_range", "fact_key": "菜心", "value": "8.0-10.0"},
    ]
    stats = _patch_model(monkeypatch, output=_Msg(json.dumps(payload, ensure_ascii=False)))
    _enable_switch()
    rid, _ = _mk_receipt()
    assert _save(client, rid).status_code == 200
    assert stats.model.calls == 1
    rows = _pending_rows()
    assert sorted(r["fact_kind"] for r in rows) == ["alias", "layout", "price_range", "unit"]
    for r in rows:
        assert r["fact_key"] and r["fact_value"], r


def test_noop_same_value_is_not_enqueued(client, monkeypatch):
    """既有同 (tenant, supplier, fact_key) 同值事实 → NOOP，不进入人工闸（不被空转变刷爆）。

    Q2 后本条判据落在**可批准队列**上：NOOP 仍会留一条 status='rejected' 的审计行
    （"落选"理由可查），但它不占 status='pending' 的闸门位。
    """
    stats = _patch_model(monkeypatch)
    _enable_switch()
    rid, sid = _mk_receipt()
    db.save_memory_fact(TENANT, VENDOR, "黑牛", fact=MemoryFact(
        kind="alias", fact_key="黑牛", value="黑牛牌黑胡椒牛油",
        supplier_id=str(sid), tenant_id=TENANT, source_kind=SOURCE_MANUAL))

    assert _save(client, rid).status_code == 200
    assert stats.model.calls == 1, "既有事实不影响「是否调模型」（差异仍在）"
    assert _reviewable_rows() == [], "同值事实不得进人工闸"
    refused = _refused_rows()
    assert len(refused) == 1, refused
    assert "已存在同值事实" in refused[0]["reject_reason"], refused[0]["reject_reason"]


def test_rejected_candidate_is_not_enqueued_and_is_visible(client, monkeypatch, caplog):
    """写入侧闸门（validate_fact）拒绝的候选：不进人工闸、有计数、有日志，且**理由落库**。

    判别式：把 helper 里的 validate_fact 复核删掉 → 非法候选会被送进 pending，本用例失败；
    把 Q2 的 `_record_refused_candidate` 调用删掉 → reject_reason 断言失败。
    """
    bad = MemoryFact(kind="price", fact_key="菜心", value="6.0",   # kind 不在白名单
                     supplier_id="1", tenant_id=TENANT)
    monkeypatch.setattr(mx, "extract_candidate_facts",
                        lambda *a, **k: [bad])
    monkeypatch.setattr(mx, "compute_field_diffs", lambda ai, user: [{"row": 0}])
    _enable_switch()
    rid, sid = _mk_receipt()

    with caplog.at_level(logging.WARNING, logger="api_receipts"):
        stats = api_receipts._extract_and_enqueue_memory_facts(
            rid, db.get_receipt_row(rid), TENANT, AI_ITEMS, USER_ITEMS)

    assert stats["rejected"] == 1 and stats["enqueued"] == 0, stats
    assert _reviewable_rows() == [], "非法候选绝不能进人工闸"
    refused = _refused_rows()
    assert len(refused) == 1, refused
    assert "kind 非法" in refused[0]["reject_reason"], refused[0]["reject_reason"]
    assert any("写入侧闸门拒绝" in r.getMessage() for r in caplog.records), \
        [r.getMessage() for r in caplog.records]


# -------------------------------------------------------------
# 4. 失败可见且不阻断主流程
# -------------------------------------------------------------
def test_model_exception_keeps_main_flow_and_logs_error(client, monkeypatch, caplog):
    """抽取抛异常 → 保存仍成功；日志里有 error（带 receipt_id / 供应商 / 原因）。"""
    _patch_model(monkeypatch, exc=RuntimeError("引擎 500"))
    _enable_switch()
    rid, _ = _mk_receipt()

    # 自校准：先证明 caplog 采集通道对目标 logger 有效，否则"没有 error"可能是通道坏了
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="api_receipts"):
        logging.getLogger("api_receipts").error("K3-SELFCALIBRATION")
        assert any(r.getMessage() == "K3-SELFCALIBRATION" for r in caplog.records), \
            "caplog 采集通道失效，后续断言不可信"

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="api_receipts"):
        resp = _save(client, rid)
    assert resp.status_code == 200, "抽取失败绝不能阻断保存"
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "抽取失败必须留 error 级日志（不得静默）"
    assert any("receipt_id=%s" % rid in m for m in errors), errors
    assert any("引擎 500" in m for m in errors), errors
    assert _pending_rows() == []


# -------------------------------------------------------------
# 5. 开关：缺省关闭 = 零行为变化
# -------------------------------------------------------------
def test_switch_registered_and_defaults_off():
    """开关必须登记在 SETTINGS_DEFAULTS 且缺省 False（否则会在单测/离线环境发外呼）。

    登记解决的是「`get_bool` 取不到缺省值」这一件事，**不等于管理台可见**：可见性另需
    前端控件 + load / collect / reset 三处接线（P23-AQ1，`main.js:12822-12892` +
    `index.html` 锚点）。只登记不接线的后果是整条抽取链在生产上永不执行，而
    `settings_service.py` 里完全看不出这一点（K3 交付时就是这样）。
    """
    assert "memory_extract_enabled" in settings_service.SETTINGS_DEFAULTS, \
        "开关必须登记，否则 get_bool 取不到缺省值（登记本身不保证管理台可见）"
    assert settings_service.SETTINGS_DEFAULTS["memory_extract_enabled"] is False


def test_switch_off_does_not_call_model(client, monkeypatch):
    """开关关闭（缺省态）：即便有字段差异也不建模型、不调模型、不入队。"""
    stats = _patch_model(monkeypatch)
    _enable_switch("false")
    rid, _ = _mk_receipt()
    assert _save(client, rid).status_code == 200
    assert stats.state.build_calls == 0 and stats.model.calls == 0
    assert _pending_rows() == []
    # 空串等价于"未设置"→ 回落缺省 False（settings_service.get 的口径）
    _enable_switch("")
    assert api_receipts._memory_extract_enabled() is False


# -------------------------------------------------------------
# 6. ai_prefill 解析失败：留痕，且与「本单本来没修正」可区分
# -------------------------------------------------------------
def test_ai_prefill_parse_failure_warns_and_still_skips_extraction(client, monkeypatch, caplog):
    """ai_prefill_json 损坏 → 仍按「无字段差异」跳过抽取（行为不变），但必须留 warning。

    为什么单独立一条：`_ai_prefill_items` 返回的 [] 有**两种成因** —— 「本单确实没有人工
    修正」与「ai_prefill 脏了解析不了」—— 二者在运行记录里同形（`memory_extract_runs` 都
    是 diffs_count=0、都不建模型）。只有这条 warning 能把它们分开。

    判别力（反证）：去掉 `_ai_prefill_items` 里的 warning → 本用例的日志断言失败；
    若为了"留痕"改成抛异常或返回非空 → 保存状态码 / 不建模型 / 不入队的断言失败。
    """
    stats = _patch_model(monkeypatch)
    _enable_switch()
    rid, _ = _mk_receipt()
    # 造一条损坏的 ai_prefill_json（真实成因：历史上游写入被截断、或人工改库改坏）
    broken = '{"items": [{"name": "黑牛"'
    db.update_receipt(rid, ai_prefill_json=broken)

    # 自校准：先证明 caplog 对目标 logger 的 WARNING 级采集有效，否则"没有 warning"不可信
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="api_receipts"):
        logging.getLogger("api_receipts").warning("AQ2-SELFCALIBRATION")
        assert any(r.getMessage() == "AQ2-SELFCALIBRATION" for r in caplog.records), \
            "caplog 采集通道失效，后续断言不可信"

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="api_receipts"):
        resp = _save(client, rid)
    # (a) 解析失败不得抛、不得阻断保存；抽取照旧被跳过（行为与修复前逐字节一致）
    assert resp.status_code == 200, resp.text
    assert stats.state.build_calls == 0, "解析失败 → 无字段差异 → 不得建模型"
    assert stats.model.calls == 0
    assert _pending_rows() == [], "解析失败不得入队"
    # (b) 留痕：带 receipt_id 与原始值片段，文案点明「与『本单确实没修正』同形」+ 排查方向
    warns = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    hits = [m for m in warns if "ai_prefill 解析失败" in m]
    assert hits, warns
    assert any("receipt_id=%s" % rid in m for m in hits), hits
    assert any(broken[:200] in m for m in hits), hits
    assert any("同形" in m for m in hits), hits
    assert any("ai_prefill_json" in m for m in hits), hits


def test_ai_prefill_legit_empty_does_not_warn(client, monkeypatch, caplog):
    """对照组：合法的空 ai_prefill（本单确实没有 AI 值/没有修正）不得报成解析失败。

    没有这一条，"两种情形可区分"就只是断言而非被守护的性质 —— 把 warning 挪到解析成功
    分支（或无条件打）时，这条必须失败。
    """
    stats = _patch_model(monkeypatch)
    _enable_switch()
    rid, _ = _mk_receipt()
    db.update_receipt(rid, ai_prefill_json="{}")   # 合法空：json 合法、无 items

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="api_receipts"):
        resp = _save(client, rid)
    assert resp.status_code == 200, resp.text
    assert stats.state.build_calls == 0 and stats.model.calls == 0
    assert _pending_rows() == []
    assert not [r for r in caplog.records if "ai_prefill 解析失败" in r.getMessage()], \
        "合法空值不得被报成解析失败（否则两种情形又混在一起了）"
