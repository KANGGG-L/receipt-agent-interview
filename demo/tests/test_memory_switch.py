# -*- coding: utf-8 -*-
"""C1：记忆总开关 memory_enabled（缺省 true）+ retrieve_context 入口短路。

why: 记忆闭环上线后，运维需要一条「怀疑注入污染影响识别时**一键停注入**」的退路，
而不必回滚代码或清库。开关关掉必须真的是零开销——只把注入结果置空却照旧查记忆表、
照旧打开向量库、照旧记账，等于没有关闭（且会把 hit_count 记成"被用过"）。

本文件锁五件事（全程离线：独立临时 DB + 临时 RAG_DIR，零网络、零 live 库/向量库写入）：

1. 键未设置 → 缺省开启，注入行为与接线前逐字节一致（有注入就有 hit 记账）。
2. 键显式关闭 → 返回空串，且**不查记忆表、不打开向量库、不写 hit_count**（用
   spy 直接证明这三件事一次都没发生，而不是只看返回值）。
3. 关闭态的空串不是"本来就没数据"造成的假阳性 —— 同一份种子数据在开启态必须
   注得出来（正反对照同文件内完成）。
4. 三态解析：true/false（大小写不敏感）→ 显式值生效；空串 → 未设置 → 缺省开启；
   无法识别的值 → 回退缺省开启（与 settings_service._coerce 同口径）。
5. 读取失败（无 DB / 表缺失）视为开启 —— 配置系统故障不得静默停掉记忆注入。

反证口径（交付说明里给实测记录，不写进用例）：把 retrieve_context 里那段
`if not memory_enabled(): return ""` 去掉 → 本文件关闭态的 3 条用例必须失败
（空串断言失败 + spy 抓到查库/开向量库 + hit_count 被写成 1）。
"""

import importlib
import os
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="memory_switch_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_memory_switch.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import pytest  # noqa: E402

import app.db as db  # noqa: E402
import app.services.rag as rag  # noqa: E402
from app.services import settings_service  # noqa: E402

rag.PERSIST_DIR = os.environ["RAG_DIR"]
rag._vs = None
rag._vs_tenant = {}

_VENDOR = "開關商戶T1"
_TENANT = "t1"


def _fresh_db():
    """每个用例独立数据库文件，避免跨用例串扰（含 app_settings 里的开关值）。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"sw_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    importlib.reload(settings_service)
    rag.PERSIST_DIR = os.environ["RAG_DIR"]
    rag._vs = None
    rag._vs_tenant = {}
    return db


def _seed(vendor=_VENDOR, tenant_id=_TENANT):
    """只落 DB 行（精确通道即可注入），不写向量 —— 本用例不关心相似度通道。"""
    mid, _is_new = db.upsert_vendor_memory(
        vendor, "版式：手写单", f"{vendor} 明细：菜心 2斤 @10 = 20",
        tenant_id=tenant_id, source_kind="manual")
    return mid


# -------------------------------------------------------------
# 1. 键未设置 → 缺省开启，行为与接线前一致
# -------------------------------------------------------------
def test_unset_key_defaults_enabled_and_injects_normally():
    _db = _fresh_db()
    assert _db.get_app_setting("memory_enabled") is None, "前置：该键未设置"
    assert rag.memory_enabled() is True

    _seed()
    ctx = rag.retrieve_context(_VENDOR, tenant_id=_TENANT)
    assert ctx.strip() != "", "缺省开启时注入路径必须照常工作"
    assert _VENDOR in ctx

    row = _db.list_vendor_memory(_VENDOR, tenant_id=_TENANT)[0]
    assert row["hit_count"] == 1, "有注入就要有命中记账（对照关闭态的 hit_count == 0）"


# -------------------------------------------------------------
# 2. 键显式关闭 → 空串 + 零开销（不查库、不查向量、不记账）
# -------------------------------------------------------------
def test_off_returns_empty_without_touching_db_or_vectors(monkeypatch):
    _db = _fresh_db()
    _seed()  # 有记忆可用：空串必须是"被开关挡掉"，不是"本来就没数据"
    _db.set_app_setting("memory_enabled", "false")
    assert rag.memory_enabled() is False

    touched = []

    def _spy_list(*a, **k):
        touched.append("list_vendor_memory")
        raise AssertionError("开关关闭时不得查记忆表")

    def _spy_store(*a, **k):
        touched.append("_store")
        raise AssertionError("开关关闭时不得打开/查询向量库")

    monkeypatch.setattr(_db, "list_vendor_memory", _spy_list)
    monkeypatch.setattr(rag, "_store", _spy_store)
    monkeypatch.setattr(_db, "bump_vendor_memory_hit",
                        lambda ids: touched.append(("bump", list(ids))))

    out = rag.retrieve_context(_VENDOR, tenant_id=_TENANT)

    assert out == ""
    assert touched == [], f"关闭态应零外部取数动作，实际发生: {touched}"


def test_off_does_not_bump_hit_count_and_does_not_raise():
    _db = _fresh_db()
    _seed()
    _db.set_app_setting("memory_enabled", "0")

    before = _db.list_vendor_memory(_VENDOR, tenant_id=_TENANT)[0]

    # 不抛异常（多条调用也不得因开关阻断识别链路）
    assert rag.retrieve_context(_VENDOR, tenant_id=_TENANT) == ""
    assert rag.retrieve_context(_VENDOR, tenant_id=_TENANT, top_k=1) == ""

    after = _db.list_vendor_memory(_VENDOR, tenant_id=_TENANT)[0]
    assert after["hit_count"] == 0 == before["hit_count"]
    assert after["last_hit_at"] == before["last_hit_at"] == ""


# -------------------------------------------------------------
# 3. 开关只影响注入，不影响已落库数据（不是数据丢失）
# -------------------------------------------------------------
def test_off_keeps_memory_rows_intact():
    _db = _fresh_db()
    mid = _seed()
    _db.set_app_setting("memory_enabled", "false")

    assert rag.retrieve_context(_VENDOR, tenant_id=_TENANT) == ""

    rows = _db.list_vendor_memory(_VENDOR, tenant_id=_TENANT, include_archived=True)
    assert [r["memory_id"] for r in rows] == [mid], "开关不得顺手删/归档记忆行"
    assert rows[0]["status"] == "active"


# -------------------------------------------------------------
# 4. 三态解析：显式值 / 空串 / 无法识别的值
# -------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("true", True),
    ("TRUE", True),
    ("on", True),
    ("false", False),
    ("False", False),
    ("off", False),
    ("0", False),
    ("", True),        # 空 = 未设置 → 缺省开启
    ("   ", True),     # 仅空白同上
    ("maybe", True),   # 无法识别 → 回退缺省（与 _coerce 同口径）
])
def test_value_parsing(raw, expected):
    _db = _fresh_db()
    _db.set_app_setting("memory_enabled", raw)
    assert rag.memory_enabled() is expected


def test_explicit_true_injects_and_empty_value_behaves_like_unset():
    _db = _fresh_db()
    _seed()

    _db.set_app_setting("memory_enabled", "true")
    assert rag.retrieve_context(_VENDOR, tenant_id=_TENANT).strip() != ""

    # 空串 = 未设置（写入空值后必须回到缺省开启，而不是被当成 False）
    _db.set_app_setting("memory_enabled", "")
    assert rag.retrieve_context(_VENDOR, tenant_id=_TENANT).strip() != ""


# -------------------------------------------------------------
# 5. 读取失败 → 视为开启（不得静默停掉记忆注入）
# -------------------------------------------------------------
def test_read_failure_falls_back_to_enabled(monkeypatch):
    _fresh_db()

    def _boom(key, default=None):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(db, "get_app_setting", _boom)
    assert rag.memory_enabled() is True


# -------------------------------------------------------------
# 6. 键已登记进 SETTINGS_DEFAULTS（管理台可读可改的消费方）
# -------------------------------------------------------------
def test_key_registered_and_reachable_via_settings_service():
    _db = _fresh_db()

    assert settings_service.SETTINGS_DEFAULTS["memory_enabled"] is True
    # 管理台 GET 全量视图（api_admin 读 all_settings）必须带上本键
    assert settings_service.all_settings()["memory_enabled"] is True
    # 未设置时经 settings_service 口径读到的也是缺省开启
    assert settings_service.get_bool("memory_enabled") is True
    assert _db.get_app_setting("memory_enabled") is None

    # 管理台 PUT 走的就是 set_value（api_admin 按 SETTINGS_DEFAULTS 校验键名）
    assert settings_service.set_value("memory_enabled", False) is False
    assert rag.memory_enabled() is False
    assert settings_service.set_value("memory_enabled", True) is True
    assert rag.memory_enabled() is True
