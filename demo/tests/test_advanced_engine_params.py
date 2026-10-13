# -*- coding: utf-8 -*-
"""系统与引擎配置升级：高级模型推理参数与渐进折叠交互设计全量自动化测试套件（Phase 3 QA 验证）。

严格对照 Phase 1 验收标准清单 (Acceptance Criteria, AC-01 至 AC-18) 与全局工程红线：
- AC-01: 数据契约：EngineConfig 与 EngineConfigBody 完整声明 20 个新增字段，类型与默认值完全对齐；
- AC-02: 数据存储：管理台保存全部 20 个新增高级参数，落库后刷新页面能准确回显各字段设置值；
- AC-03: 边界钳制：max_tokens 传入越界值（如 200 或 30000）能正确钳制在 [512, 16384] 内；
- AC-04: 边界钳制：temperature 传入越界值（如 -0.5 或 0.9）能正确钳制在 [0.0, 0.5] 内，保留两位小数；
- AC-05: 枚举合法：image_detail 仅支持 'high' / 'auto' / 'low'，非法值自动回退 'high'；
- AC-06: 枚举合法：response_format 仅支持 'json_object' / 'json_schema'，非法值自动回退 'json_object'；
- AC-07: 运行时下发：OpenAIChatModel 调用时，请求 payload 显式包含设定的 max_tokens、temperature 与 response_format；
- AC-08: 多模态切片：识别腿调用时，messages 中的 image_url block 正确携带 detail 字段及其配置值；
- AC-09: 缓存隔离：修改高级参数后，_model_cache_key 正确变更，不复用旧参数模型实例；
- AC-10: 渐进折叠：6 个引擎卡片内部均具有高级推理控制参数折叠面板，且默认处于收起 (collapsed) 状态；
- AC-11: 折叠交互：点击折叠面板标题栏，容器顺畅展开/收起，状态文本在 [展开] 与 [收起] 间切换；
- AC-12: 状态摘要：折叠标题栏 Badge 标签实时动态呈现内部参数值；
- AC-13: 防呆警示：当 temperature 输入框数值大于 0.2 时，输入框下方即时呈现预警文字框；<= 0.2 时隐藏；
- AC-14: 配置对比：灰测开启且修改高级参数后，调用 /api/admin/engine-config/diff 返回包含新字段的对比差异项；
- AC-15: 金丝雀推全：调用 /api/admin/engine-config/promote 时，灰测高级参数成功覆盖常规组，快照完整备份，灰测复位默认；
- AC-16: 一键回滚：推全后调用 /api/admin/engine-config/rollback，常规组高级参数 100% 恢复为推全前值；
- AC-17: 红线准则：代码实现、模板 HTML、测试用例与页面输出全局 0 Emoji，全部采用纯文本标签；
- AC-18: 审计与脱敏：高级参数保存、推全与回滚在 audit_logs 中正常留痕，敏感信息（API Key）脱敏不受影响；
- 扩展测试：审核腿温度硬锁定（Gap A3/A4 铁律：side=='aud' 强制 temperature=0.0）与非法类型 422 校验。
"""

import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest
from pydantic import ValidationError
from starlette.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app
from app import db
from app.models import (
    EngineConfig,
    clamp_max_tokens,
    clamp_temperature,
    clamp_image_detail,
)
from app.api_admin import (
    EngineConfigBody,
    diff_regular_vs_grey,
    promote_grey_config,
    rollback_engine_config,
)
from app.llm import (
    OpenAIChatModel,
    _resolve_max_tokens,
    _resolve_temperature,
    _resolve_image_detail,
    _lc_to_openai,
    _model_cache_key,
    build_recognition_model,
    build_audit_model,
    build_parse_model,
)

from f4_frontend_sources import frontend_union  # F4 S4-11: main.js+域文件联合全文

client = TestClient(app)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEMO_ROOT = REPO_ROOT / "demo"

ALL_14_ADV_FIELDS = [
    # 1. 常规识别 (Rec) - 3
    "openai_rec_max_tokens",
    "openai_rec_image_detail",
    "openai_rec_temperature",
    # 2. 常规审核 (Aud) - 2
    "openai_aud_max_tokens",
    "openai_aud_temperature",
    # 3. 常规解析 (Parse) - 2
    "openai_parse_max_tokens",
    "openai_parse_temperature",
    # 4. 灰测识别 (Grey Rec) - 3
    "grey_openai_rec_max_tokens",
    "grey_openai_rec_image_detail",
    "grey_openai_rec_temperature",
    # 5. 灰测审核 (Grey Aud) - 2
    "grey_openai_aud_max_tokens",
    "grey_openai_aud_temperature",
    # 6. 灰测解析 (Grey Parse) - 2
    "grey_openai_parse_max_tokens",
    "grey_openai_parse_temperature",
]
ALL_20_ADV_FIELDS = ALL_14_ADV_FIELDS


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """确保测试运行在独立的测试数据库实例中，防止污染运行环境。"""
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_advanced_engine_isolated.db")
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
# AC-01: 数据契约声明与默认值断言
# ==============================================================================

def test_ac01_engine_config_declarations_and_defaults():
    """AC-01: EngineConfig 与 EngineConfigBody 完整声明 14 个字段，类型与默认值完全对齐。"""
    # 1. 检查 EngineConfig 模型字段反射
    for field_name in ALL_14_ADV_FIELDS:
        assert field_name in EngineConfig.model_fields, f"EngineConfig 缺少字段 {field_name}"

    # 2. 检查 EngineConfigBody 模型字段反射
    for field_name in ALL_14_ADV_FIELDS:
        assert field_name in EngineConfigBody.model_fields, f"EngineConfigBody 缺少字段 {field_name}"

    # 3. 校验 EngineConfig 基线默认值
    default_cfg = EngineConfig()
    # 识别腿
    assert default_cfg.openai_rec_max_tokens == 4096
    assert default_cfg.openai_rec_image_detail == "high"
    assert default_cfg.openai_rec_temperature == 0.0
    # 审核腿
    assert default_cfg.openai_aud_max_tokens == 4096
    assert default_cfg.openai_aud_temperature == 0.0
    # 解析腿
    assert default_cfg.openai_parse_max_tokens == 4096
    assert default_cfg.openai_parse_temperature == 0.0
    # 灰测腿
    assert default_cfg.grey_openai_rec_max_tokens == 4096
    assert default_cfg.grey_openai_rec_image_detail == "high"
    assert default_cfg.grey_openai_rec_temperature == 0.0
    assert default_cfg.grey_openai_aud_max_tokens == 4096
    assert default_cfg.grey_openai_aud_temperature == 0.0
    assert default_cfg.grey_openai_parse_max_tokens == 4096
    assert default_cfg.grey_openai_parse_temperature == 0.0

    # 4. 校验 extra="forbid" 安全拦截非法未知字段
    with pytest.raises(ValidationError):
        EngineConfig(**{"unknown_adv_field_xyz": 123})

    with pytest.raises(ValidationError):
        EngineConfigBody(**{"unknown_adv_field_xyz": 123})


def test_api_type_validation_returns_422():
    """契约严格拦截：向 PUT /api/admin/engine-config 传入非法数据类型时返回 422 报错。"""
    # 1. max_tokens 传入非数值字符串
    r1 = client.put("/api/admin/engine-config", json={"openai_rec_max_tokens": "invalid_not_a_number"}, headers=_admin_headers())
    assert r1.status_code == 422, f"期望 422, 实际 {r1.status_code}: {r1.text}"

    # 2. temperature 传入非浮点字符串
    r2 = client.put("/api/admin/engine-config", json={"openai_rec_temperature": "not_float"}, headers=_admin_headers())
    assert r2.status_code == 422, f"期望 422, 实际 {r2.status_code}: {r2.text}"

    # 3. 自定义价格传入负数 (ge=0 拦截)
    r3 = client.put("/api/admin/engine-config", json={"price_input_rec": -1.0}, headers=_admin_headers())
    assert r3.status_code == 422, f"期望 422, 实际 {r3.status_code}: {r3.text}"


# ==============================================================================
# AC-02: API 保存与回显持久化
# ==============================================================================

def test_ac02_api_save_and_echo_all_14_fields():
    """AC-02: PUT /api/admin/engine-config 写入合法高级参数，GET 100% 完整回显，库内落盘一致。"""
    test_payload = {
        "openai_rec_max_tokens": 8192,
        "openai_rec_image_detail": "auto",
        "openai_rec_temperature": 0.15,
        "openai_aud_max_tokens": 6000,
        "openai_aud_temperature": 0.05,
        "openai_parse_max_tokens": 2048,
        "openai_parse_temperature": 0.1,
        "grey_openai_rec_max_tokens": 12000,
        "grey_openai_rec_image_detail": "low",
        "grey_openai_rec_temperature": 0.25,
        "grey_openai_aud_max_tokens": 5000,
        "grey_openai_aud_temperature": 0.12,
        "grey_openai_parse_max_tokens": 3000,
        "grey_openai_parse_temperature": 0.18,
    }

    # 1. 发起保存
    put_resp = client.put("/api/admin/engine-config", json=test_payload, headers=_admin_headers())
    assert put_resp.status_code == 200, f"保存失败: {put_resp.text}"
    assert put_resp.json()["status"] == "success"

    # 2. 读取回显
    get_resp = client.get("/api/admin/engine-config", headers=_admin_headers())
    assert get_resp.status_code == 200
    res_data = get_resp.json()["data"]

    for k, v in test_payload.items():
        assert res_data.get(k) == v, f"回显字段 {k} 不一致: 期望 {v}, 实际 {res_data.get(k)}"

    # 3. 校验数据库持久化实体
    db_cfg = db.get_engine_config()
    for k, v in test_payload.items():
        assert getattr(db_cfg, k) == v, f"DB 持久化实体字段 {k} 不一致: 期望 {v}, 实际 {getattr(db_cfg, k)}"


# ==============================================================================
# AC-03: max_tokens 边界钳制
# ==============================================================================

def test_ac03_max_tokens_boundary_clamping():
    """AC-03: max_tokens 传入越界值能正确钳制在 [512, 16384] 内。"""
    # 1. 独立工具函数测试
    assert clamp_max_tokens(100) == 512
    assert clamp_max_tokens(511) == 512
    assert clamp_max_tokens(512) == 512
    assert clamp_max_tokens(4096) == 4096
    assert clamp_max_tokens(16384) == 16384
    assert clamp_max_tokens(16385) == 16384
    assert clamp_max_tokens(99999) == 16384
    assert clamp_max_tokens(None) == 4096
    assert clamp_max_tokens("") == 4096
    assert clamp_max_tokens("not_num") == 4096

    # 2. API PUT 越界写入自动钳制测试
    put_resp = client.put("/api/admin/engine-config", json={
        "openai_rec_max_tokens": 10,
        "openai_aud_max_tokens": 50000,
        "grey_openai_rec_max_tokens": 300,
    }, headers=_admin_headers())
    assert put_resp.status_code == 200

    # 3. GET 回显断言
    get_resp = client.get("/api/admin/engine-config", headers=_admin_headers())
    res_data = get_resp.json()["data"]
    assert res_data.get("openai_rec_max_tokens") == 512
    assert res_data.get("openai_aud_max_tokens") == 16384
    assert res_data.get("grey_openai_rec_max_tokens") == 512


# ==============================================================================
# AC-04: temperature 边界钳制与精度格式化
# ==============================================================================

def test_ac04_temperature_boundary_clamping():
    """AC-04: temperature 传入越界值能正确钳制在 [0.0, 0.5] 内，并规整为两位浮点小数。"""
    # 1. 独立工具函数测试
    assert clamp_temperature(-0.5) == 0.0
    assert clamp_temperature(-0.01) == 0.0
    assert clamp_temperature(0.0) == 0.0
    assert clamp_temperature(0.25) == 0.25
    assert clamp_temperature(0.5) == 0.5
    assert clamp_temperature(0.55) == 0.5
    assert clamp_temperature(1.5) == 0.5
    assert clamp_temperature(0.256) == 0.26
    assert clamp_temperature(None) == 0.0
    assert clamp_temperature("") == 0.0

    # 2. API PUT 越界写入测试
    put_resp = client.put("/api/admin/engine-config", json={
        "openai_rec_temperature": -0.8,
        "openai_aud_temperature": 0.99,
        "openai_parse_temperature": 0.333333,
    }, headers=_admin_headers())
    assert put_resp.status_code == 200

    get_resp = client.get("/api/admin/engine-config", headers=_admin_headers())
    res_data = get_resp.json()["data"]
    assert res_data.get("openai_rec_temperature") == 0.0
    assert res_data.get("openai_aud_temperature") == 0.5
    assert res_data.get("openai_parse_temperature") == 0.33


# ==============================================================================
# AC-05: image_detail 枚举合法性与回退
# ==============================================================================

def test_ac05_image_detail_enum_validation():
    """AC-05: image_detail 仅支持 'high' / 'auto' / 'low'，非法值回退 'high'。"""
    assert clamp_image_detail("high") == "high"
    assert clamp_image_detail("auto") == "auto"
    assert clamp_image_detail("low") == "low"
    assert clamp_image_detail("HIGH") == "high"
    assert clamp_image_detail("Auto") == "auto"
    assert clamp_image_detail("LOW") == "low"
    assert clamp_image_detail("ultra_high") == "high"
    assert clamp_image_detail("invalid_detail") == "high"
    assert clamp_image_detail(None) == "high"
    assert clamp_image_detail("") == "high"

    # API PUT 验证
    put_resp = client.put("/api/admin/engine-config", json={
        "openai_rec_image_detail": "invalid_detail_str",
        "grey_openai_rec_image_detail": "auto",
    }, headers=_admin_headers())
    assert put_resp.status_code == 200

    get_resp = client.get("/api/admin/engine-config", headers=_admin_headers())
    res_data = get_resp.json()["data"]
    assert res_data.get("openai_rec_image_detail") == "high"
    assert res_data.get("grey_openai_rec_image_detail") == "auto"


# ==============================================================================
# ==============================================================================
# AC-06: 彻底移除 response_format 依赖并安全丢弃遗留字段
# ==============================================================================

def test_ac06_response_format_removed_and_ignored():
    """AC-06: response_format 已从数据模型中彻底剥离，旧快照/配置传入时自动安全消化，模型调用不附带该协议。"""
    # 1. 验证字段不在 EngineConfig / EngineConfigBody 模型中
    assert "openai_rec_response_format" not in EngineConfig.model_fields
    assert "openai_rec_response_format" not in EngineConfigBody.model_fields

    # 2. 验证传入遗留 response_format 字段被 _REMOVED_CLI_FIELDS 安全过滤，不抛 extra forbid 异常
    cfg = EngineConfig(openai_rec_response_format="json_schema", openai_rec_max_tokens=2048)
    assert not hasattr(cfg, "openai_rec_response_format")
    assert cfg.openai_rec_max_tokens == 2048


# ==============================================================================
# AC-07: 运行时下发 OpenAI 请求 Payload 透传（不含 response_format）
# ==============================================================================

def test_ac07_openai_payload_runtime_dispatch():
    """AC-07: OpenAIChatModel 调用时，请求 payload 显式包含设定的 max_tokens 与 temperature，不含 response_format。"""
    model = OpenAIChatModel(
        base_url="https://api.example.com/v1",
        api_key="sk-test-key",
        model="gpt-4o-mini",
        call_timeout=30,
        max_tokens=8192,
        temperature=0.2,
    )

    from langchain_core.messages import HumanMessage
    messages = [HumanMessage(content="Hello test")]

    captured_payload = {}

    def mock_requests_post(url, headers=None, json=None, timeout=None, allow_redirects=False):
        nonlocal captured_payload
        captured_payload = json
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": '{"result": "ok"}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        return mock_resp

    with patch("requests.post", side_effect=mock_requests_post):
        res = model.invoke(messages)
        assert res.content == '{"result": "ok"}'

    assert captured_payload.get("max_tokens") == 8192
    assert captured_payload.get("temperature") == 0.2
    assert "response_format" not in captured_payload, "payload 严禁包含已废弃的 response_format"


# ==============================================================================
# AC-08: 多模态切片 detail 字段注入
# ==============================================================================

def test_ac08_multimodal_vision_slice_detail():
    """AC-08: 识别腿调用时，messages 中的 image_url block 正确携带 detail 字段及其配置值。"""
    from langchain_core.messages import HumanMessage

    msg_high = HumanMessage(content=[
        {"type": "text", "text": "识别发票"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,123"}},
    ])

    # detail='high'
    res_high = _lc_to_openai(msg_high, detail="high")
    assert res_high["role"] == "user"
    assert len(res_high["content"]) == 2
    assert res_high["content"][1]["type"] == "image_url"
    assert res_high["content"][1]["image_url"]["detail"] == "high"

    # detail='auto'
    res_auto = _lc_to_openai(msg_high, detail="auto")
    assert res_auto["content"][1]["image_url"]["detail"] == "auto"

    # detail='low'
    res_low = _lc_to_openai(msg_high, detail="low")
    assert res_low["content"][1]["image_url"]["detail"] == "low"

    # 非法值自动回退为 high
    res_invalid = _lc_to_openai(msg_high, detail="ultra_super")
    assert res_invalid["content"][1]["image_url"]["detail"] == "high"


# ==============================================================================
# AC-09: 模型缓存键防串扰隔离
# ==============================================================================

def test_ac09_model_cache_key_isolation():
    """AC-09: 修改高级参数后，_model_cache_key 正确变更，不复用旧参数模型实例。"""
    base_args = ("openai", "qwen3.5-omni-flash", "https://dashscope.aliyuncs.com", "sk-123", 30, "")
    price_args = (None, None, None)

    key1 = _model_cache_key(*(base_args + price_args + (4096, 0.0, "high")))
    key2 = _model_cache_key(*(base_args + price_args + (8192, 0.0, "high")))
    key3 = _model_cache_key(*(base_args + price_args + (4096, 0.1, "high")))
    key4 = _model_cache_key(*(base_args + price_args + (4096, 0.0, "low")))

    assert key1 != key2, "max_tokens 变化未引起缓存 Key 变化"
    assert key1 != key3, "temperature 变化未引起缓存 Key 变化"
    assert key1 != key4, "image_detail 变化未引起缓存 Key 变化"


# ==============================================================================
# 审核腿温度安全硬锁定（Gap A3/A4 铁律验证）
# ==============================================================================

def test_ac_audit_model_temperature_hard_locking():
    """审核引擎安全硬锁定：无论配置温度多大，审核模型运行时 temperature 恒为 0.0。"""
    cfg = EngineConfig(
        openai_aud_base_url="https://api.openai.com/v1",
        openai_aud_api_key="sk-test-audit-key",
        openai_aud_temperature=0.45,
        grey_openai_aud_base_url="https://api.openai.com/v1",
        grey_openai_aud_api_key="sk-test-grey-audit-key",
        grey_openai_aud_temperature=0.35,
    )

    # 1. 验证解析函数强制返回 0.0
    assert _resolve_temperature(cfg, side="aud", use_grey=False) == 0.0
    assert _resolve_temperature(cfg, side="aud", use_grey=True) == 0.0

    # 2. 识别腿正常返回设定值
    cfg_rec = EngineConfig(openai_rec_temperature=0.25)
    assert _resolve_temperature(cfg_rec, side="rec", use_grey=False) == 0.25

    # 3. 验证构建的审核模型实例属性
    model_aud = build_audit_model(cfg=cfg)
    assert model_aud.temperature == 0.0, f"审核模型 temperature 必须为 0.0, 实际 {model_aud.temperature}"


# ==============================================================================
# AC-10 至 AC-13: 前端 DOM 结构与交互设计验证
# ==============================================================================

def test_ac10_progressive_disclosure_dom_containers():
    """AC-10: 6 个引擎卡片内部均具有高级推理控制参数折叠面板，且默认处于收起 (collapsed) 状态。"""
    html_file = DEMO_ROOT / "templates" / "index.html"
    assert html_file.exists(), "index.html 不存在"
    content = html_file.read_text(encoding="utf-8")

    expected_drawers = [
        "adminAdvParams_rec",
        "adminAdvParams_aud",
        "adminAdvParams_parse",
        "adminAdvParams_grey_rec",
        "adminAdvParams_grey_aud",
        "adminAdvParams_grey_parse",
    ]

    for drawer_id in expected_drawers:
        assert f'id="{drawer_id}"' in content, f"未找到抽屉容器 {drawer_id}"
        # 断言带有 collapsed 类
        pattern = rf'id="{drawer_id}"\s+class="[^"]*collapsed[^"]*"'
        assert re.search(pattern, content), f"抽屉容器 {drawer_id} 默认未包含 collapsed 类"


def test_ac11_folding_interaction_logic():
    """AC-11: main.js 中包含 toggleAdvParams 并在 [展开] 与 [收起] 间切换。"""
    content = frontend_union()  # F4 S4-11: main.js+域文件联合全文

    assert "function toggleAdvParams(legId)" in content
    assert "window.toggleAdvParams = toggleAdvParams" in content
    assert ("toggleText.textContent = '收起'" in content) or ("toggleText.textContent = '[收起]'" in content)
    assert ("toggleText.textContent = '展开'" in content) or ("toggleText.textContent = '[展开]'" in content)


def test_ac12_dynamic_badge_text_formatting():
    """AC-12: main.js 中 updateAdvBadge 支持动态格式化识别腿与其他腿的微缩 Badge 文本，满足统一前缀与顺序（思考强度、最大Token、视觉切片、采样温度）。"""
    content = frontend_union()  # F4 S4-11: main.js+域文件联合全文

    assert "function updateAdvBadge(legId)" in content
    assert "window.updateAdvBadge = updateAdvBadge" in content
    # 统一四段式微缩 Badge（思考强度、最大Token、视觉切片、采样温度）
    assert "badge.textContent = `思考强度:${effortLabel} · 最大Token:${tokens} · 视觉切片:${detailLabel} · 采样温度:${tempStr}`" in content


def test_ac13_hallucination_warning_logic():
    """AC-13: checkTempWarn 在温度 > 0.2 时展示 warning，<= 0.2 时隐藏。"""
    content = frontend_union()  # F4 S4-11: main.js+域文件联合全文

    assert "function checkTempWarn(legId)" in content
    assert "window.checkTempWarn = checkTempWarn" in content
    assert "val > 0.2" in content
    assert "warnEl.classList.remove('hide')" in content
    assert "warnEl.classList.add('hide')" in content


def test_adv_params_header_title_renamed_and_pricing_integrated():
    """验证 6 处抽屉标题统一为'高级参数'且计费单价 engine-inline-economics 整体纳入抽屉主体 adminAdvBody_{leg}。"""
    from bs4 import BeautifulSoup
    html_file = DEMO_ROOT / "templates" / "index.html"
    assert html_file.exists()
    content = html_file.read_text(encoding="utf-8")
    assert "高级工程参数" not in content, "HTML 模板中严禁残留 '高级工程参数'"
    soup = BeautifulSoup(content, "html.parser")

    legs = ["rec", "aud", "parse", "grey_rec", "grey_aud", "grey_parse"]
    for leg in legs:
        drawer = soup.find(id=f"adminAdvParams_{leg}")
        assert drawer is not None, f"未找到抽屉容器 adminAdvParams_{leg}"
        header = drawer.find("div", class_="engine-adv-header")
        assert header is not None, f"卡片 {leg} 缺少 engine-adv-header"
        assert "高级参数" in header.text, f"卡片 {leg} 抽屉标题必须包含 '高级参数'"

        adv_body = drawer.find(id=f"adminAdvBody_{leg}")
        assert adv_body is not None, f"卡片 {leg} 缺少抽屉主体 adminAdvBody_{leg}"
        eco_block = adv_body.find("div", class_="engine-inline-economics")
        assert eco_block is not None, f"卡片 {leg} 计费单价容器 engine-inline-economics 必须位于 adminAdvBody_{leg} 内部"


# ==============================================================================
# AC-14: 金丝雀配置对比 (Diff)
# ==============================================================================

def test_ac14_canary_diff_advanced_params():
    """AC-14: 灰测开启且修改高级参数后，调用 /api/admin/engine-config/diff 返回包含新字段的对比差异项。"""
    # 1. 开启灰测并设置差异值
    diff_payload = {
        "grey_enabled": True,
        "grey_percent": 30,
        "grey_openai_rec_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "grey_openai_rec_api_key": "sk-grey-test-key",
        "grey_openai_rec_model": "qwen3.5-omni-flash",
        "openai_rec_max_tokens": 4096,
        "grey_openai_rec_max_tokens": 8192,
        "openai_rec_image_detail": "high",
        "grey_openai_rec_image_detail": "low",
        "openai_rec_temperature": 0.0,
        "grey_openai_rec_temperature": 0.25,
    }
    put_resp = client.put("/api/admin/engine-config", json=diff_payload, headers=_admin_headers())
    assert put_resp.status_code == 200, f"设置灰测配置失败: {put_resp.text}"

    # 2. 调用 diff 接口
    diff_resp = client.get("/api/admin/engine-config/diff", headers=_admin_headers())
    assert diff_resp.status_code == 200
    diff_data = diff_resp.json()["data"]

    diff_labels = [d["label"] for d in diff_data["diffs"]]
    assert "识别 · Max Tokens" in diff_labels
    assert "识别 · 视觉切片粒度" in diff_labels
    assert "识别 · 采样温度" in diff_labels

    rec_token_diff = next(d for d in diff_data["diffs"] if d["label"] == "识别 · Max Tokens")
    assert rec_token_diff["regular"] == 4096
    assert rec_token_diff["grey"] == 8192


# ==============================================================================
# AC-15: 金丝雀一键推全 (Promote)
# ==============================================================================

def test_ac15_canary_promote_and_snapshot():
    """AC-15: 调用 /api/admin/engine-config/promote 时，灰测高级参数成功覆盖常规组，快照完整备份，灰测复位默认。"""
    # 1. 设置初始常规组与灰测组配置
    init_payload = {
        "grey_enabled": True,
        "grey_percent": 50,
        "grey_openai_rec_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "grey_openai_rec_api_key": "sk-grey-test-key",
        "grey_openai_rec_model": "qwen3.5-omni-flash",
        # 常规组
        "openai_rec_max_tokens": 4096,
        "openai_rec_image_detail": "high",
        "openai_rec_temperature": 0.0,
        # 灰测组 (待推全的高级参数)
        "grey_openai_rec_max_tokens": 8192,
        "grey_openai_rec_image_detail": "auto",
        "grey_openai_rec_temperature": 0.15,
        "grey_openai_aud_max_tokens": 6000,
        "grey_openai_aud_temperature": 0.08,
    }
    put_resp = client.put("/api/admin/engine-config", json=init_payload, headers=_admin_headers())
    assert put_resp.status_code == 200, f"设置初始灰测配置失败: {put_resp.text}"

    # 2. 调用推全
    promote_resp = client.put("/api/admin/engine-config/promote", headers=_admin_headers())
    assert promote_resp.status_code == 200, f"推全失败: {promote_resp.text}"

    # 3. 检查常规组是否成功晋升
    get_resp = client.get("/api/admin/engine-config", headers=_admin_headers())
    cfg_data = get_resp.json()["data"]

    assert cfg_data.get("openai_rec_max_tokens") == 8192
    assert cfg_data.get("openai_rec_image_detail") == "auto"
    assert cfg_data.get("openai_rec_temperature") == 0.15
    assert cfg_data.get("openai_aud_max_tokens") == 6000
    assert cfg_data.get("openai_aud_temperature") == 0.08

    # 4. 检查灰测组是否重置为默认基线值且关闭
    assert cfg_data.get("grey_enabled") is False
    assert cfg_data.get("grey_percent") == 0
    assert cfg_data.get("grey_openai_rec_max_tokens") == 4096
    assert cfg_data.get("grey_openai_rec_image_detail") == "high"
    assert cfg_data.get("grey_openai_rec_temperature") == 0.0

    # 5. 检查回滚快照 rollback_snapshot 是否正确保留了老配置
    snap = db.get_engine_config().rollback_snapshot
    assert snap is not None
    assert "prev" in snap
    prev = snap["prev"]
    assert prev.get("openai_rec_max_tokens") == 4096
    assert prev.get("openai_rec_image_detail") == "high"
    assert prev.get("openai_rec_temperature") == 0.0


# ==============================================================================
# AC-16: 一键回滚 (Rollback)
# ==============================================================================

def test_ac16_canary_rollback_restoration():
    """AC-16: 推全后调用 /api/admin/engine-config/rollback，常规组高级参数 100% 恢复为推全前值。"""
    # 1. 准备并执行推全
    put_resp = client.put("/api/admin/engine-config", json={
        "grey_enabled": True,
        "grey_percent": 50,
        "grey_openai_rec_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "grey_openai_rec_api_key": "sk-grey-test-key",
        "grey_openai_rec_model": "qwen3.5-omni-flash",
        "openai_rec_max_tokens": 4096,
        "openai_rec_image_detail": "high",
        "grey_openai_rec_max_tokens": 16384,
        "grey_openai_rec_image_detail": "low",
    }, headers=_admin_headers())
    assert put_resp.status_code == 200, f"设置灰测失败: {put_resp.text}"

    promote_resp = client.put("/api/admin/engine-config/promote", headers=_admin_headers())
    assert promote_resp.status_code == 200

    # 确认推全已生效
    cfg_mid = client.get("/api/admin/engine-config", headers=_admin_headers()).json()["data"]
    assert cfg_mid.get("openai_rec_max_tokens") == 16384
    assert cfg_mid.get("openai_rec_image_detail") == "low"

    # 2. 执行一键回滚
    rollback_resp = client.put("/api/admin/engine-config/rollback", headers=_admin_headers())
    assert rollback_resp.status_code == 200, f"回滚失败: {rollback_resp.text}"

    # 3. 确认常规组参数完全恢复为推全前值
    cfg_after = client.get("/api/admin/engine-config", headers=_admin_headers()).json()["data"]
    assert cfg_after.get("openai_rec_max_tokens") == 4096
    assert cfg_after.get("openai_rec_image_detail") == "high"

    # 4. 再次调用回滚应提示无快照
    rollback_again = client.put("/api/admin/engine-config/rollback", headers=_admin_headers())
    assert rollback_again.status_code == 400


# ==============================================================================
# AC-17: 全局 0 Emoji 红线核验
# ==============================================================================

def test_ac17_global_zero_emoji_scan():
    """AC-17: 代码实现、模板 HTML、测试用例与样式全局 0 Emoji，全部采用纯文本标签。"""
    files_to_scan = [
        DEMO_ROOT / "app" / "models.py",
        DEMO_ROOT / "app" / "db.py",
        DEMO_ROOT / "app" / "api_admin.py",
        DEMO_ROOT / "app" / "llm.py",
        DEMO_ROOT / "app" / "chains" / "supervisor.py",
        DEMO_ROOT / "templates" / "index.html",
        DEMO_ROOT / "static" / "css" / "style.css",
        DEMO_ROOT / "static" / "js" / "main.js",
        Path(__file__),
    ]

    emoji_pattern = re.compile(
        "["
        "\U00010000-\U0010ffff"
        "\u2600-\u26ff"
        "\u2700-\u27bf"
        "]",
        flags=re.UNICODE,
    )

    for fpath in files_to_scan:
        assert fpath.exists(), f"文件不存在: {fpath}"
        content = fpath.read_text(encoding="utf-8")
        matches = emoji_pattern.findall(content)
        assert len(matches) == 0, f"文件 {fpath.name} 包含非法 Emoji: {matches}"


# ==============================================================================
# AC-18: 审计日志与脱敏
# ==============================================================================

def test_ac18_audit_logging_and_desensitization():
    """AC-18: 高级参数推全在 audit_logs 中正常留痕，API Key 脱敏不受影响。"""
    # 1. 触发一次推全以生成审计日志
    put_resp = client.put("/api/admin/engine-config", json={
        "grey_enabled": True,
        "grey_percent": 50,
        "grey_openai_rec_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "grey_openai_rec_api_key": "sk-grey-key",
        "grey_openai_rec_model": "qwen3.5-omni-flash",
        "openai_rec_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "openai_rec_api_key": "sk-real-secret-12345",
        "grey_openai_rec_max_tokens": 8192,
    }, headers=_admin_headers())
    assert put_resp.status_code == 200, f"设置灰测失败: {put_resp.text}"

    promote_resp = client.put("/api/admin/engine-config/promote", headers=_admin_headers())
    assert promote_resp.status_code == 200, f"推全失败: {promote_resp.text}"

    # 2. 读取审计日志
    audit_logs = db.read_system_audit_log()
    actions = [log.get("action") for log in audit_logs]
    assert "promote_grey_config" in actions

    # 3. 验证 API Key 在 GET 中被掩码脱敏
    get_resp = client.get("/api/admin/engine-config", headers=_admin_headers())
    res_data = get_resp.json()["data"]
    masked_key = res_data.get("openai_rec_api_key", "")
    assert "****" in masked_key or masked_key == "********"
    assert "sk-real-secret-12345" not in masked_key
