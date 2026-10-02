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
from app.auth import require_role, resolve_request_tenant, resolve_tenant
from app.services import image_web
# P3-T2（K3）：用户修正 → 候选事实抽取。三个模块都是纯逻辑层（不 import 引擎层、
# 不碰向量库），故可放在模块级；`memory_extract` 内部对 `app.llm` 是惰性 import。
from app.services import memory_consolidate, memory_extract, memory_facts, settings_service
from app.services.contract import payment_mark_from_image, sanitize_nan
from app.services.receipt_utils import (
    build_detail, build_row, compute_review_diff, get_job, public_image_url,
    start_recognition_job, verify_preview_image_signature,
)


def _tenant_id(request: Request) -> str:
    """Gap E2 租户键：委托 app.auth.resolve_tenant（生产态由身份派生，开发态读 X-Tenant-Id）。"""
    return resolve_tenant(request)


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


def _run_image_guard(image_path: str):
    """P2-1 图内隐藏文字检测（原图、CLAHE 提亮之前）。

    必须在 preprocess.apply_pipeline 之前对**原图**执行：CLAHE 提亮会把淡墨拉亮、
    使 faint_ink 判定失真。只告警不拦截——返回值随 job 透传，不改变上传/识别/
    approve 的任何既有成功路径；连工具都 import 不进来时也返回安全缺省
    （flagged=False + reason），绝不阻断上传。
    """
    try:
        from ai_registry.tools.image_injection_guard.v1_0_0 import Tool
        return Tool().execute(image_path)
    except Exception as e:
        logging.getLogger("api_receipts").warning(
            "[WARN] image_injection_guard 调用降级 (path=%s): %s", image_path, e)
        return {
            "flagged": False,
            "reasons": ["图片无法读取，已跳过隐藏文字检查，请照常对照原图核对"],
            "metrics": {"degraded": True, "error": e.__class__.__name__},
        }


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

    鉴权：该端点按上传件收原始图片字节，只服务已认证的店员及以上角色（前端经
    apiFetch 会带 X-Role / Bearer，不破坏演示）。
    """
    require_role("staff")(request)
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
    文件落在 uploads 目录内（后者防路径穿越）。该端点是原图对外的唯一出口
    （此前并存的 `/uploads` 静态挂载零鉴权零租户校验，已整体撤销）。

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


def _vendor_hint_from_row(row, tenant_id=None) -> str:
    """P2-T6：从已落库的单据行派生识别先验（vendor_hint），供重试/重解析入口复用。

    why：重试与重解析场景单据行上**已经有**供应商信息（`supplier_name`，可能还有
    `supplier_id`），这是零成本、零模型调用的先验来源；此前这两条入口都不传
    `vendor_hint`，等于「已经知道供应商却仍不注入记忆」，首轮命中率因此被压低。

    口径（与 P2-T6「不编造供应商」纪律一致，三条都可判定）：
    1. `supplier_name` 非空 → 用它作为 hint（hint 是自由文本名称，与
       `rag.retrieve_context(vendor)` 的入参口径一致，锚定交给读侧自己做）；
    2. 行上已有 `supplier_id` 时**不再查库**；未锚定时用 `db.resolve_supplier_id`
       做一次**只读**解析，仅用于留痕（不改变 hint 取值形态、失败不阻断）——why：
       让运维能从日志区分「没供应商可用」与「有名字但没锚定到档案」两种未命中；
    3. `supplier_name` 为空 → 返回空串：不猜、不编造、不调预判模型。
    """
    name = str(getattr(row, "supplier_name", "") or "").strip()
    if not name:
        return ""
    if getattr(row, "supplier_id", None) is None:
        try:
            if db.resolve_supplier_id(name, tenant_id=tenant_id) is None:
                # 用 warning 而非 info：默认日志级别下 info 不可见，运维就没法区分
                # 「没供应商可用」与「有名字但没锚定到档案」——而这正是该分支存在的
                # 理由；同函数下方解析失败那条用的也是 warning，口径保持一致。
                logging.getLogger("api_receipts").warning(
                    "[memory] 派生 vendor_hint 未锚定到供应商档案，按名字注入首轮先验: %s", name)
        except Exception as e:
            logging.getLogger("api_receipts").warning(
                "[WARN] 派生 vendor_hint 时解析供应商失败（仍按名字注入）: %s", e)
    return name


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

    # P2-1 图内隐藏文字检测：必须在 preprocess.apply_pipeline（CLAHE 提亮）之前的
    # 原图上执行（顺序重要：提亮会让淡墨检测失真）。只告警不拦截，结果随 job 透传。
    image_guard = _run_image_guard(image_path)

    raw_image_path = image_path
    pipeline_image_path = image_path
    # T10 预处理纠偏（上传后、抽取前）：开关 'preprocess_enabled' 默认 OFF；
    # 任何失败回落原图，绝不阻断识别主链路
    try:
        from app.services import preprocess
        pipeline_image_path, _prep_meta = preprocess.apply_pipeline(raw_image_path)
        if _prep_meta.get("applied"):
            quality_warnings.append("preprocess_applied")
    except Exception:
        pipeline_image_path = raw_image_path

    # P2-T6 首轮供应商预判（后端）：前端手选优先——仅在 vendor_hint 为空时预判，
    # 避免预判覆盖用户明确选择。开关走 settings 键 vendor_precheck_enabled，缺省 false
    # （零行为变化）；predict_vendor 内部已兜底（裁剪/引擎/超时/解析失败一律空串、
    # 不抛异常），这里只再防一层导入期意外，且必须留痕不静默吞掉。
    # cfg 必须显式传：build_recognition_model(cfg=None) 会走硬编码默认模型、绕过管理台
    # 配置的识别引擎（P1 审核 mustFix M1）；get_engine_config 读失败时传 None，
    # 由 predict_vendor 内部兜底解析。
    # 可观测性（additive）：区分显式 vendor_hint 的两个来源 —— 用户手填（"user"）与
    # 预判填充（"precheck"，仅原 hint 为空且 predict_vendor 给出非空值时）。两者皆无
    # （预判失败/关闭）保持空串：下游 extra 不写键、Job result 字段为空串。只做来源
    # 标记，不改变 hint 取值与识别链路任何行为。
    _hint_origin = "user" if str(vendor_hint or "").strip() else ""
    if not vendor_hint:
        try:
            from app.services import vendor_precheck
            try:
                _vp_cfg = db.get_engine_config()
            except Exception as _cfg_err:
                logging.getLogger("api_receipts").warning(
                    "[WARN] 预判读取引擎配置失败，回落默认模型: %s", _cfg_err)
                _vp_cfg = None
            vendor_hint = vendor_precheck.predict_vendor(pipeline_image_path, cfg=_vp_cfg)
            if str(vendor_hint or "").strip():
                _hint_origin = "precheck"
        except Exception as _vp_err:
            logging.getLogger("api_receipts").warning(
                "[WARN] 供应商预判降级（不影响上传与识别）: %s", _vp_err)

    # P2-T6 首轮供应商预判（后端）：前端手选优先——仅在 vendor_hint 为空时预判，
    # 避免预判覆盖用户明确选择。开关走 settings 键 vendor_precheck_enabled，缺省 false
    # （零行为变化）；predict_vendor 内部已兜底（裁剪/引擎/超时/解析失败一律空串、
    # 不抛异常），这里只再防一层导入期意外，且必须留痕不静默吞掉。
    # cfg 必须显式传：build_recognition_model(cfg=None) 会走硬编码默认模型、绕过管理台
    # 配置的识别引擎（P1 审核 mustFix M1）；get_engine_config 读失败时传 None，
    # 由 predict_vendor 内部兜底解析。
    # 可观测性（additive）：区分显式 vendor_hint 的两个来源 —— 用户手填（"user"）与
    # 预判填充（"precheck"，仅原 hint 为空且 predict_vendor 给出非空值时）。两者皆无
    # （预判失败/关闭）保持空串：下游 extra 不写键、Job result 字段为空串。只做来源
    # 标记，不改变 hint 取值与识别链路任何行为。
    _hint_origin = "user" if str(vendor_hint or "").strip() else ""
    if not vendor_hint:
        try:
            from app.services import vendor_precheck
            try:
                _vp_cfg = db.get_engine_config()
            except Exception as _cfg_err:
                logging.getLogger("api_receipts").warning(
                    "[WARN] 预判读取引擎配置失败，回落默认模型: %s", _cfg_err)
                _vp_cfg = None
            vendor_hint = vendor_precheck.predict_vendor(image_path, cfg=_vp_cfg)
            if str(vendor_hint or "").strip():
                _hint_origin = "precheck"
        except Exception as _vp_err:
            logging.getLogger("api_receipts").warning(
                "[WARN] 供应商预判降级（不影响上传与识别）: %s", _vp_err)

    job_id, receipt_id = start_recognition_job(
        pipeline_image_path, vendor_hint=vendor_hint or "",
        tenant_id=_tenant_id(request),  # P0-1: 上传链路租户透传
        image_guard=image_guard,
        vendor_hint_origin=_hint_origin,
        raw_image_path=raw_image_path)

    image_url = public_image_url(receipt_id, raw_image_path, _tenant_id(request))
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
def job_status(job_id: str, request: Request):
    """识别任务轮询。返回体含单据结果与原图 URL，只服务已认证的店员及以上角色。

    前端轮询经 apiFetch 会带 X-Role / Bearer，不破坏演示；匿名访问改 401。
    """
    require_role("staff")(request)
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
    """批量上传（不做手选供应商先验，显式标记为范围外）。

    P2-T6 入口覆盖结论 —— **批量手选 vendor_hint 判定为范围外**，三条理由：
    1. 结构上表达不了「每张各自的供应商」：本端点契约为扁平的 `list[UploadFile]`，
       一个 `vendor_hint` 只能整批共用（1:N）。若整批来自不同供应商，等于给其余
       子单据注入**错误**的供应商先验（D2/D3 的防污染纪律优先于命中率）。
       要支持必须前端逐文件配对（如按 index 的 JSON sidecar），属接口契约变更。
    2. 单方面加 Form 形参会构成「定义了没接线」：前端批量路径
       （`main.js` 的批量上传）当前与设计都未发送该字段，新增形参没有消费方。
    3. 批量子单据的记忆收益由**解析后的行**承接：解析完成即落 `supplier_name`，
       此后重试 / 换图重解析入口会经 `_vendor_hint_from_row` 派生 hint（见下），
       即批量入口不需要在派发时刻猜供应商。
    故此处**不新增** `vendor_hint` 形参、也不调用供应商预判模型（保持零行为变化）。
    """
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
        # P2-1 图内隐藏文字检测：与单张同口径，在 apply_pipeline（CLAHE 提亮）之前
        # 对原图执行，结果随 job 透传。只告警不拦截。
        image_guard = _run_image_guard(image_path)
        raw_image_path = image_path
        pipeline_image_path = image_path
        # T10 预处理纠偏（上传后、抽取前，与单张同口径；失败回落原图不阻断）
        try:
            from app.services import preprocess
            pipeline_image_path, _prep_meta = preprocess.apply_pipeline(raw_image_path)
            if _prep_meta.get("applied"):
                photo_warnings.append("preprocess_applied")
        except Exception:
            pipeline_image_path = raw_image_path
        # P0-1: 批量上传链路租户透传（在派发 Job 线程前捕获，线程内不读 request）
        job_id, receipt_id = start_recognition_job(
            pipeline_image_path, tenant_id=_tenant_id(request),
            image_guard=image_guard, raw_image_path=raw_image_path)
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
            "image_url": public_image_url(receipt_id, raw_image_path, _tenant_id(request)),
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
    会拒绝、此处会放行，但列表只输出 URL 不打开文件，不构成可达面。

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
    basename 产出签名取图端点 URL，前端渲染即 404 破图。这里只做展示层兜底：文件不
    存在时返回空串，字段保留、响应结构不变，不删历史数据行、不改 image_path。判据
    统一为「basename 是否有文件」—— 端点读的就是这个 src，文件缺失时端点必 404。

    性能：只做路径归一 + os.stat，绝不打开/解码图片；且只在 image_url 非空时执行，
    空 image_path 的行（live 库 238 条）零开销。

    刻意不放进 build_row / public_image_url：那两处被详情、上传、Job 回写等路径共用，
    改动会波及所有产出 URL 的链路；死链治理只针对展示层（列表入口
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
    # 详情仍会产出签名取图端点 URL，归档弹窗打开时 404 破图/死链（前端只有空值守卫，
    # 挡不住非空死链）。判据与 X1 列表侧同源
    # （_blank_unreachable_image_url → _image_url_target_exists，只 os.stat 不解码）。
    # 单条查询，一次 stat 开销可忽略；文件存在时 URL 不变。
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


# -------------------------------------------------------------
# P3-T2（K3）：用户修正 → 候选事实抽取（记忆飞轮的写侧入口）
# -------------------------------------------------------------
# 为什么要有这一段：记忆要"越用越准"就必须有人把「人的修正」翻译成结构化先验。现状
# 写入侧只有 approve 处的两段自由文本（notes / sample），gap 05 §H2/H4 实测语义通道
# 线上产出为 0。抽取产物**不直接生效**：按计划 C6 的写入双轨，规则级（memory_facts
# 的四类白名单）一律经 pending 人工闸；案例级（单据明细语料）由 approve 处既有的
# ingest_memory 自由文本回写承接 —— 那是双轨的另一半，本函数不重复实现。
#
# 两处触发共用本函数（计划 §10.2 明确「两处都要接」，本仓高发「修 A 漏 B」）：
#   - save_edited（复核保存）
#   - approve_receipt（入账）
# 实现只此一份，两个调用点都调 `_extract_and_enqueue_memory_facts`，避免"同一根因
# 多份实现，只改一份"。两处各自传 `trigger=` 声明入口（只进运行记录，不参与业务判定）。
#
# P3-T4/B6 运行记录：每次"真的发起了抽取"的运行都会写一行 `memory_extract_runs`
# （经 `_persist_extract_run` → `db.record_memory_extract_run`），使
# `memory_extract.get_last_run()` 的内存态不再"进程重启即丢"。查询侧是
# `db.list_memory_extract_runs`（消费方：P8b·AA2 的重跑脚本 + 本波用例）。
MEMORY_EXTRACT_SETTINGS_KEY = "memory_extract_enabled"


def _memory_extract_enabled() -> bool:
    """抽取开关（settings 键 memory_extract_enabled，缺省 False = 零行为变化）。

    为什么缺省关闭：抽取在写请求（复核保存 / 入账）路径上发一次外部模型调用，缺省不额外
    消耗额度、也不改变既有端点耗时 —— 与 vendor_precheck_enabled（P2-T6）同口径。
    为什么读不到设置时按关闭处理：宁可这次不抽，也不在不确定状态下发外呼。
    """
    try:
        return bool(settings_service.get_bool(MEMORY_EXTRACT_SETTINGS_KEY, False))
    except Exception as e:
        logging.getLogger("api_receipts").warning(
            "[memory_extract] 开关读取失败，按关闭处理: %s", e)
        return False


def _ai_prefill_payload(row) -> dict:
    """解析 `receipts.ai_prefill_json` 为 dict（AI 预填原版：vendor/date/total/items）。

    解析失败 / 非对象一律返回 {}：空 dict 与「本单确实没有 AI 预填」在下游完全同形
    （都不产生字段级差异、也都不触发抽取），故解析失败必须单独记一条 warning，把
    receipt_id 与原始值前 200 字符一并留痕，供事后区分「确实没有 AI 值」与「ai_prefill
    脏了解析不了」—— 不记日志的话脏 json 会静默改变行为而无从归因。warning 口径与
    `_ai_prefill_items`（P23·AQ2）共用此处唯一实现，避免同口径抄两份。
    """
    raw = getattr(row, "ai_prefill_json", "") or ""
    try:
        payload = json.loads(raw or "{}")
    except (TypeError, ValueError) as e:
        logging.getLogger("api_receipts").warning(
            "[memory_extract] ai_prefill 解析失败，本次将按『无字段差异』处理 —— "
            "注意这与『本单确实没有人工修正』在运行记录上同形（都表现为不触发抽取）；"
            "排查方向：receipts.ai_prefill_json 损坏 "
            "(receipt_id=%s, 原始值前 200 字符=%r, 原因=%s)",
            getattr(row, "id", None), str(raw)[:200], e)
        return {}
    return payload if isinstance(payload, dict) else {}


def _ai_prefill_items(row) -> list:
    """取单据的 AI 预填明细（`receipts.ai_prefill_json` 的 items）。

    解析失败 / 无预填一律返回 []：空列表会让字段级 diff 为空 → 不触发抽取。对新建
    手工单（本就是"没有 AI 值可比"）与脏行都是安全方向（不抛、不猜）。解析失败时的
    warning 由 `_ai_prefill_payload` 统一发出（两种成因同形，需要留痕区分）。
    """
    items = _ai_prefill_payload(row).get("items")
    return items if isinstance(items, list) else []


def _ai_prefill_present(v) -> bool:
    """AI 对该字段是否有可用预填值：None / 空串 / 空容器 / 数值 0 都视为「未预填」。

    数值 0 也算未预填：OCR 未识别与「识别成 0」在单据上无从区分，而把 0 当成 AI 值
    会让「用户填空字段」被误判为「用户改了 AI 值」（W-8 要收窄的正是这种误伤）。
    """
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip() != ""
    if isinstance(v, (list, dict)):
        return bool(v)
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v) != 0.0
    return True


def _ai_prefill_num(v) -> float:
    """AI 预填数值的宽松归一：坏值按 0 处理（与 compute_review_diff 的 `float(av or 0)` 同口径）。"""
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _ai_prefill_corrected_fields(row, items_raw, body) -> bool:
    """W-8：本次保存是否属于「AI 预填过某字段，店员改成了别的值」。

    旧实现在「unit_price/amount/supplier_name/total_amount 任一与**库中旧值**不同」时
    就扣记忆衰减分（见 save_edited 调用点），把「用户补填空字段 / 供应商改名 / 价格正常
    波动」等业务合理变更也误判为「AI 错被纠正」并连带归档该供应商全部历史记忆。调用点
    原注释自称「店员真的改了 AI 预填值」，但实现根本没读 `ai_prefill_json`（该字段当时
    仅用于埋点）。本函数把它收窄为对**AI 预填过**的字段判定。

    判定口径（字段级，与前端 diff 同口径）：AI 预填值非空（`_ai_prefill_present`）且与
    最终提交值不同 → 计一次 AI 纠错。
      - 行级 unit_price / amount：与 `ai_prefill.items` 按位置对齐（同 `compute_review_diff`）；
      - 表头 total_amount：对应 `ai_prefill.total`，与提交总额比较。
    「AI 未预填」「用户补填空字段」都不计入 —— 这也是供应商改名不扣分的成因：改名场景下
    AI 往往没有可比的预填名（补填）或改的是标识而非值。

    `ai_prefill_json` 缺失 / 损坏时 `_ai_prefill_payload` 返回 {} → 无法判定为 AI 错，
    **返回 False（不扣分）**，且已由该函数留 warning（P23·AQ2 口径）；不会静默退化成
    「全量扣分」—— 收窄后的默认方向是保守不扣。
    """
    payload = _ai_prefill_payload(row)
    if not payload:
        return False
    items_raw = items_raw or []
    ai_items = payload.get("items")
    ai_items = ai_items if isinstance(ai_items, list) else []
    for ai_it, fin_it in zip(ai_items, items_raw):
        if not isinstance(ai_it, dict):
            continue
        for f in ("unit_price", "amount"):
            av = ai_it.get(f)
            if _ai_prefill_present(av) and _ai_prefill_num(av) != _ai_prefill_num(fin_it.get(f)):
                return True
    if _ai_prefill_present(payload.get("total")) \
            and _ai_prefill_num(payload.get("total")) != _ai_prefill_num(body.total_amount):
        return True
    return False


def _pending_fact_content(fact, decision=None) -> str:
    """候选事实 (+ 整合判定) → 人工闸里可读的一行（也是 approve 后落 notes 的文本）。

    为什么把判定并进 content 而不是只打日志：pending 表没有 action 列，而审核者必须能
    区分「新增」与「覆盖旧值 X」—— 只看「alias：A → B」看不出这条会不会顶掉既有先验。
    content 本身是 approve 后注射进 prompt 的记忆文本，故用括号补充，不加机器可读前缀。
    kind 用白名单字面量（alias/unit/layout/price_range）：不另建一份中文标签表，
    避免"同一口径多份实现"（kind 的中文说明只有 memory_extract._KIND_HINTS 一处）。

    decision 为 None（Q2：写入侧闸门直接拒绝，还没走到 decide_action）时只给事实本身
    的一行 —— 仍然复用本函数，而不是在调用点再拼一份同样的格式。
    """
    line = f"{fact.kind}：{fact.fact_key} → {fact.value}"
    if decision is not None and decision.superseded_value and decision.action in (
            memory_consolidate.ACTION_UPDATE, memory_consolidate.ACTION_DELETE):
        line += f"（{decision.action} 既有值：{decision.superseded_value}）"
    return line


def _record_refused_candidate(vendor, tenant_id, receipt_id, reason, fact=None,
                              content=None, source_kind=None):
    """把"被拒 / 落选"的候选记为 status='rejected' 的审计行（Q2：reject_reason 的写入方）。

    K2 给 pending_memory 建了 reject_reason 列，但**零写入方**，于是"上次为什么没进来"
    在库里查不到（审核者只能翻日志，被拒方的下一次写入也不知道）。本函数就是那个写入方，
    由 `_extract_and_enqueue_memory_facts` 在三条"候选没进队列"的分支上调用。

    为什么记成 status='rejected' 而不是 'pending'：被拒候选的载荷必然过不了
    `validate_fact`，若以待审行入队，审核者点"批准"时 approve 链路会回落自由文本路径，
    把非法候选写成一条自由文本记忆（人工闸给闸门开后门）；且 pending 列表与角标只列
    待批项，混进去会把人工闸刷成噪声。故被拒行只作审计载体，不进可批准队列。

    fact 为 None 时（抽取侧闸门只有逐条理由字符串、拿不到归一后的 fact）载荷列留 NULL，
    content 直接放理由本身 —— 该行永远不会被 approve，故 content 不进任何记忆库。
    消费方：`db._pending_row_to_dict` → `GET /api/memory/pending`（含 status=rejected）。

    失败只告警：审计是次要产物，绝不能把抽取链（乃至保存/入账主流程）带崩；但也不许
    静默（本仓③类缺陷），故理由之外还带上异常类型与内容。
    """
    try:
        db.enqueue_pending_memory(
            str(vendor or ""), content or str(reason or ""),
            tenant_id=tenant_id,
            source_kind=source_kind or memory_facts.SOURCE_EXTRACT,
            source_receipt_ids=[receipt_id],
            supplier_id=getattr(fact, "supplier_id", None),
            fact_kind=getattr(fact, "kind", None),
            fact_key=getattr(fact, "fact_key", None),
            fact_value=getattr(fact, "value", None),
            reject_reason=reason, status="rejected")
    except Exception as e:
        logging.getLogger("api_receipts").warning(
            "[memory_extract] 拒绝理由落库失败（不影响保存/入账主流程）"
            "receipt_id=%s：%s: %s", receipt_id, type(e).__name__, e)


def _persist_extract_run(receipt_id, row, tenant_id, supplier_id, trigger,
                         diffs_count, facts_produced, facts_rejected,
                         reject_reasons, prompt_source, error):
    """把一次抽取的运行记录落库（P3-T4 / B6：中间产物可查、失败可复盘）。

    why 单独一层 try/except：写运行记录是**次要产物**，但它与主链路共用同一个库会话工厂
    —— 让它的异常冒泡到 `_extract_and_enqueue_memory_facts` 的外层 except，会把一次**成功**的
    保存/入账降级成 stats=None，并打出一条误导性的"抽取链路异常"（失败原因被张冠李戴）。
    需求 ③ 明确要求"不得因写记录而影响主流程"，故这里独立兜底（同 `_record_refused_candidate`）。
    """
    try:
        db.record_memory_extract_run(
            tenant_id=tenant_id, receipt_id=receipt_id,
            vendor=getattr(row, "supplier_name", "") if row is not None else "",
            supplier_id=supplier_id, trigger=trigger,
            diffs_count=diffs_count, facts_produced=facts_produced,
            facts_rejected=facts_rejected, reject_reasons=reject_reasons,
            prompt_source=prompt_source, error=error)
    except Exception as e:
        logging.getLogger("api_receipts").warning(
            "[memory_extract] 运行记录落库失败（不影响保存/入账主流程）"
            "receipt_id=%s trigger=%s：%s: %s",
            receipt_id, trigger, type(e).__name__, e)


def _extract_and_enqueue_memory_facts(receipt_id, row, tenant_id,
                                     ai_items, user_items, trigger=""):
    """把「用户对 AI 预填的修正」抽成候选事实，规则级候选入 pending 人审队列。

    管线（分工：`memory_extract` 出候选 / `memory_facts` 是写入侧唯一闸门 /
    `memory_consolidate` 定动作 / `pending_memory` 是人审队列）：

    ① 纯代码算字段级差异（`compute_field_diffs`）。无差异 = 这张单没有"人的修正"可学
       → 直接返回，**不建模型、不发调用**（零成本、零噪声）。
    ② 交给模型判断"值不值得记 + 归哪一类 + 怎么写 key/value"
       （`extract_candidate_facts` 只把差异发给模型，不是整张单据）。
    ③ 逐条复核 `validate_fact`（写入侧唯一闸门）：非法丢弃并**计数 + 落日志**（不得静默）。
    ④ 与既有同 scope 事实做 `decide_action`（ADD/UPDATE/DELETE/NOOP）；**NOOP 不入队**
       —— 同值事实、以及"低可信不得覆盖高可信"这两种情形入队，只会让审核者看到一条
       没有任何变更的行，人工闸的可用性会被自己的噪声吃光。
    ⑤ 有变更（ADD/UPDATE/DELETE）的候选入 pending，载荷带 supplier_id / fact_kind /
       fact_key / fact_value（K2 的列；消费方＝`GET /api/memory/pending` 的字段级 diff 视图，
       见 db._pending_row_to_dict）。

    Q2（reject_reason 的写入方）：**没进队列的候选也要留下可查的理由**。三条"没进队列"
    的分支（抽取侧闸门拒绝 / 写入侧闸门拒绝 / decide_action 判 NOOP）各自调一次
    `_record_refused_candidate`，把理由写进 `pending_memory.reject_reason`。这些行落
    `status='rejected'`，是**审计载体**而非待批项：人工闸列表与角标只列
    `status='pending'`，故"不入队"（不进可批准队列）的语义未变，但"为什么没进来"从此在
    库里可查（此前只打日志，`GET /api/memory/pending` 里永远看不到）。

    scope 锚点：行上的权威 `supplier_id` 优先（P2-T1），缺失才按 vendor 名解析一次（与
    `db._memory_anchor_for_write` 同口径）。**有值却不传**会让候选在 validate_fact 处因
    "缺 supplier_id"被整体丢掉 —— 那才是真正的"定义了没接线"。

    参数 `trigger`：调用方声明"这次抽取是被哪个入口触发的"（`save_edited` / `approve`），
    只用于**运行记录**的分桶（P3-T4/B6），不参与任何业务判定。两个调用点都必须传
    （本仓高发「修 A 漏 B」：save_edited 与 approve 是两条独立入口）。

    运行记录（P3-T4/B6）：每一次"真的发起了抽取"的调用都会经 `_persist_extract_run` 写一行
    `memory_extract_runs`（成功、降级、无锚点、抛异常都写），供事后复盘"为什么没有产出"。
    为什么这一步必须落在本函数内、而不是在两个调用点各写一次：本函数是**唯一实现**，
    两处触发共用它；在调用点各写一次就是"同一根因多份实现"，迟早只改一处。写记录本身
    失败只 warning（见 `_persist_extract_run`），绝不会把已成功的保存/入账变成 500。
    **只记"发起了抽取"的调用**：开关关闭 / row 为空（抽取根本没被请求）不写 —— 否则
    生产上每次保存都会留一行噪声（开关缺省关闭时尤其如此）。

    失败语义：任何异常一律 logger.error（带 receipt_id / supplier / 原因），并把
    `memory_extract.get_last_error()` 的摘要一并落日志后返回 None —— 抽取是可选增益，
    绝不阻断保存/入账主链路；但也绝不静默（抽取内部把异常降级成"空列表"，只看返回值会
    与"这张单没什么可记的"混淆，本仓高发的"异常被静默吞掉导致功能静默失效"）。

    返回统计字典（diff_count / candidates / rejected / enqueued / error）；开关关闭、
    无差异、无锚点时返回 None。超时由引擎自身 `call_timeout` 兜底（已由
    MAX_CALL_TIMEOUT_SECONDS 钳到 ≤60s），不另起第二套超时 —— 避免"同一根因多份实现"。
    """
    log = logging.getLogger("api_receipts")
    # 供外层 except 写运行记录用：try 内可能在任何一步就抛（连 supplier_id 都还没解析出），
    # 这些名字必须预先存在 —— 用 locals() 反射取"可能没赋值的名字"既隐晦又容易在改名后
    # 静默退化成 0/NULL（本仓高发的静默失效）。
    run_supplier_id = None
    run_diff_count = 0
    try:
        if row is None or not _memory_extract_enabled():
            return None
        vendor = str(getattr(row, "supplier_name", "") or "").strip()
        supplier_id = getattr(row, "supplier_id", None)
        if supplier_id in (None, ""):
            supplier_id = db.resolve_supplier_id(vendor, tenant_id=tenant_id)
        if not supplier_id:
            # E2E 注记②（收尾波次工作项②）：抽取期也镜像 parse 期的记忆先验档
            # （receipt_utils 供应商锚点第四档同款）—— vendor 按名解析不出档案时，
            # 同名唯一 active 记忆行携带的锚点兜底（唯一候选才用，不猜）。db 函数
            # 本体只读、带租户门，零改动复用。先验也落空 → 走下方"缺 supplier_id"
            # 失败分支语义不变（可复盘失败态保留）。
            supplier_id = db.resolve_supplier_id_from_memory(vendor, tenant_id=tenant_id)
            if supplier_id:
                log.info("[supplier_anchor] 抽取期回退锚定 receipt=%s 档=memory_prior "
                         "supplier_id=%s（vendor=%r）", receipt_id, supplier_id, vendor)
        run_supplier_id = supplier_id
        if not supplier_id:
            log.warning(
                "[memory_extract] 单据 %s 的供应商 %r 解析不出 supplier_id，跳过抽取"
                "（记忆以供应商为锚点，无锚点候选必然过不了校验）", receipt_id, vendor)
            # 无锚点是**可复盘的失败态**（不是"没什么可记的"）：写一行带 error 的运行记录，
            # 否则复盘时只能看到"这张单没有记忆产出"，看不出是被锚点卡住的。
            _persist_extract_run(
                receipt_id, row, tenant_id, None, trigger, 0, 0, 0, [],
                memory_extract.PROMPT_SOURCE_NONE,
                "缺 supplier_id：供应商 %r 解析不出锚点，跳过抽取" % vendor)
            return None

        diffs = memory_extract.compute_field_diffs(ai_items, user_items)
        run_diff_count = len(diffs)
        if not diffs:
            # 无差异 = 这件"抽取"从未发生（不建模型、不发调用）。仍写一行（diffs_count=0、
            # prompt_source="" 明确表示没用到文案），把"为什么没有产出"变成可查事实 ——
            # 但**刻意不读 get_last_run()**：本次没调 extract_candidate_facts，那里的状态
            # 仍是上一次调用的残留，读它会把别的单据的统计写进这一行（伪造数据）。
            _persist_extract_run(
                receipt_id, row, tenant_id, supplier_id, trigger, 0, 0, 0, [],
                memory_extract.PROMPT_SOURCE_NONE, "")
            return None

        cfg = db.get_engine_config()
        facts = memory_extract.extract_candidate_facts(
            ai_items, user_items, str(supplier_id), tenant_id,
            max_facts=memory_extract.DEFAULT_MAX_FACTS, cfg=cfg)
        run = memory_extract.get_last_run()
        extract_error = str(run.get("error") or "")
        if extract_error:
            log.error("[memory_extract] 抽取降级 receipt_id=%s supplier=%s(%s)：%s",
                      receipt_id, vendor, supplier_id, extract_error)
        # 抽取侧闸门（validate_fact 的第一道）被拒的候选必须逐条可见：只留计数会让人
        # 分不清"模型没给出候选"与"候选全被拒"。理由同时**落库**（Q2）：K2 的
        # reject_reason 列此前零写入方，只打日志时库里查不到"上次为什么没进来"。
        for reason in run.get("reject_reasons") or []:
            log.warning("[memory_extract] 候选被抽取侧闸门拒绝 receipt_id=%s：%s",
                        receipt_id, reason)
            _record_refused_candidate(vendor, tenant_id, receipt_id, reason)
        stats = {"diff_count": len(diffs), "candidates": len(facts),
                 "rejected": 0, "enqueued": 0, "error": extract_error}
        # 抽取侧闸门的拒绝条数（run["rejected"]）与写入侧复核的拒绝条数（stats["rejected"]）
        # 在运行记录里合并成 facts_rejected：两者的业务含义都是"候选被闸门丢掉了"，
        # 分开存会让复盘多一次心算。`stats` 的对外语义不变（仍只含写入侧，调用方依赖它）。
        gate_rejected = int(run.get("rejected") or 0)

        existing = db.list_vendor_memory(vendor=vendor, tenant_id=tenant_id,
                                        supplier_id=supplier_id) if facts else []
        for fact in facts:
            # ③ 写入侧唯一闸门：抽取侧已过一遍，这里是**写入点**的复核（本函数是写方，
            #    写方必须自己校验，不能依赖"上游一定筛过"）
            ok, reason = memory_facts.validate_fact(fact)
            if not ok:
                stats["rejected"] += 1
                log.warning(
                    "[memory_extract] 候选被写入侧闸门拒绝（不入队）receipt_id=%s "
                    "kind=%s key=%s：%s", receipt_id, fact.kind, fact.fact_key, reason)
                # 该分支是**防御性复核**：生产链路上本函数的候选都出自
                # `memory_extract._to_facts`，那里已用同一份 validate_fact 筛过，故这里
                # 通常不可达；但"写方必须自己校验"不能省，理由也必须可查（Q2）。
                _record_refused_candidate(
                    vendor, tenant_id, receipt_id, reason, fact=fact,
                    content=_pending_fact_content(fact),
                    source_kind=fact.source_kind)
                continue
            # ④ 与既有同 (tenant, supplier, fact_key) 事实定动作
            decision = memory_consolidate.decide_action(existing, fact)
            if decision.action == memory_consolidate.ACTION_NOOP:
                log.info(
                    "[memory_extract] 候选无需变更（不入队）receipt_id=%s kind=%s "
                    "key=%s：%s", receipt_id, fact.kind, fact.fact_key, decision.reason)
                # "没进队列"的理由与"被闸门拒绝"的理由同样要可查（Q2）：本列的口径是
                # 「拒绝/落选的可读理由」，NOOP 正属"落选"（同值不变 / 低可信不得覆盖
                # 高可信 / 候选非法）。仍记 status='rejected' 的审计行，不占用人工闸。
                _record_refused_candidate(
                    vendor, tenant_id, receipt_id, decision.reason, fact=fact,
                    content=_pending_fact_content(fact, decision),
                    source_kind=fact.source_kind)
                continue
            # ⑤ 规则级候选一律入人审队列（计划 C6 双轨制的"规则必须人审"半）
            db.enqueue_pending_memory(
                vendor, _pending_fact_content(fact, decision), tenant_id=tenant_id,
                source_kind=fact.source_kind, source_receipt_ids=[receipt_id],
                supplier_id=fact.supplier_id, fact_kind=fact.kind,
                fact_key=fact.fact_key, fact_value=fact.value)
            stats["enqueued"] += 1
            log.info(
                "[memory_extract] 候选入人审队列 receipt_id=%s supplier=%s kind=%s "
                "key=%s action=%s", receipt_id, vendor, fact.kind, fact.fact_key,
                decision.action)
        # 运行记录（P3-T4/B6）：**唯一持久化点**，覆盖"成功 / 抽取降级 / 0 候选"三种终态。
        # 放在循环之后才能带上写入侧复核的计数；循环内没有任何 return，故这里必被执行到。
        _persist_extract_run(
            receipt_id, row, tenant_id, supplier_id, trigger,
            len(diffs), len(facts), gate_rejected + stats["rejected"],
            run.get("reject_reasons") or [], run.get("prompt_source") or "",
            extract_error)
        return stats
    except Exception as e:
        log.error(
            "[memory_extract] 抽取链路异常（不阻断主流程）receipt_id=%s supplier=%s："
            "%s: %s；last_error=%s", receipt_id,
            getattr(row, "supplier_name", "") if row is not None else "",
            type(e).__name__, e, memory_extract.get_last_error() or "无")
        # 未预期异常同样要留运行记录（error 非空）：只打日志时，"这次抽取整个没跑成"
        # 与"这张单没什么可记的"在库里长得一样（需求 ②：成功与失败都要写）。
        _persist_extract_run(
            receipt_id, row, tenant_id, run_supplier_id, trigger,
            run_diff_count, 0, 0, [], memory_extract.PROMPT_SOURCE_NONE,
            "%s: %s" % (type(e).__name__, e))
        return None


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
    if not is_auto and body.receipt_id is not None and row is not None:
        for old_it, new_it in zip(old_items, items_raw):
            if old_it.get("unit_price") != new_it.get("unit_price"):
                db.append_audit_log(rid, who, "save_edited", "unit_price",
                                    old_it.get("unit_price"), new_it.get("unit_price"))
            if old_it.get("amount") != new_it.get("amount"):
                db.append_audit_log(rid, who, "save_edited", "amount",
                                    old_it.get("amount"), new_it.get("amount"))
        if row.supplier_name != body.supplier_name:
            db.append_audit_log(rid, who, "save_edited", "supplier_name",
                                row.supplier_name, body.supplier_name)
        if row.total_amount != body.total_amount:
            db.append_audit_log(rid, who, "save_edited", "total_amount",
                                row.total_amount, body.total_amount)
        # T8 非对称淘汰（覆写 -0.12 口径）：**只有**「AI 预填过该字段 且 店员改成了别的
        # 值」才算 AI 错被纠正 → 该供应商活跃记忆扣分，低于归档阈值自动 archived 停止注入。
        # W-8 收窄：旧实现按 old/new 全量 diff 扣分，把「用户补填空字段 / 供应商改名 /
        # 价格正常波动」也误判为 AI 错；判定改由 `_ai_prefill_corrected_fields` 对比
        # ai_prefill_json 得出（ai_prefill 缺失/损坏 → 不扣分 + warning，不静默全量扣分）。
        # 失败不阻断保存。
        if _ai_prefill_corrected_fields(row, items_raw, body):
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
                ai_items = _ai_prefill_items(ai_row)
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
    # P3-T2（K3）触发点之一：复核保存。只在"人改过"的路径上抽取 —— auto_save（识别
    # 主链路自动回写）没有"人的修正"，且它跑在后台 Job 线程里，不该在那里发外呼。
    # 无字段差异时本调用直接返回（不建模型、不发模型调用），故对无改动保存零成本。
    if not is_auto and body.receipt_id is not None:
        _extract_and_enqueue_memory_facts(
            rid, row, tenant_id, _ai_prefill_items(row), items_raw,
            trigger="save_edited")
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
    # 供应商自动建档（approve 后成为正式供应商，归入当前租户）—— 收尾波次工作项③：
    # 上移到入账之前。新商首单 W1：apply_receipt_to_inventory 期的 SKU 匹配收窄与
    # vendor_sku_alias 别名登记都以供应商锚点为前提，建档先于入账，首单别名登记才
    # 有锚点可用（E2E 注记①：原时序下 apply 先跑，首单别名登记恒 NO_SUPPLIER）。
    # 重排不新增任何建档时机（本块本就是人工闸后动作：owner 点了 approve 才执行），
    # 幂等守卫 find_supplier_by_name 原样保留；建档失败（create_supplier 返回 err）
    # 不阻断 approve 主链路 —— 沿用原错误语义（返回值不抛出）。
    if row.supplier_name and db.find_supplier_by_name(row.supplier_name, tenant_id=_tenant_id(request)) is None:
        db.create_supplier(row.supplier_name, tenant_id=_tenant_id(request))
    # 幂等入账：写 SKU 库存流水 + 累计（P1-1: 入库链路租户透传，SKU 匹配/建档/流水限定
    # 本租户）—— 在新商建档之后执行，apply 期的锚点解析才查得到刚建的档案。
    from app.services.inventory import apply_receipt_to_inventory
    apply_receipt_to_inventory(row, tenant_id=_tenant_id(request))
    db.update_receipt(receipt_id, status="approved", version=row.version + 1)
    db.append_audit_log(receipt_id, who, "approve_receipt", "status", old_status, "approved")
    row = db.get_receipt_row(receipt_id, tenant_id=_tenant_id(request))
    # 回写 VendorMemory（只认正向信号：老板批准）
    # T3 Gap B1：透传 tenant_id 与触发 receipt_id（source_ref 可回溯）
    # J3：同时透传单据行上的权威主体锚点 supplier_id —— 本行是同一租户的档案主键，
    # 比"用 supplier_name 反查档案"可靠（名字在重名变体/自动建档前后可能解析到别的
    # 档案）。行上没有锚点（新上传单据尚未回填）时传 None，由 db 侧按名解析一次。
    from app.services.rag import ingest_memory
    items_text = "\n".join(f"- {i['name']} {i['quantity']}{i['unit']} @{i['unit_price']} = {i['amount']}"
                           for i in db.get_receipt_items(receipt_id, tenant_id=_tenant_id(request)))
    ingest_memory(row.supplier_name, items_text, notes=f"版式：{row.doc_form}",
                  tenant_id=_tenant_id(request), receipt_id=receipt_id,
                  supplier_id=getattr(row, "supplier_id", None),
                  # U1 审计链：自由文本回写行也要可追溯写入者 —— 与人工闸路径
                  # （api_memory.approve_pending 的 written_by=who）同源，均为本次
                  # 操作者身份；不写则该行 written_by 落 NULL，审计 A1 口径出现缺口。
                  written_by=who)
    # C6 ai_decision_log：approve 回填最终确认 + 幻觉判定
    try:
        ai_items = _ai_prefill_items(row)
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
    # P3-T2（K3）触发点之二：入账。放在供应商自动建档（上方）之后 —— 锚点解析按
    # vendor 名回退时，这里才能查得到刚建的档案；无字段差异时本调用直接返回。
    _extract_and_enqueue_memory_facts(
        receipt_id, row, _tenant_id(request), _ai_prefill_items(row),
        db.get_receipt_items(receipt_id, tenant_id=_tenant_id(request)),
        trigger="approve")
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
    # P2-T6: 从单据行派生 vendor_hint（不调预判模型）——行上已有供应商即可注入首轮
    # 供应商记忆；行上为空时 hint 为空、行为与接线前一致。
    job_id, _ = start_recognition_job(
        row.image_path, receipt_id=receipt_id,
        vendor_hint=_vendor_hint_from_row(row, _tenant_id(request)),
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
    # P2-T6: 换图重跑同 /retry 口径——从单据行派生 vendor_hint（不调预判模型）。
    # 注意 supplier_name 取自换图前的 row（本次 update 只改 image_path，未动供应商）。
    job_id, _ = start_recognition_job(
        image_path, receipt_id=receipt_id,
        vendor_hint=_vendor_hint_from_row(row, _tenant_id(request)),
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
    # 租户口径（P3-4 收敛 + D19）：生产态租户**只**取自身份（token 载荷，见
    # resolve_request_tenant → resolve_tenant），客户端 X-Tenant-Id 头与 body.tenant_id
    # 一律不采信 —— 两者都是客户端可任意声明的，采信等于让 staff 令牌把反馈写进他租。
    # 开发/演示态维持既有通道：服务端 header X-Tenant-Id 优先，body.tenant_id 仅在无
    # header 时兜底兼容（老客户端/脚本）；归属校验与 receipt_feedback 落库使用同一解析值。
    tenant_id = resolve_request_tenant(request, client_claimed=body.tenant_id)
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
    # 租户过滤统一走 _tenant_id（生产态=身份租户，开发态=X-Tenant-Id 或 default）并下推
    # 到 db 层：读侧同理不得采信「只在请求头里声明」的租户，否则他租反馈可被读出。
    tenant_id = _tenant_id(request)
    row = db.get_receipt_row(receipt_id, tenant_id=tenant_id)
    if row is None:
        return JSONResponse(content={"status": "error", "msg": "未找到指定收据"},
                            status_code=404)
    fbs = db.list_receipt_feedbacks(receipt_id=receipt_id, tenant_id=tenant_id)
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

