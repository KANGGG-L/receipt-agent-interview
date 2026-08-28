# -*- coding: utf-8 -*-
from __future__ import annotations
"""T12 SSOT Facade：算术门禁唯一实现已上收 ai_registry/tools/math_engine/v2_1_0.py。

本文件仅为 re-export facade（保持 app.services.math_engine 导入路径与 9 个消费测试
文件的 import 零改动）；validate_and_report / audit_trail 与 registry v2_1_0 为同一函数对象。
规则要点（以 v2_1_0 为准）：容差 0.01、is_void 划线作废行、actual_qty 生效数量、
$0 赠品容错、整单折让/押金/运费/服务费/税额/抹零字段守恒。
"""

from ai_registry.tools.math_engine.v2_1_0 import validate_and_report, audit_trail

__all__ = ["validate_and_report", "audit_trail"]
