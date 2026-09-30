# -*- coding: utf-8 -*-
"""首轮供应商记忆注入的两项验收数字（只读；必须指向库的**副本**）。

why 需要这个脚本
----------------
`memory_metrics.py` 的 `memory_hit_effective` 回答的是「有条件命中的单据里，有多少条
`rag_context_json` 非空」；它用「非空」做分子判定，因此**不能**区分下面三种形态：

1. hint 通道真的检索到了记忆并注入（本任务要钉的「首轮注入」）；
2. hint 通道跑了但该供应商没有记忆 —— 此时 `effective_hint = vendor_ctx or vendor_hint`
   会把**裸 hint 名**当先验注入，`rag_context_json` **同样非空**（实测）；
3. 无 hint，靠 supervisor 的「首轮补检索」拿到先验 —— 但那次检索跑在首轮模型调用
   **之后**，先验只能喂给重试轮（Gap H4 的旧形态），不是首轮注入。

三者在「非空」口径下混在一起，于是「首轮注入成功占比」在库里读不出来。本脚本把它拆开，
判据全部取**落库产物里可判定的形态**（不靠猜、不靠模型自报）：

- hint 通道跑过的单据：`rag_context_json` 以 `[prior:hint]` 开头
  （该前缀由 `extract_chain._merge_priors` 写入；只有 hint 非空时才会出现）。
  hint 通道在生产入口是**每轮都跑**（`run_pipeline` 把 vendor_hint 透传给每轮
  `_run_extract`），且 `extract_receipt` 在组装 prompt **之前**就 await 检索结果，
  所以带该前缀 = 第一轮 prompt 里就有 hint 通道的载荷。
- 其中「真的注入了检索记忆」：`[prior:hint]` 之下的正文多行、且首行是 `[供应商名]`
  形态（检索载荷的渲染头行）—— 裸 hint 名只有一个单行、没有头行。
- 两个桶之外归入 `unclassified`（不猜：宁可留空也不折成一个合法值）。

`eligible_denominator` / `injected` 仍以 `memory_metrics.py` 为**唯一口径实现**，本脚本
不复制那套 SQL（本仓①类缺陷＝同一根因多份实现），只打一行指向它。

用法（先复制，永远不要对 live 库直接跑）：
    cp demo/receipt_demo.db /tmp/xxx.db
    DB_PATH=/tmp/xxx.db RAG_DIR=/tmp/xxx_rag python scripts/memory_hint_stats.py --json
"""

import argparse
import json
import os
import sqlite3
import sys

HINT_TAG_PREFIX = "[prior:hint]\n"

# 「有 hint 的单据」的历史口径缺口：`has_vendor_hint` 是后加的字段，早期 upload 事件
# 里根本没有这个键。键缺失**不等于**当时没传 hint（也就不能算进分母），故本脚本把
# 「带键事件数」与「全部事件数」两个分母都打出来，而不是折成一个。
CRITERIA = {
    "hint_coverage": (
        "按 user_event(event_type='upload') 的 properties.has_vendor_hint 统计；"
        "键缺失 ≠ 无 hint（该键是后加的，早期事件没有它），故同时给出「带键事件」与"
        "「全部 upload 事件」两个分母，两者不可混读"),
    "first_round_injection": (
        "hint 通道跑过 = rag_context_json 以 '[prior:hint]' 开头（该前缀由 "
        "extract_chain._merge_priors 写入，只有 hint 非空才出现）；其中注入成功 = "
        "'[prior:hint]' 之下的正文**不等于裸 hint 名**（检索载荷必然带有额外内容）—— "
        "hint 非空但该供应商无记忆时，写进去的正文就只是裸 hint 名，故两者可判定区分。"
        "判据刻意不依赖载荷渲染格式（老库无 '[供应商名]' 头行），只用「正文 == 供应商名」"),
    "not_first_round": (
        "'[prior:retry]' 前缀 = 重试轮注入；'[prior:parse]' 前缀 = 解析级修正轮；"
        "无任何前缀 = supervisor 首轮补检索（跑在首轮模型调用之后，先验只喂重试轮）"),
    "reverse_reading": (
        "rag_context_json 非空**不等于**真的注入了记忆：hint 非空但该供应商无记忆时，"
        "裸 hint 名也会被当成先验写进去（实测），这是 memory_metrics.injected 的已知上界"),
    "source": "只读 SQL（receipts / user_event）；不 import app.*（import 期会执行迁移）",
}


def _conn(path):
    return sqlite3.connect("file:%s?mode=ro" % path, uri=True)


def _hint_coverage(c):
    rows = c.execute("select properties from user_event where event_type='upload'").fetchall()
    total = with_key = hint_true = hint_false = 0
    for (props,) in rows:
        total += 1
        if not props:
            continue
        try:
            p = json.loads(props)
        except (TypeError, ValueError):
            continue
        if not isinstance(p, dict) or "has_vendor_hint" not in p:
            continue
        with_key += 1
        if p["has_vendor_hint"]:
            hint_true += 1
        else:
            hint_false += 1
    return {
        "upload_events_total": total,
        "upload_events_with_hint_key": with_key,
        "hint_true": hint_true,
        "hint_false": hint_false,
        "coverage_pct_of_keyed_events": (
            None if with_key == 0 else round(100.0 * hint_true / with_key, 2)),
        "coverage_pct_of_all_events": (
            None if total == 0 else round(100.0 * hint_true / total, 2)),
        "criteria": CRITERIA["hint_coverage"],
    }


def _classify_payload(payload):
    """落库先验的分类：hint 通道 / 重试轮 / 解析轮 / 无前缀（首轮补检索）/ 其它标签。"""
    text = str(payload or "")
    if not text.strip():
        return None
    for tag, kind in (("[prior:hint]\n", "hint"), ("[prior:retry]\n", "retry"),
                      ("[prior:parse]\n", "parse")):
        if text.startswith(tag):
            return kind
    if text.startswith("[prior:"):
        return "other_tagged"
    # 无 [prior:*] 前缀：生产上只有 supervisor 的「首轮补检索」会这样写
    # （它直接写检索原文，不经 _merge_priors）。老库另有 "供应商先验：" 之类的历史形态，
    # 那类也会落进这里 —— 与补检索同属「非首轮注入」，本脚本不再细分。
    return "untagged"


def _classify_hint_body(body, supplier_name):
    """`[prior:hint]` 之下的正文：检索载荷 / 裸 hint 名 / 无法判定。

    why 用「正文是否等于裸 hint 名」而不是「首行是不是 `[供应商名]` 头行」：载荷渲染格式
    跨版本变过（老库带 `[供应商名]` 头行，也有直接以 `版式：...` 开头的形态），用头行判定
    会把老库的真实注入算成「无法判定」。而「正文 == 供应商名」这条判据与格式无关 ——
    hint 名本身不会含换行，故多行必然说明有额外内容。

    三种结果：
      - "memory"：正文里出现了 hint 名之外的内容 → 来自检索（注入成功）；
      - "bare"：正文就是裸 hint 名（hint 非空但该供应商没有可用记忆时 `effective_hint =
        vendor_ctx or vendor_hint` 的形态）；
      - "unknown"：单行但拿不到供应商名可比对 → 不猜，单列出来。
    """
    b = str(body or "").strip()
    name = str(supplier_name or "").strip()
    if not b:
        return "unknown"
    if name and b == name:
        return "bare"
    if "\n" in b:
        return "memory"
    return "memory" if name else "unknown"


def _first_round_injection(c):
    rows = c.execute(
        "select rag_context_json, supplier_name from receipts "
        "where ifnull(rag_context_json,'') not in ('', '{}', '\"\"')").fetchall()
    hint_total = memory_payload = bare_hint = unclassified = 0
    retry = parse = untagged = other = 0
    for payload, supplier_name in rows:
        kind = _classify_payload(payload)
        if kind is None:
            continue
        if kind == "hint":
            hint_total += 1
            verdict = _classify_hint_body(str(payload)[len(HINT_TAG_PREFIX):], supplier_name)
            if verdict == "memory":
                memory_payload += 1
            elif verdict == "bare":
                bare_hint += 1
            else:
                unclassified += 1
        elif kind == "retry":
            retry += 1
        elif kind == "parse":
            parse += 1
        elif kind == "untagged":
            untagged += 1
        else:
            other += 1
    return {
        "hint_channel_receipts": hint_total,
        "injected_receipts": memory_payload,
        "bare_hint_only_receipts": bare_hint,
        "unclassified_receipts": unclassified,
        "injected_rate_pct_of_hint_channel": (
            None if hint_total == 0 else round(100.0 * memory_payload / hint_total, 2)),
        "retry_channel_receipts": retry,
        "parse_channel_receipts": parse,
        "backfill_receipts": untagged,
        "other_tagged_receipts": other,
    }


def collect(db_path=None):
    path = db_path or os.environ.get("DB_PATH") or "receipt_demo.db"
    c = _conn(path)
    try:
        out = {
            "db_path": path,
            "hint_coverage": _hint_coverage(c),
            "first_round_injection": _first_round_injection(c),
            "criteria": CRITERIA,
            "note_eligible": (
                "「有条件的总体」（记忆有效命中率的分母）与 vendor_memory 的 active 计数本例"
                "**不重算** —— 唯一口径实现是 demo/scripts/memory_metrics.py 的 "
                "memory_hit_effective，请一并运行；本脚本只回答「hint 覆盖」与「首轮注入是否"
                "发生」这两问，避免同一口径出现第二份实现"),
        }
    finally:
        c.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--db", help="数据库副本路径（缺省取 DB_PATH 环境变量）")
    a = ap.parse_args()
    cur = collect(a.db)
    if a.json:
        print(json.dumps(cur, ensure_ascii=False, indent=2))
        return 0
    hc, fr = cur["hint_coverage"], cur["first_round_injection"]
    print("库：%s" % cur["db_path"])
    print("== hint 覆盖（事件侧） ==")
    print("  upload 事件 %s 条；带 has_vendor_hint 键 %s 条；hint=true %s 条，false %s 条" % (
        hc["upload_events_total"], hc["upload_events_with_hint_key"],
        hc["hint_true"], hc["hint_false"]))
    print("  有 hint 占比：带键事件 %s%% / 全部事件 %s%%（键缺失≠无 hint，两个分母不可混读）" % (
        hc["coverage_pct_of_keyed_events"], hc["coverage_pct_of_all_events"]))
    print("== 首轮注入（产物侧） ==")
    print("  hint 通道跑过 %s 条；其中真的注入了检索记忆 %s 条（%s%%）；"
          "只注入裸 hint 名 %s 条；无法判定 %s 条" % (
              fr["hint_channel_receipts"], fr["injected_receipts"],
              fr["injected_rate_pct_of_hint_channel"],
              fr["bare_hint_only_receipts"], fr["unclassified_receipts"]))
    print("  非首轮通道：重试轮 %s 条 / 解析轮 %s 条 / 首轮补检索 %s 条 / 其它标签 %s 条" % (
        fr["retry_channel_receipts"], fr["parse_channel_receipts"],
        fr["backfill_receipts"], fr["other_tagged_receipts"]))
    print("  说明：%s" % cur["note_eligible"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
