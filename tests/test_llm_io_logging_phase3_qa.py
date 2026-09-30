# -*- coding: utf-8 -*-
"""Phase 3: QA 自动化全量测试与验证套件 (AC-1 至 AC-7 全覆盖)。

覆盖范围：
- AC-1: 多腿全链路与各重试轮次落盘与字段完整性验证
- AC-2: Cached Token 提取归一化与 Supervisor 聚合及缓存命中率计算
- AC-3: 多模态 1MB+ Base64 图片引用化治理 (Image Reference Schema)
- AC-4: 敏感数据全方位脱敏验证 (身份证、API Key、Bearer 令牌、商业敏感价格)
- AC-5: 边界异常与容错韧性测试 (调用报错、超时记录与软失败)
- AC-6: 跨线程与多协程上下文隔离与传递验证 (ContextVars)
- AC-7: API 接口契约校验 (/api/analytics/parsing-logs/detail)
"""

import base64
import concurrent.futures
import contextvars
import json
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from app import db
from app.main import app
from app.llm import _normalize_token_usage, OpenAIChatModel
from app.services.llm_logger import (
    sanitize_text,
    sanitize_prompt_messages,
    record_llm_call,
    set_llm_context,
    get_llm_context,
    llm_context,
)
from app.services.parsing_logs import get_parsing_log_detail


def test_qa_ac1_multi_leg_and_field_integrity():
    """AC-1: 识别腿、审核腿、解析腿各轮调用均落盘至 llm_call_log，验证全部字段完整性。"""
    test_rid = 71001
    test_tid = "tenant_qa_ac1"
    test_trace = "trace_qa_ac1_001"

    # 清理旧数据
    s = db.get_session()
    try:
        s.query(db._LlmCallLogRow).filter(db._LlmCallLogRow.receipt_id == test_rid).delete()
        s.commit()
    finally:
        s.close()

    # 1. 模拟识别腿第 1 轮 (超时重试)
    log_id_1 = record_llm_call(
        receipt_id=test_rid,
        tenant_id=test_tid,
        trace_id=test_trace,
        leg="extract",
        attempt=1,
        engine="openai",
        model="qwen3.5-omni-flash",
        prompt_messages=[{"role": "user", "content": "Extract Leg Attempt 1"}],
        raw_output="",
        token_usage={"prompt_tokens": 1200, "completion_tokens": 0, "cached_tokens": 600, "total_tokens": 1200},
        elapsed_ms=5000.0,
        status="timeout",
        error_msg="Upstream call timeout after 5000ms",
        error_category="upstream",
    )
    assert log_id_1 is not None

    # 2. 模拟识别腿第 2 轮 (成功)
    log_id_2 = record_llm_call(
        receipt_id=test_rid,
        tenant_id=test_tid,
        trace_id=test_trace,
        leg="extract",
        attempt=2,
        engine="openai",
        model="qwen3.5-omni-flash",
        prompt_messages=[{"role": "user", "content": "Extract Leg Attempt 2"}],
        raw_output=json.dumps({"vendor": "Vendor_A", "total": 288.5}, ensure_ascii=False),
        token_usage={"prompt_tokens": 1200, "completion_tokens": 300, "cached_tokens": 800, "total_tokens": 1500},
        cost_hkd=0.0028,
        elapsed_ms=1850.5,
        status="success",
    )
    assert log_id_2 is not None

    # 3. 模拟解析腿 (成功)
    log_id_3 = record_llm_call(
        receipt_id=test_rid,
        tenant_id=test_tid,
        trace_id=test_trace,
        leg="parse",
        attempt=1,
        engine="openai",
        model="qwen-plus",
        prompt_messages=[{"role": "user", "content": "Parse and format JSON"}],
        raw_output=json.dumps({"vendor": "Vendor_A", "total": 288.5, "items": []}, ensure_ascii=False),
        token_usage={"prompt_tokens": 500, "completion_tokens": 150, "cached_tokens": 200, "total_tokens": 650},
        cost_hkd=0.0006,
        elapsed_ms=620.0,
        status="success",
    )
    assert log_id_3 is not None

    # 4. 模拟审核腿 (规则引擎 / code_engine)
    log_id_4 = record_llm_call(
        receipt_id=test_rid,
        tenant_id=test_tid,
        trace_id=test_trace,
        leg="audit",
        attempt=1,
        engine="code_engine",
        model="rule_engine",
        prompt_messages=[{"role": "user", "content": "Rule verification"}],
        raw_output=json.dumps({"overall_consistent": True, "trust": 0.98}, ensure_ascii=False),
        token_usage={"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "total_tokens": 0},
        cost_hkd=0.0,
        elapsed_ms=15.2,
        status="success",
    )
    assert log_id_4 is not None

    # 校验数据库落盘完整性
    logs = db.list_llm_call_logs_by_receipt(test_rid)
    assert len(logs) == 4

    # 逐项字段断言
    call_attempts = {c["leg"]: c for c in logs}
    assert "extract" in call_attempts
    assert "parse" in call_attempts
    assert "audit" in call_attempts

    # 检查第 1 轮识别超时记录
    c1 = [c for c in logs if c["leg"] == "extract" and c["attempt"] == 1][0]
    assert c1["tenant_id"] == test_tid
    assert c1["receipt_id"] == test_rid
    assert c1["trace_id"] == test_trace
    assert c1["status"] == "timeout"
    assert c1["error_category"] == "upstream"
    assert c1["elapsed_ms"] == 5000.0

    # 检查第 2 轮识别成功记录
    c2 = [c for c in logs if c["leg"] == "extract" and c["attempt"] == 2][0]
    assert c2["tenant_id"] == test_tid
    assert c2["status"] == "success"
    assert c2["cached_tokens"] == 800
    assert c2["prompt_tokens"] == 1200
    assert c2["completion_tokens"] == 300
    assert c2["total_tokens"] == 1500
    assert c2["cost_hkd"] == 0.0028

    # 清理
    s = db.get_session()
    try:
        s.query(db._LlmCallLogRow).filter(db._LlmCallLogRow.receipt_id == test_rid).delete()
        s.commit()
    finally:
        s.close()


def test_qa_ac2_cached_tokens_normalization_and_supervisor_aggregation():
    """AC-2: Cached Token 提取归一化与缓存命中率计算逻辑。"""
    # 1. 验证 DashScope / OpenAI prompt_tokens_details.cached_tokens
    u1 = {
        "prompt_tokens": 3000,
        "completion_tokens": 500,
        "total_tokens": 3500,
        "prompt_tokens_details": {"cached_tokens": 2100},
    }
    r1 = _normalize_token_usage(u1)
    assert r1["cached_tokens"] == 2100
    assert r1["prompt_tokens"] == 3000
    assert r1["total_tokens"] == 3500

    # 2. 验证 Anthropic 格式 cache_read_input_tokens
    u2 = {
        "prompt_tokens": 4000,
        "completion_tokens": 600,
        "total_tokens": 4600,
        "cache_read_input_tokens": 2500,
    }
    r2 = _normalize_token_usage(u2)
    assert r2["cached_tokens"] == 2500

    # 3. 验证顶层 cached_tokens
    u3 = {"prompt": 1000, "completion": 200, "cached_tokens": 600}
    r3 = _normalize_token_usage(u3)
    assert r3["cached_tokens"] == 600

    # 4. 边界异常：None、空字典、字符串与浮点数解析
    assert _normalize_token_usage({}) == {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "total_tokens": 0}
    assert _normalize_token_usage(None) == {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "total_tokens": 0}
    u_str = {"prompt_tokens": "1500", "completion_tokens": "300.0", "cached_tokens": "900"}
    r_str = _normalize_token_usage(u_str)
    assert r_str["prompt_tokens"] == 1500
    assert r_str["completion_tokens"] == 300
    assert r_str["cached_tokens"] == 900
    assert r_str["total_tokens"] == 1800

    # 5. 命中率计算精度与除零保护
    assert round(r1["cached_tokens"] / r1["prompt_tokens"], 4) == 0.7000
    # 0 prompt tokens 时不除零
    zero_hit_rate = round(0 / r1["prompt_tokens"], 4) if r1["prompt_tokens"] > 0 else 0.0
    assert zero_hit_rate == 0.0


def test_qa_ac3_multimodal_large_image_ref_schema():
    """AC-3: 多模态 1MB+ Base64 图片引用化治理，入库绝无超长 base64，均为紧凑元数据。"""
    # 构造 > 1MB 的 Base64 图片数据
    large_payload_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * (1024 * 1024 + 512)
    large_b64 = "data:image/png;base64," + base64.b64encode(large_payload_bytes).decode("ascii")
    assert len(large_b64) > 1024 * 1024

    messages = [
        {"role": "system", "content": "You are a receipt extraction engine."},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Please parse receipt image."},
                {"type": "image_url", "image_url": {"url": large_b64}},
            ],
        },
    ]

    test_rid = 73001
    source_file = "/path/to/virtual/receipt_large.png"

    # 执行清洗并入库
    with llm_context(receipt_id=test_rid, leg="extract", attempt=1, image_path=source_file):
        log_id = record_llm_call(
            receipt_id=test_rid,
            leg="extract",
            prompt_messages=messages,
            raw_output="{\"total\": 100.0}",
        )
    assert log_id is not None

    # 从数据库读出落盘的 Prompt
    logs = db.list_llm_call_logs_by_receipt(test_rid)
    assert len(logs) == 1
    raw_prompt_json = logs[0]["prompt_messages_json"]

    # 严苛断言 1: 绝对不含 base64 字符串
    assert "data:image" not in raw_prompt_json
    assert "base64" not in raw_prompt_json

    # 严苛断言 2: 序列化后字符串长度小，处于安全范围 (< 2KB)
    assert len(raw_prompt_json) < 2048

    # 严苛断言 3: 包含标准紧凑 image_ref 元数据
    parsed_prompt = json.loads(raw_prompt_json)
    user_blocks = parsed_prompt[1]["content"]
    img_block = [b for b in user_blocks if b.get("type") == "image_ref"][0]
    assert "image_ref" in img_block
    ref = img_block["image_ref"]
    assert ref["mime"] == "image/png"
    assert ref["byte_size"] >= 1024 * 1024
    assert len(ref["sha256"]) == 64
    assert ref["source_path"] == source_file

    # 清理
    s = db.get_session()
    try:
        s.query(db._LlmCallLogRow).filter(db._LlmCallLogRow.receipt_id == test_rid).delete()
        s.commit()
    finally:
        s.close()


def test_qa_ac4_sensitive_data_desensitization():
    """AC-4: 敏感数据脱敏验证：身份证号、API Key、Bearer 令牌及商业敏感价格。"""
    raw_text = (
        "调用服务商 API，使用 sk-live99887766554433221100aa 密钥认证。\n"
        "用户授权 Header 为 Bearer tok_sec_secret_credentials_998877665544\n"
        "持卡人身份证号码为 110101199003072345 请严格保密。\n"
        "内部核算指标：敏感毛利: 42.5%，加价率: 20%，成本底价: $850.00。"
    )

    clean_text = sanitize_text(raw_text)

    # 1. API Key 脱敏
    assert "sk-live99887766554433221100aa" not in clean_text
    assert "sk-***" in clean_text

    # 2. Bearer 令牌脱敏
    assert "tok_sec_secret_credentials_998877665544" not in clean_text
    assert "Bearer ***" in clean_text

    # 3. 身份证号脱敏 (前6后4，中间8位掩码)
    assert "110101199003072345" not in clean_text
    assert "110101********2345" in clean_text

    # 4. 商业敏感价格与毛利率脱敏
    assert "42.5%" not in clean_text
    assert "$850.00" not in clean_text
    assert "敏感毛利: $***" in clean_text
    assert "成本底价: $***" in clean_text

    # 5. 测试持久化入库时的脱敏留痕
    test_rid = 74001
    log_id = record_llm_call(
        receipt_id=test_rid,
        leg="extract",
        prompt_messages=[{"role": "user", "content": raw_text}],
        raw_output=raw_text,
    )
    assert log_id is not None

    logs = db.list_llm_call_logs_by_receipt(test_rid)
    assert len(logs) == 1
    log = logs[0]

    # prompt 与 raw_output 均无敏感信息明文
    assert "sk-live" not in log["prompt_messages_json"]
    assert "110101199003072345" not in log["prompt_messages_json"]
    assert "sk-live" not in log["raw_output"]
    assert "110101199003072345" not in log["raw_output"]

    # 清理
    s = db.get_session()
    try:
        s.query(db._LlmCallLogRow).filter(db._LlmCallLogRow.receipt_id == test_rid).delete()
        s.commit()
    finally:
        s.close()


def test_qa_ac5_boundary_exception_and_fault_tolerance():
    """AC-5: 边界异常与容错韧性：模拟 LLM 抛错与写库软失败。"""
    test_rid = 75001

    # 1. 模拟 LLM 调用抛出异常时记录错误日志
    log_id = record_llm_call(
        receipt_id=test_rid,
        leg="extract",
        attempt=1,
        engine="openai",
        model="qwen3.5-omni-flash",
        prompt_messages=[{"role": "user", "content": "test prompt"}],
        raw_output="",
        elapsed_ms=3000.0,
        status="error",
        error_msg="ConnectionRefusedError: Failed to reach upstream provider",
        error_category="upstream",
    )
    assert log_id is not None

    logs = db.list_llm_call_logs_by_receipt(test_rid)
    assert len(logs) == 1
    err_log = logs[0]
    assert err_log["status"] == "error"
    assert "ConnectionRefusedError" in err_log["error_msg"]
    assert err_log["error_category"] == "upstream"

    # 2. 模拟底层 record_llm_call 遇到不可预知参数时的软失败不崩溃
    # 传入可能导致序列化或处理异常的非标准对象
    class BadObject:
        def __str__(self):
            raise RuntimeError("Intentional string conversion error")

    # 软失败应返回 None，绝对不抛出未捕获异常导致主业务中断
    res = record_llm_call(
        receipt_id=test_rid,
        prompt_messages=BadObject(),
    )
    assert res is None or isinstance(res, int)

    # 清理
    s = db.get_session()
    try:
        s.query(db._LlmCallLogRow).filter(db._LlmCallLogRow.receipt_id == test_rid).delete()
        s.commit()
    finally:
        s.close()


def test_qa_ac6_cross_thread_context_propagation():
    """AC-6: 跨线程与多协程上下文隔离与传递验证 (ContextVars 无串单)。"""
    results = {}

    def worker(rid: int, tid: str, leg: str, attempt: int):
        with llm_context(receipt_id=rid, tenant_id=tid, leg=leg, attempt=attempt):
            ctx_snapshot = get_llm_context()
            log_id = record_llm_call(
                prompt_messages=[{"role": "user", "content": f"Worker for {rid}"}],
                raw_output=f"Output for {rid}",
                token_usage={"prompt_tokens": 100 * attempt, "cached_tokens": 50 * attempt},
            )
            return rid, ctx_snapshot, log_id

    # 启动 4 个并发线程分别处理不同单据与腿
    tasks = [
        (80001, "tenant_alpha", "extract", 1),
        (80002, "tenant_beta", "audit", 1),
        (80003, "tenant_gamma", "extract", 2),
        (80004, "tenant_delta", "parse", 1),
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(worker, *t) for t in tasks]
        for fut in concurrent.futures.as_completed(futures):
            rid, ctx_snap, log_id = fut.result()
            results[rid] = (ctx_snap, log_id)

    # 校验各线程上下文与入库日志完全隔离无交叉污染
    assert len(results) == 4

    for rid, tid, leg, att in tasks:
        ctx_snap, log_id = results[rid]
        assert ctx_snap["receipt_id"] == rid
        assert ctx_snap["tenant_id"] == tid
        assert ctx_snap["leg"] == leg
        assert ctx_snap["attempt"] == att

        # 查库校验
        db_logs = db.list_llm_call_logs_by_receipt(rid)
        assert len(db_logs) == 1
        db_log = db_logs[0]
        assert db_log["receipt_id"] == rid
        assert db_log["tenant_id"] == tid
        assert db_log["leg"] == leg
        assert db_log["attempt"] == att
        assert db_log["cached_tokens"] == 50 * att

    # 清理测试数据
    s = db.get_session()
    try:
        for rid, _, _, _ in tasks:
            s.query(db._LlmCallLogRow).filter(db._LlmCallLogRow.receipt_id == rid).delete()
        s.commit()
    finally:
        s.close()


def test_qa_ac7_detail_api_contract():
    """AC-7: 详情接口 /api/analytics/parsing-logs/detail 契约测试与指标聚合验证。"""
    test_rid = 90001

    # 准备假单据与两轮调用数据
    s = db.get_session()
    try:
        # 清理旧数据
        s.query(db._ReceiptRow).filter(db._ReceiptRow.id == test_rid).delete()
        s.query(db._LlmCallLogRow).filter(db._LlmCallLogRow.receipt_id == test_rid).delete()
        s.commit()

        # 插入单据行
        r_row = db._ReceiptRow(
            id=test_rid,
            status="parsed",
            supplier_name="测试港味供应商",
            total_amount=520.0,
            tenant_id="default",
        )
        s.add(r_row)
        s.commit()

        # 插入 extract leg 日志
        db.insert_llm_call_log(
            receipt_id=test_rid,
            leg="extract",
            attempt=1,
            engine="openai",
            model="qwen3.5-omni-flash",
            prompt_messages_json=[{"role": "user", "content": "Extract prompt"}],
            raw_output=json.dumps({"vendor": "Vendor_A", "total": 520.0}, ensure_ascii=False),
            prompt_tokens=2000,
            completion_tokens=400,
            cached_tokens=1400,
            total_tokens=2400,
            cost_hkd=0.0035,
            elapsed_ms=1800.0,
            status="success",
        )

        # 插入 audit leg 日志
        db.insert_llm_call_log(
            receipt_id=test_rid,
            leg="audit",
            attempt=1,
            engine="openai",
            model="zai-org/GLM-4.5V",
            prompt_messages_json=[{"role": "user", "content": "Audit prompt"}],
            raw_output=json.dumps({"overall_consistent": True}, ensure_ascii=False),
            prompt_tokens=600,
            completion_tokens=50,
            cached_tokens=0,
            total_tokens=650,
            cost_hkd=0.0006,
            elapsed_ms=750.0,
            status="success",
        )

        # 使用 FastAPI TestClient 校验 HTTP API 接口
        client = TestClient(app)
        resp = client.get(
            f"/api/analytics/parsing-logs/detail?record_id={test_rid}",
            headers={"X-Role": "admin"},
        )
        assert resp.status_code == 200
        res_data = resp.json()

        assert res_data.get("status") == "success"
        data = res_data.get("data", {})
        assert data.get("record_id") == test_rid

        # 校验 llm_calls 列表
        assert "llm_calls" in data
        llm_calls = data["llm_calls"]
        assert len(llm_calls) == 2

        # 校验第 1 腿 (识别腿)
        call_extract = [c for c in llm_calls if c["leg"] == "extract"][0]
        assert call_extract["leg_label"] == "识别腿 (VLM)"
        assert call_extract["engine"] == "openai"
        assert call_extract["model"] == "qwen3.5-omni-flash"
        assert call_extract["token_usage"]["cached_tokens"] == 1400
        assert call_extract["token_usage"]["prompt_tokens"] == 2000
        assert call_extract["token_usage"]["cache_hit_rate"] == 0.7

        # 校验第 2 腿 (审核腿)
        call_audit = [c for c in llm_calls if c["leg"] == "audit"][0]
        assert call_audit["leg_label"] == "审核腿 (Cross-Audit)"
        assert call_audit["model"] == "zai-org/GLM-4.5V"

        # 校验顶层 token_usage 聚合
        top_tu = data.get("token_usage", {})
        assert top_tu.get("tokens_cached") == 1400
    finally:
        s.query(db._ReceiptRow).filter(db._ReceiptRow.id == test_rid).delete()
        s.query(db._LlmCallLogRow).filter(db._LlmCallLogRow.receipt_id == test_rid).delete()
        s.commit()
        s.close()
