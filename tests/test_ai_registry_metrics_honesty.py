# -*- coding: utf-8 -*-
"""D13 指标诚实性回归守卫：禁止无支撑的 100% 指标与陈旧 active_version。

把「纠偏后的口径」固化为可回归的测试事实：
1. ai_registry 的 prompts/*/metadata.json、tools/*/metadata.json、skills/*/eval.json 中，
   任何 100.0 的「能力类」指标（注入防御率 / SQL 安全率 / 拦截率 / SQL 语法有效率 /
   缺失单据召回率 / 沙箱隔离率等）都必须带 *_scope 说明或 claim_unsupported 标记，
   否则视为无支撑的虚假指标。
2. 原三处无支撑 100.0（extract/audit 的 injection_defense_rate、query 的
   sql_safety_rate）必须已改为 null + claim_unsupported + 说明 + 复现命令。
3. benchmark_matrix.json 的 active_version 必须与各资产实际 active 对齐，且不再残留
   无 scope 的 100.0。
"""
import glob
import json
import os

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
AI_REGISTRY = os.path.join(REPO_ROOT, "ai_registry")

# 「能力类」指标键：这些键出现 100.0 时若无 scope/claim 说明即为无支撑虚假指标。
_CAPABILITY_METRIC_KEYS = {
    "injection_defense_rate",
    "sql_safety_rate",
    "sql_syntax_validity",
    "missing_receipt_recall",
    "interception_rate",
    "bypass_detection_rate",
    "sandbox_isolation_rate",
    "legacy_baseline_detection_rate",
}


def _iter_dicts(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _iter_dicts(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_dicts(v)


def _scanned_metric_files():
    pats = [
        "prompts/*/metadata.json",
        "tools/*/metadata.json",
        "skills/*/eval.json",
    ]
    out = []
    for pat in pats:
        out.extend(sorted(glob.glob(os.path.join(AI_REGISTRY, pat))))
    assert out, "未找到任何待扫描的指标文件（路径假设失效）"
    return out


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_no_unscoped_capability_metric_at_100pct():
    """能力类指标一旦取 100.0，必须带 *_scope 说明或 claim_unsupported 标记。"""
    offenders = []
    for path in _scanned_metric_files():
        for d in _iter_dicts(_load(path)):
            for key, val in d.items():
                if key not in _CAPABILITY_METRIC_KEYS:
                    continue
                if not isinstance(val, (int, float)) or float(val) != 100.0:
                    continue
                has_scope = any("scope" in str(k).lower() for k in d)
                has_claim = d.get("claim_unsupported") is True
                if not (has_scope or has_claim):
                    offenders.append((os.path.relpath(path, REPO_ROOT), key))
    assert not offenders, (
        "存在无支撑的 100.0 能力指标（须补 *_scope 或 claim_unsupported）: %s" % offenders)


def test_original_unsupported_prompt_metrics_corrected():
    """extract/audit 的 injection_defense_rate、query 的 sql_safety_rate 必须已纠偏。"""
    cases = [
        ("extract", "v1_2_8_anti_injection", "injection_defense_rate"),
        ("audit", "v2_0_0_reason", "injection_defense_rate"),
        ("query", "v1_0_0", "sql_safety_rate"),
    ]
    for scene, version, key in cases:
        meta = _load(os.path.join(AI_REGISTRY, "prompts", scene, "metadata.json"))
        metrics = meta["versions"][version]["metrics"]
        assert metrics.get(key) is None, (
            f"{scene}/{version}.{key} 应纠偏为 null，实际 {metrics.get(key)!r}")
        assert metrics.get("claim_unsupported") is True, f"{scene}/{version} 缺 claim_unsupported 标记"
        assert metrics.get("claim_unsupported_note"), f"{scene}/{version} 缺 claim_unsupported_note"
        assert metrics.get("reproduce_command"), f"{scene}/{version} 缺 reproduce_command"


def test_untested_tools_metrics_marked_unsupported():
    """无测试与生产消费者的工具，其拦截率必须为 null + claim_unsupported。"""
    for tool in ("statement_reconciler", "sku_matcher", "currency_unit_converter"):
        meta = _load(os.path.join(AI_REGISTRY, "tools", tool, "metadata.json"))
        ver = meta["active_version"]
        metrics = meta["versions"][ver]
        assert metrics.get("interception_rate") is None, (
            f"{tool} 无支撑的 interception_rate 应纠偏为 null")
        assert metrics.get("claim_unsupported") is True, f"{tool} 缺 claim_unsupported 标记"


def test_benchmark_matrix_active_versions_are_current():
    """benchmark_matrix.json 的 active_version 必须与各资产实际 active 对齐。"""
    matrix = _load(os.path.join(AI_REGISTRY, "benchmarks", "benchmark_matrix.json"))
    prompts = {p["scene"]: p["active_version"] for p in matrix["prompts"]}
    assert prompts["extract"] == "v1_2_8_anti_injection", (
        "extract 的 active_version 仍陈旧（应指向 v1_2_8_anti_injection）")
    assert prompts["audit"] == "v2_1_0_overall_schema"
    # 与 prompts metadata 实际 active 对齐
    for scene, ver in prompts.items():
        meta = _load(os.path.join(AI_REGISTRY, "prompts", scene, "metadata.json"))
        assert ver == meta["active_version"], f"{scene}: matrix={ver} metadata={meta['active_version']}"

    tools = {t["tool_name"]: t for t in matrix["tools"]}
    assert tools["math_engine"]["active_version"] == "v2_1_0"
    assert tools["query_sql_gen"]["active_version"] == "v1_1_0"
    for name, row in tools.items():
        meta = _load(os.path.join(AI_REGISTRY, "tools", name, "metadata.json"))
        assert row["active_version"] == meta["active_version"], (
            f"{name}: matrix={row['active_version']} metadata={meta['active_version']}")


def test_benchmark_matrix_tools_have_scope_or_claim():
    """matrix 的 tools 段不得残留无 scope/claim 的 100.0。"""
    matrix = _load(os.path.join(AI_REGISTRY, "benchmarks", "benchmark_matrix.json"))
    offenders = []
    for row in matrix["tools"]:
        val = row.get("interception_rate")
        if isinstance(val, (int, float)) and float(val) == 100.0:
            has_scope = any("scope" in str(k).lower() for k in row)
            has_claim = row.get("claim_unsupported") is True
            if not (has_scope or has_claim):
                offenders.append(row["tool_name"])
    assert not offenders, f"matrix tools 残留无支撑 100.0: {offenders}"
