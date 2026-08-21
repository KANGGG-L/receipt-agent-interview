"""
A/B Test Statistical Evaluator (ai_registry/canary/ab_evaluator.py)
用于计算 A/B 实验组与对照组指标 Delta、置信区间及统计学显著性 (p-value)。
"""

import math
from typing import Dict, Any

class ABEvaluator:
    @staticmethod
    def calculate_stats(group_a: Dict[str, Any], group_b: Dict[str, Any]) -> Dict[str, Any]:
        """
        输入格式:
        group_a: {"samples": 120, "accuracy": 88.0, "edit_rate": 28.5, "avg_latency": 35.2, "math_retry_rate": 18.0}
        group_b: {"samples": 120, "accuracy": 95.5, "edit_rate": 6.2, "avg_latency": 31.0, "math_retry_rate": 2.5}
        """
        n_a = group_a.get("samples", 1)
        n_b = group_b.get("samples", 1)

        acc_a = group_a.get("accuracy", 0.0)
        acc_b = group_b.get("accuracy", 0.0)
        acc_delta = acc_b - acc_a

        edit_a = group_a.get("edit_rate", 0.0)
        edit_b = group_b.get("edit_rate", 0.0)
        edit_delta = edit_b - edit_a

        # 简化双比例 Z 检验模拟 p-value 计算
        p1 = acc_a / 100.0
        p2 = acc_b / 100.0
        p_pool = (p1 * n_a + p2 * n_b) / (n_a + n_b)
        se = math.sqrt(p_pool * (1 - p_pool) * (1/n_a + 1/n_b)) if p_pool * (1 - p_pool) > 0 else 0.0001
        z_score = abs(p2 - p1) / se if se > 0 else 0
        
        # 经验近似 p-value
        if z_score > 3.29:
            p_value = 0.001
        elif z_score > 2.58:
            p_value = 0.01
        elif z_score > 1.96:
            p_value = 0.05
        else:
            p_value = 0.20

        is_significant = (p_value <= 0.05) and (n_a >= 50 and n_b >= 50)

        return {
            "sample_size": {"group_a": n_a, "group_b": n_b},
            "accuracy": {
                "group_a": acc_a,
                "group_b": acc_b,
                "delta": round(acc_delta, 2),
                "relative_lift": f"{'+' if acc_delta > 0 else ''}{round((acc_delta/acc_a)*100, 2)}%"
            },
            "edit_rate": {
                "group_a": edit_a,
                "group_b": edit_b,
                "delta": round(edit_delta, 2)
            },
            "math_retry_rate": {
                "group_a": group_a.get("math_retry_rate", 0.0),
                "group_b": group_b.get("math_retry_rate", 0.0),
                "delta": round(group_b.get("math_retry_rate", 0.0) - group_a.get("math_retry_rate", 0.0), 2)
            },
            "statistical_test": {
                "z_score": round(z_score, 2),
                "p_value": p_value,
                "is_significant_95": is_significant,
                "conclusion": " 实验组 B 指标显著优于对照组 A (p < 0.05)，建议全量推全！" if (is_significant and acc_delta > 0) else "样本量不足或差异未达到显著性水平，建议继续灰测观察。"
            }
        }
