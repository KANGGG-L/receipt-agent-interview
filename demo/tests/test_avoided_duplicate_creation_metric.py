# -*- coding: utf-8 -*-
"""E2：「避免重复建档数」口径（`duplicate_savings`）的自校准与供应商维度用例。

why 需要这一批：P2fix 审核 mustFix #2 指出 `scripts/backfill_vendor_sku_alias.py` 的
`duplicate_savings` / `groups_anchored` / `avoided_duplicate_creations` 三个输出字段
**无任何用例覆盖**。这三个字段是「供应商×SKU 别名表到底省了多少次重复建档」的唯一口径
（`--replay` 报告直接引用），而它一旦悄悄退化 —— 例如分组键里的 supplier_id 被去掉、
退化成按全局名字分组 —— 跨供应商的同名写法会被并成一组，"别的供应商首次出现的写法"
也被算成避免重复建档，指标虚高且**没有任何信号**。故本文件补：

1. **口径自校准**（判别式）：把分组键放宽为 (租户, 写法) 后，组数或出现次数必须变化；
   不变就说明统计并未真正按供应商维度分组；
2. 跨供应商不得归同组：同名写法在两个供应商下各自的首次出现都不算"避免重复建档"；
3. 同一 (供应商, 写法) 出现三次 → 避免数 = 2（首次建档不算）；
4. 空数据（0 组）→ 输出 0 而不是报错；
5. 接线：`replay()` 报告里确实带着这三个字段（防本项目高发的"定义了没接线"）。

反证：把分组键里的供应商维度去掉（`_dup_groups` 恒走放宽键）→ 第 1、2 条必失败。

全部离线假数据：模块级临时 DB + 临时 RAG 目录。脚本顶层 `import app.db` 会在**导入期**
按 DB_PATH 执行 `_make_engine()` 的迁移块，故隔离必须发生在 import 之前 ——
不带 DB_PATH 跑就是打 live 库。
"""

import importlib
import os
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* / scripts 之前设置）
_TMP = tempfile.mkdtemp(prefix="avoided_dup_metric_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_metric.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
for _p in (_DEMO_DIR, os.path.join(_DEMO_DIR, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

import app.db as db  # noqa: E402
import backfill_vendor_sku_alias as bvsa  # noqa: E402

_SUP_A = 11          # 纯口径用例用假 id：duplicate_savings 是纯函数，不查库
_SUP_B = 22


# -------------------------------------------------------------
# 夹具
# -------------------------------------------------------------
def _item(name, supplier_id, receipt_id, tenant_id="default", matched=0):
    """构造与脚本 `_load_items()` 同形的明细 dict（duplicate_savings 只读这几个键）。"""
    return {"name": name, "unit": "斤", "unit_price": 10.0, "receipt_id": receipt_id,
            "tenant_id": tenant_id, "supplier_id": supplier_id, "matched": matched}


def _fresh_db():
    """每个需要真库的用例独立数据库文件（与 P1/P2 系列同款）。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, "metric_%s.db" % uuid.uuid4().hex[:8])
    importlib.reload(db)
    return db.DB_PATH


def _mk_receipt(supplier_name, supplier_id, name, tenant_id="default"):
    """建一张单据 + 一条未匹配（matched=0）的明细，返回 receipt_id。"""
    rid = db.create_receipt_record(supplier_name=supplier_name, supplier_id=supplier_id,
                                   tenant_id=tenant_id, status="parsed")
    db.set_receipt_items(rid, [{
        "name": name, "raw_name": name, "quantity": 1, "unit": "斤", "unit_price": 10.0,
        "amount": 10.0, "sku_id": None, "cost_center_id": None, "confidence": 0.9,
        "matched": 0, "price_anomaly": 0, "price_anomaly_direction": "",
        "price_diff_percent": 0.0, "unit_conversion_warning": "", "fuzzy_candidates": [],
        "entity_candidates": [],
    }])
    return rid


# -------------------------------------------------------------
# 1. 自校准（判别式）：放宽分组键必须改变组数或出现次数
# -------------------------------------------------------------
def test_metric_self_calibration_changes_when_key_widened():
    """① 同名写法出现在两个供应商下 → 放宽键后组数/出现次数必须变化。

    两种键下这批数据的差别（这是判定供应商维度是否生效的唯一可构造形态）：
    - 按 (租户, 供应商, 写法)：两组各 1 次 → 都因不足 2 次被过滤 → 0 组 / 0 次；
    - 放宽为 (租户, 写法)：并成一组 2 次 → 1 组 / 2 次。
    若分组键退化成按名字分组，主口径读数会直接变成后者（避免数 0 → 1），
    而 `group_key_selfcheck` 会变成"无差异" → 本用例失败。
    """
    items = [_item("本吧", _SUP_A, 1), _item("本吧", _SUP_B, 2)]

    s = bvsa.duplicate_savings(items)
    sc = s["group_key_selfcheck"]
    # 主口径：两个供应商各自的首次出现，谁都不构成"重复"
    assert s["groups_anchored"] == 0 and s["occurrences_anchored"] == 0
    assert s["avoided_duplicate_creations"] == 0
    # 放宽键：并成一组 2 次（正是退化后会虚高出来的那个读数）
    assert sc["groups_by_name_only"] == 1 and sc["occurrences_by_name_only"] == 2
    assert sc["avoided_by_name_only"] == 1
    assert sc["differs_from_supplier_key"] is True, "两份数字相同 = 供应商维度没起作用"

    # 底层分组函数再核一次（防自校准字段本身被写死成常量）
    by_sup, _unanchored, _skipped = bvsa._dup_groups(items)
    by_name, _unanchored2, _skipped2 = bvsa._dup_groups(items, group_by_supplier=False)
    assert len(by_sup) == 0 and len(by_name) == 1
    assert list(by_name.values())[0] and len(list(by_name.values())[0]) == 2


# -------------------------------------------------------------
# 2. 跨供应商不得归同组
# -------------------------------------------------------------
def test_cross_supplier_same_name_not_merged_into_savings():
    """② 甲家同名出现 2 次 + 乙家出现 1 次 → 避免数只能是 1。

    乙家那一次是**乙家的首次出现**（乙家还没有任何别名先验，入账时仍要建档），
    不得因"名字和甲家一样"就并进甲家的组算成节省。分组键丢了供应商维度时两组会被
    并成一组 3 次 → 避免数变 2 → 本用例失败。
    """
    items = [_item("本吧", _SUP_A, 1), _item("本吧", _SUP_A, 2), _item("本吧", _SUP_B, 3)]

    s = bvsa.duplicate_savings(items)
    assert s["groups_anchored"] == 1, "只该有甲家那一组（乙家仅 1 次，不成组）"
    assert s["occurrences_anchored"] == 2
    assert s["avoided_duplicate_creations"] == 1, "乙家首次出现不得计入避免重复建档"
    assert s["group_key_selfcheck"]["avoided_by_name_only"] == 2, \
        "放宽键会虚高 1 次 —— 正说明这道自校准闸门有必要"


# -------------------------------------------------------------
# 3. 同一 (供应商, 写法) 出现三次 → 避免数 = 2
# -------------------------------------------------------------
def test_three_occurrences_same_supplier_yield_two_avoided():
    """③ 首次建档不算：n 次出现只省 n-1 次。"""
    items = [_item("本吧", _SUP_A, 1), _item("本吧", _SUP_A, 2), _item("本吧", _SUP_A, 3)]
    s = bvsa.duplicate_savings(items)
    assert s["groups_anchored"] == 1 and s["occurrences_anchored"] == 3
    assert s["avoided_duplicate_creations"] == 2
    assert s["cross_receipt_groups"] == 1 and s["within_receipt_only_groups"] == 0

    # 同一张单里写了两遍也算一个重复（组内出现次数不看单据，只看写法）
    same_receipt = [_item("本吧", _SUP_A, 7), _item("本吧", _SUP_A, 7), _item("本吧", _SUP_A, 9)]
    s2 = bvsa.duplicate_savings(same_receipt)
    assert s2["groups_anchored"] == 1 and s2["occurrences_anchored"] == 3
    assert s2["avoided_duplicate_creations"] == 2
    assert s2["cross_receipt_groups"] == 1


# -------------------------------------------------------------
# 4. 空数据 / 退化输入 → 0，而不是报错
# -------------------------------------------------------------
def test_empty_and_degenerate_input_outputs_zero_not_error():
    """④ 0 组时输出 0；不可计入主口径的行各归各的计数，且不混进主口径。"""
    empty = bvsa.duplicate_savings([])
    assert empty["groups_anchored"] == 0
    assert empty["occurrences_anchored"] == 0
    assert empty["avoided_duplicate_creations"] == 0
    assert empty["unanchored"]["groups"] == 0
    assert empty["group_key_selfcheck"]["differs_from_supplier_key"] is False, \
        "无数据时两种键必然相同（无从证伪），不得据此判为缺陷"

    # 每个写法只出现一次 → 没有任何"重复"，同样是 0
    singles = bvsa.duplicate_savings([_item("本吧", _SUP_A, 1), _item("本地牛腩", _SUP_A, 1)])
    assert singles["groups_anchored"] == 0 and singles["occurrences_anchored"] == 0
    assert singles["avoided_duplicate_creations"] == 0

    # 已匹配 / 供应商未锚定 / 空写法：三类都不进主口径，各自单独计数（可核对）
    mixed = bvsa.duplicate_savings([
        _item("本吧", _SUP_A, 1, matched=1), _item("本吧", _SUP_A, 2, matched=1),
        _item("本吧", None, 3), _item("本吧", None, 4),
        _item("   ", _SUP_A, 5), _item("   ", _SUP_A, 6),
    ])
    assert mixed["groups_anchored"] == 0 and mixed["avoided_duplicate_creations"] == 0
    assert mixed["skipped"] == {"matched_items": 2, "unresolved_supplier_items": 2,
                                "blank_name_items": 2}
    assert mixed["unanchored"]["groups"] == 1, "未锚定的组另报，不混进主口径"


# -------------------------------------------------------------
# 5. 接线：replay() 报告确实带着这三个字段（本用例必须排最后：replay 会切 db.DB_PATH）
# -------------------------------------------------------------
def test_replay_report_carries_metric_fields(monkeypatch):
    """⑤ 三个字段是 `--replay` 报告的直接输出，必须真被 replay 调用到。"""
    src = _fresh_db()
    sup, err = db.create_supplier("甲供應商", tenant_id="default")
    assert err is None, err
    _mk_receipt("甲供應商", sup, "本吧")
    _mk_receipt("甲供應商", sup, "本吧")
    # replay() 以模块级 _SOURCE_DB_PATH 为只读源；用本用例的临时库替换
    monkeypatch.setattr(bvsa, "_SOURCE_DB_PATH", src)

    expected = bvsa.duplicate_savings(bvsa._load_items(src))
    assert expected["groups_anchored"] == 1, "前提：同供应商同写法出现两次"
    assert expected["avoided_duplicate_creations"] == 1

    report = bvsa.replay()
    got = report["duplicate_savings"]
    for key in ("groups_anchored", "occurrences_anchored", "avoided_duplicate_creations",
                "group_key_selfcheck"):
        assert key in got, "replay 报告缺字段 %s" % key
    assert got["groups_anchored"] == expected["groups_anchored"] == 1
    assert got["occurrences_anchored"] == expected["occurrences_anchored"] == 2
    assert got["avoided_duplicate_creations"] == expected["avoided_duplicate_creations"] == 1
    assert got["groups_anchored"] == got["group_key_selfcheck"]["groups_by_name_only"], \
        "本批没有跨供应商同名写法 → 自校准应如实报无差异（不是缺陷）"


if __name__ == "__main__":   # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
