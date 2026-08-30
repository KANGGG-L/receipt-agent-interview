# -*- coding: utf-8 -*-
"""上传预处理纠偏（T10 Gap E4）：assess / deskew / enhance。

- assess(image): 清晰度 + 模糊判定 + 倾斜角估计（复用 api_receipts 的
  Laplacian 口径；ndarray 输入走同参数 numpy 实现）
- deskew(image): OpenCV 最小外接矩形估计倾斜角并旋转还原；±1° 内不纠偏
- enhance(image): 自适应对比度（CLAHE，LAB 空间 L 通道）+ 轻度去噪（双边滤波）
- apply_pipeline(image_path): 上传后、抽取前的统一入口。开关走 settings
  键 'preprocess_enabled'（默认 OFF，等灰测数据决定是否默认开启）；
  任何失败（含 HEIC 无法解码）回落原图，绝不阻断识别主链路。
"""

import logging
import os

logger = logging.getLogger(__name__)

# ±1° 以内视作已摆正，不纠偏（避免轻微抖动引入重采样噪声）
DESKEW_DEADZONE_DEG = 1.0
# 纠偏输出统一为 JPEG（识别腿输入兼容），质量 95 保细节
_OUTPUT_QUALITY = 95


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
    """读图为 BGR ndarray；中文/HEIC 等 cv2 无法解码时回退 PIL（仍失败返回 None）。"""
    try:
        import cv2
        img = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if img is not None:
            return img
    except Exception:
        pass
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
        return arr
    except Exception:
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

    开关 'preprocess_enabled' 默认 OFF：关闭或任何失败时原样返回原路径。
    产物落 <原名>_prep.jpg（同目录），不覆盖原图（审计与回看需要原件）。
    """
    meta = {"applied": False, "skew_angle": 0.0, "reason": ""}
    try:
        from app.services import settings_service
        enabled = settings_service.get_bool("preprocess_enabled", False)
    except Exception:
        enabled = False
    if not enabled:
        meta["reason"] = "disabled"
        return image_path, meta
    try:
        import cv2
        img = _imread(image_path)
        if img is None:
            meta["reason"] = "decode_failed"
            return image_path, meta
        fixed, angle = deskew(img)
        out = enhance(fixed)
        stem, _ = os.path.splitext(image_path)
        out_path = stem + "_prep.jpg"
        ok = cv2.imwrite(out_path, out, [cv2.IMWRITE_JPEG_QUALITY, _OUTPUT_QUALITY])
        if not ok or not os.path.exists(out_path):
            meta["reason"] = "write_failed"
            return image_path, meta
        meta.update({"applied": True, "skew_angle": round(float(angle), 2)})
        return out_path, meta
    except Exception as e:
        logger.warning("[preprocess] pipeline degraded to original image: %s", e)
        meta["reason"] = f"error:{e.__class__.__name__}"
        return image_path, meta
