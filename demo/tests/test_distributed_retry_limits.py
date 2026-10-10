# -*- coding: utf-8 -*-
"""分布式分腿重跑上限改造测试套件 (Phase 2 全量自动化验证)。

严格对照 Phase 1 验收标准清单 (Acceptance Criteria, AC-UI-01 至 AC-RUN-05) 与工程红线：
1. 界面呈现与 DOM 规范 (AC-UI-01 ~ AC-UI-06)
2. 数据交互与表单链路 (AC-DATA-01 ~ AC-DATA-04)
3. 后端接口校验与灰测生命周期 (AC-API-01 ~ AC-API-04)
4. 运行时调度与重试生效 (AC-RUN-01 ~ AC-RUN-05)
5. 全局禁止使用 Emoji。
"""

import os
import re
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app
from app import db
from app.models import (
    EngineConfig,
    clamp_retry_rounds,
    resolve_max_retry,
    MAX_RETRY_LIMIT,
)
from app.api_admin import (
    EngineConfigBody,
    diff_regular_vs_grey,
    promote_grey_config,
    rollback_engine_config,
)
from app.chains import supervisor


# ==============================================================================
# 维度 1: 界面呈现与 DOM 规范 (AC-UI-01 ~ AC-UI-06)
# ==============================================================================

def test_ac_ui_01_admin_max_retry_rounds_removed():
    """AC-UI-01: 顶层统一的 adminMaxRetryRounds 输入框彻底移除，无 DOM 残留。"""
    html_path = Path(__file__).resolve().parent.parent / "templates" / "index.html"
    content = html_path.read_text(encoding="utf-8")
    assert 'id="adminMaxRetryRounds"' not in content, "顶层 adminMaxRetryRounds 必须彻底从 index.html 移除"


def test_ac_ui_02_to_05_per_leg_retry_inputs_exist():
    """AC-UI-02 ~ 05: 6 个引擎腿卡片中分别存在对应的重跑上限输入框与属性配置。"""
    html_path = Path(__file__).resolve().parent.parent / "templates" / "index.html"
    content = html_path.read_text(encoding="utf-8")

    expected_inputs = [
        # placeholder 用前缀匹配：数值默认值是契约（3/1/继承常规），后缀为运营建议文案可演化
        ("adminRecMaxRetryRounds", "openai_rec_max_retry_rounds", "3"),
        ("adminAudMaxRetryRounds", "openai_aud_max_retry_rounds", "1"),
        ("adminParseMaxRetryRounds", "openai_parse_max_retry_rounds", "1"),
        ("adminGreyRecMaxRetryRounds", "grey_openai_rec_max_retry_rounds", "继承常规"),
        ("adminGreyAudMaxRetryRounds", "grey_openai_aud_max_retry_rounds", "继承常规"),
        ("adminGreyParseMaxRetryRounds", "grey_openai_parse_max_retry_rounds", "继承常规"),
    ]

    for elem_id, field_name, placeholder in expected_inputs:
        assert f'id="{elem_id}"' in content, f"卡片中必须包含 id={elem_id}"
        assert f'name="{field_name}"' in content, f"卡片中必须包含 name={field_name}"
        assert f'placeholder="{placeholder}' in content, \
            f"输入框 {elem_id} placeholder 必须以 {placeholder} 开头（数值默认值契约）"

    # 属性统配验证 min="0" max="10"
    for elem_id, _, _ in expected_inputs:
        pattern = rf'id="{elem_id}"[^>]*min="0"[^>]*max="10"'
        assert re.search(pattern, content) is not None, f"{elem_id} 必须包含 min='0' max='10'"


def test_ac_ui_06_no_emoji_in_templates_and_models():
    """AC-UI-06: 严格检查无 Emoji 红线。"""
    emoji_pattern = re.compile(r"[\U00010000-\U0010ffff]", flags=re.UNICODE)
    
    check_files = [
        Path(__file__).resolve().parent.parent / "templates" / "index.html",
        Path(__file__).resolve().parent.parent / "app" / "models.py",
        Path(__file__).resolve().parent.parent / "app" / "api_admin.py",
        Path(__file__).resolve().parent.parent / "app" / "chains" / "supervisor.py",
    ]
    for p in check_files:
        text = p.read_text(encoding="utf-8")
        matches = emoji_pattern.findall(text)
        assert len(matches) == 0, f"文件 {p} 中严禁包含 Emoji 表情，发现: {matches[:5]}"


# ==============================================================================
# 维度 2: 数据契约与双向同步 (AC-DATA-01 ~ AC-DATA-04)
# ==============================================================================

def test_models_default_values_and_sync():
    """测试 EngineConfig 默认值以及简写与 openai_ 前缀双向同步。"""
    cfg = EngineConfig()
    assert cfg.openai_rec_max_retry_rounds == 3
    assert cfg.rec_max_retry_rounds == 3
    assert cfg.openai_aud_max_retry_rounds == 1
    assert cfg.aud_max_retry_rounds == 1
    assert cfg.openai_parse_max_retry_rounds == 1
    assert cfg.parse_max_retry_rounds == 1
    assert cfg.grey_openai_rec_max_retry_rounds is None
    assert cfg.grey_rec_max_retry_rounds is None
    assert cfg.max_retry_rounds == 3

    # 历史字段兼容
    cfg_legacy = EngineConfig(max_retry_rounds=5)
    assert cfg_legacy.rec_max_retry_rounds == 5
    assert cfg_legacy.openai_rec_max_retry_rounds == 5
    assert cfg_legacy.max_retry_rounds == 5

    # 简写与 openai_ 前缀同步
    cfg_rec = EngineConfig(rec_max_retry_rounds=4)
    assert cfg_rec.openai_rec_max_retry_rounds == 4
    assert cfg_rec.max_retry_rounds == 4

    # 动态赋值双向同步
    cfg.max_retry_rounds = 2
    assert cfg.rec_max_retry_rounds == 2
    assert cfg.openai_rec_max_retry_rounds == 2

    cfg.rec_max_retry_rounds = 0
    assert cfg.max_retry_rounds == 0
    assert cfg.openai_rec_max_retry_rounds == 0


def test_clamp_retry_rounds():
    """AC-API-01: 校验重试上限钳制在 [0, 10]。"""
    assert clamp_retry_rounds(None) is None
    assert clamp_retry_rounds(-5) == 0
    assert clamp_retry_rounds(0) == 0
    assert clamp_retry_rounds(3) == 3
    assert clamp_retry_rounds(10) == 10
    assert clamp_retry_rounds(99) == 10
    assert clamp_retry_rounds("invalid", default=3) == 3


# ==============================================================================
# 维度 3: 管理端 API、灰测推全、回滚与对比 (AC-API-01 ~ AC-API-04)
# ==============================================================================

@pytest.fixture
def auth_client():
    client = TestClient(app)
    client.headers.update({"X-Role": "admin", "X-Email": "admin@demo.hk"})
    return client


def test_api_admin_engine_config_crud(auth_client):
    """AC-DATA-01 ~ 04: GET/PUT 读写分腿重跑上限。"""
    res = auth_client.get("/api/admin/engine-config")
    assert res.status_code == 200
    data = res.json()["data"]
    assert "openai_rec_max_retry_rounds" in data
    assert "openai_aud_max_retry_rounds" in data
    assert "openai_parse_max_retry_rounds" in data
    assert "grey_openai_rec_max_retry_rounds" in data

    # PUT 更新分腿重试上限
    put_payload = {
        "openai_rec_max_retry_rounds": 4,
        "openai_aud_max_retry_rounds": 2,
        "openai_parse_max_retry_rounds": 3,
        "grey_openai_rec_max_retry_rounds": 5,
    }
    res_put = auth_client.put("/api/admin/engine-config", json=put_payload)
    assert res_put.status_code == 200

    # 回读验证
    res_after = auth_client.get("/api/admin/engine-config")
    after_data = res_after.json()["data"]
    assert after_data["openai_rec_max_retry_rounds"] == 4
    assert after_data["openai_aud_max_retry_rounds"] == 2
    assert after_data["openai_parse_max_retry_rounds"] == 3
    assert after_data["grey_openai_rec_max_retry_rounds"] == 5

    # 越界值钳制验证
    res_clamp = auth_client.put("/api/admin/engine-config", json={
        "openai_rec_max_retry_rounds": 99,
        "openai_aud_max_retry_rounds": -3,
    })
    assert res_clamp.status_code == 200
    res_clamp_read = auth_client.get("/api/admin/engine-config")
    clamp_data = res_clamp_read.json()["data"]
    assert clamp_data["openai_rec_max_retry_rounds"] == 10
    assert clamp_data["openai_aud_max_retry_rounds"] == 0


def test_api_admin_promote_and_rollback(auth_client):
    """AC-API-02 & AC-API-03: 金丝雀灰测推全与一键回滚生命周期验证。"""
    # 1. 设定常规与灰测配置
    res_set = auth_client.put("/api/admin/engine-config", json={
        "grey_enabled": True,
        "openai_rec_max_retry_rounds": 3,
        "openai_aud_max_retry_rounds": 1,
        "openai_parse_max_retry_rounds": 1,
        "grey_openai_rec_max_retry_rounds": 6,
        "grey_openai_aud_max_retry_rounds": 2,
        "grey_openai_parse_max_retry_rounds": 4,
    })
    assert res_set.status_code == 200

    # 2. 检查差异
    res_diff = auth_client.get("/api/admin/engine-config/diff")
    assert res_diff.status_code == 200
    diff_items = res_diff.json()["data"]["diffs"]
    diff_labels = [d["label"] for d in diff_items]
    assert "识别 · 重跑上限 (轮)" in diff_labels
    assert "审核 · 重跑上限 (轮)" in diff_labels
    assert "解析 · 重跑上限 (轮)" in diff_labels

    # 3. 执行推全
    res_promote = auth_client.put("/api/admin/engine-config/promote")
    assert res_promote.status_code == 200
    promoted_data = res_promote.json()["data"]
    # 常规腿覆盖为原灰测值
    assert promoted_data["openai_rec_max_retry_rounds"] == 6
    assert promoted_data["openai_aud_max_retry_rounds"] == 2
    assert promoted_data["openai_parse_max_retry_rounds"] == 4
    # 灰测腿复位为 None
    assert promoted_data["grey_openai_rec_max_retry_rounds"] is None
    assert promoted_data["grey_openai_aud_max_retry_rounds"] is None
    assert promoted_data["grey_openai_parse_max_retry_rounds"] is None

    # 4. 执行一键回滚
    res_rollback = auth_client.put("/api/admin/engine-config/rollback")
    assert res_rollback.status_code == 200
    rolled_data = res_rollback.json()["data"]
    # 恢复为推全前的常规原值
    assert rolled_data["openai_rec_max_retry_rounds"] == 3
    assert rolled_data["openai_aud_max_retry_rounds"] == 1
    assert rolled_data["openai_parse_max_retry_rounds"] == 1


# ==============================================================================
# 维度 4: 运行时调度与重试生效 (AC-RUN-01 ~ AC-RUN-05)
# ==============================================================================

def test_ac_run_01_to_03_resolve_max_retry():
    """AC-RUN-01 ~ AC-RUN-03: 运行时 resolve_max_retry 正确性验证。"""
    # 缺省解析
    assert resolve_max_retry(None, leg="rec") == 3
    assert resolve_max_retry(None, leg="aud") == 1
    assert resolve_max_retry(None, leg="parse") == 1

    # 常规配置独立生效
    cfg = EngineConfig(
        openai_rec_max_retry_rounds=1,
        openai_aud_max_retry_rounds=4,
        openai_parse_max_retry_rounds=2,
    )
    assert resolve_max_retry(cfg, leg="rec", use_grey=False) == 1
    assert resolve_max_retry(cfg, leg="aud", use_grey=False) == 4
    assert resolve_max_retry(cfg, leg="parse", use_grey=False) == 2

    # 灰测继承与独立配置生效
    cfg_grey = EngineConfig(
        openai_rec_max_retry_rounds=3,
        grey_openai_rec_max_retry_rounds=None,  # 继承
        grey_openai_aud_max_retry_rounds=5,     # 灰测覆盖
    )
    # 继承常规
    assert resolve_max_retry(cfg_grey, leg="rec", use_grey=True) == 3
    # 灰测独立覆盖
    assert resolve_max_retry(cfg_grey, leg="aud", use_grey=True) == 5

    # 运行期硬护栏 [1, 10]
    cfg_zero = EngineConfig()
    cfg_zero.rec_max_retry_rounds = 0
    assert resolve_max_retry(cfg_zero, leg="rec") == 1  # 0 轮钳制为首轮 1 尝试，0 重试

    cfg_huge = EngineConfig()
    cfg_huge.rec_max_retry_rounds = 99
    assert resolve_max_retry(cfg_huge, leg="rec") == 10  # 钳制至上限 10
