# -*- coding: utf-8 -*-
"""VendorMemory × SKU 联动 L0+L1 判别式测试（SPEC_L0L1 §5 验收判据）。

L1：人工批准 alias 事实 → 供应商作用域别名表登记（rag.approve_pending_to_memory
结构化分支成功后的钩子，人工闸之后的确定性复利面）。
L0：供应商锚点回退链 —— 解析期（save_parsed_data）追加 vendor_hint 档 + 记忆先验档；
入账期（apply_receipt_to_inventory）追加记忆先验档（db.resolve_supplier_id_from_memory，
唯一实现：精确名 + active + 同租户 + 唯一候选才锚定，多/零候选不猜）。

覆盖（每条都可"回滚实现即失败"）：
- A1：批准唯一 SKU 的 alias 事实 → (tenant, supplier, raw) 别名行 + source_ref=memory:<mid>
  + `_match_sku` 第一跳命中 + 第二单复测 hit_count 递增 + prompt 注入通道不回退；
- A2：≥2 同名 SKU 不登记（SKU_AMBIGUOUS）/ 0 SKU 不登记（SKU_NOT_FOUND）/
  死锚点不登记（SUPPLIER_NOT_FOUND）/ 无锚点回落自由文本（钩子不可达）——均留审计；
- A3：非 alias 事实（unit/layout/price_range/自由文本）批准后别名表零变化、零审计噪音；
- A4：L0 锚点回退两态 —— 记忆先验登记成功（正向 spy）/ 多候选不锚定 / 零候选不锚定 /
  已锚定逐字节不变（反向 spy）/ parse 期 hint 档 / parse 期记忆先验档（含多候选不命中）；
- A5：人工闸不绕过 —— grep 文件级恰 3 文件；save_edited 与抽取入队只落 pending、
  别名表零写入；reject 不写库（既有语义，本文件不重复构造）；
- 幂等：同键重复批准不增行、hit_count 保留；同 fact_key 换 content（supersede、新 mid）
  别名层结论不变。

全部离线假数据：模块级临时 DB + 临时 RAG 目录，零外部调用、零 live 库/向量库写入。
"""

import importlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="memory_alias_l0l1_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_l0l1.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_REPO_ROOT = os.path.abspath(os.path.join(_DEMO_DIR, ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import pytest  # noqa: E402

import app.db as db  # noqa: E402
import app.services.rag as rag  # noqa: E402
import app.services.receipt_utils as receipt_utils  # noqa: E402
from app.services.inventory import apply_receipt_to_inventory  # noqa: E402
from app.services.memory_facts import MemoryFact  # noqa: E402
from app.services.receipt_utils import _match_sku  # noqa: E402
from app.services.supplier_normalizer import canonical_supplier_key  # noqa: E402

rag.PERSIST_DIR = os.environ["RAG_DIR"]
rag._vs = None
rag._vs_tenant = {}

TENANT = "vm-l0l1"
WHO = "qa@x"


def _fresh_db():
    """每个用例独立数据库文件，避免跨用例串扰（与 P2 系列用例同款）。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"l0l1_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    rag.PERSIST_DIR = os.environ["RAG_DIR"]
    rag._vs = None
    rag._vs_tenant = {}
    return db


# -------------------------------------------------------------
# 夹具
# -------------------------------------------------------------
def _mk_supplier(name, tenant_id=TENANT):
    sid, err = db.create_supplier(name, tenant_id=tenant_id)
    assert err is None, f"建供应商档案失败: {err}"
    return int(sid)


def _mk_sku(name, base_unit="", tenant_id=TENANT):
    sid, err = db.create_sku(name, base_unit=base_unit, tenant_id=tenant_id)
    assert err is None, f"建 SKU 失败: {err} ({name})"
    return int(sid)


def _item(name, unit="斤", unit_price=10.0, amount=10.0):
    """构造与 `save_parsed_data` 落库同形的明细行（_match_sku 只读其中几个键）。

    必须带 `unit_price` 键：`_match_sku` 命中且 sku.last_unit_price 非空时读
    item["unit_price"]，缺键会 KeyError。
    """
    return {
        "name": name, "raw_name": name, "quantity": 1, "unit": unit,
        "unit_price": unit_price, "amount": amount, "sku_id": None,
        "cost_center_id": None, "confidence": 0.9, "matched": 0,
        "price_anomaly": 0, "price_anomaly_direction": "", "price_diff_percent": 0.0,
        "unit_conversion_warning": "", "fuzzy_candidates": [], "entity_candidates": [],
    }


def _mk_receipt(supplier_name, items, tenant_id=TENANT, supplier_id=None,
                status="parsed"):
    rid = db.create_receipt_record(
        supplier_name=supplier_name, supplier_id=supplier_id,
        tenant_id=tenant_id, status=status)
    db.set_receipt_items(rid, items)
    return rid


def _alias_rows(tenant_id=None, supplier_id=None, raw_name=None):
    s = db.get_session()
    try:
        q = s.query(db._VendorSkuAliasRow)
        if tenant_id is not None:
            q = q.filter(db._VendorSkuAliasRow.tenant_id == tenant_id)
        if supplier_id is not None:
            q = q.filter(db._VendorSkuAliasRow.supplier_id == int(supplier_id))
        if raw_name is not None:
            q = q.filter(db._VendorSkuAliasRow.raw_name == raw_name)
        return q.all()
    finally:
        s.close()


def _one(stmt, params=()):
    conn = sqlite3.connect(db.DB_PATH)
    try:
        row = conn.execute(stmt, params).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _exec(stmt, params=()):
    conn = sqlite3.connect(db.DB_PATH)
    try:
        conn.execute(stmt, params)
        conn.commit()
    finally:
        conn.close()


def _audit_rows():
    """app_settings.system_audit_json（系统审计的持久化查询面）。"""
    raw = db.get_app_setting("system_audit_json") or "[]"
    try:
        rows = json.loads(raw)
    except Exception:
        return []
    return rows if isinstance(rows, list) else []


def _alias_audit_rows():
    return [r for r in _audit_rows()
            if str(r.get("action", "")).startswith("memory_alias_")]


def _memory_alias_rows(vendor, tenant_id=TENANT):
    """按 vendor 名精确取 active 记忆行（治理视图，dict 列表）。"""
    return [r for r in db.list_vendor_memory(vendor=vendor, tenant_id=tenant_id)
            if str(r.get("fact_kind") or "") == "alias"]


def _insert_dup_active_sku(name, tenant_id=TENANT):
    """直接向 skus 表插两行同名 active SKU（create_sku 同租户重名会返回
    SKU_NAME_CONFLICT，公开路径构造不出多候选歧义）。"""
    s = db.get_session()
    try:
        s.add(db._SkuRow(name=name, tenant_id=tenant_id, sku_code="SKU-DUP-A",
                         active=1))
        s.add(db._SkuRow(name=name, tenant_id=tenant_id, sku_code="SKU-DUP-B",
                         active=1))
        s.commit()
    finally:
        s.close()
    dup = [x for x in db.list_skus(tenant_id=tenant_id) if x.name == name]
    assert len(dup) == 2, f"夹具前提失败：同名 active SKU 应有 2 个，实得 {len(dup)}"
    return [int(x.id) for x in dup]


def _spy_memory_prior(monkeypatch):
    """正向 spy：记录记忆先验档的调用与返回（包真实实现，不改变行为）。"""
    calls = []
    real = db.resolve_supplier_id_from_memory

    def _spy(vendor_name, tenant_id=None):
        got = real(vendor_name, tenant_id=tenant_id)
        calls.append({"vendor": vendor_name, "tenant_id": tenant_id, "returned": got})
        return got

    monkeypatch.setattr(db, "resolve_supplier_id_from_memory", _spy)
    return calls


def _approve_alias(vendor, raw, value, supplier_id, tenant_id=TENANT, content="别名先验"):
    """走真实 approve 链批准一条 alias 事实（人工闸），返回返回值。"""
    return rag.approve_pending_to_memory(
        vendor=vendor, content=content, tenant_id=tenant_id,
        supplier_id=supplier_id, fact_kind="alias",
        fact_key="alias:%s" % raw, fact_value=value,
        source_kind="extract", written_by=WHO)


def _save_memory_alias(vendor, raw, value, supplier_id, tenant_id=TENANT,
                       fact_key=None, notes="先验构造"):
    """直接经 db.save_memory_fact 落一条带锚点的 alias 记忆行（构造记忆先验）。

    注意：save_memory_fact 的内容去重键是 (vendor, tenant, active, notes, sample) ——
    同 vendor 构造多行候选时必须用不同 notes，否则第二次调用会归并进既有行。
    """
    key = fact_key or ("alias:%s" % raw)
    fact = MemoryFact(kind="alias", fact_key=key, value=value,
                      supplier_id=str(supplier_id), tenant_id=tenant_id,
                      source_kind="extract")
    return db.save_memory_fact(tenant_id, vendor, key, notes=notes,
                               sample="", source_kind="extract", fact=fact)


# -------------------------------------------------------------
# A1：批准唯一 SKU 的 alias 事实 → 作用域行 + 匹配命中 + 第二单复测
# -------------------------------------------------------------
def test_a1_alias_approval_registers_scoped_alias_and_match_hits(monkeypatch):
    _fresh_db()
    sup = _mk_supplier("供應商甲")
    sku = _mk_sku("江團魚")
    before_audit = len(_alias_audit_rows())

    ret = _approve_alias("新協興", "江鯽魚", "江團魚", sup)
    assert isinstance(ret, str) and ret, "approve 返回值契约不变（content 字符串）"

    # 断言 1：别名表出现供应商作用域行（非全局），sku_id 指向唯一 SKU，
    # source_ref 可回溯到批准的 vendor_memory 行。
    rows = _alias_rows(TENANT, sup, "江鯽魚")
    assert len(rows) == 1, "批准 alias 事实必须登记恰一条作用域别名行"
    assert rows[0].sku_id == sku
    hit_count_after_register = int(rows[0].hit_count or 0)
    mem_rows = _memory_alias_rows("新協興")
    assert len(mem_rows) == 1
    mid = mem_rows[0]["memory_id"]
    assert rows[0].source_ref == "memory:%s" % mid

    # 登记成功留系统审计（who = written_by 透传值）。
    audit = [r for r in _alias_audit_rows() if r["action"] == "memory_alias_registered"]
    assert len(audit) == 1 and audit[0]["who"] == WHO
    payload = json.loads(audit[0]["new"])
    assert payload["tenant"] == TENANT and payload["supplier_id"] == sup
    assert payload["raw_name"] == "江鯽魚" and payload["sku_id"] == sku
    assert payload["memory_id"] == mid
    assert len(_alias_audit_rows()) == before_audit + 1, "无多余审计噪音"

    # 断言 2（行为级）：别名第一跳命中（写法与标准名非子串关系 —— 全局通道命中不了，
    # 关掉别名登记/锚点收窄本用例即失败）。命中本身计一次 hit_count。
    probe = {"name": "江鯽魚", "sku_id": None, "matched": 0, "unit_price": None}
    _match_sku(probe, tenant_id=TENANT, supplier_id=sup)
    assert probe["sku_id"] == sku and probe["matched"] == 1
    hit_count_after_first = int(
        _alias_rows(TENANT, sup, "江鯽魚")[0].hit_count or 0)
    assert hit_count_after_first == hit_count_after_register + 1

    # 断言 3（复测同写法第二单）：仍命中且 hit_count 递增 1。
    probe2 = {"name": "江鯽魚", "sku_id": None, "matched": 0, "unit_price": None}
    _match_sku(probe2, tenant_id=TENANT, supplier_id=sup)
    assert probe2["sku_id"] == sku
    assert int(_alias_rows(TENANT, sup, "江鯽魚")[0].hit_count or 0) \
        == hit_count_after_first + 1

    # 断言 4（prompt 注入不回退）：既有记忆渲染通道不受别名登记影响。
    line = rag._render_fact_row(mem_rows[0])
    assert line == "别名：江鯽魚 → 江團魚"


# -------------------------------------------------------------
# A2：多/零 SKU 与死锚点不登记 + 审计；无锚点回落自由文本（钩子不可达）
# -------------------------------------------------------------
def test_a2_ambiguous_sku_not_registered_with_audit(monkeypatch):
    _fresh_db()
    sup = _mk_supplier("供應商乙")
    dup_ids = _insert_dup_active_sku("菜心")
    before = len(_alias_rows(TENANT))
    before_audit = len(_alias_audit_rows())

    _approve_alias("新協興", "菜心苗", "菜心", sup)

    assert len(_alias_rows(TENANT)) == before, "歧义 SKU 绝不得登记（.first() 是猜）"
    skips = [r for r in _alias_audit_rows() if r["action"] == "memory_alias_skip"]
    assert len(skips) == before_audit + 1
    payload = json.loads(skips[-1]["new"])
    assert payload["reason"] == "SKU_AMBIGUOUS"
    assert payload["supplier_id"] == sup and payload["raw_name"] == "菜心苗"
    assert payload["candidates"] == 2
    assert sorted(dup_ids) == sorted(
        [int(x.id) for x in db.list_skus(tenant_id=TENANT) if x.name == "菜心"]), \
        "歧义路径不得新建/改动 SKU"


def test_a2_missing_sku_not_registered_with_audit(monkeypatch):
    _fresh_db()
    sup = _mk_supplier("供應商丙")
    before = len(_alias_rows(TENANT))
    before_audit = len(_alias_audit_rows())

    _approve_alias("新協興", "不存在的魚", "不存在的品名", sup)

    assert len(_alias_rows(TENANT)) == before, "0 SKU 时不得登记"
    skips = [r for r in _alias_audit_rows() if r["action"] == "memory_alias_skip"]
    assert len(skips) == before_audit + 1
    assert json.loads(skips[-1]["new"])["reason"] == "SKU_NOT_FOUND"


def test_a2_dead_supplier_anchor_not_registered_with_audit(monkeypatch):
    _fresh_db()
    before = len(_alias_rows(TENANT))
    before_audit = len(_alias_audit_rows())

    # 锚点指向不存在的档案（upsert_vendor_sku_alias 只校验 SKU，不校验 supplier_id
    # —— 4b 门必须在钩子内显式拦下，否则落出死锚点别名行且零留痕）。
    _approve_alias("新協興", "菜心苗", "本地菜心", 999999)

    assert len(_alias_rows(TENANT)) == before, "死锚点不得落别名行"
    skips = [r for r in _alias_audit_rows() if r["action"] == "memory_alias_skip"]
    assert len(skips) == before_audit + 1
    assert json.loads(skips[-1]["new"])["reason"] == "SUPPLIER_NOT_FOUND"


def test_a2_no_anchor_falls_back_to_free_text_hook_unreachable(monkeypatch):
    """supplier_id=None 时 validate_fact 硬拒 → 回落自由文本分支，钩子到不了。

    断言口径与 A2 skip 审计不同：这里永远产生不出 memory_alias_* 审计行。
    """
    _fresh_db()
    _mk_supplier("供應商丁")
    _mk_sku("本地菜心")
    before_alias = len(_alias_rows(TENANT))
    before_audit = len(_alias_audit_rows())

    _approve_alias("新協興", "菜心苗", "本地菜心", None)

    rows = db.list_vendor_memory(vendor="新協興", tenant_id=TENANT,
                                 include_archived=True)
    assert len(rows) == 1, "自由文本分支照常落 vendor_memory 行"
    assert not (rows[0].get("fact_kind") or ""), "fact_kind 列必须为空（自由文本行）"
    assert len(_alias_rows(TENANT)) == before_alias, "别名表零变化"
    assert len(_alias_audit_rows()) == before_audit, "无 memory_alias_* 审计行"


# -------------------------------------------------------------
# A3：非 alias 事实零影响
# -------------------------------------------------------------
def test_a3_non_alias_facts_leave_alias_table_untouched(monkeypatch):
    _fresh_db()
    sup = _mk_supplier("供應商戊")
    _mk_sku("本地菜心")
    before_alias = len(_alias_rows(TENANT))
    before_audit = len(_alias_audit_rows())

    # unit / layout / price_range 各一条（都过 validate_fact，落结构化行）+ 自由文本一条。
    rag.approve_pending_to_memory(vendor="新協興", content="单位习惯", tenant_id=TENANT,
                                  supplier_id=sup, fact_kind="unit",
                                  fact_key="unit:菜心", fact_value="斤",
                                  source_kind="extract", written_by=WHO)
    rag.approve_pending_to_memory(vendor="新協興", content="版式先验", tenant_id=TENANT,
                                  supplier_id=sup, fact_kind="layout",
                                  fact_key="layout:doc_form", fact_value="手寫長單",
                                  source_kind="extract", written_by=WHO)
    rag.approve_pending_to_memory(vendor="新協興", content="价格区间", tenant_id=TENANT,
                                  supplier_id=sup, fact_kind="price_range",
                                  fact_key="price_range:菜心", fact_value="8.0-10.0",
                                  source_kind="extract", written_by=WHO)
    rag.approve_pending_to_memory(vendor="新協興", content="这家常送新鲜菜",
                                  tenant_id=TENANT, supplier_id=sup,
                                  source_kind="feedback_distilled", written_by=WHO)

    assert len(_alias_rows(TENANT)) == before_alias, "非 alias 事实不得登记别名"
    assert len(_alias_audit_rows()) == before_audit, "非 alias 事实零审计噪音"
    # 结构化行照常落 vendor_memory（unit/layout/price_range 三行 + 自由文本一行）。
    mems = db.list_vendor_memory(vendor="新協興", tenant_id=TENANT)
    assert len(mems) == 4


# -------------------------------------------------------------
# A4：L0 锚点回退
# -------------------------------------------------------------
def test_a4a_memory_prior_anchors_inventory_and_registers_alias(monkeypatch):
    """a：单据未锚定 + 记忆先验唯一候选 → 入账经记忆先验锚定并登记别名。

    夹具约束：供应商档案名 ≠ vendor 名，且 canonical key 不碰撞 —— 否则现状
    ② 档（resolve_supplier_id 按名解析）直接命中，记忆先验档根本不执行。
    """
    _fresh_db()
    sup = _mk_supplier("供應商己")
    assert sup != "新協興" and canonical_supplier_key("供應商己") \
        != canonical_supplier_key("新協興"), "夹具前提：档案名与 vendor 名不得碰撞"

    _save_memory_alias("新協興", "写法甲", "本地牛腩", sup)
    calls = _spy_memory_prior(monkeypatch)

    rid = _mk_receipt("新協興", [_item("写法甲")], supplier_id=None)
    apply_receipt_to_inventory(db.get_receipt_row(rid), tenant_id=TENANT)

    assert len(calls) == 1 and calls[0]["vendor"] == "新協興" \
        and calls[0]["tenant_id"] == TENANT, "记忆先验档必须被调用（正向 spy）"
    assert calls[0]["returned"] == sup
    rows = _alias_rows(TENANT, sup, "写法甲")
    assert len(rows) == 1, "原 NO_SUPPLIER 路径经记忆先验回退后应登记别名"
    assert rows[0].source_ref == "receipt:%s" % rid


def test_a4b_memory_prior_multi_candidates_not_anchored(monkeypatch):
    """b：同 vendor 名的两个 active 记忆行锚不同档案 → 不猜，保持 NO_SUPPLIER。

    夹具口径：两行必须用不同 fact_key —— 同 vendor 同 fact_key 的第二次写入会
    supersede 旧行，active 只剩一行，测不出多候选拒绝。
    """
    _fresh_db()
    sup1 = _mk_supplier("供應商庚")
    sup2 = _mk_supplier("供應商辛")
    _save_memory_alias("新協興", "写法甲", "本地牛腩", sup1,
                       fact_key="alias:写法甲", notes="先验甲")
    _save_memory_alias("新協興", "写法乙", "本地菜心", sup2,
                       fact_key="alias:写法乙", notes="先验乙")
    # 夹具前提：两行 active 记忆并存（未被内容去重归并/supersede）。
    assert len(db.list_vendor_memory(vendor="新協興", tenant_id=TENANT)) == 2
    calls = _spy_memory_prior(monkeypatch)

    rid = _mk_receipt("新協興", [_item("奇異果")], supplier_id=None)
    apply_receipt_to_inventory(db.get_receipt_row(rid), tenant_id=TENANT)

    assert len(calls) == 1 and calls[0]["returned"] is None, "多候选必须返回 None"
    assert not _alias_rows(TENANT, sup1) and not _alias_rows(TENANT, sup2), \
        "多候选时不得登记任何供应商的别名（保持 NO_SUPPLIER 跳过）"


def test_a4c_memory_prior_zero_candidates_not_anchored(monkeypatch):
    _fresh_db()
    sup = _mk_supplier("供應商壬")
    calls = _spy_memory_prior(monkeypatch)

    rid = _mk_receipt("從未見過的商號", [_item("奇異果")], supplier_id=None)
    apply_receipt_to_inventory(db.get_receipt_row(rid), tenant_id=TENANT)

    assert len(calls) == 1 and calls[0]["returned"] is None, "零候选返回 None"
    assert not _alias_rows(TENANT, sup), "零候选不登记别名"


def test_memory_prior_requires_tenant_and_active_rows():
    """租户门 + active 门：tenant 为空直接 None；archived/superseded 行不提供锚点。"""
    _fresh_db()
    sup = _mk_supplier("供應商癸")
    _save_memory_alias("新協興", "写法甲", "本地牛腩", sup)
    assert db.resolve_supplier_id_from_memory("新協興", tenant_id=TENANT) == sup

    # 租户门：tenant_id 为空 → 直接 None（宁可不锚定，不跨租户全量扫描）。
    assert db.resolve_supplier_id_from_memory("新協興", tenant_id=None) is None
    assert db.resolve_supplier_id_from_memory("新協興", tenant_id="  ") is None

    # archived 行不提供锚点。
    mid = _memory_alias_rows("新協興")[0]["memory_id"]
    _exec("UPDATE vendor_memory SET status='archived' WHERE memory_id=?", (mid,))
    assert db.resolve_supplier_id_from_memory("新協興", tenant_id=TENANT) is None


def test_a4d_already_anchored_receipt_byte_identical_no_extra_tier(monkeypatch):
    """d：行上已锚定 → 记忆先验档不执行，apply 全部副作用与现状一致。"""
    _fresh_db()
    sup = _mk_supplier("供應商子")
    sku = _mk_sku("本地牛腩")
    calls = _spy_memory_prior(monkeypatch)

    rid = _mk_receipt("供應商子", [_item("本地牛腩")], supplier_id=sup)
    apply_receipt_to_inventory(db.get_receipt_row(rid), tenant_id=TENANT)

    assert calls == [], "已锚定单据不得触发记忆先验档（不越档）"
    # 现状语义基线：别名登记 / sku_id 回写 / 库存流水全部照旧。
    rows = _alias_rows(TENANT, sup, "本地牛腩")
    assert len(rows) == 1 and rows[0].sku_id == sku
    items = db.get_receipt_items(rid)
    assert items[0]["sku_id"] == sku and items[0]["matched"] == 1
    assert int(_one("SELECT COUNT(*) FROM inventory_log WHERE receipt_id=?",
                    (rid,)) or 0) == 1


def test_a4e_parse_hint_tier_scopes_match(monkeypatch):
    """e：parse 期 vendor_hint 档 —— hint 可解析时 _match_sku 以其收窄（别名命中）。"""
    _fresh_db()
    sup = _mk_supplier("供應商丑")
    sku_x = _mk_sku("江團魚")
    assert canonical_supplier_key("供應商丑") \
        != canonical_supplier_key("從未見過的商號"), "夹具前提：② 档不得按 vendor 名命中"
    assert db.upsert_vendor_sku_alias(
        sup, sku_x, "江鯽魚", canonical_name="江鯽魚", unit="斤",
        tenant_id=TENANT)[1] is None

    from app.models import ReceiptData
    rid = db.create_receipt(supplier_name="從未見過的商號", status="uploaded",
                            tenant_id=TENANT)
    data = ReceiptData(doc_form="printed_delivery_note", vendor="從未見過的商號",
                       date="2026-08-01",
                       items=[{"name": "江鯽魚", "qty": 1.0, "unit": "斤",
                               "unit_price": 10.0, "amount": 10.0}],
                       total=10.0, payment_marked=False, confidence=0.9)
    result = {"raw": "{}", "vendor_context": "", "status": "parsed",
              "vendor_hint": "供應商丑"}
    receipt_utils.save_parsed_data(rid, data, result)

    items = db.get_receipt_items(rid)
    assert items[0]["sku_id"] == sku_x, "hint 档获锚后别名第一跳应命中 skuX"
    assert items[0]["matched"] == 1
    # 锚点持久化语义不变：receipts.supplier_id 仍由 D17 按识别 vendor 名解析（此处 None）。
    assert db.get_receipt_row(rid).supplier_id is None


def test_a4f_parse_memory_prior_tier(monkeypatch):
    """f：parse 期记忆先验档 —— vendor 空提示 + 记忆唯一锚点 → 别名通道命中；
    多候选时不命中（不猜）。"""
    _fresh_db()
    sup = _mk_supplier("供應商寅")
    sku_x = _mk_sku("江團魚")
    assert canonical_supplier_key("供應商寅") \
        != canonical_supplier_key("新協興"), "夹具前提：② 档不得按 vendor 名命中"
    assert db.upsert_vendor_sku_alias(
        sup, sku_x, "江鯽魚", canonical_name="江鯽魚", unit="斤",
        tenant_id=TENANT)[1] is None
    _save_memory_alias("新協興", "写法甲", "江團魚", sup, fact_key="alias:写法甲")

    from app.models import ReceiptData
    rid = db.create_receipt(supplier_name="新協興", status="uploaded",
                            tenant_id=TENANT)
    data = ReceiptData(doc_form="printed_delivery_note", vendor="新協興",
                       date="2026-08-01",
                       items=[{"name": "江鯽魚", "qty": 1.0, "unit": "斤",
                               "unit_price": 10.0, "amount": 10.0}],
                       total=10.0, payment_marked=False, confidence=0.9)
    result = {"raw": "{}", "vendor_context": "", "status": "parsed"}  # 无 vendor_hint
    receipt_utils.save_parsed_data(rid, data, result)
    items = db.get_receipt_items(rid)
    assert items[0]["sku_id"] == sku_x, "记忆先验档获锚后别名第一跳应命中 skuX"

    # 多候选不命中：同 vendor 名两个 active 行锚两个不同档案（不同 fact_key）。
    sup2 = _mk_supplier("供應商卯")
    _save_memory_alias("新協興", "写法乙", "本地菜心", sup2,
                       fact_key="alias:写法乙", notes="先验乙")
    # 夹具前提：两行 active 记忆并存（多候选）。
    assert len(db.list_vendor_memory(vendor="新協興", tenant_id=TENANT)) == 2
    rid2 = db.create_receipt(supplier_name="新協興", status="uploaded",
                             tenant_id=TENANT)
    receipt_utils.save_parsed_data(rid2, data, result)
    items2 = db.get_receipt_items(rid2)
    assert items2[0]["sku_id"] is None and items2[0]["matched"] == 0, \
        "记忆先验多候选时不锚定、不命中（不猜）"


# -------------------------------------------------------------
# 幂等（§3.2-7/8）
# -------------------------------------------------------------
def test_alias_reapproval_idempotent_hit_count_preserved(monkeypatch):
    """同键重复批准：别名行不增、指向稳定、hit_count 保留。

    两种形态：content 相同 → 归并既有行（mid 不变）；同 fact_key 不同 content →
    supersede 产生新 mid。两种形态下别名层结论必须相同（不断言 mid 关系）。
    """
    _fresh_db()
    sup = _mk_supplier("供應商辰")
    sku = _mk_sku("江團魚")

    _approve_alias("新協興", "江鯽魚", "江團魚", sup, content="别名先验")
    rows = _alias_rows(TENANT, sup, "江鯽魚")
    assert len(rows) == 1
    # 预置 hit_count=5：upsert 幂等再命中时不得清零（db.upsert 同 SKU 只刷新
    # canonical/unit，hit_count 保留）。
    _exec("UPDATE vendor_sku_alias SET hit_count=5 WHERE id=?", (rows[0].id,))

    # 形态一：content 相同（幂等去重命中、复用既有行）。
    _approve_alias("新協興", "江鯽魚", "江團魚", sup, content="别名先验")
    rows2 = _alias_rows(TENANT, sup, "江鯽魚")
    assert len(rows2) == 1 and rows2[0].sku_id == sku, "重复批准不得新增行/改指"
    assert (rows2[0].hit_count or 0) == 5, "幂等再登记必须保留 hit_count"

    # 形态二：同 fact_key 不同 content（supersede 产生新 mid）。
    _approve_alias("新協興", "江鯽魚", "江團魚", sup, content="别名先验（补充样例）")
    rows3 = _alias_rows(TENANT, sup, "江鯽魚")
    assert len(rows3) == 1 and rows3[0].sku_id == sku
    assert (rows3[0].hit_count or 0) == 5


def test_alias_reapproval_different_sku_repoints(monkeypatch):
    """同供应商同写法、不同标准品名的两次批准：第二次改指（人工后到覆盖）。"""
    _fresh_db()
    sup = _mk_supplier("供應商巳")
    sku_a = _mk_sku("本地菜心")
    sku_b = _mk_sku("本地白菜")

    _approve_alias("新協興", "青菜", "本地菜心", sup)
    rows = _alias_rows(TENANT, sup, "青菜")
    assert len(rows) == 1 and rows[0].sku_id == sku_a

    _approve_alias("新協興", "青菜", "本地白菜", sup)
    rows2 = _alias_rows(TENANT, sup, "青菜")
    assert len(rows2) == 1 and rows2[0].sku_id == sku_b, "后批准的纠正应改指 B"
    probe = {"name": "青菜", "sku_id": None, "matched": 0, "unit_price": None}
    _match_sku(probe, tenant_id=TENANT, supplier_id=sup)
    assert probe["sku_id"] == sku_b


# -------------------------------------------------------------
# A5：人工闸不绕过
# -------------------------------------------------------------
def test_a5_grep_guard_only_three_files_touch_upsert():
    """守卫断言（文件级）：生产调用点恰为 db.py（定义）/ inventory.py（W1）/
    rag.py（本钩子）；save_edited 所在的 api_receipts.py 零引用。"""
    out = subprocess.run(
        ["grep", "-rln", "upsert_vendor_sku_alias", "demo/app", "--include=*.py"],
        cwd=_REPO_ROOT, capture_output=True, text=True, check=True)
    files = {line.strip() for line in out.stdout.splitlines() if line.strip()}
    assert files == {"demo/app/db.py", "demo/app/services/inventory.py",
                     "demo/app/services/rag.py"}, files
    out2 = subprocess.run(
        ["grep", "-c", "upsert_vendor_sku_alias", "demo/app/api_receipts.py"],
        cwd=_REPO_ROOT, capture_output=True, text=True)
    assert out2.stdout.strip() == "0", "save_edited 路径不得出现别名写入"


def test_a5_save_edited_and_extraction_do_not_write_alias(monkeypatch):
    """save_edited（改品名）与抽取入队：别名表零写入；alias 候选只入 pending。"""
    import app.api_receipts as api_receipts
    import app.services.memory_extract as mx

    _fresh_db()

    class _Msg:
        def __init__(self, content):
            self.content = content

    # 生产链路的 canary 握手是 fail-closed 的：stub 必须按锚定指令把 __guard_token
    # 回填为「数组末尾握手对象」，否则合规输出会被误判成协议错配（fail-closed 拒绝）。
    canary_re = re.compile(r'"__guard_token"\s*:\s*"([^"]+)"')

    class _StubModel:
        def invoke(self, messages):
            prompt = "\n".join(
                getattr(m, "content", "") or "" for m in (messages or []))
            matched = canary_re.search(prompt)
            data = [{"kind": "alias", "fact_key": "黑牛",
                     "value": "黑牛牌黑胡椒牛油", "confidence": 0.7}]
            if matched:
                data.append({"__guard_token": matched.group(1)})
            return _Msg(json.dumps(data, ensure_ascii=False))

    monkeypatch.setattr(mx, "_build_default_model",
                        lambda cfg=None, **_kw: _StubModel())
    db.set_app_setting(api_receipts.MEMORY_EXTRACT_SETTINGS_KEY, "true")

    sup = _mk_supplier("祥興食品")
    _mk_sku("黑牛牌黑胡椒牛油")
    ai_items = [{"name": "黑牛", "qty": 2, "unit": "斤", "unit_price": 8.0,
                 "amount": 16.0}]
    rid = db.create_receipt(supplier_name="祥興食品", status="edited",
                            tenant_id=TENANT)
    db.update_receipt(
        rid, receipt_date="2026-08-01", total_amount=16.0, supplier_id=sup,
        ai_prefill_json=json.dumps(
            {"vendor": "祥興食品", "date": "2026-08-01", "total": 16.0,
             "items": ai_items}, ensure_ascii=False))
    assert len(_alias_rows(TENANT)) == 0
    before_alias = len(_alias_rows(TENANT))
    before_audit = len(_alias_audit_rows())

    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        body = {
            "supplier_name": "祥興食品", "date": "2026-08-01",
            "total_amount": 16.0, "settlement_type": "cash",
            "items": [{"name": "黑牛牌黑胡椒牛油", "quantity": 2.0, "unit": "斤",
                       "unit_price": 8.0, "amount": 16.0}],
            "version": db.get_receipt_row(rid).version,
            "source": "manual", "receipt_id": rid,
        }
        resp = client.post("/api/save_edited",
                           headers={"X-Role": "staff", "X-Tenant-Id": TENANT},
                           json=body)
        assert resp.status_code == 200, resp.text

        # alias 候选只入 pending（可被人工批准），别名表零变化、零 memory_alias_* 审计。
        pending = db.list_pending_memory(status="pending", tenant_id=TENANT)
        alias_pending = [r for r in pending
                         if str(r.get("fact_kind") or "") == "alias"]
        assert len(alias_pending) == 1, pending
        assert alias_pending[0]["fact_key"] == "黑牛"
        assert len(_alias_rows(TENANT)) == before_alias, \
            "save_edited/抽取路径不得写别名表（人工闸不绕过）"
        assert len(_alias_audit_rows()) == before_audit
