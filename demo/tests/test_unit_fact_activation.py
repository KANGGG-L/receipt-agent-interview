# -*- coding: utf-8 -*-
"""工作项①（收尾波次 SPEC_FINAL §1）：unit 记忆事实激活链路 —— 写侧/审批防线/读侧
三处消费 canonical_unit，人工闸语义不变。

判据（SPEC_FINAL §1.3）：
  N1 canonical_unit 正例/组外/噪声/空值/幂等（期望值钉住：等价组 YAML 列表首位 =
     规范写法 —— 瓶/箱/包/支/kg/lb/斤）。
  N2 写侧：memory_extract._to_facts 的 unit 候选 value 落库前归一；alias 候选原文保留。
  N3 审批防线：rag._pending_memory_fact 的 unit 载荷再归一（旧 pending 行原文透传防线）。
  N4 读侧渲染：rag._render_fact_row unit 分支过 canonical_unit（存量行免迁移）。
  N5 fail-fast：DEFAULT_YAML 指向不存在路径 → SystemExit 且报文含路径（不静默降级）。
  N6 NOOP 不受扰回归钉：memory_consolidate 去重口径不变（司馬斤 vs 斤 仍判同值）。

零模型、零语料依赖、零 DB 依赖（canonical_unit 纯函数）；缺省跑。
"""

import os
import sys
import tempfile

# 隔离环境（必须在首次 import app.* 之前设置；本模块不碰 DB / 向量库，按同波次约定声明）
_TMP = tempfile.mkdtemp(prefix="unit_fact_activation_test_")
os.environ.setdefault("DB_PATH", os.path.join(_TMP, "unit_fact.db"))
os.environ.setdefault("RAG_DIR", os.path.join(_TMP, "rag_chroma"))

_DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
for _p in (_DEMO_DIR, os.path.join(_DEMO_DIR, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

from app.services.unit_normalization import (  # noqa: E402
    DEFAULT_YAML, canonical_unit)
import app.services.memory_extract as memory_extract  # noqa: E402
import app.services.rag as rag  # noqa: E402
import app.services.memory_consolidate as mc  # noqa: E402

# N1 期望值（手算钉住）：规范写法 = unit_equivalence.yaml 各组列表首位原文成员。
# 组内映射：樽/btl/BTL→瓶；司馬斤/司马斤/港斤/catty→斤；箱（48罐）→箱；
# 千克/公斤→kg；磅/lbs→lb；枝→支。组外换算同系数（两）与真噪声（D）回落 norm_unit
# 文本（不编造规范写法）；空 → ""。
_N1_CASES = [
    ("樽", "瓶"),
    ("btl", "瓶"),
    ("BTL", "瓶"),
    ("司馬斤", "斤"),
    ("司马斤", "斤"),
    ("港斤", "斤"),
    ("catty", "斤"),
    ("箱（48罐）", "箱"),
    ("千克", "kg"),
    ("公斤", "kg"),
    ("磅", "lb"),
    ("lbs", "lb"),
    ("枝", "支"),
    ("两", "两"),   # 组外但命中换算表（同系数路径）→ norm_unit 文本，无规范写法
    ("D", "d"),     # 真噪声：只有文本归一（小写），无组无换算
]


def test_n1_canonical_unit_positive_and_idempotent():
    for raw, expected in _N1_CASES:
        got = canonical_unit(raw)
        assert got == expected, "canonical_unit(%r) 期望 %r，实得 %r" % (raw, expected, got)
        # 幂等：二次归一不变
        assert canonical_unit(got) == expected, \
            "canonical_unit 非幂等：%r → %r → %r" % (raw, got, canonical_unit(got))


def test_n1_canonical_unit_empty():
    assert canonical_unit("") == ""
    assert canonical_unit(None) == ""
    assert canonical_unit("   ") == ""


def test_n2_extract_unit_value_normalized_alias_untouched():
    """写侧：unit 候选落库前归一；alias 候选原文保留（归一只针对 unit kind）。"""
    facts = memory_extract._to_facts(
        [{"kind": "unit", "fact_key": "unit:有機白菜", "value": "司馬斤",
          "confidence": 0.9}], "1", "default")
    assert len(facts) == 1
    assert facts[0].value == "斤"
    # alias 候选不受影响：value 原文保留
    alias_facts = memory_extract._to_facts(
        [{"kind": "alias", "fact_key": "alias:本吧", "value": "樽裝白菜",
          "confidence": 0.9}], "1", "default")
    assert len(alias_facts) == 1
    assert alias_facts[0].value == "樽裝白菜"


def test_n3_pending_memory_fact_unit_normalized():
    """审批防线：approve 期 pending 载荷写入前再归一（旧 pending 行原文透传防线）。"""
    fact = rag._pending_memory_fact(
        "某商", "default", "extract", "receipt:1", 1,
        "unit", "unit:有機白菜", "司馬斤")
    assert fact is not None
    assert fact.value == "斤"


def test_n4_render_fact_row_unit_normalized():
    """读侧渲染：存量原文值与已规范值渲染一致（读侧覆盖，免迁移）。"""
    assert rag._render_fact_row(
        {"vendor": "某商", "fact_kind": "unit", "fact_key": "unit:有機白菜",
         "fact_value": "司馬斤"}) == "单位习惯：有機白菜 = 斤"
    assert rag._render_fact_row(
        {"vendor": "某商", "fact_kind": "unit", "fact_key": "unit:有機白菜",
         "fact_value": "斤"}) == "单位习惯：有機白菜 = 斤"


def test_n5_fail_fast_on_missing_yaml(monkeypatch):
    """缺省表缺失 → SystemExit 且报文含路径（沿用 load_unit_equivalence 纪律）。"""
    missing = os.path.join(_TMP, "no_such_unit_equivalence.yaml")
    assert not os.path.exists(missing)
    monkeypatch.setattr("app.services.unit_normalization.DEFAULT_YAML", missing)
    with pytest.raises(SystemExit) as ei:
        canonical_unit("斤")
    assert missing in str(ei.value), "fail-fast 报文必须含缺失表路径"


def test_n6_consolidate_noop_pin_unchanged():
    """回归钉：去重/NOOP 口径不受本项扰动 —— memory_consolidate 仍判司馬斤==斤
    （键语义带单位组，本项零改动其键/NOOP 实现）。"""
    assert mc._values_equal("unit", "司馬斤", "斤") is True
