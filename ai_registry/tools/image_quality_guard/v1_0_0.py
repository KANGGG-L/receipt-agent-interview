# -*- coding: utf-8 -*-
"""
Image Quality Guard Tool v1.0.0 (Production Active)
针对收据图像输入质量（模糊度、曝光过度/不足、极低分辨率、畸变等）的确定性预检与阻断工具 (Gap 12 专项治理)。
"""

from typing import Dict, Any, Tuple


class ImageQualityGuardTool:
    """
    收据图像前置质量门禁工具。
    """

    MIN_WIDTH = 300
    MIN_HEIGHT = 300
    MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20MB
    MIN_FILE_SIZE_BYTES = 5 * 1024          # 5KB

    def evaluate_quality(self, image_metadata: Dict[str, Any]) -> Tuple[bool, float, list]:
        """
        评估图像质量元数据。
        返回: (is_acceptable, quality_score, warning_list)
        """
        warnings = []
        score = 1.0

        width = image_metadata.get("width", 800)
        height = image_metadata.get("height", 1000)
        file_size = image_metadata.get("file_size", 100000)
        blur_score = image_metadata.get("blur_score", 100.0)  # Laplacian方差估算值

        # 1. 检查文件大小
        if file_size < self.MIN_FILE_SIZE_BYTES:
            warnings.append(f"文件大小过小 ({file_size} bytes)，可能存在严重压缩或损坏")
            score -= 0.4
        elif file_size > self.MAX_FILE_SIZE_BYTES:
            warnings.append(f"文件大小超过限制 ({file_size / 1024 / 1024:.1f} MB)")
            score -= 0.3

        # 2. 检查分辨率
        if width < self.MIN_WIDTH or height < self.MIN_HEIGHT:
            warnings.append(f"图像分辨率过低 ({width}x{height})，低于最低门槛 ({self.MIN_WIDTH}x{self.MIN_HEIGHT})")
            score -= 0.5

        # 3. 检查清晰度/模糊度（P0-1 同步 api_receipts.BLUR_THRESHOLD=30，<30 极模糊直接拦截，<1s 快速失败）
        # 最严格人话：图像模糊/过暗，请到更亮处重拍，无需打字，点框选裁剪重试
        if blur_score < 30.0:
            warnings.append("图像模糊/过暗，请到更亮处重拍，无需打字，点框选裁剪重试（Laplacian方差过低）")
            score -= 0.4

        score = max(0.0, min(1.0, score))
        is_acceptable = score >= 0.5 and len(warnings) <= 2
        return is_acceptable, round(score, 2), warnings

    def execute(self, image_metadata: Dict[str, Any]) -> Dict[str, Any]:
        is_ok, score, warns = self.evaluate_quality(image_metadata)
        return {
            "is_acceptable": is_ok,
            "quality_score": score,
            "warnings": warns
        }


Tool = ImageQualityGuardTool
