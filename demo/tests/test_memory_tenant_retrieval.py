# -*- coding: utf-8 -*-
"""P0-T1 锁定：VendorMemory 读侧租户透传（Gap H1：读写租户口径分叉）。

why: 修复前 `retrieve_context` 的 tenant_id 缺省 "default"，而两个生产读点
（`extract_chain` 的 hint 通道、`supervisor` 的首轮补检索）都不传 —— 写侧传的是
真实租户（`api_receipts` approve 回写），读侧恒落 default。后果是双向的：
写进 t2 的记忆在识别时读不到；default 桶的记忆会被注入给任意租户。

本文件锁住四件事（全程离线：临时 DB + 临时 RAG_DIR + 假引擎，零网络、
零 live 库/向量库写入）：

1. `retrieve_context` 的 tenant_id 已**必填** —— 漏传直接 TypeError，
   而不是静默读 default（H1 的根因就是这个静默默认值）。
2. `run_pipeline` 的新形参真的流到 `_run_extract`（防「定义了没接线」）。
3. 两个检索点（hint 通道 / 首轮补检索）确实带着该租户调用 `retrieve_context`。
4. 读写同租户读得到、跨租户读不到（含正向对照，排除「数据本来就不存在」）。

反证口径（交付说明里给实测记录，不写进用例）：把任一处透传改回「不传」→ 对应用例
必须失败；把 tenant_id 的必填改回带默认值 → 必填用例必须失败。
"""

import os
import sys

import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app.chains import extract_chain, supervisor  # noqa: E402
from app.models import DocForm, EngineConfig, ReceiptData, ReceiptItem  # noqa: E402
from app.services import rag  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """显式隔离本文件写入的库。

    与 conftest 的 autouse 兜底重复是刻意的：本文件要往 vendor_memory 写记忆，
    自隔离可保证「换个 cwd / 兜底被绕过」时也不会碰到 live 库。
    """
    old_path = db.DB_PATH
    test_db_path = str(tmp_path / "p0t1_tenant_retrieval.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    db.DB_PATH = test_db_path
    db._make_engine()
    yield
    db.DB_PATH = old_path
    db._make_engine()


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    """离线化：假引擎兜底关闭、canary 关闭、记忆日志改道临时目录。

    记忆日志的缺省落点是仓库内 artifacts/memory/parse_log.jsonl，跑测试不该写它。
    """
    monkeypatch.setattr(supervisor, "_memory_log_path",
                        lambda: str(tmp_path / "parse_log.jsonl"))
    monkeypatch.setattr(extract_chain, "generate_canary_token", lambda: "")
    monkeypatch.setattr(extract_chain, "_dashscope_fallback_available",
                        lambda *a, **k: False)
    monkeypatch.setattr(supervisor, "_run_audit",
                        lambda *a, **k: {"skipped": True, "reason": "test"})


class _StubModel:
    """最小假引擎：返回不可解析文本，只为让管线走到检索点后干净收口。"""

    kind = "openai"

    def invoke(self, prompt):
        return "不是 JSON"


def _valid_png(tmp_path):
    p = str(tmp_path / "ok.png")
    Image.new("RGB", (40, 20), (7, 7, 7)).save(p, format="PNG")
    return p


def _fake_extract_payload(data):
    """`_run_extract` 的最小返回结构（keys 与 run_pipeline 的消费点一致）。"""
    return {
        "raw": '{"vendor": "德利行"}',
        "elapsed_ms": 1,
        "error": "" if data is not None else "boom",
        "extract_ms": 1.0,
        "rag_ms": 0.0,
        "parse_llm": {},
        "token_usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        "cost_hkd": 0.0,
        "engine": "fake-engine",
        "data": data,
        "vendor_context": "",
    }


# -------------------------------------------------------------
# 1. tenant_id 必填：漏传必须炸，不再静默落 default
# -------------------------------------------------------------
def test_retrieve_context_tenant_id_is_required():
    with pytest.raises(TypeError):
        rag.retrieve_context("德利行")


# -------------------------------------------------------------
# 2. 读写同租户读得到、跨租户读不到（生产调用形态）
# -------------------------------------------------------------
def test_tenant_memory_readable_only_from_its_own_tenant():
    # 写侧：t2 记德利行，default 记祥興（两个无共享字根的供应商名，排除相关性门干扰）
    rag.ingest_memory("德利行", "1. 菜心 2斤 @10 = 20",
                      notes="版式：ncr_handwritten", tenant_id="t2")
    rag.ingest_memory("祥興", "1. 白菜 1斤 @5 = 5",
                      notes="版式：printed", tenant_id="default")

    # 生产调用形态：extract_chain / supervisor 现在就是这样调的
    ctx_t2 = rag.retrieve_context("德利行", tenant_id="t2")
    assert "菜心" in ctx_t2, ctx_t2
    assert "白菜" not in ctx_t2, "t2 视角混入了 default 桶的内容"

    # 正向对照：祥興的记忆确实在 default 桶里 —— 证明下面的「读不到」是租户隔离，
    # 而不是「这条记忆压根没写进去 / 供应商名对不上」
    ctx_default = rag.retrieve_context("祥興", tenant_id="default")
    assert "白菜" in ctx_default, ctx_default

    # 跨租户不可见（双向）：default 桶的记忆在 t2 视角读不到；t2 的记忆在 default 视角也读不到
    assert rag.retrieve_context("祥興", tenant_id="t2").strip() == ""
    assert rag.retrieve_context("德利行", tenant_id="default").strip() == ""


# -------------------------------------------------------------
# 3. run_pipeline 的 tenant_id 真的流到 _run_extract（防「定义了没接线」）
# -------------------------------------------------------------
def test_pipeline_passes_tenant_to_run_extract(tmp_path, monkeypatch):
    seen = {}

    def _recording_run_extract(*args, **kwargs):
        seen.update(kwargs)
        return _fake_extract_payload(None)

    monkeypatch.setattr(supervisor, "_run_extract", _recording_run_extract)
    supervisor.run_pipeline(_valid_png(tmp_path), tenant_id="t2",
                            config=EngineConfig(max_retry_rounds=1))
    assert seen.get("tenant_id") == "t2", seen


# -------------------------------------------------------------
# 4. hint 通道（extract_chain）与首轮补检索（supervisor）都带租户
# -------------------------------------------------------------
def test_hint_channel_retrieval_carries_tenant(tmp_path, monkeypatch):
    """走完整链路：run_pipeline → _run_extract → extract_receipt → retrieve_context。"""
    seen = []
    monkeypatch.setattr(extract_chain, "retrieve_context",
                        lambda vendor, **kw: seen.append(
                            (vendor, kw.get("tenant_id"))) or "")
    monkeypatch.setattr(supervisor, "build_recognition_model",
                        lambda *a, **k: _StubModel())

    supervisor.run_pipeline(_valid_png(tmp_path), vendor_hint="德利行",
                            tenant_id="t2", config=EngineConfig(max_retry_rounds=1))

    assert seen, "hint 通道未触发检索，用例前提不成立"
    assert seen[0][0] == "德利行", seen
    assert [t for _, t in seen] == ["t2"] * len(seen), seen


def test_backfill_retrieval_carries_tenant(tmp_path, monkeypatch):
    """无 hint 时的首轮补检索同样带租户。"""
    data = ReceiptData(
        doc_form=DocForm.PRINTED, vendor="德利行", date="2026-09-22",
        items=[ReceiptItem(name="菜心", qty=1, unit="斤", unit_price=10, amount=10)],
        total=10, payment_marked=True, confidence=0.9,
    )
    monkeypatch.setattr(supervisor, "_run_extract",
                        lambda *a, **k: _fake_extract_payload(data))
    seen = []
    monkeypatch.setattr(supervisor, "retrieve_context",
                        lambda vendor, **kw: seen.append(
                            (vendor, kw.get("tenant_id"))) or "")

    supervisor.run_pipeline("uploads/p0t1_backfill.jpg", tenant_id="t2",
                            config=EngineConfig(max_retry_rounds=1))

    assert seen == [("德利行", "t2")], seen


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
