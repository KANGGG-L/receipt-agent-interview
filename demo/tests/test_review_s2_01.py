# -*- coding: utf-8 -*-
"""S2-01：包装个数不得覆盖落库数量。离线，不调用模型，不读密钥。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from app.chains import extract_chain, supervisor


def _parse(item, total):
    raw = {
        "doc_form": "printed_delivery_note",
        "vendor": "供应商甲",
        "date": "2026-10-05",
        "items": [item],
        "total": total,
        "payment_marked": False,
        "confidence": 0.9,
    }
    data, err = extract_chain._parse_to_receipt(raw)
    assert err is None, err
    return data


def _pack_line(**over):
    item = {"name": "大豆油 5L*2樽", "qty": 3, "unit": "樽", "unit_price": 100, "amount": 300}
    item.update(over)
    return item


def test_ac1_qty_three_stays_and_gate_does_not_invent_qty_two():
    data = _parse(_pack_line(), 300)
    assert data.items[0].qty == 3.0
    assert data.items[0].amount == 300
    checked, gate_err, issues = supervisor._run_gates_ex(data)
    assert gate_err is None and issues == []
    assert checked.items[0].qty == 3.0
    assert any("数量仍按识别到的 3" in w for w in data.field_warnings)
    assert all("已按" not in w for w in data.field_warnings)


def test_ac2_qty_one_stays_and_warning_does_not_claim_a_rewrite():
    data = _parse(_pack_line(qty=1, amount=100), 100)
    assert data.items[0].qty == 1.0
    sentence = "「大豆油 5L」的品名里写着 2樽，数量仍按识别到的 1，没有自动改。请对照原图，要改请自己填写。"
    assert sentence in data.field_warnings
    assert all("已按" not in w and "按 2 处理" not in w for w in data.field_warnings)
    mismatched = _parse(_pack_line(qty=1, amount=200), 200)
    assert mismatched.items[0].qty == 1.0
    assert mismatched.items[0].amount == 200
    _checked, gate_err, issues = supervisor._run_gates_ex(mismatched)
    assert gate_err is not None and issues
    assert mismatched.items[0].qty == 1.0


def test_ac3_float_one_and_string_one_stay_one():
    as_float = _parse(_pack_line(qty=1.0, amount=100), 100)
    as_text = _parse(_pack_line(qty="1", amount=100), 100)
    assert as_float.items[0].qty == 1.0
    assert as_text.items[0].qty == 1.0
    assert all(it.qty != 2.0 for it in (as_float.items[0], as_text.items[0]))


def test_ac4_missing_qty_is_not_filled_from_pack_count():
    missing = "「大豆油 5L」的品名里写着 2樽，数量没有识别到，没有按包装个数填写。请对照原图自己填写数量。"
    absent = _pack_line(amount=200)
    absent.pop("qty")
    no_key = _parse(absent, 200)
    assert no_key.items[0].qty == 0.0
    assert no_key.items[0].qty != 2.0
    assert missing in no_key.field_warnings
    assert any("没读清" in w for w in no_key.field_warnings)
    explicit_none = _parse(_pack_line(qty=None, amount=200), 200)
    assert explicit_none.items[0].qty == 0.0
    assert missing in explicit_none.field_warnings


def test_ac5_matching_pack_count_is_kept_without_mismatch_warning():
    data = _parse(_pack_line(qty=2, amount=200), 200)
    assert data.items[0].qty == 2.0
    assert all("品名里写着" not in w for w in data.field_warnings)
    assert data.items[0].qty != 4.0


def test_ac6_plain_name_qty_one_has_no_pack_suggestion():
    data = _parse({"name": "菜心", "qty": 1, "unit": "斤", "unit_price": 10, "amount": 10}, 10)
    assert data.items[0].qty == 1.0
    assert data.items[0].name == "菜心"
    assert all("品名里写着" not in w for w in data.field_warnings)


def test_ac7_name_may_split_but_spec_number_is_not_qty():
    data = _parse(_pack_line(), 300)
    assert data.items[0].name == "大豆油 5L"
    assert data.items[0].raw_name == "大豆油 5L*2樽"
    assert data.items[0].qty == 3.0
    assert data.items[0].qty != 5.0


def test_pack_count_is_not_written_into_actual_qty():
    data = _parse(_pack_line(actual_qty=9), 300)
    assert data.items[0].qty == 3.0
    assert data.items[0].actual_qty == 9.0


def test_ac9_splitter_still_returns_pack_count():
    from ai_registry.tools.smart_splitter.v1_2_0_multi_pack import SmartSplitterTool
    res = SmartSplitterTool().execute("大豆油 5L*2樽")
    assert res["quantity"] == 2.0
    assert res["item_name"] == "大豆油 5L"
    assert res["unit"] == "樽"
