# -*- coding: utf-8 -*-
"""
Gap 5 专项测试：香港本地化日期格式 (DD/MM/YYYY, DD-MM-YY, 英文月份, 中文年月日) 确定性归一化回归测试集。
"""

import pytest
from ai_registry.tools.date_normalizer.v1_0_0 import DateNormalizerTool


@pytest.fixture
def normalizer():
    return DateNormalizerTool()


def test_dmy_ambiguous_resolved_to_hk_convention(normalizer):
    """测试歧义数字日期（日月均 <= 12）按香港惯例优先推断为 DD/MM/YYYY。"""
    cases = [
        ("06/08/2026", "2026-08-06"),  # 8月6日 (杜绝美式 6月8日)
        ("01/05/2026", "2026-05-01"),  # 5月1日
        ("02/09/2026", "2026-09-02"),  # 9月2日
        ("11/12/2026", "2026-12-11"),  # 12月11日
        ("07/04/2026", "2026-04-07"),  # 4月7日
        ("06-08-2026", "2026-08-06"),  # 减号分隔
        ("06.08.2026", "2026-08-06"),  # 点号分隔
    ]
    for raw, expected in cases:
        norm_d, fmt = normalizer.normalize_date(raw)
        assert norm_d == expected, f"港式歧义日期解析失败: {norm_d} != {expected} (原值: {raw}, 模式: {fmt})"


def test_dmy_unambiguous_day_gt_12(normalizer):
    """测试确定性无歧义日期（日 > 12）。"""
    cases = [
        ("21/08/2026", "2026-08-21"),
        ("31/12/2025", "2025-12-31"),
        ("15-03-2026", "2026-03-15"),
        ("28/02/2026", "2026-02-28"),
    ]
    for raw, expected in cases:
        norm_d, fmt = normalizer.normalize_date(raw)
        assert norm_d == expected, f"无歧义日期解析失败: {norm_d} != {expected} (原值: {raw})"


def test_two_digit_year_expansion(normalizer):
    """测试两位年份补齐 (<=30 -> 2000+, >30 -> 1900+)。"""
    cases = [
        ("06/08/26", "2026-08-06"),
        ("21-08-26", "2026-08-21"),
        ("01/01/25", "2025-01-01"),
        ("15/09/24", "2024-09-15"),
        ("15/06/99", "1999-06-15"),
        ("01/10/95", "1995-10-01"),
        ("99年8月6日", "1999-08-06"),
        ("15-Jun-99", "1999-06-15"),
    ]
    for raw, expected in cases:
        norm_d, fmt = normalizer.normalize_date(raw)
        assert norm_d == expected, f"两位年份补齐失败: {norm_d} != {expected} (原值: {raw})"


def test_english_month_and_chinese_formats(normalizer):
    """测试英文月份与中文年月日。"""
    cases = [
        ("06-AUG-2026", "2026-08-06"),
        ("21 Aug 2026", "2026-08-21"),
        ("1-May-2026", "2026-05-01"),
        ("15-Dec-26", "2026-12-15"),
        ("2026年8月6日", "2026-08-06"),
        ("26年08月21號", "2026-08-21"),
        ("2026-08-21", "2026-08-21"),  # 标准格式直接通过
    ]
    for raw, expected in cases:
        norm_d, fmt = normalizer.normalize_date(raw)
        assert norm_d == expected, f"中英文格式解析失败: {norm_d} != {expected} (原值: {raw})"
