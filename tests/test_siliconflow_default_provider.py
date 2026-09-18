# 默认 provider 装配（用户决策 2026-09-15）：识别腿默认 DashScope qwen3.5-omni-flash，
# 审核腿默认 SiliconFlow zai-org/GLM-4.5V（跨厂商异构，Gap A3）。每次重启按 .env 重新装配双腿。
# 历史文件名为 2026-08-30 的「SF 单腿默认」决策所留，语义已更新为双腿分离，文件名保留。
import os
import tempfile

import pytest

os.environ.setdefault("AUTH_ENABLED", "0")

_tmpdir = tempfile.mkdtemp(prefix="default_provider_")
os.environ["DB_PATH"] = os.path.join(_tmpdir, "t.db")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "demo", ".env"))

from app import db  # noqa: E402
from app.models import EngineConfig  # noqa: E402

_DASHSCOPE_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_SF_BASE = "https://api.siliconflow.cn/v1"
_DASHSCOPE_KEY = "sk-dashscope-default"
_SF_KEY = "sk-test-default-provider"
_REC_MODEL = "qwen3.5-omni-flash"


@pytest.fixture(autouse=True)
def _split_env(monkeypatch):
    """隔离环境变量：识别腿 DashScope、审核腿 SiliconFlow（健康检查打桩为 True）。

    同时打桩 load_dotenv——否则 hydrate 内部会把 demo/.env 的真实密钥装回
    os.environ，破坏「双密钥皆不可用」分支的隔离性。
    """
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("DASHSCOPE_API_KEY", _DASHSCOPE_KEY)
    monkeypatch.setenv("DASHSCOPE_BASE_URL", _DASHSCOPE_BASE)
    monkeypatch.setenv("QWEN_VL_MODEL", _REC_MODEL)
    monkeypatch.setenv("SILICONFLOW_API_KEY", _SF_KEY)
    monkeypatch.setenv("SILICONFLOW_BASE_URL", _SF_BASE)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.setattr(db, "_openai_gateway_healthy", lambda key, base="": True)
    yield


def _seed_recognition(engine, api_key, base_url="", model=""):
    """把 DB 里的识别腿改写成非 DashScope 形态，模拟管理台临时切换。"""
    cfg = db.get_engine_config()
    cfg.recognition_engine = engine
    cfg.openai_rec_api_key = api_key
    cfg.openai_rec_base_url = base_url
    cfg.openai_rec_model = model
    db.set_engine_config(cfg)


def _assert_split_legs(cfg):
    """识别腿 → DashScope qwen3.5-omni-flash；审核腿 → SF GLM-4.5V（跨厂商异构）。"""
    # 识别腿（DashScope）
    assert cfg.recognition_engine == "openai"
    assert cfg.openai_rec_base_url == _DASHSCOPE_BASE
    assert cfg.openai_rec_api_key == _DASHSCOPE_KEY
    assert cfg.openai_rec_model == _REC_MODEL
    assert cfg.recognition_model == _REC_MODEL
    # 审核腿（SiliconFlow，与识别腿跨厂商异构，Gap A3）
    assert cfg.audit_engine == "openai"
    assert cfg.openai_aud_base_url == _SF_BASE
    assert cfg.openai_aud_api_key == _SF_KEY
    assert cfg.openai_aud_model == "zai-org/GLM-4.5V"
    assert cfg.audit_model == "zai-org/GLM-4.5V"
    # 双腿必须不同 provider 且不同密钥（防 base/key 跨 provider 错配）
    assert cfg.openai_rec_base_url != cfg.openai_aud_base_url
    assert cfg.openai_rec_api_key != cfg.openai_aud_api_key


def test_recognition_leg_defaults_to_dashscope(monkeypatch):
    """DB 已存 opencode 识别引擎，重启水合后装配为 DashScope qwen3.5-omni-flash。"""
    _seed_recognition("opencode", "", model="opencode/mimo-v2.5-free")
    db.hydrate_engine_config_from_env()
    _assert_split_legs(db.get_engine_config())


def test_dashscope_overwrites_db_siliconflow_config(monkeypatch):
    """DB 存的是 SiliconFlow 配置，重启后识别腿仍回到 DashScope 默认通道。"""
    _seed_recognition("openai", _SF_KEY, base_url=_SF_BASE,
                      model="Qwen/Qwen3-VL-32B-Instruct")
    db.hydrate_engine_config_from_env()
    _assert_split_legs(db.get_engine_config())


def test_audit_leg_forced_grey_untouched(monkeypatch):
    """双腿装配：审核腿装回 SF；灰测组自定义配置保持现值。"""
    cfg = db.get_engine_config()
    cfg.audit_engine = "openai"
    cfg.audit_model = "zai-org/GLM-4.5V"
    cfg.grey_recognition_engine = "openai"
    cfg.grey_recognition_model = "Qwen/Qwen2.5-VL-72B-Instruct"
    db.set_engine_config(cfg)

    db.hydrate_engine_config_from_env()
    after = db.get_engine_config()
    _assert_split_legs(after)
    assert after.audit_engine == "openai"
    assert after.audit_model == "zai-org/GLM-4.5V"
    assert after.grey_recognition_engine == "openai"
    assert after.grey_recognition_model == "Qwen/Qwen2.5-VL-72B-Instruct"


def test_legacy_cli_in_grey_leg_normalized(monkeypatch):
    """灰测组若残留历史 codebuddy CLI 配置，水合时自动归一并跟随识别腿 provider。"""
    import json
    s = db.get_session()
    try:
        row = s.get(db._AppSettingRow, "engine_config")
        val = json.loads(row.value) if row else {}
        val["grey_recognition_engine"] = "codebuddy"
        val["grey_recognition_model"] = "minimax-m3-pay"
        if not row:
            row = db._AppSettingRow(key="engine_config")
            s.add(row)
        row.value = json.dumps(val)
        s.commit()
    finally:
        s.close()

    db.hydrate_engine_config_from_env()
    after = db.get_engine_config()
    assert after.grey_recognition_engine == "openai"
    # 跟随识别腿模型，避免 DashScope base + SF 模型名的错配
    assert after.grey_recognition_model == _REC_MODEL


def test_pure_default_config_is_heterogeneous(monkeypatch):
    """纯默认即异构：识别腿 DashScope qwen3.5-omni-flash / 审核腿 SF GLM-4.5V（Gap A3）。"""
    for var in ("AUDIT_ENGINE", "AUDIT_MODEL", "SILICONFLOW_AUDIT_MODEL",
                "OPENCODE_AUDIT_MODEL", "OPENAI_AUD_MODEL", "OPENAI_MODEL",
                "OPENAI_BASE_URL", "OPENAI_API_KEY", "SILICONFLOW_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    cfg = EngineConfig()
    assert cfg.recognition_engine == "openai"
    assert cfg.recognition_model == _REC_MODEL
    assert cfg.openai_rec_base_url == _DASHSCOPE_BASE
    assert cfg.audit_engine == "openai"
    assert cfg.audit_model == "zai-org/GLM-4.5V"
    assert cfg.audit_model != "opencode/mimo-v2.5-free"
    # 识别与审核必须不同家族
    assert cfg.recognition_model != cfg.audit_model


def test_siliconflow_unhealthy_audit_falls_back_to_dashscope(monkeypatch):
    """SF 健康检查失败 → 仅审核腿降级 DashScope；识别腿不受影响（仍 DashScope）。"""
    monkeypatch.setattr(db, "_openai_gateway_healthy", lambda key, base="": False)
    _seed_recognition("opencode", "", model="opencode/mimo-v2.5-free")

    db.hydrate_engine_config_from_env()
    cfg = db.get_engine_config()
    assert cfg.recognition_engine == "openai"
    assert cfg.openai_rec_base_url == _DASHSCOPE_BASE
    assert cfg.openai_rec_model == _REC_MODEL
    # 审核腿降级 DashScope（双腿同家族，异构性弱化已在日志提示）
    assert cfg.audit_engine == "openai"
    assert cfg.openai_aud_base_url == _DASHSCOPE_BASE
    assert cfg.openai_aud_model == "qwen3-vl-plus"


def test_recognition_leg_falls_back_to_siliconflow_without_dashscope_key(monkeypatch):
    """DashScope 密钥缺失 → 识别腿降级 SF Qwen3-VL；审核腿仍 SF GLM-4.5V。"""
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    _seed_recognition("opencode", "", model="opencode/mimo-v2.5-free")

    db.hydrate_engine_config_from_env()
    cfg = db.get_engine_config()
    assert cfg.recognition_engine == "openai"
    assert cfg.openai_rec_base_url == _SF_BASE
    assert cfg.openai_rec_api_key == _SF_KEY
    assert cfg.openai_rec_model == "Qwen/Qwen3-VL-32B-Instruct"
    assert cfg.audit_engine == "openai"
    assert cfg.openai_aud_base_url == _SF_BASE
    assert cfg.openai_aud_model == "zai-org/GLM-4.5V"


def test_no_provider_available_keeps_existing_config(monkeypatch):
    """两套密钥皆不可用 → 保持现有有效配置不动。"""
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _seed_recognition("openai", "sk-custom-existing",
                      base_url="https://custom.local/v1", model="custom-model")

    db.hydrate_engine_config_from_env()
    cfg = db.get_engine_config()
    assert cfg.recognition_engine == "openai"
    assert cfg.openai_rec_api_key == "sk-custom-existing"
    assert cfg.openai_rec_base_url == "https://custom.local/v1"
    assert cfg.openai_rec_model == "custom-model"
