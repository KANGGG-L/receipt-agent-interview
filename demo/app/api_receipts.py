# -*- coding: utf-8 -*-
"""收据端点：上传(异步Job)/轮询/列表/详情/保存(乐观锁)/approve/flag/retry/导出。"""

import io
import json
import logging
import mimetypes
import os
import re
from pathlib import Path
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from app import db
from app.auth import require_role
from app.services import image_web
from app.services.contract import payment_mark_from_image, sanitize_nan
from app.services.receipt_utils import (
    build_detail, build_row, compute_review_diff, get_job, public_image_url,
    start_recognition_job, verify_preview_image_signature,
)


def _tenant_id(request: Request) -> str:
    """Gap E2 租户键：与 X-Role 同风格取请求头，缺省 default。"""
    return (request.headers.get("X-Tenant-Id")
            or request.headers.get("x-tenant-id") or "default").strip() or "default"


def _supplement_payment_mark(receipt_row) -> str:
    """印章判定补红章：若已有 payment_mark 则保留，否则用红章检测补充。

    红章检测单一来源：app.services.contract.detect_red_stamp。
    """
    existing = (receipt_row.payment_mark or "").strip()
    if existing:
        return existing
    return payment_mark_from_image(receipt_row.image_path or "", llm_marked=False)


def _track_event(account, session_id, event_type, receipt_id=None,
                 properties=None, grp=None, tenant_id=None):
    """埋点封装：失败静默忽略，不影响主业务。"""
    try:
        db.log_user_event(
            account_id=account.get("email", "") if account else "",
            session_id=str(session_id or ""),
            event_type=str(event_type),
            receipt_id=int(receipt_id) if receipt_id else None,
            properties=properties or {},
            grp=str(grp) if grp else None,
            tenant_id=tenant_id,
        )
    except Exception as e:
        import logging
        logging.getLogger("api_receipts").warning(f"[WARN] 记录用户事件失败: {e}")


def _track_ai_decision(account, receipt_id, row=None, **kw):
    """AI 决策日志封装。"""
    try:
        kwargs = dict(kw)
        kwargs.setdefault("grp", "control")
        if row is not None:
            kwargs.setdefault("supplier_id", getattr(row, "supplier_id", None))
            kwargs.setdefault("use_grey", getattr(row, "use_grey", 0) or 0)
        db.log_ai_decision(receipt_id=receipt_id, **kwargs)
    except Exception as e:
        import logging
        logging.getLogger("api_receipts").warning(f"[WARN] 记录 AI 决策失败: {e}")

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff"}


def _resolve_upload_file(raw):
    """把 DB 里的 image_path 归一到 uploads 目录内的绝对路径；越界返回 None。

    why: 原图预览端点不经请求头鉴权对外暴露，必须防 `../` 路径穿越与绝对路径注入。
    先 basename 归一（丢弃任何目录成分），再 resolve 后校验父目录链落在模块级
    UPLOAD_DIR 内（resolve 同时穿透符号链接）。用模块级 UPLOAD_DIR 以便测试
    monkeypatch 到临时目录。
    """
    if not raw:
        return None
    name = os.path.basename(str(raw).strip())
    if not name:
        return None
    base = Path(UPLOAD_DIR).resolve()
    try:
        candidate = (base / name).resolve()
    except OSError:
        return None
    if base not in candidate.parents:
        return None
    return candidate


def _cleanup_unreferenced_preview_cache(image_path) -> bool:
    """安全清理某个源文件对应的 `<stem>_web.jpg` 预览缓存（源原图一律不动）。

    why: replace-image 换图后用新的 `db.new_id()` 文件名落盘，旧源文件的预览缓存再无
    请求路径可达（预览端点只会按 image_path 定位源文件），不清理会随重拍次数长期在
    uploads 累积。

    删除前必须证明「已无任何单据引用该源文件」，且引用判定不能只看当前单据：软删单据
    可经回收站恢复、恢复后仍要展示原图，故不得过滤 deleted_at；同理不做租户过滤
    （跨租户引用同名源文件同样构成引用）。引用判定失败时保守跳过 —— 宁留缓存不误删。
    Web 格式不生成缓存副本，直接 no-op（顺带避免无谓的全表引用查询）。

    返回是否真的删除了缓存。
    """
    if not image_path or not image_web.is_non_web_image_path(image_path):
        return False
    try:
        referenced = image_web.referenced_preview_stems(db.list_all_image_paths())
    except Exception as e:
        logging.getLogger("api_receipts").warning(
            f"[WARN] 预览缓存引用判定失败，跳过清理: {e}")
        return False
    if image_web.preview_stem(image_path) in referenced:
        return False
    return image_web.remove_preview_cache(image_path)


def _version_error(payload_version, row, action):
    """乐观锁预检（对齐完整版）：缺失/非法 → 400 VERSION_REQUIRED；不匹配 → 409 VERSION_CONFLICT。"""
    current = row.version if row.version is not None else 1
    if payload_version is None:
        return JSONResponse(
            content={"status": "error", "code": "VERSION_REQUIRED",
                     "msg": f"{action}已有单据必须携带当前 version（整数）。"
                            f"请先 GET /api/receipt/{{id}} 获取 version 后再提交；"
                            f"当前单据 version={current}。"},
            status_code=400)
    if isinstance(payload_version, bool):
        return JSONResponse(content={"status": "error", "code": "VERSION_REQUIRED",
                                     "msg": "version 字段必须为整数"}, status_code=400)
    try:
        v = int(payload_version)
    except (TypeError, ValueError):
        return JSONResponse(content={"status": "error", "code": "VERSION_REQUIRED",
                                     "msg": "version 字段必须为整数"}, status_code=400)
    if v != current:
        return JSONResponse(
            content={"status": "error", "code": "VERSION_CONFLICT",
                     "msg": f"版本冲突：提交的 version={v}，单据当前 version={current}。"
                            f"该单据已被他人修改，请刷新加载最新数据后再{action}。"},
            status_code=409)
    return None


def _save_upload(file: UploadFile) -> str:
    ext = os.path.splitext(file.filename or "")[1].lower() or ".jpg"
    if ext not in ALLOWED_EXT:
        ext = ".jpg"
    path = UPLOAD_DIR / f"{db.new_id()}{ext}"
    with open(path, "wb") as f:
        import shutil
        shutil.copyfileobj(file.file, f)
    return str(path)


# P0-1 极模糊前置拦截：Laplacian 方差阈值（与 image_quality_guard 对齐）
# T10 收口：本常量仅作 settings 缺省值，运行时经 settings_service 键
# 'blur_laplacian_threshold' 实时读取（_blur_threshold()）。
BLUR_THRESHOLD = 30.0


def _image_quality_reject(msg, warnings, **extra):
    """IMAGE_QUALITY_ERROR 400 统一构造（原先 4 处重复响应体收敛于此）。"""
    body = {"status": "error", "code": "IMAGE_QUALITY_ERROR",
            "msg": msg, "quality_warnings": warnings}
    body.update(extra)
    return JSONResponse(status_code=400, content=body)


def _assert_retryable(row, action, verb):
    """挽回动作状态门（/retry 与 /replace-image 共用）。

    edited 必须放行——解析完成后自动保存（前端 autoSaveParsedPhoto）把状态置为
    edited，这是自动保存的正常产物，正是常驻挽回按钮的主场景；
    parsing 拒绝（任务在途防并发）、approved 拒绝（已背书入账，修正走冲销语义，
    与 flag 口径一致）、flagged 拒绝（人工异常须显式处置）。
    返回 None 表示放行。
    """
    old_status = row.status
    # P14/P15：parsed_with_warnings（门禁带警告）与 parsed 同属有效待核对态，须一并放行
    if old_status not in ("uploaded", "parsed", "parsed_with_warnings", "edited", "error"):
        return JSONResponse(
            content={"status": "error",
                     "msg": f"当前状态 [{old_status}] 不允许{action}：仅 uploaded/parsed/parsed_with_warnings/edited/error 单据可{verb}"
                            f"（parsing 解析在途、approved 已入账、flagged 须先人工处置）。"},
            status_code=409)
    if (row.doc_form or "") == "manual_entry":
        return JSONResponse(
            content={"status": "error",
                     "msg": f"手工录入单据无原图，不可{action}；请直接编辑保存。"},
            status_code=400)
    return None


def _blur_threshold() -> float:
    try:
        from app.services import settings_service
        return settings_service.get_float("blur_laplacian_threshold", BLUR_THRESHOLD)
    except Exception:
        return BLUR_THRESHOLD


def _laplacian_variance(image_path: str):
    """计算图像 Laplacian 方差（清晰度指标），<30 视为极模糊。失败回 None（不阻断）。"""
    try:
        # HEIF 注册统一走 image_web._register_heif_opener（加锁、只注册 HEIF、失败只 WARN
        # 一次并记录原因），不再此处自写裸注册。why: 旧写法的 `except Exception: pass` 会让
        # pillow-heif 缺失/版本不兼容时 HEIC 打不开的失败完全不可观测 —— 随后 Image.open
        # 抛错被本函数外层 except 吞掉 → 返回 None → 「极模糊前置拦截」对 HEIC 静默失效，
        # 而非 HEIC 输入一切正常，表现为「只有 HEIC 不被拦截」的隐蔽差异。
        image_web._register_heif_opener()
        from PIL import Image, ImageOps
        with Image.open(image_path) as img:
            try:
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass
            # 超大图限边 1000 保证 <1s 快速失败
            max_side = max(img.size) if img.size[0] and img.size[1] else 0
            if max_side > 1000:
                scale = 1000.0 / max_side
                new_w = max(1, int(img.size[0] * scale))
                new_h = max(1, int(img.size[1] * scale))
                img = img.resize((new_w, new_h), Image.BILINEAR)
            gray = img.convert("L")
            # 优先 numpy 向量化（最快），缺失时回退 PIL Kernel
            try:
                import numpy as np
                arr = np.asarray(gray, dtype=np.float32)
                if arr.shape[0] < 3 or arr.shape[1] < 3:
                    return None
                lap = -4 * arr[1:-1, 1:-1] + arr[:-2, 1:-1] + arr[2:, 1:-1] + arr[1:-1, :-2] + arr[1:-1, 2:]
                return float(lap.var())
            except ImportError:
                from PIL import ImageFilter
                kernel = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1)
                lap_img = gray.filter(kernel)
                vals = list(lap_img.getdata())
                if not vals:
                    return None
                mean = sum(vals) / len(vals)
                var = sum((x - mean) ** 2 for x in vals) / len(vals)
                return float(var)
    except Exception:
        return None
    return None


# -------------------------------------------------------------
# HEIC / 非 Web 格式即时转换接口
# -------------------------------------------------------------
@router.post("/api/convert-image")
async def convert_image(
    request: Request,
    file: UploadFile = File(...),
):
    """将 HEIC/HEIF/TIFF 等浏览器无法直接预览的图片即时转换为标准高质量 JPEG。

    why 复用 image_web.transcode_bytes_to_jpeg：旧实现在此处自写一份转码体，会
    `except Exception: pass` 静默吞掉 pillow_heif 注册失败，并硬编码 quality=92、
    不套 MAX_PREVIEW_SIDE，与 /api/receipt/{id}/image 的预览转码路径行为漂移。
    统一入口后两条路径共享同一套解码/EXIF 矫正/RGB 归一/缩放语义。
    """
    try:
        content = await file.read()
        if not content:
            return JSONResponse(status_code=400, content={"status": "error", "msg": "上传文件为空"})

        jpeg_bytes = image_web.transcode_bytes_to_jpeg(content)

        from fastapi.responses import Response
        return Response(content=jpeg_bytes, media_type="image/jpeg")
    except Exception as e:
        logging.getLogger("api_receipts").warning(f"[WARN] /api/convert-image 图片转换失败: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "msg": f"图片转换失败: {str(e)}"})


# -------------------------------------------------------------
# 单据原图预览：非 Web 格式（HEIC/TIFF）按需转 JPEG
# -------------------------------------------------------------
def _image_not_found(receipt_id, msg):
    """原图不可用统一 404 响应（code 固定 IMAGE_NOT_FOUND，便于前端/测试断言）。"""
    if receipt_id is not None:
        msg = f"单据 {receipt_id} 的{msg}"
    return JSONResponse(status_code=404,
                        content={"status": "error", "code": "IMAGE_NOT_FOUND", "msg": msg})


def _image_forbidden(receipt_id, msg):
    """原图签名校验失败统一 403（与 IMAGE_NOT_FOUND 同风格，便于前端/测试断言）。"""
    if receipt_id is not None:
        msg = f"单据 {receipt_id} 的{msg}"
    return JSONResponse(status_code=403,
                        content={"status": "error", "code": "IMAGE_SIGNATURE_INVALID",
                                 "msg": msg})


def _preview_media_type(src, preview) -> str:
    """按实际返回文件判定 content-type，避免 PNG 谎报为 JPEG。

    why: 端点曾硬编码 media_type="image/jpeg"，但 web_preview_path() 对 Web 格式
    （png/webp/gif）是原样返回源路径、并未转码，于是 PNG 单据的响应头会说
    image/jpeg 而 body 魔数是 89504e47（PNG），类型与内容不符。

    - 源为非 Web 格式（HEIC/TIFF）→ 已转码为 JPEG，固定 image/jpeg；
    - 源为 Web 格式 → 原样透传，按目标文件扩展名给出真实类型；
    - 扩展名无法判定 → 回落 application/octet-stream（不作无根据的猜测）。
    """
    if image_web.is_non_web_image_path(src):
        return "image/jpeg"
    guessed, _ = mimetypes.guess_type(str(preview))
    return guessed or "application/octet-stream"


@router.get("/api/receipt/{receipt_id}/image")
def get_receipt_image(receipt_id: int, request: Request):
    """返回单据原图，非 Web 格式按需转码为 JPEG。

    why 刻意不加 require_role：`<img src>` / `<a target=_blank>` 由浏览器原生发起，
    无法携带 X-Role / Authorization 等请求头，加请求头 RBAC 会让所有图片请求 401、
    功能直接不可用。防护改为四重业务校验：单据存在 + 未软删 + 租户归属 +
    文件落在 uploads 目录内（后者防路径穿越）。暴露面不大于既有的 /uploads 静态挂载
    （后者零鉴权零租户校验）。

    W7 追加签名门槛：receipt_id 自增可枚举，四重校验只挡越权、挡不住遍历 id 扫描
    （租户隔离是「知道 tenant 才能过」的知识型约束）。URL 必须带 public_image_url
    签发的 `sig`（HMAC 绑定 单据 id + 文件名主体 + 租户）；签名在 query 里随 URL
    传递、不依赖请求头，故前端零改动、浏览器匿名取图仍成立。
    """
    # 租户：query 优先（图片 URL 由后端生成时带入），回落请求头，再回落 default
    tenant = (request.query_params.get("tenant_id") or "").strip() or _tenant_id(request)
    # 签名校验置于任何 DB 访问之前：对 receipt_id 的未签名扫描不应产生查询开销
    stem = (request.query_params.get("v") or "").strip()
    if not stem or not verify_preview_image_signature(
            receipt_id, stem, tenant, request.query_params.get("sig")):
        return _image_forbidden(receipt_id, "原图链接签名无效或缺失")

    row = db.get_receipt_row(receipt_id, tenant_id=tenant)
    # get_receipt_row 不过滤软删，需显式判 deleted_at
    if row is None or getattr(row, "deleted_at", None):
        return _image_not_found(receipt_id, "原图不存在")

    src = _resolve_upload_file(getattr(row, "image_path", ""))
    if src is None:
        return _image_not_found(receipt_id, "原图路径非法或不在上传目录内")
    # 签名主体必须等于当前原图的文件名主体：replace-image 换图后旧 URL 立即失效，
    # 避免用一张旧图的合法签名取到换图后的新图（签名与真实资源始终同一份）。
    if stem != image_web.preview_stem(src.name):
        return _image_forbidden(receipt_id, "原图链接已失效（原图已被更换）")
    if not src.is_file():
        return _image_not_found(receipt_id, "原图文件缺失")

    try:
        preview = image_web.web_preview_path(src)
    except Exception as e:
        # 转码失败：返回 500 明确 code，绝不返回半截字节
        logging.getLogger("api_receipts").warning(
            f"[WARN] 单据 {receipt_id} 原图转码失败: {e}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "code": "IMAGE_TRANSCODE_FAILED",
                     "msg": f"原图转码失败: {str(e)}"})

    resp = FileResponse(preview, media_type=_preview_media_type(src, preview))
    # 开发期与静态挂载同口径：no-store 避免换图后浏览器仍用旧图缓存
    try:
        from app.main import DEV_MODE as _dev_mode
    except Exception:
        _dev_mode = False
    if _dev_mode:
        resp.headers.update({"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"})
    return resp


# -------------------------------------------------------------
# 上传 + Job 轮询
# -------------------------------------------------------------
def _resolve_treatment(request: Request, form_value: str) -> str:
    """解析「灰测分组」开关（treatment/control），返回原始字符串。

    why: 前端把该开关放在**查询串**（`/api/upload?treatment=false`），而端点的形参声明
    是 Form —— FastAPI 的 Form 只读表单体、不读查询串，于是形参恒为默认值 "true"，
    勾选被静默忽略（本文件的 `force` 正因如此才显式兼读了 query_params）。这里统一
    「查询串优先、否则用形参」，让单张与批量共用同一口径，避免再出现「一处读 query、
    一处读 Form」的分叉。缺省仍为 "true"（与本仓既有默认一致）。
    """
    raw = request.query_params.get("treatment")
    return form_value if raw is None else raw


@router.post("/api/upload")
async def upload_receipt(
    request: Request,
    receipt: UploadFile = File(...),
    treatment: str = Form("true"),
    async_: str = Form("true"),
    vendor_hint: str = Form(""),
    force: str = Form("false"),  # true → 用户已确认继续，跳过极模糊硬拦截
):
    require_role("staff")(request)
    account = getattr(request.state, "account", {})

    # 图片质量预检：空文件或极端过小文件拦截
    image_path = _save_upload(receipt)
    file_size = os.path.getsize(image_path) if os.path.exists(image_path) else 0
    if file_size < 30:
        if os.path.exists(image_path):
            os.remove(image_path)
        return _image_quality_reject(
            "图像模糊/过暗，请到更亮处重拍，无需打字，点框选裁剪重试（图片损坏或体积过小）",
            ["image_empty_or_corrupted"])

    # P0-1 极模糊前置拦截：Laplacian 方差低于阈值默认 400 快速失败（<1s，不进识别管线）
    # 若 force=true（用户已确认继续），仅记录 warning，不阻断
    blur_score = _laplacian_variance(image_path)
    quality_warnings = []
    is_force = (force.lower() == "true") or (request.query_params.get("force", "").lower() == "true")
    if blur_score is not None and blur_score < _blur_threshold():
        if is_force:
            quality_warnings.append("image_blur")
        else:
            if os.path.exists(image_path):
                try:
                    os.remove(image_path)
                except Exception:
                    pass
            return _image_quality_reject(
                "图像模糊/过暗，请到更亮处重拍，无需打字，点框选裁剪重试",
                ["image_blur"],
                blur_score=round(float(blur_score), 2), confidence=0.35)

    # T10 预处理纠偏（上传后、抽取前）：开关 'preprocess_enabled' 默认 OFF；
    # 任何失败回落原图，绝不阻断识别主链路
    try:
        from app.services import preprocess
        image_path, _prep_meta = preprocess.apply_pipeline(image_path)
        if _prep_meta.get("applied"):
            quality_warnings.append("preprocess_applied")
    except Exception:
        pass

    job_id, receipt_id = start_recognition_job(
        image_path, vendor_hint=vendor_hint or "",
        tenant_id=_tenant_id(request))  # P0-1: 上传链路租户透传

    image_url = public_image_url(receipt_id, image_path, _tenant_id(request))
    # 灰测分组标签（与批量上传同一口径，见 _resolve_treatment）
    grp = "treatment" if _resolve_treatment(request, treatment).lower() == "true" else "control"
    # PDPO 红线（11-组件Spec §8）：vendor_hint 是用户输入明文，只落布尔位，禁落原文
    _track_event(account, getattr(request.state, "session_id", job_id),
                 "upload", receipt_id=receipt_id,
                 properties={"has_vendor_hint": bool(vendor_hint),
                             "image_path": os.path.basename(image_path),
                             "async": async_.lower() == "true"},
                 grp=grp)
    # async=true → queued + job_id（前端轮询）；否则等同步结果
    if async_.lower() == "true":
        return {"status": "queued", "job_id": job_id, "receipt_id": receipt_id,
                "image_url": image_url}

    # 同步模式：等 job 完成
    import time
    for _ in range(600):
        job = get_job(job_id)
        if job["job_status"] == "done":
            return job["result"]
        if job["job_status"] == "error":
            return {"status": "error", "msg": job.get("error_msg", "识别失败"),
                    "receipt_id": receipt_id, "image_url": image_url}
        time.sleep(1)
    return {"status": "error", "msg": "识别超时", "receipt_id": receipt_id}


@router.get("/api/job/{job_id}")
def job_status(job_id: str):
    job = get_job(job_id)
    if job is None:
        return JSONResponse(content={"status": "error", "msg": "未找到该识别任务"},
                            status_code=404)
    return job


@router.post("/api/upload_batch")
async def upload_batch(
    request: Request,
    files: list[UploadFile] = File(...),
    treatment: str = Form("true"),
    force: str = Form("false"),
):
    require_role("staff")(request)
    account = getattr(request.state, "account", {})
    # 灰测分组标签：与单张上传同一口径（此前该形参在批量链路里零使用，勾选被静默忽略）
    grp = "treatment" if _resolve_treatment(request, treatment).lower() == "true" else "control"
    # C5：批量限流 — 单次≤20张
    if len(files) > 20:
        return JSONResponse(
            content={"status": "error", "code": "BATCH_LIMIT_EXCEEDED",
                     "msg": f"单次最多上传20张，当前{len(files)}张，请分批上传"},
            status_code=400)

    # C5：pHash去重检查
    seen_hashes = set()
    results = []
    queue_pos = 0
    for f in files:
        image_path = _save_upload(f)
        # P0-1 极模糊前置拦截（批量同单张）：默认 <1s 快速失败，不进管线
        # 若 force=true（用户已确认继续），仅记录 warning，不阻断
        blur_score = _laplacian_variance(image_path)
        photo_warnings = []
        is_force = (force.lower() == "true") or (request.query_params.get("force", "").lower() == "true")
        if blur_score is not None and blur_score < _blur_threshold():
            if is_force:
                photo_warnings.append("image_blur")
            else:
                if os.path.exists(image_path):
                    try:
                        os.remove(image_path)
                    except Exception:
                        pass
                results.append({
                    "status": "error",
                    "code": "IMAGE_QUALITY_ERROR",
                    "msg": "图像模糊/过暗，请到更亮处重拍，无需打字，点框选裁剪重试",
                    "quality_warnings": ["image_blur"],
                    "blur_score": round(float(blur_score), 2),
                    "confidence": 0.35,
                    "image_url": None,
                })
                continue
        # 简易pHash：基于文件内容hash（像素级pHash需imagehash库，此处用文件MD5近似去重）
        import hashlib
        file_hash = hashlib.md5(open(image_path, "rb").read()).hexdigest()
        if file_hash in seen_hashes:
            if os.path.exists(image_path):
                os.remove(image_path)
            results.append({
                "status": "duplicate", "image_url": None,
                "msg": f"疑似重复上传（文件指纹相同），已跳过",
            })
            continue
        seen_hashes.add(file_hash)
        # T10 预处理纠偏（上传后、抽取前，与单张同口径；失败回落原图不阻断）
        try:
            from app.services import preprocess
            image_path, _prep_meta = preprocess.apply_pipeline(image_path)
            if _prep_meta.get("applied"):
                photo_warnings.append("preprocess_applied")
        except Exception:
            pass
        # P0-1: 批量上传链路租户透传（在派发 Job 线程前捕获，线程内不读 request）
        job_id, receipt_id = start_recognition_job(
            image_path, tenant_id=_tenant_id(request))
        # 埋点与单张上传同口径（漏斗按 receipt_id 去重；批量此前完全不上报 upload 事件，
        # 灰测分组标签也随之丢失）
        _track_event(account, getattr(request.state, "session_id", job_id),
                     "upload", receipt_id=receipt_id,
                     properties={"mode": "batch"},
                     grp=grp)
        row = db.get_receipt_row(receipt_id)
        queue_pos += 1
        results.append({
            "status": "queued", "receipt_id": receipt_id,
            "image_url": public_image_url(receipt_id, image_path, _tenant_id(request)),
            "engine_used": None,
            "quality_warnings": photo_warnings,
            "version": row.version if row else None,
            "queue_position": queue_pos,
            "data": build_detail(row),
        })
    return {"status": "success", "results": results}


# -------------------------------------------------------------
# 列表 / 详情
# -------------------------------------------------------------
def _mask_sensitive(text: str) -> str:
    """脱敏：手机号(13/14/15/16/17/18/19 开头 11 位)、身份证号(18 位含末尾 X / 15 位旧版) 用掩码替换。

    保证：len(_mask_sensitive(明文)) == len(明文)（对匹配到的手机号/身份证号子串本身也成立）。
    """
    import re
    if not text:
        return text
    # 顺序重要：先脱敏 18/15 位身份证（含词边界），再脱敏手机号。
    # 否则 18 位身份证内部的 "19900101123"（11 位、1 开头）会被手机号正则先匹配，
    # 把生日段遮掉、留下校验段，语义错误（虽长度仍对）。
    # 18 位身份证号（含末尾 X/x）：前 6 + 中间掩码 + 后 4，总长度 == 18
    text = re.sub(r"\b\d{17}[0-9Xx]\b",
                  lambda m: m.group(0)[:6] + "*" * (len(m.group(0)) - 10) + m.group(0)[-4:],
                  text)
    # 旧版 15 位身份证号：前 6 + 中间掩码 + 后 4，总长度 == 15
    text = re.sub(r"(?<!\d)\d{15}(?!\d)",
                  lambda m: m.group(0)[:6] + "*" * (len(m.group(0)) - 10) + m.group(0)[-4:],
                  text)
    # 手机号（13/14/15/16/17/18/19 开头 11 位）：前 3 + 中间掩码 + 后 4，总长度 == 11
    text = re.sub(r"1[3-9]\d{9}",
                  lambda m: m.group(0)[:3] + "*" * (len(m.group(0)) - 7) + m.group(0)[-4:],
                  text)
    return text


def _image_url_target_exists(image_path) -> bool:
    """image_path 的 basename 在 uploads 目录下是否真有对应文件（纯存在性探测）。

    why: 列表每行都要判一次，必须只做一次 os.stat —— 不复用 `_resolve_upload_file`
    是因为它会 `Path.resolve()`（realpath 逐段 lstat），实测 1362 行要 268ms，而本
    探测只需 11ms。差异仅在「uploads 内的符号链接指向目录外」这一种情形：端点
    会拒绝、此处会放行，但列表只输出 URL 不打开文件，且 `/uploads` 静态挂载本身
    同样跟随符号链接，不构成新的可达面。

    刻意不做进程内缓存：11ms 远低于噪声；加缓存要么引入 TTL 失效语义（新上传
    的图可能被短暂判为不存在），要么需要按 UPLOAD_DIR 失效（测试会 monkeypatch
    该目录），复杂度不划算。
    """
    name = os.path.basename(str(image_path or "").strip())
    if not name or name in (".", ".."):
        return False
    return os.path.isfile(os.path.join(str(UPLOAD_DIR), name))


def _blank_unreachable_image_url(image_url, image_path):
    """单条 URL 的死链兜底：目标文件不存在则返回空串，否则原样返回。

    why: 详情接口（GET /api/receipt/{id}）与列表接口共用同一「文件是否真在
    uploads 下」判据，避免两处各写一套 os.stat 逻辑再各自漂移。空 URL 直接透传
    （空 image_path 的单据本就无图，见 public_image_url 的行为变更说明）。
    """
    if not image_url:
        return image_url
    if not _image_url_target_exists(image_path):
        return ""
    return image_url


def _blank_unreachable_image_urls(rows, data):
    """X1 展示层兜底：列表行里指向不存在文件的 image_url 一律置空。

    why: live 库存在 928 条历史 pytest 污染单据（image_path 形如
    '/uploads/img_2.png'、'/uploads/eval_reflow_src_*.png' 或已删除的 pytest 临时
    绝对路径），其 basename 在 uploads/ 下没有对应文件，`public_image_url` 仍会按其
    basename 产出 `/uploads/<name>`（非 Web 格式则产出转码端点 URL），前端渲染即
    404 破图。这里只做展示层兜底：文件不存在时返回空串，字段保留、响应结构不变，
    不删历史数据行、不改 image_path。非 Web 格式源的判据同为「basename 是否有
    文件」—— 端点转码读的就是这个 src，文件缺失时端点必 404。

    性能：只做路径归一 + os.stat，绝不打开/解码图片；且只在 image_url 非空时执行，
    空 image_path 的行（live 库 238 条）零开销。

    刻意不放进 build_row / public_image_url：那两处被详情、上传、Job 回写等路径共用，
    改动会波及「Web 格式 URL 逐字节不变」的既有契约；死链治理只针对展示层（列表入口
    与本文件 get_receipt 详情入口，后者见 Y1），故收在入口。

    逐行逻辑复用 _blank_unreachable_image_url，列表与详情两侧判据保证同源。
    """
    for row, d in zip(rows, data):
        d["image_url"] = _blank_unreachable_image_url(
            d.get("image_url"), getattr(row, "image_path", ""))
    return data


@router.get("/api/receipts")
def list_receipts(request: Request):
    # 列表查看对店员开放（上传/复核需要看到列表）；导出仍限 owner
    require_role("staff")(request)
    desensitized = request.query_params.get("desensitized", "false").lower() == "true"
    rows = db.list_receipt_rows(tenant_id=_tenant_id(request))
    # W2 列表性能修复：列表不再逐条读图补红章，直接返回已落库的 payment_mark。
    # why: 旧实现对本条 payment_mark 为空的单据调 _supplement_payment_mark →
    # contract.payment_mark_from_image → detect_red_stamp，会逐条打开并解码图片；
    # live 库 1600 条中 1487 条为空，一次列表请求触发上千次读图（实测 >2 分 28 秒
    # 未返回，并发时打满线程池连 /api/health 都超时），且前端筛选不减少后端开销。
    # 红章补充是「LLM 漏检的补丁」，语义上属详情级信息：保留在详情接口
    # （GET /api/receipt/{id} 的 _supplement_payment_mark 与 build_detail）。
    # 列表不消费 payment_mark（归档列表「付款」列走 payment_status，付款标记由
    # 详情弹窗从 /api/receipt/{id} 取），故返回已落库值不构成语义退化。
    data = [build_row(r) for r in rows]
    if desensitized:
        for row in rows:
            _mask_sensitive_text = _mask_sensitive
            row.raw_llm = _mask_sensitive_text(row.raw_llm or "")
            row.payment_mark = _mask_sensitive_text(row.payment_mark or "")
            row.supplier_name = _mask_sensitive_text(row.supplier_name or "")
        # 脱敏视图附加 raw_llm / payment_mark 字段（含掩码值）
        data = []
        for row in rows:
            d = build_row(row)
            d["raw_llm"] = row.raw_llm
            d["payment_mark"] = row.payment_mark
            data.append(d)
    # X1：展示层剔除死链（文件不存在的 image_url 置空），两条分支统一收口
    data = _blank_unreachable_image_urls(rows, data)
    return {"status": "success", "data": data}


@router.get("/api/receipts/export")
def export_receipts(request: Request, desensitized: str = "false"):
    """导出收据 CSV。支持 ?desensitized=true 脱敏导出。"""
    require_role("owner")(request)
    is_desens = desensitized.lower() == "true"
    rows = db.list_receipt_rows(tenant_id=_tenant_id(request))
    data = [
        {
            "id": r.id,
            "supplier": _mask_sensitive(r.supplier_name or "") if is_desens else (r.supplier_name or ""),
            "date": r.receipt_date or "",
            "total": r.total_amount or 0.0,
            "status": r.status,
            "doc_form": r.doc_form or "",
        }
        for r in rows
    ]
    df = pd.DataFrame(data)
    buf = io.StringIO()
    df.to_csv(buf, index=False, encoding="utf-8-sig")
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=receipts_export.csv"},
    )


@router.get("/api/receipt/{receipt_id}")
def get_receipt(receipt_id: int, request: Request):
    require_role("staff")(request)
    row = db.get_receipt_row(receipt_id, tenant_id=_tenant_id(request))
    if row is None:
        return JSONResponse(content={"status": "error", "msg": "未找到指定收据"},
                            status_code=404)
    # 印章判定补红章：详情视图补充，避免 payment_mark 空
    if not (row.payment_mark or "").strip():
        row.payment_mark = _supplement_payment_mark(row)
    data = build_detail(row)
    desensitized = request.query_params.get("desensitized", "false").lower() == "true"
    data_only = request.query_params.get("data_only", "false").lower() == "true"
    if desensitized:
        # 脱敏视图附加 raw_llm（与列表脱敏视图保持一致）
        data["raw_llm"] = _mask_sensitive(row.raw_llm or "")
        if "payment_mark" in data and data["payment_mark"]:
            data["payment_mark"] = _mask_sensitive(data["payment_mark"])
        if "supplier_name" in data and data["supplier_name"]:
            data["supplier_name"] = _mask_sensitive(data["supplier_name"])
    # P1 D-P1-3: data_only 调试开关控制 rag_context 可见性；默认隐藏
    if not data_only:
        data.pop("rag_context", None)
    else:
        # data_only 显式开启时补充未持久化字段的提示
        if not data.get("rag_context"):
            data["rag_context"] = getattr(row, "rag_context_json", None) or ""
    # Y1 详情侧死链兜底：live 库 928 条历史 pytest 污染单据「DB 有行、磁盘无文件」，
    # 详情仍会产出 /uploads/<name>（非 Web 格式则产出转码端点 URL），归档弹窗打开时
    # 404 破图/死链（前端只有空值守卫，挡不住非空死链）。判据与 X1 列表侧同源
    # （_blank_unreachable_image_url → _image_url_target_exists，只 os.stat 不解码）。
    # 单条查询，一次 stat 开销可忽略；文件存在时 URL 逐字节不变（含 HEIC 的签名 URL）。
    image_url = _blank_unreachable_image_url(
        public_image_url(row.id, row.image_path, _tenant_id(request)), row.image_path)
    return {"status": "success", "receipt_id": row.id,
            "image_url": image_url,
            "data": data}


# -------------------------------------------------------------
# 保存（乐观锁 version）
# -------------------------------------------------------------
class SaveEditedBody(BaseModel):
    receipt_id: Optional[int] = None
    supplier_name: str = ""
    date: str = ""
    sheet_name: str = ""
    total_amount: float = 0.0
    items: list = []
    settlement_type: Optional[str] = None
    department_id: Optional[int] = None
    payment_mark: str = ""
    doc_form: str = ""
    layout_type: str = ""
    version: Optional[int] = None
    currency: str = "HKD"
    discount_amount: float = 0.0
    delivery_fee: float = 0.0
    deposit_amount: float = 0.0
    rounding_adjustment: float = 0.0
    # Gap 9 / Gap 6：店员可修正的附加费用与注记（缺省不传 → 默认值，不 422）
    service_fee: float = 0.0
    tax_amount: float = 0.0
    adjustment_notes: list = []
    payment_evidence: str = ""
    source: Optional[str] = "manual"


def _recompute_math_warnings(body: "SaveEditedBody", currency: str) -> list:
    """F5 门禁联动：save_edited 用店员提交值（而非仅识别值）重算算术门禁。

    用提交的费用字段（service_fee/tax_amount 等）构造契约对象喂 math_engine，
    结果落 math_warnings_json 供复核界面提示；纯建议性，不阻断保存主链路。
    任何构造失败（如手工单字段不全）返回 []，不影响保存。
    """
    try:
        from app.models import DocForm, ReceiptData, ReceiptItem
        from app.services import math_engine
        try:
            doc = DocForm(body.doc_form) if body.doc_form else DocForm.PRINTED
        except ValueError:
            doc = DocForm.PRINTED
        items = []
        for it in body.items or []:
            if not isinstance(it, dict):
                continue
            actual_qty = it.get("actual_qty")
            items.append(ReceiptItem(
                name=str(it.get("name", "") or ""),
                qty=float(it.get("quantity", 0) or 0),
                unit=str(it.get("unit", "") or ""),
                unit_price=float(it.get("unit_price", 0) or 0),
                amount=float(it.get("amount", 0) or 0),
                is_void=bool(it.get("is_void", 0) or 0),
                actual_qty=(float(actual_qty) if actual_qty not in (None, "") else None),
            ))
        data = ReceiptData(
            doc_form=doc,
            vendor=body.supplier_name or "通用供应商",
            date=body.date or "",
            items=items,
            total=float(body.total_amount or 0),
            discount_amount=float(body.discount_amount or 0),
            deposit_amount=float(body.deposit_amount or 0),
            delivery_fee=float(body.delivery_fee or 0),
            service_fee=float(body.service_fee or 0),
            tax_amount=float(body.tax_amount or 0),
            rounding_adjustment=float(body.rounding_adjustment or 0),
            adjustment_notes=[str(n) for n in (body.adjustment_notes or [])],
            payment_marked=(body.payment_mark or "").strip() == "已付款",
            payment_evidence=str(body.payment_evidence or ""),
            currency=currency,
            confidence=1.0,
        )
        return math_engine.validate_and_report(data)
    except Exception as e:  # noqa: BLE001 门禁联动失败不阻断保存
        import logging
        logging.getLogger("api_receipts").warning(f"[WARN] save_edited 算术门禁重算失败: {e}")
        return []


@router.post("/api/save_edited")
def save_edited(body: SaveEditedBody, request: Request):
    require_role("staff")(request)
    account = getattr(request.state, "account", {})
    who = account.get("email", "unknown")
    tenant_id = _tenant_id(request)

    # 初始化，新建手工单路径不会有历史数据
    row = None
    old_items = []
    is_auto = (body.source == "auto")
    target_status = "parsed" if is_auto else "edited"
    action_type = "auto_save" if is_auto else "save_edited"
    action_details = "AI自动识别落库" if is_auto else "[店员手工修改/保存]"

    if body.receipt_id is None:
        # 新建手工单
        rid = db.create_receipt(supplier_name=body.supplier_name or "通用供应商",
                                status=target_status, tenant_id=tenant_id)
        db.append_audit_log(rid, who, action_type, "receipt", None, rid, details=action_details)
    else:
        rid = body.receipt_id
        row = db.get_receipt_row(rid, tenant_id=tenant_id)
        if row is None:
            return JSONResponse(content={"status": "error", "msg": "未找到指定收据"},
                                status_code=404)
        ver_err = _version_error(body.version, row, "保存")
        if ver_err is not None:
            return ver_err
        old_status = row.status
        # P14/P15：解析后自动保存（source=auto）是识别主链路的正常回写，不得把
        # 门禁带警告的 parsed_with_warnings 静默降级为 parsed，否则新状态刚落库就被抹掉。
        if is_auto and old_status == "parsed_with_warnings":
            target_status = "parsed_with_warnings"
        old_items = db.get_receipt_items(rid, tenant_id=tenant_id)
        db.append_audit_log(rid, who, action_type, "status", old_status, target_status, details=action_details)

    if body.settlement_type is None and body.receipt_id is not None:
        return {"status": "error", "code": "SETTLEMENT_REQUIRED",
                "msg": "缺少结算方式"}

    items_raw = []
    for it in body.items or []:
        items_raw.append({
            "name": it.get("name", ""), "raw_name": it.get("raw_name", it.get("name", "")),
            "quantity": float(it.get("quantity", 0) or 0),
            "unit": it.get("unit", "") or "", "raw_unit": it.get("raw_unit", it.get("unit", "")) or "",
            "unit_price": float(it.get("unit_price", 0) or 0),
            "amount": float(it.get("amount", 0) or 0),
            "sku_id": it.get("sku_id"), "cost_center_id": it.get("cost_center_id"),
            # P11：与识别落库同口径——前端未提交该键（复核台该行本就没有置信度读数）
            # 时保留 None，不再兜底 0.5，避免把「无数据」伪装成「置信度 0.5」。
            "confidence": sanitize_nan(it.get("confidence"), default=None),
            "matched": int(bool(it.get("sku_id"))),
            "price_anomaly": int(it.get("price_anomaly", 0) or 0),
            "price_anomaly_direction": it.get("price_anomaly_direction", ""),
            "price_diff_percent": float(it.get("price_diff_percent", 0) or 0),
            "unit_conversion_warning": it.get("unit_conversion_warning", ""),
            "fuzzy_candidates": it.get("fuzzy_candidates", []),
            "entity_candidates": it.get("entity_candidates", []),
            "is_void": int(bool(it.get("is_void", 0) or 0)),
            "actual_qty": it.get("actual_qty"),
            # Gap E1 / T7：字段级证据透传（前端未提交时为 None；落库层再归一兜底）
            "evidence": it.get("evidence"),
        })

    # 币种白名单校验
    _allowed_currencies = {"HKD", "CNY", "USD", "EUR", "JPY", "GBP", "MOP", "SGD"}
    cur = (body.currency or "HKD").strip().upper()
    if cur not in _allowed_currencies:
        cur = "HKD"
    # Gap 6：手写注记归一（list[str]，空行/空白剔除）
    _notes = [str(n).strip() for n in (body.adjustment_notes or [])
              if str(n).strip()]
    # F5 门禁联动：以店员提交值重算算术门禁（建议性，不阻断）
    _math_warnings = _recompute_math_warnings(body, cur)
    # P14/P15：带警告单据的自动保存沿用识别期门禁留痕——提交值未必能复现
    # 契约类门禁错误，重算为空时保留原警告，避免自动保存把门禁留痕抹掉。
    if is_auto and row is not None and row.status == "parsed_with_warnings" and not _math_warnings:
        try:
            _prev_warnings = json.loads(row.math_warnings_json or "[]")
        except Exception:
            _prev_warnings = []
        if isinstance(_prev_warnings, list) and _prev_warnings:
            _math_warnings = _prev_warnings
    db.update_receipt(
        rid,
        supplier_name=body.supplier_name or "通用供应商",
        receipt_date=body.date or "",
        sheet_name=body.sheet_name or (body.date or "")[:7],
        total_amount=float(body.total_amount or 0),
        settlement_type=body.settlement_type,
        payment_mark=body.payment_mark or "",
        doc_form=body.doc_form or "",
        layout_type=body.layout_type or "",
        department_id=body.department_id,
        status=target_status,
        version=body.version + 1 if body.version is not None else 1,
        currency=cur,
        # Gap 9 / Gap 6：店员修正的费用/注记/付款证据落库（回读经 build_detail）
        service_fee=float(body.service_fee or 0),
        tax_amount=float(body.tax_amount or 0),
        adjustment_notes_json=json.dumps(_notes, ensure_ascii=False),
        payment_evidence=str(body.payment_evidence or "").strip(),
        math_warnings_json=json.dumps(_math_warnings, ensure_ascii=False),
    )
    db.set_receipt_items(rid, items_raw, tenant_id=tenant_id)

    # 字段级审计：仅编辑既有单据时才对比 old/new（新建手工单无历史可比，auto_save 不记 manual diff）
    _manual_changed = False
    if not is_auto and body.receipt_id is not None and row is not None:
        for old_it, new_it in zip(old_items, items_raw):
            if old_it.get("unit_price") != new_it.get("unit_price"):
                db.append_audit_log(rid, who, "save_edited", "unit_price",
                                    old_it.get("unit_price"), new_it.get("unit_price"))
                _manual_changed = True
            if old_it.get("amount") != new_it.get("amount"):
                db.append_audit_log(rid, who, "save_edited", "amount",
                                    old_it.get("amount"), new_it.get("amount"))
                _manual_changed = True
        if row.supplier_name != body.supplier_name:
            db.append_audit_log(rid, who, "save_edited", "supplier_name",
                                row.supplier_name, body.supplier_name)
            _manual_changed = True
        if row.total_amount != body.total_amount:
            db.append_audit_log(rid, who, "save_edited", "total_amount",
                                row.total_amount, body.total_amount)
            _manual_changed = True
        # T8 非对称淘汰（覆写 -0.12 口径）：店员真的改了 AI 预填值 → 该供应商
        # 活跃记忆扣分，低于归档阈值自动 archived 停止注入。失败不阻断保存。
        if _manual_changed:
            try:
                db.apply_vendor_memory_override_penalty(
                    row.supplier_name or "", tenant_id=tenant_id)
            except Exception as _e:
                import logging as _logging
                _logging.getLogger("api_receipts").warning(
                    f"[WARN] 记忆衰减扣分失败(不阻断): {_e}")

    # 埋点（规范事件 #7 receipt_review_submitted）：行级三类 diff + FER，分析用途
    if not is_auto and body.receipt_id is not None and row is not None:
        try:
            import json as _jdiff
            _ai_prefill = _jdiff.loads(row.ai_prefill_json or "{}")
            _diff = compute_review_diff(_ai_prefill.get("items", []), items_raw)
            _edited = sum(_diff["field_mod_counts"].values()) + _diff["sku_changed"]
            _diff["fer_rate"] = round(
                _edited / max(1, _diff["total_final"] * 6), 4)
            # T6 Gap A5：人工保存与 AI 预填存在差异 -> 回流为评测候选（user_edit）。
            # ai_candidate 取 AI 预填原版（GT 供 promote 复制）；幂等去重在 db 层；
            # 任何失败不阻断保存主链路。
            if _edited > 0:
                try:
                    from app.chains.supervisor import maybe_create_eval_candidate
                    _gt = {
                        "supplier_name": _ai_prefill.get("vendor", ""),
                        "date": _ai_prefill.get("date", ""),
                        "total_amount": _ai_prefill.get("total", 0) or 0,
                        "items": [
                            {"name": it.get("name", ""),
                             "quantity": it.get("qty", 0) or 0,
                             "unit": it.get("unit", ""),
                             "unit_price": it.get("unit_price", 0) or 0,
                             "amount": it.get("amount", 0) or 0}
                            for it in (_ai_prefill.get("items", []) or [])
                            if isinstance(it, dict)
                        ],
                    }
                    maybe_create_eval_candidate(
                        rid, "user_edit", doc_form=str(row.doc_form or ""),
                        ai_candidate=_gt,
                        note="save_edited diff: %s" % _jdiff.dumps(
                            _diff.get("field_mod_counts", {}), ensure_ascii=False)[:300])
                except Exception as _e:
                    import logging as _logging
                    _logging.getLogger("api_receipts").warning(
                        f"[WARN] user_edit 评测候选回流失败: {_e}")
            _track_event(account, getattr(request.state, "session_id", ""),
                         "receipt_review_submitted", receipt_id=rid,
                         properties=_diff,
                         grp="treatment" if (row.use_grey or 0) else "control")
        except Exception as e:
            import logging
            logging.getLogger("api_receipts").warning(f"[WARN] review diff 埋点失败: {e}")

    row = db.get_receipt_row(rid, tenant_id=tenant_id)
    _track_event(account, getattr(request.state, "session_id", "batch"),
                 "upload", receipt_id=rid,
                 properties={"mode": "batch"})
    # C6 ai_decision_log：auto_save 记录自动入库决策；save_edited 回填 user_value（per-field diff）
    if is_auto:
        _track_ai_decision(account, rid, row=row,
            decision_type="auto_save",
            field_path="overall",
            ai_value={"status": "parsed", "total_amount": float(body.total_amount or 0)},
            engine="pipeline")
    elif body.receipt_id is not None:
        ai_row = db.get_receipt_row(rid, tenant_id=tenant_id)
        if ai_row:
            try:
                import json as _j
                ai_prefill = _j.loads(ai_row.ai_prefill_json or "{}")
                ai_items = ai_prefill.get("items", [])
                for i, new_it in enumerate(items_raw):
                    if i < len(ai_items):
                        ai_val = ai_items[i].get("unit_price", "")
                        user_val = new_it.get("unit_price", "")
                        if str(ai_val) != str(user_val):
                            _track_ai_decision(account, rid, row=ai_row,
                                decision_type="edit",
                                field_path=f"items[{i}].unit_price",
                                ai_value=ai_val, user_value=user_val,
                                adopted=0, engine="human")
            except Exception as e:
                import logging
                logging.getLogger("api_receipts").warning(f"[WARN] 记录编辑 AI 决策失败: {e}")
    return {"status": "success", "receipt_id": rid, "version": row.version}


# -------------------------------------------------------------
# 状态流转
# -------------------------------------------------------------
class ApproveBody(BaseModel):
    version: Optional[int] = None


@router.post("/api/receipt/{receipt_id}/approve")
def approve_receipt(receipt_id: int, body: ApproveBody, request: Request):
    require_role("owner")(request)
    account = getattr(request.state, "account", {})
    who = account.get("email", "unknown")
    row = db.get_receipt_row(receipt_id, tenant_id=_tenant_id(request))
    if row is None:
        return JSONResponse(content={"status": "error", "msg": "未找到指定收据"},
                            status_code=404)
    if row.status not in ("parsed", "parsed_with_warnings", "edited"):
        return JSONResponse(
            content={"status": "error", "code": "INVALID_STATUS",
                     "msg": f"仅 status='parsed'/'parsed_with_warnings'/'edited' 的单据可 approve，当前 status='{row.status}'"},
            status_code=409)
    ver_err = _version_error(body.version, row, "审核")
    if ver_err is not None:
        return ver_err

    old_status = row.status
    # 幂等入账：写 SKU 库存流水 + 累计（P1-1: 入库链路租户透传，SKU 匹配/建档/流水限定本租户）
    from app.services.inventory import apply_receipt_to_inventory
    apply_receipt_to_inventory(row, tenant_id=_tenant_id(request))
    db.update_receipt(receipt_id, status="approved", version=row.version + 1)
    db.append_audit_log(receipt_id, who, "approve_receipt", "status", old_status, "approved")
    row = db.get_receipt_row(receipt_id, tenant_id=_tenant_id(request))
    # 供应商自动建档（approve 后成为正式供应商，归入当前租户）
    if row.supplier_name and db.find_supplier_by_name(row.supplier_name, tenant_id=_tenant_id(request)) is None:
        db.create_supplier(row.supplier_name, tenant_id=_tenant_id(request))
    # 回写 VendorMemory（只认正向信号：老板批准）
    # T3 Gap B1：透传 tenant_id 与触发 receipt_id（source_ref 可回溯）
    from app.services.rag import ingest_memory
    items_text = "\n".join(f"- {i['name']} {i['quantity']}{i['unit']} @{i['unit_price']} = {i['amount']}"
                           for i in db.get_receipt_items(receipt_id, tenant_id=_tenant_id(request)))
    ingest_memory(row.supplier_name, items_text, notes=f"版式：{row.doc_form}",
                  tenant_id=_tenant_id(request), receipt_id=receipt_id)
    # C6 ai_decision_log：approve 回填最终确认 + 幻觉判定
    try:
        import json as _j
        ai_prefill = _j.loads(row.ai_prefill_json or "{}")
        ai_items = ai_prefill.get("items", [])
        final_items = db.get_receipt_items(receipt_id, tenant_id=_tenant_id(request))
        for i, fi in enumerate(final_items):
            if i < len(ai_items):
                ai_val = ai_items[i].get("unit_price", "")
                user_val = fi.get("unit_price", "")
                adopted = 1 if str(ai_val) == str(user_val) else 0
                _track_ai_decision(account, receipt_id, row=row,
                    decision_type="approve",
                    field_path=f"items[{i}].unit_price",
                    ai_value=ai_val, user_value=user_val,
                    adopted=adopted, engine="human")
    except Exception as e:
        import logging
        logging.getLogger("api_receipts").warning(f"[WARN] 记录审批 AI 决策失败: {e}")
    # 埋点（规范事件 #8 receipt_approved，北极星终态）：e2e 耗时
    try:
        from datetime import datetime as _dt
        _e2e = None
        if row.created_at:
            _c = _dt.fromisoformat(str(row.created_at).replace("Z", "+00:00"))
            _n = _dt.fromisoformat(db.now_iso().replace("Z", "+00:00"))
            _e2e = int((_n - _c).total_seconds() * 1000)
        _track_event(account, getattr(request.state, "session_id", ""),
                     "receipt_approved", receipt_id=receipt_id,
                     properties={"e2e_ms": _e2e, "total_amount": row.total_amount or 0.0})
    except Exception:
        pass
    return {"status": "success", "version": row.version}


@router.post("/api/receipt/{receipt_id}/flag")
def flag_receipt(receipt_id: int, request: Request):
    require_role("owner")(request)
    account = getattr(request.state, "account", {})
    who = account.get("email", "unknown")
    row = db.get_receipt_row(receipt_id, tenant_id=_tenant_id(request))
    if row is None:
        return JSONResponse(content={"status": "error", "msg": "未找到指定收据"},
                            status_code=404)
    old_status = row.status
    if old_status == "flagged":
        return {"status": "success",
                "msg": f"单据 #{receipt_id} 已处于 flagged 状态，幂等跳过。"}
    # 对齐完整版：uploaded/parsing/parsed/parsed_with_warnings/edited → flagged；approved 409
    if old_status not in ("uploaded", "parsing", "parsed", "parsed_with_warnings", "edited"):
        return JSONResponse(
            content={"status": "error",
                     "msg": f"非法状态转移 [{old_status}] → [flagged]：仅 uploaded/parsing/parsed/parsed_with_warnings/edited 单据可标记异常"
                            f"（已审核单据的问题修正必须走冲销路径）。"},
            status_code=409)
    db.update_receipt(receipt_id, status="flagged")
    db.append_audit_log(receipt_id, who, "flag_receipt", "status", old_status, "flagged")
    _track_event(account, getattr(request.state, "session_id", ""),
                 "receipt_flagged", receipt_id=receipt_id,
                 properties={"old_status": old_status})
    return {"status": "success", "msg": f"单据 #{receipt_id} 已标记为异常"}


@router.post("/api/receipt/{receipt_id}/retry")
def retry_receipt(receipt_id: int, request: Request):
    require_role("staff")(request)
    row = db.get_receipt_row(receipt_id, tenant_id=_tenant_id(request))
    if row is None:
        return JSONResponse(content={"status": "error", "msg": "未找到指定收据"},
                            status_code=404)
    gate = _assert_retryable(row, action="重试", verb="重跑识别")
    if gate is not None:
        return gate
    if not row.image_path or not os.path.exists(row.image_path):
        return JSONResponse(
            content={"status": "error",
                     "msg": f"原图缺失（{row.image_path}），无法重试；请重新上传或转手工录入。"},
            status_code=400)
    # P0-1: 重试链路租户透传（row 已按租户校验归属）
    job_id, _ = start_recognition_job(row.image_path, receipt_id=receipt_id,
                                      tenant_id=_tenant_id(request))
    return {"status": "queued", "job_id": job_id, "receipt_id": receipt_id,
            "version": row.version}


@router.post("/api/receipt/{receipt_id}/replace-image")
async def replace_receipt_image(
    receipt_id: int,
    request: Request,
    receipt: UploadFile = File(...),
    force: str = Form("false"),  # true → 用户已确认继续，跳过极模糊硬拦截
):
    """挽回动作「重拍」：新图替换原图并重跑识别（原单据保留，区别于批次换图新建单据）。

    状态门与 /retry 相同；旧图文件保留（可审计），换图动作落 audit_logs_json
    与 image_replaced 埋点（含 old_image）。
    """
    require_role("staff")(request)
    account = getattr(request.state, "account", {})
    who = account.get("email", "unknown")
    row = db.get_receipt_row(receipt_id, tenant_id=_tenant_id(request))
    if row is None:
        return JSONResponse(content={"status": "error", "msg": "未找到指定收据"},
                            status_code=404)
    gate = _assert_retryable(row, action="换图重跑", verb="重拍")
    if gate is not None:
        return gate

    # 与 /api/upload 同源的画质拦截：空文件/过小 400 + 极模糊硬拦截（force 可绕过）
    image_path = _save_upload(receipt)
    file_size = os.path.getsize(image_path) if os.path.exists(image_path) else 0
    if file_size < 30:
        if os.path.exists(image_path):
            os.remove(image_path)
        return _image_quality_reject(
            "图像模糊/过暗，请到更亮处重拍，无需打字，点框选裁剪重试（图片损坏或体积过小）",
            ["image_empty_or_corrupted"])
    blur_score = _laplacian_variance(image_path)
    is_force = (force.lower() == "true") or (request.query_params.get("force", "").lower() == "true")
    if blur_score is not None and blur_score < _blur_threshold():
        if not is_force:
            if os.path.exists(image_path):
                try:
                    os.remove(image_path)
                except Exception:
                    pass
            return _image_quality_reject(
                "图像模糊/过暗，请到更亮处重拍，无需打字，点框选裁剪重试",
                ["image_blur"],
                blur_score=round(float(blur_score), 2), confidence=0.35)

    old_image_path = row.image_path or ""
    old_image_name = os.path.basename(old_image_path) if old_image_path else ""
    new_image_name = os.path.basename(image_path)
    # 换图落库（旧图文件保留不删，可审计）+ 审计 + 埋点，随后复用原单据重跑识别
    db.update_receipt(receipt_id, image_path=image_path)
    # W5：旧源文件的预览缓存已无请求路径可达，定向清理（旧源原图本身按设计保留可
    # 审计，绝不删除）；仅当确认已无任何单据（含软删）引用该源文件时才删缓存。
    old_src = _resolve_upload_file(old_image_path)
    if old_src is not None:
        _cleanup_unreferenced_preview_cache(str(old_src))
    db.append_audit_log(receipt_id, who, "replace_image", "image_path",
                        old_image_name, new_image_name)
    _track_event(account, getattr(request.state, "session_id", ""),
                 "image_replaced", receipt_id=receipt_id,
                 properties={"old_image": old_image_name, "trigger": "retake"},
                 tenant_id=_tenant_id(request))
    job_id, _ = start_recognition_job(image_path, receipt_id=receipt_id,
                                      tenant_id=_tenant_id(request))
    return {"status": "queued", "job_id": job_id, "receipt_id": receipt_id,
            "version": row.version,
            "image_url": public_image_url(receipt_id, image_path, _tenant_id(request))}


@router.post("/api/receipt/{receipt_id}/convert_manual")
def convert_manual(receipt_id: int, request: Request):
    # 对齐完整版：转手工录入改写单据形态与状态，需 owner
    require_role("owner")(request)
    row = db.get_receipt_row(receipt_id, tenant_id=_tenant_id(request))
    if row is None:
        return JSONResponse(content={"status": "error", "msg": "未找到指定收据"},
                            status_code=404)
    old_status = row.status
    if old_status not in ("uploaded", "parsed", "parsed_with_warnings", "error"):
        return JSONResponse(
            content={"status": "error",
                     "msg": f"当前状态 [{old_status}] 不允许转手工录入：仅 uploaded/parsed/parsed_with_warnings/error 单据可转换。"},
            status_code=409)
    db.update_receipt(receipt_id, status="edited", doc_form="manual_entry")
    row = db.get_receipt_row(receipt_id)
    return {"status": "success", "receipt_id": receipt_id,
            "image_url": public_image_url(row.id, row.image_path, _tenant_id(request)),
            "version": row.version,
            "data": build_detail(row),
            "msg": f"单据 #{receipt_id} 已转为手工录入，请补全明细后保存"}


@router.post("/api/receipt/{receipt_id}/discard")
def discard_receipt(receipt_id: int, request: Request):
    require_role("staff")(request)
    account = getattr(request.state, "account", {})
    who = account.get("email", "unknown")
    row = db.get_receipt_row(receipt_id, tenant_id=_tenant_id(request))
    if row is None:
        return JSONResponse(content={"status": "error", "code": "RECEIPT_NOT_FOUND",
                                     "msg": "单据不存在或已被删除"}, status_code=404)
    if row.status == "approved":
        return JSONResponse(
            content={"status": "error", "code": "CANNOT_DISCARD_APPROVED",
                     "msg": "该单据已审核确认并入账，无法直接放弃删除；请使用冲销功能修正。"},
            status_code=409)
    # 软删除：保留单据（含原图），进入回收站
    # W5：此处刻意不清理 `<stem>_web.jpg` 预览缓存 —— 软删单据仍持有 image_path，
    # 经回收站恢复后仍要展示原图，缓存依旧被引用；真正的无主缓存由启动期兜底扫描回收。
    db.soft_delete_receipt(receipt_id)
    db.append_audit_log(receipt_id, who, "discard_receipt", "deleted_at", None, "now")
    return {"status": "success", "receipt_id": receipt_id,
            "msg": "已放弃该单据（进入回收站，可在管理后台恢复）"}


class PayDateBody(BaseModel):
    expected_pay_date: Optional[str] = None


@router.post("/api/receipt/{receipt_id}/pay_date")
def set_pay_date(receipt_id: int, body: PayDateBody, request: Request):
    require_role("owner")(request)
    row = db.get_receipt_row(receipt_id, tenant_id=_tenant_id(request))
    if row is None:
        return JSONResponse(content={"status": "error", "msg": "未找到指定收据"},
                            status_code=404)
    # 对齐完整版：null/空 = 清空；非法日期 400；不 bump version
    import re as _re
    from datetime import datetime as _dt
    new_date = None
    raw = (body.expected_pay_date or "").strip()
    if raw:
        if not _re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
            return JSONResponse(
                content={"status": "error",
                         "msg": f"expected_pay_date 必须为 YYYY-MM-DD 格式日期，当前值：'{raw}'"},
                status_code=400)
        try:
            _dt.strptime(raw, "%Y-%m-%d")
        except ValueError:
            return JSONResponse(
                content={"status": "error",
                         "msg": f"expected_pay_date '{raw}' 不是真实存在的日期"},
                status_code=400)
        new_date = raw
    row = db.set_expected_pay_date(receipt_id, new_date)
    from app.services.receipt_utils import build_detail
    return {"status": "success", "receipt_id": receipt_id,
            "expected_pay_date": row.expected_pay_date,
            "payment_status": build_detail(row).get("payment_status"),
            "version": row.version}


# -------------------------------------------------------------
# 采纳 AI 建议（阶段 1：AI 可见性与信任）
# 前端在「AI 识别 vs 当前值」对照面板点击「采纳 AI」按钮时调用，
# 记录一次 ai_suggestion_adopted 审计事件（字段级，含 old/new）。
# 仅记录审计日志，不改动单据其他字段（前端侧已把表单值同步为 AI 值）。
# -------------------------------------------------------------
class AdoptAIBody(BaseModel):
    field: str
    old: Optional[object] = None
    new: Optional[object] = None
    # 兼容前端/测试的 ai_value/user_value 命名
    ai_value: Optional[object] = None
    user_value: Optional[object] = None
    # 乐观锁：采纳 AI 建议也是写操作，须校验 version
    version: Optional[object] = None


@router.post("/api/receipt/{receipt_id}/adopt-ai")
def adopt_ai(receipt_id: int, body: AdoptAIBody, request: Request):
    require_role("staff")(request)
    row = db.get_receipt_row(receipt_id, tenant_id=_tenant_id(request))
    if row is None:
        return JSONResponse(content={"status": "error", "msg": "未找到指定收据"},
                            status_code=404)
    # 乐观锁校验（缺失 → 400 VERSION_REQUIRED；不匹配 → 409 VERSION_CONFLICT）
    ver_err = _version_error(body.version, row, "采纳 AI")
    if ver_err is not None:
        return ver_err
    account = getattr(request.state, "account", {})
    who = account.get("email", "unknown")
    field = body.field or ""
    # 字段名安全：只允许 items[n].field 格式，避免注入
    m = re.match(r"^items\[(\d+)\]\.(qty|quantity|unit_price|amount|unit)$", field)
    if not m:
        return JSONResponse(
            content={"status": "error",
                     "msg": f"非法 field 值：'{field}'（须形如 items[0].qty）"},
            status_code=400)
    idx = int(m.group(1))
    # 越界下标：items[N] 必须在 0..len(row.items)-1 范围内
    items = db.get_receipt_items(receipt_id, tenant_id=_tenant_id(request))
    if idx < 0 or idx >= len(items):
        return JSONResponse(
            content={"status": "error",
                     "msg": f"items 下标越界：items[{idx}]，当前单据仅有 {len(items)} 条明细（0..{max(len(items)-1, 0)}）"},
            status_code=400)
    new_val = body.new if body.new is not None else body.ai_value
    old_val = body.old if body.old is not None else body.user_value
    # XSS 防护：对字符串字段做 html.escape，避免 payload 写入 audit_logs_json 后被 innerHTML 渲染
    import html as _html
    def _safe(v):
        return _html.escape(str(v)) if isinstance(v, str) else v
    db.append_audit_log(
        receipt_id,
        who, "ai_suggestion_adopted",
        field, _safe(old_val), _safe(new_val),
    )
    return {"status": "success", "receipt_id": receipt_id,
            "field": field, "new": new_val, "version": row.version}


# -------------------------------------------------------------
# FR-8 / FR-9 反馈飞轮：点赞 / 点踩 + 文本反馈窗
# POST /api/receipt/{id}/feedback 接收 {like, comment, item_index}
# 落库 receipt_feedback，租户隔离 Chroma 沉淀
# （阈值 FEEDBACK_DISTILL_THRESHOLD=3 次连续点踩提炼）
# -------------------------------------------------------------
class FeedbackBody(BaseModel):
    like: Optional[object] = None
    comment: Optional[str] = None
    item_index: Optional[int] = None
    tenant_id: Optional[str] = None


@router.post("/api/receipt/{receipt_id}/feedback")
def submit_feedback(receipt_id: int, body: FeedbackBody, request: Request):
    require_role("staff")(request)
    # 租户口径（P3-4 收敛）：服务端 header X-Tenant-Id 优先，body.tenant_id 仅在
    # 无 header 时兜底兼容（老客户端/脚本）。归属校验与 receipt_feedback 落库
    # 必须使用同一解析值，且客户端 body 不得覆盖服务端 header 用于归属判定。
    # 前端（demo/static/js/main.js）目前对两处传同值（localStorage demo_tenant_id），
    # 本口径变更对其无行为影响。
    tenant_id = (request.headers.get("X-Tenant-Id")
                 or request.headers.get("x-tenant-id")
                 or body.tenant_id or "default")
    tenant_id = str(tenant_id).strip() or "default"
    # 单据归属校验：跨租户单据视为不存在（Gap E2）
    row = db.get_receipt_row(receipt_id, tenant_id=tenant_id)
    if row is None:
        return JSONResponse(content={"status": "error", "msg": "未找到指定收据"},
                            status_code=404)
    # like 归一：前端传 like=true/false 或 1/-1 或 "like"/"dislike"
    like_raw = body.like
    comment_raw = body.comment if body.comment is not None else ""
    # item_index 校验：若传了则必须在明细范围内
    item_idx = body.item_index
    if item_idx is not None:
        try:
            item_idx = int(item_idx)
        except Exception:
            return JSONResponse(content={"status": "error", "msg": "item_index 必须为整数"},
                                status_code=400)
        items = db.get_receipt_items(receipt_id, tenant_id=tenant_id)
        if item_idx < 0 or item_idx >= len(items):
            return JSONResponse(
                content={"status": "error",
                         "msg": f"item_index 越界：{item_idx}，当前仅 {len(items)} 行"},
                status_code=400)
    # like 至少需提供一项反馈（点赞/点踩 或 文本非空）
    from app.models import FEEDBACK_COMMENT_MAXLEN, FEEDBACK_DISTILL_THRESHOLD
    comment_str = str(comment_raw or "").strip()
    if like_raw is None and not comment_str:
        return JSONResponse(content={"status": "error", "msg": "请提供点赞/点踩或文本反馈"},
                            status_code=400)
    # comment 长度限制（db 层再截断，提前校验友好提示）
    if len(comment_str) > FEEDBACK_COMMENT_MAXLEN:
        return JSONResponse(
            content={"status": "error",
                     "msg": f"反馈文本过长（最多 {FEEDBACK_COMMENT_MAXLEN} 字）"},
            status_code=400)
    # qualityWarnings 快照：取当前单据的 quality_warnings
    import json as _json
    qw = []
    try:
        qw = _json.loads(row.quality_warnings_json or "[]")
    except Exception:
        qw = []

    vendor_name = row.supplier_name or ""
    # 落库
    fb = db.upsert_receipt_feedback(
        receipt_id=receipt_id,
        like=like_raw,
        comment=comment_str,
        item_index=item_idx,
        tenant_id=tenant_id,
        vendor=vendor_name,
        quality_warnings=qw,
    )
    # 审计
    account = getattr(request.state, "account", {})
    who = account.get("email", "unknown")
    import html as _html
    safe_comment = _html.escape(comment_str) if comment_str else ""
    db.append_audit_log(receipt_id, who, "feedback", f"item[{item_idx}]" if item_idx is not None else "receipt",
                        "", {"like": fb["like"], "comment": safe_comment[:200]})

    # FR-9 连续点踩提炼：阈值 FEEDBACK_DISTILL_THRESHOLD（models.py 常量，默认 3）
    # 同供应商同租户最近 N 次均为点踩则沉淀到 Chroma 租户隔离记忆
    distilled = False
    distill_info = None
    if fb["like"] == -1:
        try:
            if db.should_distill_vendor_memory(vendor_name, tenant_id):
                from app.services.rag import ingest_feedback_memory
                # T10：蒸馏阈值与判定同口径走 settings（缺省 FEEDBACK_DISTILL_THRESHOLD）
                try:
                    from app.services import settings_service
                    _distill_n = settings_service.get_int(
                        "feedback_distill_threshold", FEEDBACK_DISTILL_THRESHOLD)
                except Exception:
                    _distill_n = FEEDBACK_DISTILL_THRESHOLD
                # T3 Gap B1：source_ref 记录触发点踩的 receipt_id 列表（可回溯）
                _src_ids = []
                try:
                    _recent = db.list_receipt_feedbacks(vendor=vendor_name, tenant_id=tenant_id)
                    _src_ids = [str(f.get("receipt_id")) for f in _recent[:_distill_n]
                                if f.get("like") == -1 and f.get("receipt_id") is not None]
                except Exception:
                    _src_ids = []
                content = ingest_feedback_memory(vendor_name, comment_str, qw, tenant_id,
                                                 source_receipt_ids=_src_ids)
                distilled = True
                distill_info = content[:200]
                db.append_audit_log(receipt_id, "system", "feedback_distilled", vendor_name, "", distill_info)
        except Exception as e:
            import logging
            logging.getLogger("api_receipts").warning(f"[WARN] 反馈沉淀失败: {e}")

    return {"status": "success", "receipt_id": receipt_id, "feedback": fb,
            "distilled": distilled, "distill_info": distill_info}


@router.get("/api/receipt/{receipt_id}/feedback")
def list_feedback(receipt_id: int, request: Request):
    require_role("staff")(request)
    row = db.get_receipt_row(receipt_id, tenant_id=_tenant_id(request))
    if row is None:
        return JSONResponse(content={"status": "error", "msg": "未找到指定收据"},
                            status_code=404)
    tenant_id = request.headers.get("X-Tenant-Id") or request.headers.get("x-tenant-id")
    fbs = db.list_receipt_feedbacks(receipt_id=receipt_id)
    # 租户过滤：若传了租户则只返回该租户的
    if tenant_id:
        fbs = [f for f in fbs if f.get("tenant_id") == tenant_id]
    return {"status": "success", "receipt_id": receipt_id, "feedbacks": fbs}


# -------------------------------------------------------------
# 前端通用埋点入口（11-组件Spec-全链路埋点与体验反馈体系 §6）
# -------------------------------------------------------------
class TrackBody(BaseModel):
    event_type: str
    receipt_id: Optional[int] = None
    session_id: Optional[str] = None  # Spec §3.0 公共上下文字段：前端 localStorage 生成
    properties: Optional[dict] = None


@router.post("/api/track")
def track_event(body: TrackBody, request: Request):
    """前端行为埋点统一入口：失败静默，始终 200，不阻塞业务。"""
    require_role("staff")(request)
    account = getattr(request.state, "account", {})
    tenant_id = _tenant_id(request)
    _track_event(account, body.session_id or getattr(request.state, "session_id", ""),
                 body.event_type, receipt_id=body.receipt_id,
                 properties=body.properties or {},
                 tenant_id=tenant_id)
    return {"status": "ok"}

