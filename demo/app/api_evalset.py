# -*- coding: utf-8 -*-
"""评测集 GT 抽检台 API —— Gap A2（T4）。

评测集是全局资产（demo/evalsets/，业务数据不入库、不经 tenant 过滤），
但鉴权与现有 X-Role 模式一致：抽检接口仅限 admin。

- GET  /api/evalset/samples?split=test&gt_status=draft   样本列表（按 split/状态过滤）
- GET  /api/evalset/sample/{sample_id}                   图片 base64 + AI 候选 GT 全文
- POST /api/evalset/sample/{sample_id}/confirm           人工校正后的 GT → confirmed
- GET  /api/evalset/stats                                各 split 的 draft/confirmed/missing 计数

GT 落盘：demo/evalsets/expected/<sample_id>.json（schema 与 run_eval 的 expected
消费格式对齐：supplier_name/date/total_amount/items[...]），manifest.csv 同步
gt_status / gt_source_model。
"""

import base64
import csv
import json
import os
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.auth import require_admin

router = APIRouter()

# 评测集目录：默认 demo/evalsets，可用环境变量 EVALSET_DIR 覆盖（测试注入临时目录）
EVALSET_DIR_ENV = "EVALSET_DIR"

MANIFEST_COLUMNS = [
    "sample_id", "image", "split", "doc_form", "layout_type", "supplier_id",
    "gt_status", "gt_source_model", "src_sha1", "short_side",
]

# 人工确认后的 GT 必须携带的最小字段（与 run_eval.compare 消费口径对齐）
GT_REQUIRED_KEYS = ("supplier_name", "date", "total_amount", "items")


def get_evalset_dir():
    """评测集根目录：环境变量优先，缺省 demo/evalsets。"""
    d = os.environ.get(EVALSET_DIR_ENV, "").strip()
    if not d:
        d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "evalsets")
    return os.path.abspath(d)


# ------------------------------------------------------------------
# manifest 读写
# ------------------------------------------------------------------
def _manifest_path(evalset_dir):
    return os.path.join(evalset_dir, "manifest.csv")


def _load_manifest(evalset_dir):
    path = _manifest_path(evalset_dir)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _save_manifest(evalset_dir, rows):
    """按固定列序写回 manifest（未知列丢弃，与 build_evalset 列定义一致）。"""
    path = _manifest_path(evalset_dir)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _find_row(rows, sample_id):
    for r in rows:
        if r.get("sample_id") == sample_id:
            return r
    return None


def _expected_path(evalset_dir, sample_id):
    # sample_id 来自 manifest 白名单，不存在路径穿越风险
    return os.path.join(evalset_dir, "expected", sample_id + ".json")


def _load_gt(evalset_dir, sample_id):
    path = _expected_path(evalset_dir, sample_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ------------------------------------------------------------------
# 端点
# ------------------------------------------------------------------
@router.get("/api/evalset/samples")
def list_samples(request: Request, split: str = None, gt_status: str = None):
    """样本列表（可按 split / gt_status 过滤）。仅 admin。"""
    require_admin(request)
    evalset_dir = get_evalset_dir()
    rows = _load_manifest(evalset_dir)
    if rows is None:
        return JSONResponse(status_code=404, content={
            "status": "error",
            "msg": "评测集 manifest 不存在（%s）。请先运行 build_evalset.py 构建。" % _manifest_path(evalset_dir),
        })

    samples = []
    for r in rows:
        if split and r.get("split") != split:
            continue
        status = r.get("gt_status") or "missing"
        if gt_status and status != gt_status:
            continue
        samples.append({
            "sample_id": r.get("sample_id", ""),
            "image": r.get("image", ""),
            "split": r.get("split", ""),
            "doc_form": r.get("doc_form", ""),
            "layout_type": r.get("layout_type", ""),
            "supplier_id": r.get("supplier_id", ""),
            "gt_status": status,
            "gt_source_model": r.get("gt_source_model", ""),
            "short_side": r.get("short_side", ""),
        })
    return {"status": "success", "data": {"samples": samples, "total": len(samples)}}


@router.get("/api/evalset/sample/{sample_id}")
def get_sample(sample_id: str, request: Request):
    """单样本详情：原图 base64 + AI 候选 GT 全文。仅 admin。"""
    require_admin(request)
    evalset_dir = get_evalset_dir()
    rows = _load_manifest(evalset_dir)
    if rows is None:
        return JSONResponse(status_code=404, content={
            "status": "error", "msg": "评测集 manifest 不存在，请先运行 build_evalset.py 构建。"})
    row = _find_row(rows, sample_id)
    if row is None:
        return JSONResponse(status_code=404, content={
            "status": "error", "msg": "样本 %s 不在评测集 manifest 中" % sample_id})

    image_path = os.path.join(evalset_dir, row.get("image", ""))
    image_b64 = ""
    image_missing = None
    if os.path.exists(image_path):
        with open(image_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("ascii")
    else:
        image_missing = "图片文件缺失：%s" % row.get("image", "")

    return {"status": "success", "data": {
        "sample_id": sample_id,
        "split": row.get("split", ""),
        "doc_form": row.get("doc_form", ""),
        "layout_type": row.get("layout_type", ""),
        "supplier_id": row.get("supplier_id", ""),
        "gt_status": row.get("gt_status") or "missing",
        "gt_source_model": row.get("gt_source_model", ""),
        "image_base64": image_b64,
        "image_missing": image_missing,
        "gt": _load_gt(evalset_dir, sample_id),
    }}


class ConfirmBody(BaseModel):
    model_config = {"extra": "forbid"}
    gt: dict
    # 空总额（月结单等场景合法留空）必须显式确认，防止漏抄图面金额
    confirm_blank_total: bool = False


def _is_blank_total(value) -> bool:
    """total_amount 视为空：None、空串或纯空白串（0 是合法金额不算空）。"""
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


@router.post("/api/evalset/sample/{sample_id}/confirm")
def confirm_sample(sample_id: str, body: ConfirmBody, request: Request):
    """人工确认：写回校正后的 GT（confirmed + reviewed_by/at），manifest 同步。仅 admin。"""
    account = require_admin(request)
    evalset_dir = get_evalset_dir()
    rows = _load_manifest(evalset_dir)
    if rows is None:
        return JSONResponse(status_code=404, content={
            "status": "error", "msg": "评测集 manifest 不存在，请先运行 build_evalset.py 构建。"})
    row = _find_row(rows, sample_id)
    if row is None:
        return JSONResponse(status_code=404, content={
            "status": "error", "msg": "样本 %s 不在评测集 manifest 中" % sample_id})

    gt = body.gt or {}
    missing = [k for k in GT_REQUIRED_KEYS if k not in gt]
    if missing:
        return JSONResponse(status_code=400, content={
            "status": "error",
            "msg": "提交的 GT 缺少必要字段：%s（需与识别输出同构：商户/日期/总额/明细行）"
                   % ", ".join(missing)})

    # 空总额守卫：留空合法（如 S049 类月结单），但必须显式确认，防止漏抄图面金额
    if _is_blank_total(gt.get("total_amount")) and not body.confirm_blank_total:
        return JSONResponse(status_code=400, content={
            "status": "error",
            "msg": "总额为空，请核实图面金额，或携带 confirm_blank_total=true 明确确认留空"})

    # 保留候选溯源字段，状态流转为 confirmed
    saved = dict(gt)
    saved["gt_status"] = "confirmed"
    saved.setdefault("gt_source_model", row.get("gt_source_model") or "human")
    saved["gt_reviewed_by"] = account.get("email", "admin@demo.hk")
    saved["gt_reviewed_at"] = datetime.now().isoformat(timespec="seconds")

    exp_path = _expected_path(evalset_dir, sample_id)
    os.makedirs(os.path.dirname(exp_path), exist_ok=True)
    with open(exp_path, "w", encoding="utf-8") as f:
        json.dump(saved, f, ensure_ascii=False, indent=2)

    row["gt_status"] = "confirmed"
    if not row.get("gt_source_model"):
        row["gt_source_model"] = saved["gt_source_model"]
    _save_manifest(evalset_dir, rows)

    return {"status": "success", "data": {
        "sample_id": sample_id,
        "gt_status": "confirmed",
        "gt_reviewed_by": saved["gt_reviewed_by"],
        "gt_reviewed_at": saved["gt_reviewed_at"],
    }}


@router.get("/api/evalset/stats")
def evalset_stats(request: Request):
    """各 split 的 draft/confirmed/missing 计数。仅 admin。"""
    require_admin(request)
    evalset_dir = get_evalset_dir()
    rows = _load_manifest(evalset_dir)
    if rows is None:
        return JSONResponse(status_code=404, content={
            "status": "error", "msg": "评测集 manifest 不存在，请先运行 build_evalset.py 构建。"})

    splits = {}
    for r in rows:
        s = r.get("split") or "unknown"
        status = r.get("gt_status") or "missing"
        # expected 文件缺失的样本按 missing 计（manifest 状态可能滞后）
        if status != "missing" and _load_gt(evalset_dir, r.get("sample_id", "")) is None:
            status = "missing"
        bucket = splits.setdefault(s, {"draft": 0, "confirmed": 0, "missing": 0, "total": 0})
        bucket[status] = bucket.get(status, 0) + 1
        bucket["total"] += 1
    return {"status": "success", "data": {"splits": splits, "evalset_dir": evalset_dir}}
