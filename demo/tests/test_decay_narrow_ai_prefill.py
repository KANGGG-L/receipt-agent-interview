# -*- coding: utf-8 -*-
"""W-8：记忆衰减「扣分触发面」收窄 —— 只有「AI 预填过该字段 且 用户改成了别的值」才扣分。

旧实现（save_edited 的 `_manual_changed` 分支）只要 unit_price / amount / supplier_name /
total_amount 任一与**库中旧值**不同就对供应商记忆扣分，不区分「AI 预填错、用户纠正」与
「业务合理变更（补填空字段 / 供应商改名 / 价格正常波动）」，会把高频供应商的记忆误伤并
连带归档全部历史记忆。本文件的 (b)(c) 是判别式用例：把收窄逻辑去掉后它们必然翻红。

覆盖：
  a) AI 预填 unit_price=8 → 用户改 8.5          → 扣分（真 AI 纠错）
  b) AI 未预填（无 items）→ 用户填空字段 8        → 不扣分
  c) AI 未预填供应商名 → 用户改名（仅名称变化）    → 不扣分
  d) ai_prefill_json 缺失 / 损坏                → 不扣分 + 留 warning
另附「承重」用例：把判定 monkeypatch 回旧「old/new 全量 diff」逻辑 → (b)(c) 立即扣分。

全部离线假数据：独立临时 DB + 临时 RAG 目录，零外部调用。
"""

import json
import logging
import os
import sys
import tempfile
import uuid

_TMP = tempfile.mkdtemp(prefix="w8_decay_narrow_")
os.environ["DB_PATH"] = os.path.join(_TMP, "decay_narrow.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import pytest  # noqa: E402

STAFF = {"X-Role": "staff"}
INITIAL_DECAY = 0.7


def _client():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    return TestClient(fastapi_app)


def _vendor(tag):
    return f"W8收窄商戶_{tag}_{uuid.uuid4().hex[:6]}"


def _seed_memory(vendor):
    """直接造一条 active 供应商记忆（起始 decay_score=0.7），返回 memory_id。"""
    from app import db
    mid, _ = db.upsert_vendor_memory(vendor, "備註：常規採購", "sample",
                                     tenant_id="default")
    return mid


def _decay(vendor):
    from app import db
    rows = db.list_vendor_memory(vendor, tenant_id="default")
    assert rows, f"未找到 {vendor} 的 active 记忆行"
    return rows[0]["decay_score"]


def _mk_receipt(vendor, item_price=0.0, item_amount=0.0, prefill=None, total=0.0):
    """落一张 parsed 单据 + 一行明细 + 可选的 ai_prefill_json / total。"""
    from app import db
    rid = db.create_receipt(supplier_name=vendor, status="parsed", tenant_id="default")
    db.set_receipt_items(rid, [{
        "name": "菜心", "raw_name": "菜心", "quantity": 2, "unit": "斤",
        "raw_unit": "斤", "unit_price": item_price, "amount": item_amount,
        "sku_id": None, "cost_center_id": None, "confidence": 0.9,
        "matched": 0, "price_anomaly": 0, "price_anomaly_direction": "",
        "price_diff_percent": 0.0, "unit_conversion_warning": "",
        "fuzzy_candidates": [], "entity_candidates": [],
    }], tenant_id="default")
    db.update_receipt(rid, total_amount=total)
    if prefill is not None:
        raw = prefill if isinstance(prefill, str) else json.dumps(
            prefill, ensure_ascii=False)
        db.update_receipt(rid, ai_prefill_json=raw)
    return rid


def _save(client, rid, supplier, total, price, amount):
    return client.post("/api/save_edited", headers=STAFF, json={
        "receipt_id": rid, "supplier_name": supplier, "date": "2026-08-01",
        "total_amount": total, "settlement_type": "cash", "version": 1,
        "source": "manual",
        "items": [{"name": "菜心", "quantity": 2, "unit": "斤",
                   "unit_price": price, "amount": amount}],
    })


# -------------------------------------------------------------
# a) AI 预填 8 → 用户改 8.5 → 扣分
# -------------------------------------------------------------
def test_ai_prefilled_price_corrected_penalizes():
    from app import db
    vendor = _vendor("a")
    _seed_memory(vendor)
    before = _decay(vendor)
    prefill = {"vendor": vendor, "date": "2026-08-01", "total": 20.0,
               "items": [{"name": "菜心", "qty": 2, "unit": "斤",
                          "unit_price": 8, "amount": 16}]}
    rid = _mk_receipt(vendor, item_price=8.0, item_amount=16.0,
                      prefill=prefill, total=20.0)
    resp = _save(_client(), rid, vendor, total=20.0, price=8.5, amount=17.0)
    assert resp.status_code == 200, resp.text
    after = _decay(vendor)
    assert after < before, "AI 预填 8 被改成 8.5 属真 AI 纠错，必须扣分"
    assert abs(after - (before - 0.12)) < 1e-9


# -------------------------------------------------------------
# b) AI 未预填（无 items）→ 用户填空字段 → 不扣分
# -------------------------------------------------------------
def test_user_fills_blank_field_no_penalty():
    vendor = _vendor("b")
    _seed_memory(vendor)
    before = _decay(vendor)
    # AI 未预填任何明细（items 为空）；库中旧明细单价为 0（空字段）
    prefill = {"vendor": vendor, "date": "2026-08-01", "total": 0.0, "items": []}
    rid = _mk_receipt(vendor, item_price=0.0, item_amount=0.0,
                      prefill=prefill, total=0.0)
    resp = _save(_client(), rid, vendor, total=16.0, price=8.0, amount=16.0)
    assert resp.status_code == 200, resp.text
    after = _decay(vendor)
    assert after == before, "用户补填空字段不是 AI 纠错，不得扣分"


# -------------------------------------------------------------
# c) AI 未预填供应商名 → 用户改名（仅名称变化）→ 不扣分
# -------------------------------------------------------------
def test_supplier_rename_no_penalty():
    vendor = _vendor("c")
    _seed_memory(vendor)
    before = _decay(vendor)
    # AI 未预填供应商名（vendor 空）；明细与旧值一致，仅名称变化
    prefill = {"vendor": "", "date": "2026-08-01", "total": 20.0,
               "items": [{"name": "菜心", "qty": 2, "unit": "斤",
                          "unit_price": 10, "amount": 20}]}
    rid = _mk_receipt(vendor, item_price=10.0, item_amount=20.0,
                      prefill=prefill, total=20.0)
    renamed = vendor + "_改名"
    resp = _save(_client(), rid, renamed, total=20.0, price=10.0, amount=20.0)
    assert resp.status_code == 200, resp.text
    after = _decay(vendor)
    assert after == before, "供应商改名属业务合理变更，不得扣分"


# -------------------------------------------------------------
# d) ai_prefill_json 缺失 / 损坏 → 不扣分 + 有 warning
# -------------------------------------------------------------
def test_bad_ai_prefill_no_penalty_with_warning(caplog):
    vendor = _vendor("d")
    _seed_memory(vendor)
    before = _decay(vendor)
    # 损坏的 ai_prefill_json；同时提交与旧值不同的单价（旧逻辑会扣分）
    rid = _mk_receipt(vendor, item_price=8.0, item_amount=16.0,
                      prefill="{not-json", total=20.0)
    with caplog.at_level(logging.WARNING, logger="api_receipts"):
        resp = _save(_client(), rid, vendor, total=17.0, price=8.5, amount=17.0)
    assert resp.status_code == 200, resp.text
    after = _decay(vendor)
    assert after == before, "ai_prefill 坏/缺失时无法判定为 AI 错，不得扣分"
    assert any("ai_prefill 解析失败" in r.getMessage() for r in caplog.records), \
        "解析失败必须留 warning 痕迹（不能静默变成全量扣分）"


def test_missing_ai_prefill_no_penalty():
    vendor = _vendor("d2")
    _seed_memory(vendor)
    before = _decay(vendor)
    # 从未写过 ai_prefill_json（手工单形态）
    rid = _mk_receipt(vendor, item_price=8.0, item_amount=16.0, total=20.0)
    resp = _save(_client(), rid, vendor, total=17.0, price=8.5, amount=17.0)
    assert resp.status_code == 200, resp.text
    assert _decay(vendor) == before, "无 ai_prefill 时不得扣分"


# -------------------------------------------------------------
# 承重反证：回放到旧「old/new 全量 diff」逻辑 → (b)(c) 立即翻红
# -------------------------------------------------------------
def _wide_legacy_decision(row, items_raw, body):
    """旧逻辑等价物：任一字段与库中旧值不同即扣分（完全不读 ai_prefill_json）。"""
    from app import db
    old_items = db.get_receipt_items(row.id, tenant_id="default")
    for old_it, new_it in zip(old_items, items_raw):
        if old_it.get("unit_price") != new_it.get("unit_price"):
            return True
        if old_it.get("amount") != new_it.get("amount"):
            return True
    if row.supplier_name != body.supplier_name:
        return True
    if row.total_amount != body.total_amount:
        return True
    return False


@pytest.mark.parametrize("scenario", ["b", "c"])
def test_narrowing_is_load_bearing(monkeypatch, scenario):
    """把判定换成旧的宽口径 → b/c 场景确实会被误扣（证明收窄逻辑是承重的、用例非恒真）。"""
    from app import api_receipts
    monkeypatch.setattr(api_receipts, "_ai_prefill_corrected_fields",
                        _wide_legacy_decision)
    vendor = _vendor("mutant")
    _seed_memory(vendor)
    before = _decay(vendor)
    if scenario == "b":
        prefill = {"vendor": vendor, "date": "2026-08-01", "total": 0.0, "items": []}
        rid = _mk_receipt(vendor, item_price=0.0, item_amount=0.0,
                          prefill=prefill, total=0.0)
        resp = _save(_client(), rid, vendor, total=16.0, price=8.0, amount=16.0)
    else:
        prefill = {"vendor": "", "date": "2026-08-01", "total": 20.0,
                   "items": [{"name": "菜心", "qty": 2, "unit": "斤",
                              "unit_price": 10, "amount": 20}]}
        rid = _mk_receipt(vendor, item_price=10.0, item_amount=20.0,
                          prefill=prefill, total=20.0)
        resp = _save(_client(), rid, vendor + "_改名", total=20.0,
                     price=10.0, amount=20.0)
    assert resp.status_code == 200, resp.text
    assert _decay(vendor) < before, \
        f"场景 {scenario} 在旧宽口径下必须被误扣（若此处不扣，说明用例未真正判别收窄）"
