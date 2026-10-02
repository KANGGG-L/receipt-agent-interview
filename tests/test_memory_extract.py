# -*- coding: utf-8 -*-
"""LLM 抽取（Extract）的契约测试（计划 P3-T2 / Stream E · E2）。

覆盖：
1. diff 正确性：AI 与用户相同的字段不产出候选；只改一个字段时只产出该字段；
   **AI 侧 `qty` 与用户侧 `quantity` 视为同一字段**（键名口径不一致会让每行都产生
   假差异，这是本仓高发的"同一语义多份口径"形态，必须有断言钉住）；
2. stub 模型返回合法 JSON 数组 → 得到预期 fact（kind/key/value/scope/trust/source_kind）；
3. stub 返回非法 kind → 被 `validate_fact` 丢弃且有计数与可读理由；
4. stub 抛异常 → 返回 [] 且不向上抛，错误摘要留给上层；
5. 无差异 → 不调用模型（用调用计数断言）；
附：模型输出非 JSON / 超出 max_facts 截断 / 缺 scope 不调模型 / prompt 覆盖全部 kind。

判别力（反证）：`test_invalid_kind_is_dropped_and_counted` 是判别式用例 —— 把
`memory_extract._to_facts` 里的 `validate_fact` 过滤去掉（改为无条件 accepted）后，
本用例必须失败（非法候选会一起进结果，accepted 变 2、rejected 变 0）。

环境隔离：本模块不碰 DB / 向量库，也不联网（模型一律注入 stub）；仍按同波次约定把
DB_PATH / RAG_DIR 指向临时目录，避免将来误加 import 时把产物写到 live。
"""

import ast
import json
import os
import pathlib
import re
import sys
import tempfile

# 隔离环境：临时 DB / 向量目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="memory_extract_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "memory_extract.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")

_DEMO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo")
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import pytest  # noqa: E402

import app.services.memory_extract as mx  # noqa: E402
from app.services.memory_facts import ALLOWED_KINDS  # noqa: E402

SUPPLIER = "sup-0001"
TENANT = "tenant-a"


class _Msg:
    """最小 AIMessage 替身：真实通道的正文在 `.content`（同时覆盖 str 分支之外的路径）。"""

    def __init__(self, content):
        self.content = content


class _StubModel:
    """记录调用次数的 stub 模型：测试全程不联网（任务红线）。"""

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
        return _with_canary_handshake(self.output, messages)


# 生产链路的 canary 握手是 fail-closed 的：真实模型会按锚定指令回填 __guard_token，
# stub 也必须按同一协议回填（数组 → 末尾握手对象；对象 → 根字段），否则合规输出会被
# 误判成协议错配。fail-closed 的反向用例（缺失/错配 → 拒绝）见
# tests/test_memory_write_side_guard.py。
_CANARY_TOKEN_RE = re.compile(r'"__guard_token"\s*:\s*"([^"]+)"')


def _with_canary_handshake(output, messages):
    # stub 的 output 可能是 _Msg 包装（正文在 .content）也可能是裸 str；echo 后统一
    # 返回 str（两个模块的 _message_text 都接受 str 结果）。
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


def _messages_text(model):
    """把 stub 收到的消息拼成一段文本，便于断言"只把差异发给了模型"。"""
    parts = []
    for m in model.messages or []:
        content = getattr(m, "content", "")
        parts.append(content if isinstance(content, str) else str(content))
    return "\n".join(parts)


def _ai_items():
    """AI 明细：注意数量键是 `qty`（ReceiptData 契约口径）。"""
    return [
        {"name": "黑牛", "qty": 2, "unit": "斤", "unit_price": 8.0, "amount": 16.0},
        {"name": "菜心", "qty": 10, "unit": "斤", "unit_price": 6.0, "amount": 60.0},
    ]


def _user_items():
    """用户最终明细：数量键是 `quantity`（receipt_items 口径）；只改了第 0 行品名。"""
    return [
        {"name": "黑牛牌黑胡椒牛油", "quantity": 2, "unit": "斤",
         "unit_price": 8.0, "amount": 16.0},
        {"name": "菜心", "quantity": 10, "unit": "斤", "unit_price": 6.0, "amount": 60.0},
    ]


def _valid_payload():
    return [
        {"kind": "alias", "fact_key": "黑牛", "value": "黑牛牌黑胡椒牛油", "confidence": 0.7},
        {"kind": "unit", "fact_key": "菜心", "value": "斤"},
    ]


# -------------------------------------------------------------
# 1. 纯代码 diff 正确性
# -------------------------------------------------------------
def test_identical_items_produce_no_diff():
    items = _user_items()
    assert mx.compute_field_diffs(items, items) == []


def test_only_changed_field_is_returned():
    diffs = mx.compute_field_diffs(_ai_items(), _user_items())
    assert diffs == [{
        "row": 0, "field": "name", "ai": "黑牛", "user": "黑牛牌黑胡椒牛油",
    }], diffs


def test_qty_and_quantity_are_the_same_field():
    """判别式：把 `_FIELD_ALIASES` 里 quantity 的键名只写成 ("quantity",) → 本用例失败。

    AI 侧 `qty` 取不到值 → 每行都产出 {"field": "quantity", "ai": "", "user": "2"} 假差异。
    """
    ai = _ai_items()
    user = _user_items()
    diffs = mx.compute_field_diffs(ai, user)
    assert [d["field"] for d in diffs] == ["name"], diffs

    # 数值等价（2 vs "2.0"、int vs float）不算修正
    diffs2 = mx.compute_field_diffs(
        [{"name": "菜心", "qty": 2, "unit": "斤", "unit_price": 6, "amount": 12}],
        [{"name": "菜心", "quantity": "2.0", "unit": "斤",
          "unit_price": 6.00, "amount": 12.0}],
    )
    assert diffs2 == [], diffs2


def test_diff_covers_all_fields_and_detects_each_one():
    """五个字段各自改一处都能量到（防止"加了字段忘了进别名表"）。

    基准行先取"与 AI 完全一致"的用户明细（含键名差异），再只改目标字段一处。
    """
    base = [dict(r) for r in _user_items()]
    base[0]["name"] = "黑牛"          # 与 AI 一致，基准态无差异
    assert mx.compute_field_diffs(_ai_items(), base) == []

    for field, changed in (("name", "菜心(改)"), ("unit", "kg"),
                           ("quantity", 11), ("unit_price", 7.0), ("amount", 70.0)):
        rows = [dict(r) for r in base]
        key = "quantity" if field == "quantity" else field
        rows[1][key] = changed
        diffs = mx.compute_field_diffs(_ai_items(), rows)
        assert [d["field"] for d in diffs] == [field], (field, diffs)
        assert diffs[0]["row"] == 1, diffs
    assert set(mx.DIFF_FIELDS) == {"name", "unit", "quantity", "unit_price", "amount"}


# -------------------------------------------------------------
# 2. 合法 JSON → 预期 fact
# -------------------------------------------------------------
def test_valid_json_yields_expected_facts():
    stub = _StubModel(output=_Msg(json.dumps(_valid_payload(), ensure_ascii=False)))
    facts = mx.extract_candidate_facts(_ai_items(), _user_items(),
                                       SUPPLIER, TENANT, model=stub)

    assert stub.calls == 1
    assert len(facts) == 2
    alias = facts[0]
    assert alias.kind == "alias"
    assert alias.fact_key == "黑牛"
    assert alias.value == "黑牛牌黑胡椒牛油"
    assert alias.supplier_id == SUPPLIER
    assert alias.tenant_id == TENANT
    assert alias.source_kind == "extract"
    assert alias.trust_level == 0.7          # 取自模型 confidence
    assert alias.written_by == mx.WRITTEN_BY
    assert alias.status == "active"
    assert facts[1].trust_level == mx.DEFAULT_EXTRACT_TRUST  # 缺 confidence → 缺省信任分

    run = mx.get_last_run()
    # 逐键严格比对（不用子集断言）：漏键/多键都要当场暴露。
    # `prompt_source` 是本次新增的治理信号字段（注册表可用 -> registry），
    # 取值常量定义在 mx 上，避免字面量在两处抄写。
    assert run == {"model_called": True, "diff_count": 1, "candidate_count": 2,
                   "accepted": 2, "rejected": 0, "truncated": 0,
                   "reject_reasons": [], "prompt_source": mx.PROMPT_SOURCE_REGISTRY,
                   "error": ""}, run
    assert mx.get_last_error() == ""


def test_only_diffs_are_sent_to_model():
    """只把差异交给模型，不是整张单据（未改动的那行不得出现在请求里）。"""
    stub = _StubModel(output=_Msg("[]"))
    mx.extract_candidate_facts(_ai_items(), _user_items(),
                               SUPPLIER, TENANT, model=stub)
    text = _messages_text(stub)
    # W-SEC-1：payload 经 XML 数据沙箱包裹后进 HumanMessage，JSON 的双引号被守卫
    # 实体转义（&quot;）—— 这正是"数据进了沙箱、不再是裸拼"的可观测证据。
    assert "&quot;field&quot;: &quot;name&quot;" in text, text
    assert "memory_extract_diff" in text, "payload 必须在沙箱标签内"
    assert "黑牛牌黑胡椒牛油" in text
    assert "菜心" not in text, "未改动的行不应发给模型"
    assert SUPPLIER in text


def test_confidence_out_of_range_falls_back_but_fact_survives():
    """confidence 越界只影响信任分，不因此丢掉一条合法先验。"""
    stub = _StubModel(output=_Msg(json.dumps(
        [{"kind": "alias", "fact_key": "黑牛", "value": "黑牛牌黑胡椒牛油",
          "confidence": 1.5}], ensure_ascii=False)))
    facts = mx.extract_candidate_facts(_ai_items(), _user_items(),
                                       SUPPLIER, TENANT, model=stub)
    assert len(facts) == 1
    assert facts[0].trust_level == mx.DEFAULT_EXTRACT_TRUST


# -------------------------------------------------------------
# 3. 非法候选被丢弃且计数
# -------------------------------------------------------------
def test_invalid_kind_is_dropped_and_counted():
    """判别式：去掉 `_to_facts` 里的 validate_fact 过滤 → 本用例必须失败。"""
    payload = _valid_payload() + [
        {"kind": "price", "fact_key": "菜心", "value": "6.0", "confidence": 0.9},
    ]
    stub = _StubModel(output=_Msg(json.dumps(payload, ensure_ascii=False)))
    facts = mx.extract_candidate_facts(_ai_items(), _user_items(),
                                       SUPPLIER, TENANT, model=stub)

    assert [f.kind for f in facts] == ["alias", "unit"], [f.kind for f in facts]
    run = mx.get_last_run()
    assert run["candidate_count"] == 3
    assert run["accepted"] == 2
    assert run["rejected"] == 1
    assert len(run["reject_reasons"]) == 1
    assert "kind" in run["reject_reasons"][0] and "price" in run["reject_reasons"][0]


def test_non_mapping_candidate_is_counted_as_rejected():
    """非对象候选（模型把数组写成字符串）也不得静默放过。"""
    stub = _StubModel(output=_Msg(json.dumps(
        [{"kind": "alias", "fact_key": "黑牛", "value": "黑牛牌黑胡椒牛油"}, "黑牛"],
        ensure_ascii=False)))
    facts = mx.extract_candidate_facts(_ai_items(), _user_items(),
                                       SUPPLIER, TENANT, model=stub)
    assert len(facts) == 1
    assert mx.get_last_run()["rejected"] == 1


# -------------------------------------------------------------
# 4. 异常与解析失败：返回 []、不抛、留痕
# -------------------------------------------------------------
def test_model_exception_returns_empty_and_records_error():
    stub = _StubModel(exc=RuntimeError("引擎 500"))
    facts = mx.extract_candidate_facts(_ai_items(), _user_items(),
                                       SUPPLIER, TENANT, model=stub)
    assert facts == []
    assert stub.calls == 1
    err = mx.get_last_error()
    assert "RuntimeError" in err and "引擎 500" in err, err
    assert mx.get_last_run()["accepted"] == 0


@pytest.mark.parametrize("bad_output", [
    "这不是 JSON",
    '{"facts": []}',          # 契约是数组，dict 包装不接受（放宽会让"格式错"无法告警）
    "",                       # 空输出
])
def test_unparseable_output_returns_empty_and_records_error(bad_output):
    stub = _StubModel(output=bad_output)   # 同时覆盖 str 返回分支
    facts = mx.extract_candidate_facts(_ai_items(), _user_items(),
                                       SUPPLIER, TENANT, model=stub)
    assert facts == []
    assert mx.get_last_run()["rejected"] == 0
    assert "JSON" in mx.get_last_error()


def test_no_scope_skips_model_call():
    """缺 scope 必然过不了 validate_fact：不白付一次模型调用，但必须留痕。"""
    stub = _StubModel(output=_Msg(json.dumps(_valid_payload(), ensure_ascii=False)))
    assert mx.extract_candidate_facts(_ai_items(), _user_items(), "", TENANT,
                                      model=stub) == []
    assert mx.extract_candidate_facts(_ai_items(), _user_items(), SUPPLIER, "",
                                      model=stub) == []
    assert stub.calls == 0
    assert "supplier_id" in mx.get_last_error()


# -------------------------------------------------------------
# 5. 无差异 → 不调用模型
# -------------------------------------------------------------
def test_no_diff_does_not_call_model():
    stub = _StubModel(output=_Msg(json.dumps(_valid_payload(), ensure_ascii=False)))
    same = _user_items()
    facts = mx.extract_candidate_facts(same, same, SUPPLIER, TENANT, model=stub)
    assert facts == []
    assert stub.calls == 0
    run = mx.get_last_run()
    assert run["model_called"] is False
    assert run["diff_count"] == 0
    assert mx.get_last_error() == ""


# -------------------------------------------------------------
# 6. max_facts 截断 + prompt 覆盖全部 kind
# -------------------------------------------------------------
def test_max_facts_truncates_with_count():
    stub = _StubModel(output=_Msg(json.dumps(_valid_payload(), ensure_ascii=False)))
    facts = mx.extract_candidate_facts(_ai_items(), _user_items(),
                                       SUPPLIER, TENANT, model=stub, max_facts=1)
    assert len(facts) == 1
    run = mx.get_last_run()
    assert run["accepted"] == 1
    assert run["truncated"] == 1
    assert run["rejected"] == 0


def test_prompt_documents_every_allowed_kind():
    """prompt 少说一类 → 该类永远抽不出来（静默失效）。"""
    assert set(mx._KIND_HINTS) == ALLOWED_KINDS
    for kind in sorted(ALLOWED_KINDS):
        assert kind in mx._SYSTEM_PROMPT, kind
        assert mx._KIND_HINTS[kind] in mx._SYSTEM_PROMPT


def test_diff_field_set_matches_review_diff_constant():
    """跨模块漂移守卫（本仓高发缺陷类 ①：同一根因多份实现，只改一份）。

    "AI 明细 vs 用户明细比哪些字段"在本仓有两处声明：本模块的 `_FIELD_ALIASES`（要出
    ai/user 值，供 LLM 判断）与 `receipt_utils._DIFF_FIELDS`（只出计数，供 FER 埋点）。
    两者字段集合必须一致：任一处新增字段而不补另一处，抽取器就会静默漏掉该字段的修正
    （或反过来让埋点漏统计）。

    附注（本次实测到的既有缺陷，不在本任务范围）：`receipt_utils.compute_review_diff`
    读 AI 侧用的是 `quantity`，而 `ai_prefill_json` 的 items 实际键名是 `qty`
    （ReceiptData 契约，live 已核对），于是未修改的单据也会被计成
    `field_mod_counts={'quantity': 1}`、`rows_modified=1`（FER 分子被虚假抬高）。
    本模块用 `_FIELD_ALIASES` 同时接受两套键名，故不受影响；该缺陷属 Stream A 的
    `receipt_utils.py`，留待主 agent 决定处置。
    """
    from app.services.receipt_utils import _DIFF_FIELDS
    assert set(mx.DIFF_FIELDS) == set(_DIFF_FIELDS), (mx.DIFF_FIELDS, _DIFF_FIELDS)


def test_app_llm_import_is_lazy():
    """模型构建必须惰性 import：否则 import 本模块（及测试）就会拉起引擎层。"""
    tree = ast.parse(pathlib.Path(mx.__file__).read_text(encoding="utf-8"))
    module_level = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            module_level.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            module_level.add(node.module or "")
    assert "app.llm" not in module_level, module_level


def test_production_path_builds_model_via_audit_leg(monkeypatch):
    """model=None 时确实调用 `app.llm.build_audit_model`（防止生产路径是死代码）。

    选审核腿的理由见模块 docstring：`_build(side="aud")` 把 temperature 强制为 0.0
    （llm.py:761-763 的 Gap A3/A4 约定）→ 满足计划 §7「可复算（给定输入 → 稳定输出）」。
    此处在 `app.llm` 上打桩，不联网、不构造真实客户端。
    """
    import app.llm as llm

    captured = {}
    stub = _StubModel(output=_Msg(json.dumps(_valid_payload(), ensure_ascii=False)))

    def _fake_build_audit_model(model_name=None, cfg=None, use_grey=False):
        captured["cfg"] = cfg
        captured["use_grey"] = use_grey
        return stub

    monkeypatch.setattr(llm, "build_audit_model", _fake_build_audit_model)
    facts = mx.extract_candidate_facts(_ai_items(), _user_items(), SUPPLIER, TENANT,
                                       cfg="CFG-SENTINEL")
    assert captured == {"cfg": "CFG-SENTINEL", "use_grey": False}, captured
    assert stub.calls == 1
    assert len(facts) == 2
