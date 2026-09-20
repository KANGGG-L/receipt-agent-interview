# -*- coding: utf-8 -*-
"""一次性数据修复：把只写在 receipts.items_json、明细表为空的单据补回 receipt_items。

背景：评测集建单分支早期只把 GT 明细写进 receipts.items_json，没写 receipt_items 表；
而详情/复核/导出全仓只读 receipt_items，导致这批单据（实测 1455-1486 共 32 条）明细长期为空。
写入端已收敛为只写明细表（见 services/evalset_linkage.py），本脚本修复存量。

判据（可复算）：
    select id from receipts r
     where length(coalesce(items_json, '')) > 2
       and (select count(*) from receipt_items ri where ri.receipt_id = r.id) = 0;

幂等：只处理「明细表为空」的单据，已回填的不会再动，重复执行零写入。
用法：
    PYTHONPATH=.:demo python demo/scripts/backfill_receipt_items_from_json.py            # 预演，不写库
    PYTHONPATH=.:demo python demo/scripts/backfill_receipt_items_from_json.py --apply    # 实际写库
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db
from app.services.evalset_linkage import gt_items_for_receipt_table


def _target_rids(session):
    """返回 (rid, tenant_id, items_json) 三元组列表：有 items_json 但明细表为空。"""
    out = []
    for r in session.query(db._ReceiptRow).all():
        raw = getattr(r, "items_json", None)
        if not (isinstance(raw, str) and raw.strip() and raw.strip() != "[]"):
            continue
        n = session.query(db._ItemRow).filter(db._ItemRow.receipt_id == r.id).count()
        if n == 0:
            out.append((r.id, getattr(r, "tenant_id", None), raw))
    out.sort(key=lambda x: x[0])
    return out


def main():
    ap = argparse.ArgumentParser(description="items_json -> receipt_items 存量回填")
    ap.add_argument("--apply", action="store_true", help="实际写库（缺省仅预演）")
    args = ap.parse_args()

    print(f"目标库：{db.DB_PATH}")
    s = db.get_session()
    try:
        targets = _target_rids(s)
    finally:
        s.close()

    print(f"待回填单据：{len(targets)} 条")
    plan = []
    for rid, tenant_id, raw in targets:
        try:
            items = json.loads(raw)
        except (TypeError, ValueError):
            print(f"  [跳过] rid={rid} items_json 非合法 JSON")
            continue
        rows = gt_items_for_receipt_table(items)
        if not rows:
            print(f"  [跳过] rid={rid} items_json 解析后无有效条目")
            continue
        plan.append((rid, tenant_id, rows))
        print(f"  rid={rid} 待写入明细 {len(rows)} 条："
              + "、".join(str(r.get("name") or "") for r in rows[:3])
              + ("…" if len(rows) > 3 else ""))

    total_items = sum(len(rows) for _, _, rows in plan)
    print(f"合计待写入明细行：{total_items} 条")

    if not args.apply:
        print("\n预演结束（未写库）。加 --apply 实际执行。")
        return

    written = 0
    for rid, tenant_id, rows in plan:
        db.set_receipt_items(rid, rows, tenant_id=tenant_id)
        back = db.get_receipt_items(rid, tenant_id=tenant_id)
        ok = len(back) == len(rows)
        written += len(back)
        print(f"  [{'OK' if ok else '异常'}] rid={rid} 回填 {len(back)}/{len(rows)} 行")
    print(f"\n实际写入明细行：{written} 条")


if __name__ == "__main__":
    main()
