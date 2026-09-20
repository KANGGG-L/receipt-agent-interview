# -*- coding: utf-8 -*-
"""N1 回归：落库时 math_warnings_json 必须合并「门禁警告 + 整单校准警告」。

why: save_parsed_data 原先只把 result["math_problems"]（算术/契约/总额等门禁）
写进 math_warnings_json，完全不看 data.math_warnings —— 而整单花码警告正是由
huama_evaluator 写入 data.math_warnings。结果是本批次新增的门禁横幅对
「含街市花码」的单据永远不生效（前端读的是同一列）。

覆盖方向：
- 两类警告同时存在 → 该列同时含两类，去重且顺序稳定（problems 在前，warnings 在后）；
- 同一警告同时出现在两侧 → 只保留一条（首次出现顺序）；
- 反证：把合并退回「只取 math_problems」时，主断言必须失败。

全程用隔离临时库，不碰 live 库；不调用任何真实 VLM。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json

import pytest

from app import db
from app.models import DocForm, ReceiptData, ReceiptItem
from app.services import receipt_utils

_GATE_WARN = "算术门禁: 明细合计=100 预期总额=100，但总额=999（差-899）"
_HUAMA_WARN = "花码复核: 整单含街市花码（苏州码子），已降置信度待人工核对"


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """DB_PATH 指向临时库，跑完还原（对齐 test_parsed_with_warnings 的隔离模式）。"""
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_math_warnings_merge.db")
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


def _receipt_data(math_warnings):
    return ReceiptData(
        doc_form=DocForm.PRINTED,
        vendor="测试供应商",
        date="2026-09-19",
        items=[ReceiptItem(name="菜心", qty=1, unit="斤", unit_price=100, amount=100)],
        total=999,
        payment_marked=True,
        confidence=0.9,
        math_warnings=list(math_warnings),
    )


def _result(math_problems):
    return {
        "status": "parsed_with_warnings",
        "math_problems": list(math_problems),
        "math_warnings": list(math_problems),
        "raw": "",
        "use_grey": 0,
        "audit_result": {},
    }


def test_merge_orders_problems_first_then_warnings():
    """纯函数层：先 problems 后 warnings，顺序稳定且去重。"""
    merged = receipt_utils._merge_math_warnings(
        [_GATE_WARN, _GATE_WARN, "契约校验失败: items 为空"],
        ["契约校验失败: items 为空", _HUAMA_WARN],
    )
    assert merged == [_GATE_WARN, "契约校验失败: items 为空", _HUAMA_WARN]


def test_save_parsed_data_persists_both_warning_kinds():
    """落库：门禁警告与整单花码警告都要进 math_warnings_json，且无重复。"""
    rid = db.create_receipt(supplier_name="测试供应商", status="uploading")
    data = _receipt_data([_HUAMA_WARN])

    receipt_utils.save_parsed_data(rid, data, _result([_GATE_WARN]))

    row = db.get_receipt_row(rid)
    warnings = json.loads(row.math_warnings_json or "[]")
    assert warnings == [_GATE_WARN, _HUAMA_WARN], warnings


def test_save_parsed_data_dedupes_overlapping_warning():
    """两列同一条警告时只保留一条，且位置在门禁段（首次出现序）。"""
    rid = db.create_receipt(supplier_name="测试供应商", status="uploading")
    data = _receipt_data([_GATE_WARN, _HUAMA_WARN])

    receipt_utils.save_parsed_data(rid, data, _result([_GATE_WARN]))

    row = db.get_receipt_row(rid)
    warnings = json.loads(row.math_warnings_json or "[]")
    assert warnings == [_GATE_WARN, _HUAMA_WARN], warnings


def test_counterproof_without_merge_drops_huama_warning(monkeypatch):
    """反证：退回「只取 math_problems」的旧行为时，花码警告必然丢失 → 主断言失败。"""
    monkeypatch.setattr(
        receipt_utils, "_merge_math_warnings",
        lambda problems, warnings: list(problems or []),
    )
    rid = db.create_receipt(supplier_name="测试供应商", status="uploading")
    data = _receipt_data([_HUAMA_WARN])

    receipt_utils.save_parsed_data(rid, data, _result([_GATE_WARN]))

    row = db.get_receipt_row(rid)
    warnings = json.loads(row.math_warnings_json or "[]")
    assert _HUAMA_WARN not in warnings
    assert warnings != [_GATE_WARN, _HUAMA_WARN]
