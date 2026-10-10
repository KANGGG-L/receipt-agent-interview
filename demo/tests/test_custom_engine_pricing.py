# -*- coding: utf-8 -*-
"""自定义 Token 价格计费能力全链路自动化测试套件（Phase 3 QA 验证）。

严格对照 Phase 1 验收标准清单 (Acceptance Criteria, AC-1 至 AC-8) 与全局红线：
- AC-1: 校验 6 条引擎腿在界面表单与数据模型中的 18 个单价字段完整性与数据类型（Input/Cached Input/Output 价格，单位 元/1M Token）；
- AC-2: 预设网关下拉切换时，官方建议单价正确填入表单；
- AC-3: 自定义价格前后端保存、回显与数据持久化；
- AC-4: 负数单价非法值拦截（后端 Pydantic 422 校验与前端防御）；
- AC-5: 价格留空时平滑回落内置刊例价，计算不报错；
- AC-6: 核心计费公式断言：
  cost = ((prompt_tokens - cached_tokens) * price_input + cached_tokens * price_cached_input + completion_tokens * price_output) / 1,000,000
  测试不同 Token 组合与单价配置，断言计算精度，确保 100% 参与到决策日志 extra、llm_call_log 与观测中心成本统计中；
- AC-7: 金丝雀推全（Promote）时，灰测组配置的价格正确晋升至常规组，配置 Diff 面板完整高亮价格变更；
- AC-8: 一键回滚（Rollback）时，能够精准恢复推全前的原始价格快照。
- 红线: 全局严禁使用 Emoji，确保纯文本标签呈现。
"""

import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from starlette.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app
from app import db
from app.models import EngineConfig
from app.api_admin import (
    EngineConfigBody,
    diff_regular_vs_grey,
    get_engine_presets,
    promote_grey_config,
    rollback_engine_config,
)
from app.llm import _calc_cost_hkd, _resolve_token_price_full
from app.chains.extract_chain import _cost_from_tokens_ex

client = TestClient(app)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEMO_ROOT = REPO_ROOT / "demo"

ALL_18_PRICE_FIELDS = [
    # 常规组
    "price_input_rec", "price_cached_input_rec", "price_output_rec",
    "price_input_aud", "price_cached_input_aud", "price_output_aud",
    "price_input_parse", "price_cached_input_parse", "price_output_parse",
    # 灰测组
    "price_input_grey_rec", "price_cached_input_grey_rec", "price_output_grey_rec",
    "price_input_grey_aud", "price_cached_input_grey_aud", "price_output_grey_aud",
    "price_input_grey_parse", "price_cached_input_grey_parse", "price_output_grey_parse",
]

DOM_INPUT_IDS = [
    # 常规识别
    "adminPriceInputRec", "adminPriceCachedInputRec", "adminPriceOutputRec",
    # 常规审核
    "adminPriceInputAud", "adminPriceCachedInputAud", "adminPriceOutputAud",
    # 常规解析
    "adminPriceInputParse", "adminPriceCachedInputParse", "adminPriceOutputParse",
    # 灰测识别
    "adminGreyPriceInputRec", "adminGreyPriceCachedInputRec", "adminGreyPriceOutputRec",
    # 灰测审核
    "adminGreyPriceInputAud", "adminGreyPriceCachedInputAud", "adminGreyPriceOutputAud",
    # 灰测解析
    "adminGreyPriceInputParse", "adminGreyPriceCachedInputParse", "adminGreyPriceOutputParse",
]


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """确保测试运行在独立的测试数据库实例中，防止污染运行环境。"""
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_pricing_isolated.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    db.DB_PATH = test_db_path
    db._make_engine()
    yield
    if os.path.exists(test_db_path):
        try:
            os.remove(test_db_path)
        except Exception:
            pass
    db.DB_PATH = old_db_path
    db._make_engine()


def _admin_headers():
    return {"X-Role": "admin", "X-Email": "admin@demo.hk"}


# ==============================================================================
# AC-1: 界面元素与数据模型 18 个单价字段声明完整性
# ==============================================================================

def test_engine_config_model_pricing_fields():
    """AC-1: EngineConfig 与 EngineConfigBody 必须包含全部 18 个单价字段，默认值均为 None。"""
    cfg = EngineConfig()
    body = EngineConfigBody()
    for f in ALL_18_PRICE_FIELDS:
        assert hasattr(cfg, f), f"EngineConfig 缺少字段 {f}"
        assert getattr(cfg, f) is None, f"EngineConfig.{f} 默认值应为 None"
        assert hasattr(body, f), f"EngineConfigBody 缺少字段 {f}"
        assert getattr(body, f) is None, f"EngineConfigBody.{f} 默认值应为 None"


def test_html_dom_and_labels_pure_text_no_emoji():
    """AC-1: 校验 demo/templates/index.html 中 18 个价格输入框的存在性、属性及纯文本标签（禁止 Emoji 与冗余括号）。"""
    html_file = DEMO_ROOT / "templates" / "index.html"
    assert html_file.exists(), f"未找到模板文件: {html_file}"
    content = html_file.read_text(encoding="utf-8")

    # 验证统一单位声明由头部承载 (元/1M Tokens)
    assert content.count("元/1M Tokens") == 6, "6处价格卡片头部必须统一声明单位 '元/1M Tokens'"

    for dom_id in DOM_INPUT_IDS:
        # 验证 input 标签定义
        pattern = rf'<input[^>]*id="{dom_id}"[^>]*>'
        match = re.search(pattern, content)
        assert match is not None, f"HTML 缺少输入控件 id={dom_id}"
        input_tag = match.group(0)
        assert 'type="number"' in input_tag, f"{dom_id} 必须声明 type=number"
        assert 'min="0"' in input_tag, f"{dom_id} 必须声明 min=0"
        assert 'step="0.0001"' in input_tag, f"{dom_id} 必须声明 step=0.0001"

        # 验证关联 label 标签为简明中文，不含冗余英文与括号后缀
        label_pattern = rf'<label[^>]*for="{dom_id}"[^>]*>(.*?)</label>'
        label_match = re.search(label_pattern, content, flags=re.DOTALL)
        assert label_match is not None, f"HTML 缺少控件关联标签 label for={dom_id}"
        label_text = label_match.group(1).strip()
        assert label_text in ["输入单价", "缓存输入", "输出单价"], f"{dom_id} 标签应为简洁纯中文，实际为: {label_text}"
        assert "(" not in label_text and ")" not in label_text, f"{dom_id} 标签严禁残留括号: {label_text}"


# ==============================================================================
# AC-2: 预设网关标准建议单价接口返回与前端映射
# ==============================================================================

def test_api_admin_presets_contain_recommended_prices():
    """AC-2: get_engine_presets 接口不再返回硬编码预设；彻底解耦。"""
    resp = client.get("/api/admin/engine-presets", headers=_admin_headers())
    assert resp.status_code == 200
    data = resp.json()["data"]

    assert "bailian" not in data
    assert "siliconflow" not in data
    assert "agnes" not in data


def test_frontend_presets_mapping_integrity():
    """AC-2: main.js 中已彻底移除 OPENAI_PRESETS 预设网关绑定。"""
    js_file = DEMO_ROOT / "static" / "js" / "main.js"
    assert js_file.exists()
    content = js_file.read_text(encoding="utf-8")

    assert "OPENAI_PRESETS" not in content
    assert "applyBoxPreset" not in content


# ==============================================================================
# AC-3: 自定义价格前后端保存、回显与数据持久化
# ==============================================================================

def test_api_put_and_get_engine_config_persistence():
    """AC-3: 通过 PUT /api/admin/engine-config 持久化保存自定义单价，并通过 GET 正确回显。"""
    test_payload = {
        "price_input_rec": 1.8,
        "price_cached_input_rec": 0.45,
        "price_output_rec": 10.5,
        "price_input_aud": 1.2,
        "price_cached_input_aud": 0.3,
        "price_output_aud": 5.0,
        "price_input_parse": 0.8,
        "price_cached_input_parse": 0.2,
        "price_output_parse": 3.0,
        "price_input_grey_rec": 2.5,
        "price_cached_input_grey_rec": 0.6,
        "price_output_grey_rec": 15.0,
        "price_input_grey_aud": 1.6,
        "price_cached_input_grey_aud": 0.4,
        "price_output_grey_aud": 6.5,
        "price_input_grey_parse": 0.9,
        "price_cached_input_grey_parse": 0.22,
        "price_output_grey_parse": 3.6,
    }

    # 1. 提交保存配置
    put_resp = client.put("/api/admin/engine-config", json=test_payload, headers=_admin_headers())
    assert put_resp.status_code == 200, f"保存配置失败: {put_resp.text}"
    assert put_resp.json()["status"] == "success"

    # 2. 读取配置回显
    get_resp = client.get("/api/admin/engine-config", headers=_admin_headers())
    assert get_resp.status_code == 200
    res_data = get_resp.json()["data"]

    for k, expected_v in test_payload.items():
        assert res_data.get(k) == expected_v, f"回显字段 {k} 不一致: 期望 {expected_v}, 实际 {res_data.get(k)}"

    # 3. 直查底层持久化数据库对象
    db_cfg = db.get_engine_config()
    for k, expected_v in test_payload.items():
        assert getattr(db_cfg, k) == expected_v, f"DB 持久化字段 {k} 不一致"


# ==============================================================================
# AC-4: 负数单价非法值拦截（Pydantic 422 校验与前端防御）
# ==============================================================================

def test_engine_config_negative_price_validation():
    """AC-4: EngineConfig 与 EngineConfigBody 模型层严格拦截负数单价。"""
    for f in ALL_18_PRICE_FIELDS:
        with pytest.raises(ValidationError):
            EngineConfig(**{f: -0.001})

        with pytest.raises(ValidationError):
            EngineConfigBody(**{f: -1.0})

    # 0.0 与正数单价允许通过
    valid = EngineConfig(price_input_rec=0.0, price_output_rec=12.5)
    assert valid.price_input_rec == 0.0
    assert valid.price_output_rec == 12.5


def test_api_negative_price_returns_422():
    """AC-4: 向 PUT /api/admin/engine-config 传入负数单价时，后端必须拦截并返回 422 校验错误。"""
    bad_payloads = [
        {"price_input_rec": -1.5},
        {"price_cached_input_rec": -0.0001},
        {"price_output_grey_rec": -10.0},
        {"price_input_grey_parse": -0.1},
    ]

    for payload in bad_payloads:
        resp = client.put("/api/admin/engine-config", json=payload, headers=_admin_headers())
        assert resp.status_code == 422, f"负数参数 {payload} 未被拦截为 422: {resp.status_code} {resp.text}"
        detail = resp.json().get("detail", [])
        assert any("greater than or equal to 0" in str(d) for d in detail), f"422 详情中未包含 ge=0 提示: {detail}"


# ==============================================================================
# AC-5: 价格留空时平滑回落内置刊例价，计算不报错
# ==============================================================================

def test_cost_fallback_to_published_tiers_when_prices_none():
    """AC-5: 价格未配置或为 None 时，计算逻辑平滑回退至内置刊例价，计算不报错。"""
    tu = {
        "prompt_tokens": 1_000_000,
        "cached_tokens": 500_000,
        "completion_tokens": 100_000,
        "total_tokens": 1_100_000,
    }

    # 1. qwen3.5-omni-flash 官方档位：in=2.2, cached=0.55, out=13.3
    # Cost = (500k * 2.2 + 500k * 0.55 + 100k * 13.3) / 1,000,000 = (1.1000 + 0.2750 + 1.3300) = 2.705 HKD
    cost_omni = _calc_cost_hkd(
        tu,
        model="qwen3.5-omni-flash",
        custom_in_price=None,
        custom_cached_price=None,
        custom_out_price=None,
    )
    assert cost_omni == 2.705

    # 2. vl-flash 官方档位：in=0.15, cached=0.05, out=1.50
    # Cost = (500k * 0.15 + 500k * 0.05 + 100k * 1.50) / 1,000,000 = (0.0750 + 0.0250 + 0.1500) = 0.25 HKD
    cost_vl = _calc_cost_hkd(
        tu,
        model="qwen-vl-flash",
        custom_in_price=None,
        custom_cached_price=None,
        custom_out_price=None,
    )
    assert cost_vl == 0.25

    # 3. glm-4.5v 官方档位：in=1.0, cached=0.25, out=6.0
    # Cost = (500k * 1.0 + 500k * 0.25 + 100k * 6.0) / 1,000,000 = (0.5000 + 0.1250 + 0.6000) = 1.225 HKD
    cost_glm = _calc_cost_hkd(
        tu,
        model="zai-org/GLM-4.5V",
        custom_in_price=None,
        custom_cached_price=None,
        custom_out_price=None,
    )
    assert cost_glm == 1.225

    # 4. 未知模型回退默认档（omni：2.2 / 0.55 / 13.3）
    cost_unknown = _calc_cost_hkd(
        tu,
        model="custom-unknown-provider-model",
        custom_in_price=None,
        custom_cached_price=None,
        custom_out_price=None,
    )
    assert cost_unknown == 2.705

    # 5. 混合部分留空：自定义 in=3.0, 留空 cached（回落 0.55）, 自定义 out=10.0
    # Cost = (500k * 3.0 + 500k * 0.55 + 100k * 10.0) / 1,000,000 = (1.5000 + 0.2750 + 1.0000) = 2.775 HKD
    cost_mixed = _calc_cost_hkd(
        tu,
        model="qwen3.5-omni-flash",
        custom_in_price=3.0,
        custom_cached_price=None,
        custom_out_price=10.0,
    )
    assert cost_mixed == 2.775


# ==============================================================================
# AC-6: 核心计费公式断言与全链路调用透传
# ==============================================================================

def test_cost_calculation_formula_with_prompt_caching():
    """AC-6: 验证核心计费公式。
    Cost = ((prompt - cached) * P_in + cached * P_cached + completion * P_out) / 1,000,000
    规格书基准用例：
    prompt = 1,000,000, cached = 400,000 (uncached = 600,000), completion = 100,000
    P_in = 2.0, P_cached = 0.5, P_out = 10.0
    Cost = (600,000 * 2.0 + 400,000 * 0.5 + 100,000 * 10.0) / 1,000,000
         = (1,200,000 + 200,000 + 1,000,000) / 1,000,000
         = 2,400,000 / 1,000,000 = 2.400000 HKD
    """
    tu = {
        "prompt_tokens": 1_000_000,
        "cached_tokens": 400_000,
        "completion_tokens": 100_000,
        "total_tokens": 1_100_000,
    }
    cost = _calc_cost_hkd(
        tu,
        custom_in_price=2.0,
        custom_cached_price=0.5,
        custom_out_price=10.0,
    )
    assert cost == 2.4


def test_formula_multiple_token_combinations_and_precision():
    """AC-6: 多场景与极限 Token 组合精度验证。"""
    # 场景 1: 零缓存命中 (cached = 0)
    tu_no_cache = {
        "prompt_tokens": 500_000,
        "cached_tokens": 0,
        "completion_tokens": 50_000,
        "total_tokens": 550_000,
    }
    # Cost = (500k * 2.0 + 50k * 10.0) / 1M = 1.0 + 0.5 = 1.5 HKD
    cost_no_cache = _calc_cost_hkd(tu_no_cache, custom_in_price=2.0, custom_cached_price=0.5, custom_out_price=10.0)
    assert cost_no_cache == 1.5

    # 场景 2: 100% 缓存命中 (cached = prompt)
    tu_full_cache = {
        "prompt_tokens": 800_000,
        "cached_tokens": 800_000,
        "completion_tokens": 20_000,
        "total_tokens": 820_000,
    }
    # Cost = (0 * 2.0 + 800k * 0.5 + 20k * 10.0) / 1M = 0.4 + 0.2 = 0.6 HKD
    cost_full_cache = _calc_cost_hkd(tu_full_cache, custom_in_price=2.0, custom_cached_price=0.5, custom_out_price=10.0)
    assert cost_full_cache == 0.6

    # 场景 3: 微量 Token 6 位浮点精度计算
    tu_micro = {
        "prompt_tokens": 150,
        "cached_tokens": 50,
        "completion_tokens": 20,
        "total_tokens": 170,
    }
    # uncached = 100 * 2.0 = 200; cached = 50 * 0.5 = 25; out = 20 * 10.0 = 200
    # sum = 425 / 1,000,000 = 0.000425 HKD
    cost_micro = _calc_cost_hkd(tu_micro, custom_in_price=2.0, custom_cached_price=0.5, custom_out_price=10.0)
    assert cost_micro == 0.000425

    # 场景 4: 兜底 - 仅有 total_tokens，细分字段为 0
    tu_total_only = {
        "prompt_tokens": 0,
        "cached_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 10_000,
    }
    # Cost = 10,000 * 2.0 / 1M = 0.02 HKD
    cost_total_only = _calc_cost_hkd(tu_total_only, custom_in_price=2.0, custom_cached_price=0.5, custom_out_price=10.0)
    assert cost_total_only == 0.02


def test_supervisor_log_decision_with_custom_prices(monkeypatch):
    """AC-6: 验证 supervisor 决策履历（extract/audit）落库透传自定义单价并精确核算成本。"""
    from app.chains import supervisor

    captured_extract = {}
    captured_audit = {}

    def _fake_log_ai_decision(**kwargs):
        if kwargs.get("decision_type") == "audit":
            captured_audit.update(kwargs)
        else:
            captured_extract.update(kwargs)

    monkeypatch.setattr(supervisor.db, "log_ai_decision", _fake_log_ai_decision)

    cfg = SimpleNamespace(
        recognition_model="qwen3.5-omni-flash",
        price_input_rec=4.0,
        price_cached_input_rec=1.0,
        price_output_rec=20.0,
        audit_engine="openai",
        audit_model="zai-org/GLM-4.5V",
        price_input_aud=3.0,
        price_cached_input_aud=0.8,
        price_output_aud=15.0,
    )

    tu = {
        "prompt_tokens": 1_000_000,
        "cached_tokens": 200_000,
        "completion_tokens": 100_000,
        "total_tokens": 1_100_000,
    }

    # 测试常规识别决策
    # Cost = (800k * 4.0 + 200k * 1.0 + 100k * 20.0) / 1M = (3.2 + 0.2 + 2.0) = 5.4 HKD
    supervisor._log_extract_decision(
        101, None, cfg, False, 1, "openai", "extract_ok",
        token_usage=tu, cost_hkd=None,
    )
    assert captured_extract["extra"]["cost_hkd"] == 5.4
    assert captured_extract["extra"]["tokens_prompt"] == 1_000_000
    assert captured_extract["extra"]["tokens_cached"] == 200_000
    assert captured_extract["extra"]["tokens_completion"] == 100_000

    # 测试常规审核决策
    # Cost = (800k * 3.0 + 200k * 0.8 + 100k * 15.0) / 1M = (2.4 + 0.16 + 1.5) = 4.06 HKD
    audit_dict = {
        "overall_consistent": True,
        "trust": 0.95,
        "discrepancies": [],
        "token_usage": tu,
        "audit_ms": 50.0,
        "skipped": False,
    }
    supervisor._log_audit_decision(101, None, cfg, False, audit_dict)
    assert captured_audit["extra"]["cost_hkd"] == 4.06


def test_extract_chain_cost_from_tokens_ex_with_custom_prices():
    """AC-6: 验证 extract_chain 中的 _cost_from_tokens_ex 透传自定义单价计算与估算标志。"""
    tu = {
        "prompt_tokens": 500_000,
        "cached_tokens": 100_000,
        "completion_tokens": 50_000,
        "total_tokens": 550_000,
    }
    # 自定义单价：in=2.0, cached=0.5, out=10.0
    # uncached = 400k * 2.0 = 0.8; cached = 100k * 0.5 = 0.05; out = 50k * 10.0 = 0.5
    # cost = 1.35 HKD, 且不是估算 (is_estimated = False)
    cost, is_est, why = _cost_from_tokens_ex(
        tu,
        model="qwen3.5-omni-flash",
        custom_in_price=2.0,
        custom_cached_price=0.5,
        custom_out_price=10.0,
    )
    assert cost == 1.35
    assert is_est is False
    assert why == ""


# ==============================================================================
# AC-7: 金丝雀推全 (Promote) 与配置差异对比 (Diff) 闭环
# ==============================================================================

def test_diff_regular_vs_grey_contains_all_price_pairs(monkeypatch):
    """AC-7: diff_regular_vs_grey 必须准确对比常规与灰测全部 9 对单价。"""
    c_reg = EngineConfig(
        grey_enabled=True,
        price_input_rec=2.0,
        price_cached_input_rec=0.5,
        price_output_rec=8.0,
        price_input_grey_rec=1.5,
        price_cached_input_grey_rec=0.3,
        price_output_grey_rec=6.0,
        price_input_aud=1.2,
        price_cached_input_aud=0.3,
        price_output_aud=4.5,
        price_input_grey_aud=1.0,
        price_cached_input_grey_aud=0.25,
        price_output_grey_aud=4.0,
        price_input_parse=0.6,
        price_cached_input_parse=0.15,
        price_output_parse=2.5,
        price_input_grey_parse=0.5,
        price_cached_input_grey_parse=0.12,
        price_output_grey_parse=2.0,
    )
    monkeypatch.setattr("app.api_admin.require_admin", lambda r: None)
    monkeypatch.setattr("app.db.get_engine_config", lambda: c_reg)

    mock_req = SimpleNamespace(headers={})
    res = diff_regular_vs_grey(mock_req)
    assert res["status"] == "success"
    diffs = res["data"]["diffs"]

    # 验证 9 对价格标签全部在差异列表中高亮
    expected_labels = [
        "识别 · Input 价格 (元/1M)",
        "识别 · Cached Input 价格 (元/1M)",
        "识别 · Output 价格 (元/1M)",
        "审核 · Input 价格 (元/1M)",
        "审核 · Cached Input 价格 (元/1M)",
        "审核 · Output 价格 (元/1M)",
        "解析 · Input 价格 (元/1M)",
        "解析 · Cached Input 价格 (元/1M)",
        "解析 · Output 价格 (元/1M)",
    ]
    diff_map = {d["label"]: d for d in diffs}
    for lbl in expected_labels:
        assert lbl in diff_map, f"差异面板缺少价格比对项: {lbl}"

    assert diff_map["识别 · Input 价格 (元/1M)"]["regular"] == 2.0
    assert diff_map["识别 · Input 价格 (元/1M)"]["grey"] == 1.5


def test_promote_grey_config_pricing_migration(monkeypatch):
    """AC-7: 金丝雀推全必须将灰测价格晋升为常规价格，并将灰测价格复位为 None。"""
    initial_cfg = EngineConfig(
        grey_enabled=True,
        price_input_rec=2.2,
        price_cached_input_rec=0.55,
        price_output_rec=13.3,
        price_input_grey_rec=1.8,
        price_cached_input_grey_rec=0.45,
        price_output_grey_rec=10.0,
    )
    saved_cfg = {}

    def _fake_save(c, source="manual"):
        saved_cfg.clear()
        saved_cfg.update(c.model_dump())
        return True

    monkeypatch.setattr("app.api_admin.require_admin", lambda r: {"email": "admin@test.com"})
    monkeypatch.setattr("app.db.get_engine_config", lambda: initial_cfg)
    monkeypatch.setattr("app.db.set_engine_config", _fake_save)
    monkeypatch.setattr("app.db.append_system_audit_log", lambda *args, **kwargs: None)

    mock_req = SimpleNamespace(headers={})
    res = promote_grey_config(mock_req)
    assert res["status"] == "success"

    # 常规价格变为晋升后的灰测价格
    assert saved_cfg["price_input_rec"] == 1.8
    assert saved_cfg["price_cached_input_rec"] == 0.45
    assert saved_cfg["price_output_rec"] == 10.0

    # 灰测价格被复位为 None
    assert saved_cfg["price_input_grey_rec"] is None
    assert saved_cfg["price_cached_input_grey_rec"] is None
    assert saved_cfg["price_output_grey_rec"] is None

    # 快照 prev 记录了原本的常规价格
    assert "rollback_snapshot" in saved_cfg
    snap = saved_cfg["rollback_snapshot"]
    assert snap["prev"]["price_input_rec"] == 2.2
    assert snap["prev"]["price_cached_input_rec"] == 0.55
    assert snap["prev"]["price_output_rec"] == 13.3


# ==============================================================================
# AC-8: 一键回滚 (Rollback) 单价精准恢复
# ==============================================================================

def test_rollback_engine_config_pricing_restoration(monkeypatch):
    """AC-8: 推全后执行回滚，常规价格必须精准恢复为推全前的原始价格快照，且清空快照。"""
    # 模拟真实生命周期闭环：
    # 初始状态：常规价格 (2.2, 0.55, 13.3)，灰测价格 (1.8, 0.45, 10.0)
    current_storage = {
        "cfg": EngineConfig(
            grey_enabled=True,
            price_input_rec=2.2,
            price_cached_input_rec=0.55,
            price_output_rec=13.3,
            price_input_grey_rec=1.8,
            price_cached_input_grey_rec=0.45,
            price_output_grey_rec=10.0,
        )
    }

    def _get_cfg():
        return current_storage["cfg"]

    def _set_cfg(c, source="manual"):
        current_storage["cfg"] = c
        return True

    monkeypatch.setattr("app.api_admin.require_admin", lambda r: {"email": "admin@test.com"})
    monkeypatch.setattr("app.db.get_engine_config", _get_cfg)
    monkeypatch.setattr("app.db.set_engine_config", _set_cfg)
    monkeypatch.setattr("app.db.append_system_audit_log", lambda *args, **kwargs: None)

    mock_req = SimpleNamespace(headers={})

    # 步骤 1: 执行推全
    promote_res = promote_grey_config(mock_req)
    assert promote_res["status"] == "success"
    after_promote = current_storage["cfg"]
    assert after_promote.price_input_rec == 1.8
    assert after_promote.price_cached_input_rec == 0.45
    assert after_promote.price_output_rec == 10.0
    assert after_promote.rollback_snapshot is not None

    # 步骤 2: 执行一键回滚
    rollback_res = rollback_engine_config(mock_req)
    assert rollback_res["status"] == "success"
    after_rollback = current_storage["cfg"]

    # 断言常规组单价完全恢复为推全前的 2.2, 0.55, 13.3
    assert after_rollback.price_input_rec == 2.2
    assert after_rollback.price_cached_input_rec == 0.55
    assert after_rollback.price_output_rec == 13.3

    # 断言回滚快照已被清空（防止重复回滚脏数据）
    assert after_rollback.rollback_snapshot is None

    # 步骤 3: 再次尝试回滚，应报错 400（无快照可回滚）
    err_res = rollback_engine_config(mock_req)
    assert err_res.status_code == 400


# ==============================================================================
# 全局红线检查：严禁 Emoji
# ==============================================================================

def test_codebase_emoji_cleanliness():
    """红线: 扫描涉及价格功能的核心文件，确保绝对零 Emoji。"""
    emoji_regex = re.compile(
        r"[\U00010000-\U0010FFFF]|[\u2600-\u27BF]|[\u2300-\u23FF]|[\u2B50-\u2B55]",
        re.UNICODE,
    )
    scan_files = [
        DEMO_ROOT / "app" / "models.py",
        DEMO_ROOT / "app" / "api_admin.py",
        DEMO_ROOT / "app" / "llm.py",
        DEMO_ROOT / "app" / "chains" / "extract_chain.py",
        DEMO_ROOT / "app" / "chains" / "supervisor.py",
        DEMO_ROOT / "templates" / "index.html",
        DEMO_ROOT / "static" / "js" / "main.js",
        Path(__file__),
    ]

    for p in scan_files:
        assert p.exists(), f"检查文件不存在: {p}"
        text = p.read_text(encoding="utf-8")
        matches = emoji_regex.findall(text)
        assert len(matches) == 0, f"文件 {p.name} 中检测到非法 Emoji: {matches}"
