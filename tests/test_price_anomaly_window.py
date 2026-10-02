# -*- coding: utf-8 -*-
"""WS3 回归：价格异动的真 30 天窗口与告警方向。

why：旧实现把 avg_30d 算成**全历史**算术均价，与前端「较 30 日均价」文案名实不符；
       WS3 改为只纳入 date >= today - 30 天的样本。窗口边界（恰好 30 天前要纳入、
       31 天前要排除）、样本不足不告警、仅向上告警与 4 元组返回形状，都是
       既有消费方（api_inventory / api_export / review_chain）的硬约束。

覆盖：
1. 31 天前的点不纳入、恰好 30 天前的点纳入、今天的点纳入
2. avg_30d == 窗口内算术均价（不是全历史均价）
3. 窗口内有效样本 < 2 时不告警（vs 归零）
4. 仅向上告警：vs 为负时不 is_anomaly（即便跌幅超过阈值）
5. 返回值仍是 4 元组 (vs, is_anomaly, avg_30d, latest)

全部离线假数据：模块级独立临时 DB，日期相对 today 动态计算，零外部调用。
"""

import atexit
import datetime
import importlib
import os
import shutil
import sys
import tempfile
import uuid

_TMP = tempfile.mkdtemp(prefix="price_anomaly_window_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_anomaly_window.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo"))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402
from app.services.price_anomaly import (  # noqa: E402
    _parse_date,
    compute_vs_avg_and_anomaly,
)


def _fresh_db():
    os.environ["DB_PATH"] = os.path.join(_TMP, f"pw_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    return db


def _sku(_db, name):
    sku_id, err = _db.create_sku(name, category="食材", base_unit="斤")
    assert sku_id, f"建 SKU 失败：{err}"
    return sku_id


def _in(_db, sku_id, unit_price, date, qty=1.0, vendor="測試供應商"):
    """写一条进货流水（amount = 单价 * 数量），与 approve 入账同形态。"""
    _db.apply_stock_log(
        sku_id=sku_id, name="測試食材", qty=qty, unit="斤",
        amount=round(unit_price * qty, 4), vendor=vendor, date=date,
        receipt_id=None, kind="in", tenant_id="default")


def _days_ago(n):
    return (datetime.date.today() - datetime.timedelta(days=n)).isoformat()


# -------------------------------------------------------------
# 1. 窗口边界：31 天前排除、恰好 30 天前纳入、今天纳入
# -------------------------------------------------------------
def test_window_boundary_31d_excluded_30d_included():
    _db = _fresh_db()
    sku = _sku(_db, "邊界菜心")
    _in(_db, sku, 100.0, _days_ago(31))   # 窗口外：不得参与
    _in(_db, sku, 10.0, _days_ago(30))    # 恰好下界：纳入（闭区间）
    _in(_db, sku, 20.0, _days_ago(0))     # 今天：纳入

    vs, is_anomaly, avg_30d, latest = compute_vs_avg_and_anomaly(
        sku, threshold_pct=10.0)

    full_history_avg = round((100.0 + 10.0 + 20.0) / 3, 2)
    assert avg_30d == 15.0, f"avg_30d 应只含窗口内 (10,20)：{avg_30d}"
    assert avg_30d != full_history_avg, "avg_30d 不得退化为全历史均价"
    assert latest == 20.0, latest
    assert vs == 33.3 and is_anomaly is True, (vs, is_anomaly)
    assert is_anomaly is True


# -------------------------------------------------------------
# 2. avg_30d == 窗口内算术均价（与手工口径一致）
# -------------------------------------------------------------
def test_avg_30d_equals_window_arithmetic_mean():
    _db = _fresh_db()
    sku = _sku(_db, "均價腩肉")
    window_prices = [7.0, 13.0, 28.5, 6.5]     # 全部落在窗口内
    for i, p in enumerate(window_prices):
        _in(_db, sku, p, _days_ago(i))
    _in(_db, sku, 999.0, _days_ago(45))        # 窗口外干扰点
    _in(_db, sku, 0.5, _days_ago(60))          # 窗口外干扰点

    _vs, _is_anomaly, avg_30d, _latest = compute_vs_avg_and_anomaly(
        sku, threshold_pct=10.0)
    manual = round(sum(window_prices) / len(window_prices), 2)
    assert avg_30d == manual == 13.75, (avg_30d, manual)


# -------------------------------------------------------------
# 3. 窗口内样本 < 2 不告警
# -------------------------------------------------------------
def test_single_sample_in_window_does_not_alert():
    _db = _fresh_db()
    sku = _sku(_db, "單樣本豆腐")
    _in(_db, sku, 10.0, _days_ago(31))   # 窗口外：不计数
    _in(_db, sku, 20.0, _days_ago(0))    # 窗口内唯一有效样本

    vs, is_anomaly, avg_30d, latest = compute_vs_avg_and_anomaly(
        sku, threshold_pct=10.0)
    assert (vs, is_anomaly) == (0.0, False), (vs, is_anomaly)
    assert avg_30d == 20.0 and latest == 20.0, (avg_30d, latest)


# -------------------------------------------------------------
# 4. 仅向上告警：下跌不标红（即便跌幅远超阈值）
# -------------------------------------------------------------
def test_only_upward_alerts_never_downward():
    _db = _fresh_db()
    sku = _sku(_db, "下跌唐生菜")
    _in(_db, sku, 10.0, _days_ago(30))
    _in(_db, sku, 8.0, _days_ago(0))

    # 阈值故意设 1.0：若实现按 |vs| 判定，-11.1% 会误告警
    vs, is_anomaly, avg_30d, latest = compute_vs_avg_and_anomaly(
        sku, threshold_pct=1.0)
    assert vs < 0 and avg_30d == 9.0 and latest == 8.0, (vs, avg_30d, latest)
    assert is_anomaly is False, f"下跌不得告警：vs={vs}"


# -------------------------------------------------------------
# 5. 返回形状仍是 4 元组
# -------------------------------------------------------------
def test_returns_four_tuple():
    _db = _fresh_db()
    sku = _sku(_db, "形狀雞蛋")
    _in(_db, sku, 2.5, _days_ago(1))
    _in(_db, sku, 3.5, _days_ago(0))

    ret = compute_vs_avg_and_anomaly(sku, threshold_pct=10.0)
    assert isinstance(ret, tuple) and len(ret) == 4, ret
    vs, is_anomaly, avg_30d, latest = ret
    assert avg_30d == 3.0 and latest == 3.5
    assert is_anomaly is True

    # 无任何流水：仍返回 4 元组（消费方无需空值分支）
    empty_sku = _sku(_db, "無流水商品")
    assert compute_vs_avg_and_anomaly(empty_sku, threshold_pct=10.0) == (
        0.0, False, 0.0, 0.0)


# -------------------------------------------------------------
# 6. 日期解析：非法日期样本不纳入窗口（不误算）
# -------------------------------------------------------------
def test_unparseable_date_sample_excluded_from_window():
    _db = _fresh_db()
    sku = _sku(_db, "亂碼日期白菜")
    _in(_db, sku, 50.0, "not-a-date")      # 无法解析 → 不纳入窗口
    _in(_db, sku, 10.0, _days_ago(5))
    _in(_db, sku, 20.0, _days_ago(0))

    assert _parse_date("not-a-date") is None
    _vs, _is_anomaly, avg_30d, _latest = compute_vs_avg_and_anomaly(
        sku, threshold_pct=10.0)
    assert avg_30d == 15.0, avg_30d


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
