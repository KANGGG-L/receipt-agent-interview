# SiliconFlow 默认 provider（用户决策 2026-08-30）：每次重启强制把识别腿装配回
# SiliconFlow 通道——即使 DB 已存其他引擎配置。审核腿与灰测组不受影响。
import os
import tempfile

import pytest

os.environ.setdefault("AUTH_ENABLED", "0")

_tmpdir = tempfile.mkdtemp(prefix="sf_default_")
os.environ["DB_PATH"] = os.path.join(_tmpdir, "t.db")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "demo", ".env"))

from app import db  # noqa: E402


@pytest.fixture(autouse=True)
def _sf_env(monkeypatch):
    """隔离环境变量：默认给出健康的 SiliconFlow 通道（健康检查打桩为 True）。

    同时打桩 load_dotenv——否则 hydrate 内部会把 demo/.env 的真实密钥装回
    os.environ，破坏「双密钥皆不可用」分支的隔离性。
    """
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-test-default-provider")
    monkeypatch.setenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "Qwen/Qwen3-VL-32B-Thinking")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setattr(db, "_openai_gateway_healthy", lambda key: True)
    yield


def _seed_recognition(monkeypatch, engine, api_key, base_url="", model=""):
    """把 DB 里的识别腿改写成非 SiliconFlow 形态，模拟管理台临时切换。"""
    cfg = db.get_engine_config()
    cfg.recognition_engine = engine
    cfg.openai_rec_api_key = api_key
    cfg.openai_rec_base_url = base_url
    cfg.openai_rec_model = model
    db.set_engine_config(cfg)


def _assert_forced_to_siliconflow(cfg):
    assert cfg.recognition_engine == "openai"
    assert cfg.openai_rec_base_url == "https://api.siliconflow.cn/v1"
    assert cfg.openai_rec_api_key == "sk-test-default-provider"
    assert cfg.openai_rec_model == "Qwen/Qwen3-VL-32B-Thinking"


def test_force_siliconflow_even_when_db_stores_opencode(monkeypatch):
    """DB 已存 opencode 识别引擎，重启水合后仍强制回 SiliconFlow（用户决策）。"""
    _seed_recognition(monkeypatch, "opencode", "", model="opencode/mimo-v2.5-free")
    db.hydrate_engine_config_from_env()
    _assert_forced_to_siliconflow(db.get_engine_config())


def test_force_siliconflow_overwrites_other_openai_config(monkeypatch):
    """DB 存的是其他 openai 兼容配置（如 DashScope），重启后同样回到 SiliconFlow。"""
    _seed_recognition(monkeypatch, "openai", "sk-dashscope-key",
                      base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                      model="qwen3-vl-flash")
    db.hydrate_engine_config_from_env()
    _assert_forced_to_siliconflow(db.get_engine_config())


def test_audit_leg_and_grey_config_untouched(monkeypatch):
    """强制只作用于识别腿：审核腿与灰测组配置保持 DB 现值。"""
    cfg = db.get_engine_config()
    cfg.audit_engine = "opencode"
    cfg.audit_model = "opencode/mimo-v2.5-free"
    cfg.grey_recognition_engine = "codebuddy"
    cfg.grey_recognition_model = "minimax-m3-pay"
    db.set_engine_config(cfg)

    db.hydrate_engine_config_from_env()
    after = db.get_engine_config()
    _assert_forced_to_siliconflow(after)
    assert after.audit_engine == "opencode"
    assert after.audit_model == "opencode/mimo-v2.5-free"
    assert after.grey_recognition_engine == "codebuddy"
    assert after.grey_recognition_model == "minimax-m3-pay"


def test_siliconflow_unhealthy_falls_back_to_dashscope(monkeypatch):
    """SiliconFlow 健康检查失败 → 自动降级 DashScope（不静默回落本地 CLI）。"""
    monkeypatch.setattr(db, "_openai_gateway_healthy", lambda key: False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-dashscope-fallback")
    _seed_recognition(monkeypatch, "opencode", "", model="opencode/mimo-v2.5-free")

    db.hydrate_engine_config_from_env()
    cfg = db.get_engine_config()
    assert cfg.recognition_engine == "openai"
    assert cfg.openai_rec_base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert cfg.openai_rec_api_key == "sk-dashscope-fallback"


def test_no_provider_available_keeps_existing_config(monkeypatch):
    """两套密钥皆不可用 → 保持现有配置不动（不启用本地 CLI 之外的猜测）。"""
    monkeypatch.setattr(db, "_openai_gateway_healthy", lambda key: False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _seed_recognition(monkeypatch, "opencode", "", model="opencode/mimo-v2.5-free")

    db.hydrate_engine_config_from_env()
    cfg = db.get_engine_config()
    assert cfg.recognition_engine == "opencode"
