# -*- coding: utf-8 -*-
"""冲突整合决策的契约测试（计划 P3-T3 / Stream P3c1 · G1）。

覆盖六条判定各至少一条用例，外加 LLM 越界 / 不可解析 / 抛异常三条回退，以及值归一
（大小写与空白、价格区间端点写法）两条：

1. ① 候选非法 → NOOP（理由带非法原因，且不得抛异常）；
2. ② 无既有事实 → ADD；
3. ③ 同 key 同值 → NOOP（值按 kind 归一：文本去空白+折叠大小写、区间按端点）；
4. ④ 同 key 且既有来源可信更高 → NOOP（低可信不得覆盖高可信，防记忆投毒）；
5. ⑤ 同 key 异值且候选可信不低于既有 → UPDATE，superseded_value 带旧值；
6. ⑥ 不同 key → 有 llm 时交给它做显式矛盾判定；无 llm 则 ADD；
7. llm 输出 action 越界 / 非 JSON / 抛异常 → 一律回退 ADD 且把原因写进 reason，不抛；
8. D7 脏既有行（`from_row` 会抛错 / 非行对象）被跳过时 → 降级保留不变，但必须有
   warning 留痕**且**判定结果里查得到"跳过了 N 条"，同时无脏行路径逐字段不变。

判别力（反证）：第 ④ 条可信度守卫是判别式目标 —— 把 `memory_consolidate` 里
`_source_rank(prior) < _source_rank(cand)` 那个分支注释掉后，
`test_low_trust_cannot_override_manual`、`test_trust_order_is_the_full_chain`、
`test_trust_level_alone_does_not_override_manual`、`test_multiple_same_key_rows_pick_most_trusted_as_prior`
必须失败（流程会落到 ⑤ 变成 UPDATE）。D7 同样有判别式目标：去掉 `_as_fact` 的
`logger.warning` 与 `decide_action` 的 `_dirty_note` 附加后，本文件 D7 那节必须失败。
执行过的反证见交付报告。

环境隔离：本模块不碰 DB / 向量库、不联网（LLM 一律注入 stub）；仍按同波次约定把
DB_PATH / RAG_DIR 指向临时目录，避免将来误加 import 时把产物写到 live。
"""

import ast
import collections.abc
import json
import logging
import os
import pathlib
import sys
import tempfile

# 隔离环境：临时 DB / 向量目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="memory_consolidate_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "memory_consolidate.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")

_DEMO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo")
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import pytest  # noqa: E402

import app.services.memory_consolidate as mc  # noqa: E402
from app.services.memory_facts import (  # noqa: E402
    ALLOWED_SOURCE_KINDS,
    KIND_ALIAS,
    KIND_LAYOUT,
    KIND_PRICE_RANGE,
    KIND_UNIT,
    MemoryFact,
    SOURCE_APPROVE,
    SOURCE_EXTRACT,
    SOURCE_FEEDBACK_DISTILLED,
    SOURCE_MANUAL,
)

SUPPLIER = "sup-0001"
TENANT = "tenant-a"


class _Msg:
    """最小 AIMessage 替身：真实通道正文在 `.content`。"""

    def __init__(self, content):
        self.content = content


class _StubModel:
    """记录调用次数的 stub 模型（测试全程不联网，任务红线）。"""

    def __init__(self, output=None, exc=None):
        self.output = output
        self.exc = exc
        self.calls = 0
        self.messages = None

    def invoke(self, messages):
        self.calls += 1
        self.messages = messages
        if self.exc is not None:
            raise self.exc
        return self.output


def _fact(**over):
    """一条各项都合法的基准事实；用 over 注入单点差异。"""
    base = {
        "kind": KIND_ALIAS,
        "fact_key": "黑牛",
        "value": "黑牛牌黑胡椒牛油",
        "supplier_id": SUPPLIER,
        "tenant_id": TENANT,
        "source_kind": SOURCE_MANUAL,
    }
    base.update(over)
    return MemoryFact(**base)


def _messages_text(model):
    parts = []
    for m in model.messages or []:
        content = getattr(m, "content", "")
        parts.append(content if isinstance(content, str) else str(content))
    return "\n".join(parts)


# -------------------------------------------------------------
# ① 候选非法 → NOOP
# -------------------------------------------------------------
def test_invalid_candidate_is_noop_with_reason():
    bad = _fact(kind="price")  # 白名单外
    res = mc.decide_action(None, bad)
    assert res.action == mc.ACTION_NOOP
    assert "非法" in res.reason and "kind" in res.reason, res.reason
    assert res.superseded_value == ""


def test_invalid_candidate_does_not_raise_on_row_dict():
    """行字典入口同样走 validate_fact：脏输入给可读理由，而不是抛异常。"""
    res = mc.decide_action(_fact(), {"fact_kind": {"x": 1}, "fact_key": "k",
                                     "fact_value": "v", "supplier_id": "s",
                                     "tenant_id": "t"})
    assert res.action == mc.ACTION_NOOP
    assert "非法" in res.reason, res.reason


def test_candidate_missing_scope_is_noop():
    for over in ({"tenant_id": ""}, {"supplier_id": "  "}):
        res = mc.decide_action(_fact(), _fact(**over))
        assert res.action == mc.ACTION_NOOP, over


# -------------------------------------------------------------
# ② 无既有事实 → ADD
# -------------------------------------------------------------
def test_empty_existing_is_add():
    for empty in (None, [], (), set()):
        res = mc.decide_action(empty, _fact())
        assert res.action == mc.ACTION_ADD, empty
        assert res.reason.strip()
    # 单条 MemoryFact 形态与新事实比对，也是"有既有"的合法输入
    assert mc.decide_action(_fact(), _fact()).action == mc.ACTION_NOOP


def test_candidate_may_be_row_dict_for_add():
    row = _fact().to_row()
    res = mc.decide_action(None, row)
    assert res.action == mc.ACTION_ADD, res.reason


# -------------------------------------------------------------
# ③ 同 key 同值 → NOOP
# -------------------------------------------------------------
def test_same_key_equal_value_is_noop():
    existing = _fact(value="黑牛牌黑胡椒牛油")
    res = mc.decide_action(existing, _fact(value="黑牛牌黑胡椒牛油"))
    assert res.action == mc.ACTION_NOOP
    assert "同值" in res.reason, res.reason


def test_value_normalization_case_and_whitespace():
    """值归一：去全部空白 + 折叠大小写 —— OCR 分词与大小写差异不是新事实。"""
    cases = [
        (" 黑牛 ", "黑牛"),
        ("黑 牛", "黑牛"),
        ("ABC Brand", "abc  brand"),
    ]
    for existing_value, candidate_value in cases:
        res = mc.decide_action(
            _fact(value=existing_value), _fact(value=candidate_value))
        assert res.action == mc.ACTION_NOOP, (existing_value, candidate_value, res.reason)
    # unit / layout 同样适用（三类文本口径一致）
    for kind in (KIND_UNIT, KIND_LAYOUT):
        assert mc.decide_action(
            _fact(kind=kind, value=" 斤 "), _fact(kind=kind, value="斤"),
        ).action == mc.ACTION_NOOP, kind


def test_value_normalization_price_range_endpoints():
    """价格区间按端点比：单位后缀、空白、小数写法、端点顺序都不构成新事实。"""
    cases = [
        ("8.0-10.0 HKD/斤", "8 - 10"),
        ("10-8", "8-10"),
        ("8", "8.00"),
    ]
    for existing_value, candidate_value in cases:
        res = mc.decide_action(
            _fact(kind=KIND_PRICE_RANGE, value=existing_value),
            _fact(kind=KIND_PRICE_RANGE, value=candidate_value),
            )
        assert res.action == mc.ACTION_NOOP, (existing_value, candidate_value, res.reason)
    # 端点真的不同 → 不是同值（UPDATE，因为默认来源同为 manual）
    assert mc.decide_action(
        _fact(kind=KIND_PRICE_RANGE, value="8-10"),
        _fact(kind=KIND_PRICE_RANGE, value="8-12"),
    ).action == mc.ACTION_UPDATE


def test_unparseable_range_falls_back_to_text_scope():
    """区间解析不出端点时退回文本口径，且不得与可解析值撞车。"""
    assert mc.decide_action(
        _fact(kind=KIND_PRICE_RANGE, value="待定"),
        _fact(kind=KIND_PRICE_RANGE, value="待定"),
    ).action == mc.ACTION_NOOP
    assert mc.decide_action(
        _fact(kind=KIND_PRICE_RANGE, value="待定"),
        _fact(kind=KIND_PRICE_RANGE, value="8-10"),
    ).action == mc.ACTION_UPDATE


# -------------------------------------------------------------
# ④ 可信度守卫：低可信不得覆盖高可信
# -------------------------------------------------------------
def test_low_trust_cannot_override_manual():
    """判别式：注释掉第 ④ 条守卫后，本用例必须失败（会变成 UPDATE）。"""
    existing = _fact(value="黑牛牌黑胡椒牛油", source_kind=SOURCE_MANUAL)
    candidate = _fact(value="黑牛牌牛油(模型猜的)", source_kind=SOURCE_EXTRACT)
    res = mc.decide_action(existing, candidate)
    assert res.action == mc.ACTION_NOOP, res.reason
    assert "不得覆盖" in res.reason and "低可信" in res.reason, res.reason
    assert res.superseded_value == ""


def test_trust_order_is_the_full_chain():
    """逐级下降都拦得住：manual > approve > feedback_distilled > extract。"""
    order = (SOURCE_MANUAL, SOURCE_APPROVE, SOURCE_FEEDBACK_DISTILLED, SOURCE_EXTRACT)
    for i, higher in enumerate(order):
        for lower in order[i + 1:]:
            res = mc.decide_action(
                _fact(value="旧值", source_kind=higher),
                _fact(value="新值", source_kind=lower),
                )
            assert res.action == mc.ACTION_NOOP, (higher, lower, res.reason)
            assert "不得覆盖" in res.reason


def test_unknown_source_ranks_lowest():
    """既有来源不在排序表里 → 按最低可信：挡不住已知来源的候选（可被更新）。

    方向说明：未知来源排在 `SOURCE_TRUST_ORDER` 之末。若把它当成高可信，历史脏行会
    永久锁死该 key（本仓"归档后无恢复通道"的同类问题）；若当成低可信，任何已知来源的
    候选都能更正它。候选侧不可能出现未知来源（`validate_fact` 已把 source_kind 白名单
    锁死），故这条只作用于既有行。
    """
    assert mc.decide_action(
        _fact(value="旧值", source_kind="legacy_import"),
        _fact(value="新值", source_kind=SOURCE_EXTRACT),
    ).action == mc.ACTION_UPDATE
    assert mc.decide_action(
        _fact(value="旧值", source_kind="legacy_import"),
        _fact(value="新的人工值", source_kind=SOURCE_MANUAL),
    ).action == mc.ACTION_UPDATE


def test_trust_level_alone_does_not_override_manual():
    """守卫只认 source_kind：抽取事实自称 confidence=1.0 也覆盖不了人工确认。"""
    res = mc.decide_action(
        _fact(value="人工确认值", source_kind=SOURCE_MANUAL, trust_level=0.5),
        _fact(value="模型自称满信", source_kind=SOURCE_EXTRACT, trust_level=1.0),
    )
    assert res.action == mc.ACTION_NOOP, res.reason


# -------------------------------------------------------------
# ⑤ 同 key 异值 + 候选可信不低于既有 → UPDATE
# -------------------------------------------------------------
def test_same_key_different_value_higher_trust_is_update():
    existing = _fact(value="旧写法", source_kind=SOURCE_EXTRACT)
    candidate = _fact(value="人工更正写法", source_kind=SOURCE_MANUAL)
    res = mc.decide_action(existing, candidate)
    assert res.action == mc.ACTION_UPDATE, res.reason
    assert res.superseded_value == "旧写法", res.superseded_value
    assert "异值" in res.reason and "supersede" in res.reason, res.reason


def test_same_trust_different_value_is_update():
    """等可信允许覆盖：否则重复抽取同一 key 会永远卡死在第一条（同一模型来源自纠）。"""
    res = mc.decide_action(
        _fact(value="第一次抽的值", source_kind=SOURCE_EXTRACT),
        _fact(value="第二次抽的值", source_kind=SOURCE_EXTRACT),
    )
    assert res.action == mc.ACTION_UPDATE, res.reason
    assert res.superseded_value == "第一次抽的值"


def test_different_key_is_not_compared_without_llm():
    """不同 key 是不同的事实：无 llm 时按 ADD（不误判为覆盖）。"""
    existing = _fact(fact_key="牛油", value="黑牛牌黑胡椒牛油")
    candidate = _fact(fact_key="菜心", value="甜菜心")
    res = mc.decide_action(existing, candidate)
    assert res.action == mc.ACTION_ADD, res.reason
    assert res.reason.strip()


def test_different_scope_is_not_same_key():
    """同 fact_key 但租户/供应商不同 → 不是同一条事实（防跨租户覆盖）。"""
    for over in ({"tenant_id": "tenant-b"}, {"supplier_id": "sup-0002"}):
        res = mc.decide_action(_fact(value="旧值"), _fact(value="新值", **over))
        assert res.action == mc.ACTION_ADD, (over, res.reason)


def test_multiple_same_key_rows_pick_most_trusted_as_prior():
    """同 key 多行（历史遗留/并发）取最权威的一条比对，旧值也取它。"""
    existing = [
        _fact(value="抽取的值", source_kind=SOURCE_EXTRACT, trust_level=0.9),
        _fact(value="人工确认的值", source_kind=SOURCE_MANUAL, trust_level=0.3),
    ]
    res = mc.decide_action(existing, _fact(value="又来一个模型值",
                                           source_kind=SOURCE_EXTRACT))
    assert res.action == mc.ACTION_NOOP, res.reason
    assert "不得覆盖" in res.reason


# -------------------------------------------------------------
# ⑥ LLM 分支
# -------------------------------------------------------------
def _llm_payload(action, reason="依据"):
    return _Msg(json.dumps({"action": action, "reason": reason}, ensure_ascii=False))


def test_different_key_with_llm_honors_returned_action():
    llm = _StubModel(output=_llm_payload("UPDATE", "候选更正了既有的单位口径"))
    res = mc.decide_action(
        _fact(fact_key="菜心", value="斤"),
        _fact(kind=KIND_UNIT, fact_key="甜菜心", value="公斤"),
        llm=llm,
    )
    assert llm.calls == 1
    assert res.action == mc.ACTION_UPDATE, res.reason
    assert "候选更正了既有的单位口径" in res.reason
    assert res.superseded_value == "斤", res.superseded_value


def test_llm_receives_both_facts_in_prompt():
    llm = _StubModel(output=_llm_payload("ADD"))
    mc.decide_action(_fact(fact_key="牛油", value="黑牛牌"), _fact(fact_key="菜心"),
                     llm=llm)
    text = _messages_text(llm)
    assert "牛油" in text and "黑牛牌" in text and "菜心" in text, text


def test_llm_noop_is_honored():
    llm = _StubModel(output=_llm_payload("NOOP", "两者语义一致"))
    res = mc.decide_action(_fact(fact_key="牛油"), _fact(fact_key="菜心"), llm=llm)
    assert res.action == mc.ACTION_NOOP, res.reason
    assert res.superseded_value == ""


def test_llm_delete_carries_old_value():
    llm = _StubModel(output=_llm_payload("DELETE", "候选证明旧事实已失效"))
    res = mc.decide_action(
        _fact(fact_key="牛油", value="已停用的旧写法"), _fact(fact_key="菜心"), llm=llm)
    assert res.action == mc.ACTION_DELETE, res.reason
    assert res.superseded_value == "已停用的旧写法"


def test_llm_out_of_range_action_falls_back_to_add():
    llm = _StubModel(output=_llm_payload("REPLACE"))
    res = mc.decide_action(_fact(fact_key="牛油"), _fact(fact_key="菜心"), llm=llm)
    assert res.action == mc.ACTION_ADD, res.reason
    assert "越界" in res.reason and "回退" in res.reason, res.reason


def test_llm_non_json_output_falls_back_to_add():
    for output in (_Msg("我觉得应该覆盖"), _Msg(""), None, _Msg("[]")):
        llm = _StubModel(output=output)
        res = mc.decide_action(_fact(fact_key="牛油"), _fact(fact_key="菜心"), llm=llm)
        assert res.action == mc.ACTION_ADD, (output, res.reason)
        assert "回退" in res.reason, res.reason


def test_llm_exception_falls_back_to_add():
    """绝不向上抛：整合跑在抽取之后，抛异常会把"候选本身是合法事实"一起丢掉。"""
    llm = _StubModel(exc=RuntimeError("upstream 500"))
    res = mc.decide_action(_fact(fact_key="牛油"), _fact(fact_key="菜心"), llm=llm)
    assert res.action == mc.ACTION_ADD, res.reason
    assert "异常" in res.reason and "回退" in res.reason, res.reason


def test_llm_invoke_returning_bad_content_does_not_raise():
    class _Weird:
        content = {"action": "UPDATE"}  # 非 str 正文

    llm = _StubModel(output=_Weird())
    res = mc.decide_action(_fact(fact_key="牛油"), _fact(fact_key="菜心"), llm=llm)
    assert res.action in mc.ALLOWED_ACTIONS, res.reason


def test_llm_not_called_when_rules_decide():
    """规则能判定时不得付模型调用（值相等 / 可信度守卫 / 同 key 异值三条路径）。"""
    llm = _StubModel(output=_llm_payload("UPDATE"))
    # ③ 同值
    assert mc.decide_action(_fact(value="同值"), _fact(value="同值"),
                            llm=llm).action == mc.ACTION_NOOP
    # ④ 守卫
    assert mc.decide_action(_fact(value="旧", source_kind=SOURCE_MANUAL),
                            _fact(value="新", source_kind=SOURCE_EXTRACT),
                            llm=llm).action == mc.ACTION_NOOP
    # ⑤ 覆盖
    assert mc.decide_action(_fact(value="旧", source_kind=SOURCE_EXTRACT),
                            _fact(value="新", source_kind=SOURCE_MANUAL),
                            llm=llm).action == mc.ACTION_UPDATE
    assert llm.calls == 0, llm.calls


def test_llm_not_called_when_add_or_invalid():
    llm = _StubModel(output=_llm_payload("UPDATE"))
    assert mc.decide_action(None, _fact(), llm=llm).action == mc.ACTION_ADD
    assert mc.decide_action(None, _fact(kind="price"), llm=llm).action == mc.ACTION_NOOP
    assert llm.calls == 0, llm.calls


# -------------------------------------------------------------
# ⑧ D7 脏既有行的可见性（跳过必须留痕，且不得改变正常路径）
# -------------------------------------------------------------
class _UnreadableRow(collections.abc.Mapping):
    """一条"读取即抛错"的既有行：`MemoryFact.from_row` 会在 `row.get()` 时抛错。

    为什么用这种构造而不是 monkeypatch：`from_row` 对缺列 / NULL / 坏 trust_level 都极其
    宽容（回落缺省或交 `validate_fact` 拒绝），要让"归一"这一步本身抛错，只有"行对象读不出来"
    这一种真实形态 —— 正好对应列类型损坏 / 结果集代理失效的脏行。`Mapping.get` 只吞 KeyError，
    故 `__getitem__` 抛的 RuntimeError 会原样冒泡到 `from_row`。

    键名用于构造"概念上同 key"的脏行（脏行读不出 key，同 key 是它在 DB 里本应代表的事实）。
    """

    def __init__(self, key="黑牛"):
        self._key = key

    def __getitem__(self, item):
        raise RuntimeError("row storage corrupted")

    def __iter__(self):
        raise RuntimeError("row storage corrupted")

    def __len__(self):
        return 0


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "memory_consolidate"]


def test_dirty_same_key_existing_row_is_visible_in_reason_and_log(caplog):
    """判别式（D7）：同 key 的脏既有行被跳过时，留痕必须同时出现在日志与判定结果里。

    语义后果：该行读不出来 ⇒ 同 key 候选被判 ADD（与脏旧值并存），而不是 UPDATE 去
    supersede 它 —— 调用方若只看 `ConsolidationResult` 而不知有行被跳过，就会以为
    "确无同 key 既有事实"，从而完全察觉不到 supersede 被绕过。
    """
    dirty = _UnreadableRow(key="黑牛")
    with caplog.at_level(logging.WARNING, logger="memory_consolidate"):
        res = mc.decide_action([dirty], _fact(value="人工更正的黑牛写法"))

    # (a) warning 日志：必须带异常类型与文本（否则无法定位是哪一行、为什么读不出）
    logs = _warnings(caplog)
    assert logs, "脏既有行被跳过却没有任何 warning 留痕"
    assert any("RuntimeError" in m and "跳过" in m for m in logs), logs

    # (b) 判定结果里也查得到"跳过了脏行"（不得只能从日志知道）
    assert res.action == mc.ACTION_ADD, res.reason
    assert "跳过" in res.reason and "1 条" in res.reason, res.reason
    assert "supersede" in res.reason, res.reason


def test_no_dirty_rows_keeps_decision_and_reason_unchanged():
    """正面对照：无脏行时 action / superseded_value / reason 与修改前逐字段一致。

    精确钉住正常路径的文案 —— 补脏行可见性不得顺带改动任何判定或 reason 文本。
    """
    res = mc.decide_action(_fact(value="旧写法"), _fact(value="新写法"))
    assert res.action == mc.ACTION_UPDATE
    assert res.superseded_value == "旧写法"
    assert res.reason == (
        "同 key 异值（key='黑牛'）且候选可信不低于既有："
        "候选 source_kind='manual' vs 既有 'manual'，旧值 '旧写法' 转入 supersede 链"
    ), res.reason
    assert "跳过" not in res.reason


def test_all_existing_rows_dirty_still_adds_with_visible_note(caplog):
    """全部既有行都脏时仍判 ADD（降级不阻断），但不得让 reason 谎称"无既有事实"。"""
    with caplog.at_level(logging.WARNING, logger="memory_consolidate"):
        res = mc.decide_action(
            [_UnreadableRow(), _UnreadableRow(key="菜心")], _fact(value="新写法"))
    assert res.action == mc.ACTION_ADD, res.reason
    assert "无既有事实" in res.reason  # 原有文案保留，不因补可见性而改写判定
    assert "跳过" in res.reason and "2 条" in res.reason, res.reason
    assert len(_warnings(caplog)) == 2


def test_dirty_row_does_not_hide_readable_same_key_row(caplog):
    """脏行只被跳过、不干扰其余既有事实：可读的同 key 行仍照常驱动判定。"""
    existing = [_UnreadableRow(), _fact(value="旧写法", source_kind=SOURCE_MANUAL)]
    with caplog.at_level(logging.WARNING, logger="memory_consolidate"):
        res = mc.decide_action(existing, _fact(value="新写法", source_kind=SOURCE_EXTRACT))
    # 可读的人工确认值仍然拦下低可信候选（④ 守卫照常生效）
    assert res.action == mc.ACTION_NOOP, res.reason
    assert "不得覆盖" in res.reason
    # 脏行提醒附在同一段 reason 末尾
    assert "跳过" in res.reason and "1 条" in res.reason, res.reason
    assert any("RuntimeError" in m for m in _warnings(caplog))


def test_non_mapping_existing_item_is_also_visible(caplog):
    """同一根因的另一入口：列表里混进非行对象（裸字符串）同样不得静默丢弃。"""
    with caplog.at_level(logging.WARNING, logger="memory_consolidate"):
        res = mc.decide_action(["alias"], _fact())
    assert res.action == mc.ACTION_ADD, res.reason
    assert "跳过" in res.reason and "1 条" in res.reason, res.reason
    assert any("类型非法" in m for m in _warnings(caplog)), _warnings(caplog)


def test_dirty_existing_still_never_raises(caplog):
    """降级不得改成抛错：一条历史脏行不能让该供应商的事实永远无法整合。"""
    with caplog.at_level(logging.WARNING, logger="memory_consolidate"):
        for existing in ([_UnreadableRow()], _UnreadableRow(), [_UnreadableRow()] * 3):
            res = mc.decide_action(existing, _fact())
            assert res.action in mc.ALLOWED_ACTIONS, (existing, res)
            assert res.reason.strip()


# -------------------------------------------------------------
# 契约与纯逻辑红线
# -------------------------------------------------------------
def test_action_is_always_whitelisted_and_never_raises():
    """穷举一组输入，action 必须始终落在白名单内、reason 非空、绝不抛。"""
    candidates = [
        _fact(),
        _fact(kind="price"),
        _fact(value=""),
        _fact(tenant_id=""),
        {"fact_kind": {"x": 1}},
        None,
        "alias",
        ["alias"],
    ]
    existings = [None, [], _fact(), [_fact()], _fact(source_kind="weird"), {"a": 1}]
    for cand in candidates:
        for ex in existings:
            res = mc.decide_action(ex, cand)
            assert res.action in mc.ALLOWED_ACTIONS, (cand, ex, res)
            assert isinstance(res.reason, str) and res.reason.strip(), (cand, ex, res)
            assert isinstance(res.superseded_value, str)
    # None 候选（无法构造 fact）也必须给 NOOP 而不是抛
    assert mc.decide_action(_fact(), None).action in mc.ALLOWED_ACTIONS


def test_allowed_actions_match_plan():
    assert mc.ALLOWED_ACTIONS == {"ADD", "UPDATE", "DELETE", "NOOP"}


def test_source_trust_order_covers_all_sources():
    """漂移守卫：memory_facts 新增第五种来源而忘了排序 → 它会被静默当最低可信。"""
    assert set(mc.SOURCE_TRUST_ORDER) == set(ALLOWED_SOURCE_KINDS), (
        mc.SOURCE_TRUST_ORDER, sorted(ALLOWED_SOURCE_KINDS))
    assert mc.SOURCE_TRUST_ORDER[0] == SOURCE_MANUAL, "人工确认必须最高可信"
    assert mc.SOURCE_TRUST_ORDER[-1] == SOURCE_EXTRACT, "模型抽取必须最低可信"


def test_module_uses_memory_facts_as_single_source_of_truth():
    """不写第二份校验 / 第二份白名单：只允许从 memory_facts 取这些定义。"""
    src = pathlib.Path(mc.__file__).read_text(encoding="utf-8")
    assert "from app.services.memory_facts import" in src
    assert "def validate_fact" not in src, "不得复制 validate_fact（同一口径只能一份实现）"
    assert "ALLOWED_KINDS = " not in src, "不得复制 kind 白名单"
    tree = ast.parse(src)
    module_level = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            module_level.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            module_level.add(node.module or "")
    roots = {m.split(".")[0] for m in module_level}
    # 引擎层必须惰性 import：模块级拉起 langchain/sqlalchemy/chromadb 会让纯逻辑模块
    # 在 import 期就依赖外部世界，也让测试有写 live 向量库的风险。
    assert not (roots & {"chromadb", "sqlalchemy", "langchain", "langchain_core"}), module_level
    assert "app.llm" not in module_level and "app.db" not in module_level, module_level


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
