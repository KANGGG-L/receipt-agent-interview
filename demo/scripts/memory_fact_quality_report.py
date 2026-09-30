# -*- coding: utf-8 -*-
"""记忆事实质量巡检：把「低信息量事实」从人工肉眼观察变成可复算报告（只读）。

why（存在的理由）
----------------
我在人工逐条看 live 已生效记忆时，发现两类事实过了种子工具的退化过滤、却几乎不携带
信息，但**此前只能靠肉眼发现**（下一次换个人、或明天的我，只能重新肉眼一遍）：

- **边缘 alias**：`大天鵝鴨五花肉 (G80g*24)` → `大天鵝鴨五花肉 (G80g*24) x2`（只差尾部
  ` x2`）、`好味豬油也 (420g X 12)` → `老泥：好味豬油也 (420g X 12)`（只多一个前缀）；
- **伪区间 price_range**：`急凍牛肋條 = 40.00-40.00`（名为区间，实为单点）。

它们会占用注入预算（`rag.retrieve_context` 每轮给 prompt 的篇幅有限），也让「记忆质量」
无法度量。本脚本把这三类判据固化成客观可算的巡检：逐条给**判定与依据**（不只给结论），
再给分类汇总；退出码默认 0（它只是报告），`--fail-on-low` 让它存在低信息量事实时非 0，
便于将来接入 CI。

三类判据（`why 算低信息量` 与 `边界` 逐条写在各自的 `_CRITERIA_*` 常量里，报告末尾原样
复述 —— 判据自述与实现同源，避免两处文案分叉）：
1. `alias` 边缘映射：归一后「完全相同」或「仅首/尾续接且残差 <= 阈值」；
2. `price_range` 伪区间：`min == max`，或「区间宽度/中位价 < 阈值」；
3. `unit` 无差异：该供应商的单位与 SKU 自身 `base_unit` 相同。

归一实现（复用红线）
--------------------
alias 判据的「归一后差异度」**必须复用项目唯一的那份** `receipt_utils._normalize_sku_name`
（种子工具的退化过滤、别名表写入侧、匹配侧 canonical 通道用的都是它）。这里若自建第二套，
必然分叉成「巡检判边缘、匹配侧判有用」—— 本仓①类缺陷（同一口径多份实现只改一份）。
故本脚本不自写归一：见 `_normalize_sku_name_impl()`，含「import 期强制把 DB_PATH / RAG_DIR
指向临时路径」的保护（该实现所在的模块顶层 `from app import db`，而 `app.db` 在 import 期
执行 `_make_engine()` 迁移块、会对 `DB_PATH` 指向的库做 ALTER）。

只读红线（本脚本自身）
----------------------
- 数据面一律 `sqlite3` **只读** URI（`?mode=ro`，代码里可见），本脚本**不写任何库、含副本**：
  库不存在时返回空报告，不创建文件；连归一实现的 import 副作用也只会落在一次性临时库上。
- **不 import `app.db` / `app.services.rag`**；只 import 纯逻辑模块
  `app.services.memory_facts`（顶层仅依赖标准库）。

用法：
  # 报告默认读 live 只读；生产 cwd 或任意 cwd 都可（路径由脚本自身解析）。
  python demo/scripts/memory_fact_quality_report.py
  python demo/scripts/memory_fact_quality_report.py --include-archived
  python demo/scripts/memory_fact_quality_report.py --json
  python demo/scripts/memory_fact_quality_report.py --fail-on-low   # CI 用：有 LOW 则退出码 1
  python demo/scripts/memory_fact_quality_report.py --alias-tail-max 8 --price-width-ratio 0.02
退出码：默认 0（仅报告）；带 `--fail-on-low` 时，存在低信息量事实 → 1。
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile

_DEMO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

# 三类（加 layout 共四类）事实的 kind 白名单复用 memory_facts 的**唯一实现**。
# why 可以安全 import：memory_facts 顶层只依赖标准库（re / dataclasses / datetime / typing），
# import 它不会触发 app.db 的迁移块。
from app.services.memory_facts import (  # noqa: E402
    KIND_ALIAS,
    KIND_LAYOUT,
    KIND_PRICE_RANGE,
    KIND_UNIT,
)

LOW = "LOW"
OK = "OK"

# 汇总输出顺序（固定，保证报告逐行可比）
_KIND_ORDER = (KIND_ALIAS, KIND_UNIT, KIND_LAYOUT, KIND_PRICE_RANGE)
_KIND_TITLE = {
    KIND_ALIAS: "alias（别名映射）",
    KIND_UNIT: "unit（供应商单位习惯）",
    KIND_LAYOUT: "layout（版式先验）",
    KIND_PRICE_RANGE: "price_range（历史价格区间）",
}

# -------------------------------------------------------------
# 判据参数（默认值 + 依据）
# -------------------------------------------------------------
# alias：归一后残差（首/尾续接的那一小段）的字符数上限。
# why 默认 16：live 实测的两条边缘 alias 归一后残差分别为 ` (G80g*24) x2`（14 字符）与
# `老泥：`（3 字符），16 能覆盖实测形态；而判 LOW 还需满足「归一后一方是另一方首/尾续接」
# 这一**结构性**条件，本阈值只作上限保护（防病态长尾被误判为边缘）。
ALIAS_TAIL_MAX_DEFAULT = 16
# price_range：区间宽度 / 中位价 的比值阈值，低于它算「近单点」。
# why 默认 0.05：供应商报价随批次的正常波动远大于 5%，宽度不到中位价 5% 的历史「区间」
# 在报价先验上等价于一个单点估计；0 是它的退化端点（min==max）。
PRICE_WIDTH_RATIO_DEFAULT = 0.05

# -------------------------------------------------------------
# 判据一：alias 边缘映射
# -------------------------------------------------------------
_CRITERIA_ALIAS = (
    "为何算低信息量：alias 事实的职责是搭「该供应商的写法 → 标准品名」这座桥。若两串经"
    "**项目唯一归一实现** receipt_utils._normalize_sku_name 归一后 (a) 完全相同，或 (b) 一方"
    "只是另一方在**首/尾**续接的一小段，那么这座桥承载的信息近乎为零 —— 登记它得到的匹配"
    "结果与全局精确/核心词通道相同，只会占用注入预算。"
    "边界怎么定：(a) 判 LOW（判据码 `alias:归一后相同`）；(b) 还要求残差长度 <= 阈值"
    "（ALIAS_TAIL_MAX，默认 %d，可配），残差**恰好等于阈值判 LOW、超过则 OK**（边界用例断言"
    "上下两条分类相反）。why 用「首/尾续接」结构而不是编辑距离或相似度比：繁简异体"
    "（如 有機菜心 → 有机菜心）是**真实且有用**的写法桥，它改的是词干中部的字、不满足续接"
    "结构；而包装规格尾缀（如 ` (G80g*24) x2`）与短前缀（如 `老泥：`）恰好只在**一端** —— "
    "结构判据能把这两类分开，相似度阈值不能（实测边缘项的相似度反而低于繁简项）。"
    % ALIAS_TAIL_MAX_DEFAULT
)

# -------------------------------------------------------------
# 判据二：price_range 伪区间
# -------------------------------------------------------------
_CRITERIA_PRICE_RANGE = (
    "为何算低信息量：price_range 事实的职责是给「这个供应商这个货大概多少钱」一个区间先验。"
    "若下界 == 上界（min==max），它名为区间、实为一个点，读者会以为拿到了区间信息、实际等价于"
    "单值 —— 这种「看起来更像信息」的错觉比缺失更糟；若区间宽度相对中位价极小（< 阈值），"
    "结论同样近似单点。"
    "边界怎么定：(a) 解析出下界 == 上界 → LOW（判据码 `price_range:min==max`）；(b) 否则算"
    "宽度/中位价，**严格小于阈值才 LOW**（等于阈值判 OK，边界用例断言上下两条分类相反）；"
    "(c) 上界小于下界（区间颠倒）也判 LOW。阈值默认 %.4f（可配）。"
    "另报告样本数（若有）：从 notes 文本里解析「入库 N 次」并注明来源是文本解析 —— 事实行本身"
    "没有样本数列，读不到就不编造。"
    % PRICE_WIDTH_RATIO_DEFAULT
)

# -------------------------------------------------------------
# 判据三：unit 无差异
# -------------------------------------------------------------
_CRITERIA_UNIT = (
    "为何算低信息量：unit 事实的职责是记录「该供应商对某货惯用这个单位」这一**特有习惯**"
    "（用于复核侧单位先验）。若它与该 SKU 自身的 base_unit 相同，它就只是复述了 SKU 档案里"
    "本来就有的信息，不构成任何供应商特有习惯，登记它不改变任何判断。"
    "边界怎么定：解析出该事实对应的 SKU（fact_key 形如 `unit:<标准品名>` 或回落形态"
    "`unit:#<sku_id>`）→ 取该 SKU 的 base_unit；**单位与 base_unit 相同才判 LOW**，不同判 OK。"
    "解析不出 SKU（档案缺失/同名多行且 base_unit 不一致）时**不判 LOW**（宁可不判，也不凭空"
    "断言）—— 该情形在明细的依据列显式标注「未解析出 base_unit」。"
)

# -------------------------------------------------------------
# 归一实现（复用 + import 期隔离保护）
# -------------------------------------------------------------
_NORMALIZE_SKU_NAME = None


def _normalize_sku_name_impl():
    """复用项目**唯一**的 SKU 名归一实现 `receipt_utils._normalize_sku_name`。

    why 不自己写一套：种子工具的退化过滤、别名表写入侧（inventory）、匹配侧
    `receipt_utils._match_sku` 的 canonical 通道用的都是这一份；这里再抄一份，必然分叉成
    「巡检判边缘、匹配侧判有用」（本仓①类缺陷：同一口径多份实现只改一份）。

    why import 期间要临时把 `DB_PATH` / `RAG_DIR` 指向临时路径：该实现所在模块顶层
    `from app import db`，而 `app.db` 在 import 期执行 `_make_engine()` 的迁移块、会对
    `DB_PATH` 指向的库做 ALTER。本脚本是「只读报告」，若不做这层保护，单是 import 归一实现
    就会写 live（本项目已发生三次 live 加法迁移）。故这里在 import 前后临时改写这两个环境
    变量并还原 —— **归一逻辑仍是同一份**，只有 import 副作用被引到一次性临时库上。
    """
    global _NORMALIZE_SKU_NAME
    if _NORMALIZE_SKU_NAME is not None:
        return _NORMALIZE_SKU_NAME
    saved = {k: os.environ.get(k) for k in ("DB_PATH", "RAG_DIR")}
    tmp = tempfile.mkdtemp(prefix="mem_fact_quality_import_")
    try:
        os.environ["DB_PATH"] = os.path.join(tmp, "import_guard.db")
        os.environ["RAG_DIR"] = os.path.join(tmp, "rag")
        from app.services.receipt_utils import _normalize_sku_name
        _NORMALIZE_SKU_NAME = _normalize_sku_name
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)
    return _NORMALIZE_SKU_NAME


# -------------------------------------------------------------
# 只读连接（mode=ro，代码里可见；不写任何库、含副本）
# -------------------------------------------------------------
def _readonly_conn(path):
    """只读打开目标库；文件不存在返回 None（不创建文件）。"""
    if not os.path.exists(path):
        return None
    return sqlite3.connect("file:%s?mode=ro" % path, uri=True)


# -------------------------------------------------------------
# 分类器
# -------------------------------------------------------------
def _classify_alias(key, value, tail_max):
    """alias 事实 → (status, criterion_hit)。raw 取 fact_key 的 `alias:` 之后，canonical 取 value。"""
    raw = key.split(":", 1)[1] if (isinstance(key, str) and ":" in key) else ""
    canon = str(value or "")
    norm = _normalize_sku_name_impl()
    raw_n, canon_n = norm(raw), norm(canon)
    if not raw_n or not canon_n:
        return OK, "alias:归一后出现空串（写法或标准名为空），无法判定，不判 LOW"
    if raw_n == canon_n:
        return LOW, "alias:归一后相同（无映射信息）"
    # 只认「一方是另一方首/尾续接」——词干中部的差异（繁简异体/换词）不属边缘。
    if canon_n.startswith(raw_n):
        residual = canon_n[len(raw_n):]
    elif canon_n.endswith(raw_n):
        residual = canon_n[: len(canon_n) - len(raw_n)]
    elif raw_n.startswith(canon_n):
        residual = raw_n[len(canon_n):]
    elif raw_n.endswith(canon_n):
        residual = raw_n[: len(raw_n) - len(canon_n)]
    else:
        return OK, "alias:归一后差异不在首/尾（词干中部差异，保留）"
    n = len(residual)
    if n <= tail_max:
        return LOW, "alias:归一后仅首/尾续接，残差 %d<=%d" % (n, tail_max)
    return OK, "alias:归一后仅首/尾续接但残差 %d>%d" % (n, tail_max)


_PRICE_VALUE_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[-~—]\s*(-?\d+(?:\.\d+)?)\s*$")
_PRICE_SAMPLE_RE = re.compile(r"入库\s*(\d+)\s*次")


def _classify_price_range(value, width_ratio_max, notes=None):
    """price_range 事实 → (status, criterion_hit, sample_count)。样本数从 notes 文本解析。"""
    samples = None
    m_s = _PRICE_SAMPLE_RE.search(str(notes or ""))
    if m_s:
        samples = int(m_s.group(1))
    m = _PRICE_VALUE_RE.match(str(value or ""))
    if not m:
        return OK, "price_range:无法解析为「下界-上界」，不判 LOW", samples
    lo, hi = float(m.group(1)), float(m.group(2))
    if hi < lo:
        return LOW, "price_range:上界 %s 小于下界 %s（区间颠倒）" % (hi, lo), samples
    if lo == hi:
        return LOW, "price_range:min==max=%s（名为区间实为单点）" % lo, samples
    median = (lo + hi) / 2.0
    ratio = (hi - lo) / median if median else 1.0
    if ratio < width_ratio_max:
        return LOW, ("price_range:区间宽度/中位价 %.4f < %.4f（近单点）"
                     % (ratio, width_ratio_max)), samples
    return OK, "price_range:区间宽度/中位价 %.4f（正常）" % ratio, samples


def _classify_unit(value, base_unit, resolved):
    """unit 事实 → (status, criterion_hit)。`resolved` 为 False 时不判 LOW。"""
    unit = str(value or "").strip()
    if not resolved:
        return OK, "unit:未解析出该 SKU 的 base_unit，不判 LOW"
    bu = str(base_unit or "").strip()
    if bu and unit == bu:
        return LOW, "unit:与 SKU 基准单位「%s」相同（不构成供应商特有习惯）" % bu
    return OK, "unit:与 SKU 基准单位「%s」不同（构成供应商特有习惯）" % bu


# -------------------------------------------------------------
# SKU 基准单位索引（用于 unit 判据）
# -------------------------------------------------------------
def _load_sku_units(conn):
    """返回 (base_unit_by_id, base_unit_by_name_key)。

    `by_name` 的键是 (tenant_id, sku.name)；同名多行时若 base_unit 不一致，值存 None
    （歧义 → 调用方按「未解析」处理，不猜）。
    """
    by_id, grouped = {}, {}
    try:
        rows = conn.execute(
            "select id, name, ifnull(base_unit,''), ifnull(tenant_id,'default') from skus"
        ).fetchall()
    except sqlite3.Error:
        return by_id, {}
    for sid, name, base_unit, tid in rows:
        by_id[int(sid)] = str(base_unit or "")
        grouped.setdefault((str(tid), str(name or "")), set()).add(str(base_unit or ""))
    by_name = {}
    for k, vals in grouped.items():
        by_name[k] = next(iter(vals)) if len(vals) == 1 else None
    return by_id, by_name


def _resolve_unit_base(key, tenant_id, by_id, by_name):
    """unit 事实的 fact_key → (base_unit, resolved)。解析不出返回 (None, False)。"""
    if not isinstance(key, str) or not key.startswith("unit:"):
        return None, False
    rest = key[len("unit:"):]
    if rest.startswith("#"):                       # 回落形态 unit:#<sku_id>
        try:
            sid = int(rest[1:])
        except ValueError:
            return None, False
        if sid in by_id:
            return by_id[sid], True
        return None, False
    val = by_name.get((str(tenant_id), rest))       # 形态 unit:<标准品名>
    if val is None:
        return None, False
    return val, True


# -------------------------------------------------------------
# 主流程（纯读）
# -------------------------------------------------------------
def collect(db_path, include_archived=False, alias_tail_max=ALIAS_TAIL_MAX_DEFAULT,
            price_width_ratio_max=PRICE_WIDTH_RATIO_DEFAULT):
    """只读扫描 vendor_memory，逐条判定 + 分类汇总。返回报告字典。"""
    scope = "全部状态" if include_archived else "status='active'"
    report = {
        "db_path": db_path,
        "scope": scope,
        "thresholds": {"alias_tail_max": alias_tail_max,
                       "price_width_ratio_max": price_width_ratio_max},
        "criteria": {
            KIND_ALIAS: _CRITERIA_ALIAS,
            KIND_PRICE_RANGE: _CRITERIA_PRICE_RANGE,
            KIND_UNIT: _CRITERIA_UNIT,
            KIND_LAYOUT: ("layout：本批三类判据不含 layout（版式先验的「低信息量」尚无客观判据），"
                          "故 layout 事实一律计 OK，不判 LOW。"),
        },
        "details": [],
        "summary": {},
        "low_total": 0,
        "row_total": 0,
    }
    conn = _readonly_conn(db_path)
    if conn is None:
        report["error"] = "库文件不存在（不创建）：%s" % db_path
        return report
    try:
        try:
            conn.execute("select 1 from vendor_memory limit 1")
        except sqlite3.Error as e:
            report["error"] = "vendor_memory 表不可读：%s" % e
            return report
        where = "" if include_archived else "where status = 'active'"
        rows = conn.execute(
            "select memory_id, vendor, ifnull(tenant_id,'default'), ifnull(status,''), "
            "       fact_kind, fact_key, fact_value, notes "
            "from vendor_memory %s order by vendor, fact_kind, memory_id" % where
        ).fetchall()
        by_id, by_name = _load_sku_units(conn)
    finally:
        conn.close()

    counts = {k: {"total": 0, "low": 0} for k in _KIND_ORDER}
    for mid, vendor, tid, status, kind, key, value, notes in rows:
        kind = str(kind or "")
        samples = None
        if kind == KIND_ALIAS:
            status_c, hit = _classify_alias(key, value, alias_tail_max)
        elif kind == KIND_PRICE_RANGE:
            status_c, hit, samples = _classify_price_range(value, price_width_ratio_max, notes)
        elif kind == KIND_UNIT:
            base_unit, resolved = _resolve_unit_base(key, tid, by_id, by_name)
            status_c, hit = _classify_unit(value, base_unit, resolved)
        elif kind == KIND_LAYOUT:
            status_c, hit = OK, "layout:本批无判据（三类判据不含 layout），不判 LOW"
        else:
            status_c, hit = OK, ("非结构化事实行（fact_kind=%r）：三类判据都不适用，不判 LOW"
                                 % kind)
        bucket = counts.setdefault(kind, {"total": 0, "low": 0})
        bucket["total"] += 1
        if status_c == LOW:
            bucket["low"] += 1
            report["low_total"] += 1
        report["row_total"] += 1
        detail = {
            "memory_id": mid, "vendor": vendor, "tenant_id": tid, "status": status,
            "kind": kind, "key": key, "value": value,
            "verdict": status_c, "criterion_hit": hit,
        }
        if samples is not None:
            detail["samples"] = samples
            detail["samples_source"] = "notes 文本解析（入库 N 次）"
        report["details"].append(detail)

    for k in _KIND_ORDER:
        b = counts.get(k, {"total": 0, "low": 0})
        b = dict(b)
        b["ratio_pct"] = round(100.0 * b["low"] / b["total"], 2) if b["total"] else None
        report["summary"][k] = b
    unknown = [(k, v) for k, v in counts.items() if k not in _KIND_ORDER]
    for k, v in sorted(unknown):
        b = dict(v)
        b["ratio_pct"] = round(100.0 * b["low"] / b["total"], 2) if b["total"] else None
        report["summary"][k] = b
    report["summary"]["__all__"] = {
        "total": report["row_total"], "low": report["low_total"],
        "ratio_pct": (round(100.0 * report["low_total"] / report["row_total"], 2)
                      if report["row_total"] else None),
    }
    return report


# -------------------------------------------------------------
# 输出
# -------------------------------------------------------------
def _cell(text, width):
    """定宽单元格：过长截断并标记（避免宽表把终端撑爆）。"""
    s = "" if text is None else str(text)
    if len(s) > width:
        return s[: width - 1] + "…"
    return s.ljust(width)


def _fmt_details(details):
    w_mid, w_vendor, w_kind, w_key, w_val, w_verdict, w_smp = 12, 20, 10, 34, 34, 7, 7
    lines = ["%s  %s  %s  %s  %s  %s  %s  %s"
             % ("memory_id".ljust(w_mid), "vendor".ljust(w_vendor), "kind".ljust(w_kind),
                "key".ljust(w_key), "value".ljust(w_val), "判定".ljust(w_verdict),
                "样本数".ljust(w_smp), "命中的判据"),
             "%s  %s  %s  %s  %s  %s  %s  %s"
             % ("-" * w_mid, "-" * w_vendor, "-" * w_kind, "-" * w_key, "-" * w_val,
                "-" * w_verdict, "-" * w_smp, "-" * 40)]
    for d in details:
        smp = "" if d.get("samples") is None else str(d["samples"])
        lines.append("%s  %s  %s  %s  %s  %s  %s  %s"
                     % (_cell(d["memory_id"], w_mid), _cell(d["vendor"], w_vendor),
                        _cell(d["kind"] or "(无)", w_kind), _cell(d["key"], w_key),
                        _cell(d["value"], w_val), d["verdict"].ljust(w_verdict),
                        _cell(smp, w_smp), d["criterion_hit"]))
    return "\n".join(lines)


def _fmt_summary(summary):
    lines = ["%s  %s  %s  %s" % ("kind".ljust(28), "LOW".ljust(6), "总行数".ljust(8),
                                 "LOW 占比"),
             "%s  %s  %s  %s" % ("-" * 28, "-" * 6, "-" * 8, "-" * 10)]
    for k in _KIND_ORDER:
        b = summary.get(k)
        if not b:
            continue
        ratio = "无数据" if b["ratio_pct"] is None else "%s%%" % b["ratio_pct"]
        lines.append("%s  %-6d  %-8d  %s"
                     % (_cell(_KIND_TITLE.get(k, k), 28), b["low"], b["total"], ratio))
    # 非结构化行（fact_kind 为空，如 --include-archived 带出的遗留自由文本行）也列出来，
    # 否则合计行与各分类之和对不上（那会让读者以为漏算）。
    for k in sorted(k for k in summary if k not in _KIND_ORDER and k != "__all__"):
        b = summary[k]
        ratio = "无数据" if b["ratio_pct"] is None else "%s%%" % b["ratio_pct"]
        label = "%s（非结构化行）" % (k or "无 kind")
        lines.append("%s  %-6d  %-8d  %s"
                     % (_cell(label, 28), b["low"], b["total"], ratio))
    allb = summary.get("__all__")
    if allb:
        ratio = "无数据" if allb["ratio_pct"] is None else "%s%%" % allb["ratio_pct"]
        lines.append("%s  %-6d  %-8d  %s"
                     % (_cell("合计", 28), allb["low"], allb["total"], ratio))
    return "\n".join(lines)


def _print_report(report):
    print("记忆事实质量报告（只读扫描 vendor_memory）")
    print("  db_path : %s" % report["db_path"])
    print("  扫描范围: %s" % report["scope"])
    print("  判据参数: alias 首/尾残差上限=%d；price_range 宽度比阈值=%s"
          % (report["thresholds"]["alias_tail_max"],
             report["thresholds"]["price_width_ratio_max"]))
    if report.get("error"):
        print("  错误    : %s" % report["error"])
        return
    print("")
    print("【逐条明细】共 %d 条" % report["row_total"])
    if any(d.get("samples") is not None for d in report["details"]):
        print("  注：「样本数」列（若有）从 notes 文本解析「入库 N 次」，事实行本身没有该列；"
              "读不到即为空，不编造。")
    print(_fmt_details(report["details"]))
    print("")
    print("【分类汇总】")
    print(_fmt_summary(report["summary"]))
    print("")
    print("【判据说明】（每类为何算低信息量、边界怎么定）")
    for k in _KIND_ORDER:
        print("  [%s] %s" % (k, report["criteria"][k]))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="记忆事实质量巡检：只读扫描 vendor_memory，输出低信息量事实明细与汇总")
    ap.add_argument("--db-path", default=None,
                    help="记忆库路径（缺省读 DB_PATH，再缺省 live；一律只读打开，不写任何库）")
    ap.add_argument("--include-archived", action="store_true",
                    help="包含 archived 行（默认只扫 status='active'）")
    ap.add_argument("--alias-tail-max", type=int, default=ALIAS_TAIL_MAX_DEFAULT,
                    help="alias 判据：归一后首/尾残差字符数上限（默认 %d）"
                         % ALIAS_TAIL_MAX_DEFAULT)
    ap.add_argument("--price-width-ratio", type=float, default=PRICE_WIDTH_RATIO_DEFAULT,
                    help="price_range 判据：宽度/中位价 的比值阈值（默认 %s）"
                         % PRICE_WIDTH_RATIO_DEFAULT)
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    ap.add_argument("--fail-on-low", action="store_true",
                    help="存在低信息量事实时退出码 1（便于接入 CI）")
    args = ap.parse_args(argv)

    db_path = os.path.abspath(
        args.db_path or os.environ.get("DB_PATH")
        or os.path.join(_DEMO_DIR, "receipt_demo.db"))
    report = collect(db_path, include_archived=args.include_archived,
                     alias_tail_max=args.alias_tail_max,
                     price_width_ratio_max=args.price_width_ratio)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(report)
    if args.fail_on_low and report["low_total"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
