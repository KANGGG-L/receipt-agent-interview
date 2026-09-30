# -*- coding: utf-8 -*-
"""冷启动种子工具（`scripts/seed_memory_facts_from_history.py`）用例。

为什么需要这一批：该脚本是"把记忆的可命中总体建出来"的唯一入口（live 现状 0 条 active
记忆 → 有效命中率分母恒为 0 → 目标不可测）。它一旦悄悄失效（四类里少了一类、样本不足的
价格区间被放行、丢弃不计数、重跑重复入队、或**绕过人审直接写 `vendor_memory`**），
后果都不是报错而是"看起来建了总体、其实建错了"，所以每条纪律都要有判别式用例：

1. 四类事实各能产出（含来源口径：写法/标准名/单位频次/版式分布/价格区间与样本数）；
2. **样本不足的 price_range 被拒**（判据是缺省阈值，故把 `MIN_PRICE_SAMPLES` 调小即失败）；
3. 丢弃原因**被计数**（不得静默丢），且计数之和与丢弃总数一致；
4. **未传 `--apply` 时库无写入**（pending 与 vendor_memory 都不动）；
5. `--apply` 幂等：连续两次第二次新增 0 条、pending 行数不变；
6. 产出**只进 pending 人审队列**，`vendor_memory` 生效行始终 0 行；
7. 入队载荷（supplier_id/fact_kind/fact_key/fact_value）**真落库且真能被读回**
   （消费方＝`db.list_pending_memory` → 待审界面逐字段 diff；防本仓高发的"定义了没接线"）；
8. 预计增量口径能跑通（用 `memory_metrics` 的同一口径实现复算，而非第二份 SQL）；
9. **退化项过滤**（T1）：写法经同一归一实现后与标准名相同的别名、常见单位与 SKU
   `base_unit` 相同的单位习惯 —— 都不承载映射信息，必须**不产出且计数**；反例必须照常产出；
10. **分母预估只计过滤后的候选**：构造"全是退化项"的数据集，预计增量必须是 0（不得是
   过滤前那个含虚高的 >0 值）；同时对照值 `delta_unfiltered` 要能把虚高显出来；
11. **供应商质量门三层分类**（V1）：hard-reject（名字带测试/流程标记、括号纯数字 id、
   无非测试形态真实证据）/ suspect（名字像买方 / 真实单据证据 < 阈值）/ pass 三类必须
   各自落位，**hard-reject 的理由被逐条计数**（不静默丢）；
12. **四类产出数与分母预估只统计 pass 类**：suspect 计入单独一栏、hard-reject 只进丢弃，
   两者都**不得**进入分母预估（构造 3 家各 1/1/3 张单据 → 预估只按 pass 家的 3 张算，
   若把 suspect 与 hard-reject 也算进去会是 5）；suspect 候选入队时**必须带可见标记**
   （content 前置理由），不得"默默入队"。

反证（三条，分别在报告里给 md5）：
- 把 `seed_memory_facts_from_history.MIN_PRICE_SAMPLES` 由 3 改成 1 → 第 2、3 条必失败
  （那条 2 个样本的 SKU 会被放行，`price_range` 产出数由 1 变 2、样本不足的丢弃计数由 1 变 0）；
- 把两处退化过滤判据改成恒假（`if False and ...`）→ 第 9、10 条必失败
  （退化别名/单位会被放行、产出数由 1 变 2；"全是退化项"数据集的分母预估由 0 变 >0）；
- 把质量门的 hard-reject **名字判定**注释掉（`_judge_vendor` 里那两段 return 改成不返回）
  → 第 11 条必失败（`测试供应商` 不再被判 hard-reject，家数 1→0）且第 12 条中
  `gaining_scopes`/`after_eligible` 会因该家的候选被放行而改变。
反证补丁必须逐字节还原后再跑一次全绿。

全部离线假数据：模块级临时 DB + 临时 RAG 目录。脚本顶层 `import app.db` 会在**导入期**
按 DB_PATH 执行 `_make_engine()` 的迁移块，故隔离必须发生在 import 之前 —— 不带 DB_PATH
跑就是打 live 库。
"""

import importlib
import json
import os
import sqlite3
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* / scripts 之前设置）
_TMP = tempfile.mkdtemp(prefix="seed_facts_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_seed.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
for _p in (_DEMO_DIR, os.path.join(_DEMO_DIR, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import app.db as db  # noqa: E402
import seed_memory_facts_from_history as seed  # noqa: E402
from app.services.memory_facts import ALLOWED_KINDS, FACT_KEY_MAX_LEN  # noqa: E402


# -------------------------------------------------------------
# 夹具
# -------------------------------------------------------------
def _fresh_db():
    """每个用例独立数据库文件（脚本顶层 import app.db 后按 DB_PATH 现读）。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, "seed_%s.db" % uuid.uuid4().hex[:8])
    importlib.reload(db)
    return db.DB_PATH


def _item(name, sku_id, unit, matched=1, unit_price=10.0):
    """一条收据明细（键与 set_receipt_items 的契约一致）。"""
    return {
        "name": name, "raw_name": name, "quantity": 1, "unit": unit,
        "unit_price": unit_price, "amount": unit_price, "sku_id": sku_id,
        "cost_center_id": None, "confidence": 0.9, "matched": matched,
        "price_anomaly": 0, "price_anomaly_direction": "", "price_diff_percent": 0.0,
        "unit_conversion_warning": "", "fuzzy_candidates": [], "entity_candidates": [],
    }


def _mk_receipt(supplier_name, supplier_id, doc_form, items, image_path=None):
    """建单 + 写明细。缺省给一条**真实形态**的原图路径。

    why 缺省给原图：供应商质量门把「该供应商名下没有任何非测试形态的真实单据证据」判为
    hard-reject，而"没有原图"正是最典型的判据输入之一 —— 若夹具默认不给原图，
    所有既有场景都会被误判成硬拒，测的就不是本意了。
    """
    if image_path is None:
        image_path = ("/Users/ethan/Documents/GitHub/receipt-agent-interview/demo/uploads/"
                      "%s.jpg" % uuid.uuid4().hex[:8])
    rid = db.create_receipt_record(
        supplier_name=supplier_name, supplier_id=supplier_id, image_path=image_path,
        tenant_id="default", status="approved", doc_form=doc_form)
    db.set_receipt_items(rid, items)
    return rid


def _add_in_log(sku_id, vendor, receipt_id, amount, qty, tenant_id="default"):
    """直插 inventory_log 的一条入库流水（只读依赖：该表是价格区间的唯一来源）。"""
    conn = sqlite3.connect(db.DB_PATH)
    try:
        conn.execute(
            "INSERT INTO inventory_log (sku_id, name, qty, unit, amount, vendor, date,"
            " receipt_id, kind, note, created_at, tenant_id)"
            " VALUES (?, '', ?, '', ?, ?, '2026-09-01', ?, 'in', '', '2026-09-01T00:00:00', ?)",
            (sku_id, qty, amount, vendor, receipt_id, tenant_id))
        conn.commit()
    finally:
        conn.close()


def _scaffold():
    """构造小数据集：2 供应商 / 2 SKU / 4 单据 / 5 条入库流水。

    数据是按"四类各能产出 + 一条样本不足 + 退化项 + 质量门两侧"设计的：
    - alias:(甲,本吧)->本地牛腩(3 票，**有映射信息**) ; (甲,白糖)->白糖 与 (乙,白糖)->白糖
      （写法 == 标准名，**退化**，应被过滤并计数）
    - unit :(甲,本地牛腩)=斤(2 票) vs 箱(1 票)，base_unit=包（斤 != 包，**有信息**）;
      (甲,白糖)=箱 与 (乙,白糖)=箱（== base_unit 箱，**退化**，应被过滤并计数）
    - layout:(甲) handwritten 2 : printed 1 ; (乙) printed 1
    - price:(甲,本地牛腩) 3 个样本 8/10/12 ; (甲,白糖) **只有 2 个样本（应被拒）**
    - 质量门：甲 3 张真实单据 → **pass**；乙只有 1 张（< 阈值 3）→ **suspect**
      （故乙的 layout 候选进 `suspect_produced` 而非 `produced`，也不进分母预估）
    """
    sup_a, err = db.create_supplier("甲供應商", tenant_id="default")
    assert err is None, err
    sup_b, err = db.create_supplier("乙供應商", tenant_id="default")
    assert err is None, err
    # 本地牛腩的 base_unit 刻意取「包」，与明细里习惯用的「斤」不同 —— 否则单位事实会
    # 因「与基准单位相同」被判退化（那不是本夹具要演示的形态）。
    sku_niunai, err = db.create_sku("本地牛腩", base_unit="包", tenant_id="default")
    assert err is None, err
    sku_tang, err = db.create_sku("白糖", base_unit="箱", tenant_id="default")
    assert err is None, err

    r1 = _mk_receipt("甲供應商", sup_a, "handwritten",
                     [_item("本吧", sku_niunai, "斤"), _item("白糖", sku_tang, "箱")])
    r2 = _mk_receipt("甲供應商", sup_a, "handwritten", [_item("本吧", sku_niunai, "斤")])
    r3 = _mk_receipt("甲供應商", sup_a, "printed", [_item("本吧", sku_niunai, "箱")])
    r4 = _mk_receipt("乙供應商", sup_b, "printed", [_item("白糖", sku_tang, "箱")])

    # 价格流水：走两条归属路径 —— 有 receipt_id 锚点的、以及只有 vendor 名的
    _add_in_log(sku_niunai, "甲供應商", r1, amount=16.0, qty=2)   # 单价 8
    _add_in_log(sku_niunai, "甲供應商", r2, amount=10.0, qty=1)   # 单价 10
    _add_in_log(sku_niunai, "甲供應商", None, amount=48.0, qty=4)  # 单价 12（按 vendor 名解析）
    _add_in_log(sku_tang, "甲供應商", r1, amount=20.0, qty=1)     # 单价 20（样本不足）
    _add_in_log(sku_tang, "甲供應商", r1, amount=50.0, qty=2)     # 单价 25（样本不足）

    return {"sup_a": sup_a, "sup_b": sup_b, "sku_niunai": sku_niunai,
            "sku_tang": sku_tang, "receipts": [r1, r2, r3, r4]}


def _scaffold_quality():
    """质量门三层各一家：hard-reject（名字带测试标记）/ suspect（仅 1 条证据）/ pass（3 条）。

    三家都给到**同样会产出事实**的数据（同 (供应商, 写法) 的别名 + 同 (供应商, 单位) +
    版式），这样"某类有没有候选产出"只由质量门决定，而不是被数据本身的稀缺性掩盖：
    - hard-reject：`测试供应商`（名字命中「测试」标记）—— 即使有真实原图也照拒；
    - suspect   ：`七月餐室`（名字像买方 + 真实单据证据 1 条 < 阈值 3）；
    - pass      ：`德利行 Tak Lee Hong`（3 条真实单据、名字无标记）。
    单据单位取「斤」、SKU 基准单位取「包」—— 刻意**不**产生退化项，把本夹具的观测面
    限定在质量门本身（退化项由第 9/10 条各自覆盖）。
    """
    sku, err = db.create_sku("本地牛腩", base_unit="包", tenant_id="default")
    assert err is None, err
    hr, err = db.create_supplier("测试供应商", tenant_id="default")
    assert err is None, err
    su, err = db.create_supplier("七月餐室", tenant_id="default")
    assert err is None, err
    ok, err = db.create_supplier("德利行 Tak Lee Hong", tenant_id="default")
    assert err is None, err

    _mk_receipt("测试供应商", hr, "printed_delivery_note", [_item("本吧", sku, "斤")])
    _mk_receipt("七月餐室", su, "printed_delivery_note", [_item("本吧", sku, "斤")])
    for _ in range(3):
        _mk_receipt("德利行 Tak Lee Hong", ok, "printed_delivery_note",
                    [_item("本吧", sku, "斤")])
    return {"hard_reject": hr, "suspect": su, "pass": ok, "sku": sku}


def _pending_count():
    conn = sqlite3.connect(db.DB_PATH)
    try:
        return conn.execute("SELECT count(*) FROM pending_memory").fetchone()[0]
    finally:
        conn.close()


def _vendor_memory_count():
    conn = sqlite3.connect(db.DB_PATH)
    try:
        return conn.execute("SELECT count(*) FROM vendor_memory").fetchone()[0]
    finally:
        conn.close()


# -------------------------------------------------------------
# 1. 四类各能产出，且内容来自指定的历史来源
# -------------------------------------------------------------
def test_four_kinds_each_produce_from_history():
    _fresh_db()
    ids = _scaffold()
    report = seed.plan()

    # alias/unit 各只产出「有映射信息」的那一条（其余是退化项，见第 9 条用例）；
    # layout 只有甲（pass）那一条进 produced —— 乙是 suspect（1 张单据 < 阈值 3）。
    assert report["produced"]["alias"] == 1, report["produced"]
    assert report["produced"]["unit"] == 1, report["produced"]
    assert report["produced"]["layout"] == 1, report["produced"]
    assert report["produced"]["price_range"] == 1, report["produced"]
    assert set(report["produced"]) == set(ALLOWED_KINDS), "四类必须全部覆盖"
    assert report["suspect_produced"]["layout"] == 1, "乙的 layout 属 suspect 一栏"
    assert sum(report["suspect_produced"].values()) == 1

    # layout 的 fact_key 是常量（一个供应商只有一条版式先验），故按 (kind, key, supplier) 索引
    by_key = {(c["kind"], c["fact_key"], c.get("supplier_id")): c
              for c in report["candidates"]}
    # alias：写法「本吧」→ 标准品名「本地牛腩」（取频次最高的 SKU）
    alias = by_key[("alias", "alias:本吧", ids["sup_a"])]
    assert alias["value"] == "本地牛腩" and alias["supplier_id"] == ids["sup_a"]
    # unit：同 (供应商, SKU) 取频次最高的单位
    unit = [c for c in report["candidates"]
            if c["kind"] == "unit" and c["supplier_id"] == ids["sup_a"]
            and c["value"] == "斤"]
    assert unit, "甲供应商对「本地牛腩」的习惯单位应为频次最高的「斤」"
    assert unit[0]["fact_key"] == "unit:本地牛腩"
    assert unit[0]["evidence"]["单位分布"] == {"斤": 2, "箱": 1}
    # layout：取占比最高的一项，且分布原样输出
    lay = by_key[("layout", "layout:doc_form", ids["sup_a"])]
    assert lay["value"] == "handwritten"
    assert report["layout_distribution"]["default|%d" % ids["sup_a"]] == {
        "handwritten": 2, "printed": 1}
    # price_range：最低价/最高价 + 样本数
    pr = by_key[("price_range", "price_range:本地牛腩", ids["sup_a"])]
    assert pr["value"] == "8.00-12.00"
    assert pr["evidence"]["样本数"] == 3
    # 每条产出都过 kind 白名单与 key 长度约束
    for c in report["candidates"]:
        assert c["kind"] in ALLOWED_KINDS
        assert len(c["fact_key"]) <= FACT_KEY_MAX_LEN


# -------------------------------------------------------------
# 2. 样本不足的 price_range 被拒（反证点：阈值改成 1 即失败）
# -------------------------------------------------------------
def test_price_range_below_min_samples_is_rejected():
    """判据刻意用**缺省阈值**（不显式传参）——把 MIN_PRICE_SAMPLES 调小即必须失败。

    「白糖」那组只有 2 条入库流水；在 2 个样本上"算出"价格区间等于凭极少样本编造先验，
    所以它必须被拒，而不是被产出。
    """
    _fresh_db()
    ids = _scaffold()
    report = seed.plan()
    assert report["min_price_samples"] == seed.MIN_PRICE_SAMPLES

    produced_keys = [c["fact_key"] for c in report["candidates"]
                     if c["kind"] == "price_range" and c["decision"] in ("ADD", "UPDATE")]
    assert produced_keys == ["price_range:本地牛腩"], \
        "2 个样本的「白糖」不得产出价格区间，实得 %r" % produced_keys

    rejected = [c for c in report["candidates"]
                if c["kind"] == "price_range"]
    assert len(rejected) == 1, "只该有一条 price_range 候选（样本不足那条被采集期拒掉）"

    # 反过来：显式把阈值降到 2，那组就该产出 —— 证明拒绝确由阈值判据驱动，而非别的原因
    loose = seed.plan(min_price_samples=2)
    assert loose["produced"]["price_range"] == 2
    assert loose["discard_reasons"].get("collector:price_range 样本不足") in (None, 0)

    # 再反过来：仅当阈值 <=1 时该拒绝才会消失（反证的最小改动面）
    assert any("样本不足" in code for code in report["discard_reasons"]), \
        "样本不足必须留下计数痕迹，不得静默丢"


# -------------------------------------------------------------
# 3. 丢弃原因被计数，且与总数一致
# -------------------------------------------------------------
def test_discard_reasons_are_counted_and_consistent():
    _fresh_db()
    _scaffold()
    report = seed.plan()

    assert report["discarded_total"] >= 1
    assert sum(report["discard_reasons"].values()) == report["discarded_total"], \
        "原因计数的总和必须等于丢弃总数（防漏计）"
    assert set(report["discard_reasons"]) == set(report["discard_reason_texts"]), \
        "每个原因码都要有一条可读原文"
    for text in report["discard_reason_texts"].values():
        assert text.strip(), "原因原文不得为空"
    # 采集期丢弃（样本不足）与整合期丢弃共用一张表
    assert report["discard_reasons"]["collector:price_range 样本不足"] == 1
    # 退化项也必须留痕（alias 2 条：甲/乙 的「白糖」；unit 2 条：同上两组的「箱」== base_unit）
    assert report["discard_reasons"][seed._REASON_ALIAS_DEGENERATE] == 2
    assert report["discard_reasons"][seed._REASON_UNIT_DEGENERATE] == 2
    # 两类丢弃分别记账
    assert report["discarded"]["price_range"] == 1
    assert report["discarded"]["alias"] == 2 and report["discarded"]["unit"] == 2


# -------------------------------------------------------------
# 4. 未传 --apply 时库无写入
# -------------------------------------------------------------
def test_dry_run_writes_nothing(capsys):
    _fresh_db()
    _scaffold()
    rc = seed.main(["--json"])
    assert rc == 0
    seed.main([])          # 人类可读输出也走一遍（防两条分支只测一条）
    capsys.readouterr()
    assert _pending_count() == 0, "预演不得写 pending_memory"
    assert _vendor_memory_count() == 0, "预演不得写 vendor_memory"


# -------------------------------------------------------------
# 5+6+7. --apply 只进人审队列、载荷可读回、幂等
# -------------------------------------------------------------
def test_apply_enqueues_pending_only_and_is_idempotent():
    _fresh_db()
    _scaffold()
    first = seed.main(["--json", "--apply"])
    assert first == 0
    first_rows = db.list_pending_memory(status="pending")
    assert len(first_rows) == 5, \
        "pass 4 条（alias1+unit1+layout1+price1）+ suspect 1 条（乙的 layout）"

    # 6. 只进 pending，绝不写 vendor_memory 生效行（规则级事实必须人审）
    assert _vendor_memory_count() == 0, "种子化产物不得直接成为生效记忆"

    # 7. 载荷真落库、真能被读回（消费方＝待审界面的逐字段 diff）
    for r in first_rows:
        assert r["supplier_id"] is not None
        assert r["fact_kind"] in ALLOWED_KINDS
        assert r["fact_key"] and r["fact_value"]
        assert r["reject_reason"] is None       # 产出即通过校验，理由列不该有值
        assert r["status"] == "pending"
        assert r["source_kind"] == seed.SEED_SOURCE_KIND
        assert r["content"].strip()

    # 12. suspect 候选入队时**必须带可见标记**（不得默默入队）：content 前置理由，
    #     且只有那一条带标记（不能把所有入队项都标成可疑）。
    marked = [r for r in first_rows
              if r["content"].startswith(seed._SUSPECT_CONTENT_PREFIX.split("%s")[0])]
    assert len(marked) == 1, "恰好乙的 layout 一条应带可疑标记"
    assert "证据仅 1 条" in marked[0]["content"], marked[0]["content"]

    # 5. 幂等：同候选不重复入队
    seed.main(["--json", "--apply"])
    second_rows = db.list_pending_memory(status="pending")
    assert len(second_rows) == len(first_rows), "重跑不得新增 pending 行"
    assert _vendor_memory_count() == 0


# -------------------------------------------------------------
# 8. 预计能让 effective 分母增加多少（复用 memory_metrics 的同一口径）
# -------------------------------------------------------------
def test_effective_denominator_projection_matches_metric_implementation():
    _fresh_db()
    _scaffold()
    report = seed.plan()
    proj = seed.estimate_effective_denominator_delta(
        report["db_path"], seed._gaining_scopes(report))

    assert proj["available"] is True, proj.get("reason")
    # 只有甲是 pass（乙 1 张单据 → suspect，其候选不进分母预估）
    assert proj["gaining_suppliers"] == 1
    # 4 张单据里，甲占 3 张且此刻一条可用记忆都没有 → 分母 0
    assert proj["before_eligible"] == 0
    # 只有甲的 3 张单据进分母；若把 suspect 的乙也算进去会是 4
    assert proj["after_eligible"] == 3
    assert proj["delta"] == 3
    assert proj["criteria"].strip(), "预计值必须自带计算口径自述"

    # 口径必须真的来自 memory_metrics（而不是本脚本另写一份 SQL）：无候选时增量为 0
    zero = seed.estimate_effective_denominator_delta(report["db_path"], set())
    assert zero["delta"] == 0
    # 模拟只在副本上发生：源库的 vendor_memory 仍然是 0 行
    assert _vendor_memory_count() == 0


def test_projection_degrades_without_crashing(monkeypatch):
    """预计值算不出时必须降级带原因，而不是让整次预演失败。"""
    _fresh_db()
    _scaffold()
    report = seed.plan()

    class _Boom(object):
        @staticmethod
        def collect(_path):
            raise RuntimeError("模拟 memory_metrics 不可用")

    proj = seed.estimate_effective_denominator_delta(
        report["db_path"], seed._gaining_scopes(report), memory_metrics_mod=_Boom)
    assert proj["available"] is False
    assert "memory_metrics" in proj["reason"]
    assert proj["gaining_suppliers"] == 1


# -------------------------------------------------------------
# 9. 退化项过滤：raw == canonical 的别名 / 单位 == base_unit 的事实都必须被拒且计数
#    （反证点：把两处过滤判据改成恒假即失败）
# -------------------------------------------------------------
def test_degenerate_alias_and_unit_are_rejected_and_counted():
    """不承载映射信息的事实不产出，但**必须留痕**（不得静默丢）。

    构造两个 SKU：一个写法 == 标准名且单位 == base_unit（两条都退化），
    另一个写法 != 标准名且单位 != base_unit（两条都有映射信息）—— 前者被拒、后者产出。
    why 该供应商要给到 3 张真实单据：质量门对证据 < 3 的供应商只标 suspect，
    那样候选会进 `suspect_produced` 而不是 `produced`，测的就不是退化项而是质量门了。
    """
    _fresh_db()
    sup, err = db.create_supplier("戊供應商", tenant_id="default")
    assert err is None, err
    sku_same, err = db.create_sku("白糖", base_unit="箱", tenant_id="default")
    assert err is None, err
    sku_diff, err = db.create_sku("本地牛腩", base_unit="包", tenant_id="default")
    assert err is None, err
    # r1：写法 == 标准名（alias 退化）且单位 == base_unit（unit 退化）
    _mk_receipt("戊供應商", sup, "printed", [_item("白糖", sku_same, "箱")])
    # r2/r3：写法「本吧」!= 标准名「本地牛腩」、单位「斤」!= base_unit「包」（两条都有信息）
    _mk_receipt("戊供應商", sup, "printed", [_item("本吧", sku_diff, "斤")])
    _mk_receipt("戊供應商", sup, "printed", [_item("本吧", sku_diff, "斤")])

    report = seed.plan()
    assert report["quality_gate"]["counts"][seed.VENDOR_PASS] == 1, \
        "3 张真实单据 → pass（否则本用例测的是质量门而不是退化项）"

    # 退化项不产出（只留下那条有映射信息的）
    alias_keys = [c["fact_key"] for c in report["candidates"] if c["kind"] == "alias"]
    unit_keys = [c["fact_key"] for c in report["candidates"] if c["kind"] == "unit"]
    assert alias_keys == ["alias:本吧"], alias_keys
    assert unit_keys == ["unit:本地牛腩"], unit_keys
    assert report["produced"]["alias"] == 1 and report["produced"]["unit"] == 1

    # 退化项被计数（原因码与文案都要在，且按 kind 的丢弃数对得上）
    assert report["discard_reasons"][seed._REASON_ALIAS_DEGENERATE] == 1
    assert report["discard_reasons"][seed._REASON_UNIT_DEGENERATE] == 1
    assert report["discarded"]["alias"] == 1
    assert report["discarded"]["unit"] == 1
    assert sum(report["discard_reasons"].values()) == report["discarded_total"]


def test_denominator_projection_counts_only_filtered_candidates():
    """「全是退化项」的数据集：过滤后无任何候选 → 预计增量必须是 0，不得是过滤前的虚高。

    这是"分母预估只基于过滤后的候选"的判别式：若退化项被算进预估，这里会得到 >0。
    why 该供应商要给到 3 张真实单据（= pass）：对照值只撤销**退化项过滤**这一层，硬拒与
    suspect 两层保持不变；若该供应商是 suspect，它会同时被 suspect 层挡在对照之外，
    这条用例就测不到"退化项过滤"本身了。
    """
    _fresh_db()
    sup, err = db.create_supplier("丁供應商", tenant_id="default")
    assert err is None, err
    sku, err = db.create_sku("白糖", base_unit="箱", tenant_id="default")
    assert err is None, err
    # 写法 == 标准名（alias 退化）+ 单位 == base_unit（unit 退化）+ 无 doc_form、无入库流水
    for _ in range(3):
        _mk_receipt("丁供應商", sup, "", [_item("白糖", sku, "箱")])

    report = seed.plan()
    assert report["quality_gate"]["counts"][seed.VENDOR_PASS] == 1, report["quality_gate"]
    assert report["produced"] == {"alias": 0, "unit": 0, "layout": 0, "price_range": 0}
    assert seed._gaining_scopes(report) == set(), "过滤后不该有任何分母贡献者"
    assert seed._prefilter_gaining_scopes(report) == {("default", sup)}, \
        "过滤前应仍含该供应商（仅作对照，不是报告口径）"

    proj = seed.estimate_effective_denominator_delta(
        report["db_path"], seed._gaining_scopes(report),
        gaining_unfiltered=seed._prefilter_gaining_scopes(report))
    assert proj["available"] is True, proj.get("reason")
    # 报告口径：过滤后无候选 → 增量为 0（不是 >0 的虚高）
    assert proj["delta"] == 0, "预计增量只能基于过滤后的候选"
    assert proj["gaining_suppliers"] == 0
    # 对照：把退化项也算作候选时才会出现虚高（该家 3 张单据进分母）—— 两个数字不可混读
    assert proj["delta_unfiltered"] == 3
    assert proj["gaining_suppliers_unfiltered"] == 1
    # 模拟只在副本上发生：源库没有任何生效记忆
    assert _vendor_memory_count() == 0


# -------------------------------------------------------------
# 11. 供应商质量门三层分类（反证点：注释掉 hard-reject 的名字判定即失败）
# -------------------------------------------------------------
def test_vendor_quality_gate_three_classes():
    """hard-reject / suspect / pass 三类各自落位，且 hard-reject 的理由被逐条计数。

    三家给到同样会产出事实的数据，故"某类有没有候选产出"只由质量门决定。
    """
    _fresh_db()
    ids = _scaffold_quality()
    report = seed.plan()
    gate = report["quality_gate"]

    assert gate["counts"] == {seed.VENDOR_HARD_REJECT: 1, seed.VENDOR_SUSPECT: 1,
                              seed.VENDOR_PASS: 1}, gate["counts"]
    hr = gate["suppliers"][seed.VENDOR_HARD_REJECT]
    su = gate["suppliers"][seed.VENDOR_SUSPECT]
    ok = gate["suppliers"][seed.VENDOR_PASS]
    assert [e["supplier_id"] for e in hr] == [ids["hard_reject"]]
    assert [e["supplier_id"] for e in su] == [ids["suspect"]]
    assert [e["supplier_id"] for e in ok] == [ids["pass"]]
    # 理由必须给依据（不只给结论）：名字标记 + 该家的证据读数
    hr0 = hr[0]
    assert hr0["reason_code"] == seed._HR_NAME_MARKER, hr0
    assert "测试" in hr0["reason"] and hr0["real_receipts"] == 1
    su0 = su[0]
    assert su0["reason_code"] == seed._SUSPECT_BUYER_NAME, su0
    assert "买方" in su0["reason"] and "证据仅 1 条" in su0["reason"], su0["reason"]
    assert ok[0]["reason_code"] == "" and ok[0]["real_receipts"] == 3

    # ① hard-reject 直接不产出（一条候选都不到 candidates），且理由被逐条计数
    assert all(c["supplier_id"] != ids["hard_reject"] for c in report["candidates"])
    hr_codes = [k for k in report["discard_reasons"]
                if k.startswith("quality:hard-reject")]
    assert len(hr_codes) == 1, report["discard_reasons"]
    assert report["discard_reasons"][hr_codes[0]] == 3 == hr0["dropped_candidates"], \
        "该家 3 条候选（alias/unit/layout）都要计入丢弃原因，不得静默丢"
    assert "测试" in report["discard_reason_texts"][hr_codes[0]]
    assert sum(report["discard_reasons"].values()) == report["discarded_total"]

    # ② suspect 产出但打标：只进 suspect 一栏，不进 produced
    assert report["produced"] == {"alias": 1, "unit": 1, "layout": 1, "price_range": 0}
    assert report["suspect_produced"] == {"alias": 1, "unit": 1, "layout": 1,
                                          "price_range": 0}
    su_candidates = [c for c in report["candidates"] if c.get("suspect")]
    assert su_candidates and all(c["supplier_id"] == ids["suspect"]
                                 for c in su_candidates)
    assert all(c.get("suspect_reason") for c in su_candidates)
    # ③ pass 家的候选不带标记
    ok_candidates = [c for c in report["candidates"]
                     if c.get("decision") in ("ADD", "UPDATE") and not c.get("suspect")]
    assert ok_candidates and all(c["supplier_id"] == ids["pass"] for c in ok_candidates)


def test_denominator_projection_excludes_suspect_and_hard_reject():
    """分母预估只计 pass 类：suspect 与 hard-reject 都不得进（否则预估再次虚高）。"""
    _fresh_db()
    ids = _scaffold_quality()
    report = seed.plan()

    gaining = seed._gaining_scopes(report)
    assert gaining == {("default", ids["pass"])}, gaining
    assert {s for _, s in gaining}.isdisjoint({ids["suspect"], ids["hard_reject"]})
    # suspect 仍可单独查出来（入队项带标记），只是不参与预估
    assert seed._suspect_scopes(report) == {("default", ids["suspect"])}

    proj = seed.estimate_effective_denominator_delta(
        report["db_path"], gaining,
        gaining_unfiltered=seed._prefilter_gaining_scopes(report))
    assert proj["available"] is True, proj.get("reason")
    assert proj["gaining_suppliers"] == 1
    assert proj["before_eligible"] == 0
    # 全库共 5 张单据（hr 1 / su 1 / pass 3）；只有 pass 家的 3 张进分母。
    # 若 suspect 或 hard-reject 被算进去，这里会是 4 或 5。
    assert proj["after_eligible"] == 3, "分母预估必须排除 suspect 与 hard-reject"
    assert proj["delta"] == 3
    # 对照值只含 pass ∪ 退化项（本夹具无退化项）→ 与报告口径一致
    assert proj["delta_unfiltered"] == 3
    assert _vendor_memory_count() == 0, "模拟只在副本上发生"


def test_suspect_evidence_threshold_is_configurable(capsys):
    """「真实单据证据不足」这条 suspect 准则的阈值必须**可配**（不是写死的 3）。

    构造一个名字中性、只有 2 张真实单据的供应商：缺省阈值 3 → suspect；阈值降到 2 → pass。
    why 走 CLI 一次：证明 `--suspect-min-evidence` **真的被消费**（本仓高发的"定义了没接线"——
    加了形参但调用点不传，等价于开关不存在）。
    """
    _fresh_db()
    sku, err = db.create_sku("本地牛腩", base_unit="包", tenant_id="default")
    assert err is None, err
    sup, err = db.create_supplier("丙供應商", tenant_id="default")
    assert err is None, err
    for _ in range(2):
        _mk_receipt("丙供應商", sup, "printed_delivery_note", [_item("本吧", sku, "斤")])

    # 缺省阈值 3：2 条证据 → suspect，候选进 suspect 一栏
    strict = seed.plan()
    assert strict["quality_gate"]["suspect_min_evidence"] == 3
    assert strict["quality_gate"]["suppliers"][seed.VENDOR_SUSPECT][0]["supplier_id"] == sup
    assert strict["quality_gate"]["suppliers"][seed.VENDOR_SUSPECT][0]["reason_code"] \
        == seed._SUSPECT_FEW_EVIDENCE
    assert strict["produced"]["alias"] == 0, "suspect 不计入 pass 产出数"

    # 阈值降到 2 → 该家转为 pass，候选正常产出
    loose = seed.plan(suspect_min_evidence=2)
    assert loose["quality_gate"]["counts"] == {seed.VENDOR_HARD_REJECT: 0,
                                               seed.VENDOR_SUSPECT: 0, seed.VENDOR_PASS: 1}
    assert loose["produced"]["alias"] == 1

    # CLI 形参确实被消费（不只是被 argparse 收下）
    rc = seed.main(["--json", "--suspect-min-evidence", "2"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["quality_gate"]["suspect_min_evidence"] == 2
    assert payload["quality_gate"]["counts"][seed.VENDOR_PASS] == 1


# -------------------------------------------------------------
# 13. 采集器对"不可采"的数据如实计数、不编造
# -------------------------------------------------------------
def test_collectors_reject_unanchored_and_blank_with_reasons():
    """无供应商锚点 / 写法为空 / SKU 已消失 的数据必须被计数拒绝，而非编造主体。"""
    _fresh_db()
    sup, err = db.create_supplier("丙供應商", tenant_id="default")
    assert err is None, err
    sku, err = db.create_sku("菜心", base_unit="斤", tenant_id="default")
    assert err is None, err
    # 单据有 supplier_id，但明细写法为空 → alias/unit 采集器各自计入拒绝
    rid = _mk_receipt("丙供應商", sup, "handwritten", [_item("   ", sku, "斤")])
    # 明细指向一个不存在的 SKU（悬空）→ 不产出，也不能崩
    db.set_receipt_items(rid, [_item("菜心", 99999, "斤")])
    _add_in_log(99999, "丙供應商", rid, amount=10.0, qty=1)

    report = seed.plan()
    assert report["produced"]["alias"] == 0
    assert report["produced"]["price_range"] == 0
    # 悬空 SKU 的入库流水：价格采集器要拒（skus 里不存在该 id）
    assert any("price_range 标准名为空" in c for c in report["discard_reasons"])
    assert sum(report["discard_reasons"].values()) == report["discarded_total"]


def test_sku_key_falls_back_to_sku_id_when_name_too_long():
    """超长标准名 → fact_key 回落 sku_id 形态而不是让事实静默消失。"""
    long_name = "超" * 80
    key = seed._sku_key("unit", 42, long_name)
    assert key == "unit:#42"
    assert len(key) <= FACT_KEY_MAX_LEN
    short = seed._sku_key("unit", 42, "本地牛腩")
    assert short == "unit:本地牛腩"


def test_report_is_json_serializable():
    """--json 分支的输出必须可序列化（否则报告给不出去）。"""
    _fresh_db()
    _scaffold()
    report = seed.plan()
    report["effective_denominator_projection"] = seed.estimate_effective_denominator_delta(
        report["db_path"], seed._gaining_scopes(report))
    printable = {k: v for k, v in report.items() if k != "enqueue_items"}
    text = json.dumps(printable, ensure_ascii=False)
    assert "price_range" in text
