# -*- coding: utf-8 -*-
"""P2-T15 (G7): 前端治理面板自动化回归套件 (demo/tests 镜像)。

直接委托执行 tests/test_frontend_governance_harnesses.py 中的用例。
"""

from tests.test_frontend_governance_harnesses import (
    test_memory_metrics_panel_harness_passes,
    test_vendor_memory_governance_panel_harness_passes,
    test_frontend_pending_memory_badge_harness_passes,
    test_governance_panel_mutation_counterproof,
    test_metrics_panel_mutation_counterproof,
)

__all__ = [
    "test_memory_metrics_panel_harness_passes",
    "test_vendor_memory_governance_panel_harness_passes",
    "test_frontend_pending_memory_badge_harness_passes",
    "test_governance_panel_mutation_counterproof",
    "test_metrics_panel_mutation_counterproof",
]
