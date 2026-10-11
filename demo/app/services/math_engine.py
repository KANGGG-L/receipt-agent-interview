# -*- coding: utf-8 -*-
from __future__ import annotations
"""T12 SSOT Facade：算术门禁唯一实现已上收 ai_registry/tools/math_engine/v2_1_0.py。

本文件仅为 re-export facade（保持 app.services.math_engine 导入路径不变）；
validate_and_report / validate_and_issues / audit_trail 与 registry v2_1_0 为同一函数对象。
规则要点（以 v2_1_0 为准）：Decimal 四舍五入、容差 0.01、取整印刷只提示、is_void 划线作废行、
actual_qty 实收另算、$0 赠品容错、贷项单按负值校验、整单折让/押金/运费/服务费/税额/抹零字段守恒。
"""

from ai_registry.tools.math_engine.v2_1_0 import (
    validate_and_report, validate_and_issues, evaluate, audit_trail,
)

__all__ = ["validate_and_report", "validate_and_issues", "evaluate", "audit_trail"]
