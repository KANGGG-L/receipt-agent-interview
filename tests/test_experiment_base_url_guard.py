# -*- coding: utf-8 -*-
"""A/B 实验覆盖路径的凭据与 SSRF 保护（P0-A / A3）测试集。

覆盖：
1. 内网/云元数据 base_url 创建实验 → 400，且带可读原因（不静默放行）。
2. 公网 base_url 且未提供 api_key：库内真实密钥被清空，实际发出的请求不带 Authorization。
3. 公网 base_url 且显式提供 key：实际发出的请求带该 key。
4. GET/POST 实验端点的 *_api_key 脱敏返回（库里仍为明文，后端调用不受影响）。

说明（第 2/3 项的路径真实性）：识别的真实外呼在 llm.OpenAIChatModel._generate 里用
requests.post 构造请求头。测试用 receipt_utils._apply_snapshot_credential（即覆盖逻辑抽取出的
同一函数）把实验快照应用到 cfg，再用 llm._build 从被覆盖后的 cfg 构造引擎并触发一次真实
请求构造，从而断言「发出去的请求头」——而不是只断言中间变量。
"""

import json
import os
import socket
import sys

from starlette.testclient import TestClient

DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "demo"))
if DEMO_DIR not in sys.path:
    sys.path.insert(0, DEMO_DIR)

from app import db, llm
from app.main import app
from app.services import receipt_utils
from langchain_core.messages import HumanMessage

client = TestClient(app, headers={"X-Role": "admin"})


# -------------------------------------------------------------
# 1. 内网 / 云元数据 base_url → 400
# -------------------------------------------------------------
def test_create_experiment_rejects_private_or_metadata_base_url():
    for url in ("http://169.254.169.254/v1", "http://10.0.0.1/v1"):
        for field in ("treatment_base_url", "control_base_url"):
            resp = client.post("/api/admin/experiments",
                               json={"name": "ssrf-guard", field: url})
            assert resp.status_code == 400, f"{field}={url} 必须被拒绝"
            body = resp.json()
            assert body["code"] == "EXPERIMENT_BASE_URL_INVALID"
            # 必须带可读原因，不得静默放行
            assert body["msg"], "拒绝必须给出可读原因"
            assert any(kw in body["msg"] for kw in ("安全", "私网", "元数据", "本地", "阻断"))


# -------------------------------------------------------------
# 2/3. 实际外呼请求头里的凭据保护
# -------------------------------------------------------------
def _capture_posts(monkeypatch):
    """monkeypatch requests.post，返回记录每次请求 (url, headers) 的列表。"""
    captured = []

    class _Resp:
        status_code = 200
        text = "ok"

        def json(self):
            return {"choices": [{"message": {"content": "{}"}}], "usage": {}}

    def _post(url, headers=None, json=None, timeout=None, allow_redirects=None, **kw):
        captured.append({"url": url, "headers": dict(headers or {})})
        return _Resp()

    monkeypatch.setattr("requests.post", _post)
    return captured


def _allow_dns(monkeypatch):
    """把任意域名的 DNS 解析固定到公网 IP，避免用例依赖真实网络。"""
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],
    )


def _fire_recognition_request(cfg):
    model = llm._build("openai", cfg.openai_rec_model, cfg, side="rec")
    model._generate([HumanMessage(content="hi")])


def test_public_base_url_without_key_sends_no_authorization(monkeypatch):
    captured = _capture_posts(monkeypatch)
    _allow_dns(monkeypatch)

    cfg = db.get_engine_config()
    cfg.openai_rec_base_url = "https://api.siliconflow.cn/v1"
    cfg.openai_rec_api_key = "sk-library-real-secret-1234567890"
    cfg.openai_rec_model = "Qwen/Qwen3-VL-32B-Instruct"

    # 复刻 Job 内的覆盖顺序：先按快照改写 base_url，再应用凭据规则
    snap = {"treatment_base_url": "https://experiment.example.com/v1"}
    cfg.openai_rec_base_url = snap["treatment_base_url"]
    applied = {}
    receipt_utils._apply_snapshot_credential(cfg, snap, "treatment", applied)

    assert applied["api_key_source"] == "cleared"
    assert cfg.openai_rec_api_key == "", "换端点未给 key 时必须清空库内真实密钥"

    _fire_recognition_request(cfg)
    assert captured, "应发出一次识别请求"
    assert captured[0]["url"].startswith("https://experiment.example.com"), \
        "请求应打到实验指定的第三方端点"
    assert "Authorization" not in captured[0]["headers"], \
        "库内真实密钥不得随实验端点外发"


def test_public_base_url_with_explicit_key_sends_that_key(monkeypatch):
    captured = _capture_posts(monkeypatch)
    _allow_dns(monkeypatch)

    cfg = db.get_engine_config()
    cfg.openai_rec_base_url = "https://api.siliconflow.cn/v1"
    cfg.openai_rec_api_key = "sk-library-real-secret-1234567890"
    cfg.openai_rec_model = "Qwen/Qwen3-VL-32B-Instruct"

    snap = {"treatment_base_url": "https://experiment2.example.com/v1",
            "treatment_api_key": "sk-experiment-key-abcdef123456"}
    cfg.openai_rec_base_url = snap["treatment_base_url"]
    applied = {}
    receipt_utils._apply_snapshot_credential(cfg, snap, "treatment", applied)

    assert applied["api_key_source"] == "snapshot"
    assert cfg.openai_rec_api_key == "sk-experiment-key-abcdef123456"

    _fire_recognition_request(cfg)
    assert captured[0]["headers"].get("Authorization") == \
        "Bearer sk-experiment-key-abcdef123456"


def test_apply_snapshot_credential_keeps_library_key_when_no_base_url_override():
    """回归：快照既没改 base_url 也没给 key 时，库内密钥必须原样保留（不误清）。"""
    cfg = db.get_engine_config()
    cfg.openai_rec_api_key = "sk-library-real-secret-1234567890"
    applied = {}
    receipt_utils._apply_snapshot_credential(cfg, {"treatment_model": "gpt-4o"},
                                             "treatment", applied)
    assert cfg.openai_rec_api_key == "sk-library-real-secret-1234567890"
    assert "api_key_source" not in applied


# -------------------------------------------------------------
# 4. 实验端点 *_api_key 脱敏
# -------------------------------------------------------------
def test_experiments_endpoints_mask_snapshot_api_keys():
    resp = client.post("/api/admin/experiments", json={
        "name": "mask-check",
        "treatment_base_url": "https://93.184.216.34/v1",
        "treatment_api_key": "sk-treatment-secret-abcdef123456",
        "control_api_key": "sk-control-secret-abcdef123456",
        "production_data_outbound_acknowledged": True,
    })
    assert resp.status_code == 200
    created = resp.json()
    exp_id = created["id"]

    snap = created["experiment"]["grey_snapshot"]
    assert snap["treatment_api_key"] == "sk-tre****3456"
    assert snap["control_api_key"] == "sk-con****3456"
    assert "sk-treatment-secret" not in json.dumps(created, ensure_ascii=False)

    # 列表与详情同样脱敏
    rows = client.get("/api/admin/experiments").json()["data"]
    row = next(e for e in rows if e["id"] == exp_id)
    assert row["grey_snapshot"]["treatment_api_key"] == "sk-tre****3456"
    detail = client.get(f"/api/admin/experiments/{exp_id}").json()["experiment"]
    assert detail["grey_snapshot"]["control_api_key"] == "sk-con****3456"

    # 库内仍是明文，后端调用不受影响
    internal = db.get_experiment(exp_id)
    assert internal["grey_snapshot"]["treatment_api_key"] == "sk-treatment-secret-abcdef123456"


def test_create_experiment_requires_outbound_disclaimer_for_unadmitted_domain():
    """W-ISO-2 / EC-7.2: 未准入域名若未显式勾选出站免责，必须拦截并返回 400 OUTBOUND_DISCLAIMER_REQUIRED。"""
    # 1. 未勾选免责 -> 400
    resp = client.post("/api/admin/experiments", json={
        "name": "unadmitted-outbound-test",
        "treatment_base_url": "https://93.184.216.34/v1",
        "production_data_outbound_acknowledged": False,
    })
    assert resp.status_code == 400
    data = resp.json()
    assert data["code"] == "OUTBOUND_DISCLAIMER_REQUIRED"
    assert "未在合规准入白名单中" in data["msg"]

    # 2. 显式勾选免责 -> 200 并返回警告标记
    resp_ok = client.post("/api/admin/experiments", json={
        "name": "unadmitted-outbound-acknowledged",
        "treatment_base_url": "https://93.184.216.34/v1",
        "production_data_outbound_acknowledged": True,
    })
    assert resp_ok.status_code == 200
    ok_data = resp_ok.json()
    assert "warnings" in ok_data
    assert any("生产单据数据将出站发送至实验节点" in w for w in ok_data["warnings"])

