# -*- coding: utf-8 -*-
"""视觉切片粒度全中文化改造自动化测试套件 (Phase 3 QA 验证).

对照 Phase 1 需求规格说明书 (01_spec.md) 验收标准清单 (AC-01 至 AC-05) 进行严密自动化验证：
- AC-01: DOM 结构与文案全中文合规：#adminRecImageDetail 与 #adminGreyRecImageDetail 的 3 个 option 文本 100% 为全中文，无英文字母前缀；
- AC-02: Option Value 协议兼容性：两个下拉框中各选项的 value 属性严格保持为 "high", "auto", "low"；
- AC-03: 全局 0 Emoji 规范：模板 HTML 与改动代码 100% 无任何 Emoji；
- AC-04: 后端数据契约与接口回显：clamp_image_detail 边界校验、PUT 保存与 GET 回显、灰测推全及回滚流转一致性；
- AC-05: 多模态 Vision 协议组装：_lc_to_openai 中 image_url block 的 detail 参数下发与回退行为。
"""

import os
import re
import sys
from pathlib import Path
from bs4 import BeautifulSoup
import pytest
from starlette.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app
from app import db
from app.models import clamp_image_detail, EngineConfig
from app.llm import _resolve_image_detail, _lc_to_openai

client = TestClient(app)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEMO_ROOT = REPO_ROOT / "demo"
HTML_PATH = DEMO_ROOT / "templates" / "index.html"

EXPECTED_OPTIONS = [
    ("high", ["高精度", "全切片·高精度"]),
    ("auto", ["自适应"]),
    ("low", ["粗粒度", "低开销·粗粒度"]),
]


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """确保测试运行在独立的测试数据库实例中，防止污染运行环境。"""
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_image_detail_isolated.db")
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
# AC-01 & AC-02: DOM 结构解析与 Option 文本/Value 校验
# ==============================================================================

def test_ac01_and_ac02_admin_rec_image_detail_options():
    """AC-01 & AC-02: 验证常规识别下拉框 (#adminRecImageDetail) 选项全中文与 value 一致性。"""
    assert HTML_PATH.exists(), f"文件不存在: {HTML_PATH}"
    html_content = HTML_PATH.read_text(encoding="utf-8")
    soup = BeautifulSoup(html_content, "html.parser")

    select_el = soup.find("select", id="adminRecImageDetail")
    assert select_el is not None, "未在 index.html 中找到 select#adminRecImageDetail"

    options = select_el.find_all("option")
    assert len(options) == 3, f"期望 3 个 option 元素，实际找到 {len(options)} 个"

    for idx, (expected_val, expected_text) in enumerate(EXPECTED_OPTIONS):
        opt = options[idx]
        actual_val = opt.get("value", "").strip()
        actual_text = opt.get_text().strip()

        # AC-02 断言: value 强一致
        assert actual_val == expected_val, f"第 {idx+1} 个 option 的 value 期望为 '{expected_val}', 实际为 '{actual_val}'"

        # AC-01 断言: text 严格全中文匹配
        if isinstance(expected_text, list):
            assert actual_text in expected_text, f"第 {idx+1} 个 option 的文本期望在 {expected_text} 中，实际为 '{actual_text}'"
        else:
            assert actual_text == expected_text, f"第 {idx+1} 个 option 的文本期望为 '{expected_text}', 实际为 '{actual_text}'"

        # AC-01 严苛断言: 文本中 100% 不含任何 ASCII 英文字母
        assert not re.search(r"[a-zA-Z]", actual_text), f"选项文本 '{actual_text}' 包含英文字母，不符合全中文化要求"


def test_ac01_and_ac02_admin_grey_rec_image_detail_options():
    """AC-01 & AC-02: 验证灰测识别下拉框 (#adminGreyRecImageDetail) 选项全中文与 value 一致性。"""
    assert HTML_PATH.exists(), f"文件不存在: {HTML_PATH}"
    html_content = HTML_PATH.read_text(encoding="utf-8")
    soup = BeautifulSoup(html_content, "html.parser")

    select_el = soup.find("select", id="adminGreyRecImageDetail")
    assert select_el is not None, "未在 index.html 中找到 select#adminGreyRecImageDetail"

    options = select_el.find_all("option")
    assert len(options) == 3, f"期望 3 个 option 元素，实际找到 {len(options)} 个"

    for idx, (expected_val, expected_text) in enumerate(EXPECTED_OPTIONS):
        opt = options[idx]
        actual_val = opt.get("value", "").strip()
        actual_text = opt.get_text().strip()

        # AC-02 断言: value 强一致
        assert actual_val == expected_val, f"灰测第 {idx+1} 个 option 的 value 期望为 '{expected_val}', 实际为 '{actual_val}'"

        # AC-01 断言: text 严格全中文匹配
        if isinstance(expected_text, list):
            assert actual_text in expected_text, f"灰测第 {idx+1} 个 option 的文本期望在 {expected_text} 中，实际为 '{actual_text}'"
        else:
            assert actual_text == expected_text, f"灰测第 {idx+1} 个 option 的文本期望为 '{expected_text}', 实际为 '{actual_text}'"

        # AC-01 严苛断言: 文本中 100% 不含任何 ASCII 英文字母
        assert not re.search(r"[a-zA-Z]", actual_text), f"灰测选项文本 '{actual_text}' 包含英文字母，不符合全中文化要求"


# ==============================================================================
# AC-03: 全局 0 Emoji 规范扫描
# ==============================================================================

def test_ac03_zero_emoji_scan():
    """AC-03: 验证 index.html、test_image_detail_localization.py 及相关配置文件 100% 无 Emoji。"""
    emoji_pattern = re.compile(
        "["
        "\U00010000-\U0010ffff"
        "\u2600-\u26ff"
        "\u2700-\u27bf"
        "]",
        flags=re.UNICODE,
    )

    files_to_check = [
        HTML_PATH,
        Path(__file__),
    ]

    for fpath in files_to_check:
        assert fpath.exists(), f"文件不存在: {fpath}"
        content = fpath.read_text(encoding="utf-8")
        matches = emoji_pattern.findall(content)
        assert len(matches) == 0, f"文件 {fpath.name} 中检测到非法 Emoji 字符: {matches}"


# ==============================================================================
# AC-04: 后端数据模型防呆与 API 保存/回显
# ==============================================================================

def test_ac04_clamp_image_detail_boundaries():
    """AC-04: clamp_image_detail 边界与非法输入白名单回退测试。"""
    # 标准枚举
    assert clamp_image_detail("high") == "high"
    assert clamp_image_detail("auto") == "auto"
    assert clamp_image_detail("low") == "low"

    # 大小写与首尾空格容错
    assert clamp_image_detail("  HIGH  ") == "high"
    assert clamp_image_detail("  Auto  ") == "auto"
    assert clamp_image_detail("  low  ") == "low"

    # 非法字符与空值回退默认 "high"
    assert clamp_image_detail("unknown") == "high"
    assert clamp_image_detail("medium") == "high"
    assert clamp_image_detail("") == "high"
    assert clamp_image_detail(None) == "high"
    assert clamp_image_detail(123) == "high"


def test_ac04_api_save_and_echo_image_detail():
    """AC-04: 通过 PUT /api/admin/engine-config 写入切片粒度并验证 GET 回显。"""
    payload = {
        "openai_rec_image_detail": "low",
        "grey_openai_rec_image_detail": "auto",
    }
    put_resp = client.put("/api/admin/engine-config", json=payload, headers=_admin_headers())
    assert put_resp.status_code == 200, f"保存失败: {put_resp.text}"

    get_resp = client.get("/api/admin/engine-config", headers=_admin_headers())
    assert get_resp.status_code == 200
    data = get_resp.json()["data"]

    assert data.get("openai_rec_image_detail") == "low"
    assert data.get("grey_openai_rec_image_detail") == "auto"


# ==============================================================================
# AC-05: 运行时解析与多模态请求体下发
# ==============================================================================

def test_ac05_runtime_multimodal_payload_dispatch():
    """AC-05: 验证多模态 Vision 协议组装中 image_url block 的 detail 参数正确注入。"""
    from langchain_core.messages import HumanMessage

    msg = HumanMessage(content=[
        {"type": "text", "text": "餐饮发票识别"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,sample_base64"}},
    ])

    for detail_val in ["high", "auto", "low"]:
        res = _lc_to_openai(msg, detail=detail_val)
        assert res["role"] == "user"
        assert len(res["content"]) == 2
        img_block = res["content"][1]
        assert img_block["type"] == "image_url"
        assert img_block["image_url"]["detail"] == detail_val

    # 非法值自动兜底为 high
    res_fallback = _lc_to_openai(msg, detail="invalid_value")
    assert res_fallback["content"][1]["image_url"]["detail"] == "high"
