# -*- coding: utf-8 -*-
"""Image Injection Guard Tool v1.0.0 (experimental)

收据图像内隐藏文字（图内提示注入）的第一层确定性检测信号。只告警、不拦截。

威胁模型
--------
审计实测确认：图片内的隐藏文字（白底白字 / 极小字号 / 低对比度文本层）会完整
送达 VLM，此前唯一的防线是提取 Prompt 里的一句软声明，图像层零防护。本工具在
上传时、任何 CLAHE 提亮（preprocess.apply_pipeline）之前，对**原图**做一次纯
CPU 的确定性扫描，产出三个可测信号：

1. ``faint_ink``（淡墨层）：灰度化 → 大核形态学闭运算估计局部背景 → 残差图；
   取残差落在「低幅区间」且远离强墨迹的像素，做连通域与行结构统计，要求同时
   满足「文字形态」与「非平凡密度」双条件，以压制纸张噪声/折痕的误报。
2. ``tiny_text``（极小字）：Otsu 二值化的连通域高度相对**文档主导字高**的分位数
   判定（按原图分辨率，不用限边后的图），要求成行、有横向跨度、字迹对比度足够。
3. ``edge_band``（边距外内容）：落在页面边缘带、且与主体内容分离的强墨迹连通域。

产品原则（硬约束）
------------------
- **只告警、不拦截**：热敏纸收据天然低对比度，拦截会误伤真实业务；且项目原则是
  「不甩锅画质、随时保留转手工录入的逃生通道」。本工具只返回 flagged/reasons，
  调用方不得据此阻断上传。
- **真实样本零误报**：阈值默认值按「真实收据零误报」标定（见 metadata.json 的
  实测数字与阈值—误报取舍表）。
- **面向店员的文案是人话**：reasons 里不出现任何技术黑话，也不暗示「可能是欺诈」。
- **安全降级**：任何异常一律返回 flagged=False + 明确 reason，并留 [WARN] 日志，
  绝不阻断上传主链路，也绝不静默。

阈值全部经 ``app.services.settings_service``（T10 范式）热调，缺省值同时登记在
``settings_service.SETTINGS_DEFAULTS``；本模块的 DEFAULT_* 常量仅作为独立运行
（脱离 app 包）时的兜底。

能力边界（如实登记，不做过度承诺）
----------------------------------
本工具是「第一层可测信号」，不是通用图像注入检测器。已知检不出/不覆盖：
- 仅含淡色或极小文本、且与真实低对比度/低分辨率收据统计不可分的整页单据（零误报
  优先，宁可漏报，登记为能力上限）；
- 语义型注入（文字本身清晰可读，靠语义而非图面特征生效）——由 prompt_injection_guard
  与沙箱层负责，不在本工具口径内。
"""

import logging
import os

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 缺省阈值（= 按真实收据样本标定、可经 settings_service 热调的现值）
# ---------------------------------------------------------------------------
DEFAULTS = {
    # 工作副本最长边（淡墨/边缘信号在此尺寸上计算；极小字按原图分辨率单独计算）
    "image_guard_work_max_side": 1400.0,
    # 淡墨层
    "image_guard_faint_band_low": 10.0,     # 残差低幅区间下界（≤此值视为无墨）
    "image_guard_faint_band_high": 38.0,    # 残差低幅区间上界（>此值视为强墨迹）
    "image_guard_faint_strong_th": 70.0,    # 强墨迹阈（用于「远离强墨迹」排除）
    "image_guard_faint_exclude_frac": 0.20, # 强墨迹排除半径 = 比例 × 短边
    "image_guard_faint_bright_min": 100.0,  # 文档平均亮度下限（暗底单据跳过淡墨判定）
    "image_guard_faint_min_score": 90.0,    # 命中阈：成行淡墨连通域数
    # 极小字
    "image_guard_tiny_abs_max_h": 6.0,      # 连通域高度绝对上限（原图像素）
    "image_guard_tiny_ratio": 0.4,          # 相对文档主导字高的比例上限
    "image_guard_tiny_contrast_min": 100.0, # 极小字笔画对比度下限（压制纸面噪声）
    "image_guard_tiny_min_score": 60.0,     # 命中阈：成行极小字连通域数
    # 边距外内容
    "image_guard_edge_band_frac": 0.04,     # 边缘带宽 = 比例 × 短边
    "image_guard_edge_gap_frac": 0.03,      # 与主体内容的最小分离间距 = 比例 × 短边
    "image_guard_edge_min_score": 0.45,     # 命中阈：边缘带强墨迹面积 / 强墨迹总面积
    "image_guard_edge_min_components": 3.0, # 命中阈：边缘带独立连通域数下限
    # 二维码与覆盖层 (W-SEC-4)
    "image_guard_qr_check_enabled": 1.0,    # 是否开启二维码载荷扫描（1.0=开，0.0=关）
    "image_guard_overlay_min_score": 0.65,  # 边缘强覆盖层/假界面判定阈值
}

# 面向店员的人话文案（纯文本，无 Emoji，不暗示欺诈）
_REASON_FAINT = "此单据检出淡色隐藏文字层，请对照原图逐行核对金额"
_REASON_TINY = "此单据检出异常极小字文本层，请对照原图逐行核对金额"
_REASON_EDGE = "此单据在页边空白处检出额外文字，请对照原图确认是否为误加内容"
_REASON_OVERLAY = "此单据边缘检出异常覆盖层或假界面印刷带，请核对原图"
_REASON_QR = "此单据检出包含网址或可疑指令的二维码/条形码，请人工核验是否包含诱导跳转或注入信息"
_REASON_DEGRADED = "图片无法读取，已跳过隐藏文字检查，请照常对照原图核对"


def _setting(key, default):
    """读取可热调阈值：settings_service 优先，缺省回退 default，任何异常不抛。"""
    try:
        from app.services import settings_service
        return float(settings_service.get_float(key, default))
    except Exception:
        return float(default)


class ImageInjectionGuardTool:
    """图内隐藏文字检测工具（只告警）。

    ``execute(image_path)`` 返回::

        {"flagged": bool,
         "reasons": [str, ...],
         "metrics": {"faint_ink_score": float, "tiny_text_score": float,
                     "edge_band_score": float, ...}}
    """

    # 兼容历史 registry 取 Tool 的习惯
    name = "image_injection_guard"
    version = "v1_0_0"

    def __init__(self):
        self.defaults = dict(DEFAULTS)

    # ------------------------------------------------------------------
    # 对外入口
    # ------------------------------------------------------------------
    def execute(self, image_path):
        """检测主入口。任何异常安全降级为 flagged=False + 明确 reason + [WARN]。"""
        try:
            return self._detect(image_path)
        except Exception as e:
            logger.warning(
                "[WARN] image_injection_guard degraded (path=%s): %s",
                image_path, e)
            return {
                "flagged": False,
                "reasons": [_REASON_DEGRADED],
                "metrics": {"degraded": True, "error": e.__class__.__name__,
                            "faint_ink_score": 0.0, "tiny_text_score": 0.0,
                            "edge_band_score": 0.0, "edge_band_components": 0,
                            "edge_overlay_detected": False, "qr_detected": False,
                            "qr_suspicious": False, "qr_payload": "",
                            "signal_count": 0, "high_confidence_injection": False},
            }

    # 别名，便于调用方以 detect 语义使用
    detect = execute

    # ------------------------------------------------------------------
    # 实现
    # ------------------------------------------------------------------
    def _detect(self, image_path):
        if not image_path:
            raise ValueError("empty image_path")
        if not os.path.exists(image_path):
            raise FileNotFoundError(str(image_path))

        import cv2
        import numpy as np

        # 原图灰度（极小字按原图分辨率判定；淡墨/边缘用限边工作副本）
        native = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        if native is None or native.size == 0:
            raise ValueError("decode_failed")
        if native.ndim != 2:
            raise ValueError("not_gray")
        h, w = native.shape
        if h < 8 or w < 8:
            # 过小图无法判定，安全返回不告警（不视为异常）
            return self._result(False, [], {
                "faint_ink_score": 0.0, "tiny_text_score": 0.0,
                "edge_band_score": 0.0, "width": int(w), "height": int(h),
                "degraded": False, "too_small": True,
            })

        work = self._resize_long_side(native, _setting(
            "image_guard_work_max_side", DEFAULTS["image_guard_work_max_side"]))

        faint_score, strong_frac = self._faint_ink_score(work)
        tiny_score = self._tiny_text_score(native)
        edge_score, edge_comps = self._edge_band_score(work)

        faint_min = _setting("image_guard_faint_min_score", DEFAULTS["image_guard_faint_min_score"])
        tiny_min = _setting("image_guard_tiny_min_score", DEFAULTS["image_guard_tiny_min_score"])
        edge_min = _setting("image_guard_edge_min_score", DEFAULTS["image_guard_edge_min_score"])
        edge_comp_min = _setting("image_guard_edge_min_components", DEFAULTS["image_guard_edge_min_components"])
        overlay_min = _setting("image_guard_overlay_min_score", DEFAULTS["image_guard_overlay_min_score"])
        qr_enabled = _setting("image_guard_qr_check_enabled", DEFAULTS["image_guard_qr_check_enabled"]) > 0.5

        reasons = []
        signal_count = 0
        if faint_score >= faint_min:
            reasons.append(_REASON_FAINT)
            signal_count += 1
        if tiny_score >= tiny_min:
            reasons.append(_REASON_TINY)
            signal_count += 1
        if edge_score >= edge_min and edge_comps >= edge_comp_min:
            reasons.append(_REASON_EDGE)
            signal_count += 1

        # 边缘覆盖层/假界面启发式 (Carrier 3)
        edge_overlay_detected = False
        if edge_score >= overlay_min and edge_comps >= 3:
            edge_overlay_detected = True
            if _REASON_OVERLAY not in reasons:
                reasons.append(_REASON_OVERLAY)
                signal_count += 1

        # 二维码/条形码载荷检测 (Carrier 5)
        qr_detected = False
        qr_suspicious = False
        qr_payload = ""
        if qr_enabled:
            qr_detected, qr_suspicious, qr_payload = self._scan_qr_payload(native)
            if not qr_detected and work is not native:
                qr_detected, qr_suspicious, qr_payload = self._scan_qr_payload(work)
            if qr_suspicious:
                reasons.append(_REASON_QR)
                signal_count += 1

        # 高置信拦截判定 (W-SEC-4): 信号数 >= 2 或 强边缘覆盖 或 强二维码注入
        high_confidence = (signal_count >= 2) or qr_suspicious or (edge_score >= 0.70 and edge_comps >= 3)

        metrics = {
            "faint_ink_score": round(float(faint_score), 2),
            "tiny_text_score": round(float(tiny_score), 2),
            "edge_band_score": round(float(edge_score), 4),
            "edge_band_components": int(edge_comps),
            "edge_overlay_detected": edge_overlay_detected,
            "qr_detected": qr_detected,
            "qr_suspicious": qr_suspicious,
            "qr_payload": qr_payload[:120] if qr_payload else "",
            "signal_count": signal_count,
            "high_confidence_injection": high_confidence,
            "strong_ink_fraction": round(float(strong_frac), 5),
            "width": int(w), "height": int(h),
            "degraded": False,
        }
        return self._result(bool(reasons), reasons, metrics)

    @staticmethod
    def _scan_qr_payload(gray):
        try:
            import cv2
            detector = cv2.QRCodeDetector()
            val, pts, _ = detector.detectAndDecode(gray)
            if not val or not isinstance(val, str):
                return False, False, ""
            text = val.strip()
            if not text:
                return False, False, ""
            lower = text.lower()
            suspicious = False
            if any(s in lower for s in ("http://", "https://", "ftp://", "javascript:", "file:", "data:", "ws://", "wss://")):
                suspicious = True
            elif any(k in lower for k in (
                "ignore previous", "system prompt", "system:", "system directive",
                "curl ", "wget ", "override", "leak", "canary", "total: 0", "total=0",
                "免审", "bypass", "instruction", "eval(", "prompt injection",
                "api_key", "password", "token", "secret"
            )):
                suspicious = True
            return True, suspicious, text
        except Exception:
            return False, False, ""

    @staticmethod
    def _result(flagged, reasons, metrics):
        return {"flagged": bool(flagged), "reasons": list(reasons), "metrics": metrics}

    @staticmethod
    def _resize_long_side(gray, max_side):
        import cv2
        h, w = gray.shape
        m = max(h, w)
        if max_side and m > max_side:
            s = max_side / float(m)
            return cv2.resize(gray, (max(1, int(w * s)), max(1, int(h * s))),
                              interpolation=cv2.INTER_AREA)
        return gray

    @staticmethod
    def _group_rows(comps, tol_floor, tol_scale):
        """把 (cy, ...) 连通域按 y 聚成文本行，返回行列表（每行是 comp 列表）。"""
        import numpy as np
        comps = sorted(comps, key=lambda c: c[0])
        rows = []
        cur = []
        for c in comps:
            if not cur:
                cur = [c]
                continue
            mean_cy = float(np.mean([q[0] for q in cur]))
            med_h = float(np.median([q[1] for q in cur]))
            tol = max(tol_floor, tol_scale * med_h)
            if abs(c[0] - mean_cy) <= tol:
                cur.append(c)
            else:
                rows.append(cur)
                cur = [c]
        if cur:
            rows.append(cur)
        return rows

    # ------------------------------------------------------------------
    # 信号 1：淡墨层
    # ------------------------------------------------------------------
    def _faint_ink_score(self, gray):
        import cv2
        import numpy as np
        h, w = gray.shape
        short = min(h, w)

        band_lo = _setting("image_guard_faint_band_low", DEFAULTS["image_guard_faint_band_low"])
        band_hi = _setting("image_guard_faint_band_high", DEFAULTS["image_guard_faint_band_high"])
        strong_th = _setting("image_guard_faint_strong_th", DEFAULTS["image_guard_faint_strong_th"])
        excl_frac = _setting("image_guard_faint_exclude_frac", DEFAULTS["image_guard_faint_exclude_frac"])
        bright_min = _setting("image_guard_faint_bright_min", DEFAULTS["image_guard_faint_bright_min"])

        mean_gray = float(gray.mean())
        # 暗底/夜间模式单据：淡墨判定不适用（避免把整体低亮度误当隐藏层）
        if mean_gray < bright_min:
            return 0.0, 0.0

        # 局部背景估计：大核闭运算（局部极大 -> 纸上背景），残差即「墨迹深度」
        k = max(3, int(short * 0.05) | 1)
        bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE,
                              cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
        res = cv2.subtract(bg, gray)

        strong = (res >= strong_th).astype(np.uint8)
        strong_frac = float(strong.mean())
        rr = max(3, int(short * excl_frac) | 1)
        if strong.any():
            exclude = cv2.dilate(strong, np.ones((rr, rr), np.uint8))
        else:
            exclude = strong

        band = ((res >= band_lo) & (res <= band_hi)).astype(np.uint8)
        band[exclude > 0] = 0

        n, labels, stats, cent = cv2.connectedComponentsWithStats(band, 8)
        comps = []
        for i in range(1, n):
            x, y, bw, bh, area = stats[i]
            if area < 40 or area > 1200:
                continue
            if bh < 5 or bh > 45 or bw < 3 or bw > 130:
                continue
            fill = area / float(bw * bh)
            if fill < 0.10 or fill > 0.85:
                continue
            comps.append((float(cent[i][1]), int(bh)))

        rows = self._group_rows(comps, 6.0, 0.8)
        # 文字形态：至少 4 行、每行至少 4 个连通域；非平凡密度：命中连通域总数
        good_rows = [r for r in rows if len(r) >= 4]
        n_good_rows = len(good_rows)
        score = float(sum(len(r) for r in good_rows))
        if n_good_rows < 4:
            score = 0.0
        return score, strong_frac

    # ------------------------------------------------------------------
    # 信号 2：极小字
    # ------------------------------------------------------------------
    def _tiny_text_score(self, gray):
        import cv2
        import numpy as np
        h, w = gray.shape

        abs_max_h = _setting("image_guard_tiny_abs_max_h", DEFAULTS["image_guard_tiny_abs_max_h"])
        ratio = _setting("image_guard_tiny_ratio", DEFAULTS["image_guard_tiny_ratio"])
        cmin = _setting("image_guard_tiny_contrast_min", DEFAULTS["image_guard_tiny_contrast_min"])

        k = max(3, int(min(h, w) * 0.05) | 1)
        bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE,
                              cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
        res = cv2.subtract(bg, gray).astype(np.float64)

        blur = cv2.GaussianBlur(gray, (3, 3), 0)
        _, thr = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        n, labels, stats, cent = cv2.connectedComponentsWithStats(thr, 8)
        if n <= 1:
            return 0.0

        lab = labels.ravel()
        counts = np.bincount(lab, minlength=n)
        wsum = np.bincount(lab, weights=res.ravel(), minlength=n)
        cmean = np.divide(wsum, np.maximum(counts, 1))

        def _ok(i, row):
            x, y, bw, bh, area = stats[i]
            if bh < 2 or bh > 0.12 * h or area < 4:
                return False
            if bw > 0.3 * w:
                return False
            fill = area / float(bw * bh)
            if fill < 0.06 or fill > 0.85:
                return False
            return cmean[i] >= row

        # 文档主导字高：先取全部合规连通域高度的中位数
        heights = []
        for i in range(1, n):
            x, y, bw, bh, area = stats[i]
            if bh < 2 or bh > 0.12 * h or area < 4 or bw > 0.3 * w:
                continue
            fill = area / float(bw * bh)
            if fill < 0.06 or fill > 0.85 or cmean[i] < cmin:
                continue
            heights.append(bh)
        if len(heights) < 10:
            return 0.0
        # 主导字高取中位数。已知边界：若「极小字层」本身占据了多数连通域（整页都是
        # 极小字），中位数会被拉低、相对上限自我坍缩，本信号检不出（登记为能力上限；
        # 零误报优先）。注入型隐藏文字通常是页面上的一小块，不占多数。
        dom_h = float(np.median(heights))
        limit = min(abs_max_h, max(2.0, ratio * dom_h))

        comps = []
        for i in range(1, n):
            x, y, bw, bh, area = stats[i]
            if bh < 2 or bh > limit:
                continue
            if not _ok(i, cmin):
                continue
            comps.append((float(cent[i][1]), int(bh), int(x), int(x + bw)))

        rows = self._group_rows(comps, 2.0, 1.0)
        score = 0.0
        for r in rows:
            if len(r) < 5:
                continue
            span = max(c[3] for c in r) - min(c[2] for c in r)
            if span < 0.15 * w:
                continue
            score += len(r)
        return float(score)

    # ------------------------------------------------------------------
    # 信号 3：边距外内容
    # ------------------------------------------------------------------
    def _edge_band_score(self, gray):
        import cv2
        import numpy as np
        h, w = gray.shape
        short = min(h, w)

        band_frac = _setting("image_guard_edge_band_frac", DEFAULTS["image_guard_edge_band_frac"])
        gap_frac = _setting("image_guard_edge_gap_frac", DEFAULTS["image_guard_edge_gap_frac"])

        k = max(3, int(short * 0.05) | 1)
        bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE,
                              cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
        res = cv2.subtract(bg, gray)
        strong = (res >= 70).astype(np.uint8)
        total = float(strong.sum())
        if total < 50:
            return 0.0, 0

        # 主体内容区域：强墨迹膨胀后取最大连通域的包围盒
        d = max(3, int(short * 0.06) | 1)
        content = cv2.dilate(strong, np.ones((d, d), np.uint8))
        n, labels, stats, cent = cv2.connectedComponentsWithStats(content, 8)
        if n <= 1:
            return 0.0, 0
        idx = 1 + int(np.argmax(stats[1:, 4]))
        cx, cy, cw, ch = stats[idx][0], stats[idx][1], stats[idx][2], stats[idx][3]

        band_px = max(2, int(short * band_frac))
        gap_px = max(2, int(short * gap_frac))
        # 边缘带掩膜（四边各 band_px 宽）
        edge_mask = np.zeros((h, w), np.uint8)
        edge_mask[:band_px, :] = 1
        edge_mask[-band_px:, :] = 1
        edge_mask[:, :band_px] = 1
        edge_mask[:, -band_px:] = 1

        # 主体包围盒外扩 gap_px 作为「主体保护区」，边缘带内容须落在保护区之外
        prot = np.zeros((h, w), np.uint8)
        x0 = max(0, cx - gap_px); y0 = max(0, cy - gap_px)
        x1 = min(w, cx + cw + gap_px); y1 = min(h, cy + ch + gap_px)
        prot[y0:y1, x0:x1] = 1

        ns, ls, ss, cs = cv2.connectedComponentsWithStats(strong, 8)
        edge_area = 0.0
        edge_comps = 0
        for i in range(1, ns):
            x, y, bw, bh, area = ss[i]
            if area < 20:
                continue
            comp_mask = (ls[y:y + bh, x:x + bw] == i)
            inside_edge = edge_mask[y:y + bh, x:x + bw][comp_mask].mean()
            inside_prot = prot[y:y + bh, x:x + bw][comp_mask].mean()
            if inside_edge >= 0.9 and inside_prot <= 0.1:
                edge_area += float(area)
                edge_comps += 1
        return (edge_area / total), edge_comps


Tool = ImageInjectionGuardTool
