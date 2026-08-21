# -*- coding: utf-8 -*-
"""
P0-3 修复专项测试：更正单/Credit Note 允许负总额，其他单据仍拒绝负总额与零总额。
"""

import pytest
from app.services.contract import validate_contract
from app.models import DocForm


def test_credit_note_allows_negative_total():
    payload = {
        "doc_form": "credit_note",
        "vendor": "香港水产批发",
        "date": "2026-08-21",
        "items": [
            {"name": "退回死虾", "qty": 4.0, "unit": "斤", "unit_price": 45.0, "amount": -180.0}
        ],
        "total": -180.0,
        "payment_marked": False,
        "confidence": 0.95
    }
    data, err = validate_contract(payload)
    assert err is None, f"Credit Note 负总额被错误拦截: {err}"
    assert data is not None
    assert data.total == -180.0


def test_correction_note_allows_negative_total():
    payload = {
        "doc_form": "correction_note",
        "vendor": "九龙粮油",
        "date": "2026-08-21",
        "items": [
            {"name": "差额冲减", "qty": 1.0, "unit": "件", "unit_price": 50.0, "amount": -50.0}
        ],
        "total": -50.0,
        "payment_marked": False,
        "confidence": 0.95
    }
    data, err = validate_contract(payload)
    assert err is None, f"Correction Note 负总额被错误拦截: {err}"
    assert data is not None
    assert data.total == -50.0


def test_printed_delivery_rejects_negative_total():
    payload = {
        "doc_form": "printed_delivery_note",
        "vendor": "九龙粮油",
        "date": "2026-08-21",
        "items": [
            {"name": "大豆油", "qty": 1.0, "unit": "樽", "unit_price": 85.0, "amount": 85.0}
        ],
        "total": -1.0,
        "payment_marked": False,
        "confidence": 0.95
    }
    data, err = validate_contract(payload)
    assert data is None
    assert "非退款/更正单据总额不能为负数" in err


def test_rejects_zero_total():
    payload = {
        "doc_form": "printed_delivery_note",
        "vendor": "九龙粮油",
        "date": "2026-08-21",
        "items": [
            {"name": "大豆油", "qty": 1.0, "unit": "樽", "unit_price": 85.0, "amount": 85.0}
        ],
        "total": 0.0,
        "payment_marked": False,
        "confidence": 0.95
    }
    data, err = validate_contract(payload)
    assert data is None
    assert "总额不能为0" in err
