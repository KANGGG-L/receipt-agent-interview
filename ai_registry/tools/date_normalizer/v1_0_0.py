# -*- coding: utf-8 -*-
"""
Hong Kong Date Normalizer Tool v1.0.0 (Production Active)
针对香港餐饮单据本地化日期格式 (DD/MM/YYYY, DD-MM-YY, DD-Mon-YYYY) 的确定性解析与标准 YYYY-MM-DD 归一化工具 (Gap 5 专项治理)。
"""

import re
from datetime import datetime
from typing import Optional, Tuple


class DateNormalizerTool:
    """
    香港本地化单据日期解析与归一化工具。
    
    核心规则：
    1. 香港商业单据默认优先采用英国/香港惯例：DD/MM/YYYY 与 DD-MM-YY。
    2. 当出现 06/08/2026 时，确定性解析为 2026-08-06（8月6日），彻底纠正美式 MM/DD/YYYY 倒置。
    3. 支持英文月份缩写：06-AUG-2026, 21 Aug 2026 -> 2026-08-21。
    4. 支持两位年份智能判定（标准 Pivot 规则：<=30 转为 2000+，>30 转为 1900+，如 26->2026, 99->1999）。
    5. 支持中文年月日：2026年8月21日, 26年8月6日 -> 2026-08-06。
    6. 输出严格满足 Pydantic Contract YYYY-MM-DD 正则。
    """

    MONTH_MAP = {
        "jan": 1, "january": 1, "一月": 1,
        "feb": 2, "february": 2, "二月": 2,
        "mar": 3, "march": 3, "三月": 3,
        "apr": 4, "april": 4, "四月": 4,
        "may": 5, "五月": 5,
        "jun": 6, "june": 6, "六月": 6,
        "jul": 7, "july": 7, "七月": 7,
        "aug": 8, "august": 8, "八月": 8,
        "sep": 9, "september": 9, "sept": 9, "九月": 9,
        "oct": 10, "october": 10, "十月": 10,
        "nov": 11, "november": 11, "十一月": 11,
        "dec": 12, "december": 12, "十二月": 12,
    }

    def _expand_year(self, y_raw: int) -> int:
        """两位年份展开（<=30 映射到 2000 年代，>30 映射到 1900 年代）。"""
        if y_raw < 100:
            return (2000 + y_raw) if y_raw <= 30 else (1900 + y_raw)
        return y_raw

    def normalize_date(self, raw_date_str: str, default_year: int = 2026) -> Tuple[Optional[str], str]:
        """
        解析并归一化日期字符串。
        返回: (normalized_date_str_or_None, format_detected)
        """
        s = (raw_date_str or "").strip()
        if not s:
            return None, "empty_input"

        # 1. 已经是标准 YYYY-MM-DD
        m_std = re.match(r"^(\d{4})[-/\.](\d{1,2})[-/\.](\d{1,2})$", s)
        if m_std:
            y, m, d = int(m_std.group(1)), int(m_std.group(2)), int(m_std.group(3))
            return self._format_valid_date(y, m, d, "standard_ymd")

        # 2. 中文格式: 2026年8月6日 或 26年08月06日 或 99年8月6日
        m_cn = re.match(r"^(\d{2,4})年\s*(\d{1,2})月\s*(\d{1,2})[日號号]?$", s)
        if m_cn:
            y_raw, m, d = int(m_cn.group(1)), int(m_cn.group(2)), int(m_cn.group(3))
            y = self._expand_year(y_raw)
            return self._format_valid_date(y, m, d, "chinese_ymd")

        # 3. 英文月份格式: 06-AUG-2026, 6 Aug 2026, 21-Sept-26, 15-Jun-99
        m_en = re.match(r"^(\d{1,2})[-/\s]+([a-zA-Z\u4e00-\u9fa5]+)[-/\s]+(\d{2,4})$", s)
        if m_en:
            d = int(m_en.group(1))
            mon_str = m_en.group(2).lower()
            y_raw = int(m_en.group(3))
            y = self._expand_year(y_raw)
            if mon_str in self.MONTH_MAP:
                m = self.MONTH_MAP[mon_str]
                return self._format_valid_date(y, m, d, "english_dmy")

        # 4. 港式 DD/MM/YYYY 或 DD-MM-YY 格式 (Gap 5 核心)
        m_dmy = re.match(r"^(\d{1,2})[-/\.](\d{1,2})[-/\.](\d{2,4})$", s)
        if m_dmy:
            p1 = int(m_dmy.group(1))
            p2 = int(m_dmy.group(2))
            y_raw = int(m_dmy.group(3))
            y = self._expand_year(y_raw)

            # 若 p1 > 12，则 p1 必为 Day，p2 必为 Month
            if p1 > 12 and 1 <= p2 <= 12:
                return self._format_valid_date(y, p2, p1, "hk_dmy_unambiguous")

            # 若 p2 > 12 且 1 <= p1 <= 12，则可能为 MM/DD/YYYY
            if p2 > 12 and 1 <= p1 <= 12:
                return self._format_valid_date(y, p1, p2, "us_mdy_fallback")

            # 歧义日期 (如 06/08/2026): 香港本地商业单据确定性以 DD/MM/YYYY 为准
            if 1 <= p1 <= 12 and 1 <= p2 <= 12:
                return self._format_valid_date(y, p2, p1, "hk_dmy_ambiguous_resolved")

        # 5. 紧凑型无分隔符格式: 20260806 (YYYYMMDD) 或 060826 (DDMMYY)
        if re.match(r"^\d{8}$", s):
            y, m, d = int(s[:4]), int(s[4:6]), int(s[6:8])
            return self._format_valid_date(y, m, d, "compact_yyyymmdd")

        if re.match(r"^\d{6}$", s):
            # 港式 DDMMYY (如 060826 -> 2026-08-06, 150699 -> 1999-06-15)
            d, m, y_raw = int(s[:2]), int(s[2:4]), int(s[4:6])
            y = self._expand_year(y_raw)
            if 1 <= m <= 12 and 1 <= d <= 31:
                return self._format_valid_date(y, m, d, "compact_ddmmyy")

        return None, "unrecognized_format"

    def _format_valid_date(self, year: int, month: int, day: int, detected_format: str) -> Tuple[Optional[str], str]:
        """验证日期合法性并格式化为 YYYY-MM-DD。"""
        try:
            dt = datetime(year, month, day)
            return dt.strftime("%Y-%m-%d"), detected_format
        except (ValueError, OverflowError):
            return None, f"invalid_calendar_date_{year}_{month}_{day}"

    def execute(self, raw_date_str: str) -> str:
        """主执行函数，解析失败时返回原串。"""
        norm_date, _ = self.normalize_date(raw_date_str)
        return norm_date if norm_date else (raw_date_str or "")


Tool = DateNormalizerTool
