# -*- coding: utf-8 -*-
"""
Huama (Suzhou Numerals) & Cursive Confidence Evaluator Tool v1.0.0 (Production Active)
针对香港街市花码（苏州码子）与草书连笔的确定性特征检测、花码转译与置信度校准压降工具 (Gap 7 专项治理)。
"""

import re
from typing import List, Dict, Any, Tuple


class HuamaEvaluatorTool:
    """
    街市花码（苏州码子）检测、转译辅助与置信度硬性校准器。
    
    核心规则：
    1. Unicode 苏州码子字符集：
       - 〡 (1), 〢 (2), 〣 (3), 〤 (4), 〥 (5), 〦 (6), 〧 (7), 〨 (8), 〩 (9)
       - 〸 (10), 〹 (20), 〺 (30), 卄 (20), 卅 (30), 卌 (40)
    2. 发现花码或草书特征时，绝不允许模型自报 0.95+ 高置信度，强制压降置信度至 <= 0.45。
    3. 为明细行打上 contains_huama: true 与 warning 标签，驱动前端高亮警示与人工复核流。
    4. 提供确定性花码到阿拉伯数字的转换字典辅助初步结构化。
    """

    # 苏州码子 Unicode 范围与变体
    HUAMA_CHARS = "〡〢〣〤〥〦〧〨〩〸〹〺卄卅卌〇"
    HUAMA_MAP = {
        "〡": "1", "〢": "2", "〣": "3", "〤": "4", "〥": "5",
        "〦": "6", "〧": "7", "〨": "8", "〩": "9", "〇": "0",
        "〸": "10", "〹": "20", "〺": "30", "卄": "20", "卅": "30", "卌": "40"
    }

    HUAMA_REGEX = re.compile(r"[" + HUAMA_CHARS + r"]")

    def contains_huama(self, text: str) -> bool:
        """检测字符串中是否包含苏州码子/花码字符。"""
        if not text:
            return False
        return bool(self.HUAMA_REGEX.search(text))

    def translate_huama_digits(self, text: str) -> Tuple[str, bool]:
        """将包含花码的文本转换为阿拉伯数字表达，并返回是否包含花码。"""
        if not text:
            return "", False
        has_h = self.contains_huama(text)
        if not has_h:
            return text, False

        res = []
        for char in text:
            if char in self.HUAMA_MAP:
                res.append(self.HUAMA_MAP[char])
            else:
                res.append(char)
        return "".join(res), True

    @staticmethod
    def _safe_confidence(value: Any, default: float = 0.40) -> float:
        """把 LLM 可能给出的非数值置信度（"high"/""/None）安全归一为浮点。

        why：本工具被白名单之后调用，若此处 float() 抛异常，异常会被 extract_chain
        的外层 except 吞掉并跳过整段后处理（Gap1-9 静默失效）。宁可回落默认值也不能抛。
        """
        try:
            v = float(value)
        except (TypeError, ValueError):
            return default
        if v != v:  # NaN
            return default
        return v

    def calibrate_confidence_and_flags(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        全面扫描单据 payload，若检测到花码或草书，强制压降置信度并打上复核警示标记。
        """
        has_any_huama = False
        items = payload.get("items", [])

        for it in items:
            name = str(it.get("name") or "")
            unit = str(it.get("unit") or "")
            raw_text = f"{name} {unit} {it.get('quantity', '')} {it.get('unit_price', '')} {it.get('amount', '')}"

            if self.contains_huama(raw_text) or it.get("contains_huama"):
                has_any_huama = True
                it["contains_huama"] = True
                # 强制压降置信度至 0.40（触发前端 low_confidence 警示底色）
                it["confidence"] = min(self._safe_confidence(it.get("confidence")), 0.40)
                it["unit_conversion_warning"] = "包含街市花码，需人工核验"
                # 尝试辅助翻译品名中的花码
                trans_name, _ = self.translate_huama_digits(name)
                it["name"] = trans_name

        if has_any_huama:
            payload["contains_huama"] = True
            # 整单置信度压降至 <= 0.40
            payload["confidence"] = min(self._safe_confidence(payload.get("confidence")), 0.40)
            # 添加全局待复核警告。math_warnings 已进契约白名单，LLM 可能给字符串，
            # 必须先归一为 list，否则 append 抛异常会让整段后处理静默失效。
            warnings = payload.get("math_warnings")
            if isinstance(warnings, str):
                warnings = [warnings] if warnings.strip() else []
            elif not isinstance(warnings, list):
                warnings = []
            else:
                warnings = list(warnings)
            if "检测到街市花码（苏州码子），已强制降权并推送人工复核" not in warnings:
                warnings.append("检测到街市花码（苏州码子），已强制降权并推送人工复核")
            payload["math_warnings"] = warnings

        return payload


Tool = HuamaEvaluatorTool
