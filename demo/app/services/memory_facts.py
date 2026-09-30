# -*- coding: utf-8 -*-
from __future__ import annotations

"""记忆语义 schema（计划 P3-T1 / 行业最小集 A4）：结构化事实的唯一定义与契约校验。

为什么要有这一层：现有 `vendor_memory` 把一条记忆存成 `notes`/`sample` 两段自由文本
（gap 05 §H2/H4 实测：语义检索通道 live 产出为 0；自由文本无法回答"这次注入命中了哪条
事实"）。要让它变成可注入、可去重、可失效、可度量的先验，事实必须先是结构化的
(kind, key, value, scope, source, valid_from/to, trust) 七元组 —— 本模块是该结构的
唯一定义，也是写入侧唯一闸门。

scope = (tenant_id, supplier_id)：读侧必须同时按这两维收窄（P3-T6）。只按名字检索会
在别名/繁简/简称处分叉（gap 05 §⑦ 实测 SKU 匹配率 23.7%），只按 vendor 不看租户会重演
§H1 的跨租户注入。

为什么用 dataclass 而不是 Pydantic：调用方（草稿区 / 人审 / LLM 抽取）需要的是
`(ok, reason)` 这种**可落库、可展示、可计数**的判定结果，而不是构造期异常 —— P3-T2 的
抽取产物会先以"可能非法"的形态落草稿区，若构造期就抛异常，非法草稿既无法记录也无法
告警（正是本仓高发的"异常被静默吞掉导致功能静默失效"的入口）。故构造保持宽松、
校验全部收在 `validate_fact` 一处：只有它放行的事实才允许落库。

本模块不碰 DB、不碰向量库、不做 IO：纯逻辑，便于被 db / rag / api_memory / 抽取服务
复用，也便于反证（见 tests/test_memory_facts.py）。

消费方（当前为暂存式交付 —— 接线在其后，故本模块目前没有生产调用方）：
- P3-T2 `memory_extract.extract_candidate_facts()` 产出候选 → 用 `validate_fact` 过滤；
- P3-T3 整合服务用 `fact_key` 判同 key 冲突，并写 `superseded_by` / `status`；
- P1-T4 / P2-T1 / P3-T1 的 db.py 迁移按 `ROW_FIELDS` 补列（`fact_key` / `fact_value` /
  `superseded_by` / `valid_to` / `written_by` / `trust_level` / `supplier_id` / `fact_kind`），
  并由 `to_row` / `from_row` 作为 DB 行 ↔ 事实之间**唯一的映射实现**，避免本仓高发的
  "同一口径多份实现，只改了一份"。
"""

import re
from dataclasses import MISSING, dataclass, fields
from datetime import date, datetime
from typing import Any, Dict, Mapping, Optional, Tuple

# -------------------------------------------------------------
# 四类可判定的事实（计划 C6 / P2-T3 白名单）
# 只收"能被判对错、能带有效期、能按 key 去重"的规则型事实；已确认单据明细属
# 案例级语料，走另一条通道（P2-T3 双轨写入），不进这里。
# -------------------------------------------------------------
KIND_ALIAS = "alias"              # 别名映射：该供应商的手写缩写 → 标准品名
KIND_UNIT = "unit"                # 单位习惯：该供应商惯用的计价单位
KIND_LAYOUT = "layout"            # 版式先验：单据形态（印刷/手写/热敏…）
KIND_PRICE_RANGE = "price_range"  # 价格区间：历史协议价区间
ALLOWED_KINDS = frozenset({KIND_ALIAS, KIND_UNIT, KIND_LAYOUT, KIND_PRICE_RANGE})

# -------------------------------------------------------------
# 来源白名单：事实是怎么进来的。来源不可追溯 → 无法审计、无法追责、无法按来源降权
# （P1-T4 的信任元数据依赖这一维度）。
# -------------------------------------------------------------
SOURCE_APPROVE = "approve"
SOURCE_FEEDBACK_DISTILLED = "feedback_distilled"
SOURCE_EXTRACT = "extract"
SOURCE_MANUAL = "manual"
ALLOWED_SOURCE_KINDS = frozenset({
    SOURCE_APPROVE, SOURCE_FEEDBACK_DISTILLED, SOURCE_EXTRACT, SOURCE_MANUAL,
})

# fact_key 长度边界：过短（含空白）无法区分事实，过长说明把整段文本当成了 key。
FACT_KEY_MIN_LEN = 1
FACT_KEY_MAX_LEN = 64

# 日期口径：valid_* 允许 YYYY-MM-DD，也允许同族的 ISO 时间戳（DB 的 created_at/
# updated_at 就是 ISO），避免调用方混用两种写法时被无故拒绝；比较只取日期部分。
_YMD_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[ T].*)?$")

# -------------------------------------------------------------
# 行字段名（＝vendor_memory 的扩展列名）
# 键序固定，便于 db 侧迁移与测试逐列比对。fact_kind 是本模块定的列名（计划只点名了
# fact_key/fact_value/superseded_by/valid_to），与 fact_* 家族一致；P3-T3 落库时按
# ROW_FIELDS 逐列 ALTER，不要再另起一套命名。
# -------------------------------------------------------------
ROW_FIELDS = (
    "memory_id",
    "fact_kind",
    "fact_key",
    "fact_value",
    "supplier_id",
    "tenant_id",
    "source_kind",
    "source_ref",
    "trust_level",
    "valid_from",
    "valid_to",
    "written_by",
    "status",
    "superseded_by",
)

# 领域字段名 ↔ 行字段名（唯一映射，两个方向都由这里派生，不手写第二份）
_FACT_TO_ROW = {
    "kind": "fact_kind",
    "fact_key": "fact_key",
    "value": "fact_value",
    "supplier_id": "supplier_id",
    "tenant_id": "tenant_id",
    "source_kind": "source_kind",
    "source_ref": "source_ref",
    "trust_level": "trust_level",
    "valid_from": "valid_from",
    "valid_to": "valid_to",
    "written_by": "written_by",
    "memory_id": "memory_id",
    "status": "status",
    "superseded_by": "superseded_by",
}
_ROW_TO_FACT = {v: k for k, v in _FACT_TO_ROW.items()}


@dataclass
class MemoryFact:
    """一条结构化的供应商事实。

    字段名是领域名；落库/读库的行名见 `to_row()` / `from_row()`（`ROW_FIELDS`）。
    无默认值的五个字段（kind/fact_key/value/supplier_id/tenant_id）是事实的身份，
    其余是治理元数据；缺省值即 DB 列缺省值，两边必须保持一致（否则
    `from_row(to_row(f)) == f` 不成立）。
    """

    kind: str
    fact_key: str
    value: str
    supplier_id: str
    tenant_id: str
    source_kind: str = SOURCE_MANUAL
    source_ref: str = "[]"          # 触发来源 receipt_id 列表 JSON，与 DB 缺省一致
    trust_level: float = 1.0        # 0.0-1.0；人工/用户来源缺省 1.0，模型抽取由调用方下调
    valid_from: str = ""
    valid_to: str = ""              # 空＝无失效期；TTL 到期由读侧过滤
    written_by: str = ""
    memory_id: str = ""
    status: str = "active"          # active|superseded|archived
    superseded_by: str = ""         # 被哪条新事实取代（P3-T3 写；旧事实不删除）

    def to_row(self) -> Dict[str, Any]:
        """事实 → DB 行字典（纯函数，不碰 DB）。

        trust_level 强制转 float：列是 REAL，若写进字符串，SQLite 排序会按字典序
        比大小（'9' > '10'），"按信任排序"就静默失真。转换失败显式抛错而不是把
        脏值写进库 —— 调用方须先过 `validate_fact`。
        """
        row: Dict[str, Any] = {}
        for f in fields(self):
            row[_FACT_TO_ROW[f.name]] = getattr(self, f.name)
        trust = _coerce_trust_level(row["trust_level"])
        if trust is None:
            raise ValueError(
                f"trust_level 非法，拒绝写出：{row['trust_level']!r}（先过 validate_fact）")
        row["trust_level"] = trust
        return row

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "MemoryFact":
        """DB 行字典 → 事实（纯函数，不碰 DB）。

        只读 `ROW_FIELDS` 里的列，其余列（vendor / notes / sample / decay_score /
        hit_count / created_at…）一概忽略：vendor_memory 一行同时承载旧的自由文本
        字段与新的结构化字段，映射只认扩展列才能保证 `from_row(to_row(f)) == f`。

        缺列与 NULL 一律回落该列缺省值（缺列＝迁移尚未补齐，不等价于"值为空"），
        但 trust_level 无法归一成数值时保留 None 交给 `validate_fact` 拒绝 —— 不静默
        改成默认值。
        """
        defaults = _field_defaults()
        kwargs: Dict[str, Any] = {}
        for row_key in ROW_FIELDS:
            name = _ROW_TO_FACT[row_key]
            value = row.get(row_key) if hasattr(row, "get") else None
            kwargs[name] = defaults[name] if value is None else value
        kwargs["trust_level"] = _coerce_trust_level(kwargs["trust_level"])
        return cls(**kwargs)


def validate_fact(fact: Any) -> Tuple[bool, str]:
    """写入侧唯一闸门：合法 → (True, "")；非法 → (False, 可读理由)。

    接受 `MemoryFact` 或行字典（行字典按 `from_row` 解析）。只判"结构是否可信"，
    不判内容真伪（同 key 冲突属 P3-T3 整合）。

    逐条检查、返回**第一条命中的理由**：理由会落 pending 的 reject_reason 与告警文本，
    所以每条都写清「哪里错 + 为什么这条必须存在」，而不是只给一个布尔。
    两处调用点都会把它落到 `pending_memory.reject_reason`（Q2）：
    - 抽取侧 `memory_extract._to_facts`（理由前缀 `候选[idx] `）→ 由
      `api_receipts._record_refused_candidate` 记为 status='rejected' 的审计行；
    - 写入侧闸门 `api_receipts._extract_and_enqueue_memory_facts`（这里才是真正的写方）。
    因此**不要**把理由写成只对调试有意义的短句。
    """
    if isinstance(fact, Mapping):
        fact = MemoryFact.from_row(fact)
    if not isinstance(fact, MemoryFact):
        return False, f"fact 类型非法：需要 MemoryFact 或 dict，实得 {type(fact).__name__}"

    # isinstance 前置：kind/source_kind 若是 dict/list 等不可哈希类型，直接做集合成员测试会抛
    # TypeError（E0 审核 mustFix M1），而本函数的契约是**只返回 (ok, reason)、绝不抛**——
    # 上层要靠 reason 落 pending 的 reject_reason，抛异常会让整条抽取链断掉。
    # `or` 短路保证 isinstance 为 False 时右侧不再求值。
    if not isinstance(fact.kind, str) or fact.kind not in ALLOWED_KINDS:
        return False, (
            f"kind 非法：{fact.kind!r}，只允许 {sorted(ALLOWED_KINDS)}"
            "（白名单外的事实无法确定注入位置与去重口径）")

    if not isinstance(fact.source_kind, str) or fact.source_kind not in ALLOWED_SOURCE_KINDS:
        return False, (
            f"source_kind 非法：{fact.source_kind!r}，只允许 {sorted(ALLOWED_SOURCE_KINDS)}"
            "（来源不可追溯的事实无法审计、无法按来源降权）")

    if not str(fact.supplier_id or "").strip():
        return False, (
            "缺 supplier_id：记忆以供应商为锚点 —— 同一缩写在不同供应商指不同 SKU，"
            "没有锚点就只能退化成按名字分叉的自由文本（gap 05 实测 SKU 匹配率 23.7%）")

    if not str(fact.tenant_id or "").strip():
        return False, (
            "缺 tenant_id：读侧隔离靠 tenant_id，缺省落 default 桶会把一条记忆注入给"
            "任意租户（gap 05 §H1）")

    key = str(fact.fact_key or "").strip()
    if not (FACT_KEY_MIN_LEN <= len(key) <= FACT_KEY_MAX_LEN):
        return False, (
            f"fact_key 长度越界：{len(key)}，需在 {FACT_KEY_MIN_LEN}-{FACT_KEY_MAX_LEN} 之间"
            "（过短无法区分事实，过长说明把整段文本当成了 key）")

    if not str(fact.value or "").strip():
        return False, "value 为空：事实必须有内容（空值表示“未抽取到”，不应入库）"

    try:
        vf = _parse_ymd(fact.valid_from)
        vt = _parse_ymd(fact.valid_to)
    except ValueError as e:
        return False, f"valid_from/valid_to 需为 YYYY-MM-DD（或 ISO 时间戳）：{e}"
    if vf and vt and vt < vf:
        return False, f"valid_to 早于 valid_from：{vt} < {vf}（这条事实一写入即过期）"

    trust = _coerce_trust_level(fact.trust_level)
    if trust is None or not (0.0 <= trust <= 1.0):
        return False, f"trust_level 越界：{fact.trust_level!r}，需在 0.0-1.0 之间"

    return True, ""


def _field_defaults() -> Dict[str, Any]:
    """dataclass 各字段的缺省值（无缺省的身份字段回落空串，交由校验拒绝）。"""
    out: Dict[str, Any] = {}
    for f in fields(MemoryFact):
        out[f.name] = "" if f.default is MISSING else f.default
    return out


def _coerce_trust_level(value: Any) -> Optional[float]:
    """trust_level 归一成 float；无法归一返回 None，由调用方决定拒绝还是保留。

    NaN / Infinity 由范围判断拦下（`0.0 <= nan <= 1.0` 恒为 False），不额外特判。
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_ymd(value: Any) -> Optional[date]:
    """解析 valid_from / valid_to；空值返回 None（表示该侧无边界）。

    非法写法抛 ValueError，由 `validate_fact` 转成可读理由 —— 不静默当作"无边界"，
    否则一个写错的日期会让本该过期的记忆永久驻留。
    """
    s = "" if value is None else str(value).strip()
    if not s:
        return None
    m = _YMD_RE.match(s)
    if not m:
        raise ValueError(repr(s))
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(repr(s))
