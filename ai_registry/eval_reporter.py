"""
Evaluation & Benchmark Reporter (ai_registry/eval_reporter.py)
用于多维度展示 Prompt、Tool、Skill 的评测指标演进与 A/B 灰测实验对比。
"""

import sys
import json
import argparse
from pathlib import Path
from ai_registry.registry import ai_registry
from ai_registry.canary.experiment_manager import ExperimentManager
from ai_registry.canary.ab_evaluator import ABEvaluator

def show_summary():
    matrix = ai_registry.get_benchmark_matrix()
    print("=" * 80)
    print("【AI Assets Benchmark Matrix 全量资产评测指标总览】")
    print("=" * 80)
    print(f"{'类型':<10} | {'资产名称':<20} | {'激活版本':<16} | {'核心指标 (Accuracy/CER/Latency)':<25}")
    print("-" * 80)

    for item in matrix.get("prompts", []):
        name = item["scene"]
        ver = item["active_version"]
        metric_str = f"Acc: {item['accuracy']}% | CER: {item.get('cer', 'N/A')}%"
        print(f"{'Prompt':<10} | {name:<20} | {ver:<16} | {metric_str:<25}")

    for item in matrix.get("tools", []):
        name = item["tool_name"]
        ver = item["active_version"]
        metric_str = f"Pass: {item['interception_rate']}% | Latency: {item['latency_ms']}ms"
        print(f"{'Tool':<10} | {name:<20} | {ver:<16} | {metric_str:<25}")

    for item in matrix.get("skills", []):
        name = item["skill_name"]
        ver = item["version"]
        metric_str = f"Score: {item['benchmark_score']}/100 | Quality: {item['pass_rate']}%"
        print(f"{'Skill':<10} | {name:<20} | {ver:<16} | {metric_str:<25}")

    print("=" * 80)

def show_ab_status():
    mgr = ExperimentManager()
    exps = mgr.list_experiments()
    print("=" * 85)
    print("【A/B 测试与灰测实验大盘 (Active & Historical Experiments)】")
    print("=" * 85)
    if not exps:
        print("当前暂无运行中的 A/B 实验。")
        return

    for exp in exps:
        print(f"[INFO]  实验 ID: {exp['id']} | 名称: {exp['name']} | 状态: 【{exp['status'].upper()}】")
        print(f"   假设: {exp['hypothesis']}")
        print(f"   流量分流: {exp['traffic_percent']}% | 分配模式: {exp['assign_mode']}")
        print("-" * 85)
        stats = ABEvaluator.calculate_stats(exp["group_a"], exp["group_b"])
        print(f"   样本量: 对照组 A = {stats['sample_size']['group_a']} 单 | 实验组 B = {stats['sample_size']['group_b']} 单")
        print(f"   准确率: 对照组 {stats['accuracy']['group_a']}% -> 实验组 {stats['accuracy']['group_b']}% (相对提升: {stats['accuracy']['relative_lift']})")
        print(f"   人工修改率: 对照组 {stats['edit_rate']['group_a']}% -> 实验组 {stats['edit_rate']['group_b']}% (净降低: {stats['edit_rate']['delta']}pp)")
        print(f"   算术重试率: 对照组 {stats['math_retry_rate']['group_a']}% -> 实验组 {stats['math_retry_rate']['group_b']}% (净降低: {stats['math_retry_rate']['delta']}pp)")
        print(f"   统计显著性: p-value = {stats['statistical_test']['p_value']} (Z={stats['statistical_test']['z_score']})")
        print(f"    决策判定: {stats['statistical_test']['conclusion']}")
        print("=" * 85)

def show_diff(asset_type: str, name: str, v1: str, v2: str):
    print(f"\n【A/B 版本指标对比】类型: {asset_type} | 资产: {name} | 对比: {v1} vs {v2}")
    print("-" * 70)
    
    if asset_type == "prompt":
        meta = ai_registry.get_prompt_metadata(name)
        versions = meta.get("versions", {})
        meta_v1 = versions.get(v1, {})
        meta_v2 = versions.get(v2, {})
        
        if not meta_v1 or not meta_v2:
            print(f"[FAIL]  错误: 版本 {v1} 或 {v2} 不存在于元数据中！")
            return
            
        print(f"{'指标项':<20} | {v1:<20} | {v2:<20} | {'变动 (Delta)':<15}")
        print("-" * 70)
        
        m1 = meta_v1.get("metrics", {})
        m2 = meta_v2.get("metrics", {})
        
        all_keys = set(m1.keys()).union(set(m2.keys()))
        for k in sorted(all_keys):
            val1 = m1.get(k, 0)
            val2 = m2.get(k, 0)
            delta = val2 - val1 if isinstance(val1, (int, float)) and isinstance(val2, (int, float)) else "N/A"
            delta_str = f"+{delta:.2f}" if isinstance(delta, (int, float)) and delta > 0 else f"{delta}"
            print(f"{k:<20} | {str(val1):<20} | {str(val2):<20} | {delta_str:<15}")

def main():
    parser = argparse.ArgumentParser(description="AI 资产评测与 A/B 灰测指标追踪工具")
    parser.add_argument("--summary", action="store_true", help="打印全量资产评测指标总览")
    parser.add_argument("--ab-status", action="store_true", help="查看当前 A/B 灰测实验大盘与显著性分析")
    parser.add_argument("--diff", action="store_true", help="执行 A/B 版本效果对比")
    parser.add_argument("--type", type=str, choices=["prompt", "tool", "skill"], default="prompt")
    parser.add_argument("--name", type=str, help="资产名称")
    parser.add_argument("--v1", type=str, help="基准版本 V1")
    parser.add_argument("--v2", type=str, help="对比版本 V2")

    args = parser.parse_args()

    if args.ab_status:
        show_ab_status()
    elif args.summary:
        show_summary()
    elif args.diff:
        if not (args.name and args.v1 and args.v2):
            print("[FAIL]  执行 --diff 必须指定 --name, --v1 和 --v2")
            sys.exit(1)
        show_diff(args.type, args.name, args.v1, args.v2)
    else:
        show_summary()

if __name__ == "__main__":
    main()
