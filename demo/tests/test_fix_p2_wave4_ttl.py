# -*- coding: utf-8 -*-
"""VendorMemory P2 Wave 4: 按 Kind 分级 TTL 体系测试套件 (T20 / AC-12).

分级策略：
- price_range: 90 天
- unit: 180 天
- alias: 0 天（永不过期）
- layout: 0 天（永不过期）
- Fail-Open: 读故障时不过期 + warning 警示
"""

from datetime import datetime, timedelta
import os
import shutil
import sqlite3
import sys
import tempfile
from unittest.mock import patch
import pytest

_DEMO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

from app.services import settings_service, memory_facts
from app.services.memory_facts import MemoryFact
import app.db as db


@pytest.fixture
def isolated_env():
    """隔离的临时数据库环境。"""
    tmp = tempfile.mkdtemp(prefix="vm_w4_ttl_")
    db_file = os.path.join(tmp, "test_ttl.db")
    rag_dir = os.path.join(tmp, "test_rag")
    old_db = os.environ.get("DB_PATH")
    old_rag = os.environ.get("RAG_DIR")
    old_db_path = getattr(db, "DB_PATH", None)

    os.environ["DB_PATH"] = db_file
    os.environ["RAG_DIR"] = rag_dir
    db.DB_PATH = db_file
    db._make_engine()

    yield {"tmp": tmp, "db_file": db_file, "rag_dir": rag_dir}

    if old_db_path is not None:
        db.DB_PATH = old_db_path
        db._make_engine()
    if old_db is not None:
        os.environ["DB_PATH"] = old_db
    else:
        os.environ.pop("DB_PATH", None)
    if old_rag is not None:
        os.environ["RAG_DIR"] = old_rag
    else:
        os.environ.pop("RAG_DIR", None)
    shutil.rmtree(tmp, ignore_errors=True)


def test_t20_kind_ttl_registered_defaults():
    """验证各 kind 的 TTL 缺省登记项。"""
    defaults = settings_service.SETTINGS_DEFAULTS
    assert defaults["memory_ttl_days_price_range"] == 90
    assert defaults["memory_ttl_days_unit"] == 180
    assert defaults["memory_ttl_days_alias"] == 0
    assert defaults["memory_ttl_days_layout"] == 0
    assert defaults["memory_ttl_days"] == 0

    assert db.memory_ttl_days("price_range") == 90
    assert db.memory_ttl_days("unit") == 180
    assert db.memory_ttl_days("alias") == 0
    assert db.memory_ttl_days("layout") == 0
    assert db.memory_ttl_days("") == 0


def test_t20_price_range_90d_expiration(isolated_env):
    """验证 price_range 记忆在第 91 天过期，第 89 天仍然有效。"""
    now = datetime.now()
    created_91d = (now - timedelta(days=91)).isoformat()
    created_89d = (now - timedelta(days=89)).isoformat()

    conn = sqlite3.connect(isolated_env["db_file"])
    conn.execute(
        "INSERT INTO vendor_memory (memory_id, vendor, fact_kind, fact_key, fact_value, notes, status, created_at, tenant_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("p_old", "測試供應商T", "price_range", "急凍牛肉", "40.00-50.00", "notes", "active", created_91d, "default")
    )
    conn.execute(
        "INSERT INTO vendor_memory (memory_id, vendor, fact_kind, fact_key, fact_value, notes, status, created_at, tenant_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("p_new", "測試供應商T", "price_range", "急凍豬肉", "30.00-40.00", "notes", "active", created_89d, "default")
    )
    conn.commit()
    conn.close()

    # 查库并使用 _filter_memory_rows_for_read 过滤
    rows = db.list_vendor_memory(vendor="測試供應商T", tenant_id="default")
    active_mids = [r["memory_id"] for r in rows]

    assert "p_new" in active_mids, "第 89 天的 price_range 必须未过期"
    assert "p_old" not in active_mids, "第 91 天的 price_range 必须过期并被过滤"


def test_t20_unit_180d_expiration(isolated_env):
    """验证 unit 记忆在第 181 天过期，第 179 天仍然有效。"""
    now = datetime.now()
    created_181d = (now - timedelta(days=181)).isoformat()
    created_179d = (now - timedelta(days=179)).isoformat()

    conn = sqlite3.connect(isolated_env["db_file"])
    conn.execute(
        "INSERT INTO vendor_memory (memory_id, vendor, fact_kind, fact_key, fact_value, notes, status, created_at, tenant_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("u_old", "測試供應商U", "unit", "unit:鸡蛋", "箱", "notes", "active", created_181d, "default")
    )
    conn.execute(
        "INSERT INTO vendor_memory (memory_id, vendor, fact_kind, fact_key, fact_value, notes, status, created_at, tenant_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("u_new", "測試供應商U", "unit", "unit:鸭蛋", "箱", "notes", "active", created_179d, "default")
    )
    conn.commit()
    conn.close()

    rows = db.list_vendor_memory(vendor="測試供應商U", tenant_id="default")
    active_mids = [r["memory_id"] for r in rows]

    assert "u_new" in active_mids, "第 179 天的 unit 必须未过期"
    assert "u_old" not in active_mids, "第 181 天的 unit 必须过期并被过滤"


def test_t20_alias_and_layout_never_expire(isolated_env):
    """验证 alias 与 layout 即使在第 400 天依然存活不过期。"""
    now = datetime.now()
    created_400d = (now - timedelta(days=400)).isoformat()

    conn = sqlite3.connect(isolated_env["db_file"])
    conn.execute(
        "INSERT INTO vendor_memory (memory_id, vendor, fact_kind, fact_key, fact_value, notes, status, created_at, tenant_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("a_400", "測試供應商L", "alias", "alias:有機菜心", "有机菜心", "notes", "active", created_400d, "default")
    )
    conn.execute(
        "INSERT INTO vendor_memory (memory_id, vendor, fact_kind, fact_key, fact_value, notes, status, created_at, tenant_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("l_400", "測試供應商L", "layout", "layout:doc_form", "printed_delivery_note", "notes", "active", created_400d, "default")
    )
    conn.commit()
    conn.close()

    rows = db.list_vendor_memory(vendor="測試供應商L", tenant_id="default")
    active_mids = [r["memory_id"] for r in rows]

    assert "a_400" in active_mids, "400 天前的 alias 别名绝对不应过期"
    assert "l_400" in active_mids, "400 天前的 layout 版式绝对不应过期"


def test_t20_save_memory_fact_calculates_valid_to(isolated_env):
    """验证 save_memory_fact 依据 kind 自动计算并写入 valid_to。"""
    sup_id, _ = db.create_supplier("測試供應商W", tenant_id="default")

    # 1. 写入 price_range
    f_price = MemoryFact(
        kind=memory_facts.KIND_PRICE_RANGE,
        fact_key="排骨",
        value="50.00-60.00",
        supplier_id=str(sup_id),
        tenant_id="default"
    )
    res_p = db.save_memory_fact("default", "測試供應商W", f_price.fact_key, notes="排骨价格", fact=f_price)
    mid_p = res_p["memory_id"]

    # 2. 写入 alias
    f_alias = MemoryFact(
        kind=memory_facts.KIND_ALIAS,
        fact_key="alias:排骨粒",
        value="标准排骨",
        supplier_id=str(sup_id),
        tenant_id="default"
    )
    res_a = db.save_memory_fact("default", "測試供應商W", f_alias.fact_key, notes="排骨别名", fact=f_alias)
    mid_a = res_a["memory_id"]

    # 检查数据库中写入的 valid_to
    conn = sqlite3.connect(isolated_env["db_file"])
    row_p = conn.execute("SELECT valid_to FROM vendor_memory WHERE memory_id = ?", (mid_p,)).fetchone()
    row_a = conn.execute("SELECT valid_to FROM vendor_memory WHERE memory_id = ?", (mid_a,)).fetchone()
    conn.close()

    assert row_p is not None and row_p[0], "price_range 写入时必须携带 valid_to"
    vt_p = datetime.fromisoformat(row_p[0])
    # 应当在当前时间的 ~90 天之后
    delta_days = (vt_p - datetime.now()).days
    assert 88 <= delta_days <= 92, f"price_range valid_to 需在 ~90 天后，实得: {delta_days}"

    assert row_a is not None and row_a[0] == "", "alias 写入时 valid_to 必须为空串（不过期）"


def test_t20_fail_open_on_settings_failure(isolated_env, caplog):
    """验证 Fail-Open 纪律：settings 读取故障时回退为 0（不过期）并输出 warning 警示。"""
    with patch("app.services.settings_service.get_int", side_effect=RuntimeError("DB disk I/O error")):
        ttl_val = db.memory_ttl_days("price_range")
        assert ttl_val == 0, "读取故障必须回退为 0"

    # 验证输出告警日志
    assert any("memory_ttl_days 读取失败" in rec.message for rec in caplog.records)


def test_t20_mutation_counterproof_alias_long_lived(isolated_env):
    """突变体对抗：若将 alias 误设为 90 天过期，断言必须捕捉到过期并翻红。"""
    now = datetime.now()
    created_100d = (now - timedelta(days=100)).isoformat()

    conn = sqlite3.connect(isolated_env["db_file"])
    conn.execute(
        "INSERT INTO vendor_memory (memory_id, vendor, fact_kind, fact_key, fact_value, notes, status, created_at, tenant_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("mutant_alias", "測試供應商X", "alias", "alias:手切肥牛", "肥牛", "notes", "active", created_100d, "default")
    )
    conn.commit()
    conn.close()

    # 正常分级：alias 不过期
    rows = db.list_vendor_memory(vendor="測試供應商X", tenant_id="default")
    assert any(r["memory_id"] == "mutant_alias" for r in rows)

    # 模拟突变（错误地对 alias 也启用了 90 天过期）
    with patch("app.db.memory_ttl_days", side_effect=lambda kind="": 90 if kind == "alias" else 0):
        # 突变状态下 alias 在第 100 天被误判为过期
        rows_mutant = db.list_vendor_memory(vendor="測試供應商X", tenant_id="default")
        assert not any(r["memory_id"] == "mutant_alias" for r in rows_mutant), "突变体必须使 alias 在 90d 后失效，以此证明测试具备判别力"
