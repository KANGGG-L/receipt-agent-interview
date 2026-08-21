# -*- coding: utf-8 -*-
"""B-P0-1 专项：SKU 流水号未剥离导致库存爆炸 — 回归与边界覆盖。

覆盖：
- 正则剥离 _\\d{10} 后缀及核心词归一（有机菜心_1787140420→有机菜心、本地新鲜菜心_1787140411→本地新鲜菜心）
- demo/app/services/receipt_utils.py 与 ai_registry/tools/smart_splitter/v1_2_0_multi_pack.py 双侧归一
- demo/app/api_inventory.py 创建/合并 SKU 时归一去重
- 10司馬斤 / 5L*2樽 / 复合包装+流水号混杂 等边界
- 迁移存量脏数据 sku07 合并到 sku03 后 GET /api/inventory 仅 1 条
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "demo"))

import pytest
import re

# 隔离 DB
os.environ.setdefault("DB_PATH", "/tmp/receipt_demo_test_b_p0_1.db")
if os.path.exists("/tmp/receipt_demo_test_b_p0_1.db"):
    os.remove("/tmp/receipt_demo_test_b_p0_1.db")

from ai_registry.tools.smart_splitter.v1_2_0_multi_pack import SmartSplitterTool


def test_serial_suffix_regex_stripping():
    """验证显式 _\\d{10} 后缀用正则精确剥离，核心词归一。"""
    tool = SmartSplitterTool()
    # 检查正则常量存在
    assert hasattr(tool, "SERIAL_SUFFIX_RE")
    assert tool.SERIAL_SUFFIX_RE.pattern == r"_(\d{10})$"
    cases = [
        ("有机菜心_1787140420", "有机菜心", "1787140420"),
        ("本地新鲜菜心_1787140411", "本地新鲜菜心", "1787140411"),
        ("大豆油 5L*2樽_1787140420", "大豆油 5L", "1787140420"),
    ]
    for raw, exp_name, exp_code in cases:
        res = tool.execute(raw)
        assert res["item_name"] == exp_name, f"{raw} -> {res['item_name']} != {exp_name}"
        assert res["item_code"] == exp_code
        # canonical helper
        assert tool.canonical_name(raw) == exp_name
        clean, code = tool.strip_serial_suffix(raw)
        # strip_serial_suffix 仅处理 _\\d{10}
        if re.search(r"_\d{10}$", raw):
            assert code == exp_code
            assert clean == exp_name or "大豆油" in clean

def test_receipt_utils_canonical_helpers():
    """demo/app/services/receipt_utils.py 双侧归一：canonical_sku_name / _normalize_sku_name / strip_serial_suffix."""
    from demo.app.services.receipt_utils import canonical_sku_name, strip_serial_suffix, _normalize_sku_name
    assert strip_serial_suffix("有机菜心_1787140420") == "有机菜心"
    assert strip_serial_suffix("本地新鲜菜心_1787140411") == "本地新鲜菜心"
    assert canonical_sku_name("有机菜心_1787140420") == "有机菜心"
    assert canonical_sku_name("本地新鲜菜心_1787140411") == "本地新鲜菜心"
    # _normalize_sku_name 应剥离后缀并保留核心词
    assert _normalize_sku_name("有机菜心_1787140420") == "有机菜心"
    assert _normalize_sku_name("本地新鲜菜心_1787140411") == "本地新鲜菜心"
    # 不应误删合法 10司馬斤 等港式单位
    assert "司馬斤" in _normalize_sku_name("本地新鲜菜心 10司馬斤") or _normalize_sku_name("本地新鲜菜心 10司馬斤") == "本地新鲜菜心 10司馬斤"

def test_10_sima_jin_protected_and_multi_pack():
    """10司馬斤 受保护不被误剥离；5L*2樽 等复合包装精确解耦且叠加流水号仍归一。"""
    tool = SmartSplitterTool()
    # 10司馬斤 场景：不应被当作流水号或规格剥离
    res = tool.execute("本地新鲜菜心 10司馬斤")
    # 10司馬斤 不是流水号，品名应保持或至少包含核心词
    assert "本地新鲜菜心" in res["item_name"]
    assert res["item_code"] == ""
    # 确保 10 不被误删为数量剥离（应保持原品名或视为整体）
    # 若含 10司馬斤且无 * 乘数，则 quantity 应为 1.0
    assert res["quantity"] == 1.0

    # 5L*2樽 标准解耦
    res2 = tool.execute("大豆油 5L*2樽")
    assert res2["item_name"] == "大豆油 5L"
    assert res2["quantity"] == 2.0
    assert res2["unit"] == "樽"

    # 5L*2樽 叠加流水号：剥离后仍能解耦
    res3 = tool.execute("大豆油 5L*2樽_1787140420")
    assert res3["item_name"] == "大豆油 5L"
    assert res3["quantity"] == 2.0
    assert res3["unit"] == "樽"
    assert res3["item_code"] == "1787140420"

    # 多种港式多包装
    cases = [
        ("可口可乐 330ml*24罐", "可口可乐 330ml", 24.0, "罐"),
        ("急冻牛肋条 2kg*5包", "急冻牛肋条 2kg", 5.0, "包"),
        ("鲜鸡蛋 30只*3盘", "鲜鸡蛋 30只", 3.0, "盘"),
        ("李锦记旧庄蚝油 510g*12樽", "李锦记旧庄蚝油 510g", 12.0, "樽"),
    ]
    for raw, exp_name, exp_qty, exp_unit in cases:
        r = tool.execute(raw)
        assert r["item_name"] == exp_name
        assert r["quantity"] == exp_qty
        assert r["unit"] == exp_unit

def test_api_inventory_create_normalizes():
    """api_inventory 创建 SKU 时强制归一：流水号变体应触发去重/冲突。"""
    from demo.app import db as _db
    from fastapi.testclient import TestClient
    from demo.app.main import app
    from demo.app.auth import ROLE_RANK

    client = TestClient(app)
    # 清理相关 SKU
    for s in _db.list_skus(include_inactive=True):
        if s.name in ("测试菜心", "测试菜心_1787140420", "测试菜心_1787140421"):
            _db.delete_sku(s.id)
            # 物理删除后补一次彻底清理（active=0 也删）
            sess = _db.get_session()
            try:
                row = sess.get(_db._SkuRow, s.id)
                if row:
                    sess.delete(row)
                    sess.commit()
            finally:
                sess.close()

    # 创建干净主 SKU
    r1 = client.post("/api/inventory/skus", json={"name": "测试菜心", "category": "蔬菜", "base_unit": "斤"}, headers={"X-Role": "staff"})
    assert r1.status_code == 200
    assert r1.json()["status"] == "success"
    clean_id = r1.json()["id"]

    # 再以流水号变体创建，应被识别为同名冲突（归一后冲突）
    r2 = client.post("/api/inventory/skus", json={"name": "测试菜心_1787140420", "category": "蔬菜", "base_unit": "斤"}, headers={"X-Role": "staff"})
    assert r2.json()["status"] == "error"
    assert r2.json()["code"] == "SKU_NAME_CONFLICT"
    # 校验 inventory 列表仅有一条测试菜心
    r3 = client.get("/api/inventory", params={"q": "测试菜心"}, headers={"X-Role": "owner"})
    names = [x["name"] for x in r3.json()["data"]]
    assert names.count("测试菜心") == 1
    assert "测试菜心_1787140420" not in names

    # 清理
    _db.delete_sku(clean_id)
    sess = _db.get_session()
    try:
        row = sess.get(_db._SkuRow, clean_id)
        if row:
            sess.delete(row)
            sess.commit()
    finally:
        sess.close()

def test_migration_sku07_to_sku03():
    """迁移存量脏数据：sku07 本地新鲜菜心_1787140411 合并到 sku03 本地新鲜菜心，验证仅 1 条活跃。"""
    from demo.app import db as _db
    # 清理后重建现场
    sess = _db.get_session()
    try:
        for row in sess.query(_db._SkuRow).filter(_db._SkuRow.name.in_(["本地新鲜菜心", "本地新鲜菜心_1787140411"])).all():
            sess.delete(row)
        sess.commit()
    finally:
        sess.close()
    id_clean, _ = _db.create_sku("本地新鲜菜心", category="蔬菜", base_unit="斤")
    id_dirty, _ = _db.create_sku("本地新鲜菜心_1787140411", category="蔬菜", base_unit="斤")
    # 模拟库存与流水
    _db.apply_stock_log(sku_id=id_clean, name="本地新鲜菜心", qty=10, unit="斤", amount=100, vendor="供应商A", date="2026-08-10", receipt_id=None, kind="in")
    _db.apply_stock_log(sku_id=id_dirty, name="本地新鲜菜心_1787140411", qty=5, unit="斤", amount=50, vendor="供应商A", date="2026-08-11", receipt_id=None, kind="in")
    # 执行合并（显式 sku07->sku03）
    result, err = _db.merge_skus(id_clean, [id_dirty])
    assert err is None
    assert result["primary_name"] == "本地新鲜菜心"
    # 再次走全量 dedup 幂等
    reports = _db.deduplicate_skus_by_canonical()
    # 此时应无新增
    # 验证活跃仅 1 条
    active = [s for s in _db.list_skus(include_inactive=False) if s.name == "本地新鲜菜心"]
    assert len(active) == 1
    assert active[0].current_stock == 15.0  # 10+5 归集

    # API 视角验证
    from fastapi.testclient import TestClient
    from demo.app.main import app
    client = TestClient(app)
    resp = client.get("/api/inventory", params={"q": "本地新鲜菜心"}, headers={"X-Role": "owner"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len([x for x in data if x["name"] == "本地新鲜菜心"]) == 1
    assert not any(x["name"] == "本地新鲜菜心_1787140411" for x in data)
