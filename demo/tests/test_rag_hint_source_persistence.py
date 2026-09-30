# -*- coding: utf-8 -*-
"""FP-1：`rag_hint_source` 落持久层（ai_decision_log.extra + Job result 透传）。

现状缺口：W-1 首轮注入标记只在管线内存 state（supervisor），两个消费点没接 ——
(a) `_log_extract_decision` 的 extra 不落库；(b) receipt_utils 的 done 分支 result
不含该键。修复为纯增量：**非空才写键**，空串路径的 extra 序列化字节与改动前逐字节
一致（审计 C3 / 开关关红线的实现保证）。

判别式判据（SPEC §1.3/§1.5）：
(f1-1) 开关开 + 预判出供应商 + 首轮成功：attempt=1 的 extract 决策行 extra 含
       "rag_hint_source":"first_round"（A1）；
(f1-2) 开关开 + 显式 vendor_hint（不走首轮注入）：extra **不含**该键（不猜不冒写）；
(f1-3) 开关关（任意输入）：extra 不含该键 —— 即「空串不写键」的逐字节一致判据；
(f1-4) receipt_id=None（run_pipeline 直调/冒烟）：照旧跳过写库不抛错；
(f1-5) Job result 透传：done 分支 result 恒有 rag_hint_source 键（first_round /
       空串），由打桩 run_pipeline 判别 —— 影响只在 receipt_utils 那一行映射。

全程离线：临时 DB / 临时 RAG / 假引擎 / 打桩 vendor_precheck，零网络、零 live 写入。
prompt 链路一行未改：本文件不改 extract_chain 任何桩行为，(f1-3) 的「extra 无键 +
基线键集合完整」即字节一致证明（json.dumps 同一 dict = 改动前字节）。
"""

import json
import os
import sys
import time

import pytest
from PIL import Image

os.environ.setdefault("DB_PATH", "/tmp/fp1_rag_hint_source.db")
os.environ.setdefault("RAG_DIR", "/tmp/fp1_rag_hint_source_rag")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app.chains import extract_chain, supervisor  # noqa: E402
from app.models import EngineConfig  # noqa: E402
from app.services import rag, settings_service, vendor_precheck  # noqa: E402

SENTINEL_ITEM = "FP1哨兵菜心"
HINT_VENDOR = "德利行"

# 开关关路径的 extra 基线键集合（FP-1 之前就存在的全部键）——
# 任何多出的键都意味着空串路径没有做到「纯增量、字节一致」。
_BASELINE_EXTRA_KEYS = {
    "tokens_prompt", "tokens_completion", "tokens_total", "cost_hkd",
    "cost_estimated", "cost_estimated_reason", "elapsed_ms", "success",
    "image_path", "supplier", "doc_form",
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
    """每例独立临时库（要往 ai_decision_log / vendor_memory 写，绝不碰 live）。"""
    old_path = db.DB_PATH
    test_db_path = str(tmp_path / "fp1_rag_hint_source.db")
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
    """读回本库全部 extract 决策行的 (attempt, extra dict)。

    attempt 不在列上（表结构只有 ai_value/extra 两个 JSON 载体），从 ai_value 载荷取。
    """
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


def _seed_memory(vendor=HINT_VENDOR, tenant_id="default"):
    rag.ingest_memory(vendor, f"1. {SENTINEL_ITEM} 2斤 @5 = 10",
                      notes=f"版式：fp1手写单", tenant_id=tenant_id)


def _install_model(monkeypatch, model):
    monkeypatch.setattr(supervisor, "build_recognition_model", lambda *a, **k: model)


def _set_switch(on: bool):
    settings_service.set_value("memory_first_round_inject_enabled", bool(on))


def _stub_precheck(monkeypatch, vendor, enabled=True):
    monkeypatch.setattr(vendor_precheck, "is_enabled", lambda: enabled)
    monkeypatch.setattr(vendor_precheck, "predict_vendor",
                        lambda image_path, cfg=None: vendor)


def _run(tmp_path, hint="", receipt_id=None, tenant_id="default"):
    return supervisor.run_pipeline(
        _valid_png(tmp_path), vendor_hint=hint, receipt_id=receipt_id,
        tenant_id=tenant_id, config=EngineConfig(max_retry_rounds=2))


# -------------------------------------------------------------
# (f1-1) 开关开 + 预判命中：attempt=1 决策行 extra 含 first_round
# -------------------------------------------------------------
def test_first_round_inject_persists_rag_hint_source_in_extra(tmp_path, monkeypatch):
    _seed_memory()
    _set_switch(True)
    _stub_precheck(monkeypatch, HINT_VENDOR, enabled=True)
    model = _RecordingModel(output=_valid_json())
    _install_model(monkeypatch, model)

    receipt_id = db.create_receipt(tenant_id="default")
    state = _run(tmp_path, receipt_id=receipt_id)

    assert state["rag_hint_source"] == "first_round"
    rows = _extract_rows(receipt_id)
    assert rows and rows[0][0] == 1, rows  # 首轮即成功：只有 attempt=1 一行
    extra = rows[0][1]
    assert extra.get("rag_hint_source") == "first_round", extra


# -------------------------------------------------------------
# (f1-2) 显式 hint：不走首轮注入 → extra 不含该键
# -------------------------------------------------------------
def test_explicit_hint_leaves_no_rag_hint_source_key(tmp_path, monkeypatch):
    _seed_memory()
    _set_switch(True)
    model = _RecordingModel(output=_valid_json())
    _install_model(monkeypatch, model)

    receipt_id = db.create_receipt(tenant_id="default")
    state = _run(tmp_path, hint=HINT_VENDOR, receipt_id=receipt_id)

    assert state["rag_hint_source"] == ""
    rows = _extract_rows(receipt_id)
    assert rows, "应有决策行落库"
    for attempt, extra in rows:
        assert "rag_hint_source" not in extra, (attempt, extra)
        assert set(extra) == _BASELINE_EXTRA_KEYS, sorted(set(extra))


# -------------------------------------------------------------
# (f1-3) 开关关：extra 不含该键且基线键完整（空串不写键 → 字节与改动前一致）
# -------------------------------------------------------------
def test_switch_off_extra_is_byte_identical_baseline(tmp_path, monkeypatch):
    _seed_memory()
    _set_switch(False)
    _stub_precheck(monkeypatch, HINT_VENDOR, enabled=True)
    model = _RecordingModel(output=_valid_json())
    _install_model(monkeypatch, model)

    receipt_id = db.create_receipt(tenant_id="default")
    state = _run(tmp_path, receipt_id=receipt_id)

    assert state["rag_hint_source"] == ""
    rows = _extract_rows(receipt_id)
    assert rows, "开关关也照常落决策行"
    for attempt, extra in rows:
        assert "rag_hint_source" not in extra, (attempt, extra)
        # 基线键集合完整：同值下 json.dumps 的字节与 FP-1 之前完全一致（C3 红线）
        assert set(extra) == _BASELINE_EXTRA_KEYS, sorted(set(extra))


# -------------------------------------------------------------
# (f1-4) receipt_id=None：照旧跳过写库不抛错
# -------------------------------------------------------------
def test_receipt_id_none_skips_logging_without_error(tmp_path, monkeypatch):
    _seed_memory()
    _set_switch(True)
    _stub_precheck(monkeypatch, HINT_VENDOR, enabled=True)
    model = _RecordingModel(output=_valid_json())
    _install_model(monkeypatch, model)

    state = _run(tmp_path)  # receipt_id=None（直调/冒烟形态）
    assert state["rag_hint_source"] == "first_round"
    assert _extract_rows() == [], "receipt_id=None 不得写决策库"


# -------------------------------------------------------------
# (f1-5) Job result 透传（receipt_utils done 分支恒有 rag_hint_source 键）
# -------------------------------------------------------------
def _drive_job(monkeypatch, rag_hint_source, tmp_path):
    """打桩 run_pipeline 后真实走一遍 start_recognition_job 的 done 分支，
    判别 receipt_utils 的 result 组装是否透传该键。"""
    from app.services import receipt_utils

    def _fake_pipeline(image_path, vendor_hint="", config=None, supplier_name="",
                       receipt_id=None, experiment_id=None, on_event=None,
                       image_guard=None, tenant_id="default"):
        return {
            "status": "parsed", "data": {"vendor": HINT_VENDOR, "items": []},
            "log": [], "retry_count": 0, "use_grey": False,
            "gate_warnings": [],
            "rag_hint_source": rag_hint_source,
        }

    monkeypatch.setattr(supervisor, "run_pipeline", _fake_pipeline)
    monkeypatch.setattr(receipt_utils, "save_parsed_data",
                        lambda receipt_id, data, result: {"stub": True})

    job_id, _rid = receipt_utils.start_recognition_job(_valid_png(tmp_path),
                                                       tenant_id="default")
    deadline = time.time() + 15
    job = None
    while time.time() < deadline:
        job = receipt_utils.get_job(job_id)
        if job and job.get("job_status") in ("done", "error"):
            break
        time.sleep(0.05)
    assert job is not None and job["job_status"] == "done", job
    return job["result"]


def test_job_result_carries_first_round_marker(tmp_path, monkeypatch):
    result = _drive_job(monkeypatch, "first_round", tmp_path)
    assert result["rag_hint_source"] == "first_round", result


def test_job_result_carries_empty_marker_when_off(tmp_path, monkeypatch):
    """开关关/显式 hint：字段恒存在、值为空串（前端纯增量，无键时报 undefined 的旧患不犯）。"""
    result = _drive_job(monkeypatch, "", tmp_path)
    assert "rag_hint_source" in result and result["rag_hint_source"] == "", result


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
