# -*- coding: utf-8 -*-
"""T7（Gap E1）字段级证据完整落地测试 —— 全部离线假数据，零外部调用。

覆盖（对照计划 Step 1 与验收口径）：
1. 契约层：Evidence 默认值；ReceiptItem.evidence Optional + 默认 None；
   extra=forbid 语义不变（模型不输出 evidence 合法；schema 外字段仍拒绝）
2. 解析层：模型返回 evidence 时正确解析；缺失时 evidence=None 且不阻断主链路；
   bbox 非法/越界/非数字一律置 None 不阻断
3. 落库层：receipt_items.evidence_json 幂等迁移 + 写读回环
4. 端到端：save_parsed_data → build_detail → /api/receipt/{id} 返回归一化坐标
5. 指标层：run_eval 的 evidence_coverage 统计（fixture 验证；真实灰测属用户动作）
6. 资产层：prompt v1_3_0_evidence 登记（status=draft，active 保持 v1_2_8 不动）
7. 前端静态断言：明细行证据点击/高亮函数/无证据人话提示/控件尺寸
"""

import csv
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
_REPO_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

os.environ.setdefault("DB_PATH", "/tmp/receipt_demo_field_evidence.db")
if os.path.exists("/tmp/receipt_demo_field_evidence.db"):
    os.remove("/tmp/receipt_demo_field_evidence.db")

import pytest

from app import db


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_field_evidence.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    db.DB_PATH = test_db_path
    db._make_engine()
    yield
    if os.path.exists(test_db_path):
        try:
            os.remove(test_db_path)
        except Exception:
            pass
    db.DB_PATH = old_db_path
    db._make_engine()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def _mk_receipt():
    rid = db.create_receipt(status="uploaded")
    db.update_receipt(rid, supplier_name="祥興食品", receipt_date="2026-08-01")
    return rid


# -------------------------------------------------------------
# 1. 契约层：Evidence 模型 + ReceiptItem.evidence Optional
# -------------------------------------------------------------
def test_evidence_model_defaults():
    from app.models import Evidence
    ev = Evidence()
    assert ev.page == 1
    assert ev.bbox is None
    assert ev.raw_text is None


def test_receipt_item_evidence_optional_default_none():
    """模型不输出 evidence 字段完全合法（契约门禁不强制）。"""
    from app.models import ReceiptItem
    it = ReceiptItem(name="菜心", qty=5.0, unit="斤", unit_price=10.0, amount=50.0)
    assert it.evidence is None


def test_receipt_item_evidence_parses_dict():
    from app.models import Evidence, ReceiptItem
    it = ReceiptItem(name="菜心", qty=5.0, unit="斤", unit_price=10.0, amount=50.0,
                     evidence={"page": 1, "bbox": [0.1, 0.2, 0.4, 0.25],
                               "raw_text": "菜心 5 斤 @10"})
    assert isinstance(it.evidence, Evidence)
    assert it.evidence.bbox == [0.1, 0.2, 0.4, 0.25]
    assert it.evidence.raw_text == "菜心 5 斤 @10"


def test_receipt_item_extra_forbid_semantics_unchanged():
    """extra=forbid 语义不变：schema 外字段仍被拒绝。"""
    from pydantic import ValidationError
    from app.models import ReceiptItem
    with pytest.raises(ValidationError):
        ReceiptItem(name="菜心", qty=5.0, unit="斤", unit_price=10.0, amount=50.0,
                    not_in_schema="x")


def test_validate_contract_roundtrip_with_evidence():
    """契约门禁整单回环：明细带 evidence 通过；缺失也通过。"""
    from app.services.contract import validate_contract
    payload = {
        "doc_form": "printed_delivery_note", "vendor": "祥興",
        "date": "2026-08-01",
        "items": [
            {"name": "菜心", "qty": 5.0, "unit": "斤", "unit_price": 10.0,
             "amount": 50.0,
             "evidence": {"page": 1, "bbox": [0.1, 0.2, 0.4, 0.25],
                          "raw_text": "菜心 5 斤 @10"}},
            {"name": "白菜", "qty": 2.0, "unit": "斤", "unit_price": 8.0,
             "amount": 16.0},
        ],
        "total": 66.0, "payment_marked": False, "confidence": 0.9,
    }
    data, err = validate_contract(payload)
    assert err is None and data is not None
    assert data.items[0].evidence is not None
    assert data.items[0].evidence.bbox == [0.1, 0.2, 0.4, 0.25]
    assert data.items[1].evidence is None


# -------------------------------------------------------------
# 2. 归一化：bbox 非法/越界/非数字一律置 None，不阻断
# -------------------------------------------------------------
def test_normalize_evidence_valid():
    from app.services.contract import normalize_evidence
    ev = normalize_evidence({"page": "1", "bbox": [0.1, 0.2, 0.4, 0.25],
                             "raw_text": "菜心 5 斤"})
    assert ev == {"page": 1, "bbox": [0.1, 0.2, 0.4, 0.25], "raw_text": "菜心 5 斤"}


def test_normalize_evidence_bbox_out_of_range_sets_none():
    from app.services.contract import normalize_evidence
    ev = normalize_evidence({"bbox": [1.5, -0.2, 0.9, 0.9], "raw_text": "菜心"})
    assert ev is not None and ev["bbox"] is None
    assert ev["raw_text"] == "菜心"


def test_normalize_evidence_bbox_non_numeric_sets_none():
    from app.services.contract import normalize_evidence
    for bad in (["a", "b", "c", "d"], [0.1, None, 0.3, 0.4], "not-a-list",
                [0.1, 0.2], [0.1, 0.2, 0.3, 0.4, 0.5]):
        ev = normalize_evidence({"bbox": bad, "raw_text": "x"})
        assert ev is None or ev["bbox"] is None, f"非法 bbox 未置 None: {bad}"


def test_normalize_evidence_garbage_returns_none_not_blocking():
    from app.services.contract import normalize_evidence
    for garbage in (None, "", "evidence-string", 123, ["list"], object()):
        assert normalize_evidence(garbage) is None


def test_normalize_evidence_no_content_returns_none():
    """bbox/raw_text 双缺失 → 整体 None（不留空壳证据）。"""
    from app.services.contract import normalize_evidence
    assert normalize_evidence({}) is None
    assert normalize_evidence({"page": 1}) is None
    assert normalize_evidence({"bbox": [2.0, 2.0, 3.0, 3.0]}) is None


# -------------------------------------------------------------
# 3. 解析层：extract_chain 从模型原始输出解析 evidence（容错）
# -------------------------------------------------------------
_RAW_V130_WITH_EVIDENCE = json.dumps({
    "supplier_name": "祥興食品", "date": "2026-08-01", "is_paid": False,
    "items": [
        {"item_name": "菜心", "quantity": 5.0, "unit": "斤", "unit_price": 10.0,
         "amount": 50.0,
         "evidence": {"page": 1, "bbox": [0.10, 0.20, 0.40, 0.25],
                      "raw_text": "菜心  5斤 @10.0"}},
        {"item_name": "白菜", "quantity": 2.0, "unit": "斤", "unit_price": 8.0,
         "amount": 16.0},
    ],
    "total_amount": 66.0, "confidence": 0.95,
}, ensure_ascii=False)


def test_parse_to_receipt_with_evidence():
    from app.chains.extract_chain import _parse_to_receipt
    data, err = _parse_to_receipt(_RAW_V130_WITH_EVIDENCE)
    assert err is None and data is not None
    assert len(data.items) == 2
    ev = data.items[0].evidence
    assert ev is not None
    assert ev.bbox == [0.10, 0.20, 0.40, 0.25]
    assert ev.raw_text == "菜心  5斤 @10.0"
    assert data.items[1].evidence is None


def test_parse_to_receipt_without_evidence_not_blocking():
    """active v1_2_8 不要求 evidence：输出无 evidence 时主链路照常。"""
    from app.chains.extract_chain import _parse_to_receipt
    raw = json.dumps({
        "supplier_name": "祥興食品", "date": "2026-08-01", "is_paid": False,
        "items": [{"item_name": "菜心", "quantity": 5.0, "unit": "斤",
                   "unit_price": 10.0, "amount": 50.0}],
        "total_amount": 50.0, "confidence": 0.95,
    }, ensure_ascii=False)
    data, err = _parse_to_receipt(raw)
    assert err is None and data is not None
    assert data.items[0].evidence is None


def test_parse_to_receipt_bad_evidence_degrades_not_blocks():
    """bbox 越界/类型非法 → 证据降级（bbox 置 None 或整体 None），识别结果照常。"""
    from app.chains.extract_chain import _parse_to_receipt
    for bad_ev in ({"page": 1, "bbox": [1.5, -0.2, 0.9, 0.9], "raw_text": "菜心"},
                   "garbage-evidence", 123,
                   {"bbox": ["a", "b", "c", "d"]}):
        raw = json.dumps({
            "supplier_name": "祥興食品", "date": "2026-08-01", "is_paid": False,
            "items": [{"item_name": "菜心", "quantity": 5.0, "unit": "斤",
                       "unit_price": 10.0, "amount": 50.0, "evidence": bad_ev}],
            "total_amount": 50.0, "confidence": 0.95,
        }, ensure_ascii=False)
        data, err = _parse_to_receipt(raw)
        assert err is None and data is not None, f"证据非法不得阻断主链路: {bad_ev}"
        ev = data.items[0].evidence
        if ev is not None:
            assert ev.bbox is None, f"越界/非法 bbox 必须置 None: {bad_ev}"


# -------------------------------------------------------------
# 4. 落库层：evidence_json 幂等迁移 + 写读回环
# -------------------------------------------------------------
def test_receipt_items_has_evidence_json_column():
    from sqlalchemy import text
    with db._engine.connect() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(receipt_items)"))}
    assert "evidence_json" in cols


def test_migration_idempotent_second_run_silent(caplog):
    """幂等迁移：二次执行零日志零异常（R5 静默幂等口径）。"""
    db._make_engine()
    with caplog.at_level(logging.WARNING, logger="app.db"):
        db._make_engine()
    migrate_logs = [r for r in caplog.records if "migrate" in r.getMessage().lower()]
    assert migrate_logs == [], "二次迁移必须静默（零日志）"
    from sqlalchemy import text
    with db._engine.connect() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(receipt_items)"))}
    assert "evidence_json" in cols


def test_set_get_receipt_items_evidence_roundtrip():
    rid = _mk_receipt()
    db.set_receipt_items(rid, [
        {"name": "菜心", "quantity": 5.0, "unit": "斤", "unit_price": 10.0,
         "amount": 50.0,
         "evidence": {"page": 1, "bbox": [0.1, 0.2, 0.4, 0.25],
                      "raw_text": "菜心 5 斤"}},
        {"name": "白菜", "quantity": 2.0, "unit": "斤", "unit_price": 8.0,
         "amount": 16.0},
        {"name": "萝卜", "quantity": 1.0, "unit": "斤", "unit_price": 5.0,
         "amount": 5.0, "evidence": {"bbox": [2.0, 0.0, 3.0, 1.0]}},
    ])
    items = db.get_receipt_items(rid)
    assert len(items) == 3
    assert items[0]["evidence"] == {"page": 1, "bbox": [0.1, 0.2, 0.4, 0.25],
                                    "raw_text": "菜心 5 斤"}
    assert items[1]["evidence"] is None
    # 越界 bbox 落库前被归一为无效证据（空壳证据不落库）
    assert items[2]["evidence"] is None


def test_set_receipt_items_without_evidence_key_backwards_compatible():
    """既有写入路径不带 evidence 键 → 照常落库，不炸。"""
    rid = _mk_receipt()
    db.set_receipt_items(rid, [
        {"name": "菜心", "quantity": 5.0, "unit": "斤", "unit_price": 10.0,
         "amount": 50.0},
    ])
    items = db.get_receipt_items(rid)
    assert items[0]["name"] == "菜心"
    assert items[0]["evidence"] is None


# -------------------------------------------------------------
# 5. 端到端：save_parsed_data → build_detail → API 归一化坐标回显
# -------------------------------------------------------------
def _mk_parsed_data(with_evidence=True):
    from app.models import ReceiptData
    items = [
        {"name": "菜心", "qty": 5.0, "unit": "斤", "unit_price": 10.0, "amount": 50.0},
        {"name": "白菜", "qty": 2.0, "unit": "斤", "unit_price": 8.0, "amount": 16.0},
    ]
    if with_evidence:
        items[0]["evidence"] = {"page": 1, "bbox": [0.10, 0.20, 0.40, 0.25],
                                "raw_text": "菜心 5斤 @10"}
    return ReceiptData(doc_form="printed_delivery_note", vendor="祥興食品",
                       date="2026-08-01", items=items, total=66.0,
                       payment_marked=False, confidence=0.95)


def test_save_parsed_data_evidence_lands_and_api_returns_normalized(client):
    rid = _mk_receipt()
    from app.services.receipt_utils import save_parsed_data
    save_parsed_data(rid, _mk_parsed_data(with_evidence=True),
                     {"raw": "{}", "vendor_context": ""})

    resp = client.get("/api/receipt/%d" % rid, headers={"X-Role": "staff"})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    items = data["items"]
    ev = items[0]["evidence"]
    assert ev is not None
    # 前端高亮接口返回归一化坐标：0-1 区间
    assert len(ev["bbox"]) == 4
    assert all(isinstance(v, (int, float)) and 0.0 <= v <= 1.0 for v in ev["bbox"])
    assert ev["bbox"] == [0.10, 0.20, 0.40, 0.25]
    assert ev["raw_text"] == "菜心 5斤 @10"
    assert items[1]["evidence"] is None


def test_save_parsed_data_without_evidence_main_chain_intact(client):
    """stub 识别无 evidence 时全链路照常 parsed（不阻断红线）。"""
    rid = _mk_receipt()
    from app.services.receipt_utils import save_parsed_data
    detail = save_parsed_data(rid, _mk_parsed_data(with_evidence=False),
                              {"raw": "{}", "vendor_context": ""})
    assert detail["status"] == "parsed"
    assert all(it["evidence"] is None for it in detail["items"])
    resp = client.get("/api/receipt/%d" % rid, headers={"X-Role": "staff"})
    assert resp.status_code == 200


# -------------------------------------------------------------
# 6. 指标层：evidence_coverage 统计（run_eval，fixture 验证）
# -------------------------------------------------------------
def test_evidence_coverage_unit_stats():
    from run_eval import _evidence_coverage
    items = [
        {"evidence": {"bbox": [0.1, 0.2, 0.3, 0.4]}},
        {"evidence": None},
        {},
        {"evidence": {"raw_text": "菜心"}},
    ]
    assert _evidence_coverage(items) == 0.5
    assert _evidence_coverage([]) == 0.0


def test_compare_counts_items_with_evidence():
    from run_eval import compare
    expected = {"vendor": "祥興", "date": "2026-08-01", "total": 66.0,
                "doc_form": "printed_delivery_note",
                "items": [{"name": "菜心", "qty": 5, "unit": "斤",
                           "unit_price": 10, "amount": 50},
                          {"name": "白菜", "qty": 2, "unit": "斤",
                           "unit_price": 8, "amount": 16}]}
    predicted = dict(expected)
    predicted["items"] = [
        {**expected["items"][0],
         "evidence": {"page": 1, "bbox": [0.1, 0.2, 0.3, 0.4], "raw_text": "菜心"}},
        expected["items"][1],
    ]
    res = compare(expected, predicted)
    assert res["item_total"] == 2
    assert res["items_with_evidence"] == 1


def _write_evalset_fixture(root):
    """构造两张样本的最小评测集：S001 两行全带证据，S002 一行带证据。"""
    evalset_dir = os.path.join(str(root), "evalsets")
    os.makedirs(os.path.join(evalset_dir, "expected"), exist_ok=True)
    os.makedirs(os.path.join(evalset_dir, "receipts"), exist_ok=True)
    with open(os.path.join(evalset_dir, "manifest.csv"), "w",
              encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "sample_id", "split", "doc_form", "layout_type",
            "gt_status", "gt_source_model", "image"])
        w.writeheader()
        for sid in ("S001", "S002"):
            w.writerow({"sample_id": sid, "split": "test",
                        "doc_form": "printed_delivery_note", "layout_type": "table",
                        "gt_status": "confirmed", "gt_source_model": "fixture",
                        "image": "receipts/%s.jpg" % sid})

    def _gt(with_evidence_rows):
        items = []
        for i, name in enumerate(["菜心", "白菜"]):
            it = {"name": name, "qty": 5.0, "unit": "斤",
                  "unit_price": 10.0, "amount": 50.0}
            if i < with_evidence_rows:
                it["evidence"] = {"page": 1,
                                  "bbox": [0.1, 0.2, 0.4, 0.25],
                                  "raw_text": name}
            items.append(it)
        return {"vendor": "祥興", "date": "2026-08-01", "total": 100.0,
                "doc_form": "printed_delivery_note", "payment_marked": False,
                "items": items}

    for sid, n_ev in (("S001", 2), ("S002", 1)):
        with open(os.path.join(evalset_dir, "expected", sid + ".json"), "w",
                  encoding="utf-8") as f:
            json.dump(_gt(n_ev), f, ensure_ascii=False)
    return evalset_dir


def test_run_eval_report_evidence_coverage(tmp_path):
    """EvalReport.evidence_coverage：stub 回读 GT 验证统计与报告通路。

    口径：真实灰测跑分（v1_2_8 vs v1_3_0 对比）属用户/运维动作，本用例仅用
    fixture 验证字段与统计逻辑就绪。
    """
    import run_eval
    evalset_dir = _write_evalset_fixture(tmp_path)
    report_dir = os.path.join(str(tmp_path), "reports")
    report = run_eval.run_eval(split="test", prompt_version="v1_3_0_evidence",
                               engine="stub", evalset_dir=evalset_dir,
                               report_dir=report_dir,
                               require_confirmed=False)  # L2：本用例只验证据统计通路
    # 3/4 明细行带证据
    assert report["evidence_coverage"] == 0.75
    assert os.path.exists(report["report_path"])
    with open(report["report_path"], encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["evidence_coverage"] == 0.75


# -------------------------------------------------------------
# 7. 资产层：v1_3_0_evidence 登记（draft 口径，active 不动）
# -------------------------------------------------------------
def test_registry_v1_3_0_evidence_loadable_via_get_prompt():
    from ai_registry.registry import ai_registry
    prompt = ai_registry.get_prompt("extract", "v1_3_0_evidence")
    assert isinstance(prompt, str) and prompt.strip()
    assert "evidence" in prompt, "v1_3_0 必须追加证据输出要求"
    assert "bbox" in prompt and "raw_text" in prompt
    # 增量追加：v1_2_8 的既有规范不被丢弃
    assert "苏州码子" in prompt or "花码" in prompt


def test_metadata_v1_3_0_registered_draft_active_unchanged():
    from ai_registry.registry import ai_registry
    meta = ai_registry.get_prompt_metadata("extract")
    assert meta["active_version"] == "v1_2_8_anti_injection", \
        "active 必须保持 v1_2_8 不动（灰测验证通过前不置 production）"
    v130 = meta["versions"].get("v1_3_0_evidence")
    assert v130 is not None, "v1_3_0_evidence 未登记 metadata"
    assert v130["status"] == "draft", "灰测验证通过前 status 必须为 draft"
    changelog = " ".join(v130.get("changelog", []))
    assert "灰测" in changelog and "production" in changelog.replace("Production", "production")


def test_extract_chain_default_prompt_unchanged_and_explicit_loader():
    """默认链路行为零变化（active v1_2_8）；v1_3_0 仅在显式加载入口可得。"""
    from ai_registry.registry import ai_registry
    from app.chains import extract_chain
    active = ai_registry.get_prompt("extract")
    assert extract_chain.SYSTEM_PROMPT == active, "默认 SYSTEM_PROMPT 必须是 active 版本"
    explicit = extract_chain.load_prompt_with_evidence()
    assert explicit == ai_registry.get_prompt("extract", "v1_3_0_evidence")


# -------------------------------------------------------------
# 8. 前端静态断言：审核台明细行证据高亮
# -------------------------------------------------------------
_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "static")


def _read_static(rel):
    with open(os.path.join(_STATIC_DIR, rel), encoding="utf-8") as f:
        return f.read()


def test_main_js_evidence_highlight_wiring():
    src = _read_static("js/main.js")
    for fn in ("function highlightItemEvidence",
               "function showEvidenceHighlight",
               "function clearEvidenceHighlight"):
        assert fn in src, "main.js 缺少证据高亮函数 %s" % fn
    assert "该行无证据数据" in src, "无证据行点击必须有人话提示"
    # 明细行渲染携带证据数据（Tab1 与归档弹窗两处行渲染器）
    tab1_start = src.index("function appendTableRow")
    tab1_end = src.index("function addEmptyRow")
    assert "evidence" in src[tab1_start:tab1_end], "appendTableRow 必须挂证据数据"
    arc_start = src.index("function appendArcTableRow")
    arc_end = src.index("function addArcEmptyRow")
    assert "evidence" in src[arc_start:arc_end], "appendArcTableRow 必须挂证据数据"


def test_main_js_evidence_survives_save_roundtrip():
    """证据随保存链路透传：采集（collect）与 payload 构造（build）均不丢证据。"""
    src = _read_static("js/main.js")
    collect_start = src.index("function collectReviewFormData")
    collect_end = src.index("function buildSavePayloadFromData")
    assert "evidence" in src[collect_start:collect_end], \
        "collectReviewFormData 必须采集行证据"
    build_start = src.index("function buildSavePayloadFromData")
    build_end = src.index("settlement_type:", build_start)
    assert "evidence" in src[build_start:build_end], \
        "buildSavePayloadFromData 必须透传行证据"


def test_style_css_evidence_highlight_controls():
    css = _read_static("css/style.css")
    assert ".evidence-highlight-layer" in css, "缺少证据高亮层样式"
    assert ".evidence-highlight-box" in css, "缺少证据高亮框样式"
    btn_pos = css.index(".btn-evidence {")
    btn_css = css[btn_pos:btn_pos + 400]
    assert "min-height: 38px" in btn_css, "证据控件高度必须 >= 38px"
    assert "min-width: 38px" in btn_css, "证据控件宽度必须 >= 38px"
