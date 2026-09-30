# -*- coding: utf-8 -*-
"""VendorMemory 目标指标度量（只读）。

为什么单独成脚本：计划 2026-09-22-vendormemory-convergence.md 的验收要求「每批收口给出指标数字」，
而这些数字此前没有任何可复算入口（gap 05 里的人工 SQL 无法对比）。本脚本把口径固化下来，
使「记忆命中率 / SKU 匹配率 / 有记忆 vs 无记忆的修改率 / 字段级未采纳率」四组数字可一键复现与对比。

用法：
  python demo/scripts/memory_metrics.py                # 打印当前指标
  python demo/scripts/memory_metrics.py --json         # 输出 JSON
  python demo/scripts/memory_metrics.py --save /tmp/baseline.json   # 存基线
  python demo/scripts/memory_metrics.py --compare /tmp/baseline.json  # 与基线对比

口径（与计划 §1 一致，勿私改）：
- 记忆命中率（旧口径，字段 `memory_hit`）分母＝全部单据数；分子＝rag_context_json 非空。
  why 保留旧口径：它是一个连续序列，用来对比说明「两个数字的差异来自分母定义、而不是行为变化」。
  它与下面的有效命中率**不可混读**（本仓纪律：per-instance 真实标记 vs 主体代理变量）。
- 有效命中率（P2-T6 验收口径，字段 `memory_hit_effective`）：
  * 分母 `eligible_denominator` ＝ 「供应商可确定」**且**「该供应商有可用记忆」的单据数；
  * 分子 `injected` ＝ 分母中 rag_context_json 非空（本次真的注入了记忆）的单据数；
  * 「供应商可确定」＝ `receipts.supplier_name` 非空 —— 名字为空时注入入口
    `rag.retrieve_context(vendor)` 直接返回空串，属**未尝试**，不是未命中；
  * 「该供应商有可用记忆」＝ 同租户下存在 `status='active'`、未过期（`valid_to`/TTL）、
    带内容（notes/sample 非空，否则检索侧跳过）、`decay_score > 归档阈值` 的
    `vendor_memory` 行，且该行与单据按 `supplier_id` 锚点或供应商名精确匹配
    （与 `db.list_vendor_memory` 的默认读口径对齐）；
  * **「未尝试」（供应商不可确定）与「无条件兑现」（供应商可确定但该供应商根本没有可用记忆）
    两类都不计入分子、也不计入分母** —— 把「本就不可能命中」的数据算进分母，会让这个比率
    既不能衡量能力（数字偏低）也不能衡量回归（变小看不出为什么），而这看起来只是一个
    偏低的百分数、不像错误；
  * 分母为 0 时 `effective_rate_pct` 输出 null（＝无数据），**不用 0% 冒充**（0% 会被读成
    「完全不命中」，而事实是「没有可命中的样本」）；
  * **总开关**：settings 键 `memory_enabled` 关闭时，整个口径判为「未测量」而不是 0% ——
    `eligible_denominator` 与 `injected` 归 0、`effective_rate_pct=null`、`no_data=true`，
    并置显式理由 `disabled_by_switch=true`（消费方：demo/static/js/main.js 的看板分支）。
    why：开关关闭与「开着但一次都没命中」此前在报告里长得一模一样（都是分母 1 / 分子 0），
    而前者根本不该声称任何命中率。「没测」与「测了没命中」是两件事，混在一起会让这个数字
    既不能衡量能力（数字偏低）也不能衡量回归（变小看不出为什么）；
  * 分子/分母的完整判定规则同时写进输出的 `criteria` 字段（口径自述），便于事后审计。
- 匹配标记占比（口径名 `matched_flag_rate`，对象键仍叫 `sku_match` 以兼容看板消费方）＝
  receipt_items.matched=1 / 明细行总数。**它只统计落库标记，不等于匹配逻辑的成功率** ——
  匹配逻辑的成功率要复放 `_match_sku`，见 `backfill_vendor_sku_alias.py --replay` 的
  `simulated_match_rate`。两个数都常被叫作「SKU 匹配率」而口径完全不同，两个 note 互相
  指向并声明不可混读（本仓纪律：同一指标名两个口径必须显式命名 + 交叉引用）。
- 修改率＝receipts.status='edited' / 该组单据数，按「供应商是否有记忆行」分组（观察性 A/B，非因果）。
- 未采纳率＝ai_decision_log.adopted=0 / 决策总数。
只读：以 mode=ro 打开库，绝不写入。脚本只依赖 sqlite3，**不 import app.db** ——
本项目的 `app.db` 在 import 期就执行 `_make_engine()` 的迁移块（会按 DB_PATH 做 ALTER），
度量脚本绝不能被那种副作用牵连。
"""

import argparse
import json
import os
import sqlite3
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.environ.get("DB_PATH") or os.path.join(REPO, "demo", "receipt_demo.db")

# -------------------------------------------------------------
# 口径命名（R9：同一指标名两个口径）
# -------------------------------------------------------------
# 本脚本数出来的是 receipt_items.matched=1 这个**落库标记**占明细行总数的比例，键名
# SKU_MATCH_FLAG_KEY；另一侧 backfill_vendor_sku_alias.py --replay 复放了匹配逻辑
# （_match_sku，含核心词归一），键名 SIMULATED_MATCH_RATE_KEY。两个数此前都叫「SKU 匹配率」
# 却口径不同（实测 23.73% vs 36.10%），汇报时会被当成互相矛盾。命名单独成常量，
# 既让 note 里的交叉引用可自证、也让 tests/test_metric_wording_no_confusion.py 能按名断言。
SKU_MATCH_FLAG_KEY = "matched_flag_rate"
SIMULATED_MATCH_RATE_KEY = "simulated_match_rate"

# 交叉引用自述（must_contain 对侧键名由常量插值保证，防「改了命名忘了改文案」）
_MATCHED_FLAG_NOTE = (
    "本指标（%s）只统计 receipt_items.matched=1 这个**落库标记**占明细行总数的比例，"
    "**不等于匹配逻辑的成功率**；匹配逻辑的成功率见 backfill_vendor_sku_alias.py --replay 的 "
    "%s（它复放了 _match_sku）。两者都常被叫作「SKU 匹配率」但口径不同（分子口径不同："
    "本指标只认标记、后者真跑匹配），不可混读、不可相互比较。"
    % (SKU_MATCH_FLAG_KEY, SIMULATED_MATCH_RATE_KEY)
)

# -------------------------------------------------------------
# 有效命中率口径的两个条件（各自单独成常量）
# -------------------------------------------------------------
# 分母的两个条件单独成常量，不只是为了可读：这样「把某个条件去掉 → 口径必须变化」的
# 自校准/反证可以直接替换这一个片段来执行（见 tests/test_memory_effective_hit_rate.py），
# 不必在语法上重写整条 SQL。口径退化（例如悄悄退化成按全部单据当分母）因此有信号。
#
# 条件一：供应商可确定。名字为空时注入入口 rag.retrieve_context(vendor) 直接返回空串，
# 该单据从未被检索过（未尝试）—— 既不计入分子也不计入分母。
VENDOR_DETERMINABLE_PREDICATE = "r.name <> ''"
# 条件二：该供应商有可用记忆（同租户 + active + 未过期 + 未劣化归档 + 锚点或名字命中）。
# 「无条件兑现」的单据（供应商可确定但根本没有可用记忆）排除在分母之外。
USABLE_MEMORY_PREDICATE = (
    "EXISTS (SELECT 1 FROM usable u WHERE u.tid = r.tid "
    "AND ((r.supplier_id IS NOT NULL AND u.supplier_id = r.supplier_id) "
    "OR u.vendor = r.name))"
)


def _conn(path=None):
    return sqlite3.connect("file:%s?mode=ro" % (path or DB), uri=True)


def _setting_value(c, key, default):
    """按 app_settings 现读设置键（缺省/不可解析回退 default）。

    why 用 SQL 现读而不是 import app.services.settings_service：本脚本必须保持
    「不 import app.*」（app.db 在 import 期执行迁移块）。缺省值与
    settings_service.SETTINGS_DEFAULTS 保持同一取值（memory_archive_threshold=0.5、
    memory_ttl_days=0），键缺失时行为与生产读侧一致。
    """
    try:
        row = c.execute("select value from app_settings where key=?", (key,)).fetchone()
    except sqlite3.Error:
        return default, None
    raw = None if not row else row[0]
    if raw is None or str(raw).strip() == "":
        return default, raw
    try:
        return float(raw), raw
    except (TypeError, ValueError):
        return default, raw


def _fmt_rate_pct(v):
    """比率文本：None ＝「无数据」而不是 0%（分母为 0 时不允许用 0% 冒充）。"""
    return "无数据（分母为 0）" if v is None else "%s%%" % v


def _eff_rate_text(eff):
    """有效命中率的文本：**开关关闭**与**分母为 0** 是两种不同的「无数据」，文案必须区分。

    why 不直接复用 `_fmt_rate_pct`：它把 None 一律说成「分母为 0」，而开关关闭时分母是被
    我们主动归零的、库里未必没有记忆 —— 沿用那句会给出错误的理由（本仓纪律：读不出来就
    别折成一个合法值／别编造理由）。
    """
    if eff.get("disabled_by_switch"):
        return "无数据（记忆总开关已关闭）"
    return _fmt_rate_pct(eff.get("effective_rate_pct"))


def _load_coerce_bool_raw():
    """取 settings_service 的布尔词表实现（唯一实现），供本脚本解析开关原始值。

    why 这个 import 不违反本脚本「不 import app.db」的纪律：settings_service 顶层只
    `import logging`（`from app import db` 在函数体内惰性执行），import 它**不会**触发
    app.db 的 import 期迁移。此结论由
    `tests/test_memory_effective_hit_rate.py::test_switch_read_does_not_import_app_db`
    用子进程实测钉住（断言 `'app.db' not in sys.modules` 且临时库文件根本没被创建）。
    词表必须复用这一份而不是在脚本里再抄一份：本仓①类缺陷就是「同一根因多份实现」
    （此前脚本用 `float(raw)` 解析开关，`false`/`off` 会抛 ValueError 后回退缺省 True ——
    即把**显式关闭读成开启**，正是「读错源导致的假绿」）。
    返回 None 表示解析入口不可用（环境缺 app 包等）；调用方按缺省「开启」处理并在
    criteria 里留痕，不静默。
    """
    try:
        demo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if demo_dir not in sys.path:
            sys.path.insert(0, demo_dir)
        from app.services.settings_service import coerce_bool_raw
        return coerce_bool_raw
    except Exception:
        return None


# 模块级解析一次（纯函数，无副作用）；None ＝ 解析入口不可用
_COERCE_BOOL_RAW = _load_coerce_bool_raw()


def _read_memory_switch(c):
    """读记忆总开关，返回 (enabled, raw, read_error)。

    语义与生产读侧 `rag.memory_enabled()` 逐条一致：
    - 键未设置 / 空串 → 缺省 True（与接线前行为一致）；
    - 布尔词（大小写不敏感，词表复用 settings_service.coerce_bool_raw）→ 对应值；
    - 无法识别的值 → 缺省 True；
    - 读取失败（表缺失 / 解析入口不可用）→ 视为**开启**并回传 error 文本：配置系统故障
      不得静默改变行为，更不得凭空声称「已关闭」（那会把未测量写成 0%）。
    """
    try:
        row = c.execute("select value from app_settings where key=?",
                        ("memory_enabled",)).fetchone()
    except sqlite3.Error as e:
        return True, None, "读 app_settings 失败: %s" % e
    raw = None if not row else row[0]
    if _COERCE_BOOL_RAW is None:
        return True, raw, "布尔解析入口不可用（settings_service 无法 import），按缺省开启处理"
    try:
        return bool(_COERCE_BOOL_RAW(raw, True)), raw, None
    except Exception as e:
        return True, raw, "解析 memory_enabled 失败: %s" % e


def _collect_memory_hit_effective(c):
    """P2-T6 验收口径：有效命中率（分母＝供应商可确定且有可用记忆的单据数）。

    why 单列一段而不是改旧字段：旧口径（分母＝全部单据）把「本就不可能命中」的单据也
    算进了分母 —— 供应商无法确定的（连该查谁的记忆都不知道）与该供应商根本没有记忆的
    都没有条件兑现。两个数字回答的是不同问题，必须并列输出且声明不可混读。

    「可用记忆」的判据与 `db.list_vendor_memory`（默认读口径）对齐：
    同租户、`status='active'`、未过期（`valid_to` 已过 或 `created_at + TTL` 已过即过期，
    时间戳无法解析时按未过期处理 —— 与 `db._memory_row_expired` 一致）、
    `decay_score > memory_archive_threshold`、且带内容（`rag.retrieve_context` 对
    notes 与 sample 都为空的行会 `continue` 跳过，那种行不可能被注入）。刻意**不**复制
    rag 的 fuzzy 相关度兜底通道：那是子串/核心词猜测，无法在纯 SQL 里复现；本字段因此
    在分母上是**保守**的（见 criteria）。

    总开关（Q3-M3）：`memory_enabled` 关闭时本口径**整体不测量** —— 分母归 0、分子必然为 0
    （分子是分母的子集）、`effective_rate_pct=null`、`no_data=true`、`disabled_by_switch=true`。
    此前开关虽被读出来却**从未被使用**（`_switch` 定义后无人消费，本仓②类形态），于是
    「开关关着」与「开着但一次没命中」在同一份报告里无法区分，且关闭态会输出 0.00%
    ——把「没测」说成「测了，一次都没命中」。
    """
    arch, arch_raw = _setting_value(c, "memory_archive_threshold", 0.5)
    ttl, ttl_raw = _setting_value(c, "memory_ttl_days", 0.0)
    ttl_days = int(ttl) if ttl and ttl > 0 else 0
    mem_on, switch_raw, switch_err = _read_memory_switch(c)

    ttl_clause = "1 = 1"
    if ttl_days:
        ttl_clause = ("(ifnull(trim(created_at),'') = '' OR datetime(created_at) IS NULL "
                      "OR datetime(created_at, '+%d days') > datetime('now'))" % ttl_days)

    sql = """
        WITH usable AS (
            SELECT vendor, ifnull(tenant_id,'default') AS tid, supplier_id
              FROM vendor_memory
             WHERE status = 'active'
               AND ifnull(decay_score, 1.0) > :arch
               AND (ifnull(trim(valid_to),'') = '' OR datetime(valid_to) IS NULL
                    OR datetime(valid_to) > datetime('now'))
               AND (ifnull(trim(notes),'') <> '' OR ifnull(trim(sample),'') <> '')
               AND {ttl_clause}
        ),
        r AS (
            SELECT id,
                   trim(supplier_name) AS name,
                   supplier_id,
                   ifnull(tenant_id,'default') AS tid,
                   CASE WHEN ifnull(rag_context_json,'') NOT IN ('', '{{}}', '""')
                        THEN 1 ELSE 0 END AS inj
              FROM receipts
        )
        SELECT count(*),
               sum(CASE WHEN {det} THEN 1 ELSE 0 END),
               sum(CASE WHEN {det} AND {usable} THEN 1 ELSE 0 END),
               sum(CASE WHEN {det} AND {usable} AND r.inj = 1 THEN 1 ELSE 0 END)
          FROM r
    """.format(ttl_clause=ttl_clause, det=VENDOR_DETERMINABLE_PREDICATE,
               usable=USABLE_MEMORY_PREDICATE)
    total, determinable, eligible, injected = c.execute(sql, {"arch": arch}).fetchone()
    determinable, eligible, injected = determinable or 0, eligible or 0, injected or 0
    disabled = not mem_on
    if disabled:
        # 开关关闭 ⇒ 未测量：分母归 0（分子是分母的子集，故一并归 0）。excluded 也置 0 ——
        # 该字段名说的是「因**无可用记忆**被排除」，而开关关闭时的差额不是这个原因；
        # 若照旧填 determinable-eligible，报告就会把「开关关了」写成「库里没有记忆」，
        # 只是换了一个错误的理由（本仓纪律：读不出来就别折成一个合法值）。
        eligible = injected = 0
    excluded = 0 if disabled else determinable - eligible
    no_data = eligible == 0
    return {
        "receipts_total": total or 0,
        "vendor_determinable": determinable,
        "excluded_no_usable_memory": excluded,
        "eligible_denominator": eligible,
        "injected": injected,
        "effective_rate_pct": None if no_data else round(100.0 * injected / eligible, 2),
        "no_data": no_data,
        "disabled_by_switch": disabled,
        "incomparable_with": "memory_hit",
        "incomparable_reason": (
            "旧口径 memory_hit.rate_pct 的分母是全部单据（含『供应商不可确定』与『该供应商无可用记忆』"
            "两类无缘命中的单据）；本字段分母只含『有条件命中』的单据。两者数值不可混读、不可相互比较，"
            "差异来自分母定义而不是行为变化。"),
        "criteria": {
            "denominator": "供应商可确定 且 该供应商有可用记忆 的单据数",
            "numerator": "分母中 rag_context_json 非空（本次真的注入了记忆）的单据数",
            "vendor_determinable": (
                "receipts.supplier_name 非空（%s）；名字为空＝注入入口直接返回空串＝未尝试，"
                "既不计入分子也不计入分母" % VENDOR_DETERMINABLE_PREDICATE),
            "usable_memory": (
                "同租户下存在 status='active' 且未过期（valid_to 未过；TTL=%d 天，0=不过期）"
                "且 decay_score > 归档阈值 %s 且带内容（notes/sample 至少一项非空，"
                "否则 rag 检索侧会跳过、不可能注入）的 vendor_memory 行，且按 supplier_id "
                "锚点或供应商名精确匹配（%s）" % (ttl_days, arch, USABLE_MEMORY_PREDICATE)),
            "not_attempted": "供应商不可确定的单据：不计入分子，也不从分母里剔除后虚高 —— 它压根不进分母",
            "denominator_zero": "分母为 0 时 effective_rate_pct=null（无数据），不输出 0%",
            "switch_off": (
                "总开关（settings 键 memory_enabled）关闭时不计分母：eligible_denominator 与 "
                "injected 归 0、effective_rate_pct=null、no_data=true、disabled_by_switch=true，"
                "excluded_no_usable_memory 也置 0（该差额由开关解释，不是「该供应商无可用记忆」）。"
                "「开关关闭」与「开关开启但一次都没命中」（分母>0、rate=0.0%）语义相反，必须区分"),
            "match_channel_gap": (
                "不含 rag 的 fuzzy 相关度兜底通道（子串/核心词猜测无法在纯 SQL 复现）→ "
                "本分母是保守下界；若日后补齐，需同步修改本字段"),
            "settings": {
                "memory_archive_threshold": arch, "memory_archive_threshold_raw": arch_raw,
                "memory_ttl_days": ttl_days, "memory_ttl_days_raw": ttl_raw,
                "memory_enabled": mem_on, "memory_enabled_raw": switch_raw,
                "memory_enabled_read_error": switch_err,
            },
            "source": "只读 SQL（receipts / vendor_memory / app_settings）；不 import app.db（import 期会执行迁移）",
        },
    }


def collect(db_path=None):
    path = db_path or DB
    c = _conn(path)
    try:
        out = {"db_path": path}

        total, with_ctx = c.execute(
            "select count(*), sum(case when ifnull(rag_context_json,'') not in ('','{}','\"\"') "
            "then 1 else 0 end) from receipts").fetchone()
        out["memory_hit"] = {
            "total_receipts": total,
            "with_memory_context": with_ctx or 0,
            "rate_pct": round(100.0 * (with_ctx or 0) / total, 2) if total else 0.0,
            "note": ("旧口径（分母=全部单据）：含『供应商不可确定』与『该供应商无可用记忆』两类"
                     "无缘命中的单据，故它既不能衡量能力也不能衡量回归。有效命中率见 "
                     "memory_hit_effective，两者不可混读。"),
        }

        out["memory_hit_effective"] = _collect_memory_hit_effective(c)

        rows = c.execute(
            "select case when exists(select 1 from vendor_memory vm where vm.vendor = r.supplier_name) "
            "then 'has_memory' else 'no_memory' end g, count(*), "
            "sum(case when r.status='edited' then 1 else 0 end) from receipts r group by 1").fetchall()
        ab = {}
        for g, n, edited in rows:
            ab[g] = {"receipts": n, "edited": edited or 0,
                     "edited_rate_pct": round(100.0 * (edited or 0) / n, 2) if n else 0.0}
        out["edit_rate_ab"] = ab
        if "has_memory" in ab and "no_memory" in ab:
            out["edit_rate_ab"]["delta_pp"] = round(
                ab["has_memory"]["edited_rate_pct"] - ab["no_memory"]["edited_rate_pct"], 2)

        total_items, matched = c.execute(
            "select count(*), sum(case when matched=1 then 1 else 0 end) from receipt_items").fetchone()
        # 计算逻辑与改动前逐值一致：`rate_pct` 保持原来的公式与取值（看板
        # demo/static/js/main.js 与 demo/tests/test_memory_metrics_dashboard.py 按它消费）。
        # 新增的 matched_flag_rate 只是把同一个值挂到「带口径的键名」上，并把分子/分母显式
        # 输出（便于一眼看出它与 simulated_match_rate 的差异来自分子口径而非分母）。
        _mf_rate = round(100.0 * (matched or 0) / total_items, 2) if total_items else 0.0
        out["sku_match"] = {
            "items": total_items, "matched": matched or 0,
            "rate_pct": _mf_rate,
            SKU_MATCH_FLAG_KEY: _mf_rate,
            "numerator": matched or 0,
            "denominator": total_items or 0,
            "incomparable_with": SIMULATED_MATCH_RATE_KEY,
            "note": _MATCHED_FLAG_NOTE,
        }

        dec, not_adopted, halluc = c.execute(
            "select count(*), sum(case when adopted=0 then 1 else 0 end), "
            "sum(case when is_hallucination=1 then 1 else 0 end) from ai_decision_log").fetchone()
        out["decision_log"] = {
            "decisions": dec, "not_adopted": not_adopted or 0, "hallucination": halluc or 0,
            "not_adopted_rate_pct": round(100.0 * (not_adopted or 0) / dec, 2) if dec else 0.0,
        }

        vm_total, vm_active, vm_superseded = c.execute(
            "select count(*), sum(case when status='active' then 1 else 0 end), "
            "sum(case when status='superseded' then 1 else 0 end) from vendor_memory").fetchone()
        out["vendor_memory"] = {
            "rows": vm_total, "active": vm_active or 0, "superseded": vm_superseded or 0,
        }
        return out
    finally:
        c.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--save")
    ap.add_argument("--compare")
    a = ap.parse_args()
    cur = collect()
    if a.save:
        with open(a.save, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=2)
        print("已保存基线 -> %s" % a.save)
    if a.compare:
        with open(a.compare, encoding="utf-8") as f:
            base = json.load(f)
        print("=== 与基线对比（基线: %s）===" % a.compare)
        for k, path in (("memory_hit", "rate_pct"),):
            b, n = base.get(k, {}).get(path), cur.get(k, {}).get(path)
            if b is not None and n is not None:
                print("%-28s %s%% -> %s%% (%+.2f)" % (k, b, n, n - b))
        # 匹配标记占比单独一行：**以口径名 matched_flag_rate 出现**，不再打出裸的 `sku_match`
        # 行（那会被读成「SKU 匹配率」而与复放口径 simulated_match_rate 混读）。同时给出分子/
        # 分母，便于一眼看出与 simulated_match_rate 的差异来自分子口径而非分母。
        b_sku = base.get("sku_match") or {}
        n_sku = cur.get("sku_match") or {}
        if b_sku.get("rate_pct") is not None and n_sku.get("rate_pct") is not None:
            print("%-28s %s%% -> %s%% (%+.2f) （分子 %s/%s；只数落库标记，非匹配逻辑成功率，"
                  "后者见 backfill_vendor_sku_alias.py --replay 的 %s，不可混读）" % (
                      SKU_MATCH_FLAG_KEY, b_sku["rate_pct"], n_sku["rate_pct"],
                      n_sku["rate_pct"] - b_sku["rate_pct"],
                      n_sku.get("matched"), n_sku.get("items"), SIMULATED_MATCH_RATE_KEY))
        # 有效命中率单独一行并标注不可混读：它与上一行的 memory_hit 分母不同，
        # 紧跟其后打印容易让人顺手对比（本仓纪律：两个口径必须显式声明不可混读）。
        # 基线侧没有 disabled_by_switch 字段（旧基线 JSON）也不会误读：缺省 False。
        eff = cur.get("memory_hit_effective") or {}
        b_eff = base.get("memory_hit_effective") or {}
        print("%-28s %s -> %s （%s/%s，与上行分母不同、不可混读）" % (
            "memory_hit_effective", _eff_rate_text(b_eff), _eff_rate_text(eff),
            eff.get("injected"), eff.get("eligible_denominator")))
        d0 = base.get("edit_rate_ab", {}).get("delta_pp")
        d1 = cur.get("edit_rate_ab", {}).get("delta_pp")
        print("%-28s %s -> %s （负值=有记忆组修改率更低，是目标方向）" % ("edit_rate delta_pp", d0, d1))
    if a.json or not (a.save or a.compare):
        print(json.dumps(cur, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
