# -*- coding: utf-8 -*-
"""WS2 回归：db.vendor_price_history（供应商 × SKU 历史进货单价）。

why：复核侧的「供应商历史报价区间」必须只取**同一供应商**的进货流水，且单价由
       `amount / qty` 推导、剔除无有效单价的异常行。若这里退化成读
       `sku.last_unit_price`（只有"最新一笔"，无法构成区间），或按模糊匹配把别家
       供应商的价混进来，前端区间提示会给出误导性的对照。

覆盖：
1. 只取同一 SKU × 同一 vendor（原字符串精确匹配；查询串前后空格先 strip）
2. qty <= 0 或 amount <= 0 的行剔除；单价 = amount / qty
3. 跨租户不可见（显式 tenant_id 时按流水租户过滤）
4. **不**回退到 sku.last_unit_price（造 last_unit_price 与流水不符的数据证明）
5. 空 vendor / 未知供应商 → []

全部离线假数据：模块级独立临时 DB，零外部调用、零 live 写入。
"""

import atexit
import importlib
import os
import shutil
import sys
import tempfile
import uuid

_TMP = tempfile.mkdtemp(prefix="vendor_price_history_db_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_vendor_price_history.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo"))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402


def _fresh_db():
    os.environ["DB_PATH"] = os.path.join(_TMP, f"vph_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    return db


def _sku(_db, name, tenant="default"):
    sku_id, err = _db.create_sku(name, category="食材", base_unit="斤",
                                 tenant_id=tenant)
    assert sku_id, f"建 SKU 失败：{err}"
    return sku_id


def _log(_db, sku_id, qty, amount, vendor, date, tenant="default", kind="in"):
    _db.apply_stock_log(
        sku_id=sku_id, name="測試食材", qty=qty, unit="斤", amount=amount,
        vendor=vendor, date=date, receipt_id=None, kind=kind,
        tenant_id=tenant)


# -------------------------------------------------------------
# 1. 只取同一供应商（原字符串精确匹配 + 查询串 strip）
# -------------------------------------------------------------
def test_only_same_vendor_rows_and_vendor_stripped():
    _db = _fresh_db()
    sku = _sku(_db, "供應商匹配菜心")
    _log(_db, sku, 1.0, 10.0, "佳記欄", "2026-08-01")
    _log(_db, sku, 1.0, 20.0, "佳記欄", "2026-08-02")
    _log(_db, sku, 1.0, 99.0, "別家供應商", "2026-08-03")   # 别家：不得参与

    got = _db.vendor_price_history(sku, "佳記欄", tenant_id="default")
    assert [round(p, 2) for _d, p, _q, _r in got] == [10.0, 20.0], got

    # 查询串前后空格先 strip，仍精确匹配
    padded = _db.vendor_price_history(sku, "  佳記欄  ", tenant_id="default")
    assert [round(p, 2) for _d, p, _q, _r in padded] == [10.0, 20.0], padded

    # 精确匹配：子串 / 大小写不同 / 未知供应商都不匹配
    assert _db.vendor_price_history(sku, "佳記", tenant_id="default") == []
    assert _db.vendor_price_history(
        sku, "從未進過貨的商戶", tenant_id="default") == []
    assert _db.vendor_price_history(sku, "   ", tenant_id="default") == []
    assert _db.vendor_price_history(sku, "", tenant_id="default") == []


# -------------------------------------------------------------
# 2. 单价 = amount / qty；qty<=0 或 amount<=0 的行剔除
# -------------------------------------------------------------
def test_unit_price_derived_and_nonpositive_rows_dropped():
    _db = _fresh_db()
    sku = _sku(_db, "單價推導豆腐")
    # 正常行：2 斤 60 元 → 30.0/斤
    _log(_db, sku, 2.0, 60.0, "佳記欄", "2026-08-01")
    # 正常行：4 斤 10 元 → 2.5/斤（确认不是取整/取均价）
    _log(_db, sku, 4.0, 10.0, "佳記欄", "2026-08-02")
    # 异常行：qty=0 / qty<0 / amount=0 —— 单价无意义，全部剔除
    _log(_db, sku, 0.0, 50.0, "佳記欄", "2026-08-03")
    _log(_db, sku, -1.0, 10.0, "佳記欄", "2026-08-04")
    _log(_db, sku, 3.0, 0.0, "佳記欄", "2026-08-05")

    got = _db.vendor_price_history(sku, "佳記欄", tenant_id="default")
    assert len(got) == 2, got
    assert [(d, round(p, 4), q) for d, p, q, _r in got] == [
        ("2026-08-01", 30.0, 2.0),
        ("2026-08-02", 2.5, 4.0),
    ], got


# -------------------------------------------------------------
# 3. 跨租户不可见
# -------------------------------------------------------------
def test_cross_tenant_invisible():
    _db = _fresh_db()
    sku_t2 = _sku(_db, "他租戶商品", tenant="t2")
    _log(_db, sku_t2, 1.0, 12.0, "佳記欄", "2026-08-01", tenant="t2")

    # t2 视角可见
    own = _db.vendor_price_history(sku_t2, "佳記欄", tenant_id="t2")
    assert len(own) == 1 and round(own[0][1], 2) == 12.0, own
    # default 视角看不到 t2 的流水
    assert _db.vendor_price_history(
        sku_t2, "佳記欄", tenant_id="default") == []

    # 反向：default 流水不被 t2 读到
    sku_d = _sku(_db, "默認租戶商品")
    _log(_db, sku_d, 1.0, 7.0, "佳記欄", "2026-08-01")
    assert _db.vendor_price_history(
        sku_d, "佳記欄", tenant_id="t2") == []
    assert len(_db.vendor_price_history(
        sku_d, "佳記欄", tenant_id="default")) == 1


# -------------------------------------------------------------
# 4. 不回退读 sku.last_unit_price
# -------------------------------------------------------------
def test_no_fallback_to_sku_last_unit_price():
    _db = _fresh_db()
    sku = _sku(_db, "凍肉無流水")
    _log(_db, sku, 2.0, 60.0, "佳記欄", "2026-08-01")  # 单价 30

    # 人为把 SKU 上的「最新价」改成与流水不符
    session = _db.get_session()
    try:
        row = session.get(_db._SkuRow, sku)
        assert round(float(row.last_unit_price or 0), 2) == 30.0
        row.last_unit_price = 999.0
        session.commit()
    finally:
        session.close()

    got = _db.vendor_price_history(sku, "佳記欄", tenant_id="default")
    assert len(got) == 1 and round(got[0][1], 2) == 30.0, got

    # 该供应商毫无流水时必须是空列表，绝不回退拿 last_unit_price 造出"区间"
    assert _db.vendor_price_history(
        sku, "別家供應商", tenant_id="default") == []
    assert _db.vendor_price_history(
        _sku(_db, "無任何流水的商品"), "佳記欄", tenant_id="default") == []


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
