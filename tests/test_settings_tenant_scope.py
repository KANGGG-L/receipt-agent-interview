# -*- coding: utf-8 -*-
"""WS3 回归：app_settings 租户维度与 settings_service 三级回退。

why：阈值归属从「单一全局值」改为「admin 全局默认 + owner 门店覆盖」后，
       解析优先级（租户覆盖 > 全局 default > SETTINGS_DEFAULTS）与后向兼容
       （tenant_id 缺省仍读全局桶）是整套门店自助阈值的地基。若这条口径错，
       「owner 改了自己门店的阈值」要么无效、要么污染全局，且现有调用点
       （一律不传 tenant_id）会静默换值。

覆盖：
1. 三级回退：租户覆盖存在读覆盖；无覆盖回落全局；都没有回落 SETTINGS_DEFAULTS
2. 覆盖值落在 app_setting_overrides（非 app_settings 复合主键行）—— 按实际实现断言
3. tenant_id None / "" / 纯空白 / "default" 四者等价（都读全局桶）
4. set_value(..., tenant_id="t1") 不影响默认租户读值（全局与门店互不串味）
5. all_settings(tenant_id=None) 返回全局视图（含全部缺省键）

全部离线假数据：模块级独立临时 DB + 临时 RAG 目录，零外部调用、零 live 写入。
"""

import atexit
import importlib
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="settings_tenant_scope_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_settings_tenant.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo"))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402
from app.services import settings_service  # noqa: E402

# 本文件统一用价格阈值键做探针（WS3 的消费方，缺省 10.0）
KEY = "price_anomaly_threshold_pct"


def _fresh_db():
    """每个用例独立数据库文件，避免跨用例串扰。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"st_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    return db


# -------------------------------------------------------------
# 1. 三级回退：覆盖 → 全局 → 缺省
# -------------------------------------------------------------
def test_three_level_fallback():
    _fresh_db()
    # 三级都未设置 → SETTINGS_DEFAULTS
    assert settings_service.get_float(KEY) == 10.0
    assert settings_service.get_float(KEY, tenant_id="t1") == 10.0

    # 写全局（tenant_id 缺省 = 全局 default 桶）
    settings_service.set_value(KEY, 12.0)
    assert settings_service.get_float(KEY) == 12.0
    # 门店无覆盖 → 回落全局（三级解析的中间层）
    assert settings_service.get_float(KEY, tenant_id="t1") == 12.0

    # 写门店覆盖 → 该租户读覆盖，其它视角仍读全局
    settings_service.set_value(KEY, 33.0, tenant_id="t1")
    assert settings_service.get_float(KEY, tenant_id="t1") == 33.0
    assert settings_service.get_float(KEY) == 12.0
    assert settings_service.get_float(KEY, tenant_id="t2") == 12.0


# -------------------------------------------------------------
# 2. 覆盖值落 app_setting_overrides（按实际实现，不是 app_settings 复合主键行）
# -------------------------------------------------------------
def test_override_stored_in_overrides_table_not_global_bucket():
    _fresh_db()
    settings_service.set_value(KEY, 12.0)                  # 全局桶
    settings_service.set_value(KEY, 33.0, tenant_id="t1")  # 门店覆盖

    # 读取口径：全局仍是 12.0，t1 是 33.0
    assert db.get_app_setting(KEY) == "12.0"
    assert db.get_app_setting(KEY, tenant_id="t1") == "33.0"

    # list 口径同源
    assert db.list_app_settings()[KEY] == "12.0"
    assert db.list_app_settings(tenant_id="t1")[KEY] == "33.0"

    # 物理落表核实：全局桶 app_settings（单列主键 key）该键只有一行；
    # 门店覆盖行在 app_setting_overrides（复合主键 (key, tenant_id)）
    conn = sqlite3.connect(db.DB_PATH)
    global_rows = conn.execute(
        "SELECT key, value FROM app_settings WHERE key = ?",
        (KEY,)).fetchall()
    override_rows = conn.execute(
        "SELECT key, tenant_id, value FROM app_setting_overrides WHERE key = ?",
        (KEY,)).fetchall()
    conn.close()
    assert global_rows == [(KEY, "12.0")], global_rows
    assert (KEY, "t1", "33.0") in override_rows, override_rows


# -------------------------------------------------------------
# 3. tenant_id None / "" / 纯空白 / "default" 四者等价
# -------------------------------------------------------------
_EQUIVALENT_TENANT_KEYS = (None, "", "   ", "default")


def test_none_blank_and_default_tenant_are_equivalent():
    _fresh_db()
    settings_service.set_value(KEY, 14.0)  # 全局
    for t in _EQUIVALENT_TENANT_KEYS:
        assert settings_service.get_float(KEY, tenant_id=t) == 14.0, t
        assert db.get_app_setting(KEY, tenant_id=t) == "14.0", t
        assert db.list_app_settings(tenant_id=t)[KEY] == "14.0", t

    # 写入门店覆盖后，四种等价写法仍一致读全局（都不误读门店行）
    settings_service.set_value(KEY, 55.0, tenant_id="t1")
    for t in _EQUIVALENT_TENANT_KEYS:
        assert settings_service.get_float(KEY, tenant_id=t) == 14.0, t

    # 用空白字符串写入 = 写全局桶（不新建门店行）
    settings_service.set_value(KEY, 20.0, tenant_id="   ")
    assert settings_service.get_float(KEY) == 20.0
    assert settings_service.get_float(KEY, tenant_id="t1") == 55.0


# -------------------------------------------------------------
# 4. 门店写入不影响默认租户读值
# -------------------------------------------------------------
def test_tenant_write_does_not_affect_default_reads():
    _fresh_db()
    settings_service.set_value(KEY, 11.0)
    settings_service.set_value(KEY, 66.0, tenant_id="t1")

    assert settings_service.get_float(KEY) == 11.0
    assert settings_service.get_float(KEY, tenant_id="default") == 11.0
    assert settings_service.all_settings()[KEY] == 11.0
    # 全局物理行未被改写
    assert db.get_app_setting(KEY) == "11.0"


# -------------------------------------------------------------
# 5. all_settings(tenant_id=None) 返回全局视图
# -------------------------------------------------------------
def test_all_settings_none_returns_global_view():
    _fresh_db()
    settings_service.set_value(KEY, 12.0)
    settings_service.set_value(KEY, 33.0, tenant_id="t1")
    settings_service.set_value("blur_laplacian_threshold", 44.0)

    view = settings_service.all_settings()  # tenant_id 缺省 = 全局视图
    assert set(settings_service.SETTINGS_DEFAULTS) <= set(view)
    assert view[KEY] == 12.0
    assert view["blur_laplacian_threshold"] == 44.0

    # 传入租户则返回该门店生效值（覆盖优先）
    tenant_view = settings_service.all_settings(tenant_id="t1")
    assert tenant_view[KEY] == 33.0
    assert tenant_view["blur_laplacian_threshold"] == 44.0


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
