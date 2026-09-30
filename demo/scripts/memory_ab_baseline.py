# -*- coding: utf-8 -*-
"""VendorMemory 观察性 A/B 基线与双比例 Z 检验（只读）。

为什么要单独成脚本：计划 §7 的验收判据是「有记忆组修改率相对对照组**翻正**」，
而现有 `memory_metrics.py` 的 `edit_rate_ab` 用的是**代理变量**——「该供应商在
vendor_memory 里有行」。这个代理变量在本项目会引入选择偏差（gap 05 §H4）：记忆
只有在「门禁打回后的重试轮」才会被注入，于是"有记忆"这个标签同时等价于"这是一张
难单"，测出来的关系方向与真实因果相反（live 基线：有记忆 27.93% vs 无记忆 23.22%）。

本脚本改用**处理变量的真实定义**——`receipts.rag_context_json` 非空，即该单据的
识别提示里**真的**被注入了记忆；同时保留代理口径一起输出，好把两个口径的差异摊开
解释，而不是只报一个数字。另外给出双比例 Z 检验，使「差异是否统计显著」有可复算
答案，而不是只看两个百分点的差。

公式来源与边界处理（自实现，见 `two_proportion_z` docstring）：
- 合并方差（pooled）双比例 z 检验；
- 双侧 p 值用 math.erf 精确算标准正态 CDF；
- 任一组 n=0 或方差为 0 时不抛异常，只返回不可判定；
- 任一组 n<30 一律标 low_confidence 并把结论写成「不足以判定」（沿用
  db.get_grey_compare 的 min_sample=30 口径），**不给显著结论**。

用法：
  python demo/scripts/memory_ab_baseline.py                     # 打印当前 A/B 基线
  python demo/scripts/memory_ab_baseline.py --json              # 输出 JSON
  python demo/scripts/memory_ab_baseline.py --save /tmp/ab.json # 存基线
  python demo/scripts/memory_ab_baseline.py --compare /tmp/ab.json

只读：以 mode=ro 打开库；不 import app.*（避免触发引擎初始化等模块级副作用）。
"""

import argparse
import json
import math
import os
import sqlite3
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.environ.get("DB_PATH") or os.path.join(REPO, "demo", "receipt_demo.db")

# 与 db.get_grey_compare / db.get_experiment_report 一致的样本量下限
MIN_SAMPLE = 30

# 与 memory_metrics.py 同口径：这三种值都表示「没有注入过记忆」
EMPTY_CTX = ("", "{}", '""')

TREATMENT = "treatment"
CONTROL = "control"


def _conn(db_path):
    """只读连接。绝不用可写连接 —— 本脚本不得改动任何库内容。"""
    return sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)


def has_rag_context(raw):
    """rag_context_json 是否表示「真的注入过记忆」（口径与 memory_metrics.py 一致）。"""
    return str(raw or "").strip() not in EMPTY_CTX


def two_proportion_z(x_t, n_t, x_c, n_c):
    """双比例 z 检验：H0 为 p_treatment == p_control。

    公式来源：合并方差（pooled）双比例检验，见 Agresti & Caffo (2000),
    "Simple and effective confidence intervals for proportions" 及标准教科书：
        p_hat = (x_t + x_c) / (n_t + n_c)
        SE    = sqrt(p_hat * (1 - p_hat) * (1/n_t + 1/n_c))
        z     = (p_t - p_c) / SE       # 正号 = 处理组修改率更高
    双侧 p 值 = 2 * (1 - Φ(|z|))，Φ 用 math.erf 精确实现，不使用查表近似。

    为什么重复实现而不是复用 db.py 里的 `_z_test`：它是 `get_experiment_report`
    内部的嵌套函数，模块级不可导入；且本脚本按只读约定不 import app.*。两处公式
    一致（pooled + erf 双侧），重复的只是 6 行公式，不是判据。

    边界处理（一律返回结构、绝不抛异常）：
    - 任一组 n<=0：p 无定义，返回 z=None；
    - p_hat 为 0 或 1（两组修改率同为 0% 或同为 100%）：方差为 0，z 无定义，
      此时两组差异必然为 0，判「不显著」由调用方按 note 处理。
    """
    if n_t <= 0 or n_c <= 0:
        return {"z": None, "p_value": None, "effect_size_pp": None,
                "note": "任一组样本为 0，双比例 z 检验无定义"}
    p_t = x_t / float(n_t)
    p_c = x_c / float(n_c)
    p_hat = (x_t + x_c) / float(n_t + n_c)
    var = p_hat * (1.0 - p_hat) * (1.0 / n_t + 1.0 / n_c)
    if var <= 0:
        return {"z": None, "p_value": None,
                "effect_size_pp": round((p_t - p_c) * 100, 4),
                "note": "两组修改率同为 0% 或同为 100%，方差为 0，z 无定义"}
    z = (p_t - p_c) / math.sqrt(var)
    p_value = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(z) / math.sqrt(2.0))))
    return {"z": round(z, 6), "p_value": round(p_value, 8),
            "effect_size_pp": round((p_t - p_c) * 100, 4), "note": ""}


def load_receipts(conn):
    """读出参与 A/B 的全部单据（只看需要的列）。

    返回 (rows, vendor_memory 供应商名集合)。每行是一个 dict：
      id / supplier_name / status / edited / has_rag_context / has_vendor_memory
    与 memory_metrics.py 一样**不排除软删单据**，否则与计划 §1 记录的基线
    （701 张单据、12 张有 rag_context）不可比。
    """
    vendors = set()
    for (v,) in conn.execute("select distinct vendor from vendor_memory"):
        nm = str(v or "").strip()
        if nm:
            vendors.add(nm)

    rows = []
    for rid, name, status, ctx in conn.execute(
            "select id, ifnull(supplier_name,''), ifnull(status,''), "
            "ifnull(rag_context_json,'') from receipts"):
        nm = str(name or "").strip()
        rows.append({
            "id": rid,
            "supplier_name": nm,
            "status": status,
            "edited": status == "edited",
            "has_rag_context": has_rag_context(ctx),
            "has_vendor_memory": bool(nm) and nm in vendors,
        })
    return rows, vendors


def classify_rag_context(row):
    """真实处理定义：该单据的识别提示里真的被注入了记忆。"""
    return TREATMENT if row["has_rag_context"] else CONTROL


def classify_vendor_memory(row):
    """代理口径：该供应商在 vendor_memory 里有行（会被难单标记污染，仅供对照）。"""
    return TREATMENT if row["has_vendor_memory"] else CONTROL


def _group(rows):
    n = len(rows)
    edited = sum(1 for r in rows if r["edited"])
    return {"receipts": n, "edited": edited,
            "edited_rate_pct": round(100.0 * edited / n, 2) if n else 0.0}


def ab_stats(rows, classify):
    """按 classify（行 -> treatment/control）分组算修改率与 Z 检验。

    classify 以可调用对象注入而不是写死 SQL，是为了让反证用例能把处理组定义故意
    换成「全部单据」（分母错）并断言正确性断言因此失败 —— 定义方式可替换，
    正确性判据才可被反证，否则它只是恒真断言。
    """
    t = [r for r in rows if classify(r) == TREATMENT]
    c = [r for r in rows if classify(r) == CONTROL]
    gt, gc = _group(t), _group(c)
    z = two_proportion_z(gt["edited"], gt["receipts"], gc["edited"], gc["receipts"])
    low = min(gt["receipts"], gc["receipts"]) < MIN_SAMPLE

    if gt["receipts"] == 0 or gc["receipts"] == 0:
        significant = False
        verdict = "不足以判定：任一组样本为 0（两组各需 >= %d）" % MIN_SAMPLE
    elif low:
        significant = False
        verdict = "不足以判定：样本不足（任一组 n < %d）" % MIN_SAMPLE
    elif z["p_value"] is None:
        significant = False
        verdict = "差异不显著（两组修改率相同，方差为 0，z 无定义）"
    elif z["p_value"] < 0.05:
        significant = True
        verdict = "差异统计显著（双侧 p=%.4g < 0.05）" % z["p_value"]
    else:
        significant = False
        verdict = "差异统计不显著（双侧 p=%.4g >= 0.05）" % z["p_value"]

    delta = round(gt["edited_rate_pct"] - gc["edited_rate_pct"], 2)
    if delta > 0:
        direction = "处理组修改率更高（与目标方向相反）"
    elif delta < 0:
        direction = "处理组修改率更低（目标方向）"
    else:
        direction = "两组持平"

    return {
        "treatment": gt,
        "control": gc,
        "delta_pp": delta,
        "direction": direction,
        "z_test": z,
        "min_sample": MIN_SAMPLE,
        "low_confidence": low,
        "significant_at_0_05": significant,
        "verdict": verdict,
    }


# 处理变量的两种定义：(键, 说明, 分类函数, 口径性质)
DEFINITIONS = (
    ("rag_context",
     "处理组=本次单据真的被注入记忆（receipts.rag_context_json 非空）",
     classify_rag_context,
     "真实处理定义"),
    ("vendor_memory",
     "处理组=该供应商在 vendor_memory 里有行",
     classify_vendor_memory,
     "代理口径：记忆只在打回的重试轮注入，该标签同时标记了难单，会引入选择偏差"),
)


def collect(db_path=None):
    """读 live 库（只读）产出两种口径的 A/B 基线。"""
    path = db_path or DB
    c = _conn(path)
    try:
        rows, vendors = load_receipts(c)
    finally:
        c.close()

    defs = {}
    for key, label, fn, nature in DEFINITIONS:
        s = ab_stats(rows, fn)
        s["label"] = label
        s["nature"] = nature
        defs[key] = s

    primary = defs["rag_context"]
    report = {
        "db_path": path,
        "total_receipts": len(rows),
        "vendor_memory_vendors": len(vendors),
        "primary_definition": "rag_context",
        "definitions": defs,
        "conclusion": (
            "以「本次单据是否真的被注入了记忆」（rag_context_json 非空）为处理变量："
            "处理组修改率 %.2f%%（%d/%d）vs 对照组 %.2f%%（%d/%d），差值 %+.2fpp；%s；%s"
            % (primary["treatment"]["edited_rate_pct"], primary["treatment"]["edited"],
               primary["treatment"]["receipts"], primary["control"]["edited_rate_pct"],
               primary["control"]["edited"], primary["control"]["receipts"],
               primary["delta_pp"], primary["verdict"], primary["direction"])),
        "selection_bias_note": (
            "代理口径（供应商有 vendor_memory 行）与真实口径的分组规模不同，"
            "两者差值不可混读：真实口径测「本次是否注入」，代理口径测「该供应商是否有记忆」，"
            "后者额外混入了「难单才被注入」的选择偏差。"),
    }
    return report


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--save")
    ap.add_argument("--compare")
    a = ap.parse_args(argv)
    cur = collect()
    if a.save:
        with open(a.save, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=2)
        print("已保存 A/B 基线 -> %s" % a.save)
    if a.compare:
        with open(a.compare, encoding="utf-8") as f:
            base = json.load(f)
        print("=== 与基线对比（基线: %s）===" % a.compare)
        for key in ("rag_context", "vendor_memory"):
            b = base.get("definitions", {}).get(key, {})
            n = cur["definitions"].get(key, {})
            for grp in (TREATMENT, CONTROL):
                b0 = b.get(grp, {}).get("edited_rate_pct")
                n0 = n.get(grp, {}).get("edited_rate_pct")
                if b0 is not None and n0 is not None:
                    print("%-14s %-10s %s%% -> %s%% (%+.2f)"
                          % (key, grp, b0, n0, n0 - b0))
        print("%-14s %s -> %s （负值=处理组修改率更低，是目标方向）"
              % ("delta_pp", base.get("definitions", {}).get("rag_context", {}).get("delta_pp"),
                 cur["definitions"].get("rag_context", {}).get("delta_pp")))
        print("结论：%s" % cur["conclusion"])
    if a.json or not (a.save or a.compare):
        print(json.dumps(cur, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
