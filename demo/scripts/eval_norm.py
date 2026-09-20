# -*- coding: utf-8 -*-
"""评测/GT 两侧共用的字段归一（单一实现，防口径分叉）。

why: 同一份模型输出在**写入侧**（`gen_gt_candidates._coerce_bool`，用于生成 GT 真值）
与**评分侧**（`run_eval._norm_payment_marked`，用于比对打分的归一）各有一份实现，
且口径已经分叉：

- 生成侧接受 `"y"/"n"`，把 `""` 映射为 **False**；
- 评分侧不接受 `y/n`（`"y"` 落到 `return None`），`""` 归 **None**（不参与比对）。

后果有两类：① 预测里出现 `"Y"` 时评分侧判为「不可判」直接跳过比对 ——
`payment_marked` 实际上永远没被比过，指标静默缺失；② 候选里 `payment_marked` 留空/不可判
时写入侧折成 False，等于**凭空造出「未付款」的真值**，再拿它去给预测打分。

口径（本模块统一，`None` = 未知）：
- 显式真值/假值字面量（含 y/n、已付款/未付款、paid/unpaid、none）→ True/False；
- `""`、缺失、无法判读 → `None`。

为什么「未知」用 `None` 而不是 False：该字段会成为 GT 真值参与打分（`run_eval.compare`
只要 GT 有该字段就比对），把「模型没说」写成「未付款」是伪造负标签；而 `None` 在评分侧
已有明确语义 = 不参与比对。GT 落盘契约（`validate_gt` / `api_evalset`）要求布尔，
因此生成侧遇到 `None` 时**不折算**，交给既有校验显式失败（该候选不进语料），
不要再折成 False 把校验「喂饱」。
"""

TRUE_LITERALS = ("true", "1", "yes", "y", "paid", "已付款")
FALSE_LITERALS = ("false", "0", "no", "n", "unpaid", "未付款", "none")


def coerce_payment_marked(v):
    """付款标记归一：bool/0/1/常见字面量 → bool；缺失或不可判 → None（未知）。

    两侧（GT 生成 / 评测比对）必须共用本函数，避免再次分叉。
    """
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and v in (0, 1):
        return bool(v)
    s = str(v or "").strip().lower()
    if s in TRUE_LITERALS:
        return True
    if s in FALSE_LITERALS:
        return False
    return None
