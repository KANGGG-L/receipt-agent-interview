# -*- coding: utf-8 -*-
"""set_golden_sample 幂等性回归测试。

why: main.py 的启动自愈每次 uvicorn --reload 重启都会经 evalset_linkage 调
set_golden_sample，若该函数无条件写 updated_at，黄金样本的时间戳就会被无业务含义地刷新。
这里用隔离临时库断言：值未变时不写库、值真变时才刷新 updated_at。
"""

import os
import sys

import pytest

DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if DEMO_DIR not in sys.path:
    sys.path.insert(0, DEMO_DIR)

from app import db


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """把 DB_PATH 指向临时库，避免触碰 live demo 数据。"""
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_golden_idempotent.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    db.DB_PATH = test_db_path
    db._make_engine()
    yield
    db.DB_PATH = old_db_path
    db._make_engine()


def _read(row_id):
    s = db.get_session()
    try:
        row = s.get(db._ReceiptRow, int(row_id))
        return row.is_golden_sample, row.updated_at
    finally:
        s.close()


def _force(row_id, is_golden_sample, updated_at):
    """直接改写标记与时间戳，构造可控的初始状态。"""
    s = db.get_session()
    try:
        row = s.get(db._ReceiptRow, int(row_id))
        row.is_golden_sample = is_golden_sample
        row.updated_at = updated_at
        s.commit()
    finally:
        s.close()


def test_second_mark_does_not_touch_updated_at():
    """(a) 已标记为黄金样本后再次标记，不应刷新 updated_at。"""
    rid = db.create_receipt(supplier_name="Idempotent A")
    _force(rid, 0, "2020-01-01T00:00:00")

    assert db.set_golden_sample(rid, 1, tenant_id="default") is True
    flag1, ts1 = _read(rid)
    assert flag1 == 1
    # 首次标记值确实变化 -> updated_at 被刷新
    assert ts1 != "2020-01-01T00:00:00"

    _force(rid, 1, "2021-02-02T00:00:00")
    assert db.set_golden_sample(rid, 1, tenant_id="default") is True
    flag2, ts2 = _read(rid)
    assert flag2 == 1
    # 值相同 -> 不写库、时间戳保持原样
    assert ts2 == "2021-02-02T00:00:00"


def test_second_unmark_does_not_touch_updated_at():
    """(a) 已取消标记后再次取消，同样不应刷新 updated_at。"""
    rid = db.create_receipt(supplier_name="Idempotent B")
    _force(rid, 0, "2022-03-03T00:00:00")

    assert db.set_golden_sample(rid, 0, tenant_id="default") is True
    flag, ts = _read(rid)
    assert flag == 0
    assert ts == "2022-03-03T00:00:00"


def test_flag_change_still_refreshes_updated_at():
    """(b) 值实际变化时必须仍然刷新 updated_at。"""
    rid = db.create_receipt(supplier_name="Idempotent C")
    _force(rid, 0, "2023-04-04T00:00:00")

    assert db.set_golden_sample(rid, 1, tenant_id="default") is True
    flag, ts = _read(rid)
    assert flag == 1
    assert ts != "2023-04-04T00:00:00"

    # 反向：1 -> 0 也必须刷新
    _force(rid, 1, "2024-05-05T00:00:00")
    assert db.set_golden_sample(rid, 0, tenant_id="default") is True
    flag, ts = _read(rid)
    assert flag == 0
    assert ts != "2024-05-05T00:00:00"


def test_none_value_treated_as_zero():
    """历史空值（None）按未标记处理：设 0 不写库，设 1 才写库。"""
    rid = db.create_receipt(supplier_name="Idempotent D")
    _force(rid, None, "2025-06-06T00:00:00")

    assert db.set_golden_sample(rid, 0, tenant_id="default") is True
    flag, ts = _read(rid)
    assert flag is None or flag == 0
    assert ts == "2025-06-06T00:00:00"

    assert db.set_golden_sample(rid, 1, tenant_id="default") is True
    flag, ts = _read(rid)
    assert flag == 1
    assert ts != "2025-06-06T00:00:00"


def test_missing_receipt_and_cross_tenant_still_return_false():
    """既有行为不变：单据不存在或跨租户返回 False。"""
    rid = db.create_receipt(supplier_name="Idempotent E")
    assert db.set_golden_sample(99999999, 1, tenant_id="default") is False
    assert db.set_golden_sample(rid, 1, tenant_id="other_tenant") is False
