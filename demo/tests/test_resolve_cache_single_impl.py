# -*- coding: utf-8 -*-
"""D2 + D10：`backfill_vendor_sku_alias.py` 的带缓存解析收敛为单点实现 + 降级留痕。

why 需要这一批：
- D2（「同一根因多份实现只改一份」）：`plan()` 与 `_load_items()` 此前各自抄了一份
  `(tenant, name) → supplier_id` 的带缓存解析（连缓存字典名都不同）。这类重复不会报错，
  只会让某一处悄悄落后于另一处 —— 本仓已被该形态反复咬过。
- D10（「异常被静默吞掉 → 功能静默失效」）：两处都是裸 `except Exception: ... = None`，
  解析长期失败时读数看着正常却查不到原因。改为 `logger.warning`（带 name / tenant_id /
  异常类型与文本），但**行为不变**：仍降级为 None、仍不中断回填。

判据分层（每一条都是判别式，回滚修复必红）：
1. 单一实现：源码里 `db.resolve_supplier_id(` 只允许一处调用（机械可数）；且 `plan` 与
   `_load_items` 两条路径**都真的经过同一个 helper 对象**（spy 记录调用，若某处仍是内联
   实现则它不会被 spy 拦到 → 断言失败）。这是「同一 helper 对象」比「行为恰好一致」可靠
   的用法：行为一致可以由两份实现凑巧达成，调用点被同一个对象拦到则不可能。
2. 降级留痕：让 resolve 抛错 → 回填仍完成、结果降级为 None、caplog 里有 warning
  （**先做 caplog 自校准**，否则「没有 warning」会假绿）。
3. 计算不变（golden）：同一夹具跑 `replay()` 的汇总数字与硬编码期望逐值一致，防止
   「收敛实现」顺手改掉了读数。

反证：把 `_load_items()` 改回内联实现（不经 helper）→ 第 1 条两项断言必失败；
去掉 helper 里的 `logger.warning` → 第 2 条的 caplog 断言必失败。

全部离线假数据：模块级临时 DB + 临时 RAG 目录。脚本顶层 `import app.db` 会在**导入期**
按 DB_PATH 执行 `_make_engine()` 的迁移块，故隔离必须发生在 import 之前 ——
不带 DB_PATH 跑就是打 live 库。
"""

import importlib
import inspect
import logging
import os
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* / scripts 之前设置）
_TMP = tempfile.mkdtemp(prefix="resolve_cache_single_impl_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_single_impl.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"
os.environ["RAG_EMBEDDING_PROVIDER"] = "hash"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
for _p in (_DEMO_DIR, os.path.join(_DEMO_DIR, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

import app.db as db  # noqa: E402
import backfill_vendor_sku_alias as bvsa  # noqa: E402


# -------------------------------------------------------------
# 夹具
# -------------------------------------------------------------
def _fresh_db():
    """每个用例独立数据库文件（与 P1/P2 系列同款）。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, "single_impl_%s.db" % uuid.uuid4().hex[:8])
    importlib.reload(db)
    return db.DB_PATH


def _mk_receipt(supplier_name, supplier_id, name, matched=0, sku_id=None,
                tenant_id="default"):
    """建一张单据 + 一条明细，返回 receipt_id。

    `supplier_id=None` + 非空 `supplier_name` 是本次要覆盖的形态：行上没有结构锚点，
    必须走名字解析（即 `_resolve_cached` 那条路）。
    """
    rid = db.create_receipt_record(supplier_name=supplier_name, supplier_id=supplier_id,
                                   tenant_id=tenant_id, status="parsed")
    db.set_receipt_items(rid, [{
        "name": name, "raw_name": name, "quantity": 1, "unit": "斤", "unit_price": 10.0,
        "amount": 10.0, "sku_id": sku_id, "cost_center_id": None, "confidence": 0.9,
        "matched": matched, "price_anomaly": 0, "price_anomaly_direction": "",
        "price_diff_percent": 0.0, "unit_conversion_warning": "", "fuzzy_candidates": [],
        "entity_candidates": [],
    }])
    return rid


# -------------------------------------------------------------
# 1. 单一实现（判别式）
# -------------------------------------------------------------
def test_source_has_exactly_one_resolve_call():
    """① 机械判据：全脚本只允许一处调用 `db.resolve_supplier_id(`。

    收敛前是两处（plan 与 _load_items 各一份），故这条断言直接对应 D2 的回归面；
    比「两边行为一致」严格 —— 两份实现可以凑巧行为一致，一处调用点却只有一份。
    """
    source = inspect.getsource(bvsa)
    hits = source.count("db.resolve_supplier_id(")
    assert hits == 1, (
        "带缓存解析的实现只允许有一份（应为 1 处 db.resolve_supplier_id( 调用），"
        "实测 %d 处 —— 说明又抄了一份，改一处会漏另一处（D2 回归）" % hits)


def test_both_call_sites_go_through_the_same_helper(monkeypatch):
    """② 行为判据：`plan()` 与 `_load_items()` 都被**同一个 helper 对象**拦到。

    why 用「同一个对象」而不是「行为一致」：spy 是模块级 `_resolve_cached` 这一**唯一
    函数对象**；两处调用点若都从模块命名空间取它，就都会被拦到。某一处若仍是内联实现，
    它照样能解析出正确的 supplier_id（行为一致），但**不会被 spy 记录** → 断言失败。
    这正是「单一实现」比「行为恰好一致」可靠的判据。
    """
    src = _fresh_db()
    sid, err = db.create_supplier("甲供應商", tenant_id="default")
    assert err is None, err
    sku_id, err = db.create_sku("菜心", base_unit="斤", tenant_id="default")
    assert err is None and sku_id, err
    # matched=1 且带 sku_id：会进 plan 的 _SELECT_MATCHED；supplier_id=None：两处都需解析
    _mk_receipt("甲供應商", None, "菜心", matched=1, sku_id=sku_id)

    calls = []
    real = bvsa._resolve_cached

    def spy(name, tenant_id, cache):
        calls.append((name, tenant_id))
        return real(name, tenant_id, cache)

    monkeypatch.setattr(bvsa, "_resolve_cached", spy)
    assert bvsa._resolve_cached is spy, "前提：spy 确实替换了模块级 helper"

    candidates, stats = bvsa.plan(src)
    after_plan = len(calls)
    items = bvsa._load_items(src)
    after_load = len(calls)

    assert after_plan >= 1, \
        "plan() 未经过 _resolve_cached —— 它仍是自带的内联实现（D2 未收敛）"
    assert after_load > after_plan, \
        "_load_items() 未经过 _resolve_cached —— 它仍是自带的内联实现（D2 未收敛）"
    assert stats["no_supplier"] == 0, "解析应成功（名字与档案同名）"
    assert [c["supplier_id"] for c in candidates] == [sid], \
        "解析结果仍必须是真实档案 id（收敛不得改变结果）"
    assert items[0]["supplier_id"] == sid
    assert {c[0] for c in calls} == {"甲供應商"} and {c[1] for c in calls} == {"default"}


# -------------------------------------------------------------
# 2. 降级留痕（判别式，含 caplog 自校准）
# -------------------------------------------------------------
def test_resolve_failure_degrades_to_none_with_warning(monkeypatch, caplog):
    """③ resolve 抛错 → 不中断、降级 None、且留下可定位的 warning。

    (a) 回填流程仍完成（plan 扫完所有行、_load_items 正常返回）
    (b) 得到 None 降级结果
    (c) caplog 里有 warning，且带 name / tenant_id / 异常类型与文本
    附带两条：同一名字只告警一次（证明加日志后缓存仍生效）；两处调用点传**同一个**
    缓存 dict 时共用命中（D2「不要两个字典」的直接判据）。
    """
    src = _fresh_db()
    sku_id, err = db.create_sku("菜心", base_unit="斤", tenant_id="default")
    assert err is None and sku_id, err
    _mk_receipt("未知商號", None, "菜心", matched=1, sku_id=sku_id)

    def _boom(name, tenant_id=None):
        raise RuntimeError("resolve boom")

    monkeypatch.setattr(db, "resolve_supplier_id", _boom)

    shared_cache = {}
    with caplog.at_level(logging.WARNING):
        # caplog 自校准：先证明采集通道活着，否则「没有 warning」是假绿
        bvsa.logger.warning("CAPLOG_SELFCHECK 采集通道自校准")
        assert any("CAPLOG_SELFCHECK" in r.getMessage() for r in caplog.records), \
            "caplog 采集通道无效 → 本用例的日志断言不可信"
        caplog.clear()

        candidates, stats = bvsa.plan(src, cache=shared_cache)   # (a) 未抛错，流程走完
        items = bvsa._load_items(src, cache=shared_cache)
        msgs = [r.getMessage() for r in caplog.records]

    assert stats["scanned"] == 1 and stats["no_supplier"] == 1, \
        "(a) 必须扫完所有行、(b) 解析失败降级为 None（不是中断、也不是凭空建档）"
    assert candidates == [], "解析不出供应商就不该产出候选（绝不凭空建档）"
    assert items[0]["supplier_id"] is None, "(b) 缺省降级结果必须是 None"
    assert shared_cache == {("default", "未知商號"): None}, \
        "两处调用点必须共用同一个缓存 dict（只有一条键），而不是各存一份"

    hits = [m for m in msgs if "resolve_supplier_id 失败" in m]
    assert len(hits) == 1, \
        "(c) 共用缓存下必须恰好一条 warning；实测 %r" % (msgs,)
    assert "未知商號" in hits[0], "(c) warning 必须带 name"
    assert "default" in hits[0], "(c) warning 必须带 tenant_id"
    assert "RuntimeError" in hits[0] and "resolve boom" in hits[0], \
        "(c) warning 必须带异常类型与异常文本"


def test_resolve_failure_does_not_break_replay(monkeypatch):
    """③bis 端到端：整条 `--replay` 在 resolve 恒抛错时仍跑完并给出报告。

    why 单测 (a) 只覆盖了两个入口函数；`replay()` 才是生产用的那条命令，
    它在同一批数据上会调用两个入口，必须确认降级不会让它半路抛错。
    """
    src = _fresh_db()
    sku_id, err = db.create_sku("菜心", base_unit="斤", tenant_id="default")
    assert err is None and sku_id, err
    _mk_receipt("未知商號", None, "菜心", matched=1, sku_id=sku_id)

    monkeypatch.setattr(db, "resolve_supplier_id",
                        lambda name, tenant_id=None: (_ for _ in ()).throw(
                            RuntimeError("resolve boom")))
    monkeypatch.setattr(bvsa, "_SOURCE_DB_PATH", src)
    try:
        report = bvsa.replay()
    finally:
        os.environ["DB_PATH"] = src          # replay 会把 DB_PATH 切到副本，用完还原
        importlib.reload(db)

    assert report["items"] == 1
    assert report["plan_stats"]["no_supplier"] == 1


# -------------------------------------------------------------
# 3. 计算不变（golden，判别式）—— 必须排最后：replay 会切 db.DB_PATH
# -------------------------------------------------------------
def test_replay_numbers_unchanged_golden(monkeypatch):
    """④ 同一夹具跑 `replay()`，汇总数字与硬编码期望逐值一致。

    这是「收敛实现不得改变计算结果」的钉死断言：夹具固定为
    「1 条已匹配（行上无 supplier_id，需名字解析）+ 2 条同写法未匹配」，
    所有数字都可手工复算（items=3 行；基线/处理组各命中 1 行 = 33.33%；
    同供应商同写法出现 2 次 → 避免重复建档 1；回填别名 1 条）。
    """
    src = _fresh_db()
    sid, err = db.create_supplier("甲供應商", tenant_id="default")
    assert err is None, err
    sku_id, err = db.create_sku("菜心", base_unit="斤", tenant_id="default")
    assert err is None and sku_id, err
    _mk_receipt("甲供應商", None, "菜心", matched=1, sku_id=sku_id)
    _mk_receipt("甲供應商", sid, "本吧", matched=0)
    _mk_receipt("甲供應商", sid, "本吧", matched=0)

    monkeypatch.setattr(bvsa, "_SOURCE_DB_PATH", src)
    try:
        report = bvsa.replay()
    finally:
        os.environ["DB_PATH"] = src
        importlib.reload(db)

    assert report["items"] == 3
    assert (report["baseline_matched"], report["supplier_dim_only_matched"],
            report["treatment_matched"]) == (1, 1, 1)
    assert report["baseline_rate"] == report["treatment_rate"] == 33.33
    assert report["delta_pp"] == 0.0
    assert (report["alias_fires"], report["alias_differs_from_global"]) == (1, 0)
    assert (report["aliases_written"], report["aliases_failed"]) == (1, 0)
    assert report["plan_stats"]["scanned"] == 1
    assert report["plan_stats"]["candidates"] == 1
    assert report["plan_stats"]["no_supplier"] == 0

    s = report["duplicate_savings"]
    assert (s["groups_anchored"], s["occurrences_anchored"]) == (1, 2)
    assert s["avoided_duplicate_creations"] == 1
    assert s["cross_receipt_groups"] == 1
    assert s["skipped"]["matched_items"] == 1
    cf = s["counterfactual"]
    assert (cf["creations_no_alias"], cf["creations_with_alias"]) == (1, 1)
    assert cf["marginal_saved"] == 0


if __name__ == "__main__":   # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
