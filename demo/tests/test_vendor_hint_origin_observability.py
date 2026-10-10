# -*- coding: utf-8 -*-
"""可观测性（additive）：显式 vendor_hint 的来源区分（vendor_hint_origin）。

背景（QA_evidence §11 备注 1）：upload 端点把 vendor_precheck 预判出的供应商作为
**显式 vendor_hint** 传入管线，下游无法区分「用户手填 hint」与「预判 hint」——
两者的 rag_hint_source 恒为空串（按 FP-1 语义本就不触发首轮注入标记）。本修复沿用
FP-1 的同一套纯增量模式：管线 state 加 `vendor_hint_origin`（"user"=用户手填 /
"precheck"=上传端点预判填充），透传进 Job result（receipt_utils done 分支）与
ai_decision_log.extra（_log_extract_decision）；**空串不写键**；prompt 链路一行不改。

判别式判据：
(f2-1) origin="user" + 显式 hint：extract 决策行 extra 含 "vendor_hint_origin":"user"，
       且同输入下 PROMPT 与不传 origin 的对照 run 逐字节一致（改观测不改行为）；
(f2-2) origin="precheck"：extra 含 "vendor_hint_origin":"precheck"；
(f2-3) 缺省（无 origin）：extra **不含**该键且基线键集合完整 —— 空串不写键 ⇒
       json.dumps 字节与改动前逐字节一致；
(f2-4) Job result 透传：start_recognition_job 把 origin 转发给 run_pipeline，
       done 分支 result 恒有 vendor_hint_origin 键（有值为 "precheck"，缺省为空串）；
(f2-5) upload 端点：用户手填 hint → origin="user"；预判填充 → origin="precheck"；
       预判落空（返回空串）→ origin=""（不写键的来源形态）。

全程离线：临时 DB / 临时 RAG / 假引擎 / 打桩 vendor_precheck，零网络、零 live 写入。
"""

import json
import os
import sys
import time

import pytest

os.environ.setdefault("DB_PATH", "/tmp/vendor_hint_origin.db")
os.environ.setdefault("RAG_DIR", "/tmp/vendor_hint_origin_rag")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app import api_receipts  # noqa: E402
from app import db  # noqa: E402
from app.chains import extract_chain, supervisor  # noqa: E402
from app.models import EngineConfig  # noqa: E402
from app.services import receipt_utils, settings_service, vendor_precheck  # noqa: E402

HINT_VENDOR = "德利行"

# 开关关路径的 extra 基线键集合（vendor_hint_origin 接线之前就存在的全部键）——
# 任何多出的键都意味着空串路径没有做到「纯增量、字节一致」。
# 2026-10-10 契约对齐：2e42ea3（6腿Token单价 + Prompt Caching 细分计费）为决策
# extra 新增 4 个引擎自适应参数观测键（max_tokens / image_detail / tokens_cached /
# temperature），parsing_logs 有真消费方 —— 纳入合法基线。
_BASELINE_EXTRA_KEYS = {
    "tokens_prompt", "tokens_completion", "tokens_total", "tokens_cached",
    "cost_hkd", "cost_estimated", "cost_estimated_reason", "elapsed_ms",
    "success", "image_path", "supplier", "doc_form",
    "max_tokens", "image_detail", "temperature",
}


def _valid_json() -> str:
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


class _RecordingModel:
    kind = "openai"

    def __init__(self, output=""):
        self.calls = 0
        self.prompts = []
        self._output = output

    def invoke(self, prompt):
        self.calls += 1
        self.prompts.append(prompt)
        return self._output


def _valid_png(tmp_path):
    p = str(tmp_path / "ok.png")
    Image.new("RGB", (40, 20), (7, 7, 7)).save(p, format="PNG")
    return p


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """每例独立临时库（要往 ai_decision_log 写，绝不碰 live）。"""
    old_path = db.DB_PATH
    test_db_path = str(tmp_path / "vendor_hint_origin.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    db.DB_PATH = test_db_path
    db._make_engine()
    yield
    db.DB_PATH = old_path
    db._make_engine()


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor, "_memory_log_path",
                        lambda: str(tmp_path / "parse_log.jsonl"))
    monkeypatch.setattr(extract_chain, "generate_canary_token", lambda: "")
    monkeypatch.setattr(extract_chain, "_dashscope_fallback_available",
                        lambda *a, **k: False)
    monkeypatch.setattr(supervisor, "_run_audit",
                        lambda *a, **k: {"skipped": True, "reason": "test"})


def _extract_rows(receipt_id=None):
    """读回本库全部 extract 决策行的 (attempt, extra dict)。"""
    s = db.get_session()
    try:
        from sqlalchemy import text
        q = ("SELECT ai_value, extra FROM ai_decision_log "
             "WHERE decision_type='extract'")
        params = {}
        if receipt_id is not None:
            q += " AND receipt_id=:rid"
            params["rid"] = receipt_id
        rows = s.execute(text(q), params).fetchall()
        out = []
        for ai_value, extra in rows:
            payload = json.loads(ai_value) if isinstance(ai_value, str) \
                else (ai_value or {})
            extra_d = json.loads(extra) if isinstance(extra, str) else extra
            out.append((int((payload or {}).get("attempt") or 0), extra_d or {}))
        return out
    finally:
        s.close()


def _install_model(monkeypatch, model):
    monkeypatch.setattr(supervisor, "build_recognition_model", lambda *a, **k: model)


def _set_switch(on: bool):
    settings_service.set_value("memory_first_round_inject_enabled", bool(on))


def _run(tmp_path, hint="", origin=None, receipt_id=None):
    kwargs = {}
    if origin is not None:
        kwargs["vendor_hint_origin"] = origin
    return supervisor.run_pipeline(
        _valid_png(tmp_path), vendor_hint=hint, receipt_id=receipt_id,
        tenant_id="default", config=EngineConfig(max_retry_rounds=2), **kwargs)


# -------------------------------------------------------------
# (f2-1) origin="user" + 显式 hint：extra 落 "user"，且 PROMPT 与对照 run 逐字节一致
# -------------------------------------------------------------
def test_user_origin_persists_and_prompt_is_byte_identical(tmp_path, monkeypatch):
    _set_switch(False)
    model = _RecordingModel(output=_valid_json())
    _install_model(monkeypatch, model)

    rid = db.create_receipt(tenant_id="default")
    state = _run(tmp_path, hint=HINT_VENDOR, origin="user", receipt_id=rid)

    assert state["vendor_hint_origin"] == "user"
    rows = _extract_rows(rid)
    assert rows and rows[0][0] == 1, rows
    extra = rows[0][1]
    assert extra.get("vendor_hint_origin") == "user", extra

    # 对照 run：同输入、同开关，唯一差异是不传 origin —— PROMPT 必须逐字节一致
    # （本修复只加观测键，不改 prompt 任何字节；prompt 变了即回归）
    control = _RecordingModel(output=_valid_json())
    _install_model(monkeypatch, control)
    _run(tmp_path, hint=HINT_VENDOR)
    assert control.prompts == model.prompts, (
        "origin 透传不得改变 prompt 任何字节（对照 run 与观测 run 的 prompt 序列不同）")


# -------------------------------------------------------------
# (f2-2) origin="precheck"：extra 落 "precheck"
# -------------------------------------------------------------
def test_precheck_origin_persists(tmp_path, monkeypatch):
    _set_switch(False)
    model = _RecordingModel(output=_valid_json())
    _install_model(monkeypatch, model)

    rid = db.create_receipt(tenant_id="default")
    state = _run(tmp_path, hint=HINT_VENDOR, origin="precheck", receipt_id=rid)

    assert state["vendor_hint_origin"] == "precheck"
    rows = _extract_rows(rid)
    assert rows, "应有决策行落库"
    for attempt, extra in rows:
        assert extra.get("vendor_hint_origin") == "precheck", (attempt, extra)


# -------------------------------------------------------------
# (f2-3) 缺省（无 origin）：extra 不含该键且基线键集合完整（空串不写键）
# -------------------------------------------------------------
def test_no_origin_leaves_no_extra_key(tmp_path, monkeypatch):
    _set_switch(False)
    model = _RecordingModel(output=_valid_json())
    _install_model(monkeypatch, model)

    rid = db.create_receipt(tenant_id="default")
    state = _run(tmp_path, hint=HINT_VENDOR, receipt_id=rid)

    assert state["vendor_hint_origin"] == ""
    rows = _extract_rows(rid)
    assert rows, "应有决策行落库"
    for attempt, extra in rows:
        assert "vendor_hint_origin" not in extra, (attempt, extra)
        # 基线键集合完整：同值下 json.dumps 的字节与接线前完全一致
        assert set(extra) == _BASELINE_EXTRA_KEYS, sorted(set(extra))


# -------------------------------------------------------------
# (f2-4) Job result 透传（start_recognition_job 转发 origin + done 分支恒有键）
# -------------------------------------------------------------
def _drive_job(monkeypatch, origin, tmp_path):
    """打桩 run_pipeline（记录收到的 origin，回带该键）后真实走一遍
    start_recognition_job 的 done 分支，判别 receipt_utils 的转发与 result 组装。"""

    captured = {}

    def _fake_pipeline(image_path, vendor_hint="", config=None, supplier_name="",
                       receipt_id=None, experiment_id=None, on_event=None,
                       image_guard=None, tenant_id="default",
                       vendor_hint_origin=""):
        captured["vendor_hint_origin"] = vendor_hint_origin
        return {
            "status": "parsed", "data": {"vendor": HINT_VENDOR, "items": []},
            "log": [], "retry_count": 0, "use_grey": False,
            "gate_warnings": [],
            "vendor_hint_origin": vendor_hint_origin,
        }

    monkeypatch.setattr(supervisor, "run_pipeline", _fake_pipeline)
    monkeypatch.setattr(receipt_utils, "save_parsed_data",
                        lambda receipt_id, data, result: {"stub": True})

    kwargs = {"vendor_hint_origin": origin} if origin is not None else {}
    job_id, _rid = receipt_utils.start_recognition_job(
        _valid_png(tmp_path), vendor_hint=HINT_VENDOR, tenant_id="default",
        **kwargs)
    deadline = time.time() + 15
    job = None
    while time.time() < deadline:
        job = receipt_utils.get_job(job_id)
        if job and job.get("job_status") in ("done", "error"):
            break
        time.sleep(0.05)
    assert job is not None and job["job_status"] == "done", job
    return captured["vendor_hint_origin"], job["result"]


def test_job_forwards_origin_and_result_carries_precheck(tmp_path, monkeypatch):
    forwarded, result = _drive_job(monkeypatch, "precheck", tmp_path)
    assert forwarded == "precheck", "start_recognition_job 必须把 origin 转发给管线"
    assert result["vendor_hint_origin"] == "precheck", result


def test_job_result_carries_empty_origin_by_default(tmp_path, monkeypatch):
    """缺省（调用方不传 origin）：转发空串、result 恒有键且为空串（前端纯增量）。"""
    forwarded, result = _drive_job(monkeypatch, None, tmp_path)
    assert forwarded == ""
    assert "vendor_hint_origin" in result and result["vendor_hint_origin"] == "", result


# -------------------------------------------------------------
# (f2-5) upload 端点：手填 / 预判 / 落空 三种来源判定
# -------------------------------------------------------------
def _make_upload_client(monkeypatch, tmp_path, predicted_vendor):
    """只挂 api_receipts 路由；识别 Job 派发与预判模型全部打桩，记录入参。"""
    calls = []

    # 桩签名必须跟齐 start_recognition_job 的现行参数：_safe_start_recognition_job
    # 兼容链会在 TypeError 时逐级降级到老签名，桩若缺 quality_warnings/raw_image_path
    # 会静默丢掉 vendor_hint_origin（2026-10-10 修复）。
    def _stub_start(image_path, vendor_hint="", receipt_id=None, tenant_id=None,
                    image_guard=None, vendor_hint_origin="", quality_warnings=None,
                    raw_image_path=None, **kwargs):
        calls.append({"vendor_hint": vendor_hint,
                      "vendor_hint_origin": vendor_hint_origin})
        return ("job-stub-1", 9001)

    monkeypatch.setattr(api_receipts, "start_recognition_job", _stub_start)
    monkeypatch.setattr(vendor_precheck, "predict_vendor",
                        lambda image_path, cfg=None: predicted_vendor)

    def _save_upload(_f):
        p = tmp_path / "fake_upload.jpg"
        p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
        return str(p)

    monkeypatch.setattr(api_receipts, "_save_upload", _save_upload)
    monkeypatch.setattr(api_receipts, "_laplacian_variance", lambda _p: None)
    monkeypatch.setattr(api_receipts, "_run_image_guard",
                        lambda _p: {"flagged": False, "reasons": [], "metrics": {}})
    monkeypatch.setattr(api_receipts, "public_image_url", lambda *a, **k: "")
    monkeypatch.setattr(api_receipts, "_track_event", lambda *a, **k: None)
    monkeypatch.setattr(
        __import__("app.services.preprocess", fromlist=["apply_pipeline"]),
        "apply_pipeline", lambda p: (p, {"applied": False}))

    app = FastAPI()
    app.include_router(api_receipts.router)
    return TestClient(app), calls


def _upload(client, vendor_hint=None):
    data = {"async_": "true"}
    if vendor_hint is not None:
        data["vendor_hint"] = vendor_hint
    r = client.post("/api/upload?async=true",
                    files=[("receipt", ("x.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64,
                                         "image/png"))],
                    data=data, headers={"X-Role": "staff"})
    assert r.status_code == 200, r.text
    return r


def test_upload_user_typed_hint_origin_is_user(monkeypatch, tmp_path):
    client, calls = _make_upload_client(monkeypatch, tmp_path, predicted_vendor="預判商號")
    _upload(client, vendor_hint=HINT_VENDOR)
    assert len(calls) == 1
    assert calls[0]["vendor_hint"] == HINT_VENDOR, "用户手填的 hint 必须原样透传"
    assert calls[0]["vendor_hint_origin"] == "user", calls


def test_upload_precheck_hint_origin_is_precheck(monkeypatch, tmp_path):
    client, calls = _make_upload_client(monkeypatch, tmp_path, predicted_vendor=HINT_VENDOR)
    _upload(client)  # 不带 vendor_hint → 走预判
    assert len(calls) == 1
    assert calls[0]["vendor_hint"] == HINT_VENDOR, "预判出的供应商必须仍作为 vendor_hint 传入"
    assert calls[0]["vendor_hint_origin"] == "precheck", calls


def test_upload_precheck_miss_origin_stays_empty(monkeypatch, tmp_path):
    client, calls = _make_upload_client(monkeypatch, tmp_path, predicted_vendor="")
    _upload(client)
    assert len(calls) == 1
    assert calls[0]["vendor_hint"] == ""
    assert calls[0]["vendor_hint_origin"] == "", (
        "预判落空时来源必须保持空串（下游 extra 不写键）")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
