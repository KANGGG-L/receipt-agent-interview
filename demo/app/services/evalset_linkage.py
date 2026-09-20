# -*- coding: utf-8 -*-
"""评测集样本与单据主表数据双向关联同步服务。

解决痛点：
GT 抽检确权评测集（demo/evalsets/expected/*.json）与单据主表（receipts）脱节，
导致黄金基准集看板无法通过 source_receipt_id 关联并统计已确权 GT 样本。

核心功能：
1. sync_evalset_receipts_linkage(): 幂等遍历评测集，为每个样本建立/确认与 DB 单据的关联：
   - 校验 expected/<sample_id>.json 已有的 source_receipt_id（要求「单据存在 + 语义吻合」）是否有效；
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

# 部署默认（共享）语料目录 demo/evalsets。语料已 gitignore，是本机共用的真实评测数据；
# 其 source_receipt_id 是「某个库里的 id」，只对与它成对的部署默认库有意义（见
# `_shared_corpus_write_allowed`）。
SHARED_EVALSET_DIR = os.path.abspath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "evalsets"))


def _receipt_image_matches(receipt, abs_img_path: str) -> bool:
    """单据的 image_path 是否就是本样本的原图（绝对路径或文件名一致）。

    这是「本样本载体」最强的判据：语料样本的原图文件名唯一（receipts/S0xx.png），
    以它为 image_path 的单据就是该样本的载体。
    """
    img_path = getattr(receipt, "image_path", None) or ""
    if not (img_path and abs_img_path):
        return False
    return (os.path.abspath(img_path) == abs_img_path
            or os.path.basename(img_path) == os.path.basename(abs_img_path))


def _find_receipt_by_image(abs_img_path: str, tenant_id: str) -> Optional[int]:
    """按 image_path（绝对路径或文件名）在库中查单据 id，找不到返回 None。"""
    img_base = os.path.basename(abs_img_path)
    if not img_base:
        return None
    for r in db.list_receipt_rows(tenant_id=tenant_id):
        if _receipt_image_matches(r, abs_img_path):
            return r.id
    return None


def _shared_corpus_write_allowed(evalset_dir: str, write_back: bool) -> bool:
    """是否允许把 source_receipt_id 回写进目标语料。

    - write_back=False：调用方显式声明只读，一律不回写（如「核对引用」类脚本）；
    - 目标是共享语料、但活动库不是部署默认库：拒绝回写并告警。

    why（第二条）：source_receipt_id 是「库内 id」，只对与语料成对的默认库有意义。测试用例
    会把 DB_PATH 指到临时库（见 demo/conftest.py 的 autouse 隔离），若此时仍回写共享语料，
    写进去的是临时库的 id；临时库一丢引用即全部悬空（历史 source_receipt_id=1..33 顺序号
    污染正是这条路径，见 main.py::_sync_evalset_receipts_startup 的同类闸门）。启动自愈那条
    路径已由 main.py 的闸门挡住，这里挡住的是「显式对共享语料调 linkage」的其余调用点。
    隔离库上联动仍可正常建单与置位黄金标记，只是不再改写共享语料。
    """
    if not write_back:
        return False
    try:
        if os.path.abspath(str(evalset_dir)) != SHARED_EVALSET_DIR:
            return True
    except (ValueError, TypeError):
        return True
    try:
        return os.path.abspath(str(db.DB_PATH)) == os.path.abspath(str(db._default_db_path))
    except Exception:
        return False


def _receipt_matches_sample(receipt, abs_img_path: str, gt: Dict[str, Any]) -> bool:
    """判断一条 receipts 记录是否确实是该评测集样本的载体单据。

    判据与 `link_or_create_receipt_for_sample` 的匹配顺序保持一致：图片路径/文件名相同，
    或 供应商名 + 日期 + 总金额（> 0）三者相同。用于校验历史 source_receipt_id 是否仍然有效
    —— 只校验「单据存在」会把 id 回收后的错误引用永久固化（旧引用恰好仍指向另一条业务单据）。
    """
    try:
        if _receipt_image_matches(receipt, abs_img_path):
            return True
        s_name = (gt.get("supplier_name") or "").strip()
        s_date = (gt.get("date") or "").strip()
        s_total = float(gt.get("total_amount", 0.0) or 0.0)
        if s_name and s_date and s_total > 0:
            return ((receipt.supplier_name or "").strip() == s_name
                    and (receipt.receipt_date or "").strip() == s_date
                    and abs((receipt.total_amount or 0.0) - s_total) < 0.01)
    except (ValueError, TypeError):
        return False
    return False


def gt_items_for_receipt_table(items) -> list:
    """把 GT 明细条目归一为 receipt_items 可落库的 dict 列表。

    唯一的字段契约差异：GT / items_json 用 `qty`，receipt_items 表列名是
    `quantity`（其余键 name/unit/unit_price/amount 同名直传）。非 dict 条目丢弃。
    这是「GT 明细 → 明细表」的唯一转换实现，建单分支与历史数据回填共用。
    """
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        row = dict(it)
        if "quantity" not in row and "qty" in row:
            row["quantity"] = row.get("qty", 0.0)
        row.pop("qty", None)
        out.append(row)
    return out


def link_or_create_receipt_for_sample(
    evalset_dir: str,
    sample_id: str,
    manifest_row: Optional[Dict[str, Any]] = None,
    gt: Optional[Dict[str, Any]] = None,
    tenant_id: str = "default",
    write_back: bool = True,
) -> Optional[int]:
    """为单个评测集样本关联或创建单据记录，确保 source_receipt_id 回填及黄金标记同步。

    write_back=False 时只做关联与黄金标记同步，不回写 target 语料（只读核对场景）。
    即便 write_back=True，共享语料在「活动库非部署默认库」时也拒绝回写，
    理由见 `_shared_corpus_write_allowed`。
    """
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

    # 确定图片路径（既用于匹配，也用于校验已有引用是否确实指向本样本的载体）
    img_rel = (manifest_row.get("image") if manifest_row else None) or f"receipts/{sample_id}.png"
    if os.path.isabs(img_rel):
        abs_img_path = img_rel
    else:
        abs_img_path = os.path.abspath(os.path.join(evalset_dir, img_rel))

    if not os.path.exists(abs_img_path):
        fallback_path = os.path.abspath(os.path.join(evalset_dir, "receipts", f"{sample_id}.png"))
        if os.path.exists(fallback_path):
            abs_img_path = fallback_path

    # 1. 校验已有的 source_receipt_id：不仅要求单据存在，还要求它确实是本样本的载体
    #    why: 历史库经历过 id 回收（旧引用恰好仍指向另一条业务单据），只做存在性校验会把
    #    错误引用永久固化；这里改成「存在 + 语义吻合」双条件，语义不符则回到匹配流程重挂。
    rid = None
    stale_rid = None
    s_rid = gt.get("source_receipt_id")
    if s_rid is not None:
        try:
            r_obj = db.get_receipt_row(int(s_rid))
        except (ValueError, TypeError):
            r_obj = None
        if r_obj is not None:
            if _receipt_image_matches(r_obj, abs_img_path):
                # 图片就是本样本原图，最强判据，直接采信
                rid = int(s_rid)
            elif _receipt_matches_sample(r_obj, abs_img_path, gt):
                # 旧引用仅靠三元组吻合：同值重复单据会让三元组命中「别人的」单据
                # （S112 曾命中 660，而 343/344/660 三条同值）。此时若库中确实存在以
                # 本样本原图为 image_path 的载体，以图片载体为准并登记旧引用待治理。
                image_rid = _find_receipt_by_image(abs_img_path, tenant_id)
                if image_rid is not None and image_rid != int(s_rid):
                    rid = image_rid
                    stale_rid = int(s_rid)
                else:
                    rid = int(s_rid)
            else:
                stale_rid = int(s_rid)

    # 2. 若无有效 source_receipt_id，尝试在数据库中匹配已有单据
    if rid is None:
        # 优先按图片文件名匹配
        rid = _find_receipt_by_image(abs_img_path, tenant_id)

        # 其次按供应商名 + 日期 + 总金额匹配（仅当供应商名和日期非空且金额 > 0）
        if rid is None:
            s_name = (gt.get("supplier_name") or "").strip()
            s_date = (gt.get("date") or "").strip()
            s_total = float(gt.get("total_amount", 0.0) or 0.0)
            if s_name and s_date and s_total > 0:
                for r in db.list_receipt_rows(tenant_id=tenant_id):
                    if ((r.supplier_name or "").strip() == s_name and
                            (r.receipt_date or "").strip() == s_date and
                            abs((r.total_amount or 0.0) - s_total) < 0.01):
                        rid = r.id
                        break

    # 3. 旧引用存在但语义不符、且库中找不到对应单据：保留原引用并告警，不新增单据
    #    （凭空补建会造出重复载体，改写成一个新造单据同样会掩盖数据问题，交由人工治理）
    if rid is None and stale_rid is not None:
        logger.warning(
            "[evalset_linkage] %s: source_receipt_id=%s 与样本不符且库中无匹配单据，保留原引用不改写",
            sample_id, stale_rid)
        return stale_rid

    # 4. 若仍未匹配到，则在 DB 中新增一条 receipt 记录
    if rid is None:
        doc_form = (manifest_row.get("doc_form") if manifest_row else "") or gt.get("doc_form", "") or ""
        total_amt = float(gt.get("total_amount", 0.0) or 0.0)
        items = gt.get("items", [])
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
            is_golden_sample=1 if is_confirmed else 0,
            tenant_id=tenant_id,
            adjustment_notes_json=adj_notes_json,
            payment_evidence=gt.get("payment_evidence", "") or "",
            service_fee=float(gt.get("service_fee", 0.0) or 0.0),
            tax_amount=float(gt.get("tax_amount", 0.0) or 0.0),
            payment_mark="paid" if gt.get("payment_marked") else "",
        )

        # 保存明细到 receipt_items 表（若明细非空）
        # 历史遗留：曾同时把 GT 明细写进 receipts.items_json。该列全仓零消费方
        # （详情/复核/导出一律只读 receipt_items），双写只会造成「同一份数据两个来源
        # 可能不一致」——1455-1486 这 32 条载体就是只写进 items_json、明细表为空，
        # 导致详情/导出明细长期为空。此处只写明细表，接收端只认它这一处。
        if items and isinstance(items, list):
            items_to_save = gt_items_for_receipt_table(items)
            expected = len(items_to_save)
            try:
                db.set_receipt_items(rid, items_to_save, tenant_id=tenant_id)
                # 写入后校验：receipt_items 已是明细的唯一数据源（items_json 不再写），
                # 写入静默失败会让该载体在详情/复核/导出里永久显示为空且无第二线索，
                # 故行数不符必须升级为 error，不能只留一条 warning 就放过。
                actual = len(db.get_receipt_items(rid, tenant_id=tenant_id))
                if actual != expected:
                    logger.error(
                        "[evalset_linkage] %s: rid=%s 明细写入不完整（预期 %s 条、实际 %s 条），"
                        "该载体在详情/复核/导出中的明细将为空或不完整",
                        sample_id, rid, expected, actual)
            except Exception as e:
                logger.error(
                    "[evalset_linkage] %s: rid=%s 明细写入失败（预期 %s 条），"
                    "该载体在详情/复核/导出中的明细将为空：%s",
                    sample_id, rid, expected, e)

    # 5. 回写 source_receipt_id 到 expected/<sample_id>.json
    #    共享语料 + 非部署默认库时拒绝回写（见 `_shared_corpus_write_allowed`）
    if rid is not None:
        if gt.get("source_receipt_id") != rid:
            if _shared_corpus_write_allowed(evalset_dir, write_back):
                gt["source_receipt_id"] = rid
                exp_path = os.path.join(evalset_dir, "expected", f"{sample_id}.json")
                os.makedirs(os.path.dirname(exp_path), exist_ok=True)
                try:
                    with open(exp_path, "w", encoding="utf-8") as f:
                        json.dump(gt, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    logger.warning("[evalset_linkage] Failed writing source_receipt_id to %s: %s", exp_path, e)
            else:
                logger.warning(
                    "[evalset_linkage] %s: 拒绝回写共享语料（活动库 %s 非部署默认库 %s），"
                    "本次解析到的 rid=%s 未写入。测试请把 EVALSET_DIR 指向语料副本。",
                    sample_id, db.DB_PATH, db._default_db_path, rid)

    # 6. 若样本为已确权状态，确保 DB 中黄金基准集标记为 1
    if is_confirmed and rid is not None:
        try:
            db.set_golden_sample(rid, 1, tenant_id=tenant_id)
        except Exception as e:
            logger.warning("[evalset_linkage] Failed setting is_golden_sample for rid=%s: %s", rid, e)

    return rid


def sync_evalset_receipts_linkage(
    evalset_dir: Optional[str] = None,
    tenant_id: str = "default",
    write_back: bool = True,
) -> Dict[str, int]:
    """幂等同步整个评测集与单据数据库的关联。

    遍历 manifest.csv 中的每个样本，确保：
    1. expected/<sample_id>.json 包含有效的 source_receipt_id 并指向真实单据；
    2. gt_status == "confirmed" 的样本对应的单据 is_golden_sample == 1。

    write_back=False 时只读不回写语料；即便为 True，共享语料在活动库非部署默认库时
    同样拒绝回写（见 `_shared_corpus_write_allowed`）。
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
            write_back=write_back,
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
