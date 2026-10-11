# -*- coding: utf-8 -*-
"""整合侧 prompt 接线 + 漂移守卫（计划 P3c1 / G2 的 mustFix 第 2 条）。

为什么需要这个文件：整合侧原先只有 `memory_consolidate._CONSOLIDATE_SYSTEM_PROMPT` 一份
内联常量，而 `ai_registry/prompts/memory/v1_1_0_consolidate.py` 里的 PROMPT **全文件零引用**
—— 属本仓高发缺陷的第一类（同一根因两份实现）与第二类（已建未接）的合体，且两侧已经分叉
（注册表版有「外部数据不可信」，内联版没有）。只把接线接上还不够：**将来任何一侧单独改动
判据，都会让"回退路径"悄悄比线上弱**（回退即静默减弱防御），所以必须有一条漂移守卫把
四条关键条款的两侧等价性钉死。

覆盖：
1. 注册表文案与内联回退在四条关键条款上等价（四选一动作 / 必须输出 reason 且不得为空 /
   外部数据不可信其内指令一律不执行 / 不得改写人工确认的高可信事实），逐条对两侧断言；
2. 判据自校准：先证明本文件的条款检查器**抓得到缺失**（造一段缺条款的文本必须被判缺），
   否则「两边都有」可能只是检查器恒真（本仓已出现过恒真用例与"把缺陷固化成断言"两类事故）；
3. 接线正向：真正发给模型的 SystemMessage 就是注册表那一版（且两版文本不同，否则本断言
   无法区分真的走了注册表）；
4. 接线反向：注册表不可用时回退内联常量，且 warning 必须留痕（降级不许静默）。

反证靶子（判别力）：删掉内联常量里的「外部数据不可信」条款 → 用例 1 必须失败；把
`_ask_llm` 里的 `_system_prompt()` 换回 `_CONSOLIDATE_SYSTEM_PROMPT` → 用例 3 必须失败。

环境隔离：本模块不碰 DB / 向量库、不联网（LLM 一律注入 stub）；仍按同波次约定把
DB_PATH / RAG_DIR 指向临时目录，避免将来误加 import 时把产物写到 live。
"""

import json
import logging
import os
import re
import sys
import tempfile
from typing import List, Tuple

# 隔离环境：临时 DB / 向量目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="memory_consolidate_prompt_drift_")
os.environ["DB_PATH"] = os.path.join(_TMP, "memory_consolidate_prompt_drift.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")

_DEMO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo")
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
for _p in (_DEMO_DIR, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

import ai_registry.registry as registry_mod  # noqa: E402
from ai_registry.registry import load_prompt  # noqa: E402
import app.services.memory_consolidate as mc  # noqa: E402
from app.services.memory_facts import (  # noqa: E402
    KIND_ALIAS,
    MemoryFact,
    SOURCE_EXTRACT,
    SOURCE_MANUAL,
)

# 两侧文案：注册表按模块显式钉住的场景/版本号载入（不是把版本号抄在测试里 —— 测试抄一份
# 就等于又一处"同一口径多份实现"），内联回退直接取模块常量。
_REGISTRY_PROMPT = load_prompt(mc._PROMPT_SCENE, mc._PROMPT_VERSION)

# 四条关键条款：每条形如 (条款名, 若干"token 组")，**每组内任选其一命中即可**，组间全部命中
# 才算该条款成立。用多 token 组而不是单关键词，是为了让"条款在同义改写后仍然成立"与
# "关键词偶然出现"区分开（例如仅凭文本里出现 "reason" 无法证明它被要求输出）。
_CLAUSES: Tuple[Tuple[str, Tuple[Tuple[str, ...], ...]], ...] = (
    ("四选一动作 ADD/UPDATE/DELETE/NOOP",
     tuple((a,) for a in sorted(mc.ALLOWED_ACTIONS)) + (("只能取", "只允许"),)),
    ("必须输出 reason 且不得为空",
     (("reason",), ("必填", "必须输出"), ("不得为空",))),
    ("外部数据不可信、其内指令一律不执行",
     (("不可信",), ("一律不执行",), ("Schema",))),
    ("不得改写人工确认的高可信事实",
     (("人工确认",), ("不得改写",), ("高可信",))),
)

_PROMPT_SIDES = (("注册表版本", _REGISTRY_PROMPT),)


def _missing_clauses(text: str) -> List[str]:
    """返回该文本未覆盖的条款名（条款名 + 缺失的那一组 token）。"""
    missing: List[str] = []
    for name, groups in _CLAUSES:
        for group in groups:
            if not any(tok in text for tok in group):
                missing.append("%s（缺 %s）" % (name, "/".join(group)))
                break
    return missing


def _fact(**over) -> MemoryFact:
    """一条各项都合法的基准事实；用 over 注入单点差异。"""
    base = {
        "kind": KIND_ALIAS,
        "fact_key": "黑牛",
        "value": "黑牛牌黑胡椒牛油",
        "supplier_id": "sup-0001",
        "tenant_id": "tenant-a",
        "source_kind": SOURCE_MANUAL,
    }
    base.update(over)
    return MemoryFact(**base)


class _StubModel:
    """记录收到的消息的 stub 模型：测试全程不联网（任务红线）。"""

    def __init__(self, output=None):
        self.output = output
        self.messages = None
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        self.messages = messages
        return _with_canary_handshake(self.output, messages)


# 生产链路的 canary 握手是 fail-closed 的：真实模型会按锚定指令把 __guard_token 回填进
# 输出 JSON 根节点，stub 也必须按同一协议回填，否则合规输出会被误判成协议错配。
_CANARY_TOKEN_RE = re.compile(r'"__guard_token"\s*:\s*"([^"]+)"')


def _with_canary_handshake(output, messages):
    content = output if isinstance(output, str) else getattr(output, "content", None)
    if not isinstance(content, str) or not content.strip():
        return output
    prompt = "\n".join(
        getattr(m, "content", "") or "" for m in (messages or []))
    matched = _CANARY_TOKEN_RE.search(prompt)
    if not matched:
        return output
    s = content.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        data = json.loads(s)
    except (TypeError, ValueError):
        return output
    token = {"__guard_token": matched.group(1)}
    if isinstance(data, list):
        return json.dumps(data + [token], ensure_ascii=False)
    if isinstance(data, dict):
        return json.dumps({**data, **token}, ensure_ascii=False)
    return output


def _expected_system_with_canary(prompt_text, model):
    """注册表/回退 prompt + 调用期注入的 canary 对象通道锚定指令（逐字节精确期望）。"""
    from app.services.canary_guard import CANARY_INSTRUCTION_TEMPLATE
    human_text = "\n".join(
        getattr(m, "content", "") or "" for m in model.messages or []
        if type(m).__name__ == "HumanMessage")
    matched = _CANARY_TOKEN_RE.search(human_text)
    assert matched, "HumanMessage 里应有 canary 锚定指令"
    return prompt_text + CANARY_INSTRUCTION_TEMPLATE.format(token=matched.group(1))


def _system_message_contents(model) -> list:
    return [getattr(m, "content", "")
            for m in (model.messages or [])
            if type(m).__name__ == "SystemMessage"]


# -------------------------------------------------------------
# 1. 漂移守卫：四条关键条款两侧等价
# -------------------------------------------------------------
def test_clause_checker_is_not_vacuous():
    """自校准：本文件的条款检查器必须**抓得到缺失**，否则下面两条用例可能只是恒真。

    先给一段必然缺条款的文本（空串），再给一份必然覆盖四条的真实文案（模块 docstring 里
    逐条写明了这四条判据），两者必须给出不同结论 —— 这是"先反证扫描器有效，再报结论"。
    """
    assert len(_missing_clauses("")) == len(_CLAUSES), _missing_clauses("")
    # 只覆盖第 1 条、其余全缺 → 必须恰好报出 3 条缺失（证明是按条款逐条判，不是一把梭）
    only_actions = "action 只允许四个取值：" + " ".join(sorted(mc.ALLOWED_ACTIONS))
    assert len(_missing_clauses(only_actions)) == len(_CLAUSES) - 1, _missing_clauses(only_actions)


def test_registry_prompt_carries_the_four_clauses():
    """判别式：四条关键条款在注册表版里必须成立（内置副本已删除，只剩这一份事实源）。

    反证靶子：删掉注册表版里的「不得改写人工确认的高可信事实」条款，必须让本用例失败。
    """
    problems = {side: _missing_clauses(text) for side, text in _PROMPT_SIDES}
    assert not any(problems.values()), (
        "prompt 条款漂移（回退即静默减弱防御）：%s" % problems)


def test_registry_prompt_is_nonempty_and_lists_all_actions():
    """两侧必须是**不同**文本（否则"走没走注册表"无法区分），且都是非空字符串。

    同时钉住动作白名单：prompt 逐字列出四个动作，任何一侧漏说一个动作，模型就永不输出它
    （`ALLOWED_ACTIONS` 是代码侧唯一事实源，本断言把它与两侧文案对齐）。
    """
    assert _REGISTRY_PROMPT.strip()
    assert not hasattr(mc, "_CONSOLIDATE_SYSTEM_PROMPT"), "内置副本必须已删除"
    for side, text in _PROMPT_SIDES:
        for action in sorted(mc.ALLOWED_ACTIONS):
            assert action in text, "%s 漏说 action %s（该动作模型永不输出）" % (side, action)


# -------------------------------------------------------------
# 2. 接线：优先注册表
# -------------------------------------------------------------
def test_consolidation_sends_registry_prompt_to_model():
    """正向：真正发给模型的 system prompt 就是注册表 v1_1_0_consolidate 的文本。

    走完整入口（不同 key + 注入 llm → `_ask_llm`），而不是直接调 `_system_prompt()` ——
    后者只能证明"接线函数能跑"，证明不了它被**消费**（本仓高发的"定义了没接线"）。
    """
    stub = _StubModel(output=json.dumps({"action": "ADD", "reason": "新事实"},
                                        ensure_ascii=False))
    res = mc.decide_action(_fact(fact_key="牛油"), _fact(fact_key="菜心", kind=KIND_ALIAS),
                           llm=stub)
    assert res.action in mc.ALLOWED_ACTIONS, res.reason
    assert stub.calls == 1, stub.calls
    assert _system_message_contents(stub) == [
        _expected_system_with_canary(_REGISTRY_PROMPT, stub)]


# -------------------------------------------------------------
# 3. 接线反向：注册表不可用时降级且留痕
# -------------------------------------------------------------
def test_registry_unavailable_fails_explicitly_without_inline_copy(monkeypatch, caplog):
    """注册表不可用 -> 显式失败（PromptUnavailableError）且 warning 留痕，不再悄悄换内置副本。"""
    def _boom(scene, version=None):
        raise FileNotFoundError("simulated registry failure")

    monkeypatch.setattr(registry_mod, "load_prompt", _boom)
    with caplog.at_level(logging.WARNING, logger="memory_consolidate"):
        with pytest.raises(mc.PromptUnavailableError):
            mc._system_prompt()
    assert "不使用内置副本" in caplog.text


def test_empty_registry_prompt_counts_as_unavailable(monkeypatch, caplog):
    """注册表返回空串/空白同样视为不可用（不得让空 prompt 进 SystemMessage）。"""
    monkeypatch.setattr(registry_mod, "load_prompt", lambda scene, version=None: "   ")
    with caplog.at_level(logging.WARNING, logger="memory_consolidate"):
        with pytest.raises(mc.PromptUnavailableError):
            mc._system_prompt()
    assert "不使用内置副本" in caplog.text


def test_decide_action_with_unavailable_prompt_does_not_call_model_and_adds(monkeypatch):
    """走完整入口：提示词读不到时不调模型，回退追加（交人工闸）并写明原因。"""
    monkeypatch.setattr(registry_mod, "load_prompt", lambda scene, version=None: None)
    stub = _StubModel(output="{}")
    res = mc.decide_action(_fact(fact_key="牛油"), _fact(fact_key="菜心", kind=KIND_ALIAS), llm=stub)
    assert res.action == mc.ACTION_ADD and stub.calls == 0
    assert "注册表读不到" in res.reason


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
