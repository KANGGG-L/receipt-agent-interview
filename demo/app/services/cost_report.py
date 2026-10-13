# -*- coding: utf-8 -*-
"""成本口径：以已入账单据的总额为准，按明细金额比例摊到部门。

三处共用：/api/cost_report、/api/cost_report/items、spend_summary 导出。
口径：
- 一张单据对成本的贡献 = 单据 total_amount（含服务费、税、折扣、运费、押金、抹零之后的应付总额）
  折成港币（入账时留的汇率快照优先，没有就用设置里现在的汇率；仍没有的单独列出，不进合计）
- 作废行不参与分摊；明细金额只当比例，不当金额
- 明细没有部门用单据部门，再没有就是未分配
- 分摊按「分」取整并把尾差补到最大的几份上，所以各部门之和严格等于单据总额
"""

import math
from typing import Dict, List, Optional

from app import db, units_store


def _split_cents(total_cents: int, weights: List[float]) -> Optional[List[int]]:
    sw = sum(weights)
    if not weights or sw == 0:
        return None
    raw = [total_cents * w / sw for w in weights]
    floors = [math.floor(x) for x in raw]
    rest = total_cents - sum(floors)
    order = sorted(range(len(raw)), key=lambda i: -(raw[i] - floors[i]))
    for i in order[:max(rest, 0)]:
        floors[i] += 1
    return floors


def _cents(x) -> int:
    return int(round(float(x or 0.0) * 100))


def allocate_receipt(r, items, rate: float, valid_dept_ids=None) -> dict:
    """把一张单据的总额摊到各部门。

    返回 {"total_hkd","total_orig","parts":[{"dept_id","item","amount_hkd","amount_orig"}]}，
    parts 之和 == total。
    """
    total_orig = round(float(r.total_amount or 0.0), 2)
    total_hkd = round(total_orig * rate, 2)
    live = [it for it in (items or []) if not it.get("is_void")]

    def dept_of(it):
        d = it.get("cost_center_id") if it is not None else None
        if d is None:
            d = r.department_id
        if valid_dept_ids is not None and d not in valid_dept_ids:
            return None
        return d

    weights = [float(it.get("amount") or 0.0) for it in live]
    split_h = _split_cents(_cents(total_hkd), weights) if live else None
    split_o = _split_cents(_cents(total_orig), weights) if live else None
    parts = []
    if split_h is None:
        parts.append({"dept_id": dept_of(None), "item": None,
                      "amount_hkd": total_hkd, "amount_orig": total_orig})
    else:
        for it, ch, co in zip(live, split_h, split_o):
            parts.append({"dept_id": dept_of(it), "item": it,
                          "amount_hkd": ch / 100.0, "amount_orig": co / 100.0})
    return {"total_hkd": total_hkd, "total_orig": total_orig, "parts": parts}


def build_allocations(tenant_id, in_cur, in_prev, include_non_approved=False,
                      valid_dept_ids=None) -> dict:
    """返回 {"allocs":[{receipt,is_cur,is_prv,currency,rate,alloc}], "unconverted":[...]}。

    in_cur / in_prev 是 receipt_date -> bool 的函数，只取落在本期或上期的单据；
    明细一次批量取出，不逐单查询。
    """
    rows = db.list_receipt_rows(tenant_id=tenant_id)
    if not include_non_approved:
        rows = [r for r in rows if r.status == "approved"]
    picked = []
    for r in rows:
        d = r.receipt_date or ""
        c, p = in_cur(d), in_prev(d)
        if c or p:
            picked.append((r, c, p))
    items_by = db.get_receipt_items_multi([r.id for r, _, _ in picked], tenant_id=tenant_id)
    snap = units_store.receipt_fx_map(tenant_id)
    rate_map = {x["currency"]: x["rate"] for x in units_store.get_fx_rates(tenant_id)}
    allocs, unconverted = [], []
    for r, c, p in picked:
        cur = units_store.norm_currency(getattr(r, "currency", None))
        rate = units_store.hkd_rate_for_receipt(tenant_id, r.id, cur, snap, rate_map)
        if rate is None:
            unconverted.append({"receipt_id": r.id, "currency": cur,
                                "amount": round(float(r.total_amount or 0.0), 2)})
            continue
        allocs.append({"receipt": r, "is_cur": c, "is_prv": p, "currency": cur,
                       "rate": rate,
                       "alloc": allocate_receipt(r, items_by.get(r.id, []), rate, valid_dept_ids)})
    return {"allocs": allocs, "unconverted": unconverted}


def unconverted_warning(unconverted: List[dict]) -> str:
    if not unconverted:
        return ""
    cur = "、".join(sorted({u["currency"] for u in unconverted}))
    return ("有 %d 张单据是「%s」，设置里没有对应汇率，没有算进合计。请老板到「设置」里填好汇率。"
            % (len(unconverted), cur))
