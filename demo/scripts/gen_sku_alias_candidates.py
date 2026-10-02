#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工具②：SKU 别名候选生成器（SPEC_PHASE1 §4）—— 只产候选清单，**零自动登记**。

产品红线（§4.4 三道闸）：
1. 代码闸：本文件**不含** ``upsert_vendor_sku_alias`` / ``create_sku`` / ``INSERT INTO`` /
   ``UPDATE `` / ``session.add`` 等任何写语义；候选唯一去向是 CSV 文件。
   登记只能走既有 approve 路径（入账钩子调用 ``db.upsert_vendor_sku_alias``）；
   CSV→别名表的导入工具不在本阶段交付（SPEC_PHASE1 §7 明确不做）。
2. 测试闸：``demo/tests/test_sku_alias_candidates.py::test_no_auto_registration`` 对本文件
   源码 grep 断言上述关键词零命中，并在副本上跑完生成器后断言 ``vendor_sku_alias`` /
   ``skus`` / ``receipt_items`` 逐行不变。
3. 流程闸：即本 docstring——人审 CSV 是唯一消费方式，不存在"已登记"状态列。

live 防护（§4.1，比既有脚本更严）：``DB_PATH`` 未设置、或解析后指向任一
``demo/receipt_demo.db``（主树/worktree 都是 live 语义）、或落在本脚本所在仓库树内
→ ``SystemExit`` 并打印防护原因。本工具只允许对 /tmp 副本运行（import app.db 会在
导入期对 DB_PATH 执行迁移块，这只允许发生在副本上）。

流水线（全部内存内，对副本库零写）：
  全量明细 → residue_rules 剔 93 行残渣 + is_unmatchable 剔 7 行 '0T'（'0T' 永不可匹配、
  无候选可修，不产候选）→ live 行 → 按 ``receipt_utils._normalize_sku_name`` 归一分组
  （归一与匹配同源，候选才能回灌别名通道，Q6）→ 每写法聚合（行数/原始写法频次/
  供应商写法频次/receipt 样例/回放匹配状态）→ 置信分级 → CSV + stdout 摘要。

置信分级（确定性）：
  high   = 命中 OCR 家族表 **或** 未锚定（回放 miss）且行数 ≥2；
  medium = 未锚定一次性写法但供应商锚定存在；
  low    = 其余（含回放已锚定写法——只作对照，不进人审首屏）。

与规格校准值的偏差声明（SPEC_PHASE1 §8 纪律：实测与规格记录不符时如实呈报，禁止为凑数
放宽/收紧判别式；数值以本工具在 live 副本 md5=7cc3140907d71becb714152a30e6bc51 上的实跑
为准，stdout 摘要每次运行都会对照规格参考值打印本表）：
  - 回放命中/未锚定：实测 32 / 172 种 vs 规格 Q9·C-R1 记录 29 / 175 种。
    原因：29 恰是「receipt_items.matched=1 落库标记」口径下的锚定写法数；§4.1 步骤 3 的
    规范定义是**真跑 _match_sku 全局口径**（与 backfill --replay 基线同源），两者分子口径
    不同——`特級菜心`/`菜心`/`菜心苗`/`維他檸檬茶500毫升` 回放可命中而落库标记为 0、
    `商品A` 落库标记为 1 而回放 miss。实现取规范定义。
  - 高置信段：实测 43 种/226 行 vs 规格 Q9·C-R1 记录 46 种/272 行；未锚定重复实测
    22 种/186 行 vs 规格 26 种/251 行。原因：上条的口径差传导（真跑回放比落库标记
    多锚定 4 种重复写法、少 3 种 miss），判别式未改。
  - 維他檸檬茶家族：实测 7 种/28 行 vs 规格 Q9 记录 6 种/26 行。原因：§4.2 正则原文
    ``^維他檸檬茶|^维他柠檬茶`` 本就覆盖简体变体「维他柠檬茶500毫升瓶」（1 种/1 行），
    规格家族表自身不自洽（其四族合计仍写 27 种/102 行）。判别式按 §4.2 原文逐字实现。
  - 其余校准值全部复现一致：live 行 545、归一写法 204、残渣 93、'0T' 7、家族合计
    27 种/102 行（江團魚 11/29、生蠔 6/26、五香肉丁 3/19）。
"""

import argparse
import csv
import os
import re
import sqlite3
import sys
from collections import Counter, OrderedDict

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEMO_DIR = os.path.join(REPO, "demo")

# -------------------------------------------------------------
# OCR 家族表（§4.2，写死常量；副本实测 live 545 行口径下逐族校准值见各注释）
# -------------------------------------------------------------
OCR_FAMILY_PATTERNS = [
    # 家族: 江X魚（同一商品 江團魚 的 OCR 变体；ERRORS §1.2，5 个供应商写法均归此）
    # 实测 11 种写法/29 行
    ("江團魚", re.compile(r"^江.{0,2}(魚|鰭|魷)$")),
    # 家族: 半殼生蠔（半殼/半型/半製/半殺/半泓/半海）
    # 实测 6 种写法/26 行
    ("生蠔", re.compile(r"^(半.生蠔|生蠔)$")),
    # 家族: 五香肉丁（天大龍牌/大天龍牌 前缀丢错）
    # 实测 3 种写法/19 行
    ("五香肉丁", re.compile(r"^(天大龍牌|大天龍牌)?五香肉丁$|^(天大龍牌|大天龍牌)$")),
    # 家族: 維他檸檬茶（裝/瓶/膠 规格后缀变体；ERRORS §1.2 c) 类）
    # 实测 7 种写法/28 行（含 500毫升裝/罐/膠/特別版 及简体写法；§8 偏差声明：
    # 比规格 Q9 记录的 6/26 多 1 种简体「维他柠檬茶」变体，判别式按 §4.2 原文）
    ("維他檸檬茶", re.compile(r"^維他檸檬茶|^维他柠檬茶")),
]

CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2}

# 规格 Q9/C-R1 的校准参考值（仅作 stdout 偏差对照的基准，不参与任何计算/判别式；
# 与实测不符的项按 §8 纪律在摘要中显式呈报，见模块 docstring 的偏差声明）
SPEC_REFERENCE = {
    "anchored_writings": 29,   # 规格 Q9：回放已锚定 29 种（实为落库标记口径，见 docstring）
    "miss_writings": 175,      # 规格 Q9/C-R1：replay miss 175 种
    "unanchored_dup_writings": 26,  # 规格 Q9：未锚定重复 26 种/251 行
    "unanchored_dup_rows": 251,
    "high_writings": 46,       # 规格 Q9/C-R1：高置信并集 46 种/272 行
    "high_rows": 272,
    "family_writings": 27,     # 规格 Q9：四家族合计 27 种/102 行（该项实测复现一致）
    "family_rows": 102,
}

CSV_HEADERS = ["canonical_writing", "raw_writings", "row_count", "supplier_names",
               "receipt_id_samples", "replay_match", "candidate_sku", "confidence",
               "evidence", "suggested_action"]

_SUGGESTED_ACTION = "人工审核：确认指向的既有 SKU 或新建"

# ------------------------------------------------------------------
# live 防护（§4.1 硬门）
# ------------------------------------------------------------------
def guard_db_path():
    """校验 DB_PATH 指向 /tmp 副本；违规 SystemExit。返回解析后的绝对路径。"""
    raw = os.environ.get("DB_PATH")
    if not raw or not str(raw).strip():
        raise SystemExit(
            "[live 防护] DB_PATH 未设置。本工具只允许对 /tmp 副本运行"
            "（import app.db 会在导入期对 DB_PATH 执行迁移块）："
            "先 `cp demo/receipt_demo.db /tmp/<你的副本>.db` 再 "
            "`export DB_PATH=/tmp/<你的副本>.db`。")
    resolved = os.path.realpath(os.path.expanduser(str(raw).strip()))
    if os.path.basename(resolved) == "receipt_demo.db":
        raise SystemExit(
            "[live 防护] DB_PATH 指向 %s —— receipt_demo.db 是 live 库"
            "（主树是 GT 确认现场，绝不读写）。请先 cp 到 /tmp 副本再运行。" % resolved)
    repo_root = os.path.realpath(REPO)
    if resolved == repo_root or resolved.startswith(repo_root + os.sep):
        raise SystemExit(
            "[live 防护] DB_PATH 落在本仓仓库树内（%s）：候选生成只允许对 /tmp 副本运行。"
            % resolved)
    return resolved


def load_rows(db_path):
    """只读全量明细行（receipt_items ⨝ receipts），按 i.id 稳定排序（确定性）。"""
    conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    try:
        return conn.execute(
            "SELECT i.name, i.unit_price, i.matched, i.receipt_id,"
            " r.supplier_name, r.supplier_id, r.tenant_id, i.id"
            " FROM receipt_items i JOIN receipts r ON r.id = i.receipt_id"
            " ORDER BY i.id").fetchall()
    finally:
        conn.close()


def build_candidates(rows, receipt_utils):
    """全量行 → 候选列表（内存内，零 DB 写）。

    receipt_utils：``(match_sku, normalize_sku_name)`` 元组——由调用方在 live 防护后
    import 传入（本模块顶部不 import app.*，保持可被测试安全源码审查）。
    """
    _match_sku, _normalize_sku_name = receipt_utils
    import residue_rules

    # 1) 剔残渣 93 行 + '0T' 7 行
    live_rows = []
    excluded_residue = excluded_unmatchable = 0
    for name, unit_price, matched, rid, supplier_name, supplier_id, tenant_id, iid in rows:
        is_res, _rule = residue_rules.classify(name, supplier_name)
        if is_res:
            excluded_residue += 1
            continue
        if residue_rules.is_unmatchable(name):
            excluded_unmatchable += 1
            continue
        live_rows.append((name, unit_price, matched, int(rid), supplier_name or "", tenant_id or "default"))

    # 2) 归一分组（归一键与匹配通道同源：_normalize_sku_name）
    groups = OrderedDict()
    for name, unit_price, matched, rid, supplier_name, tenant_id in live_rows:
        key = _normalize_sku_name(name)
        g = groups.setdefault(key, {
            "row_count": 0,
            "raws": Counter(),       # 原始写法 → 频次
            "sups": Counter(),       # 原始供应商写法 → 频次
            "rids": [],              # 收据 id（样例取前 5）
            "rep": None,             # 代表行（首个出现，i.id 稳定序）
        })
        g["row_count"] += 1
        g["raws"][name] += 1
        g["sups"][supplier_name] += 1
        g["rids"].append(rid)
        if g["rep"] is None:
            g["rep"] = (name, tenant_id)

    # 3) 家族成员统计（归一写法级）
    family_members = {fam: [] for fam, _pat in OCR_FAMILY_PATTERNS}
    key_family = {}
    for key, g in groups.items():
        for fam, pat in OCR_FAMILY_PATTERNS:
            if pat.match(key):
                family_members[fam].append(key)
                key_family[key] = fam
                break

    # 4) 逐写法回放匹配（代表行，全局口径 supplier_id=None，与 backfill --replay 基线同源）
    candidates = []
    for key, g in groups.items():
        rep_name, rep_tenant = g["rep"]
        item = {"name": rep_name, "unit": "", "unit_price": 0, "sku_id": None,
                "matched": 0, "price_anomaly": 0, "price_anomaly_direction": "",
                "price_diff_percent": 0.0}
        _match_sku(item, tenant_id=rep_tenant, supplier_id=None)
        anchored = bool(item["matched"])
        if anchored:
            replay_match = "hit"
            candidate_sku = "SKU#%s(%s)" % (item["sku_id"], _sku_name(item["sku_id"]))
        else:
            replay_match = "miss"
            candidate_sku = "（主数据缺失：需人工指定既有 SKU 或新建）"

        fam = key_family.get(key)
        has_supplier = any(s for s in g["sups"])
        if fam:
            confidence = "high"
            evidence = "OCR 家族:%s（%d 种写法/%d 行）" % (
                fam, len(family_members[fam]),
                sum(groups[k]["row_count"] for k in family_members[fam]))
        elif not anchored and g["row_count"] >= 2:
            confidence = "high"
            evidence = "未锚定重复写法（%d 行，回放 miss）" % g["row_count"]
        elif not anchored and g["row_count"] == 1 and has_supplier:
            confidence = "medium"
            evidence = "一次性写法（1 行，回放 miss），供应商锚定存在：%s" % g["sups"].most_common(1)[0][0]
        elif anchored:
            confidence = "low"
            evidence = "回放已锚定（%s），对照用，不进人审首屏" % candidate_sku
        else:
            confidence = "low"
            evidence = "一次性写法（1 行，回放 miss），无供应商锚定"

        candidates.append({
            "canonical_writing": key,
            "row_count": g["row_count"],
            "raw_writings": ";".join("%s×%d" % (n, c) for n, c in
                                     sorted(g["raws"].items(), key=lambda kv: (-kv[1], kv[0]))),
            "supplier_names": ";".join("%s×%d" % (s, c) for s, c in
                                       sorted(g["sups"].items(), key=lambda kv: (-kv[1], kv[0]))),
            "receipt_id_samples": ";".join(str(r) for r in sorted(set(g["rids"]))[:5]),
            "replay_match": replay_match,
            "candidate_sku": candidate_sku,
            "confidence": confidence,
            "evidence": evidence,
            "suggested_action": _SUGGESTED_ACTION,
        })

    stats = {
        "total_rows": len(rows),
        "excluded_residue_rows": excluded_residue,
        "excluded_unmatchable_rows": excluded_unmatchable,
        "live_rows": len(live_rows),
        "distinct_writings": len(groups),
        "anchored_writings": sum(1 for c in candidates if c["replay_match"] == "hit"),
        "miss_writings": sum(1 for c in candidates if c["replay_match"] == "miss"),
        "family_writings": sum(len(v) for v in family_members.values()),
        "family_rows": sum(groups[k]["row_count"] for v in family_members.values() for k in v),
        "by_confidence": Counter(c["confidence"] for c in candidates),
        "rows_by_confidence": Counter(),
    }
    for c in candidates:
        stats["rows_by_confidence"][c["confidence"]] += c["row_count"]
    high_keys = {c["canonical_writing"] for c in candidates if c["confidence"] == "high"}
    stats["high_writings"] = len(high_keys)
    stats["high_rows"] = sum(c["row_count"] for c in candidates if c["confidence"] == "high")
    # 未锚定重复（miss 且 ≥2 行）：stdout 偏差对照规格 Q9 用
    dup = [c for c in candidates if c["replay_match"] == "miss" and c["row_count"] >= 2]
    stats["unanchored_dup_writings"] = len(dup)
    stats["unanchored_dup_rows"] = sum(c["row_count"] for c in dup)
    return candidates, stats


def _sku_name(sku_id):
    """回放命中行的 SKU 名（只读查询；与 _match_sku 同库）。"""
    conn = sqlite3.connect("file:%s?mode=ro" % os.environ["DB_PATH"], uri=True)
    try:
        row = conn.execute("SELECT name FROM skus WHERE id = ?", (sku_id,)).fetchone()
    finally:
        conn.close()
    return (row[0] if row else "") or ""


def write_csv(candidates, out_dir):
    """候选 CSV（UTF-8 with BOM）。排序：confidence → row_count 降序 → canonical_writing 升序。"""
    os.makedirs(out_dir, exist_ok=True)
    ordered = sorted(candidates, key=lambda c: (CONFIDENCE_ORDER[c["confidence"]],
                                                -c["row_count"], c["canonical_writing"]))
    path = os.path.join(out_dir, "sku_alias_candidates.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_HEADERS)
        for c in ordered:
            w.writerow([c[h] for h in CSV_HEADERS])
    return path, ordered


def main():
    ap = argparse.ArgumentParser(
        description="SKU 别名候选生成器：只产候选 CSV，零自动登记（登记走 approve 人工路径）")
    ap.add_argument("--out-dir", default=os.path.join(DEMO_DIR, "evalsets", "alias_candidates"),
                    help="产物目录（默认 demo/evalsets/alias_candidates/）")
    args = ap.parse_args()

    # live 防护必须先于任何 app.* import（import 即对 DB_PATH 执行迁移块）
    db_path = guard_db_path()
    if DEMO_DIR not in sys.path:
        sys.path.insert(0, DEMO_DIR)
    _scripts_dir = os.path.dirname(os.path.abspath(__file__))
    if _scripts_dir not in sys.path:
        sys.path.insert(0, _scripts_dir)
    from app.services.receipt_utils import _match_sku, _normalize_sku_name

    rows = load_rows(db_path)
    candidates, stats = build_candidates(rows, (_match_sku, _normalize_sku_name))
    csv_path, ordered = write_csv(candidates, args.out_dir)

    print("全量明细行        : %d" % stats["total_rows"])
    print("剔除残渣          : %d 行（判别式 residue_rules.py）" % stats["excluded_residue_rows"])
    print("剔除 '0T'         : %d 行（OCR 废码，永不可匹配，不产候选）" % stats["excluded_unmatchable_rows"])
    print("live 行           : %d（覆盖率分母口径：live 行 / 归一写法）" % stats["live_rows"])
    print("归一写法          : %d 种（覆盖率分母）" % stats["distinct_writings"])
    print("回放命中/未锚定    : %d / %d 种（真跑 _match_sku 全局口径，与 backfill --replay 同源）"
          % (stats["anchored_writings"], stats["miss_writings"]))
    print("OCR 家族          : %d 种/%d 行" % (stats["family_writings"], stats["family_rows"]))
    print("高置信段          : %d 种/%d 行（家族 ∪ 未锚定重复，置顶）" % (stats["high_writings"], stats["high_rows"]))
    print("置信段            : high %d 种/%d 行；medium %d 种/%d 行；low %d 种/%d 行" % (
        stats["by_confidence"]["high"], stats["rows_by_confidence"]["high"],
        stats["by_confidence"]["medium"], stats["rows_by_confidence"]["medium"],
        stats["by_confidence"]["low"], stats["rows_by_confidence"]["low"]))
    # §8 纪律：实测与规格校准参考值逐项对照显式打印（相符/偏差都呈报，依据见 docstring）
    print("")
    print("【与规格校准值对照（SPEC_PHASE1 §8 纪律，基准=规格 Q9/C-R1，详见 docstring 偏差声明）】")
    for label, key in (
            ("回放命中(种)", "anchored_writings"),
            ("回放miss(种)", "miss_writings"),
            ("未锚定重复(种)", "unanchored_dup_writings"),
            ("未锚定重复(行)", "unanchored_dup_rows"),
            ("高置信段(种)", "high_writings"),
            ("高置信段(行)", "high_rows"),
            ("OCR家族(种)", "family_writings"),
            ("OCR家族(行)", "family_rows")):
        actual = stats[key]
        ref = SPEC_REFERENCE[key]
        verdict = "相符" if actual == ref else "偏差（原因见 docstring，判别式未改）"
        print("  %-18s 实测 %s / 规格 %s —— %s" % (label, actual, ref, verdict))
    print("候选 CSV          : %s（人审是唯一消费方式；登记只能走 approve 路径，本工具零写库）"
          % csv_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
