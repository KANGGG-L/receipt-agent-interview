# -*- coding: utf-8 -*-
"""单位归一共享模块测试（SPEC_PHASE2A §3.3 合成夹具）。

核心判据：run_eval 评分器与 memory_consolidate 的 unit 类事实共用
``app/services/unit_normalization.py`` 这**一把**尺子 —— 评分器不再有独立单位逻辑
（grep 闸）、YAML 资产单一（迁址闸）、unit 事实判定与评分口径同源（同义/负例）、
其他 kind 的键形态冻结（防去重语义静默漂移）。
"""

import os
import sys
import tempfile

# 隔离环境（必须在首次 import app.* 之前设置；本模块不碰 DB / 向量库，按同波次约定声明）
_TMP = tempfile.mkdtemp(prefix="unit_norm_shared_test_")
os.environ.setdefault("DB_PATH", os.path.join(_TMP, "unit_norm.db"))
os.environ.setdefault("RAG_DIR", os.path.join(_TMP, "rag_chroma"))

_DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_SCRIPTS_DIR = os.path.join(_DEMO_DIR, "scripts")
for _p in (_DEMO_DIR, _SCRIPTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

from app.services.unit_normalization import (  # noqa: E402
    DEFAULT_YAML, load_unit_equivalence, norm_unit, unit_value_key, units_equal)
import app.services.memory_consolidate as mc  # noqa: E402
from app.services.memory_facts import (  # noqa: E402
    KIND_ALIAS, KIND_LAYOUT, KIND_PRICE_RANGE, KIND_UNIT, MemoryFact, SOURCE_MANUAL)

import run_eval  # noqa: E402  （评分器侧别名符号；scripts 目录已在 sys.path）

RUN_EVAL_PATH = os.path.join(_SCRIPTS_DIR, "run_eval.py")


# -------------------------------------------------------------
# N-单一实现（grep 闸）：评分器不再有独立单位逻辑
# -------------------------------------------------------------
def test_run_eval_has_no_local_unit_logic():
    src = open(RUN_EVAL_PATH, encoding="utf-8").read()
    for banned in ("UNIT_TO_KG", "safe_load", "member_index", "PAREN_SPEC"):
        assert banned not in src, "run_eval.py 不得再有独立单位逻辑：%r" % banned
    assert "unit_normalization" in src, "run_eval.py 必须消费共享模块"


def test_run_eval_alias_symbols_behave():
    """阶段 0 测试消费的同名别名符号与共享实现行为一致。"""
    assert run_eval._UNIT_EQUIV["version"] == load_unit_equivalence()["version"]
    assert run_eval._units_equal("樽", "瓶") and run_eval._units_equal("司马斤", "斤")
    assert run_eval._norm_unit("箱（48罐）") == "箱"
    assert run_eval._load_unit_equivalence is load_unit_equivalence
    assert run_eval.UNIT_EQUIV_PATH == os.path.join(
        _DEMO_DIR, "app", "services", "unit_equivalence.yaml")


# -------------------------------------------------------------
# N-YAML 迁址：资产单一
# -------------------------------------------------------------
def test_yaml_moved_and_single():
    new_path = os.path.join(_DEMO_DIR, "app", "services", "unit_equivalence.yaml")
    old_path = os.path.join(_SCRIPTS_DIR, "unit_equivalence.yaml")
    assert os.path.exists(new_path), "新址必须存在"
    assert not os.path.exists(old_path), "旧址必须已迁走（资产单一）"
    from_shared = load_unit_equivalence(new_path)
    from_eval = load_unit_equivalence(run_eval.UNIT_EQUIV_PATH)
    assert from_shared["version"] == from_eval["version"]
    assert len(from_shared["groups"]) == 7 == len(from_eval["groups"])


# -------------------------------------------------------------
# N-等价语义：与 run_eval 旧行为逐点一致
# -------------------------------------------------------------
@pytest.mark.parametrize("a,b", [("樽", "瓶"), ("BTL", "瓶"), ("司马斤", "斤"),
                                 ("箱", "case"), ("kg", "公斤"), ("支", "枝")])
def test_units_equal_positive(a, b):
    assert units_equal(a, b, load_unit_equivalence()) is True


def test_units_equal_negative():
    eq = load_unit_equivalence()
    # 手算：斤 0.6048 vs 磅 0.4536 —— 系数不同是语义差异，不得判等
    assert units_equal("斤", "磅", eq) is False
    assert units_equal("D", "文", eq) is False  # 真噪声
    assert units_equal("斤", "", eq) is False   # 单侧空


def test_norm_unit_paren_spec():
    assert norm_unit("箱（48罐）", load_unit_equivalence()) == "箱"


# -------------------------------------------------------------
# N-fail-fast 透传
# -------------------------------------------------------------
def test_missing_yaml_fails_fast(tmp_path):
    missing = str(tmp_path / "xxx_missing.yaml")
    with pytest.raises(SystemExit) as ei:
        load_unit_equivalence(missing)
    assert missing in str(ei.value)


# -------------------------------------------------------------
# N-fact-unit：unit 事实口径与评分口径同源
# -------------------------------------------------------------
def _fact(**over):
    base = {"kind": KIND_ALIAS, "fact_key": "黑牛", "value": "黑牛牌黑胡椒牛油",
            "supplier_id": 1, "tenant_id": "default", "source_kind": SOURCE_MANUAL}
    base.update(over)
    return MemoryFact(**base)


@pytest.mark.parametrize("a,b", [("司马斤", "斤"), ("樽", "瓶"), ("箱（48罐）", "箱"),
                                 ("BTL", "瓶")])
def test_fact_unit_equivalent_is_noop(a, b):
    assert mc._values_equal(KIND_UNIT, a, b) is True
    res = mc.decide_action(_fact(kind=KIND_UNIT, value=a, fact_key="计价单位"),
                           _fact(kind=KIND_UNIT, value=b, fact_key="计价单位"))
    assert res.action == mc.ACTION_NOOP, (a, b, res.reason)


@pytest.mark.parametrize("a,b", [("斤", "磅"), ("D", "文")])
def test_fact_unit_negative_stays_different(a, b):
    assert mc._values_equal(KIND_UNIT, a, b) is False


def test_fact_unit_key_prefixes():
    """unit_value_key 三层键（与 units_equal 同构）：组/换算/文本互不撞车。"""
    eq = load_unit_equivalence()
    # 同组 → grp: 代表元（确定性 sorted()[0]）
    assert unit_value_key("樽", eq) == unit_value_key("瓶", eq)
    assert unit_value_key("樽", eq).startswith("grp:")
    # 同换算系数 → kg:（克 0.001；评分侧 U-2 换算路径同源）
    assert unit_value_key("克", eq) == unit_value_key("g", eq)
    assert unit_value_key("g", eq).startswith("kg:")
    # 文本兜底 → txt:；空值 → "txt:"
    assert unit_value_key("D", eq).startswith("txt:")
    assert unit_value_key("", eq) == "txt:"


# -------------------------------------------------------------
# N-fact 既有守卫不破 / N-fact 键前缀冻结
# -------------------------------------------------------------
def test_fact_other_kinds_unchanged():
    # 既有守卫（tests/test_memory_consolidate.py:165-185 原样语义）
    assert mc._values_equal(KIND_UNIT, " 斤 ", "斤") is True   # 空白/大小写口径仍成立
    assert mc._values_equal(KIND_ALIAS, " 黑牛 ", "黑牛") is True
    assert mc._values_equal(KIND_ALIAS, "黑 牛", "黑牛") is True
    assert mc._values_equal(KIND_LAYOUT, "Abc", "abc") is True
    assert mc._values_equal(KIND_PRICE_RANGE, "10-8", "8-10") is True
    assert mc._values_equal(KIND_PRICE_RANGE, "8", "8.0") is True
    assert mc._values_equal(KIND_PRICE_RANGE, "8", "8-10") is False
    # 斤 vs 磅 不因 unit 语义放宽（评分侧 U-3 镜像）
    assert mc._values_equal(KIND_UNIT, "斤", "磅") is False


def test_fact_key_prefix_frozen():
    """其他 kind 的键形态冻结（防去重语义静默漂移）。"""
    assert mc._normalize_value(KIND_ALIAS, "x") == "text:x"
    assert mc._normalize_value(KIND_LAYOUT, " X ") == "text:x"
    assert mc._normalize_value(KIND_PRICE_RANGE, "10-8").startswith("range:")
    assert mc._normalize_value(KIND_PRICE_RANGE, "abc").startswith("unparsed:")
    # unit 键前缀（新形态，前缀分段防撞车）
    assert mc._normalize_value(KIND_UNIT, "斤").startswith("unit:")
