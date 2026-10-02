# -*- coding: utf-8 -*-
"""名称后缀剥离 —— 匹配（``receipt_utils._normalize_sku_name``）与候选生成
（``gen_sku_alias_candidates`` 归一分组）共用的**唯一**实现（阶段 2A 工作项③）。

规则表 ``name_suffix_rules.yaml`` 与本模块同目录（版本化资产；不放 demo/evalsets/，
该目录 gitignore，与 SPEC_PHASE0 §4.1 unit_equivalence 同款理由）。

维护纪律（宁窄勿宽）：只剥【显式后缀】—— 模式锚定结尾（``$``）且必须含显式
单位/容器词；斤/司馬斤 重量族、裸数字、无容器词的 NxM（TS-4X8 型货号）一律禁入
（阴性对照 24 条钉在 demo/tests/test_name_suffix_rules.py）。

fail-fast（SPEC_PHASE0 §5.3 同款纪律）：缺文件 / 坏 YAML / 非法正则一律 SystemExit
并指明路径 —— **不得静默按"无后缀剥离"降级**（规则表没生效但匹配照跑的假绿）。

纯函数、零 DB 依赖。
"""

import os
import re

DEFAULT_YAML = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "name_suffix_rules.yaml")


def load_rules(path=None):
    """加载后缀剥离规则表；缺文件/坏 YAML/非法正则 → SystemExit（指明路径）。"""
    p = os.path.abspath(path or DEFAULT_YAML)
    if not os.path.exists(p):
        raise SystemExit("名称后缀规则表不存在：%s（匹配必需资产，禁止静默降级）" % p)
    try:
        import yaml
    except ImportError:
        raise SystemExit("名称后缀规则表需要 PyYAML（demo/requirements.txt 已声明），"
                         "当前解释器不可用：%s" % p)
    try:
        with open(p, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
    except Exception as e:
        raise SystemExit("名称后缀规则表 YAML 解析失败：%s（%s）" % (p, e))
    if not isinstance(doc, dict) or not isinstance(doc.get("suffix_patterns"), list) \
            or not doc["suffix_patterns"]:
        raise SystemExit("名称后缀规则表结构非法（缺非空 suffix_patterns 列表）：%s" % p)
    try:
        max_repeat = int(doc.get("max_repeat", 1))
    except (TypeError, ValueError):
        raise SystemExit("名称后缀规则表 max_repeat 必须是整数：%s" % p)
    if max_repeat < 1:
        raise SystemExit("名称后缀规则表 max_repeat 必须 ≥1：%s" % p)
    patterns = []
    for raw in doc["suffix_patterns"]:
        if not isinstance(raw, str) or not raw.strip():
            raise SystemExit("名称后缀规则表存在非字符串/空模式：%s" % p)
        try:
            patterns.append(re.compile(raw))
        except re.error as e:
            raise SystemExit("名称后缀规则表存在非法正则 %r（%s）：%s" % (raw, e, p))
    return {
        "version": str(doc.get("version", "")),
        "max_repeat": max_repeat,
        "patterns": patterns,
    }


def strip_suffix(text, rules):
    """按序应用全部模式；重复至稳定（上限 ``max_repeat`` 次，防病态回溯）。

    每次替换后 ``rstrip()``：剥离留下的尾随空白不进下一轮比对，也不进返回值。
    复合后缀（如「…500毫升裝 6包裝」）靠逐轮迭代剥净；一轮内没有任何模式命中
    即视为已稳定、提前返回（终局不动 —— 宁窄勿宽，不做猜测性改写）。
    """
    out = str(text or "")
    for _ in range(int(rules["max_repeat"])):
        before = out
        for pat in rules["patterns"]:
            new = pat.sub("", out).rstrip()
            if new != out:
                out = new
        if out == before:
            break
    return out
