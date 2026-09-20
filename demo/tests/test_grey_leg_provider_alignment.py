# -*- coding: utf-8 -*-
"""T12：前端预设已下架模型 + 灰测识别腿 provider 与常规腿对齐。

为什么要有这组用例：
1. SiliconFlow 已于 2026-09 下架 Qwen/Qwen2.5-VL-7B-Instruct，但前端 OPENAI_PRESETS 与
   实验弹窗 placeholder 仍在用它。db.py 的 _SF_DEFAULT_* 常量是单一事实源，前端预设必须与之一致。
2. 灰测/分组实验的语义是「同一 provider 下比较模型/prompt」。此前常规识别腿走 DashScope、
   灰测识别腿走 SiliconFlow，A/B 结论里混入了供应商变量，无法归因到被测模型。
   修复后：灰测开启且灰测腿仍是 SF 历史默认时，provider（base/key/model）对齐常规腿；
   灰测关闭时（线上当前状态）为纯 no-op，配置逐字段不变。

覆盖：
1. main.js / index.html / api_admin.py 不再把已下架模型作为可用预设或默认值
2. 前端 siliconflow 预设与 db 常量逐字段一致
3. A2 主行为：grey_enabled=True + SF 历史默认 → 对齐常规识别腿 provider
4. A2 守护：grey_enabled=False → hydrate 后灰测字段逐字段不变
5. A2 守护：灰测腿已改成其它供应商 → 保持不动（只告警），不堵死自定义路径
6. 反证：去掉对齐调用后，第 3 条必须失败
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app.models import DASHSCOPE_DEFAULT_REC_MODEL  # noqa: E402

# 仓库根（demo/tests/ 往上两级）
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEMO_DIR = os.path.join(REPO_ROOT, "demo")

DS_BASE = "https://dashscope.test.example.com/compatible-mode/v1"
DS_KEY = "sk-test-dashscope-1234567890"
DS_MODEL = "qwen-test-omni"
SF_BASE = "https://api.siliconflow.cn/v1"
SF_KEY = "sk-test-siliconflow-1234567890"

DELISTED_MODEL = "Qwen/Qwen2.5-VL-7B-Instruct"


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """隔离 DB + 钉住 .env 取值，不写 live 库、不依赖本机 .env。"""
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_grey_align.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    db.DB_PATH = test_db_path
    db._make_engine()

    monkeypatch.setenv("DASHSCOPE_BASE_URL", DS_BASE)
    monkeypatch.setenv("DASHSCOPE_API_KEY", DS_KEY)
    monkeypatch.setenv("QWEN_VL_MODEL", DS_MODEL)
    monkeypatch.setenv("SILICONFLOW_BASE_URL", SF_BASE)
    monkeypatch.setenv("SILICONFLOW_API_KEY", SF_KEY)
    # 审核腿装配会做 /models 探针，离线测试统一视为健康，避免真实网络调用
    monkeypatch.setattr(db, "_openai_gateway_healthy", lambda *a, **k: True)

    yield

    if os.path.exists(test_db_path):
        try:
            os.remove(test_db_path)
        except Exception:
            pass
    db.DB_PATH = old_db_path
    db._make_engine()


def _read(*parts):
    with open(os.path.join(*parts), "r", encoding="utf-8") as f:
        return f.read()


def _seed_grey_leg(monkeypatch, *, enabled, base, model, key="sk-grey-key-1234567890"):
    """把灰测识别腿写成指定形态，供 hydrate 观察。"""
    cfg = db.get_engine_config()
    cfg.grey_enabled = enabled
    cfg.grey_openai_rec_base_url = base
    cfg.grey_openai_rec_api_key = key
    cfg.grey_openai_rec_model = model
    cfg.grey_recognition_model = model
    db.set_engine_config(cfg)


# ---------------------------------------------------------------
# 第 1 组：前端 / 后端预设不得再引用已下架模型
# ---------------------------------------------------------------

def test_main_js_siliconflow_preset_uses_current_models():
    """main.js 的 siliconflow 预设必须与 db._SF_DEFAULT_* 常量一致，且不含已下架模型。"""
    js = _read(DEMO_DIR, "static", "js", "main.js")
    m = re.search(r"siliconflow:\s*\{(.*?)\}", js, re.S)
    assert m, "未找到 main.js 的 siliconflow 预设块"
    block = m.group(1)
    assert db._SF_DEFAULT_REC_MODEL in block, f"识别模型应为 {db._SF_DEFAULT_REC_MODEL}"
    assert db._SF_DEFAULT_AUD_MODEL in block, f"审核模型应为 {db._SF_DEFAULT_AUD_MODEL}"
    assert DELISTED_MODEL not in block, "siliconflow 预设仍引用已下架模型"


def test_no_delisted_model_in_js_or_html():
    """前端资产（js/html）不得再出现已下架模型名（注释例外，但 js/html 不含该注释）。"""
    offenders = []
    for root, _dirs, files in os.walk(DEMO_DIR):
        if "node_modules" in root:
            continue
        for name in files:
            if not name.endswith((".js", ".html")):
                continue
            path = os.path.join(root, name)
            if DELISTED_MODEL in _read(path):
                offenders.append(os.path.relpath(path, REPO_ROOT))
    assert offenders == [], f"以下前端资产仍引用已下架模型: {offenders}"


def test_api_admin_preset_matches_db_constants():
    """api_admin.get_engine_presets 的 siliconflow 预设同样与 db 常量一致。"""
    src = _read(DEMO_DIR, "app", "api_admin.py")
    m = re.search(r'"siliconflow":\s*\{(.*?)\}', src, re.S)
    assert m, "未找到 api_admin 的 siliconflow 预设块"
    block = m.group(1)
    assert db._SF_DEFAULT_REC_MODEL in block
    assert db._SF_DEFAULT_AUD_MODEL in block
    assert DELISTED_MODEL not in block


def test_db_sf_constants_are_not_delisted():
    assert db._SF_DEFAULT_REC_MODEL == "Qwen/Qwen3-VL-32B-Instruct"
    assert db._SF_DEFAULT_AUD_MODEL == "zai-org/GLM-4.5V"
    assert DASHSCOPE_DEFAULT_REC_MODEL == "qwen3.5-omni-flash"


# ---------------------------------------------------------------
# 第 2 组：灰测识别腿 provider 对齐（A2 主行为与守护）
# ---------------------------------------------------------------

def test_grey_leg_aligns_to_recognition_leg_when_enabled(monkeypatch):
    """grey_enabled=True + 灰测腿仍是 SF 历史默认 → base/key/model 全部对齐常规识别腿。"""
    _seed_grey_leg(monkeypatch, enabled=True, base=SF_BASE,
                   model=db._SF_DEFAULT_REC_MODEL)
    db.hydrate_engine_config_from_env()
    cfg = db.get_engine_config()
    # 常规识别腿：DashScope（本用例 .env 钉死）
    assert cfg.openai_rec_base_url == DS_BASE
    assert cfg.openai_rec_model == DS_MODEL
    # 灰测识别腿：同源
    assert cfg.grey_openai_rec_base_url == DS_BASE
    assert cfg.grey_openai_rec_api_key == DS_KEY
    assert cfg.grey_openai_rec_model == DS_MODEL
    assert cfg.grey_recognition_model == DS_MODEL
    assert cfg.grey_recognition_engine.value == "openai"


def test_grey_leg_untouched_when_disabled(monkeypatch):
    """grey_enabled=False（线上当前状态）→ 灰测字段逐字段不变（纯 no-op）。"""
    _seed_grey_leg(monkeypatch, enabled=False, base=SF_BASE,
                   model=db._SF_DEFAULT_REC_MODEL)
    before = db.get_engine_config().model_dump()
    db.hydrate_engine_config_from_env()
    after = db.get_engine_config().model_dump()
    grey_before = {k: v for k, v in before.items() if k.startswith("grey")}
    grey_after = {k: v for k, v in after.items() if k.startswith("grey")}
    assert grey_before == grey_after, "灰测关闭时 hydrate 不得改动任何灰测字段"


def test_grey_leg_with_custom_provider_is_preserved(monkeypatch):
    """灰测腿已被明确改成其它供应商 → 保持不动（只告警），不堵死自定义 provider 路径。"""
    custom_base = "https://custom-grey.example.com/v1"
    _seed_grey_leg(monkeypatch, enabled=True, base=custom_base,
                   model="custom/grey-model", key="sk-custom-grey-1234567890")
    db.hydrate_engine_config_from_env()
    cfg = db.get_engine_config()
    assert cfg.grey_openai_rec_base_url == custom_base
    assert cfg.grey_openai_rec_api_key == "sk-custom-grey-1234567890"
    assert cfg.grey_openai_rec_model == "custom/grey-model"


def test_grey_leg_custom_base_with_default_model_is_preserved(monkeypatch):
    """只改了 base（模型仍是 SF 默认）→ 视为刻意配置，整腿保持不动，不做半对齐。"""
    custom_base = "https://custom-grey.example.com/v1"
    _seed_grey_leg(monkeypatch, enabled=True, base=custom_base,
                   model=db._SF_DEFAULT_REC_MODEL)
    db.hydrate_engine_config_from_env()
    cfg = db.get_engine_config()
    assert cfg.grey_openai_rec_base_url == custom_base
    assert cfg.grey_openai_rec_model == db._SF_DEFAULT_REC_MODEL


def test_grey_leg_empty_base_follows_recognition_leg(monkeypatch):
    """灰测腿 base/model 为空（从未配置）→ 直接继承常规识别腿 provider。"""
    _seed_grey_leg(monkeypatch, enabled=True, base="", model="")
    db.hydrate_engine_config_from_env()
    cfg = db.get_engine_config()
    assert cfg.grey_openai_rec_base_url == DS_BASE
    assert cfg.grey_openai_rec_model == DS_MODEL


def test_reverse_proof_alignment_call_is_required(monkeypatch):
    """反证：把对齐函数替换为 no-op（等价于修复前）→ 主行为用例的同一断言必须失败。"""
    _seed_grey_leg(monkeypatch, enabled=True, base=SF_BASE,
                   model=db._SF_DEFAULT_REC_MODEL)
    monkeypatch.setattr(db, "_align_grey_recognition_leg", lambda *a, **k: False)
    db.hydrate_engine_config_from_env()
    cfg = db.get_engine_config()
    # 主行为用例断言「灰测 base == 常规 base」。去掉对齐后灰测腿仍是 SF，
    # 该断言必然不成立——证明主行为用例不是恒真断言。
    with pytest.raises(AssertionError):
        assert cfg.grey_openai_rec_base_url == cfg.openai_rec_base_url
    assert cfg.grey_openai_rec_base_url == SF_BASE
