# -*- coding: utf-8 -*-
"""评测集样本与单据主表数据双向关联同步服务。

解决痛点：
GT 抽检确权评测集（demo/evalsets/expected/*.json）与单据主表（receipts）脱节，
导致黄金基准集看板无法通过 source_receipt_id 关联并统计已确权 GT 样本。

核心功能：
1. sync_evalset_receipts_linkage(): 幂等遍历评测集，为每个样本建立/确认与 DB 单据的关联：
   - 检查 expected/<sample_id>.json 是否已有有效 source_receipt_id；
   - 若无，匹配或新建 receipts 记录，回填 source_receipt_id；
   - 若样本已确权（gt_status == "confirmed"），确保 is_golden_sample 置为 1。
2. link_or_create_receipt_for_sample(): 单个样本的关联与按需创表。
"""

import json
import logging
import os
from typing import Optional, Dict, Any

from app import db
from app.api_evalset import get_evalset_dir, _load_manifest, _load_gt

logger = logging.getLogger("evalset_linkage")


def link_or_create_receipt_for_sample(
    evalset_dir: str,
    sample_id: str,
    manifest_row: Optional[Dict[str, Any]] = None,
    gt: Optional[Dict[str, Any]] = None,
    tenant_id: str = "default",
) -> Optional[int]:
    """为单个评测集样本关联或创建单据记录，确保 source_receipt_id 回填及黄金标记同步。"""
    if gt is None:
        gt = _load_gt(evalset_dir, sample_id) or {}
    else:
        gt = dict(gt)

    if manifest_row is None:
        m_rows = _load_manifest(evalset_dir) or []
        for r in m_rows:
            if r.get("sample_id") == sample_id:
                manifest_row = r
                break

    gt_status = (manifest_row.get("gt_status") if manifest_row else None) or gt.get("gt_status") or "draft"
    is_confirmed = (gt_status == "confirmed") or (gt.get("gt_status") == "confirmed")

    # 1. 检查已有的 source_receipt_id 是否有效
    rid = None
    s_rid = gt.get("source_receipt_id")
    if s_rid is not None:
        try:
            r_obj = db.get_receipt_row(int(s_rid))
            if r_obj is not None:
                rid = int(s_rid)
        except (ValueError, TypeError):
            rid = None

    # 确定图片路径
    img_rel = (manifest_row.get("image") if manifest_row else None) or f"receipts/{sample_id}.png"
    if os.path.isabs(img_rel):
        abs_img_path = img_rel
    else:
        abs_img_path = os.path.abspath(os.path.join(evalset_dir, img_rel))

    if not os.path.exists(abs_img_path):
        fallback_path = os.path.abspath(os.path.join(evalset_dir, "receipts", f"{sample_id}.png"))
        if os.path.exists(fallback_path):
            abs_img_path = fallback_path

    # 2. 若无有效 source_receipt_id，尝试在数据库中匹配已有单据
    if rid is None:
        all_receipts = db.list_receipt_rows(tenant_id=tenant_id)
        img_base = os.path.basename(abs_img_path)

        # 优先按图片文件名匹配
        for r in all_receipts:
            if r.image_path:
                if (os.path.abspath(r.image_path) == abs_img_path or
                        os.path.basename(r.image_path) == img_base):
                    rid = r.id
                    break

        # 其次按供应商名 + 日期 + 总金额匹配（仅当供应商名和日期非空且金额 > 0）
        if rid is None:
            s_name = (gt.get("supplier_name") or "").strip()
            s_date = (gt.get("date") or "").strip()
            s_total = float(gt.get("total_amount", 0.0) or 0.0)
            if s_name and s_date and s_total > 0:
                for r in all_receipts:
                    if ((r.supplier_name or "").strip() == s_name and
                            (r.receipt_date or "").strip() == s_date and
                            abs((r.total_amount or 0.0) - s_total) < 0.01):
                        rid = r.id
                        break

    # 3. 若仍未匹配到，则在 DB 中新增一条 receipt 记录
    if rid is None:
        doc_form = (manifest_row.get("doc_form") if manifest_row else "") or gt.get("doc_form", "") or ""
        total_amt = float(gt.get("total_amount", 0.0) or 0.0)
        items = gt.get("items", [])
        items_json = json.dumps(items, ensure_ascii=False) if isinstance(items, list) else "[]"
        adj_notes = gt.get("adjustment_notes", [])
        adj_notes_json = json.dumps(adj_notes, ensure_ascii=False) if isinstance(adj_notes, list) else "[]"

        rid = db.create_receipt_record(
            supplier_name=gt.get("supplier_name", "") or "",
            receipt_date=gt.get("date", "") or "",
            total_amount=total_amt,
            currency=gt.get("currency", "HKD") or "HKD",
            doc_form=doc_form,
            status="uploaded",
            image_path=abs_img_path,
            items_json=items_json,
            is_golden_sample=1 if is_confirmed else 0,
            tenant_id=tenant_id,
            adjustment_notes_json=adj_notes_json,
            payment_evidence=gt.get("payment_evidence", "") or "",
            service_fee=float(gt.get("service_fee", 0.0) or 0.0),
            tax_amount=float(gt.get("tax_amount", 0.0) or 0.0),
            payment_mark="paid" if gt.get("payment_marked") else "",
        )

        # 保存明细到 receipt_items 表（若有）
        if items and isinstance(items, list):
            items_to_save = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                it_copy = dict(it)
                if "quantity" not in it_copy and "qty" in it_copy:
                    it_copy["quantity"] = it_copy.get("qty", 0.0)
                it_copy.pop("qty", None)
                items_to_save.append(it_copy)
            try:
                db.set_receipt_items(rid, items_to_save, tenant_id=tenant_id)
            except Exception as e:
                logger.warning("[evalset_linkage] Failed saving receipt items for %s (rid=%s): %s", sample_id, rid, e)

    # 4. 回写 source_receipt_id 到 expected/<sample_id>.json
    if rid is not None:
        if gt.get("source_receipt_id") != rid:
            gt["source_receipt_id"] = rid
            exp_path = os.path.join(evalset_dir, "expected", f"{sample_id}.json")
            os.makedirs(os.path.dirname(exp_path), exist_ok=True)
            try:
                with open(exp_path, "w", encoding="utf-8") as f:
                    json.dump(gt, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.warning("[evalset_linkage] Failed writing source_receipt_id to %s: %s", exp_path, e)

    # 5. 若样本为已确权状态，确保 DB 中黄金基准集标记为 1
    if is_confirmed and rid is not None:
        try:
            db.set_golden_sample(rid, 1, tenant_id=tenant_id)
        except Exception as e:
            logger.warning("[evalset_linkage] Failed setting is_golden_sample for rid=%s: %s", rid, e)

    return rid


def sync_evalset_receipts_linkage(
    evalset_dir: Optional[str] = None,
    tenant_id: str = "default",
) -> Dict[str, int]:
    """幂等同步整个评测集与单据数据库的关联。

    遍历 manifest.csv 中的每个样本，确保：
    1. expected/<sample_id>.json 包含有效的 source_receipt_id 并指向真实单据；
    2. gt_status == "confirmed" 的样本对应的单据 is_golden_sample == 1。
    """
    if evalset_dir is None:
        evalset_dir = get_evalset_dir()

    manifest_rows = _load_manifest(evalset_dir)
    if not manifest_rows:
        logger.info("[evalset_linkage] No manifest found in %s, skipping sync.", evalset_dir)
        return {"total": 0, "linked": 0, "created": 0, "confirmed": 0}

    stats = {
        "total": len(manifest_rows),
        "linked": 0,
        "created": 0,
        "confirmed": 0,
    }

    for m_row in manifest_rows:
        sample_id = m_row.get("sample_id")
        if not sample_id:
            continue

        gt = _load_gt(evalset_dir, sample_id) or {}
        s_rid = gt.get("source_receipt_id")
        already_had_valid_rid = False
        if s_rid is not None:
            try:
                already_had_valid_rid = (db.get_receipt_row(int(s_rid)) is not None)
            except (ValueError, TypeError):
                already_had_valid_rid = False

        rid = link_or_create_receipt_for_sample(
            evalset_dir=evalset_dir,
            sample_id=sample_id,
            manifest_row=m_row,
            gt=gt,
            tenant_id=tenant_id,
        )

        if rid is not None:
            stats["linked"] += 1
            if not already_had_valid_rid:
                stats["created"] += 1
            is_conf = (m_row.get("gt_status") == "confirmed") or (gt.get("gt_status") == "confirmed")
            if is_conf:
                stats["confirmed"] += 1

    logger.info("[evalset_linkage] Sync completed: %s", stats)
    return stats
