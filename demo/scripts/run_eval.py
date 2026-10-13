# -*- coding: utf-8 -*-
"""可复现评测 harness —— Gap A1 + A5a。

读 `demo/evalsets/manifest.csv` 的某个 split → 逐张跑抽取 → 与 expected/ GT 比对 →
输出 `EvalReport` 并落盘 `ai_registry/benchmarks/eval_runs/<ts>_<split>_<prompt_ver>.json`。

用法：
    python run_eval.py --split test --prompt v1_2_0_sku_clean
    python run_eval.py --split test --prompt v1_2_8_anti_injection --engine openai \\
                       --model qwen3.5-omni-flash --limit 20
    python run_eval.py --split val --prompt v1_2_0_sku_clean --engine stub   # harness 自检（离线）

`--engine stub` 直接回读 expected 作为预测，**仅用于验证 harness 通路，严禁用于出分**；
报告里 engine 字段会如实写 "stub"。

契约：run_eval(split, prompt_version, engine, model) -> EvalReport
    EvalReport = {accuracy, cer, per_field_accuracy, per_field_f1, field_f1_groups,
                  edit_proxy, evidence_coverage, avg_tokens, p50_latency_ms,
                  gt_status_breakdown, sample_ids[]}

per-field P/R/F1（阶段 0 工作项①，并列新增口径，不替换 accuracy）——
TP/FP/FN 唯一权威口径（对字段 f 的每个计分实例）：

    | 情形                                            | 判定        |
    |-------------------------------------------------|------------|
    | 对齐对 (e,p)，e 与 p 的 f 双 present 且相等       | TP +1      |
    | 对齐对 (e,p)，双 present 但不等                   | FP +1 且 FN +1（值错误） |
    | 对齐对 (e,p)，e present、p 缺失（None/空）        | FN +1      |
    | 对齐对 (e,p)，e 缺失、p present                  | FP +1（多抽） |
    | 双缺失                                           | 不计分（跳过） |
    | 未配对 GT 行 (e, None)：e 的 f present            | FN +1      |
    | 未配对预测行 (None, p)：p 的 f present            | FP +1      |

聚合一律 **micro**（整集 tp/fp/fn 直接累加后再算 P/R/F1），明令禁止样本级 F1 求平均
（macro）——33 张小分母上单张 1.0 会稀释真实错误。precision=tp/(tp+fp)、
recall=tp/(tp+fn)、f1=2PR/(P+R)，分母 0 取 0.0；P/R/F1 round 4，tp/fp/fn 为 int。
item_count / CER / evidence_coverage 非抽取字段，不进 F1 体系。
"""

import argparse
import csv
import enum
import hashlib
import json
import os
import re
import statistics
import sys
import time
from datetime import date, datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
DEMO_DIR = os.path.abspath(os.path.join(_HERE, ".."))
REPO_ROOT = os.path.abspath(os.path.join(DEMO_DIR, ".."))
for _p in (_HERE, DEMO_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 付款标记等字段的归一收敛到 scripts/eval_norm.py 单一实现（与 GT 生成侧同口径）
import eval_norm  # noqa: E402

DEFAULT_EVALSET_DIR = os.path.join(DEMO_DIR, "evalsets")
DEFAULT_REPORT_DIR = os.path.join(REPO_ROOT, "ai_registry", "benchmarks", "eval_runs")
DEFAULT_PROMPT_VERSION = "v1_2_0_sku_clean"

# 比对字段：整单 4 项 + 明细行 5 项；item_count 单独统计（漏行/多行的关键信号）
TOP_FIELDS = ("vendor", "date", "total", "doc_form")
ITEM_FIELDS = ("name", "qty", "unit", "unit_price", "amount")
# GT schema v2（对齐 ReceiptData 契约，合并反馈①）：付款标记必须比对；费用/币种/注记
# 按「GT 与识别双有」原则条件比对（见 compare），避免恒 0 字段稀释分数。
GT_V2_FEE_FIELDS = ("discount_amount", "deposit_amount", "delivery_fee",
                    "service_fee", "tax_amount", "rounding_adjustment")

# ------------------------------------------------------------------
# 阶段 0 评测器口径常量（以下均为「口径分」：只修尺子不修模型，任何分数变化
# 须与模型/数据改进分句陈述，见 EXPLORATION_REPORT §4.1 披露纪律）
# ------------------------------------------------------------------
# E2 品名对齐容差：编辑距离 ≤ max(2, 30%·len) 才允许模糊配对（保守，宁漏勿错）
NAME_FUZZY_REL_TOL = 0.3        # [口径分]
NAME_FUZZY_MIN_EDIT = 2         # [口径分]
# E5a vendor 包含式：互为子串且短边归一后长度 ≥ 4 才算等价（防短串蹭分）
VENDOR_CONTAIN_MIN_LEN = 4      # [口径分]


# ------------------------------------------------------------------
# 归一化
# ------------------------------------------------------------------
def _norm_text(s):
    """文本归一：去空白/常见标点 + 全角半角 + 大小写。

    [口径分] E1：pydantic>=2 的 model_dump(mode="python") 对 str-Enum 返回**枚举成员**
    而非字符串（见 app.models.DocForm / supervisor.data.model_dump()），str() 成员得到
    "DocForm.PRINTED" 而非 "printed_delivery_note"，导致 doc_form 恒错。首行先做枚举
    转型，同时覆盖未来任何 enum 字段；不改 app/models.py、不改 eval_norm.py。
    """
    if isinstance(s, enum.Enum):
        s = s.value          # pydantic v2 model_dump() 对 str-Enum 返回成员而非字符串
    s = str(s if s is not None else "").strip().lower()
    s = re.sub(r"[\s　]+", "", s)
    s = re.sub(r"[()（）\[\]【】,，.。:：\-_/\\|]", "", s)
    return s


def _norm_name(s):
    return _norm_text(s)


def _norm_date(s):
    """日期归一：接受 2026-08-01 / 2026/8/1 / 01-08-2026 等，统一成 YYYY-MM-DD。"""
    s = str(s if s is not None else "").strip()
    m = re.search(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", s)
    if m:
        return "%04d-%02d-%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return s


def _norm_num(v, ndigits=2):
    """数值归一：货币符号/千分位/单位后缀 → float，失败返回 None（None 视为缺失）。"""
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return round(float(v), ndigits)
    m = re.search(r"[-+]?\d[\d,]*\.?\d*", str(v).replace(" ", ""))
    if not m:
        return None
    try:
        return round(float(m.group(0).replace(",", "")), ndigits)
    except ValueError:
        return None


# 付款标记归一：bool/常见字面量 → bool；缺失或不可判 → None（不参与比对）。
# 口径收敛到 eval_norm.coerce_payment_marked（与 GT 生成侧同一实现）。why: 旧实现各自维护
# 字面量表，评分侧漏了 "y"/"n" —— 预测写 "Y"/"N" 时被判为不可判而直接跳过比对，
# payment_marked 实际上永远没被比过（指标静默缺失）。直接别名，杜绝再次分叉。
_norm_payment_marked = eval_norm.coerce_payment_marked


# ------------------------------------------------------------------
# 单位语义等价（阶段 0 工作项③，[口径分]）：阶段 2A 工作项②起，实现整体上收至
# app/services/unit_normalization.py（评分器与 unit 类记忆事实共用同一把尺子，
# 消灭两份归一/判等逻辑）。本文件只保留同名别名符号——阶段 0 测试
# （test_eval_unit_equivalence.py）对这些符号的行为级断言零改动全绿（零漂移判据）。
# why：旧口径对 item.unit 只做归一后字符串严格相等（P5），司马斤/斤、瓶/樽/btl 等
# 等价写法被判错；换算索引已存在且与权威一致，但未接进评分。
# ------------------------------------------------------------------
from app.services.unit_normalization import (  # noqa: E402
    load_unit_equivalence, norm_unit, units_equal)

UNIT_EQUIV_PATH = os.path.join(DEMO_DIR, "app", "services", "unit_equivalence.yaml")
_load_unit_equivalence = load_unit_equivalence  # 模块别名（测试直接传 tmp 路径仍成立）
_UNIT_EQUIV = load_unit_equivalence(UNIT_EQUIV_PATH)


def _norm_unit(raw):
    """单位归一（[口径分]）：枚举转型 → 规格污染括号剥离 → _norm_text。"""
    return norm_unit(raw, _UNIT_EQUIV)


def _units_equal(a_raw, b_raw):
    """单位等价判定（[口径分]）：短路顺序 1/2/3 见 unit_normalization.units_equal。"""
    return units_equal(a_raw, b_raw, _UNIT_EQUIV)


def normalize(payload):
    """把 GT / 预测统一成比对用的结构（兼容 supplier_name / total_amount / quantity 等别名）。

    v2 字段（对齐 ReceiptData 契约）：payment_marked / currency / 各项费用 /
    adjustment_notes 一并归一；旧 GT 缺这些键时为 None/空，compare 按「双有」跳过。
    """
    _EMPTY = {"doc_form": "", "vendor": "", "date": "", "total": None, "items": [],
              "payment_marked": None, "currency": "", "fees": {}, "adjustment_notes": []}
    if payload is None:
        return dict(_EMPTY)
    if not isinstance(payload, dict):
        try:
            payload = dict(payload)
        except Exception:
            return dict(_EMPTY)

    items = []
    for it in (payload.get("items") or []):
        if not isinstance(it, dict):
            continue
        name = it.get("name", it.get("item_name", ""))
        qty = it.get("qty", it.get("quantity"))
        items.append({
            "name": _norm_name(name),
            "qty": _norm_num(qty),
            # [口径分] ③：unit 归一改走 _norm_unit（枚举转型 + 规格括号剥离 + _norm_text），
            # 使「箱（48罐）」在比对前先剥成「箱」；空串语义不变（空 = 缺失）
            "unit": _norm_unit(it.get("unit", "")),
            "unit_price": _norm_num(it.get("unit_price")),
            "amount": _norm_num(it.get("amount")),
            "evidence": it.get("evidence"),
        })
    total = payload.get("total", payload.get("total_amount"))

    fees = {}
    for key in GT_V2_FEE_FIELDS:
        fees[key] = _norm_num(payload.get(key))
    notes = payload.get("adjustment_notes")
    if isinstance(notes, list):
        norm_notes = sorted(_norm_text(n) for n in notes if str(n or "").strip())
    else:
        norm_notes = []
    return {
        "doc_form": _norm_text(payload.get("doc_form", "")),
        "vendor": _norm_text(payload.get("vendor", payload.get("supplier_name", ""))),
        "date": _norm_date(payload.get("date", "")),
        "total": _norm_num(total),
        "items": items,
        "payment_marked": _norm_payment_marked(payload.get("payment_marked")),
        "currency": _norm_text(payload.get("currency", "")),
        "fees": fees,
        "adjustment_notes": norm_notes,
    }


# ------------------------------------------------------------------
# 编辑距离（CER）
# ------------------------------------------------------------------
def _levenshtein(a, b):
    """标准 DP 编辑距离（无第三方依赖）。"""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1]


# ------------------------------------------------------------------
# 证据覆盖率
# ------------------------------------------------------------------
def _evidence_of(item):
    """取明细行的字段级证据；dict 与对象（ReceiptItem）都兼容。"""
    if isinstance(item, dict):
        return item.get("evidence")
    return getattr(item, "evidence", None)


def _evidence_coverage(items):
    """有字段级证据的明细行占比（T7 前恒为 0，不阻断）。"""
    if not items:
        return 0.0
    hit = sum(1 for it in items if _evidence_of(it))
    return round(hit / float(len(items)), 4)


# ------------------------------------------------------------------
# 单张比对
# ------------------------------------------------------------------
def _align_items(exp_items, pred_items):
    """明细行对齐（[口径分] 三段式，宁漏勿错）。

    段 A（优先级不变）：归一品名严格相等贪心——完全匹配 GT 的路径逐字节同结果。
    段 B（模糊对齐）：剩余行按品名编辑距离配对，阈值
        dist ≤ min(max(NAME_FUZZY_MIN_EDIT, floor(NAME_FUZZY_REL_TOL·len)), len-1)
    按 (距离, exp 下标, pred 下标) 升序贪心取不冲突对（确定性）。
    段 C（数字签名兜底）：仍剩余者按 (qty, unit_price, amount)（经 _norm_num）完全
    相等、且 sig 至少一个非 None、且该 sig 在剩余 exp 与剩余 pred 中**各自唯一**
    （并列即不配）才配对；按 (exp 下标, pred 下标) 升序。

    why [口径分]：旧实现按品名严格相等配对，1 处品名 OCR 误读（S030 江團魚 vs 江鱸魚）
    把 qty/price/amount 全对的整行 5 字段级联计错（E2 对齐级联放大）。段 B 的
    `min(…, len-1)` 上限是防短名被 max(2,…) 下限整个吃进容差：2 字名 dist=2 等于整个
    名字都换掉（白菜 vs 蘿蔔），相对容差 100% 不应配对。
    只修尺子不修模型。返回 [(exp_item|None, pred_item|None)]，未配上的一侧为 None。
    """
    used_e = set()
    used_p = set()
    pairs_idx = []
    # 段 A：归一品名严格相等贪心（原口径，行为不变）
    for i, e in enumerate(exp_items):
        ename = e.get("name", "")
        matched = None
        for j, p in enumerate(pred_items):
            if j in used_p:
                continue
            if ename and p.get("name") == ename:
                matched = j
                break
        if matched is not None:
            used_e.add(i)
            used_p.add(matched)
            pairs_idx.append((i, matched))
    # 段 B：品名编辑距离模糊配对（确定性：按 (距离, exp 下标, pred 下标) 升序贪心）
    cands = []
    for i, e in enumerate(exp_items):
        if i in used_e:
            continue
        ename = e.get("name", "")
        if not ename:
            continue
        tol = min(max(NAME_FUZZY_MIN_EDIT, int(NAME_FUZZY_REL_TOL * len(ename))),
                  max(len(ename) - 1, 0))
        for j, p in enumerate(pred_items):
            if j in used_p:
                continue
            if _levenshtein(ename, p.get("name", "")) <= tol:
                cands.append((_levenshtein(ename, p.get("name", "")), i, j))
    for _d, i, j in sorted(cands):
        if i in used_e or j in used_p:
            continue
        used_e.add(i)
        used_p.add(j)
        pairs_idx.append((i, j))
    # 段 C：数字签名兜底（两侧各自唯一才配，并列即不配，宁漏勿错）
    def _sig(item):
        return tuple(_norm_num(item.get(k)) for k in ("qty", "unit_price", "amount"))

    from collections import Counter
    rem_e = [(i, _sig(e)) for i, e in enumerate(exp_items) if i not in used_e]
    rem_p = [(j, _sig(p)) for j, p in enumerate(pred_items) if j not in used_p]
    exp_sig_count = Counter(s for _, s in rem_e)
    pred_sig_count = Counter(s for _, s in rem_p)
    for i, j in sorted((i, j) for i, si in rem_e for j, sj in rem_p
                       if si == sj and any(v is not None for v in si)
                       and exp_sig_count[si] == 1 and pred_sig_count[sj] == 1):
        if i in used_e or j in used_p:
            continue
        used_e.add(i)
        used_p.add(j)
        pairs_idx.append((i, j))
    # 组装：保持原顺序语义——按 exp 下标输出配对/未配对行，尾部追加未配对预测行
    by_e = dict(pairs_idx)
    used_p_final = set(by_e.values())
    out = []
    for i, e in enumerate(exp_items):
        j = by_e.get(i)
        out.append((e, pred_items[j]) if j is not None else (e, None))
    for j, p in enumerate(pred_items):
        if j not in used_p_final:
            out.append((None, p))
    return out


class FieldStat(list):
    """field_stats 行：[total, matched, tp, fp, fn]（前两位语义冻结）。

    why 子类定 __eq__：既有断言（test_eval_harness / test_eval_payment_marked_norm）
    写作 `field_stats["payment_marked"] == [1, 1]`——扩出 F1 三元组后该断言形式必须
    继续成立，故前两位与两位 list 判等兼容；len!=2 的普通 list 语义不变。
    """
    def __eq__(self, other):
        if isinstance(other, list) and len(other) == 2 and len(self) == 5:
            return self[0] == other[0] and self[1] == other[1]
        return list.__eq__(self, other)

    def __ne__(self, other):
        return not self.__eq__(other)


# F1 presence 的文本类字段（归一后非空 = present）；其余数值/布尔字段 not None 即 present
_F1_TEXT_FIELDS = ("vendor", "date", "doc_form", "currency", "item.name", "item.unit")
# 不进 F1 体系的字段（非抽取字段，避免双重计分；维持 accuracy 键）
_NON_F1_FIELDS = ("item_count",)


def _field_present(field, value):
    """字段计分实例的 presence 判定：文本类归一后非空；数值/布尔 not None；
    adjustment_notes 为非空 list。"""
    if field in _F1_TEXT_FIELDS:
        return bool(value)
    if field == "adjustment_notes":
        return isinstance(value, list) and bool(value)
    return value is not None


def _vendor_equal(a, b):
    """vendor 等价判定（[口径分] E5a）：相等，或互为子串（任一方向）且短边归一后
    长度 ≥ VENDOR_CONTAIN_MIN_LEN。why：预测只出中文段 vs GT 全称必错的假错
    （ERRORS §3.2 E5）；短串下限防「公司」这类通用词蹭分。"""
    if a == b:
        return True
    if not a or not b:
        return False
    short, long_s = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) < VENDOR_CONTAIN_MIN_LEN:
        return False
    return short in long_s


def _prf(tp, fp, fn):
    """P/R/F1（分母 0 取 0.0；P/R/F1 round 4）。"""
    precision = tp / float(tp + fp) if (tp + fp) else 0.0
    recall = tp / float(tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return round(precision, 4), round(recall, 4), round(f1, 4)


def _per_field_f1_from_stats(field_stats):
    """由聚合 field_stats（值含 tp/fp/fn 位）产出 per_field_f1（micro 口径）。

    只输出有计分实例（tp+fp+fn>0）的字段；tp/fp/fn 保留原始 int 供 QA 手算复核。
    """
    out = {}
    for name, stat in sorted(field_stats.items()):
        if name in _NON_F1_FIELDS:
            continue
        tp, fp, fn = stat[2], stat[3], stat[4]
        if tp + fp + fn == 0:
            continue
        precision, recall, f1 = _prf(tp, fp, fn)
        out[name] = {"tp": tp, "fp": fp, "fn": fn,
                     "precision": precision, "recall": recall, "f1": f1}
    return out


# 口径拆分（工作项④）：high_freq / line_item 两口径组内 micro 聚合，两口径
# 不得互相搬家（防 86.4/98.2 式跨口径复发的机制性约束）。费用与 adjustment_notes
# 实例归入 high_freq。
F1_GROUP_HIGH_FREQ = ("vendor", "date", "total", "doc_form", "payment_marked",
                      "currency") + GT_V2_FEE_FIELDS + ("adjustment_notes",)
F1_GROUP_LINE_ITEM = tuple("item." + f for f in ITEM_FIELDS)


def _field_f1_groups(field_stats):
    """field_f1_groups：两组各 micro 聚合（tp/fp/fn 求和后算 P/R/F1）。"""
    groups = {}
    for gname, fields in (("high_freq", F1_GROUP_HIGH_FREQ),
                          ("line_item", F1_GROUP_LINE_ITEM)):
        tp = fp = fn = 0
        for f in fields:
            stat = field_stats.get(f)
            if not stat:
                continue
            tp += stat[2]
            fp += stat[3]
            fn += stat[4]
        precision, recall, f1 = _prf(tp, fp, fn)
        groups[gname] = {"fields": list(fields), "tp": tp, "fp": fp, "fn": fn,
                         "precision": precision, "recall": recall, "f1": f1}
    return groups


def compare(expected, predicted):
    """比对单张样本的预测与 GT。

    返回 {ok, field_total, field_matched, mismatched_fields, field_stats,
          cer, cer_dist, cer_chars, item_total, items_with_evidence}
    field_stats 为 {字段名: FieldStat([total, matched, tp, fp, fn])}：
    前两位 [total, matched] 语义冻结（供整集层面精确聚合 per_field_accuracy，与
    历史报告可比）；后三位 tp/fp/fn 为并列新增的 F1 口径（[口径分]，唯一权威
    定义见模块 docstring），accuracy 位口径一字不动。
    """
    exp = normalize(expected)
    pred = normalize(predicted)
    mismatched = []
    field_stats = {}

    def _cmp(field, a, b, equal=None):
        """accuracy 位（旧口径不动，unpaired 行维持旧计错语义）。"""
        stat = field_stats.setdefault(field, FieldStat([0, 0, 0, 0, 0]))
        stat[0] += 1
        ok = (a == b) if equal is None else equal(a, b)
        if ok:
            stat[1] += 1
        else:
            mismatched.append(field)

    def _f1(field, a, b, equal=None):
        """F1 位（TP/FP/FN 见模块 docstring 权威表）；双缺失不计分。"""
        stat = field_stats.setdefault(field, FieldStat([0, 0, 0, 0, 0]))
        e_present, p_present = _field_present(field, a), _field_present(field, b)
        if not e_present and not p_present:
            return
        ok = (a == b) if equal is None else equal(a, b)
        if e_present and p_present:
            if ok:
                stat[2] += 1
            else:
                stat[3] += 1
                stat[4] += 1
        elif e_present:
            stat[4] += 1
        else:
            stat[3] += 1

    for field in TOP_FIELDS:
        equal = _vendor_equal if field == "vendor" else None
        _cmp(field, exp.get(field), pred.get(field), equal)
        _f1(field, exp.get(field), pred.get(field), equal)

    # v2（合并反馈①）：payment_marked 必须——GT 有该字段即比对（识别契约必含，
    # 缺失即「已付款印章漏识别/多识别」，直接计错）。F1 实例同口径：GT 非 None
    # 才产生实例（GT 未知 = None 时不反推 FP）。
    if exp.get("payment_marked") is not None:
        _cmp("payment_marked", exp.get("payment_marked"), pred.get("payment_marked"))
        _f1("payment_marked", exp.get("payment_marked"), pred.get("payment_marked"))
    # 币种：GT 声明了币种才比对（识别侧恒有，默认 HKD）
    if exp.get("currency"):
        _cmp("currency", exp.get("currency"), pred.get("currency"))
        _f1("currency", exp.get("currency"), pred.get("currency"))
    # 费用与注记按「GT 与识别双有」原则：GT 非零/非空才比对——
    # 恒 0 字段双边天然相等，计入只稀释分数，不提供区分度
    exp_fees = exp.get("fees") or {}
    pred_fees = pred.get("fees") or {}
    for key in GT_V2_FEE_FIELDS:
        ev = exp_fees.get(key)
        if ev is not None and ev != 0:
            _cmp(key, ev, pred_fees.get(key))
            _f1(key, ev, pred_fees.get(key))
    if exp.get("adjustment_notes"):
        _cmp("adjustment_notes", exp.get("adjustment_notes"), pred.get("adjustment_notes"))
        _f1("adjustment_notes", exp.get("adjustment_notes"), pred.get("adjustment_notes"))

    exp_items = exp["items"]
    pred_items = pred["items"]
    _cmp("item_count", len(exp_items), len(pred_items))

    dist = 0
    chars = 0
    for e, p in _align_items(exp_items, pred_items):
        for field in ITEM_FIELDS:
            fname = "item.%s" % field
            ev_ = (e or {}).get(field)
            pv_ = (p or {}).get(field)
            # accuracy 位：unit 从 a==b 改为 _units_equal（[口径分] ③），其余字段
            # 判等逻辑一字不动；unpaired 行维持旧计错语义（None==None 计 match）
            equal = _units_equal if field == "unit" else None
            _cmp(fname, ev_, pv_, equal)
            # F1 位：未配对行按权威表只计 FN/FP（e 侧 present→FN，p 侧 present→FP）
            if e is None:
                if _field_present(fname, pv_):
                    field_stats.setdefault(fname, FieldStat([0, 0, 0, 0, 0]))[3] += 1
            elif p is None:
                if _field_present(fname, ev_):
                    field_stats.setdefault(fname, FieldStat([0, 0, 0, 0, 0]))[4] += 1
            else:
                _f1(fname, ev_, pv_, equal)
        # CER 统计文本转录质量：品名逐行累加编辑距离
        e_name = (e or {}).get("name", "") or ""
        p_name = (p or {}).get("name", "") or ""
        dist += _levenshtein(e_name, p_name)
        chars += len(e_name)
    # 供应商名同样计入 CER
    dist += _levenshtein(exp.get("vendor", ""), pred.get("vendor", ""))
    chars += len(exp.get("vendor", ""))

    total = sum(v[0] for v in field_stats.values())
    matched = sum(v[1] for v in field_stats.values())
    return {
        "ok": not mismatched,
        "field_total": total,
        "field_matched": matched,
        "mismatched_fields": mismatched,
        "field_stats": field_stats,
        "cer": round(dist / float(chars), 4) if chars else 0.0,
        "cer_dist": dist,
        "cer_chars": chars,
        "item_total": len(pred_items),
        "items_with_evidence": sum(1 for it in pred_items if _evidence_of(it)),
    }


# ------------------------------------------------------------------
# manifest / GT 读取
# ------------------------------------------------------------------
def load_manifest(evalset_dir):
    path = os.path.join(evalset_dir, "manifest.csv")
    if not os.path.exists(path):
        raise SystemExit(
            "评测集 manifest 不存在：%s\n"
            "请先运行 build_evalset.py 构建（语料属业务数据，不入库）" % path)
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _expected_path(evalset_dir, sample_id):
    return os.path.join(evalset_dir, "expected", sample_id + ".json")


def _load_expected(evalset_dir, sample_id):
    path = _expected_path(evalset_dir, sample_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ------------------------------------------------------------------
# 预测器
# ------------------------------------------------------------------
def _stub_predictor(evalset_dir):
    """harness 自检用：直接回读 GT 作为预测，验证比对与聚合通路（离线、零成本）。"""
    def _predict(image_path, sample_id):
        return _load_expected(evalset_dir, sample_id), {
            "latency_ms": 0.0,
            "tokens": 0,
            "error": None if _load_expected(evalset_dir, sample_id) else "GT 缺失",
        }
    return _predict


def _real_predictor(engine, model, prompt_version):
    """真实识别链路：按 prompt_version 换 SYSTEM_PROMPT，按 engine/model 建模型。"""
    from app.chains import extract_chain
    from app.models import EngineConfig, EngineKind
    from app.prompts import get_prompt

    cfg = EngineConfig()
    if engine:
        try:
            cfg.recognition_engine = EngineKind(str(engine).lower())
        except ValueError:
            raise SystemExit("未知引擎：%s（可选：%s）"
                             % (engine, ", ".join(e.value for e in EngineKind)))
    if model:
        cfg.recognition_model = model
    # 单事实源（T12）：app.prompts 为 ai_registry 的 re-export 委托层，
    # 显式版本号直接落在 ai_registry/prompts/extract/<version>.py
    extract_chain.SYSTEM_PROMPT = get_prompt("extract", prompt_version)

    def _predict(image_path, sample_id):
        res = extract_chain.extract_receipt(image_path, config=cfg)
        data = res.get("data")
        pred = data.model_dump() if data is not None else None
        return pred, {
            "latency_ms": float(res.get("extract_ms") or res.get("elapsed_ms") or 0.0),
            "tokens": int((res.get("token_usage") or {}).get("total_tokens", 0) or 0),
            "error": res.get("error"),
        }
    return _predict


def _set_memory_enabled(enabled):
    """评测进程内开关 VendorMemory 注入：关闭时识别链路的所有记忆检索一律返回空。

    只改本进程内的模块属性，不碰数据库与设置表；返回还原函数。
    """
    from app.chains import extract_chain, supervisor
    saved = (extract_chain.retrieve_context, supervisor.retrieve_context,
             supervisor.first_round_inject_enabled)
    if not enabled:
        extract_chain.retrieve_context = lambda *a, **k: ""
        supervisor.retrieve_context = lambda *a, **k: ""
        supervisor.first_round_inject_enabled = lambda: False

    def _restore():
        (extract_chain.retrieve_context, supervisor.retrieve_context,
         supervisor.first_round_inject_enabled) = saved
    return _restore


def _pipeline_predictor(engine, model, prompt_version, memory=True):
    """完整流程模式：走线上同一条 supervisor.run_pipeline（门禁、记忆注入、部署配置的
    max_tokens / temperature / image_detail、解析与修正腿、交叉审核），而不是只测单次抽取。

    引擎配置读部署配置（db.get_engine_config，读不到才退回默认），再按 CLI 参数覆盖
    识别引擎/模型；记忆开关见 _set_memory_enabled，用于 A/B 度量记忆的收益。
    """
    from app import db
    from app.chains import extract_chain, supervisor
    from app.models import EngineKind
    from app.prompts import get_prompt

    try:
        cfg = db.get_engine_config()
    except Exception as e:  # noqa: BLE001
        print("!! 读取部署引擎配置失败，退回默认配置：%s" % e)
        from app.models import EngineConfig
        cfg = EngineConfig()
    if engine:
        try:
            cfg.recognition_engine = EngineKind(str(engine).lower())
        except ValueError:
            raise SystemExit("未知引擎：%s" % engine)
    if model:
        cfg.recognition_model = model
    extract_chain.SYSTEM_PROMPT = get_prompt("extract", prompt_version)
    _set_memory_enabled(bool(memory))

    def _predict(image_path, sample_id):
        state = supervisor.run_pipeline(image_path, config=cfg)
        data = state.get("data")
        pred = data.model_dump() if data is not None else None
        return pred, {
            "latency_ms": float(state.get("total_ms") or 0.0),
            "tokens": int(state.get("tokens_total", 0) or 0),
            "error": state.get("user_error") or state.get("last_error") or None,
            "pipeline_status": state.get("status"),
            "gate_warnings": len(state.get("gate_warnings") or []),
            "retry_count": state.get("retry_count", 0),
            "cost_hkd": state.get("cost_hkd", 0.0),
            "memory": "on" if memory else "off",
        }
    return _predict


# ------------------------------------------------------------------
# 主流程
# ------------------------------------------------------------------
def _baseline_sha256(payload):
    """基线文件 sha256：对不含 sha256 键的 canonical JSON（ensure_ascii=False、
    sort_keys=True、indent=2）的 UTF-8 字节计算。"""
    body = {k: v for k, v in payload.items() if k != "sha256"}
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _count_gt_items(gt):
    """GT 明细分母计数（收尾波次④c）：freeze_eval_baseline.count_gt_items 的消费别名。

    why 不本地再写一份：冻结侧与 run 侧的计数口径必须**逐字节同源**（同一根因一份
    实现），否则显式比对会因两套实现漂移出假差异。惰性 import 同目录模块（scripts
    已在 sys.path，与上方 import eval_norm 同机制）。
    """
    from freeze_eval_baseline import count_gt_items
    return count_gt_items(gt)


def _denominator_mismatches(b_denoms, actual_denoms):
    """冻结件 denominators 与 run 侧实际分母的逐键差异（④c）。

    返回 [{"key", "baseline", "actual"}]；空列表 = 逐键一致。两键集合取并：
    冻结件多出的键（baseline 独有）与 run 侧多出的键都是漂移，不得静默忽略。
    """
    out = []
    for k in sorted(set(b_denoms) | set(actual_denoms)):
        bv, av = b_denoms.get(k), actual_denoms.get(k)
        if bv != av:
            out.append({"key": k, "baseline": bv, "actual": av})
    return out


def _format_denominator_mismatches(mismatches):
    """逐键差异的一行文本（stdout 警告 / strict 报文共用，口径单一）。"""
    return ", ".join("%s baseline=%s actual=%s" % (m["key"], m["baseline"], m["actual"])
                     for m in mismatches)


def run_eval(split, prompt_version=DEFAULT_PROMPT_VERSION, engine="openai", model=None,
             limit=None, evalset_dir=None, report_dir=None, predictor=None,
             require_confirmed=None, baseline=None, baseline_strict=False,
             pipeline=False, memory=True, backup_assets=False):
    """跑一个 split 的评测，返回 EvalReport（dict）。

    pipeline（S2-27）：True 时走完整线上流程 supervisor.run_pipeline（含门禁、记忆注入、
    部署配置的生成参数），默认 False 保持「单次抽取 + 后处理」的历史口径；memory 控制完整流程模式
    下是否启用 VendorMemory 注入（A/B 度量记忆收益用）。backup_assets：出分前先把 GT 与
    基线备份一份（见 eval_assets_backup.py），防语料被批量重写后无从恢复。

    predictor（可选）：自定义预测器 `(image_path, sample_id) -> (payload|None, meta)`，
    供测试注入故障或离线自检；不传则按 engine 构建真实识别链路。

    require_confirmed（T4 + L2 收口）：None 时按 split 取默认——test split 默认 True
    （强制 GT 人工确认门禁，生产准入口径），val/train 默认 False（调参观测用，
    向后兼容）。显式传 True/False 覆盖默认；test split 显式传 False（CLI
    --no-require-confirmed）时报告顶层 gt_status 标记为 "partial"（含未经人工
    抽检确认的 GT，分数不得作为生产基线），CLI 侧同时在 stdout 大字警告。
    为 True 时，该 split 存在任何未 confirmed 的 GT（missing/draft）即拒绝出分
    （SystemExit），保证报告只由人工抽检确认过的 GT 产生。

    baseline（可选，工作项④）：冻结周基线分母文件路径（freeze_eval_baseline.py
    产出）。传入时报告新增 baseline_file / baseline_sha256 / baseline_match /
    baseline_missing_samples / baseline_extra_samples 键：冻结文件 sha256 自校验
    不符（被篡改）→ stdout 警告且 match=false；本次实际读到 GT 的 sample 集合与
    冻结 sample_ids 的差集任一非空 → match=false。baseline_strict=True 时
    match=false 直接 SystemExit（周报流水线门禁用），默认只警告（向后兼容）。
    """
    evalset_dir = os.path.abspath(evalset_dir or DEFAULT_EVALSET_DIR)
    report_dir = os.path.abspath(report_dir or DEFAULT_REPORT_DIR)

    # L2 门禁默认值：test split 生产准入口径强制人工确认；val/train 维持默认关。
    # None（未显式指定）→ 按 split 取默认；显式 True/False 覆盖默认。
    gate_default_enforced = (split == "test")
    if require_confirmed is None:
        require_confirmed = gate_default_enforced
    # test split 显式关闭门禁 → 报告标记 partial（不可作为生产基线分数）
    gt_gate_partial = gate_default_enforced and not require_confirmed

    rows = [r for r in load_manifest(evalset_dir) if r.get("split") == split]
    if not rows:
        raise SystemExit("manifest 中没有 split=%s 的样本" % split)
    if limit:
        rows = rows[:int(limit)]

    if require_confirmed:
        unconfirmed = [r for r in rows
                       if (r.get("gt_status") or "missing") != "confirmed"
                       or _load_expected(evalset_dir, r["sample_id"]) is None]
        if unconfirmed:
            by_status = {}
            for r in unconfirmed:
                s = r.get("gt_status") or "missing"
                by_status[s] = by_status.get(s, 0) + 1
            raise SystemExit(
                "GT 未全部人工确认，--require-confirmed 拒绝出分：split=%s 共 %d 张未确认"
                "（%s），例如 %s。请先在 GT 抽检台（/evalset）逐张确认后再出分。"
                % (split, len(unconfirmed),
                   ", ".join("%s=%d" % kv for kv in sorted(by_status.items())),
                   ", ".join(r["sample_id"] for r in unconfirmed[:5])))

    if backup_assets and engine != "stub":
        try:
            from eval_assets_backup import backup_eval_assets
            info = backup_eval_assets(evalset_dir=evalset_dir)
            print("评测资产已备份 → %s（%d 个文件）" % (info["dir"], info["files"]))
        except Exception as e:  # noqa: BLE001 备份失败要大声说，但不挡评测
            print("!! 评测资产备份失败：%s" % e)

    if predictor is None:
        if engine == "stub":
            predictor = _stub_predictor(evalset_dir)
        elif pipeline:
            predictor = _pipeline_predictor(engine, model, prompt_version, memory=memory)
        else:
            predictor = _real_predictor(engine, model, prompt_version)

    per_sample = []
    field_total = field_matched = 0
    cer_dist = cer_chars = 0
    items_total = items_with_evidence = 0
    field_stats = {}
    latencies, tokens_list = [], []
    gt_status_breakdown = {}
    scored = ok_count = error_count = 0
    scored_sample_ids = []   # 成功读到 GT 的样本（基线分母比对用，工作项④）
    # run 侧 GT 明细计数（收尾波次④c）：与冻结件 denominators 显式逐键比对用。
    # 只对 readable GT 计数 —— 与 freeze 的口径一致（缺 GT 样本计 samples 不计 items_gt）。
    run_items_gt = 0
    run_unit_present_gt = 0

    for row in rows:
        sample_id = row["sample_id"]
        gt_status = row.get("gt_status", "missing") or "missing"
        expected = _load_expected(evalset_dir, sample_id)
        if expected is None:
            gt_status = "missing"
        else:
            scored_sample_ids.append(sample_id)
            _n_items, _n_unit = _count_gt_items(expected)
            run_items_gt += _n_items
            run_unit_present_gt += _n_unit
        gt_status_breakdown[gt_status] = gt_status_breakdown.get(gt_status, 0) + 1

        record = {
            "sample_id": sample_id,
            "doc_form": row.get("doc_form", ""),
            "gt_status": gt_status,
            "ok": False,
            "mismatched_fields": [],
            "latency_ms": 0.0,
            "tokens": 0,
            "error": "",
        }
        if expected is None:
            record["error"] = "GT 缺失（未生成或未确认）"
            per_sample.append(record)
            continue

        image_path = os.path.join(evalset_dir, row.get("image", ""))
        t0 = time.time()
        try:
            pred, meta = predictor(image_path, sample_id)
            elapsed_ms = float(meta.get("latency_ms") or 0.0) or \
                round((time.time() - t0) * 1000, 1)
            error = meta.get("error") or ("" if pred is not None else "抽取返回空")
        except Exception as e:
            pred, elapsed_ms, tokens, error = None, round((time.time() - t0) * 1000, 1), 0, str(e)[:200]
        else:
            tokens = int(meta.get("tokens") or 0)

        latencies.append(elapsed_ms)
        tokens_list.append(tokens)
        record["latency_ms"] = elapsed_ms
        record["tokens"] = tokens
        record["error"] = error or ""
        if pipeline and isinstance(locals().get("meta"), dict):
            # 完整流程模式：把线上状态、警告数、重试数、成本一并记进样本，便于对比线上口径
            for key in ("pipeline_status", "gate_warnings", "retry_count", "cost_hkd"):
                if key in meta:
                    record[key] = meta[key]

        if pred is None:
            error_count += 1
            per_sample.append(record)
            continue

        scored += 1
        res = compare(expected, pred)
        field_total += res["field_total"]
        field_matched += res["field_matched"]
        items_total += res["item_total"]
        items_with_evidence += res["items_with_evidence"]
        cer_dist += res["cer_dist"]
        cer_chars += res["cer_chars"]
        for name, stat in res["field_stats"].items():
            agg = field_stats.setdefault(name, [0, 0, 0, 0, 0])
            for k in range(5):
                agg[k] += stat[k]
        if res["ok"]:
            ok_count += 1
        record["ok"] = res["ok"]
        record["mismatched_fields"] = res["mismatched_fields"]
        per_sample.append(record)

    per_field_accuracy = {
        name: round(stat[1] / float(stat[0]), 4)
        for name, stat in sorted(field_stats.items()) if stat[0]
    }
    # F1 并列新增口径（[口径分]）：整集 tp/fp/fn 累加后算 P/R/F1（micro，禁 macro）
    per_field_f1 = _per_field_f1_from_stats(field_stats)
    field_f1_groups = _field_f1_groups(field_stats)

    report = {
        "split": split,
        "prompt_version": prompt_version,
        "engine": engine,
        "model": model or "",
        "mode": "pipeline" if pipeline and engine != "stub" else "extract_only",
        "memory": ("on" if memory else "off") if pipeline and engine != "stub" else "n/a",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "n_samples": len(rows),
        "n_scored": scored,
        "errors": error_count,
        "accuracy": round(ok_count / float(scored), 4) if scored else 0.0,
        "cer": round(cer_dist / float(cer_chars), 4) if cer_chars else 0.0,
        "per_field_accuracy": per_field_accuracy,
        "per_field_f1": per_field_f1,
        "field_f1_groups": field_f1_groups,
        "edit_proxy": round((field_total - field_matched) / float(scored), 4) if scored else 0.0,
        "evidence_coverage": round(items_with_evidence / float(items_total), 4) if items_total else 0.0,
        "avg_tokens": round(statistics.mean(tokens_list), 1) if tokens_list else 0.0,
        "p50_latency_ms": round(statistics.median(latencies), 1) if latencies else 0.0,
        "gt_status_breakdown": gt_status_breakdown,
        "sample_ids": [r["sample_id"] for r in rows],
        "per_sample": per_sample,
    }
    if gt_gate_partial:
        # L2：test split 显式 --no-require-confirmed 时带 partial 标记，
        # 下游与评审一眼识别「含未确认 GT 的分数」
        report["gt_status"] = "partial"

    # ④c：分母显式比对的报告键 —— baseline 未传时恒 None（消费方按 None 判"无基线"）
    report["baseline_denominators_baseline"] = None
    report["baseline_denominators_actual"] = None

    if baseline:
        # 工作项④：冻结周基线分母消费（sha256 自校验 + sample 集合差集）
        # ④c 追加：denominators 数值显式逐键比对 —— sha256 只保护文件完整性，
        # 篡改分母并重算 sha 仍能过自校验（G3 场景的分母版），必须数值比对兜底。
        bpath = os.path.abspath(baseline)
        with open(bpath, encoding="utf-8") as f:
            bpayload = json.load(f)
        declared_sha = bpayload.get("sha256")
        calc_sha = _baseline_sha256(bpayload)
        b_ids = list(bpayload.get("sample_ids") or [])
        b_set, a_set = set(b_ids), set(scored_sample_ids)
        missing = [s for s in b_ids if s not in a_set]
        extra = [s for s in scored_sample_ids if s not in b_set]
        # run 侧四值与冻结件 denominators 同源同键（samples=len(rows)，与 freeze
        # len(sample_ids) 同源；confirmed/draft 来自 gt_status_breakdown；GT 明细
        # 计数经 count_gt_items 唯一实现累计）
        actual_denoms = {
            "samples": len(rows),
            "confirmed": gt_status_breakdown.get("confirmed", 0),
            "draft": gt_status_breakdown.get("draft", 0),
            "items_gt": run_items_gt,
            "unit_present_gt": run_unit_present_gt,
        }
        b_denoms = bpayload.get("denominators") or {}
        denom_mismatches = _denominator_mismatches(b_denoms, actual_denoms)
        match = ((declared_sha == calc_sha) and not missing and not extra
                 and not denom_mismatches)
        if declared_sha != calc_sha:
            print("!! 警告：基线文件被篡改（sha256 自校验失败）：%s" % bpath)
        if denom_mismatches:
            print("!! 警告：基线分母不一致（显式比对）：%s"
                  % _format_denominator_mismatches(denom_mismatches))
        report["baseline_file"] = bpath
        report["baseline_sha256"] = declared_sha
        report["baseline_match"] = match
        report["baseline_missing_samples"] = missing
        report["baseline_extra_samples"] = extra
        report["baseline_denominators_baseline"] = b_denoms
        report["baseline_denominators_actual"] = actual_denoms
        report["baseline_denominator_mismatches"] = denom_mismatches
        if baseline_strict and not match:
            if denom_mismatches:
                raise SystemExit(
                    "基线分母不一致（--baseline-strict 拒绝出分）：%s（%s）"
                    % (bpath, _format_denominator_mismatches(denom_mismatches)))
            raise SystemExit(
                "基线分母校验失败（--baseline-strict 拒绝出分）：%s"
                "（missing=%d extra=%d sha_ok=%s）"
                % (bpath, len(missing), len(extra), declared_sha == calc_sha))

    os.makedirs(report_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(report_dir, "%s_%s_%s.json" % (ts, split, prompt_version))
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    report["report_path"] = out_path
    return report


def _print_summary(report):
    print("=" * 68)
    print("EvalReport | split=%s | prompt=%s | engine=%s | model=%s"
          % (report["split"], report["prompt_version"], report["engine"],
             report["model"] or "-"))
    print("-" * 68)
    print("样本 %d（有效比对 %d，失败 %d）" %
          (report["n_samples"], report["n_scored"], report["errors"]))
    print("  accuracy          %.4f   （整单零编辑率）" % report["accuracy"])
    print("  cer               %.4f" % report["cer"])
    print("  edit_proxy        %.4f   （平均每单需修改字段数）" % report["edit_proxy"])
    print("  evidence_coverage %.4f" % report["evidence_coverage"])
    print("  avg_tokens        %.1f" % report["avg_tokens"])
    print("  p50_latency_ms    %.1f" % report["p50_latency_ms"])
    print("  gt_status         %s" % report["gt_status_breakdown"])
    print("  per_field         %s" % json.dumps(report["per_field_accuracy"], ensure_ascii=False))
    # [口径分] F1 为并列新增口径（micro 聚合），与 accuracy 分列陈述，不得混用
    print("  per_field_f1      %s   （micro，[口径分]）"
          % json.dumps(report.get("per_field_f1", {}), ensure_ascii=False, sort_keys=True))
    for gname in ("high_freq", "line_item"):
        g = (report.get("field_f1_groups") or {}).get(gname) or {}
        print("  %-12s F1=%.4f (P=%.4f R=%.4f tp=%d fp=%d fn=%d)   [口径分·两口径不得互相搬家]"
              % (gname, g.get("f1", 0.0), g.get("precision", 0.0), g.get("recall", 0.0),
                 g.get("tp", 0), g.get("fp", 0), g.get("fn", 0)))
    if "baseline_match" in report:
        print("  baseline          %s (sha=%s, missing=%d, extra=%d)"
              % (report["baseline_match"], (report.get("baseline_sha256") or "")[:12],
                 len(report.get("baseline_missing_samples") or []),
                 len(report.get("baseline_extra_samples") or [])))
    print("report → %s" % report["report_path"])
    print("=" * 68)


def _run_memory_ab(common):
    """记忆开/关各跑一遍同一批样本，打印并返回差值（记忆的收益由此度量）。"""
    reports = {}
    for flag in ("on", "off"):
        reports[flag] = run_eval(pipeline=True, memory=(flag == "on"), **dict(common, backup_assets=(flag == "on" and common.get("backup_assets", True))))
    on, off = reports["on"], reports["off"]
    delta = {k: round(on[k] - off[k], 4) for k in ("accuracy", "cer", "edit_proxy", "avg_tokens")}
    out = {"memory_on": {k: on[k] for k in ("accuracy", "cer", "edit_proxy", "avg_tokens", "n_scored")},
           "memory_off": {k: off[k] for k in ("accuracy", "cer", "edit_proxy", "avg_tokens", "n_scored")},
           "delta_on_minus_off": delta}
    print("=" * 60)
    print("记忆 A/B（同一批样本）：")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    report_dir = os.path.abspath(common.get("report_dir") or DEFAULT_REPORT_DIR)
    os.makedirs(report_dir, exist_ok=True)
    path = os.path.join(report_dir, "memory_ab_%s_%s.json" % (
        common["split"], datetime.now().strftime("%Y%m%d_%H%M%S")))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("A/B 结果已写入 %s" % path)
    return 0


def main():
    ap = argparse.ArgumentParser(description="可复现评测 harness（train/val/test 三分法）")
    ap.add_argument("--split", required=True, choices=["train", "val", "test"], help="评测集分片")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT_VERSION, help="extract prompt 版本")
    ap.add_argument("--engine", default="openai",
                    help="识别引擎通道：openai / stub（harness 自检，不出分）。"
                         "历史注记：本机 CLI 引擎 opencode / codebuddy 已于 2026-09-02 弃用删除")
    ap.add_argument("--model", default=None, help="识别模型名（覆盖引擎默认）")
    ap.add_argument("--limit", type=int, default=None, help="只跑前 N 张（按 manifest 顺序）")
    ap.add_argument("--evalset-dir", default=None, help="评测集目录（默认 demo/evalsets）")
    ap.add_argument("--report-dir", default=None,
                    help="报告落盘目录（默认 ai_registry/benchmarks/eval_runs）")
    ap.add_argument("--require-confirmed", action=argparse.BooleanOptionalAction,
                    default=None,
                    help="GT 门禁：split 内存在未人工确认（missing/draft）的 GT 时拒绝出分。"
                         "test split 默认强制（生产准入），可用 --no-require-confirmed 显式关闭"
                         "（报告带 gt_status=partial 标记并大字警告）；val/train 默认关闭，"
                         "需显式 --require-confirmed 才开启")
    ap.add_argument("--baseline", default=None,
                    help="冻结周基线分母文件（freeze_eval_baseline.py 产出）；传入后报告带"
                         " baseline_match 等键，sample 集合与冻结分母不一致即 match=false")
    ap.add_argument("--baseline-strict", action="store_true", default=False,
                    help="baseline_match=false 时直接 SystemExit（周报流水线门禁用）；"
                         "默认只警告（向后兼容）")
    ap.add_argument("--pipeline", action="store_true", default=False,
                    help="完整流程模式：走线上 supervisor.run_pipeline（门禁/记忆/部署配置），"
                         "不加则只测单次抽取")
    ap.add_argument("--memory", choices=["on", "off"], default="on",
                    help="完整流程模式下是否启用 VendorMemory 注入（默认 on）")
    ap.add_argument("--memory-ab", action="store_true", default=False,
                    help="记忆 A/B：同一批样本各跑一次 memory=on 与 memory=off，输出差值（隐含 --pipeline）")
    ap.add_argument("--no-backup", action="store_true", default=False,
                    help="出分前不备份 GT 与基线（默认会备份）")
    args = ap.parse_args()

    # 离线评测进程：允许 OPENAI_REC_CALL_TIMEOUT 抬高单次调用超时（线上进程会忽略它）
    os.environ.setdefault("RECEIPT_OFFLINE_EVAL", "1")
    common = dict(split=args.split, prompt_version=args.prompt, engine=args.engine,
                  model=args.model, limit=args.limit,
                  evalset_dir=args.evalset_dir, report_dir=args.report_dir,
                  require_confirmed=args.require_confirmed,
                  baseline=args.baseline, baseline_strict=args.baseline_strict,
                  backup_assets=not args.no_backup)
    if args.memory_ab:
        return _run_memory_ab(common)
    report = run_eval(pipeline=args.pipeline, memory=(args.memory == "on"), **common)
    if report.get("gt_status") == "partial":
        # L2 大字警告：test split 被显式关闭 GT 确认门禁，分数仅作调参参考
        print("!" * 68)
        print("!! 警告：本次运行显式关闭了 GT 确认门禁（--no-require-confirmed）！！")
        print("!! 报告 gt_status 已标记为 partial —— 含未经人工抽检确认的 GT，")
        print("!! 该分数不得作为生产基线或对外汇报口径。请先在 GT 抽检台逐张确认。")
        print("!" * 68)
    _print_summary(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
