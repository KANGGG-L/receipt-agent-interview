# -*- coding: utf-8 -*-
"""单位语义归一 —— 评分器（run_eval）与 unit 类记忆事实（memory_consolidate）的**唯一**实现。

阶段 2A（工作项②）：从 ``demo/scripts/run_eval.py`` 整体搬迁（不是复制）而来，
行为逐点一致（U-1..U-8 为零漂移判据，见 demo/tests/test_eval_unit_equivalence.py）：

- ``load_unit_equivalence``：加载单位等价表并建索引；文件缺失/坏 YAML 时 fail-fast
  SystemExit（指明路径）。why fail-fast：静默降级回严格相等会造成「等价表没生效但
  分数照出」的假绿。
- ``norm_unit(raw, equiv)``：单位归一（枚举转型 → 规格污染括号剥离 → 文本归一）。
- ``units_equal(a_raw, b_raw, equiv)``：单位等价判定，短路顺序 1/2/3 固定（见下）。
- ``unit_value_key(value, equiv)``：unit 类记忆事实的可比较键（阶段 2A 新增），
  与 ``units_equal`` 完全同构的三层短路 —— 保证「评分判等」与「事实去重」两个判定
  永不分叉（②的设计目标：消灭两把尺子）。
- ``canonical_unit(value, equiv=None)``：unit 值 → 规范写法（收尾波次工作项①新增），
  组内成员映射到该等价组 YAML 列表首位的原文写法；不在组则回落 ``norm_unit`` 文本
  （不编造规范写法）。供写侧（memory_extract / rag._pending_memory_fact）落库前
  归一与读侧（rag._render_fact_row）渲染存量行复用。

纯函数、零 DB 依赖；等价表资产与本模块同目录（unit_equivalence.yaml）。
只读 import ``ai_registry/tools/currency_unit_converter``（P6：不得修改该目录任何文件）。
"""

import enum
import os
import re

DEFAULT_YAML = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "unit_equivalence.yaml")
_PAREN_SPEC_RE = re.compile(r"\([^)]*\)|（[^）]*）")

# 只读 import（P6：不得修改 ai_registry/tools/currency_unit_converter/ 任何文件）。
# demo/app/main.py 已把 REPO_ROOT 插入 sys.path；pytest 经根 conftest 亦可达。
from ai_registry.tools.currency_unit_converter.v1_0_0 import CurrencyUnitConverterTool  # noqa: E402

# 换算系数索引：键经 strip().lower()，与该工具 convert_weight_to_standard_kg 的取键
# 方式一致（v1_0_0.py:52 一带）
_UNIT_TO_KG_LOWER = {str(k).strip().lower(): v
                     for k, v in CurrencyUnitConverterTool.UNIT_TO_KG.items()}


def _norm_text(s):
    """文本归一：去空白/常见标点 + 全角半角 + 大小写。

    与 run_eval._norm_text 同口径（枚举成员转型 + strip/lower + 去全部空白 +
    去常见中英标点）。本模块自带一份而不是 import run_eval：消费方在 demo/app 侧
    （memory_consolidate），不能反向依赖 scripts 目录。
    """
    if isinstance(s, enum.Enum):
        s = s.value          # pydantic v2 model_dump() 对 str-Enum 返回成员而非字符串
    s = str(s if s is not None else "").strip().lower()
    s = re.sub(r"[\s　]+", "", s)
    s = re.sub(r"[()（）\[\]【】,，.。:：\-_/\\|]", "", s)
    return s


def load_unit_equivalence(path=None):
    """加载单位等价表并建索引；文件缺失/坏 YAML 时 fail-fast SystemExit（指明路径）。"""
    p = os.path.abspath(path or DEFAULT_YAML)
    if not os.path.exists(p):
        raise SystemExit("单位等价表不存在：%s（评测评分必需资产，禁止静默降级）" % p)
    try:
        import yaml
    except ImportError:
        raise SystemExit("单位等价表需要 PyYAML（demo/requirements.txt 已声明），"
                         "当前解释器不可用：%s" % p)
    try:
        with open(p, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
    except Exception as e:
        raise SystemExit("单位等价表 YAML 解析失败：%s（%s）" % (p, e))
    if not isinstance(doc, dict) or not isinstance(doc.get("groups"), list):
        raise SystemExit("单位等价表结构非法（缺 groups 列表）：%s" % p)
    strip_paren = bool(doc.get("strip_paren_spec"))
    groups = []
    member_index = {}
    canonical = {}
    for gi, group in enumerate(doc["groups"]):
        if not isinstance(group, list):
            raise SystemExit("单位等价表 groups 成员须为列表：%s" % p)
        # 规范写法 = YAML 列表首位的原文成员（维护纪律：新增等价组须把最通用写法列
        # 首位 —— 表是人工维护资产，首位即维护者认定的通用写法；用 sorted(members)[0]
        # 会选出 'btl' 这类 ASCII 序产物，不可作显示值）。与 groups/member_index 同
        # 循环构建，避免第二遍遍历两套口径。
        canonical_name = str(group[0]) if group else ""
        members = set()
        for raw in group:
            key = _norm_unit_with(strip_paren, raw)
            if key:
                members.add(key)
                canonical[key] = canonical_name
        for key in members:
            if key in member_index and member_index[key] != gi:
                raise SystemExit("单位等价表成员跨组重复：%r（%s）" % (key, p))
            member_index[key] = gi
        groups.append(members)
    return {
        "version": str(doc.get("version", "")),
        "strip_paren_spec": strip_paren,
        "convert_via_converter": bool(doc.get("convert_via_converter")),
        "groups": groups,
        "member_index": member_index,
        "canonical": canonical,
    }


def _norm_unit_with(strip_paren, raw):
    """norm_unit 的可注入版（供等价表加载期建索引自举，避免读未初始化的 equiv）。"""
    if isinstance(raw, enum.Enum):
        raw = raw.value
    s = str(raw if raw is not None else "")
    if strip_paren:
        # 规格污染剥离：箱（48罐）→ 箱（括号内容剥离后再比对）
        s = _PAREN_SPEC_RE.sub("", s)
    return _norm_text(s)


def norm_unit(raw, equiv):
    """单位归一：枚举转型 → 规格污染括号剥离 → 文本归一（= 旧 _norm_unit）。"""
    return _norm_unit_with(equiv["strip_paren_spec"], raw)


def units_equal(a_raw, b_raw, equiv):
    """单位等价判定，短路顺序（固定，不得重排）：
    1. norm_unit 后相等；
    2. 两侧落入同一等价组（组内元素经 norm_unit 建索引）；
    3. convert_via_converter：两侧在 CurrencyUnitConverterTool.UNIT_TO_KG 均命中
       且系数精确相等（同 dict 来源，不做容差）——斤(0.6048) vs 磅(0.4536) 必须
       判错，那是语义差异不是书写噪声。
    """
    a, b = norm_unit(a_raw, equiv), norm_unit(b_raw, equiv)
    if a == b:
        return True
    if not a or not b:
        return False
    idx = equiv["member_index"]
    ga, gb = idx.get(a), idx.get(b)
    if ga is not None and ga == gb:
        return True
    if equiv["convert_via_converter"]:
        fa = _UNIT_TO_KG_LOWER.get(a)
        fb = _UNIT_TO_KG_LOWER.get(b)
        if fa is not None and fb is not None and fa == fb:
            return True
    return False


def unit_value_key(value, equiv):
    """unit 类记忆事实的可比较键（阶段 3 铺路：事实去重/NOOP 判定的单位语义）。

    与 ``units_equal`` 完全同构的三层短路，键空间按前缀分段、互不撞车：
      ① norm_unit 后相同 → 同键（"txt:" + 归一，或下面②③的前缀键）；
      ② 同等价组 → "grp:" + sorted(members)[0]（确定性代表元，组内任意写法同键）；
      ③ 同换算系数 → "kg:{:.6g}"（仅两侧都在换算表时 units_equal 才走此路，键一致）；
      ④ 否则 "txt:" + norm_unit。
    空值 → "txt:"（空 = 缺失，与评分口径「空不占分母」同语义）。
    """
    n = norm_unit(value, equiv)
    if not n:
        return "txt:"
    idx = equiv["member_index"]
    gi = idx.get(n)
    if gi is not None:
        return "grp:" + sorted(equiv["groups"][gi])[0]
    if equiv["convert_via_converter"]:
        f = _UNIT_TO_KG_LOWER.get(n)
        if f is not None:
            return "kg:{:.6g}".format(f)
    return "txt:" + n


# canonical_unit(equiv=None) 的缺省表缓存：按解析后的 YAML 路径分键 —— 换路径（含
# 测试 monkeypatch DEFAULT_YAML 指向不存在路径）必然是新键，必然重新走加载期
# fail-fast；同一进程内不会因缓存而吞掉 SystemExit。
_CANONICAL_EQUIV_CACHE = {}


def canonical_unit(value, equiv=None):
    """unit 值 → 规范写法（收尾波次工作项①；纯函数、幂等）。

    判定顺序（与 units_equal / unit_value_key 的短路序同源）：
    1. norm_unit 后为空 → 返回 ""（缺失不编造）；
    2. 归一键在等价组内 → 返回该组 YAML 列表首位的原文写法（人工维护的规范写法）；
    3. 不在组（含命中换算表的同系数写法，如「两」）→ 返回 norm_unit 文本 —— 表里没有
       人工维护的规范写法就不编造，归一（小写/去括号）后的文本即显示值；
    空值 → ""。

    幂等：canonical_unit(canonical_unit(x)) == canonical_unit(x)（组内成员映射到首位
    原文，首位原文自身归一后仍在组内 → 二次映射 NOOP；组外文本归一是幂等运算）。

    维护纪律（unit_equivalence.yaml 同步遵守）：新增等价组须把最通用写法列首位。
    equiv=None 时惰性加载缺省表（按路径缓存）；表缺失/坏 YAML 沿用
    load_unit_equivalence 的 SystemExit fail-fast（与 memory_consolidate 同纪律，
    不静默降级）。
    """
    if equiv is None:
        p = os.path.abspath(DEFAULT_YAML)
        eq = _CANONICAL_EQUIV_CACHE.get(p)
        if eq is None:
            eq = load_unit_equivalence(p)
            _CANONICAL_EQUIV_CACHE[p] = eq
        equiv = eq
    n = norm_unit(value, equiv)
    if not n:
        return ""
    canon = equiv["canonical"].get(n)
    if canon:
        return canon
    return n
