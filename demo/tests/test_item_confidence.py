# -*- coding: utf-8 -*-
"""P11（T5）：item 级置信度完整落地测试 —— 全离线、零外部调用。

why：修复前 extract_chain 的 schema 归一化把每个 item 的 confidence 直接 pop 掉，
且 ReceiptItem 契约未声明该字段（extra="forbid"），导致 save_parsed_data 里的
`it.confidence if hasattr(...)` 恒为假 → 落库永远是默认 0.5，"哪一行不准"无法定位。

覆盖：
1. 解析层：合法 item confidence 被保留并过契约；非数值 / 越界 / NaN 一律丢弃不阻断
2. 契约层：ReceiptItem 声明 confidence（Optional，缺失合法，越界仍拒绝）
3. 落库层：save_parsed_data → receipt_items.confidence → build_detail 回读一致；
   模型未给出时落 NULL（不再兜底 0.5）——「无数据」与「置信度 0.5」必须可区分
4. 前端/静态层：明细行低置信阈值常量 + 角标存在（按行标色定位）

钉住口径：顶层 confidence 的既有行为完全不变（本文件不触碰）。
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app import db

_DEMO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """强制把 DB_PATH 指到 tmp_path，绝不写 live demo 库。"""
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_item_confidence.db")
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


def _payload(item_conf_marker):
    """构造一份最小合法识别结果；item_conf_marker 为哨兵（None 表示不带该键）。"""
    item = {"name": "本地菜心", "qty": 5, "unit": "斤", "unit_price": 10, "amount": 50}
    if item_conf_marker is not _MISSING:
        item["confidence"] = item_conf_marker
    return {
        "doc_form": "printed_delivery_note",
        "vendor": "祥興欄",
        "date": "2026-09-19",
        "items": [item],
        "total": 50,
        "payment_marked": False,
        "confidence": 0.9,
    }


_MISSING = object()


# -------------------------------------------------------------
# 1. 解析层：合法值保留，非法值丢弃
# -------------------------------------------------------------
def test_item_confidence_preserved_through_contract():
    """0.32 这类合法 item 置信度必须一路带到 ReceiptData（修复前恒为丢失）。"""
    from app.chains.extract_chain import _parse_to_receipt

    data, err = _parse_to_receipt(json.dumps(_payload(0.32)))
    assert err is None, f"契约不应拒绝：{err}"
    assert data is not None
    assert data.items[0].confidence == pytest.approx(0.32), "item 级置信度被丢弃"
    # 顶层行为不变
    assert data.confidence == pytest.approx(0.9)


@pytest.mark.parametrize("bad", ["high", "", "低", 1.5, -0.01, float("nan")])
def test_non_numeric_or_out_of_range_item_confidence_dropped(bad):
    """非数值/越界 item 置信度一律丢弃，且不得让后处理整段异常（契约仍通过）。"""
    from app.chains.extract_chain import _parse_to_receipt

    data, err = _parse_to_receipt(json.dumps(_payload(bad)))
    assert err is None, f"非法 item 置信度不应阻断主链路，实际错误：{err}"
    assert data.items[0].confidence is None, f"{bad!r} 应被丢弃而不是落库"


def test_item_confidence_absent_is_legal():
    """LLM 不输出 item confidence 时契约仍合法（Optional 语义）。"""
    from app.chains.extract_chain import _parse_to_receipt

    data, err = _parse_to_receipt(json.dumps(_payload(_MISSING)))
    assert err is None, f"缺失 item confidence 必须合法：{err}"
    assert data.items[0].confidence is None


# -------------------------------------------------------------
# 2. 契约层：字段声明与边界
# -------------------------------------------------------------
def test_contract_declares_item_confidence():
    from app.models import ReceiptItem

    assert "confidence" in ReceiptItem.model_fields, "ReceiptItem 必须声明 confidence 字段"
    field = ReceiptItem.model_fields["confidence"]
    assert field.is_required() is False, "confidence 必须可选（LLM 不输出为合法）"

    # 越界值仍被契约拒绝（Pydantic ge/le），不是静默放行
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ReceiptItem(name="菜心", qty=1, unit="斤", unit_price=1, amount=1, confidence=1.5)


def test_contract_still_rejects_unknown_item_field():
    """extra=forbid 语义不变：schema 外字段仍必须拒绝。"""
    from pydantic import ValidationError
    from app.models import ReceiptItem

    with pytest.raises(ValidationError):
        ReceiptItem(name="菜心", qty=1, unit="斤", unit_price=1, amount=1, bogus_field=1)


# -------------------------------------------------------------
# 3. 落库层：DB 回读一致 + 缺失时旧默认不变
# -------------------------------------------------------------
def _make_receipt_data(item_conf):
    from app.models import DocForm, ReceiptData, ReceiptItem

    kwargs = dict(name="本地菜心", qty=5, unit="斤", unit_price=10, amount=50)
    if item_conf is not _MISSING:
        kwargs["confidence"] = item_conf
    return ReceiptData(
        doc_form=DocForm.PRINTED,
        vendor="祥興欄",
        date="2026-09-19",
        items=[ReceiptItem(**kwargs)],
        total=50,
        payment_marked=False,
        confidence=0.9,
    )


def test_save_parsed_data_persists_item_confidence():
    """端到端：item 置信度落 receipt_items 并经 build_detail 回读（详情可见）。"""
    from app.services.receipt_utils import save_parsed_data

    rid = db.create_receipt(supplier_name="祥興欄", status="parsed")
    data = _make_receipt_data(0.27)
    detail = save_parsed_data(rid, data, {"raw": "", "math_problems": []})

    stored = db.get_receipt_items(rid)
    assert stored, "明细应已落库"
    assert stored[0]["confidence"] == pytest.approx(0.27), "item 置信度未落库"
    assert detail["items"][0]["confidence"] == pytest.approx(0.27), "详情未回读 item 置信度"
    # 顶层置信度既有行为不变
    assert db.get_receipt_row(rid).confidence == pytest.approx(0.9)


def test_save_parsed_data_missing_item_confidence_stored_as_null():
    """LLM 未给出 item 置信度时落 NULL，不得伪造 0.5。

    兜底 0.5 把「无数据」伪装成「中等置信度」：0.5 高于前端低置信阈值 0.40，
    等于让「无从判断」的行永远不被提示人工复核，且与契约声明
    （ReceiptItem.confidence 缺失即为 None）自相矛盾。
    """
    from app.services.receipt_utils import save_parsed_data

    rid = db.create_receipt(supplier_name="祥興欄", status="parsed")
    data = _make_receipt_data(_MISSING)
    save_parsed_data(rid, data, {"raw": "", "math_warnings": []})

    stored = db.get_receipt_items(rid)
    assert stored[0]["confidence"] is None, "缺失置信度必须落 NULL，不得兜底成 0.5"


def test_explicit_half_confidence_is_preserved_as_half():
    """模型真的给出 0.5 时必须原样保留——证明 NULL 与 0.5 确实可区分。"""
    from app.services.receipt_utils import save_parsed_data

    rid = db.create_receipt(supplier_name="祥興欄", status="parsed")
    save_parsed_data(rid, _make_receipt_data(0.5), {"raw": "", "math_warnings": []})

    stored = db.get_receipt_items(rid)
    assert stored[0]["confidence"] == pytest.approx(0.5)


def test_item_confidence_column_has_no_python_side_default():
    """列定义不得留 default：Python 侧默认值会把显式 None 变回一个伪造数值。"""
    from app.db import _ItemRow

    col = _ItemRow.__table__.columns["confidence"]
    assert col.nullable is True, "receipt_items.confidence 必须可空（NULL 表达「未给出」）"
    assert col.default is None, (
        "receipt_items.confidence 不得有 Python 侧默认值——它会把 None 改写成 0.0/0.5")


def test_detail_readback_keeps_null_item_confidence():
    """详情视图回读同样保留 None，前端据此不显示任何置信度角标（而不是显示 0.50）。"""
    from app.services.receipt_utils import save_parsed_data

    rid = db.create_receipt(supplier_name="祥興欄", status="parsed")
    detail = save_parsed_data(rid, _make_receipt_data(_MISSING),
                              {"raw": "", "math_warnings": []})
    assert detail["items"][0]["confidence"] is None


# -------------------------------------------------------------
# 4. 静态层：schema 白名单 + 前端按行标色/显示数值
# -------------------------------------------------------------
def _read(rel_path):
    with open(os.path.join(_DEMO_DIR, rel_path), "r", encoding="utf-8") as f:
        return f.read()


def test_extract_chain_whitelist_keeps_confidence():
    src = _read(os.path.join("app", "chains", "extract_chain.py"))
    # 归一化白名单必须含 confidence
    m = re.search(r"_allowed_item = \{(.*?)\}", src, re.S)
    assert m is not None, "未找到 _allowed_item 白名单"
    assert '"confidence"' in m.group(1), "_allowed_item 必须放行 confidence"
    # pop 清单不得再包含 confidence
    m_pop = re.search(r"for _ik in \[(.*?)\]:", src, re.S)
    assert m_pop is not None, "未找到 item 字段 pop 清单"
    assert '"confidence"' not in m_pop.group(1), "confidence 不应再被 pop 丢弃"


def test_frontend_marks_review_rows_by_deterministic_signal():
    """复核标记改由确定性信号触发（不再依赖模型自评置信度）。

    背景：实测模型自报置信度集中在 0.85~0.98，旧阈值 0.40 永不触发；抬到 0.50 又会
    让整表历史兜底值全部标黄，0.40~0.49 无可用中间档 —— 阈值路线不可行，故下线。
    新口径：行级 = unit_conversion_warning（huama_evaluator 确定的街市花码）；
            整单级 = 街市手写单 / 门禁警告（只标整单，不贴到每一行）。
    """
    js = _read(os.path.join("static", "js", "main.js"))
    assert "ITEM_LOW_CONFIDENCE_THRESHOLD" not in js, "阈值触发的旧常量应已下线"
    assert "isLowConfidence" not in js, "不应再按置信度判定复核标记"
    assert "function itemReviewFlags(" in js, "缺少行级确定性判据的唯一实现"
    assert "function itemReviewBadgeHtml(" in js, "缺少行级角标渲染"
    assert "item-conf-badge" in js, "前端未展示行级角标"
    assert "function receiptReviewReasons(" in js, "缺少整单级「需人工复核」判据"
    assert "ncr_handwritten" in js, "整单级未接入街市手写单信号"
    # 两处表格必须复用同一份判定，避免「同一根因多份实现」导致口径漂移
    assert js.count("itemFlags = itemReviewFlags(item);") == 2, \
        "复核台与归档详情应各调一次同一份行级判据"
    assert js.count("receiptReviewReasons(data)") == 3, \
        "整单级判据应为 1 处定义 + 复核台/归档详情各 1 处调用"
    # 置信度保留为参考信息（不再触发标记）
    assert "tr.dataset.confidence" in js, "置信度读数应仍随行回传"

    css = _read(os.path.join("static", "css", "style.css"))
    assert ".item-conf-badge" in css, "缺少角标样式"
