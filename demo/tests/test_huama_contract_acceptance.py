# -*- coding: utf-8 -*-
"""R5：含花码单据不再被 extra=forbid 契约整单打回。

why：huama_evaluator 在 schema 白名单裁剪之后才写入 items 的 contains_huama /
unit_conversion_warning 与顶层 contains_huama / math_warnings。这些键既不在
白名单内、也未在 ReceiptItem/ReceiptData 声明，而两者都是 extra="forbid"，
于是含花码品名（如「〡〢菜心」）的一整张单据会被契约门禁打回：
    err='契约校验失败: items.0.contains_huama: Extra inputs are not permitted'
    data=None
即 Gap 7 专项治理产出的花码警示反而让单据识别整体失败。

修法（方案 a）：白名单放行 + 契约声明为可选字段（保持后处理顺序不变，风险最小）。
另接线 receipt_items.unit_conversion_warning 落库（原实现硬编码空串，前端读不到花码警示）。

覆盖：
1. 解析层：含花码样本 data 不为 None，item 级/顶层花码标记与警告完整
2. 非花码样本：标记为默认（False/None），既有行为不变
3. 落库层：unit_conversion_warning 落 receipt_items 并经 build_detail 回读传前端
4. 契约层：extra=forbid 语义不变（schema 外字段仍拒绝），未放水
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app import db


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """强制把 DB_PATH 指到 tmp_path，绝不写 live demo 库。"""
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_huama_contract.db")
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


def _payload(item_name, item_conf=0.9, top_conf=0.9, math_warnings=None):
    """构造最小合法识别结果；花码样本用「〡〢菜心」（〡=1 〢=2）。"""
    item = {"name": item_name, "qty": 5, "unit": "斤", "unit_price": 10, "amount": 50}
    if item_conf is not None:
        item["confidence"] = item_conf
    payload = {
        "doc_form": "printed_delivery_note",
        "vendor": "祥興欄",
        "date": "2026-09-19",
        "items": [item],
        "total": 50,
        "payment_marked": False,
        "confidence": top_conf,
    }
    if math_warnings is not None:
        payload["math_warnings"] = math_warnings
    return payload


# -------------------------------------------------------------
# 1. 解析层：含花码样本通过契约且标记完整（修复前 data=None）
# -------------------------------------------------------------
def test_huama_item_passes_contract_with_flags():
    from app.chains.extract_chain import _parse_to_receipt

    data, err = _parse_to_receipt(json.dumps(_payload("〡〢菜心")))
    assert err is None, f"含花码单据不应被契约打回，实际错误：{err}"
    assert data is not None, "修复前此处 data=None（extra=forbid 整单打回）"

    it = data.items[0]
    assert it.contains_huama is True, "item 级花码标记丢失"
    assert it.unit_conversion_warning, "item 级花码提示丢失"
    assert it.confidence is not None and it.confidence <= 0.40, "花码行置信度未压降"
    # 花码辅助转译：〡〢菜心 -> 12菜心
    assert "12" in it.name and "菜心" in it.name

    assert data.contains_huama is True, "顶层花码标记丢失"
    assert data.confidence <= 0.40, "顶层置信度未压降"
    assert any("花码" in w for w in data.math_warnings), "顶层花码复核警告丢失"


def test_llm_supplied_huama_flags_are_accepted():
    """LLM 直接输出 contains_huama 时不得被契约打回（值由校准器复核后覆盖）。"""
    from app.chains.extract_chain import _parse_to_receipt

    payload = _payload("本地菜心")
    payload["contains_huama"] = True
    payload["items"][0]["contains_huama"] = True
    payload["items"][0]["unit_conversion_warning"] = "LLM 标注：单位不可折算"
    data, err = _parse_to_receipt(json.dumps(payload))
    assert err is None, f"LLM 提供的花码键不应打回整单：{err}"
    assert data.items[0].contains_huama is True
    assert data.items[0].unit_conversion_warning, "花码行必须带非空提示"


def test_llm_only_unit_warning_is_preserved():
    """LLM 只给 unit_conversion_warning（无花码）时，该提示应原样保留到契约。"""
    from app.chains.extract_chain import _parse_to_receipt

    payload = _payload("本地菜心")
    payload["items"][0]["unit_conversion_warning"] = "LLM 标注：单位不可折算"
    data, err = _parse_to_receipt(json.dumps(payload))
    assert err is None, f"LLM 提供的单位提示不应打回整单：{err}"
    assert data.items[0].contains_huama is False
    assert "不可折算" in (data.items[0].unit_conversion_warning or ""), "非花码提示被丢弃"


def test_string_math_warnings_does_not_break_calibration():
    """math_warnings 为字符串（LLM 常见形态）时校准不得抛异常导致整段后处理失效。"""
    from app.chains.extract_chain import _parse_to_receipt

    data, err = _parse_to_receipt(json.dumps(_payload("〡〢菜心", math_warnings="模型自报尾数抹零")))
    assert err is None, f"字符串 math_warnings 不应阻断：{err}"
    assert isinstance(data.math_warnings, list)
    assert any("花码" in w for w in data.math_warnings), "原有 LLM 警告之外仍要追加花码警告"


# -------------------------------------------------------------
# 2. 非花码样本：既有行为不变
# -------------------------------------------------------------
def test_non_huama_item_keeps_defaults():
    from app.chains.extract_chain import _parse_to_receipt

    data, err = _parse_to_receipt(json.dumps(_payload("本地菜心")))
    assert err is None
    assert data.items[0].contains_huama is False
    assert data.items[0].unit_conversion_warning is None
    assert data.contains_huama is False
    assert data.math_warnings == []
    assert data.confidence == pytest.approx(0.9), "无花码时置信度不得被压降"


# -------------------------------------------------------------
# 3. 落库层：花码提示落 receipt_items 并回读给前端
# -------------------------------------------------------------
def test_save_parsed_data_persists_unit_conversion_warning():
    from app.models import DocForm, ReceiptData, ReceiptItem
    from app.services.receipt_utils import save_parsed_data

    rid = db.create_receipt(supplier_name="祥興欄", status="parsed")
    data = ReceiptData(
        doc_form=DocForm.PRINTED,
        vendor="祥興欄",
        date="2026-09-19",
        items=[ReceiptItem(
            name="12菜心", qty=5, unit="斤", unit_price=10, amount=50,
            confidence=0.40, contains_huama=True,
            unit_conversion_warning="包含街市花码，需人工核验",
        )],
        total=50,
        payment_marked=False,
        confidence=0.40,
    )
    detail = save_parsed_data(rid, data, {"raw": "", "math_problems": []})

    stored = db.get_receipt_items(rid)
    assert stored, "明细应已落库"
    assert "花码" in stored[0]["unit_conversion_warning"], "花码提示未落库（原实现硬编码空串）"
    # 详情接口回读：前端按该字段渲染行内警示
    assert any("花码" in (i.get("unit_conversion_warning") or "") for i in detail["items"]), \
        "详情未回传 unit_conversion_warning，前端读不到花码警示"


# -------------------------------------------------------------
# 4. 契约层：extra=forbid 语义不变（未放水）
# -------------------------------------------------------------
def test_contract_still_rejects_unknown_fields():
    from pydantic import ValidationError
    from app.models import ReceiptItem, ReceiptData

    with pytest.raises(ValidationError):
        ReceiptItem(name="菜心", qty=1, unit="斤", unit_price=1, amount=1, bogus_field=1)
    with pytest.raises(ValidationError):
        ReceiptData(
            doc_form="printed_delivery_note", vendor="祥興欄", date="2026-09-19",
            items=[], total=1, payment_marked=False, confidence=0.9, bogus_field=1,
        )
