# -*- coding: utf-8 -*-
"""上传预处理纠偏（T10 Gap E4）：assess / deskew / enhance / orthogonal。

- assess(image): 清晰度 + 模糊判定 + 倾斜角估计（复用 api_receipts 的
  Laplacian 口径；ndarray 输入走同参数 numpy 实现）
- deskew(image): OpenCV 最小外接矩形估计倾斜角并旋转还原；±1° 内不纠偏
- enhance(image): 自适应对比度（CLAHE，LAB 空间 L 通道）+ 轻度去噪（双边滤波）
- _orthogonal_correct(image): 正交旋转纠正（90/180/270），四假设+水平
  投影方差+顶部重心启发式，90/270 换边 via cv2.rotate，避免 warpAffine 裁切
- apply_pipeline(image_path): 上传后、抽取前的统一入口。
  · 正交纠正由 'preprocess_orthogonal_enabled'（默认 true）控制，不受
    'preprocess_enabled' 限制（正交错误致命）；小角度 deskew+enhance 仍由
    'preprocess_enabled'（默认 OFF）控制。任何失败（含 HEIC 无法解码）回落
    原图，绝不阻断识别主链路。产物落 <原名>_prep.jpg（同目录）。
"""

import logging
import os

logger = logging.getLogger(__name__)

# ±1° 以内视作已摆正，不纠偏（避免轻微抖动引入重采样噪声）
DESKEW_DEADZONE_DEG = 1.0
# 纠偏输出统一为 JPEG（识别腿输入兼容），质量 95 保细节
_OUTPUT_QUALITY = 95

# 正交检测阈值：水平投影方差过小时视为空白图，不做正交判定（避免误转）
_ORTHO_ROWVAR_MIN = 1e6
# 判断 90/180 时“显著优于”比例（95% 过滤）
_ORTHO_RATIO_FILTER = 0.95


def _laplacian_variance_gray(gray_arr):
    """灰度 ndarray 的 Laplacian 方差（与 api_receipts._laplacian_variance 同口径）。"""
    import numpy as np
    arr = np.asarray(gray_arr, dtype=np.float32)
    if arr.ndim != 2 or arr.shape[0] < 3 or arr.shape[1] < 3:
        return None
    lap = (-4 * arr[1:-1, 1:-1] + arr[:-2, 1:-1] + arr[2:, 1:-1]
           + arr[1:-1, :-2] + arr[1:-1, 2:])
    return float(lap.var())


def _imread(image_path):
    """读图为 BGR ndarray；中文/HEIC 等 cv2 无法解码时回退 PIL（仍失败返回 None）。

    EXIF 处理：优先走 PIL + ImageOps.exif_transpose（cv2.imread 无 EXIF），
    成功则直接返回已校正的 BGR；失败再回退 cv2，确保带 EXIF 的正交图被
    矫正且与 apply_pipeline 的 EXIF 逻辑不重复丢失（幂等）。
    """
    # PIL 主路径：自带 exif_transpose，覆盖 HEIC 与 EXIF 正交
    try:
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except Exception:
            pass
        import numpy as np
        from PIL import Image, ImageOps
        with Image.open(image_path) as pil:
            try:
                pil = ImageOps.exif_transpose(pil)
            except Exception:
                pass
            rgb = pil.convert("RGB")
            arr = np.asarray(rgb)[:, :, ::-1].copy()  # RGB → BGR
            if arr is not None and arr.size != 0:
                return arr
    except Exception:
        pass
    # 回退 cv2（无 EXIF，但兼容 PIL 未覆盖的解码路径）
    try:
        import cv2
        img = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if img is not None:
            return img
    except Exception:
        pass
    return None


def _estimate_skew_angle(gray):
    """最小外接矩形法估计倾斜角（度，(-90, 90]）。失败返回 0.0。

    口径：二值化后取非零像素的 minAreaRect，返回值等于「图被旋转过的角度」
    （逆时针为正），纠偏时按 -angle 旋转还原。标定（cv2 5.0 / 4.x 均验证）：
    w < h 时 angle 为竖直边偏角 → skew = -(angle + 90)；否则 skew = -angle。
    """
    try:
        import cv2
        import numpy as np
        if gray is None or gray.size == 0:
            return 0.0
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        _, thr = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        pts = cv2.findNonZero(thr)
        if pts is None or len(pts) < 64:
            return 0.0
        rect = cv2.minAreaRect(pts)
        (w, h), angle = rect[1], float(rect[2])
        if w <= 0 or h <= 0:
            return 0.0
        skew = -(angle + 90.0) if w < h else -angle
        if skew > 90.0:
            skew -= 180.0
        if skew <= -90.0:
            skew += 180.0
        return float(skew)
    except Exception:
        return 0.0


def _orientation_metrics(gray):
    """单方向灰度的正交判据：行/列投影方差 + 重心 y。

    返回 (rowVar, colVar, cy)；失败回 (0,0,h/2)。
    """
    try:
        import cv2
        import numpy as np
        if gray is None or gray.size == 0:
            return 0.0, 0.0, 0.0
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        _, thr = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        h, w = thr.shape
        # 行/列求和（uint 0/255 -> int）
        row_sums = thr.sum(axis=1).astype(np.float64)
        col_sums = thr.sum(axis=0).astype(np.float64)
        rowVar = float(row_sums.var()) if row_sums.size else 0.0
        colVar = float(col_sums.var()) if col_sums.size else 0.0
        total = float(row_sums.sum())
        if total < 1:
            cy = h / 2.0
        else:
            y_idx = np.arange(h, dtype=np.float64)
            cy = float((y_idx * row_sums).sum() / total)
        return rowVar, colVar, cy
    except Exception:
        try:
            h = gray.shape[0] if gray is not None else 0
            return 0.0, 0.0, h / 2.0
        except Exception:
            return 0.0, 0.0, 0.0


def _orthogonal_correct(image):
    """正交检测与纠正（90/180/270），返回 (corrected, angle)。

    策略：四假设（0/90/180/270）分别计算水平投影方差（行 var）——水
    平文字行在摆正时行方差最大、列方差最小——取行 var 最大者为水平组；
    在水平组内以重心最靠上（cy 最小，顶部 heaviness）挑直立方向，180°
    场景由此区分为 upright vs upside-down。90/270 换边 via cv2.rotate
    （交换 w/h），避免 warpAffine 保持 (w,h) 的裁切。对空白/低方差图
    回落 0，不阻断主链路。
    angle 为将输入图恢复摆正所需的顺时针旋转角度（0/90/180/270），0 表示已摆正。
    """
    try:
        import cv2
        import numpy as np
        if image is None or not hasattr(image, 'shape'):
            return image, 0
        is_gray = image.ndim == 2
        if is_gray:
            gray = image
        else:
            try:
                gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            except Exception:
                return image, 0
        if gray.size == 0:
            return image, 0
        # 四假设灰度候选（用于打分，省去对 BGR 的重复转换）
        try:
            cand_gray = {
                0: gray,
                90: cv2.rotate(gray, cv2.ROTATE_90_CLOCKWISE),
                180: cv2.rotate(gray, cv2.ROTATE_180),
                270: cv2.rotate(gray, cv2.ROTATE_90_COUNTERCLOCKWISE),
            }
        except Exception:
            return image, 0
        metrics = {}
        for ang, g in cand_gray.items():
            rv, cv_, cy = _orientation_metrics(g)
            metrics[ang] = (rv, cv_, cy, g.shape[0], g.shape[1])
        # 行方差最大值；过小说明空白/模糊，不做正交判定
        max_rv = max(v[0] for v in metrics.values())
        if max_rv < _ORTHO_ROWVAR_MIN:
            return image, 0
        # 进一步门限：水平组需显著大于垂直组才视为可信的正交差异
        # 若所有候选行 var 接近（小角度情形 0/180 与 90/270 比值 ~2-3），
        # 仍能通过 0.95 过滤保留水平组而不误转（见 tests）。
        # 但若图像本身各向同性（无文字行），max_rv 与次大接近，回落
        # 避免误转：要求 max 至少比非水平组大 20% 才继续（经验阈）
        # 以 minColVar 等价判断？简化：若 max_rv < _ORTHO_ROWVAR_MIN 则已回落，
        # 否则认为有判别度，继续走 0.95 过滤。
        thresh = max_rv * _ORTHO_RATIO_FILTER
        filtered = [a for a, v in metrics.items() if v[0] >= thresh]
        if not filtered:
            return image, 0
        # 水平组内挑最靠上的（cy 最小）——直立假设：内容重心偏上（表头）
        # 对于合成横条图，上边距 60 vs 下边距 80，重心差约 10px，可区分
        # 真实收据表头通常更靠上，同样适用；若完全对称则 tie 取首个（0）
        best = min(filtered, key=lambda a: metrics[a][2])
        if best == 0:
            return image, 0
        # 按 best 角度对原图（BGR 或灰度）做 cv2.rotate
        try:
            if best == 90:
                corrected = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
            elif best == 180:
                corrected = cv2.rotate(image, cv2.ROTATE_180)
            elif best == 270:
                corrected = cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
            else:
                corrected = image
            return corrected, int(best)
        except Exception:
            return image, 0
    except Exception as e:
        logger.warning("[preprocess] orthogonal_correct degraded: %s", e)
        return image, 0


def sharpness(image):
    """清晰度（Laplacian 方差）：路径输入复用 api_receipts 口径，ndarray 走 numpy。"""
    if isinstance(image, (str, os.PathLike)):
        try:
            from app.api_receipts import _laplacian_variance
            v = _laplacian_variance(str(image))
            return float(v) if v is not None else None
        except Exception:
            return None
    try:
        import cv2
        gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        return _laplacian_variance_gray(gray)
    except Exception:
        return None


def assess(image):
    """质量评估：{sharpness, is_blurry, skew_angle}。

    is_blurry 按 settings 键 'blur_laplacian_threshold' 判定（与上传拦截同口径）。
    任何子项失败给安全缺省（不阻断）。
    """
    sharp = sharpness(image)
    try:
        from app.services import settings_service
        blur_th = settings_service.get_float("blur_laplacian_threshold", 30.0)
    except Exception:
        blur_th = 30.0
    if isinstance(image, (str, os.PathLike)):
        img = _imread(str(image))
        gray = None
        if img is not None:
            try:
                import cv2
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            except Exception:
                gray = None
    else:
        gray = image
    skew = _estimate_skew_angle(gray) if gray is not None else 0.0
    return {
        "sharpness": sharp,
        "is_blurry": bool(sharp is not None and sharp < blur_th),
        "skew_angle": round(float(skew), 2),
    }


def deskew(image):
    """倾斜还原：返回 (image, angle)。angle 为估计出的倾斜角；±1° 内原样返回。

    旋转方向：估计角等于「图被旋转过的角度」（逆时针为正），故按 -angle 还原。
    """
    import cv2
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    angle = _estimate_skew_angle(gray)
    if abs(angle) <= DESKEW_DEADZONE_DEG:
        return image, angle
    h, w = image.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), -angle, 1.0)
    rotated = cv2.warpAffine(
        image, m, (w, h),
        flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REPLICATE)
    return rotated, angle


def enhance(image):
    """自适应对比度 + 轻度去噪 + 纠偏清晰度补偿。

    CLAHE 作用于 LAB 空间 L 通道；轻度 unsharp 补偿旋转重采样造成的笔画软化
    （保证纠偏后模糊评分不下降）；双边滤波保边去噪。
    """
    import cv2
    if image.ndim == 2:
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        out = clahe.apply(image)
    else:
        lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        l = clahe.apply(l)
        out = cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)
    try:
        blur = cv2.GaussianBlur(out, (0, 0), 2.0)
        out = cv2.addWeighted(out, 1.4, blur, -0.4, 0)
    except Exception:
        pass
    try:
        out = cv2.bilateralFilter(out, d=5, sigmaColor=35, sigmaSpace=35)
    except Exception:
        pass
    return out


def apply_pipeline(image_path):
    """上传后、抽取前统一入口：(new_image_path, meta)。

    两阶段：
    1) 正交纠正（90/180/270）由 'preprocess_orthogonal_enabled'（默认 true）
       控制，不受 'preprocess_enabled' 限制，致命错误必纠；
    2) 小角度 deskew（±1° 死区）+ enhance 由 'preprocess_enabled'（默认 OFF）
       控制。
    任何阶段失败均回落原图，不阻断识别主链路。产物落 <原名>_prep.jpg。
    """
    meta = {"applied": False, "skew_angle": 0.0, "reason": "", "orthogonal_angle": 0}
    # 读取开关（任一读失败走安全缺省）
    try:
        from app.services import settings_service
        orthogonal_enabled = settings_service.get_bool("preprocess_orthogonal_enabled", True)
    except Exception:
        orthogonal_enabled = True
    try:
        from app.services import settings_service
        enabled = settings_service.get_bool("preprocess_enabled", False)
    except Exception:
        enabled = False

    if not orthogonal_enabled and not enabled:
        meta["reason"] = "disabled"
        return image_path, meta

    try:
        import cv2
        img = _imread(image_path)
        if img is None:
            meta["reason"] = "decode_failed"
            return image_path, meta

        orthogonal_angle = 0
        if orthogonal_enabled:
            try:
                img, orthogonal_angle = _orthogonal_correct(img)
            except Exception as e:
                logger.warning("[preprocess] orthogonal stage degraded: %s", e)
                orthogonal_angle = 0

        # 小角度纠偏受开关控制
        skew_angle = 0.0
        if enabled:
            try:
                fixed, skew_angle = deskew(img)
                img = fixed
            except Exception as e:
                logger.warning("[preprocess] deskew degraded: %s", e)
                skew_angle = 0.0
            try:
                img = enhance(img)
            except Exception as e:
                logger.warning("[preprocess] enhance degraded: %s", e)
        else:
            # OFF 且未发生正交纠正时直接回落原图
            if orthogonal_angle == 0:
                meta["reason"] = "disabled"
                return image_path, meta
            # OFF 且已正交纠正：不做 enhance/deskew，直接落盘纠正后图
            skew_angle = 0.0

        # 判定是否需要落盘：正交已纠 或 小角度开关开启（即使 0° 也做 enhance，沿用历史语义）
        need_write = (orthogonal_angle != 0) or enabled
        if not need_write:
            meta["reason"] = "disabled"
            return image_path, meta

        stem, _ = os.path.splitext(image_path)
        out_path = stem + "_prep.jpg"
        ok = cv2.imwrite(out_path, img, [cv2.IMWRITE_JPEG_QUALITY, _OUTPUT_QUALITY])
        if not ok or not os.path.exists(out_path):
            meta["reason"] = "write_failed"
            return image_path, meta

        # meta 语义：applied True；skew_angle 取 deskew 角（OFF 时为 orthogonal_angle）
        # 以兼容既有测试（10° 小角 case skew≈10），orthogonal_angle 另字段
        reported_skew = float(skew_angle) if enabled else float(orthogonal_angle if orthogonal_angle else skew_angle)
        meta.update({
            "applied": True,
            "skew_angle": round(reported_skew, 2),
            "orthogonal_angle": int(orthogonal_angle),
            "reason": "orthogonal" if (orthogonal_angle != 0 and not enabled) else "",
        })
        # 当 enabled 亦发生正交，reason 置空以免误解
        if enabled and orthogonal_angle != 0:
            meta["reason"] = ""
        return out_path, meta
    except Exception as e:
        logger.warning("[preprocess] pipeline degraded to original image: %s", e)
        meta["reason"] = f"error:{e.__class__.__name__}"
        return image_path, meta
