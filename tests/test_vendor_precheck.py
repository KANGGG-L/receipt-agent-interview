# -*- coding: utf-8 -*-
"""P2-T6 首轮供应商预判（后端）验证：开关语义 + 手选优先 + 失败不阻断。

背景：供应商记忆注入只有 vendor_hint 一条通道，而前端从不发送该字段，导致
首轮注入不可达（记忆命中率 1.71%）。本模块补的预判通道必须满足三条硬约束，
本文件逐条锁定：

1. 开关关闭（缺省）→ 零影响：不建模型、不调用，vendor_hint 仍为空串。
2. 开关开启且预判成功 → 预判值真的传进 start_recognition_job（不是「定义了
   没接线」）。
3. 预判异常 → 上传仍正常返回，vendor_hint 为空串（绝不阻断识别）。

额外锁定两条与本任务契约直接相关的行为：
4. 前端手选优先：手选值存在时不得调用预判（否则预判会覆盖用户明确选择）。
5. 抬头 ROI 临时文件必须被清理（本仓对「跑测试留下孤儿文件」有历史事故）。

反证（写在报告里）：去掉 predict_vendor 内的开关判断（恒开启）后，用例 1 失败。
"""

import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo"))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import api_receipts  # noqa: E402
from app.services import preprocess, vendor_precheck  # noqa: E402

STAFF_HEADERS = {"X-Role": "staff"}
_ENV_SWITCH = "VENDOR_PRECHECK_ENABLED"


def _make_real_jpeg(path, size=(400, 600)):
    """生成真实可解码的 JPEG（假字节会让 _imread 失败、预判直接短路，测不到链路）。"""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    draw.text((20, 20), "DELI HONG FOOD SUPPLY", fill="black")
    draw.text((20, 60), "INVOICE NO. 20260922", fill="black")
    img.save(path, format="JPEG", quality=90)
    return str(path)


class _FakeModel:
    """识别腿模型替身：记录调用次数，返回可配置的原始输出（或抛异常）。"""

    def __init__(self, raw="", raises=None):
        self.raw = raw
        self.raises = raises
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return SimpleNamespace(content=self.raw)


@pytest.fixture
def env(monkeypatch, tmp_path):
    """单张上传测试台：重活全部打桩，只保留预判→vendor_hint 这条接线。

    返回可写字典，记录 start_recognition_job 实际收到的 vendor_hint 与模型调用。
    """
    state = {"vendor_hint": [], "models": [], "roi_tmp": []}

    captured = _make_real_jpeg(tmp_path / "upload.jpg")
    monkeypatch.setattr(api_receipts, "_save_upload", lambda _f: captured)
    # 清晰度与图内隐藏文字检测打成轻量替身（本用例不关心，避免真实重计算）
    monkeypatch.setattr(api_receipts, "_laplacian_variance", lambda _p: None)
    monkeypatch.setattr(api_receipts, "_run_image_guard",
                        lambda _p: {"flagged": False, "reasons": [], "metrics": {}})
    monkeypatch.setattr(api_receipts, "public_image_url", lambda *a, **k: "")
    monkeypatch.setattr(api_receipts, "_track_event", lambda *a, **k: None)
    # 预处理保持真实（缺省两开关均不落盘），但其产物路径不影响预判断言
    monkeypatch.setattr(preprocess, "apply_pipeline", lambda p: (p, {"applied": False}))

    def _stub_start(image_path, vendor_hint="", **kw):
        state["vendor_hint"].append(vendor_hint)
        return "job-test-1", 9001

    monkeypatch.setattr(api_receipts, "start_recognition_job", _stub_start)

    # 记录 ROI 临时文件，供「不留孤儿」断言使用
    real_mkstemp = tempfile.mkstemp

    def _spy_mkstemp(*a, **kw):
        fd, p = real_mkstemp(*a, **kw)
        state["roi_tmp"].append(p)
        return fd, p

    monkeypatch.setattr(vendor_precheck.tempfile, "mkstemp", _spy_mkstemp)

    def _install_model(raw="", raises=None):
        model = _FakeModel(raw=raw, raises=raises)
        state["models"].append(model)
        monkeypatch.setattr("app.llm.build_recognition_model",
                            lambda *a, **k: model)
        return model

    state["install_model"] = _install_model
    return state


def _client():
    app = FastAPI()
    app.include_router(api_receipts.router)
    return TestClient(app)


def _upload(client, data=None):
    payload = {"async_": "true"}
    payload.update(data or {})
    return client.post("/api/upload", headers=STAFF_HEADERS,
                       files={"receipt": ("up.jpg", b"\xff\xd8\xff\xe0fake", "image/jpeg")},
                       data=payload)


# ------------------------------------------------------------------
# 1. 开关关闭（缺省）→ 零影响
# ------------------------------------------------------------------
def test_switch_off_is_zero_impact(env, monkeypatch):
    """缺省（未设 VENDOR_PRECHECK_ENABLED）：不建模型、不上报 vendor_hint。"""
    monkeypatch.delenv(_ENV_SWITCH, raising=False)
    model = env["install_model"](raw='{"vendor": "德利行"}')

    resp = _upload(_client())
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "queued", resp.json()
    assert model.calls == 0, "开关关闭时不得调用预判模型（零行为变化）"
    assert env["vendor_hint"] == [""], "开关关闭时 vendor_hint 必须仍为空串"
    assert env["roi_tmp"] == [], "开关关闭时不得裁剪抬头 ROI"


# ------------------------------------------------------------------
# 2. 开关开启 + 预判成功 → vendor_hint 非空（接线真的生效）
# ------------------------------------------------------------------
def test_switch_on_precheck_success_wires_vendor_hint(env, monkeypatch):
    """开启且模型回 {'vendor': ...}：预判值作为 vendor_hint 传给识别 Job。"""
    monkeypatch.setenv(_ENV_SWITCH, "true")
    model = env["install_model"](raw='{"vendor": "德利行"}')

    resp = _upload(_client())
    assert resp.status_code == 200, resp.text
    assert model.calls == 1, "开关开启时应调用预判模型一次"
    assert env["vendor_hint"] == ["德利行"], "预判结果必须真的传进 start_recognition_job"


def test_precheck_parses_fenced_json_with_regex_fallback(env, monkeypatch):
    """模型不老实（带代码围栏/多余文字）时仍能解析出 vendor（正则兜底口径）。"""
    monkeypatch.setenv(_ENV_SWITCH, "true")
    env["install_model"](raw='好的，结果如下：\n```json\n{"vendor": "祥興食品"}\n```')

    assert _upload(_client()).status_code == 200
    assert env["vendor_hint"] == ["祥興食品"]


# ------------------------------------------------------------------
# 3. 预判异常 → 上传仍正常且 vendor_hint 为空（不阻断识别）
# ------------------------------------------------------------------
def test_precheck_failure_does_not_block_upload(env, monkeypatch):
    """预判抛异常：HTTP 200 + queued，vendor_hint 空串（识别链路照常启动）。"""
    monkeypatch.setenv(_ENV_SWITCH, "true")
    env["install_model"](raises=RuntimeError("boom: 上游 500"))

    resp = _upload(_client())
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "queued", resp.json()
    assert env["vendor_hint"] == [""], "预判失败必须按未命中（空串）而非异常向上抛"


def test_precheck_empty_vendor_returns_empty(env, monkeypatch):
    """模型给出空 vendor（无法确定）：不注入空字符串噪音，按未命中处理。"""
    monkeypatch.setenv(_ENV_SWITCH, "true")
    env["install_model"](raw='{"vendor": ""}')

    assert _upload(_client()).status_code == 200
    assert env["vendor_hint"] == [""]


def test_precheck_undecodable_image_does_not_block_upload(env, monkeypatch, tmp_path):
    """上传的图不可解码（抬头裁剪失败）：预判短路，上传仍 200 且不建模型。"""
    monkeypatch.setenv(_ENV_SWITCH, "true")
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 32)
    monkeypatch.setattr(api_receipts, "_save_upload", lambda _f: str(bad))
    model = env["install_model"](raw='{"vendor": "德利行"}')

    resp = _upload(_client())
    assert resp.status_code == 200, resp.text
    assert model.calls == 0, "图不可解码时不应白调一次模型"
    assert env["vendor_hint"] == [""]


# ------------------------------------------------------------------
# 4. 前端手选优先
# ------------------------------------------------------------------
def test_manual_hint_wins_over_precheck(env, monkeypatch):
    """手选供应商存在时必须短路：不调用预判、不被预判值覆盖。"""
    monkeypatch.setenv(_ENV_SWITCH, "true")
    model = env["install_model"](raw='{"vendor": "预判出来的别的商号"}')

    resp = _upload(_client(), data={"vendor_hint": "手选商号"})
    assert resp.status_code == 200, resp.text
    assert model.calls == 0, "手选优先：有手选值时不得调用预判"
    assert env["vendor_hint"] == ["手选商号"], "预判不得覆盖用户手选值"


# ------------------------------------------------------------------
# 5. 临时 ROI 清理（不留孤儿文件）
# ------------------------------------------------------------------
def test_roi_temp_file_cleaned_after_precheck(env, monkeypatch):
    """预判用的抬头 ROI 临时文件必须被删除（本仓对孤儿落盘文件有历史事故）。"""
    monkeypatch.setenv(_ENV_SWITCH, "true")
    env["install_model"](raw='{"vendor": "德利行"}')

    assert _upload(_client()).status_code == 200
    assert env["roi_tmp"], "预判成功路径应产出抬头 ROI 临时文件"
    leftovers = [p for p in env["roi_tmp"] if os.path.exists(p)]
    assert leftovers == [], f"预判临时文件未清理: {leftovers}"


# ------------------------------------------------------------------
# 单元层：preprocess.crop_header_roi 的几何口径
# ------------------------------------------------------------------
def test_crop_header_roi_keeps_top_ratio(tmp_path):
    """crop_header_roi 只保留顶部 top_ratio，返回 BGR ndarray。"""
    src = _make_real_jpeg(tmp_path / "src.jpg", size=(400, 600))
    roi = preprocess.crop_header_roi(src, top_ratio=0.25)
    assert roi is not None, "抬头裁剪应返回 ndarray"
    assert roi.shape[0] == 150 and roi.shape[1] == 400, \
        f"应只保留上 25%（600*0.25=150），实际 {roi.shape}"


def test_crop_header_roi_invalid_ratio_falls_back(tmp_path):
    """非法 top_ratio（0/负数/超 1/非数值）回落到默认值，而不是裁出空图或抛异常。"""
    src = _make_real_jpeg(tmp_path / "src2.jpg", size=(200, 400))
    for bad in (0, -1, 1.5, "x", None):
        roi = preprocess.crop_header_roi(src, top_ratio=bad)
        assert roi is not None and roi.shape == (100, 200, 3), \
            f"top_ratio={bad!r} 应回落 {preprocess.HEADER_ROI_DEFAULT_TOP_RATIO}"


def test_crop_header_roi_returns_none_on_undecodable(tmp_path):
    """图片不可解码：返回 None（调用方据此跳过预判），不抛异常。"""
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 32)
    assert preprocess.crop_header_roi(str(bad)) is None


def test_is_enabled_parsing(monkeypatch):
    """开关取值口径：1/true/yes/on 视为开启，其余（含未设、false、0）为关闭。"""
    monkeypatch.delenv(_ENV_SWITCH, raising=False)
    assert vendor_precheck.is_enabled() is False
    for on in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv(_ENV_SWITCH, on)
        assert vendor_precheck.is_enabled() is True, on
    for off in ("0", "false", "", "no", "off"):
        monkeypatch.setenv(_ENV_SWITCH, off)
        assert vendor_precheck.is_enabled() is False, off


# ------------------------------------------------------------------
# 6. cfg 必须来自管理台配置（P1 审核 mustFix M1）
# ------------------------------------------------------------------
def test_predict_vendor_resolves_cfg_from_db_when_cfg_none(tmp_path, monkeypatch):
    """不传 cfg 时必须自行解析管理台配置，而不是走硬编码默认模型。

    判据取「真正解析出来的模型名」而不是「有没有传参」——本仓高发缺陷就是
    「形参声明了但调用点不传」，而原有用例全部打桩 build_recognition_model，
    正好把这条掩盖掉。这里把桩打在 _build（比 build_recognition_model 更低的
    接缝），使 cfg → 模型名的解析逻辑真实执行：若 cfg 丢失，解析结果会变成
    llm.py 里硬编码的默认模型名，断言立刻失败。
    """
    from app import db as app_db
    from app import llm
    from app.models import EngineConfig

    jpg = tmp_path / "roi.jpg"
    _make_real_jpeg(str(jpg))

    seen = {}

    class _FakeModel:
        def invoke(self, messages):
            return SimpleNamespace(content='{"vendor": "德利行"}')

    def _fake_build(kind, model_name, cfg=None, side="rec", use_grey=False):
        seen["kind"] = kind
        seen["model_name"] = model_name
        seen["cfg"] = cfg
        return _FakeModel()

    sentinel = EngineConfig(recognition_model="SENTINEL/model-x")
    monkeypatch.setattr(app_db, "get_engine_config", lambda: sentinel)
    monkeypatch.setattr(llm, "_build", _fake_build)
    monkeypatch.setenv(_ENV_SWITCH, "true")

    # 参照组：cfg=None 时 build_recognition_model 会走 llm.py 里硬编码的默认模型
    # （实测解析为 "Qwen/Qwen3-VL-32B-Instruct" + kind="qwen"，连引擎类型都和
    # 管理台配置不同）。动态取这组值作对照，避免把期望写死成某个具体模型名。
    llm.build_recognition_model(cfg=None)
    default_pair = (seen.get("kind"), seen.get("model_name"))

    seen.clear()
    out = vendor_precheck.predict_vendor(str(jpg))
    assert out == "德利行", out
    assert seen.get("cfg") is sentinel, "必须用管理台配置构模型，不得回落硬编码默认"
    assert (seen.get("kind"), seen.get("model_name")) != default_pair, (
        "解析结果与 cfg=None 的硬编码默认完全相同 → cfg 丢失（绕过管理台配置）")


# ------------------------------------------------------------------
# 7. 开关：settings 键优先于环境变量（P1 审核 mustFix M2）
# ------------------------------------------------------------------
def test_is_enabled_settings_key_wins_over_env(monkeypatch):
    from app import db as app_db

    try:
        monkeypatch.setenv(_ENV_SWITCH, "true")
        app_db.set_app_setting(vendor_precheck.SETTINGS_KEY, "false")
        assert vendor_precheck.is_enabled() is False, "settings 有值时以 settings 为准"

        app_db.set_app_setting(vendor_precheck.SETTINGS_KEY, "true")
        monkeypatch.delenv(_ENV_SWITCH, raising=False)
        assert vendor_precheck.is_enabled() is True, "settings 开启时不依赖环境变量"
    finally:
        # 还原：删掉该键，避免污染同进程其它用例（隔离库内操作，不触 live）
        app_db.set_app_setting(vendor_precheck.SETTINGS_KEY, "")
    monkeypatch.delenv(_ENV_SWITCH, raising=False)
    assert vendor_precheck.is_enabled() is False, "键被清空且无环境变量时必须回到缺省 false"


def test_settings_key_registered_with_default_false():
    """键必须登记进 SETTINGS_DEFAULTS，否则管理台 all_settings() 看不到、无法切。"""
    from app.services import settings_service
    assert "vendor_precheck_enabled" in settings_service.SETTINGS_DEFAULTS
    assert settings_service.SETTINGS_DEFAULTS["vendor_precheck_enabled"] is False
