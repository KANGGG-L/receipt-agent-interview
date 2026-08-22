# -*- coding: utf-8 -*-
"""U-06 一次性清理：占位供应商软停用 + 归一变体合并。

1. 占位名（如「（看不清）」「（字迹模糊）」「（字跡模糊無法辨識）」）匹配
   ^[（(]?[字看不清无法辨认跡模灣糊無法辨識]+[）)]?$ 的供应商 → active=0（收据保留）；
2. 归一 key 相同的变体供应商（繁简/英文括号注）→ 保留 id 最小（最早创建）者为主档，
   其余收据 supplier_name 改指主档名并 active=0。

幂等，可重复执行。执行前后打印供应商对照表。
用法：PYTHONPATH=.:demo python demo/scripts/cleanup_placeholder_suppliers.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db
from app.services.supplier_normalizer import canonical_supplier_key

_PLACEHOLDER_RE = re.compile(r"^[\s（(）)]*[字看不清无法辨认跡模糊無法辨識]{1,12}[\s（(）)]*$")


def snapshot():
    s = db.get_session()
    try:
        return [(r.id, r.name, r.active) for r in s.query(db._SupplierRow).all()]
    finally:
        s.close()


def main():
    before = snapshot()
    print("== 清理前供应商对照 ==")
    for sid, name, active in sorted(before):
        print(f"  [{sid}] active={active}  {name}")

    s = db.get_session()
    try:
        # 1) 占位名软停用
        disabled = []
        for r in s.query(db._SupplierRow).filter(db._SupplierRow.active == 1).all():
            if _PLACEHOLDER_RE.match(r.name or ""):
                r.active = 0
                disabled.append((r.id, r.name))
        s.commit()

        # 2) 归一变体合并：同 key 保留最小 id 为主档
        groups = {}
        for r in s.query(db._SupplierRow).all():
            key = canonical_supplier_key(r.name)
            if key:
                groups.setdefault(key, []).append(r)
        merged = []
        for key, rows in groups.items():
            if len(rows) < 2:
                continue
            rows.sort(key=lambda x: x.id)
            keep = rows[0]
            for drop in rows[1:]:
                for rec in s.query(db._ReceiptRow).filter(db._ReceiptRow.supplier_name == drop.name).all():
                    rec.supplier_name = keep.name
                if drop.active == 1:
                    drop.active = 0
                merged.append((drop.id, drop.name, keep.id, keep.name))
        s.commit()
    finally:
        s.close()

    print("\n== 执行动作 ==")
    if disabled:
        for sid, name in disabled:
            print(f"  软停用占位供应商 [{sid}] {name}")
    else:
        print("  无占位供应商需要软停用")
    if merged:
        for did, dname, kid, kname in merged:
            print(f"  合并变体 [{did}] {dname} -> 主档 [{kid}] {kname}")
    else:
        print("  无归一变体需要合并")

    after = snapshot()
    print("\n== 清理后供应商对照（active=0 已从下拉隐藏）==")
    for sid, name, active in sorted(after):
        print(f"  [{sid}] active={active}  {name}")


if __name__ == "__main__":
    main()
