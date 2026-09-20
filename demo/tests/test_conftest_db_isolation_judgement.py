# -*- coding: utf-8 -*-
"""S1 回归：conftest DB 隔离兜底判据必须能区分「自己隔离」与「上游泄漏」。

原判据只看「生效的 db.DB_PATH 是否仍等于 live」，非 live 一律整体跳过隔离。它
无法区分 (a) 本用例主动隔离、(b) 上一个测试文件把自己的临时库泄漏在 db.DB_PATH
上 —— (b) 会被误判成 (a)，泄漏因此自我延续（后续文件落到陌生库、读到别人的
running 实验，正是 test_engine_rule_priority.py 与 test_experiment_guardian.py
同跑必挂的根因）。

本文件直接断言判据纯函数 `_classify_db_path` 与登记逻辑 `_record_module_local`，
并显式对比「旧判据表达式」对同一场景的结论，说明修复不是空转。

全程纯内存、零 DB 读写、零 live 库依赖。
"""

import importlib.util
import os
import sys
import types

import pytest

_DEMO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFTEST_PATH = os.path.join(_DEMO_DIR, "conftest.py")

_LIVE = "/srv/live/receipt_demo.db"
_AMBIENT = "/tmp/ambient.db"
_LEAKED = "/srv/tmp/pytest-of-x/pytest-3/test_other0/other.db"
_MY_LOCAL = "/srv/tmp/pytest-of-x/pytest-9/test_me0/mine.db"
_OTHER_MOD = "/srv/tests/test_other.py"
_MY_MOD = "/srv/tests/test_me.py"


def _load_demo_conftest():
    """按文件路径加载 demo/conftest.py（pytest 只把该文件当 conftest 加载，测试里
    需要直接引用它的纯函数，故显式按路径载入一份）。"""
    spec = importlib.util.spec_from_file_location("demo_conftest_under_test", _CONFTEST_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cf():
    return _load_demo_conftest()


# ---------------------------------------------------------------
# 1. 判据：四类归类
# ---------------------------------------------------------------
def test_live_path_must_be_isolated(cf):
    kind, source = cf._classify_db_path(_LIVE, _LIVE, "", _MY_MOD, {})
    assert (kind, source) == ("live", None)


def test_own_module_local_path_is_respected(cf):
    registry = {_MY_MOD: {_MY_LOCAL}}
    kind, source = cf._classify_db_path(_MY_LOCAL, _LIVE, "", _MY_MOD, registry)
    assert (kind, source) == ("owned", None), "本模块自己的隔离必须被尊重"


def test_other_module_leaked_path_is_detected(cf):
    """核心场景：别的模块遗留的库 → 判为泄漏（兜底必须接管）。"""
    registry = {_OTHER_MOD: {_LEAKED}}
    kind, source = cf._classify_db_path(_LEAKED, _LIVE, _AMBIENT, _MY_MOD, registry)
    assert kind == "leak", "上游文件泄漏的库必须判为泄漏"
    assert source == _OTHER_MOD, "应能指出是哪个模块泄漏的，便于排查"


def test_leaked_path_wins_even_when_env_matches(cf):
    """最难救的情形：env 与 db.DB_PATH 双双指向泄漏库，仍须判为泄漏。"""
    registry = {_OTHER_MOD: {_LEAKED}}
    kind, _ = cf._classify_db_path(_LEAKED, _LIVE, _LEAKED, _MY_MOD, registry)
    assert kind == "leak", "env 也一起泄漏时不能退化成「环境库」而被尊重"


def test_env_declared_ambient_path_is_respected(cf):
    kind, source = cf._classify_db_path(_AMBIENT, _LIVE, _AMBIENT, _MY_MOD, {})
    assert (kind, source) == ("ambient", None), "模块级 env 声明的环境库必须被尊重"


def test_unknown_non_live_path_is_treated_as_leak(cf):
    """非 live、非环境库、未登记的路径 → 判为泄漏，不允许悄悄用。"""
    kind, source = cf._classify_db_path("/srv/strange/unknown.db", _LIVE, _AMBIENT, _MY_MOD, {})
    assert (kind, source) == ("leak", None)


# ---------------------------------------------------------------
# 2. 反证：旧判据表达式对同一场景不会隔离
# ---------------------------------------------------------------
def _old_judge(current, live_path):
    """旧判据的等价表达式：非 live 即视为「已隔离」并整体跳过。"""
    return "live" if current == live_path else "ambient"


def test_old_judge_would_not_isolate_the_leaked_case(cf):
    registry = {_OTHER_MOD: {_LEAKED}}
    new_kind, _ = cf._classify_db_path(_LEAKED, _LIVE, _LEAKED, _MY_MOD, registry)
    old_kind = _old_judge(_LEAKED, _LIVE)
    assert old_kind == "ambient", "旧判据把泄漏库当成「已隔离」"
    assert new_kind == "leak", "新判据把它判为泄漏 —— 这就是修复点"
    assert old_kind != new_kind, "新旧判据在同一场景下结论必须不同，否则修复是空转"


# ---------------------------------------------------------------
# 3. 登记逻辑：只有「用例执行期间被改过」的库才入册
# ---------------------------------------------------------------
def _fake_db(path, default=_LIVE):
    return types.SimpleNamespace(DB_PATH=path, _default_db_path=default)


def test_record_module_local_registers_changed_path(cf):
    cf._reset_isolation_registry()
    cf._record_module_local(_OTHER_MOD, expected_path="/srv/tmp/expected.db",
                            db_module=_fake_db(_LEAKED))
    assert _LEAKED in cf._MODULE_LOCAL_DB.get(_OTHER_MOD, set()), \
        "用例执行期间被改成的库必须登记，否则下次无法判为泄漏"


def test_record_module_local_ignores_unchanged_and_live(cf):
    cf._reset_isolation_registry()
    # 未变化（正常用例跑完仍在自己那条隔离路径上）→ 不登记
    cf._record_module_local(_MY_MOD, expected_path=_MY_LOCAL, db_module=_fake_db(_MY_LOCAL))
    # 收尾回到 live → 不登记（live 由判据第一条兜住）
    cf._record_module_local(_MY_MOD, expected_path=_LIVE, db_module=_fake_db(_LIVE))
    assert cf._MODULE_LOCAL_DB.get(_MY_MOD, set()) == set(), \
        f"不应登记未变化/live 的路径，实际 {cf._MODULE_LOCAL_DB}"


def test_module_key_uses_module_file_not_nodeid(cf):
    """registry 的键必须是模块文件（不含用例名），否则同模块多次登记会各成一档。"""
    node = types.SimpleNamespace(path=os.path.join(_DEMO_DIR, "tests", "test_x.py"))
    assert cf._module_key(node) == os.path.join(_DEMO_DIR, "tests", "test_x.py")
    node2 = types.SimpleNamespace(path=os.path.join(_DEMO_DIR, "tests", "test_x.py"),
                                  nodeid="tests/test_x.py::test_a")
    assert cf._module_key(node2) == cf._module_key(node), "同一模块不同用例必须同键"


# ---------------------------------------------------------------
# 4. 隔离兜底不干扰正常自隔离（本文件自身就是自隔离用例）
# ---------------------------------------------------------------
def test_current_test_db_is_isolated_from_live(cf):
    """本文件的用例跑在 conftest 兜底的临时库上，绝不等于 live 默认库。"""
    sys.path.insert(0, _DEMO_DIR)
    from app import db

    live = os.path.abspath(db._default_db_path)
    assert os.path.abspath(str(db.DB_PATH)) != live, \
        f"用例不应跑在 live 库上：{db.DB_PATH}"
