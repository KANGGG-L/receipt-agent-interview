# -*- coding: utf-8 -*-
"""P2b-Y3 首轮注入闭合：有 vendor_hint 时**第一轮**就完成「检索 → 注入」的端到端锁定。

why 单列这条用例（Gap H4 的最后一跳）：
  H4 原来的表述是「记忆几乎进不了 prompt」，它有三层根因 —— ①前端从不发 hint
  （P2d 已修：单张上传可选供应商 + 重试/重解析从单据行派生）；②无 hint 时首轮不检索
  （只有「首轮补检索」，而它跑在首轮模型调用**之后**，先验只能喂给重试轮）；③hint 通道
  虽有检索，却**没有任何用例证明检索到的内容真的进了第一轮的 prompt**。
  既有用例 `demo/tests/test_memory_tenant_retrieval.py` 只能证明「检索被调用过」——
  它把检索打成返回空串，因此「调用发生了」与「内容进 prompt 了」是两件事；它断言的是
  函数返回值与调用参数，不是模型真正收到的东西。本文件把最后一跳钉死。

判据全部取**模型侧可观测证据**（不用函数返回值）：
  1. 假引擎记录每一次 `model.invoke(prompt)` 的 **prompt 快照**与 **调用次数**；
  2. 正向：hint 非空且该供应商有可用记忆 → `attempt == 1`、`model.calls == 1`、管线成功，
     且**第一次** prompt 里含有只可能来自记忆的哨兵文本（`Y3哨兵菜心` /
     `版式：ncr_handwritten`），并经 XML 数据沙箱注入（`<vendor_context data_only="true"`）；
  3. 反向（证明正向不是夹具自带）：把 hint 换成「没有任何记忆的供应商」→ 第一次 prompt
     不得含哨兵；完全不给 hint 时同样不得含哨兵；
  4. 租户对照：记忆写在 t2、hint 在 default 读 → 第一次 prompt 不得含哨兵。
  第 2 条同时断言「检索确实发生在首轮」（hint 通道被调 1 次、入参就是该 hint）——
  否则「prompt 里有哨兵」可能来自别的通道。

反证口径（实测记录写在交付报告里，不写进用例）：把 extract_chain 的 hint 通道检索
去掉（`if vendor_hint:` → `if False:`）→ 第 2 条用例必须失败。

全程离线：临时 DB + 临时 RAG_DIR + 假引擎，零网络、零真实引擎调用、零 live 库/向量库写入。
"""

import json
import os
import sys

import pytest
from PIL import Image

# 导入期自保（H3 形态）：`app/db.py` 在模块底部就 `_make_engine()`（import 即执行迁移块），
# 缺 DB_PATH 时会直接对 live 库做 ALTER。这里在 import app.* 之前兜住两个环境变量，
# 使「忘了 export 就单跑本文件」也不会碰到 live 库与 live 向量库。
os.environ.setdefault("DB_PATH", "/tmp/p2b_y3_first_round.db")
os.environ.setdefault("RAG_DIR", "/tmp/p2b_y3_first_round_rag")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app.chains import extract_chain, supervisor  # noqa: E402
from app.models import EngineConfig  # noqa: E402
from app.services import rag  # noqa: E402


# 记忆语料里的哨兵：只可能来自本次种下的记忆行，不可能是 hint 文本、图片或系统 prompt 的内容
SENTINEL_ITEM = "Y3哨兵菜心"
SENTINEL_NOTES = "版式：ncr_handwritten"
# 过沙箱后仍可判定的形态：PromptInjectionGuardTool 会做归一（实测全角「：」变半角），
# 故断言用不含全角标点的子串，避免把「沙箱做了归一」误判成「记忆没注入」。
SENTINEL_LAYOUT = "ncr_handwritten"
HINT_VENDOR = "德利行"
BARE_VENDOR = "Y3無記憶商號"


def _valid_json() -> str:
    """一份能干净通过契约与算术门禁的识别结果（明细合计 == 总额）。"""
    return json.dumps({
        "vendor": HINT_VENDOR,
        "date": "2026-01-01",
        "doc_form": "ncr_handwritten",
        "payment_marked": False,
        "confidence": 0.9,
        "items": [{"name": "菜心", "qty": 2.0, "unit": "斤",
                   "unit_price": 5.0, "amount": 10.0}],
        "total": 10.0,
    })


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """每例独立临时库（本文件要往 vendor_memory 写记忆，绝不碰 live 库）。"""
    old_path = db.DB_PATH
    test_db_path = str(tmp_path / "p2b_y3_first_round.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    db.DB_PATH = test_db_path
    db._make_engine()
    yield
    db.DB_PATH = old_path
    db._make_engine()


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    """离线化：关 canary、关引擎兜底、记忆日志改道、审核腿打桩（只关心首轮注入）。"""
    monkeypatch.setattr(supervisor, "_memory_log_path",
                        lambda: str(tmp_path / "parse_log.jsonl"))
    monkeypatch.setattr(extract_chain, "generate_canary_token", lambda: "")
    monkeypatch.setattr(supervisor, "_run_audit",
                        lambda *a, **k: {"skipped": True, "reason": "test"})


class _RecordingModel:
    """记录每次 invoke 收到的 prompt 快照与调用次数的假引擎。

    why 记 prompt 而不是只看返回值：本文件要证的是「记忆进了模型**实际收到**的输入」，
    函数返回值（`result["vendor_context"]`）只说明管线组装过什么，不说明真正发给了模型。
    """

    kind = "openai"

    def __init__(self, output=""):
        self.calls = 0
        self.prompts = []
        self._output = output

    def invoke(self, prompt):
        self.calls += 1
        self.prompts.append(prompt)
        return self._output


def _prompt_text(prompt) -> str:
    """把 messages 里的全部文本拼成一个串（image_url 块按类型跳过）。"""
    parts = []

    def _walk(content):
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, (list, tuple)):
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    text = block.get("text")
                    if isinstance(text, str):
                        parts.append(text)

    for msg in prompt or []:
        _walk(getattr(msg, "content", msg))
    return "\n".join(parts)


def _valid_png(tmp_path):
    p = str(tmp_path / "ok.png")
    Image.new("RGB", (40, 20), (7, 7, 7)).save(p, format="PNG")
    return p


def _install_model(monkeypatch, model):
    """把假引擎装进识别腿（supervisor 内部自己 build，必须在这里截住）。"""
    monkeypatch.setattr(supervisor, "build_recognition_model", lambda *a, **k: model)


def _count_hint_retrievals(monkeypatch):
    """计数 hint 通道的真实检索调用（透传真实现，不改任何返回值）。"""
    seen = []
    real = extract_chain.retrieve_context

    def _spy(vendor, *args, **kwargs):
        seen.append((vendor, kwargs.get("tenant_id")))
        return real(vendor, *args, **kwargs)

    monkeypatch.setattr(extract_chain, "retrieve_context", _spy)
    return seen


def _seed_memory(vendor=HINT_VENDOR, tenant_id="default"):
    """种一条「可用记忆」：active、带内容、decay 满分（走 ingest_memory 生产写入口）。"""
    rag.ingest_memory(vendor, f"1. {SENTINEL_ITEM} 2斤 @5 = 10",
                      notes=SENTINEL_NOTES, tenant_id=tenant_id)


def _run(tmp_path, hint="", tenant_id="default", max_rounds=2):
    return supervisor.run_pipeline(
        _valid_png(tmp_path), vendor_hint=hint, tenant_id=tenant_id,
        config=EngineConfig(max_retry_rounds=max_rounds))


# -------------------------------------------------------------
# 1. 正向：首轮就注入（本文件的核心判据）
# -------------------------------------------------------------
def test_first_round_prompt_carries_vendor_memory(tmp_path, monkeypatch):
    """hint 非空 + 该供应商有可用记忆 → 第一轮 prompt 里就有记忆正文。"""
    _seed_memory()
    model = _RecordingModel(output=_valid_json())
    _install_model(monkeypatch, model)
    retrievals = _count_hint_retrievals(monkeypatch)

    state = _run(tmp_path, hint=HINT_VENDOR)

    # 首轮即成功：只调了一次模型、attempt 仍是 1 —— 所以下面断言的是**第一轮**的输入，
    # 不是「重试轮才吃到的先验」（H4 的旧形态）
    assert model.calls == 1, f"首轮应即成功，实际 invoke={model.calls} 次"
    assert state["attempt"] == 1, state.get("attempt")
    assert state["data"] is not None, "夹具本身应能通过门禁"

    # 检索确实发生在首轮，且用的就是该 hint（排除「哨兵来自别的通道」）
    assert retrievals == [(HINT_VENDOR, "default")], retrievals

    first_prompt = _prompt_text(model.prompts[0])
    assert SENTINEL_LAYOUT in first_prompt, \
        f"第一轮 prompt 未含记忆的版式先验（前 400 字）：{first_prompt[:400]!r}"
    assert SENTINEL_ITEM in first_prompt, \
        "第一轮 prompt 未含记忆的明细正文：记忆被检索却未注入模型输入"
    assert '<vendor_context data_only="true"' in first_prompt, \
        "注入必须先过 XML 数据沙箱（Gap 8 的数据边界对新载荷同样生效）"


# -------------------------------------------------------------
# 2. 反向对照：哨兵只可能来自记忆，不是夹具/系统 prompt 自带的
# -------------------------------------------------------------
def test_first_round_without_memory_has_no_sentinel(tmp_path, monkeypatch):
    """hint 指向一个没有任何记忆的供应商 → 第一轮 prompt 不得含哨兵。"""
    _seed_memory()  # 记忆只给 德利行；本用例 hint 是另一个供应商
    model = _RecordingModel(output=_valid_json())
    _install_model(monkeypatch, model)

    state = _run(tmp_path, hint=BARE_VENDOR)

    assert model.calls == 1 and state["attempt"] == 1
    first_prompt = _prompt_text(model.prompts[0])
    assert SENTINEL_ITEM not in first_prompt, \
        "无记忆的供应商拿到了别家的记忆正文（越权召回）"
    # 如实记录本仓现状：hint 非空但检索为空时，**裸 hint 名**仍会被当成"先验"注入
    # （`effective_hint = vendor_ctx or vendor_hint`）。这不是本用例要断言的行为，
    # 但必须写出来，因为它正是「rag_context_json 非空 ≠ 真的注入了记忆」的原因。
    assert BARE_VENDOR in first_prompt, first_prompt[:400]


def test_first_round_without_hint_has_no_sentinel(tmp_path, monkeypatch):
    """完全不给 hint → 首轮 prompt 不得含哨兵（首轮补检索拿到先验时首轮已经跑完）。"""
    _seed_memory()
    model = _RecordingModel(output=_valid_json())
    _install_model(monkeypatch, model)

    state = _run(tmp_path, hint="")

    assert model.calls == 1 and state["attempt"] == 1
    first_prompt = _prompt_text(model.prompts[0])
    assert SENTINEL_ITEM not in first_prompt, \
        "无 hint 时首轮不应有记忆正文（H4：先验只能喂给重试轮）"
    assert SENTINEL_LAYOUT not in first_prompt, first_prompt[:400]


# -------------------------------------------------------------
# 3. 租户对照：首轮注入仍受租户硬门约束
# -------------------------------------------------------------
def test_first_round_injection_is_tenant_scoped(tmp_path, monkeypatch):
    """记忆写在 t2、识别在 default → 第一轮 prompt 不得含哨兵。"""
    _seed_memory(tenant_id="t2")
    model = _RecordingModel(output=_valid_json())
    _install_model(monkeypatch, model)

    state = _run(tmp_path, hint=HINT_VENDOR, tenant_id="default")

    assert model.calls == 1 and state["attempt"] == 1
    first_prompt = _prompt_text(model.prompts[0])
    assert SENTINEL_ITEM not in first_prompt, "跨租户记忆被首轮注入了"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
