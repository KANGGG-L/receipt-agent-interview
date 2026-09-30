# -*- coding: utf-8 -*-
"""记忆语义 schema 的契约测试（计划 P3-T1 / Stream E · E1）。

覆盖：
1. 四类 kind 各自合法通过（返回 (True, "")）；
2. 每类非法输入各一条拒绝断言，并断言理由可读（必须说清"哪里错"）：
   未知 kind / 空 value / 缺 supplier_id / 缺 tenant_id / fact_key 长度越界 /
   valid_to 早于 valid_from；另补 source_kind 白名单、trust_level 范围、日期格式；
3. to_row / from_row 往返等价、行字段名与计划中的扩展列一致、映射覆盖全部字段；
4. 纯逻辑红线：本模块不 import 任何 app 侧模块（否则测试就有写 live 向量库的风险）。

判别力（反证）：`test_reject_unknown_kind` 是判别式用例 —— 把 memory_facts 里的 kind
白名单判断注释掉后，本用例必须失败（ok 翻成 True 且理由里不再有 "kind"）。
`test_row_fields_cover_every_dataclass_field` 同理：新增字段而不补映射即失败。

环境隔离：本模块只用纯逻辑，不建库也不建向量库；仍按同波次约定把 DB_PATH / RAG_DIR
指向临时目录，避免将来误加 import 时把产物写到 live。
"""

import ast
import dataclasses
import os
import pathlib
import sys
import tempfile

# 隔离环境：临时 DB / 向量目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="memory_facts_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "memory_facts.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")

_DEMO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo")
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import pytest  # noqa: E402

import app.services.memory_facts as mf  # noqa: E402


def _valid_fact(**over):
    """一条各项都合法的基准事实；用 over 注入单点缺陷，保证"只错一处"。"""
    base = {
        "kind": mf.KIND_ALIAS,
        "fact_key": "黑牛",
        "value": "黑牛 -> 黑牛牌黑胡椒牛油",
        "supplier_id": "sup-0001",
        "tenant_id": "tenant-a",
    }
    base.update(over)
    return mf.MemoryFact(**base)


# -------------------------------------------------------------
# 1. 四类 kind 合法通过
# -------------------------------------------------------------
def test_all_four_kinds_accepted():
    assert sorted(mf.ALLOWED_KINDS) == ["alias", "layout", "price_range", "unit"]
    for kind in sorted(mf.ALLOWED_KINDS):
        ok, reason = mf.validate_fact(_valid_fact(kind=kind))
        assert ok is True, f"{kind} 应合法，实得理由 {reason!r}"
        assert reason == ""


def test_all_four_source_kinds_accepted():
    for src in sorted(mf.ALLOWED_SOURCE_KINDS):
        ok, reason = mf.validate_fact(_valid_fact(source_kind=src))
        assert ok is True, f"{src} 应合法，实得理由 {reason!r}"


# -------------------------------------------------------------
# 2. 非法输入逐类拒绝（含可读理由）
# -------------------------------------------------------------
def test_reject_unknown_kind():
    """判别式：注释掉 kind 白名单判断后，本用例必须失败。"""
    ok, reason = mf.validate_fact(_valid_fact(kind="price"))
    assert ok is False
    assert "kind" in reason and "price" in reason, reason


def test_reject_empty_value():
    for bad in ("", "   ", None):
        ok, reason = mf.validate_fact(_valid_fact(value=bad))
        assert ok is False, f"value={bad!r} 应被拒"
        assert "value 为空" in reason, reason


def test_reject_missing_supplier_id():
    for bad in ("", "   ", None):
        ok, reason = mf.validate_fact(_valid_fact(supplier_id=bad))
        assert ok is False, f"supplier_id={bad!r} 应被拒"
        assert "supplier_id" in reason, reason


def test_reject_missing_tenant_id():
    for bad in ("", "   ", None):
        ok, reason = mf.validate_fact(_valid_fact(tenant_id=bad))
        assert ok is False, f"tenant_id={bad!r} 应被拒"
        assert "tenant_id" in reason, reason


def test_reject_fact_key_length_out_of_range():
    too_long = "k" * (mf.FACT_KEY_MAX_LEN + 1)
    ok, reason = mf.validate_fact(_valid_fact(fact_key=too_long))
    assert ok is False
    assert "fact_key 长度越界" in reason and str(mf.FACT_KEY_MAX_LEN + 1) in reason, reason

    # 空白 key 去空白后长度为 0，同样越界（"   " 不能当合法 key）
    ok2, reason2 = mf.validate_fact(_valid_fact(fact_key="   "))
    assert ok2 is False
    assert "fact_key 长度越界" in reason2, reason2

    # 边界内的不拒绝（证明边界是闭区间，不是把合法值一起拦掉）
    ok3, _ = mf.validate_fact(_valid_fact(fact_key="k" * mf.FACT_KEY_MAX_LEN))
    assert ok3 is True


def test_reject_valid_to_before_valid_from():
    ok, reason = mf.validate_fact(
        _valid_fact(valid_from="2026-03-01", valid_to="2026-02-01"))
    assert ok is False
    assert "valid_to 早于 valid_from" in reason, reason

    # 同一天合法；valid_to 空表示无失效期
    assert mf.validate_fact(
        _valid_fact(valid_from="2026-03-01", valid_to="2026-03-01"))[0] is True
    assert mf.validate_fact(_valid_fact(valid_from="2026-03-01", valid_to=""))[0] is True
    # 只给 valid_to（起点开放）不构成"早于"
    assert mf.validate_fact(_valid_fact(valid_to="2026-03-01"))[0] is True


def test_reject_unparseable_date():
    """日期写错不得被当成"无边界"静默放过（否则该记忆永久驻留）。"""
    for bad in ("2026/03/01", "2026-13-01", "2026-02-31", "下个月"):
        ok, reason = mf.validate_fact(_valid_fact(valid_from=bad))
        assert ok is False, f"valid_from={bad!r} 应被拒"
        assert "YYYY-MM-DD" in reason, reason


def test_reject_unknown_source_kind():
    ok, reason = mf.validate_fact(_valid_fact(source_kind="llm_guess"))
    assert ok is False
    assert "source_kind" in reason and "llm_guess" in reason, reason


def test_reject_trust_level_out_of_range():
    for bad in (1.5, -0.1, "high", float("nan")):
        ok, reason = mf.validate_fact(_valid_fact(trust_level=bad))
        assert ok is False, f"trust_level={bad!r} 应被拒"
        assert "trust_level 越界" in reason, reason
    # 闭区间两端合法
    assert mf.validate_fact(_valid_fact(trust_level=0.0))[0] is True
    assert mf.validate_fact(_valid_fact(trust_level=1.0))[0] is True
    # 数字字符串可归一，不算非法
    assert mf.validate_fact(_valid_fact(trust_level="0.6"))[0] is True


# -------------------------------------------------------------
# 3. 行映射：字段名对齐计划扩展列 + 往返等价
# -------------------------------------------------------------
def test_row_fields_match_planned_extension_columns():
    row = _valid_fact().to_row()
    assert set(row) == set(mf.ROW_FIELDS)
    for col in ("fact_kind", "fact_key", "fact_value", "supplier_id", "tenant_id",
                "source_kind", "source_ref", "trust_level", "valid_from", "valid_to",
                "written_by", "superseded_by", "memory_id", "status"):
        assert col in row, f"缺计划列 {col}"


def test_row_fields_cover_every_dataclass_field():
    """判别式：新增字段却不补映射 → 该字段会静默丢在映射外（"定义了没接线"）。"""
    domain_names = {f.name for f in dataclasses.fields(mf.MemoryFact)}
    assert set(mf._FACT_TO_ROW) == domain_names
    assert set(mf.ROW_FIELDS) == set(mf._FACT_TO_ROW.values())
    assert len(mf.ROW_FIELDS) == len(set(mf.ROW_FIELDS)), "行字段名不得重复"


def test_row_round_trip_equivalence():
    for kind in sorted(mf.ALLOWED_KINDS):
        fact = _valid_fact(
            kind=kind,
            source_kind=mf.SOURCE_EXTRACT,
            source_ref="[1487]",
            trust_level=0.6,
            valid_from="2026-03-01",
            valid_to="2026-09-01",
            written_by="extract@v1",
            memory_id="mid-0001",
            status="active",
            superseded_by="",
        )
        assert mf.MemoryFact.from_row(fact.to_row()) == fact, kind
    # 空边界 / 缺省元数据同样往返
    plain = _valid_fact()
    assert mf.MemoryFact.from_row(plain.to_row()) == plain


def test_from_row_ignores_non_extension_columns():
    """同一行还带着旧的自由文本列：映射只认扩展列，否则往返等价不成立。"""
    row = _valid_fact().to_row()
    row.update({
        "vendor": "德利行",
        "notes": "版式：手写",
        "sample": "菜心 10斤 @8 = 80",
        "decay_score": 0.7,
        "hit_count": 3,
        "created_at": "2026-09-22T04:00:00",
    })
    assert mf.MemoryFact.from_row(row) == _valid_fact()


def test_from_row_missing_columns_fall_back_to_defaults():
    """迁移未补齐的列回落缺省值（缺列不等于"值为空"）。"""
    fact = mf.MemoryFact.from_row({
        "fact_kind": mf.KIND_UNIT,
        "fact_key": "菜心",
        "fact_value": "斤",
        "supplier_id": "sup-0001",
        "tenant_id": "tenant-a",
    })
    assert fact.source_kind == mf.SOURCE_MANUAL
    assert fact.trust_level == 1.0
    assert fact.status == "active"
    assert fact.valid_to == ""
    assert fact.superseded_by == ""
    ok, reason = mf.validate_fact(fact)
    assert ok is True, reason


def test_validate_fact_accepts_row_dict():
    """第二个入口：调用方可能直接拿到行字典（抽取产物 / 巡检脚本），不应要求先手工转型。"""
    assert mf.validate_fact(_valid_fact().to_row()) == (True, "")

    ok, reason = mf.validate_fact({})
    assert ok is False
    assert "kind 非法" in reason, reason  # 缺列 → 可读理由，而不是抛异常


def test_validate_fact_rejects_non_fact_type():
    for bad in (None, "alias", ["alias"]):
        ok, reason = mf.validate_fact(bad)
        assert ok is False
        assert "fact 类型非法" in reason, reason


def test_to_row_trust_level_is_float():
    """DB 列是 REAL：写字符串会让 SQLite 按字典序比大小（'9' > '10'）而静默失真。"""
    row = _valid_fact(trust_level="0.6").to_row()
    assert isinstance(row["trust_level"], float)
    assert row["trust_level"] == 0.6


def test_to_row_rejects_unparseable_trust_level():
    """不静默把脏值写进库：无法归一就显式抛错。"""
    with pytest.raises(ValueError):
        _valid_fact(trust_level="high").to_row()


# -------------------------------------------------------------
# 4. 纯逻辑红线
# -------------------------------------------------------------
def test_module_imports_no_app_dependencies():
    tree = ast.parse(pathlib.Path(mf.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    roots = {m.split(".")[0] for m in imported}
    assert not (roots & {"app", "chromadb", "sqlalchemy", "langchain"}), imported


# -------------------------------------------------------------
# 5. 不可哈希输入的契约（E0 审核 mustFix M1：只返回 (ok, reason)，绝不抛）
# -------------------------------------------------------------
def test_validate_fact_unhashable_kind_returns_reason_not_typeerror():
    """kind 传 dict/list 时曾抛 TypeError（集合成员测试要求可哈希）。

    契约是「只返回 (ok, reason)」：上层拿 reason 落 pending 的 reject_reason，
    一旦抛异常整条抽取链就会断掉，而不是"丢掉一条坏事实"。
    """
    for bad in ({"a": 1}, ["alias"], {"alias"}):
        ok, reason = mf.validate_fact(_valid_fact(kind=bad))
        assert ok is False, bad
        assert isinstance(reason, str) and reason.strip(), bad
        assert "kind" in reason, bad


def test_validate_fact_unhashable_source_kind_returns_reason_not_typeerror():
    for bad in ({"approve": 1}, ["manual"], {"manual"}):
        ok, reason = mf.validate_fact(_valid_fact(source_kind=bad))
        assert ok is False, bad
        assert isinstance(reason, str) and reason.strip(), bad
        assert "source_kind" in reason, bad


def test_validate_fact_unhashable_via_row_dict_entry():
    """行字典入口同样不得抛：from_row 后走的是同一套校验。"""
    ok, reason = mf.validate_fact({
        "fact_kind": {"x": 1}, "fact_key": "k", "fact_value": "v",
        "supplier_id": "s", "tenant_id": "t",
    })
    assert ok is False and isinstance(reason, str) and reason.strip()
