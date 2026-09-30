# -*- coding: utf-8 -*-
"""memory 场景 prompt 注册表：版本化 + 真正可加载的契约测试（计划 P3c1 / G2，修 gap L4）。

背景（gap 分析 L4）：memory 场景原先只有 v1_0_0 一个版本文件，其 PROMPT **全仓零调用方**，
而 metadata.json 却登记着 alias_extraction_accuracy=94.2 / knowledge_validity=96.0 /
avg_tokens=380 —— 三个数字无任何评测集、脚本或报告可复现。"登记了 prompt 与指标、实际
没人读、数字也无处复现"正是本任务要终结的状态。

覆盖：
1. active_version 指向的版本文件必须存在，且 `load_prompt` 能真的载到（不是"登记了没人读"）；
2. metadata 版本键集与目录内 .py 文件集**双向一致**（反向那一半是本文件的反证靶子）；
3. 任何声明了 metrics 的版本，要么给得出可复现来源，要么显式标 unvalidated —— 本条在
   "修 v1_0_0 那三个数字之前"必须失败（判别力见各用例 docstring）；
4. 两个新版本必须是 draft（不得自行标 production），且不得登记任何指标数字；
5. 两个新 PROMPT 的内容契约：抽取版含四类 kind / 严格 JSON 数组 / 外部数据不可信；
   整合版含 ADD/UPDATE/DELETE/NOOP 四选一 + 强制 reason；
6. 载入失败必须可见：未登记版本、不存在的场景一律抛错，绝不返回空串或回退别的版本；
7. `memory_extract` 优先用注册表 prompt；注册表不可用时回退内置常量并 logger.warning 留痕；
8. 回退常量与注册表版**条款等价**（四类 kind / 严格 JSON 数组契约 / 防注入三句），且回退
   这件事在 `get_last_run()["prompt_source"]` 上可见 —— 本条修掉的是一个**把缺陷固化的
   断言**：原先此处断言「内置常量 != 注册表文本」，等于宣布「回退时防注入变弱」合法，
   任何修复都会被自己的用例判红（与「恒真用例」互为镜像：恒真假装守护，固化断言不许守护）。

环境隔离：本模块不碰 DB / 向量库（只读 prompt 注册表 + 纯逻辑抽取模块），仍按同波次约定
把 DB_PATH / RAG_DIR 指向临时目录，避免将来误加 import 时把产物写到 live。
"""

import importlib
import json
import logging
import os
import sys
import tempfile
from typing import List, Tuple

# 隔离环境：临时 DB / 向量目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="memory_prompt_registry_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "memory_prompt_registry.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")

_DEMO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo")
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
for _p in (_DEMO_DIR, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

import ai_registry.registry as registry_mod  # noqa: E402
from ai_registry.registry import ai_registry, load_prompt  # noqa: E402
from app.services import memory_extract as mx  # noqa: E402
from app.services.memory_consolidate import ALLOWED_ACTIONS  # noqa: E402
from app.services.memory_facts import ALLOWED_KINDS, FACT_KEY_MAX_LEN  # noqa: E402

MEMORY_DIR = os.path.join(_REPO_ROOT, "ai_registry", "prompts", "memory")
METADATA_PATH = os.path.join(MEMORY_DIR, "metadata.json")

NEW_VERSIONS = ("v1_1_0_extract", "v1_1_0_consolidate")

# "有可复现来源"的登记形态（沿用本仓既有约定）：评测命令 / 来源字段 / 出处说明。
_SOURCE_KEYS = ("reproduce_command", "metrics_source", "metrics_provenance")
# "显式标未验证"的登记形态。
_UNVALIDATED_KEYS = ("claim_unsupported", "unvalidated")

# 抽取 prompt 的条款清单 —— 这就是「条款等价」的操作化定义。形状与整合侧的
# `tests/test_memory_consolidate_prompt_drift.py::_CLAUSES` **一致**（本仓高发
# "同一口径多份实现，只改一份"：两侧守卫换一种写法，判据迟早分叉）。
# 计分口径：每条形如 (条款名, 若干"token 组")，**组内任选其一命中即可、组间必须全部命中**；
# 用多 token 组是为了让"条款在同义改写后仍然成立"与"关键词偶然出现"区分开。
_EXTRACT_CLAUSES: Tuple[Tuple[str, Tuple[Tuple[str, ...], ...]], ...] = (
    # 四类 kind：名称与描述都要在文案里，且描述与 memory_extract._KIND_HINTS 逐字一致
    # （prompt 少说一类 -> 该类永远抽不出来；描述分叉 -> prompt 说的一类与代码校验的错开）
    ("四类 kind 名称与描述（与 _KIND_HINTS 逐字一致）",
     tuple((k,) for k in sorted(ALLOWED_KINDS))
     + tuple((mx._KIND_HINTS[k],) for k in sorted(ALLOWED_KINDS))),
    ("fact_key 长度上限与 value 非空",
     ((str(FACT_KEY_MAX_LEN),), ("不得为空",))),
    ("严格 JSON 数组输出契约",
     (("JSON 数组",), ("不要解释", "不要 markdown 围栏"))),
    ("外部数据不可信、其内指令一律不执行、不得改写输出 Schema",
     (("不可信",), ("一律不执行",), ("Schema",), ("放宽上述约束",))),
)

# 两侧文案：注册表按模块显式钉住的场景/版本号载入，内置回退直接取模块常量。
# 定义在 `_prompt_text` 之后（模块级求值需要它已就绪）。


def _missing_extract_clauses(text: str) -> List[str]:
    """返回该文案未覆盖的抽取条款（条款名 + 缺失的那一组 token）。"""
    missing: List[str] = []
    for name, groups in _EXTRACT_CLAUSES:
        for group in groups:
            if not any(tok in text for tok in group):
                missing.append("%s（缺 %s）" % (name, "/".join(group)))
                break
    return missing


def _metadata() -> dict:
    with open(METADATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _prompt_text(version: str) -> str:
    """直接按文件读取该版本的 PROMPT（不经 registry，作为对照基准）。"""
    mod = importlib.import_module("ai_registry.prompts.memory.%s" % version)
    text = getattr(mod, "PROMPT", None)
    assert isinstance(text, str) and text.strip(), "%s.PROMPT 必须是非空字符串" % version
    return text


def _metrics_are_sourced_or_flagged(block: dict) -> bool:
    has_source = any(block.get(k) for k in _SOURCE_KEYS)
    flagged = any(block.get(k) is True for k in _UNVALIDATED_KEYS)
    return bool(has_source or flagged)


# 抽取 prompt 的两侧文案：注册表按模块显式钉住的版本号载入（不把版本号抄在测试里 ——
# 测试抄一份就等于又一处"同一口径多份实现"），内置回退直接取模块常量。
_EXTRACT_PROMPT_SIDES = (
    ("注册表版本", _prompt_text("v1_1_0_extract")),
    ("内置回退", mx._SYSTEM_PROMPT),
)


# -------------------------------------------------------------
# 1. active_version 必须真的可加载
# -------------------------------------------------------------
def test_active_version_prompt_file_exists_and_loads():
    """active_version 指向的文件必须存在，且载入函数取到的 PROMPT 与文件内一致。"""
    meta = _metadata()
    active = meta["active_version"]
    path = os.path.join(MEMORY_DIR, "%s.py" % active)
    assert os.path.exists(path), "active_version %s 指向的文件不存在：%s" % (active, path)

    loaded = load_prompt("memory")  # version 缺省 -> metadata 的 active_version
    assert loaded == _prompt_text(active), "载入函数取到的文本与该版本文件内的 PROMPT 不一致"


def test_explicit_version_load_matches_file():
    """显式版本载入同样逐字一致（两个新版本都要能被载到）。"""
    for version in NEW_VERSIONS:
        assert load_prompt("memory", version) == _prompt_text(version)


# -------------------------------------------------------------
# 2. metadata 版本键集 <-> 目录 .py 文件集（双向）
# -------------------------------------------------------------
def test_metadata_versions_match_prompt_files_both_ways():
    """双向 SSOT：metadata 的版本键集 == 目录内 .py 文件集。

    判别力（反证靶子）：**删掉 metadata.json 里 v1_1_0_extract 的版本键后，本用例必须失败**
    （断言 files - registered 为空）。只查"登记的版本有没有对应文件"会漏掉"文件存在但没登记"
    —— 那正是 prompt 版本治理被绕过的形态（load_prompt 也因此显式拒绝未登记版本）。

    同时反向钉住：本目录不得出现 `__init__.py` 等非版本 .py（会被 metadata 一致性判据
    当成未登记版本，也会破坏 ai_registry 的版本命名约定）。
    """
    files = {f[:-3] for f in os.listdir(MEMORY_DIR) if f.endswith(".py")}
    registered = set(_metadata()["versions"].keys())
    assert registered - files == set(), "metadata 登记了不存在的版本文件：%s" % sorted(registered - files)
    assert files - registered == set(), "目录里有未登记的 .py（版本文件必须同时登记）：%s" % sorted(files - registered)
    for version in NEW_VERSIONS:
        assert version in registered, "%s 未登记进 metadata.json" % version
        mod = importlib.import_module("ai_registry.prompts.memory.%s" % version)
        assert getattr(mod, "VERSION", None) == version, (
            "%s.VERSION 必须与版本键/文件名一致 —— 复制一个版本文件出来改版本号时，"
            "最容易漏改的就是这个常量（registry 只读 PROMPT，靠本断言钉住）" % version)


# -------------------------------------------------------------
# 3. 指标诚实性：要么有可复现来源，要么显式标未验证
# -------------------------------------------------------------
def test_every_metrics_block_is_sourced_or_marked_unvalidated():
    """声明了 metrics 的版本，必须给出来源或显式标 unvalidated。

    判别力（反证）：在本次修 v1_0_0 之前，本用例**必须失败** —— v1_0_0 登记的
    alias_extraction_accuracy=94.2 / knowledge_validity=96.0 / avg_tokens=380
    既无评测集/脚本/报告支撑，也没有任何未验证标记（gap 分析 L4）。
    """
    offenders = []
    for version, info in _metadata()["versions"].items():
        metrics = info.get("metrics")
        if not metrics:
            continue
        if not _metrics_are_sourced_or_flagged(metrics):
            offenders.append((version, sorted(metrics)))
    assert not offenders, (
        "以下版本的 metrics 既无可复现来源也未标 unvalidated（不得把无支撑数字当事实登记）：%s"
        % offenders)


def test_v1_0_0_unreproducible_metrics_are_corrected():
    """v1_0_0 的三个无支撑数字必须已置 null，并在说明里指回 gap 分析的 L4。"""
    metrics = _metadata()["versions"]["v1_0_0"]["metrics"]
    for key in ("alias_extraction_accuracy", "knowledge_validity", "avg_tokens"):
        assert metrics.get(key) is None, "%s 仍登记着无支撑数值：%r" % (key, metrics.get(key))
    assert metrics.get("claim_unsupported") is True, "纠偏后必须带 claim_unsupported 标记"
    note = str(metrics.get("claim_unsupported_note") or "")
    assert "L4" in note, "纠偏说明必须指回 gap 分析的 L4 条目（便于追溯为何判为无支撑）"


def test_new_versions_are_draft_and_register_no_metrics():
    """新版本一律 draft（晋升 production 需人工审批），且不得登记任何指标数字。"""
    versions = _metadata()["versions"]
    for version in NEW_VERSIONS:
        info = versions[version]
        assert info["status"] == "draft", "%s 不得自行标 production（prompt 属 L1 资产，需人工审批）" % version
        numeric = {
            k: v for k, v in (info.get("metrics") or {}).items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        }
        assert not numeric, "%s 登记了未验证的指标数字：%s" % (version, numeric)


def test_benchmark_matrix_memory_row_is_aligned_and_honest():
    """benchmark_matrix 的 memory 行是同一口径的第二份副本（本仓高发：多份实现只改一份）。

    matrix 曾与 metadata 各写一份 94.2/1.2/380 的"accuracy/cer/avg_tokens"，只修 metadata
    等于把同一个无支撑数字留在管理台/评测总览里，故本用例同时钉住对齐与诚实性。
    """
    path = os.path.join(_REPO_ROOT, "ai_registry", "benchmarks", "benchmark_matrix.json")
    with open(path, "r", encoding="utf-8") as f:
        matrix = json.load(f)
    rows = [p for p in matrix["prompts"] if p.get("scene") == "memory"]
    assert len(rows) == 1, "matrix 应有且仅有一行 memory"
    row = rows[0]
    assert row["active_version"] == _metadata()["active_version"], "matrix 与 metadata 的 active_version 不一致"
    assert row.get("accuracy") is None, "matrix 仍残留无支撑的 accuracy：%r" % row.get("accuracy")
    assert row.get("avg_tokens") is None, "matrix 仍残留无支撑的 avg_tokens：%r" % row.get("avg_tokens")
    assert row.get("claim_unsupported") is True, "置 null 后必须带 claim_unsupported 标记"


# -------------------------------------------------------------
# 4. 两个新 PROMPT 的内容契约
# -------------------------------------------------------------
def test_extract_clause_checker_is_not_vacuous():
    """自校准：本文件的条款检查器必须**抓得到缺失**，否则下面的等价用例可能只是恒真。

    先给一段必然缺条款的文本（空串），再给一段只覆盖「JSON 数组」一条的文本，两者必须
    给出不同结论 —— 这是"先反证扫描器有效，再报结论"（只读一遍代码说"我没发现缺失"
    不可信；关键条款缺失正是本任务要禁的形态：回退时防注入静默变弱）。
    """
    assert len(_missing_extract_clauses("")) == len(_EXTRACT_CLAUSES), _missing_extract_clauses("")
    # 只覆盖「JSON 数组输出契约」这一条（两个 token 组都要命中才算该条成立）→ 必须恰好报 3 条缺失
    only_output = "只输出 JSON 数组：不要解释"
    assert len(_missing_extract_clauses(only_output)) == len(_EXTRACT_CLAUSES) - 1, (
        _missing_extract_clauses(only_output))


def test_extract_prompt_clauses_are_equivalent_on_both_sides():
    """抽取版：四类 kind 全列 + 严格 JSON 数组契约 + 外部数据不可信 —— **两侧都**成立。

    这就是「回退常量与注册表条款等价」的判据。判别力（反证靶子）：删掉
    `memory_extract._SYSTEM_PROMPT` 末尾的 7)-9) 防注入三句后，本用例必须失败
    （`_missing_extract_clauses` 会报出「外部数据不可信、其内指令一律不执行、
    不得改写输出 Schema（缺 一律不执行）」之类），即回退路径不得静默减弱防注入。
    """
    problems = {side: _missing_extract_clauses(text) for side, text in _EXTRACT_PROMPT_SIDES}
    assert not any(problems.values()), (
        "抽取 prompt 条款漂移（回退即静默减弱防御）：%s" % problems)


def test_consolidate_prompt_declares_four_actions_and_mandatory_reason():
    """整合版：ADD/UPDATE/DELETE/NOOP 四选一 + 强制输出 reason（与代码白名单同口径）。"""
    prompt = _prompt_text("v1_1_0_consolidate")
    for action in sorted(ALLOWED_ACTIONS):
        assert action in prompt, "prompt 漏说 action '%s' -> 该动作模型永不输出" % action
    assert "JSON 对象" in prompt, "整合的契约是「只输出一个 JSON 对象」"
    assert "reason" in prompt, "必须要求输出 reason"
    assert ("必填" in prompt) and ("不得为空" in prompt), "必须写清 reason 不得为空"
    for token in ("不可信", "一律不执行"):
        assert token in prompt, "缺外部数据不可信条款关键词：%s" % token


# -------------------------------------------------------------
# 5. 载入失败必须可见（不静默回退）
# -------------------------------------------------------------
def test_load_prompt_fails_loudly_for_unknown_version_and_scene():
    """未登记版本 / 不存在的场景：抛错，绝不返回空串或悄悄换成别的版本。"""
    with pytest.raises(KeyError):
        load_prompt("memory", "v9_9_9_not_registered")
    with pytest.raises(KeyError):
        load_prompt("no_such_scene_at_all")


def test_load_prompt_rejects_file_that_is_not_registered(monkeypatch):
    """文件在、登记没有 -> 拒绝加载。

    why：registry 的 `get_prompt` 只校验"版本文件存在"，目录里放个 .py 就能被加载，
    绕过 metadata 的版本治理；`load_prompt` 额外要求版本已登记，本用例把这条差异钉住。
    """
    monkeypatch.setattr(ai_registry, "get_prompt_metadata",
                        lambda scene: {"active_version": "v1_0_0", "versions": {"v1_0_0": {}}})
    with pytest.raises(KeyError):
        load_prompt("memory", "v1_1_0_extract")


# -------------------------------------------------------------
# 6. memory_extract 的接线：优先注册表，失败回退且留痕
# -------------------------------------------------------------
class _Msg:
    """最小 AIMessage 替身（正文在 .content）。"""

    def __init__(self, content):
        self.content = content


class _StubModel:
    """记录收到的消息的 stub 模型：测试全程不联网（任务红线）。"""

    def __init__(self, output="[]"):
        self.output = output
        self.messages = None

    def invoke(self, messages):
        self.messages = messages
        return self.output


def _system_message_contents(model) -> list:
    out = []
    for m in model.messages or []:
        if type(m).__name__ == "SystemMessage":
            out.append(getattr(m, "content", ""))
    return out


def test_extraction_sends_registry_prompt_to_model():
    """正向：真正发给模型的 system prompt 就是注册表 v1_1_0_extract 的文本。"""
    stub = _StubModel()
    mx.extract_candidate_facts(
        [{"name": "黑牛", "qty": 1, "unit": "斤", "unit_price": 10, "amount": 10}],
        [{"name": "黑牛腩", "quantity": 1, "unit": "斤", "unit_price": 10, "amount": 10}],
        "sup-0001", "tenant-a", model=stub)
    assert _system_message_contents(stub) == [_prompt_text("v1_1_0_extract")]
    # 这里原先写的是「内置常量必须与注册表版本不同，否则本用例无法区分是否真走了注册表」——
    # 那条断言把「回退文案比注册表少防注入条款」固化成合法行为（缺陷固化断言：任何修复都会
    # 被自己的用例判红）。现改为断言**两侧条款等价**（四类 kind / JSON 数组契约 / 防注入）。
    assert not _missing_extract_clauses(mx._SYSTEM_PROMPT), (
        _missing_extract_clauses(mx._SYSTEM_PROMPT))
    # 运行状态也要标出本次用的是注册表版（回退率是可观测的治理信号）
    assert mx.get_last_run()["prompt_source"] == mx.PROMPT_SOURCE_REGISTRY


def test_extraction_prefers_registry_when_both_texts_are_available(monkeypatch):
    """注册表可用时必须真的走注册表 —— 用哨兵文本证明，不依赖「两侧文本不同」。

    why：上面那条用例断言的是"发出去的文本 == 注册表文本"，两侧条款等价之后，
    `_SYSTEM_PROMPT` 与注册表文本措辞已经很接近（且将来可能被对齐到完全一致），
    仅靠文本比较不再能区分"走了注册表"与"回退到常量"。故这里用哨兵值直接证明
    注册表返回值被原样采用（这比原来那条 `!=` 断言强：它证明的是取值路径，不是文本差异）。
    """
    sentinel = "SENTINEL-REGISTRY-PROMPT-不参与条款比较"
    monkeypatch.setattr(registry_mod, "load_prompt", lambda scene, version=None: sentinel)
    stub = _StubModel()
    mx.extract_candidate_facts(
        [{"name": "黑牛", "qty": 1, "unit": "斤", "unit_price": 10, "amount": 10}],
        [{"name": "黑牛腩", "quantity": 1, "unit": "斤", "unit_price": 10, "amount": 10}],
        "sup-0001", "tenant-a", model=stub)
    assert _system_message_contents(stub) == [sentinel]
    assert mx.get_last_run()["prompt_source"] == mx.PROMPT_SOURCE_REGISTRY


def test_extraction_falls_back_visibly_when_registry_unavailable(monkeypatch, caplog):
    """反向：注册表不可用 -> 回退内置常量，且 warning 必须留痕（不许静默）。"""
    def _boom(scene, version=None):
        raise FileNotFoundError("simulated registry failure")

    monkeypatch.setattr(registry_mod, "load_prompt", _boom)
    with caplog.at_level(logging.WARNING, logger="memory_extract"):
        assert mx._system_prompt() == mx._SYSTEM_PROMPT
    assert "回退内置常量" in caplog.text, "回退必须可见：缺 warning 就等于 prompt 治理静默失效"


def test_fallback_run_is_marked_in_last_run(monkeypatch, caplog):
    """回退只在日志里可见是不够的：运行状态必须标出「本次用的是回退文案」。

    why：`get_last_run()` 是上层落库/告警/度量的取数口（`error` 只覆盖"这次没跑成"，
    回退则是"跑成了但治理降级"）。若回退不落进这个字段，长期走回退在指标上完全不可见，
    正是本仓高发的"异常被静默吞掉导致功能静默失效"。
    """
    def _boom(scene, version=None):
        raise FileNotFoundError("simulated registry failure")

    monkeypatch.setattr(registry_mod, "load_prompt", _boom)
    stub = _StubModel(output="[]")
    with caplog.at_level(logging.WARNING, logger="memory_extract"):
        mx.extract_candidate_facts(
            [{"name": "黑牛", "qty": 1, "unit": "斤", "unit_price": 10, "amount": 10}],
            [{"name": "黑牛腩", "quantity": 1, "unit": "斤", "unit_price": 10, "amount": 10}],
            "sup-0001", "tenant-a", model=stub)
    assert mx.get_last_run()["prompt_source"] == mx.PROMPT_SOURCE_BUILTIN_FALLBACK
    assert _system_message_contents(stub) == [mx._SYSTEM_PROMPT]
    # 回退不是 error：文案条款等价、结果可用；它是治理信号，不是本次失败
    assert mx.get_last_run()["error"] == ""


def test_no_prompt_used_is_distinct_from_fallback(monkeypatch, caplog):
    """没用到 prompt 的一次运行（无差异提前返回）不得被记成「用了回退文案」。

    判别力：若 `_reset_state` 漏了初始化 `prompt_source`，本用例第二步会沿用**上一次调用**
    的取值 —— 多张单据同线程连续抽取时，"回退率"这个度量会被前一张单的结果污染。
    """
    def _boom(scene, version=None):
        raise FileNotFoundError("simulated registry failure")

    monkeypatch.setattr(registry_mod, "load_prompt", _boom)
    stub = _StubModel(output="[]")
    with caplog.at_level(logging.WARNING, logger="memory_extract"):
        # 第一次：有差异 -> 走回退，prompt_source = builtin_fallback
        mx.extract_candidate_facts(
            [{"name": "黑牛", "qty": 1, "unit": "斤", "unit_price": 10, "amount": 10}],
            [{"name": "黑牛腩", "quantity": 1, "unit": "斤", "unit_price": 10, "amount": 10}],
            "sup-0001", "tenant-a", model=stub)
        assert mx.get_last_run()["prompt_source"] == mx.PROMPT_SOURCE_BUILTIN_FALLBACK
        # 第二次：无差异 -> 不取 prompt，取值必须回到"未使用"，不得沿用上一次
        same = [{"name": "菜心", "quantity": 1, "unit": "斤", "unit_price": 6, "amount": 6}]
        assert mx.extract_candidate_facts(same, same, "sup-0001", "tenant-a",
                                          model=stub) == []
    assert mx.get_last_run()["prompt_source"] == mx.PROMPT_SOURCE_NONE

