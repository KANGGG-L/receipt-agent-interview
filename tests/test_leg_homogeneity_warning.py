# -*- coding: utf-8 -*-
"""L1 生成器-评估器同源运行时告警测试。

覆盖：
1. 纯默认（识别腿与审核腿均 opencode/mimo-v2.5-free）触发 logger warning（caplog 捕获），
   告警文案含「ai_registry 准入规则 3」人话指引；
2. 异构配置（审核腿换引擎）不告警；
3. 同一进程同一组合只告警一次（防刷屏）；灰测组双腿同源同样告警且独立去重。

全部离线：只构建模型对象，不发起任何 CLI/HTTP 调用。
"""

import logging
import os
import sys

os.environ.setdefault("AUTH_ENABLED", "0")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "demo")))

from app.llm import _HOMOGENEITY_WARNED, build_audit_model, build_recognition_model
from app.models import EngineConfig, EngineKind

SAME_KIND = "opencode/mimo-v2.5-free"


def _same_source_cfg():
    """纯默认同源：双腿均 opencode/mimo-v2.5-free。"""
    return EngineConfig(
        recognition_engine=EngineKind.OPENCODE,
        recognition_model=SAME_KIND,
        audit_engine=EngineKind.OPENCODE,
        audit_model=SAME_KIND,
    )


def _hetero_cfg():
    """异构：审核腿换 codebuddy 引擎。"""
    return EngineConfig(
        recognition_engine=EngineKind.OPENCODE,
        recognition_model=SAME_KIND,
        audit_engine=EngineKind.CODEBUDDY,
        audit_model="minimax-m3-pay",
    )


def _reset_warned(monkeypatch):
    """每用例重置去重集合，保证告警计数可观测。"""
    monkeypatch.setattr("app.llm._HOMOGENEITY_WARNED", set())


def test_same_source_triggers_warning_once(caplog, monkeypatch):
    _reset_warned(monkeypatch)
    cfg = _same_source_cfg()
    with caplog.at_level(logging.WARNING, logger="llm"):
        build_audit_model(cfg=cfg)
        build_recognition_model(cfg=cfg)
        build_audit_model(cfg=cfg)  # 第三次构建：同组合不得重复告警
    msgs = [r.message for r in caplog.records
            if "识别腿与审核腿同源" in r.message]
    assert len(msgs) == 1, "同源配置应告警且只告警一次，实际 %d 次" % len(msgs)
    assert "准入规则 3" in msgs[0]
    assert "opencode" in msgs[0]
    assert "切换异构审核模型" in msgs[0]


def test_hetero_config_does_not_warn(caplog, monkeypatch):
    _reset_warned(monkeypatch)
    cfg = _hetero_cfg()
    with caplog.at_level(logging.WARNING, logger="llm"):
        build_audit_model(cfg=cfg)
        build_recognition_model(cfg=cfg)
    assert not [r for r in caplog.records if "识别腿与审核腿同源" in r.message], \
        "异构配置不得触发同源告警"


def test_grey_legs_checked_independently(caplog, monkeypatch):
    """灰测组双腿同源同样告警，且与常规腿告警各自独立去重一次。"""
    _reset_warned(monkeypatch)
    cfg = EngineConfig(
        recognition_engine=EngineKind.CODEBUDDY,
        recognition_model="minimax-m3-pay",
        audit_engine=EngineKind.OPENCODE,
        audit_model=SAME_KIND,  # 常规腿异构
        grey_recognition_engine=EngineKind.OPENCODE,
        grey_recognition_model=SAME_KIND,
        grey_audit_engine=EngineKind.OPENCODE,
        grey_audit_model=SAME_KIND,  # 灰测腿同源
    )
    with caplog.at_level(logging.WARNING, logger="llm"):
        build_audit_model(cfg=cfg)      # 常规腿：异构，不告警
        build_audit_model(cfg=cfg, use_grey=True)   # 灰测腿：同源，告警 1 次
        build_recognition_model(cfg=cfg, use_grey=True)  # 灰测腿重复：不告警
    grey_msgs = [r.message for r in caplog.records
                 if "识别腿与审核腿同源" in r.message]
    assert len(grey_msgs) == 1, "灰测组同源应独立告警一次，实际 %d 次" % len(grey_msgs)
