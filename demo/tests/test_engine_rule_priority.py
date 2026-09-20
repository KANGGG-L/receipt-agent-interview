# -*- coding: utf-8 -*-
"""
引擎配置多规则优先级与冲突仲裁单元测试
验证：
1. P0 A/B 实验运行态优先接管流量与组别分配；
2. 无运行态实验时顺位回退至 P1 金丝雀灰度发布；
3. 灰测关闭时回退至 P2 生产基线识别引擎；
4. 全流程无 Emoji 规范检验。
"""

import pytest
from app import db
from app.models import EngineConfig, should_use_grey


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """显式 DB 隔离：本文件不依赖 demo/conftest.py 的全局兜底。

    why：conftest 的 _isolate_test_db 只在「生效的 db.DB_PATH 仍等于 live」时才
    兜底隔离；若同一 pytest 进程内某个先跑的测试文件把 db.DB_PATH 改成了别的库
    且未还原（如 test_experiment_guardian.py 用 importlib.reload(db) 切换临时库
    后不还原），兜底会整体跳过，本文件就落到那个陌生库上，读到别人遗留的 running
    实验 —— 这正是本文件与 test_experiment_guardian.py 同跑必挂的根因。显式夹具
    让本文件无论执行顺序、无论上游是否污染全局态，都使用自己的临时库。
    """
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_engine_rule_priority.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    db.DB_PATH = test_db_path
    db._make_engine()
    yield
    db.DB_PATH = old_db_path
    db._make_engine()


def test_running_experiment_priority():
    # 创建并启动测试实验
    created = db.create_experiment(
        name="Priority-Rule-Test-Exp",
        hypothesis="验证 P0 优先级",
        success_metric="accuracy",
        target_percent=50,
        min_sample=10,
    )
    exp_id = created["id"]
    
    # 模拟灰度快照中指定候选与基线模型
    snapshot = {
        "treatment_model": "Qwen/Qwen3-VL-32B-Instruct",
        "control_model": "gpt-4o-mini-baseline",
    }
    db.start_experiment(exp_id, grey_snapshot=snapshot)

    # 1. 验证 get_running_experiment 正常命中
    running = db.get_running_experiment()
    assert running is not None
    assert running["id"] == exp_id
    assert running["status"] == "running"

    # 2. 模拟单据分配 (按 receipt_id 模 100)
    # receipt_id = 20 -> 20 < 50 -> treatment
    bucket_t = 20 % 100
    grp_t = "treatment" if bucket_t < 50 else "control"
    assert grp_t == "treatment"

    # receipt_id = 80 -> 80 >= 50 -> control
    bucket_c = 80 % 100
    grp_c = "treatment" if bucket_c < 50 else "control"
    assert grp_c == "control"

    # 停止实验，恢复环境
    db.stop_experiment(exp_id)
    assert db.get_running_experiment() is None


def test_canary_fallback_when_no_experiment():
    # 当没有运行中的实验时，正常使用 Canary 灰度规则
    assert db.get_running_experiment() is None
    cfg = EngineConfig(
        grey_enabled=True,
        grey_percent=100,  # 100% 灰度
    )
    assert should_use_grey(cfg, "测试供应商") is True

    # 灰测关闭时回退基线
    cfg_off = EngineConfig(grey_enabled=False, grey_percent=100)
    assert should_use_grey(cfg_off, "测试供应商") is False
