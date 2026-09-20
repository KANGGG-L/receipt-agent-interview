# -*- coding: utf-8 -*-
"""导入黄金样本到 demo 平台。

把之前人工标注/复核的测试数据（samples/receipts/*.jpg + samples/expected/*.json
+ manifest.csv 的 doc_form/layout_type）导入为 edited 收据，并建供应商/SKU/库存。

用法：
  python scripts/import_golden.py --limit 10          # 导入前 10 张（按文件名排序）
  python scripts/import_golden.py --limit 10 --vendor 祥興   # 只导指定供应商
  python scripts/import_golden.py --dry-run           # 预览不写入
  python scripts/import_golden.py --tenant-id demo2   # 导入到指定租户（P3-5 租户化）

P3-5 租户口径：所有写入（收据/明细/SKU/库存流水/供应商）与去重读取
统一携带 --tenant-id（默认 default），不污染其他租户数据。
"""

import argparse
import csv
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
SAMPLES = os.path.join(os.path.dirname(__file__), "../samples")
RECEIPTS_DIR = SAMPLES
EXPECTED_DIR = SAMPLES
MANIFEST = os.path.join(SAMPLES, "manifest.csv")
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "../uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 演示用 doc_form 映射（manifest.doc_form → demo 枚举）
DOC_FORM_MAP = {
    "delivery_note": "printed_delivery_note",
    "statement": "monthly_statement",
    "thermal_receipt": "thermal",
    "ncr_handwritten": "ncr_handwritten",
}


def load_manifest():
    meta = {}
    if os.path.exists(MANIFEST):
        for r in csv.DictReader(open(MANIFEST, encoding="utf-8")):
            meta[r["filename"]] = r
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10, help="导入张数（默认 10）")
    ap.add_argument("--vendor", default="", help="只导指定供应商（模糊匹配）")
    ap.add_argument("--dry-run", action="store_true", help="预览不写入")
    ap.add_argument("--tenant-id", default="default",
                    help="目标租户（P3-5 租户化，默认 default）")
    args = ap.parse_args()
    tenant_id = (args.tenant_id or "default").strip() or "default"

    meta = load_manifest()
    expected_files = sorted(f for f in os.listdir(EXPECTED_DIR) if f.endswith(".json"))
    if args.limit:
        expected_files = expected_files[: args.limit]

    # 供应商 → 收据计数（用已导入数据避免重复；去重口径限定目标租户）
    existing_suppliers = {s.name for s in db.list_suppliers(include_inactive=True,
                                                            tenant_id=tenant_id)}
    existing_receipts = {r.supplier_name for r in db.list_receipt_rows(tenant_id=tenant_id)}
    dedup_keys = {(r.supplier_name, r.receipt_date)
                  for r in db.list_receipt_rows(tenant_id=tenant_id)}

    imported = 0
    skipped = 0
    for ef in expected_files:
        img = ef.replace(".json", ".jpg")
        if not os.path.exists(os.path.join(RECEIPTS_DIR, img)):
            img = ef.replace(".json", ".jpeg")
        if not os.path.exists(os.path.join(RECEIPTS_DIR, img)):
            print(f"  [WARN]  跳过（无图）: {ef}")
            skipped += 1
            continue

        exp = json.load(open(os.path.join(EXPECTED_DIR, ef), encoding="utf-8"))
        supplier = (exp.get("supplier_name") or "").strip()
        date = (exp.get("date") or "").strip()
        if not supplier:
            print(f"  [WARN]  跳过（无供应商）: {ef}")
            skipped += 1
            continue
        if args.vendor and args.vendor not in supplier:
            skipped += 1
            continue

        # 去重：同供应商同日期已导入 → 跳过（幂等补标基准集成员，支持看板恢复标记）
        if (supplier, date) in dedup_keys:
            for r in db.list_receipt_rows(tenant_id=tenant_id):
                if r.supplier_name == supplier and (r.receipt_date or "") == date:
                    db.set_golden_sample(r.id, 1, tenant_id)
            skipped += 1
            continue

        if args.dry_run:
            print(f"  [DRY] {supplier[:20]} | {date} | {exp.get('total_amount')} | {len(exp.get('items', []))}项")
            imported += 1
            continue

        # 复制图片到 uploads
        img_name = db.new_id() + os.path.splitext(img)[1]
        dst = os.path.join(UPLOAD_DIR, img_name)
        shutil.copy2(os.path.join(RECEIPTS_DIR, img), dst)

        # 建收据（edited；P3-5：携带目标租户）
        rid = db.create_receipt(supplier_name=supplier, status="edited",
                                tenant_id=tenant_id)
        db.set_golden_sample(rid, 1, tenant_id)  # 黄金基准集成员标记
        manifest_row = meta.get(img, {})
        doc_form = DOC_FORM_MAP.get(manifest_row.get("doc_form", ""), "printed_delivery_note")
        layout = manifest_row.get("layout_type", "")
        items = exp.get("items", [])
        total = exp.get("total_amount", sum(i.get("amount", 0) for i in items))
        db.update_receipt(
            rid,
            supplier_name=supplier,
            receipt_date=date,
            sheet_name=(date or "")[:7] if date else "",
            total_amount=total,
            doc_form=doc_form,
            layout_type=layout,
            settlement_type=manifest_row.get("settlement_type", "") or "credit",
            status="edited",
        )
        # 明细 + SKU 匹配
        item_rows = []
        for it in items:
            row = {
                "name": it.get("name", ""), "raw_name": it.get("name", ""),
                "quantity": float(it.get("quantity", 0) or 0),
                "unit": it.get("unit", "") or "", "raw_unit": it.get("unit", "") or "",
                "unit_price": float(it.get("unit_price", 0) or 0),
                "amount": float(it.get("amount", 0) or 0),
                # P11：GT 明细由人工确认，本就不存在「模型对该行的把握」这一读数，
                # 故落 NULL 而不是伪造一个 0.95（同一根因的第三处兜底）。
                "sku_id": None, "cost_center_id": None, "confidence": None,
                "matched": 0, "price_anomaly": 0, "price_anomaly_direction": "",
                "price_diff_percent": 0.0, "unit_conversion_warning": "",
                "fuzzy_candidates": [], "entity_candidates": [],
            }
            sku = db.find_sku_by_name(row["name"], tenant_id=tenant_id)
            if sku is None:
                sku_id, _ = db.create_sku(row["name"], base_unit=row["unit"] or "",
                                          tenant_id=tenant_id)
                sku = db.get_sku(sku_id, tenant_id=tenant_id)
            if sku:
                row["sku_id"] = sku.id
                row["matched"] = 1
            item_rows.append(row)
        db.set_receipt_items(rid, item_rows, tenant_id=tenant_id)

        # 库存累计（edited 不真正入账，但建 SKU 时记录价格）
        for it in item_rows:
            if it["sku_id"]:
                db.apply_stock_log(
                    sku_id=it["sku_id"], name=it["name"],
                    qty=it["quantity"], unit=it["unit"], amount=it["amount"],
                    vendor=supplier, date=date, receipt_id=rid, kind="in",
                    note="golden import", tenant_id=tenant_id,
                )

        # 供应商建档
        if supplier not in existing_suppliers:
            db.create_supplier(supplier, tenant_id=tenant_id)
            existing_suppliers.add(supplier)
        dedup_keys.add((supplier, date))
        imported += 1
        print(f"  [PASS]  {supplier[:22]} | {date} | ${total} | {len(item_rows)}项 | {doc_form}")

    print(f"\n完成：导入 {imported} 张，跳过 {skipped} 张" + ("（dry-run 预览）" if args.dry_run else ""))


if __name__ == "__main__":
    main()
