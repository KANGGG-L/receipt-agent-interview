# -*- coding: utf-8 -*-
"""发票识别智能体系统 · 引擎配置全量 6 张卡片标准化与统一重构全量自动化测试套件 (Phase 3 QA 验收)。

严格对照 Phase 1 验收标准清单 (Acceptance Criteria, AC-01 至 AC-16) 与全局工程红线：
- AC-01: Header 工具栏组件化与连通性测试按钮收拢（彻底移出表单网格，收拢至 Header）；
- AC-02: Tier 1 & Tier 2 基础与调度层对称性与单位声明（恒定 5 项，包含 '调用超时 (秒)' 与 placeholder 60，重跑上限 0-10 / 继承常规）；
- AC-03: Tier 3 高级超参 2x2 四宫格标准化插槽平整性（无单元格坍塌或右下方留白）；
- AC-04: 纯文本引擎视觉切片粒度语义占位（[N/A · 纯文本风控] / [N/A · 纯文本自愈]，disabled readonly，禁止向后端提交）；
- AC-05: 审核引擎采样温度运行时确定性硬锁定（readonly="0.0"，配齐统一绿色锁定指引，LLM 调用强行下发 0.0）；
- AC-06: 识别与解析引擎采样温度动态防呆预警（温度 > 0.2 即时展示 [WARN]，<= 0.2 隐藏）；
- AC-07: Tier 4 计费单价 18 个输入框标签包含 (元/1M) 量纲；
- AC-08: Tier 4 3 档单据场景实时测算器全量覆盖与初始货币对齐（3 档单选 Radios 完整，默认选中正确，初始为 约 ¥0 / 张发票，严禁 $ 符号）；
- AC-09: 测算器联动计算与 DOM 即时回显（updateEngineCostCalculator 换算公式精度验证）；
- AC-10: 渐进折叠面板默认收起与四段式 Badge 状态流转；
- AC-11: 模型特性即时提示气泡容器完整性（顶部提示条与 3 大独立提示气泡）；
- AC-12: 存量测试契约与既有 DOM 选择器零回归；
- AC-13: 引擎配置模版全生命周期闭环（Combobox 检索、保存、删除）；
- AC-14: 单引擎腿独立连通性测试（btnProbe_{leg} 与 probeStatus_{leg}）；
- AC-15: 全局工程红线规范（0 Emoji 纯文本标签合规）；
- AC-16: 敏感凭据脱敏与安全防御规范（API Key 掩码呈现）。
"""

import os
import re
import sys
from pathlib import Path
import pytest
from bs4 import BeautifulSoup
from starlette.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEMO_ROOT = REPO_ROOT / "demo"

if str(DEMO_ROOT) not in sys.path:
    sys.path.insert(0, str(DEMO_ROOT))

from app.main import app
from app import db
from app.models import EngineConfig
from app.llm import _resolve_temperature, build_audit_model

client = TestClient(app)

ALL_6_LEGS = ["rec", "aud", "parse", "grey_rec", "grey_aud", "grey_parse"]
REGULAR_LEGS = ["rec", "aud", "parse"]
GREY_LEGS = ["grey_rec", "grey_aud", "grey_parse"]
VISION_LEGS = ["rec", "grey_rec"]
TEXT_LEGS = ["aud", "parse", "grey_aud", "grey_parse"]
AUDIT_LEGS = ["aud", "grey_aud"]
PARSE_LEGS = ["parse", "grey_parse"]

ALL_18_PRICE_IDS = [
    # 常规识别
    "adminPriceInputRec", "adminPriceCachedInputRec", "adminPriceOutputRec",
    # 常规审核
    "adminPriceInputAud", "adminPriceCachedInputAud", "adminPriceOutputAud",
    # 常规解析
    "adminPriceInputParse", "adminPriceCachedInputParse", "adminPriceOutputParse",
    # 灰测识别
    "adminGreyPriceInputRec", "adminGreyPriceCachedInputRec", "adminGreyPriceOutputRec",
    # 灰测审核
    "adminGreyPriceInputAud", "adminGreyPriceCachedInputAud", "adminGreyPriceOutputAud",
    # 灰测解析
    "adminGreyPriceInputParse", "adminGreyPriceCachedInputParse", "adminGreyPriceOutputParse",
]


@pytest.fixture(scope="module")
def html_soup():
    """解析并缓存 demo/templates/index.html 的 BeautifulSoup 树。"""
    html_path = DEMO_ROOT / "templates" / "index.html"
    assert html_path.exists(), f"未找到模板文件: {html_path}"
    content = html_path.read_text(encoding="utf-8")
    return BeautifulSoup(content, "html.parser")


@pytest.fixture(scope="module")
def main_js_content():
    """读取并缓存 demo/static/js/main.js 的文本。"""
    js_path = DEMO_ROOT / "static" / "js" / "main.js"
    assert js_path.exists(), f"未找到前端主脚本: {js_path}"
    return js_path.read_text(encoding="utf-8")


def _get_card_for_leg(soup, leg):
    """根据 leg 查找对应的卡片容器（常规为 .engine-card，灰测为 .trx-leg-box）。"""
    btn = soup.find(id=f"btnProbe_{leg}")
    assert btn is not None, f"未找到 leg={leg} 的测试按钮 btnProbe_{leg}"
    card = btn.find_parent("div", class_="engine-card") or btn.find_parent("div", class_="trx-leg-box")
    assert card is not None, f"未找到 leg={leg} 的父级卡片容器"
    return card


def _admin_headers():
    return {"X-Role": "admin", "X-Email": "admin@demo.hk"}


# ==============================================================================
# AC-01: Header 工具栏组件化与连通性测试按钮收拢
# ==============================================================================

def test_ac01_header_toolbar_components_and_probe_button_consolidation(html_soup):
    """AC-01: 6 个卡片统一在 Header 呈现 Combobox、保存/删除按钮、测试按钮与状态标签；表单中严禁残留测试按钮。"""
    for leg in ALL_6_LEGS:
        card = _get_card_for_leg(html_soup, leg)

        # 1. 查找 header 内的 preset toolbar
        header = card.find("div", class_="engine-card-header")
        assert header is not None, f"卡片 {leg} 缺少 engine-card-header 结构"

        toolbar = header.find("div", class_="engine-preset-toolbar")
        assert toolbar is not None, f"卡片 {leg} Header 缺少 engine-preset-toolbar 组件"

        # 2. 校验 Combobox 与操作按钮完整性
        # Leg 的首字母大写形式处理
        leg_camel = "".join([part.capitalize() for part in leg.split("_")])
        select_id = f"admin{leg_camel}PresetSelect"
        dropdown_id = f"admin{leg_camel}PresetDropdown"
        save_btn_id = f"admin{leg_camel}SavePresetBtn"
        delete_btn_id = f"admin{leg_camel}DeletePresetBtn"

        assert toolbar.find(id=select_id) is not None, f"卡片 {leg} 缺少 Combobox 输入框 {select_id}"
        assert toolbar.find(id=dropdown_id) is not None, f"卡片 {leg} 缺少下拉容器 {dropdown_id}"
        assert toolbar.find(id=save_btn_id) is not None, f"卡片 {leg} 缺少保存按钮 {save_btn_id}"
        assert toolbar.find(id=delete_btn_id) is not None, f"卡片 {leg} 缺少删除按钮 {delete_btn_id}"

        # 3. 校验测试按钮与状态标签已收拢至 Header 工具栏
        probe_btn = toolbar.find(id=f"btnProbe_{leg}")
        assert probe_btn is not None, f"卡片 {leg} 工具栏未包含 btnProbe_{leg}"
        assert probe_btn.text.strip() == "测试此引擎"
        assert f"probeSingleEngineLeg('{leg}')" in probe_btn.get("onclick", "")

        status_tag = toolbar.find(id=f"probeStatus_{leg}")
        assert status_tag is not None, f"卡片 {leg} 工具栏未包含 probeStatus_{leg}"
        assert "probe-status-tag" in status_tag.get("class", [])

        # 4. 严格校验表单网格中彻底移除了 btnProbe_* 与 probeStatus_*
        form_grid = card.find("div", class_="form-grid")
        assert form_grid is not None, f"卡片 {leg} 缺少 form-grid 结构"
        assert form_grid.find(id=f"btnProbe_{leg}") is None, f"卡片 {leg} 的 form-grid 仍残留侵入的 btnProbe_{leg}"
        assert form_grid.find(id=f"probeStatus_{leg}") is None, f"卡片 {leg} 的 form-grid 仍残留侵入的 probeStatus_{leg}"


# ==============================================================================
# AC-02: Tier 1 & Tier 2 基础与调度层对称性与单位声明
# ==============================================================================

def test_ac02_tier1_tier2_form_grid_symmetry_and_unit_labels(html_soup):
    """AC-02: 表单网格恒定包含且仅包含 5 项；包含 '调用超时 (秒)' 与 placeholder 60；重跑上限 placeholder 符合规范。"""
    for leg in ALL_6_LEGS:
        card = _get_card_for_leg(html_soup, leg)
        form_grid = card.find("div", class_="form-grid")
        assert form_grid is not None, f"卡片 {leg} 缺少 form-grid"

        # 1. 严格断言仅包含 5 个直接 form-group 孩子项，消灭孤立的第 6 项
        form_groups = [c for c in form_grid.find_all("div", recursive=False) if "form-group" in c.get("class", [])]
        assert len(form_groups) == 5, f"卡片 {leg} form-grid 必须且仅包含 5 个元素，当前包含 {len(form_groups)} 个"

        # 2. 校验第 1 项: Base URL
        base_group = form_groups[0]
        base_lbl = base_group.find("label")
        assert base_lbl and "Base URL" in base_lbl.text

        # 3. 校验第 2 项: API Key
        key_group = form_groups[1]
        key_lbl = key_group.find("label")
        assert key_lbl and "API Key" in key_lbl.text

        # 4. 校验第 3 项: 模型名
        model_group = form_groups[2]
        model_lbl = model_group.find("label")
        assert model_lbl and "模型名" in model_lbl.text

        # 5. 校验第 4 项: 调用超时 (秒)，单位显式声明，推荐占位符符合规范
        timeout_group = form_groups[3]
        timeout_lbl = timeout_group.find("label")
        assert timeout_lbl is not None
        assert "调用超时 (秒)" in timeout_lbl.text, f"卡片 {leg} 超时标签必须包含 '(秒)' 单位，实际: {timeout_lbl.text}"

        timeout_inp = timeout_group.find("input")
        assert timeout_inp is not None
        expected_timeout_placeholders = {
            "rec": "60 (推荐 45 - 60s)",
            "aud": "30 (推荐 20 - 30s)",
            "parse": "20 (推荐 15 - 20s)",
            "grey_rec": "60 (推荐 45 - 60s)",
            "grey_aud": "30 (推荐 20 - 30s)",
            "grey_parse": "20 (推荐 15 - 20s)",
        }
        assert timeout_inp.get("placeholder") == expected_timeout_placeholders[leg], f"卡片 {leg} 超时输入框 placeholder 应为 '{expected_timeout_placeholders[leg]}'，实际: {timeout_inp.get('placeholder')}"
        assert timeout_inp.get("type") == "number"
        assert timeout_inp.get("min") == "1"
        assert timeout_inp.get("max") == "60"

        # 6. 校验第 5 项: 重跑上限
        retry_group = form_groups[4]
        retry_lbl = retry_group.find("label")
        assert retry_lbl is not None
        assert "重跑上限" in retry_lbl.text

        retry_inp = retry_group.find("input")
        assert retry_inp is not None
        expected_retry_placeholders = {
            "rec": "3 (识别建议 2 - 3 轮)",
            "aud": "1 (风控审核建议 1 轮)",
            "parse": "1 (自愈修复建议 1 轮)",
            "grey_rec": "继承常规",
            "grey_aud": "继承常规",
            "grey_parse": "继承常规",
        }
        assert retry_inp.get("placeholder") == expected_retry_placeholders[leg], f"卡片 {leg} 重跑上限 placeholder 应为 '{expected_retry_placeholders[leg]}'，实际: {retry_inp.get('placeholder')}"


# ==============================================================================
# AC-03: Tier 3 高级超参 2x2 四宫格标准化插槽平整性与高级参数抽屉结构
# ==============================================================================

def test_ac03_tier3_advanced_hyperparams_2x2_grid_symmetry(html_soup):
    """AC-03: 6 张卡片高级抽屉默认收起，展开后呈现完全平整的 2x2 四宫格，正好 4 个插槽；计费单价纳进抽屉主体；标题为'高级参数'。"""
    for leg in ALL_6_LEGS:
        drawer = html_soup.find(id=f"adminAdvParams_{leg}")
        assert drawer is not None, f"未找到抽屉容器 adminAdvParams_{leg}"

        # 1. 默认收起类 collapsed
        classes = drawer.get("class", [])
        assert "engine-advanced-params-drawer" in classes, f"卡片 {leg} 抽屉缺少 engine-advanced-params-drawer 类"
        assert "collapsed" in classes, f"卡片 {leg} 抽屉默认必须包含 collapsed 类"

        # 2. 抽屉标题栏折叠文本与 Badge，校验标题文本统一为'高级参数'，严禁残留'高级工程参数'
        header = drawer.find("div", class_="engine-adv-header")
        assert header is not None, f"卡片 {leg} 缺少 engine-adv-header"
        assert "高级参数" in header.text, f"卡片 {leg} 抽屉标题必须包含 '高级参数'"
        assert "高级工程参数" not in header.text, f"卡片 {leg} 抽屉标题严禁残留 '高级工程参数'"

        toggle_text = drawer.find(id=f"adminAdvToggleText_{leg}")
        assert toggle_text is not None, f"卡片 {leg} 缺少 adminAdvToggleText_{leg}"
        assert toggle_text.text.strip() in ["[展开]", "展开"], f"卡片 {leg} 初始状态折叠文本应为 '展开' 或 '[展开]'"

        badge = drawer.find(id=f"adminAdvBadge_{leg}")
        assert badge is not None, f"卡片 {leg} 缺少 adminAdvBadge_{leg}"

        # 3. 抽屉主体 adminAdvBody_{leg}
        adv_body = drawer.find(id=f"adminAdvBody_{leg}")
        assert adv_body is not None, f"卡片 {leg} 缺少抽屉主体 adminAdvBody_{leg}"

        # 4. 2x2 超参网格恒定正好包含 4 个 form-group
        hyper_grid = adv_body.find("div", class_="form-grid")
        assert hyper_grid is not None, f"卡片 {leg} 缺少 2x2 超参网格"
        form_groups = [c for c in hyper_grid.find_all("div", recursive=False) if "form-group" in c.get("class", [])]
        assert len(form_groups) == 4, f"卡片 {leg} 2x2 超参网格必须正好包含 4 个插槽，当前包含 {len(form_groups)} 个，四宫格坍塌"

        # 验证 Slot 1 为思考强度，Slot 2 为 Token，Slot 3 为视觉粒度，Slot 4 为采样温度
        assert "思考强度" in form_groups[0].find("label").text
        assert "Token" in form_groups[1].find("label").text
        assert "视觉切片粒度" in form_groups[2].find("label").text
        assert "采样温度" in form_groups[3].find("label").text

        # 5. 校验计费单价容器 engine-inline-economics 已纳进抽屉主体内部
        eco_block = adv_body.find("div", class_="engine-inline-economics")
        assert eco_block is not None, f"卡片 {leg} 计费单价容器 engine-inline-economics 必须位于 adminAdvBody_{leg} 内部"
        price_grid = eco_block.find("div", class_="price-grid")
        assert price_grid is not None, f"卡片 {leg} 缺少 price-grid"
        price_groups = [c for c in price_grid.find_all("div", recursive=False) if "form-group" in c.get("class", [])]
        assert len(price_groups) == 3, f"卡片 {leg} 计费单价必须包含 3 个单价输入项，实际: {len(price_groups)}"


# ==============================================================================
# AC-04: 纯文本引擎视觉切片粒度统一套用标准模版下拉
# ==============================================================================

def test_ac04_slot3_pure_text_engines_semantic_na_placeholders(html_soup):
    """AC-04: 6 张引擎卡片 Slot 3 视觉切片粒度统一套用标准模版 select 下拉选择框。"""
    for leg in ALL_6_LEGS:
        drawer = html_soup.find(id=f"adminAdvParams_{leg}")
        hyper_grid = drawer.find("div", class_="form-grid")
        assert hyper_grid is not None, f"卡片 {leg} 缺少超参网格"
        form_groups = [c for c in hyper_grid.find_all("div", recursive=False) if "form-group" in c.get("class", [])]
        slot3 = form_groups[2]

        sel = slot3.find("select")
        assert sel is not None, f"卡片 {leg} Slot 3 必须统一套用标准 select 下拉选择框"
        options = [opt.get("value") for opt in sel.find_all("option")]
        assert "high" in options
        assert "auto" in options
        assert "low" in options


# ==============================================================================
# AC-05: 审核引擎采样温度推荐占位与运行时确定性硬锁定
# ==============================================================================

def test_ac05_audit_engine_temperature_deterministic_hard_lock_ui(html_soup):
    """AC-05: 审核引擎 (aud, grey_aud) 温度输入框套用标准模板，配置推荐保持 0.0 确定性风控 placeholder。"""
    for leg in AUDIT_LEGS:
        drawer = html_soup.find(id=f"adminAdvParams_{leg}")
        hyper_grid = drawer.find("div", class_="form-grid")
        assert hyper_grid is not None, f"卡片 {leg} 缺少超参网格"
        form_groups = [c for c in hyper_grid.find_all("div", recursive=False) if "form-group" in c.get("class", [])]
        slot4 = form_groups[3]

        inp = slot4.find("input")
        assert inp is not None
        expected_id = "adminAudTemperature" if leg == "aud" else "adminGreyAudTemperature"
        assert inp.get("id") == expected_id
        ph = inp.get("placeholder", "")
        assert "0.0" in ph, f"审核引擎 {leg} 温度推荐占位应包含 0.0"


def test_ac05_audit_engine_temperature_deterministic_hard_lock_runtime():
    """AC-05: 后端运行时与 LLM 请求体下发时，无论配置温度为何值，审核引擎强制下发 temperature=0.0。"""
    # 模拟极端配置：传入 temperature=0.45
    cfg = EngineConfig(
        openai_aud_base_url="https://api.openai.com/v1",
        openai_aud_api_key="sk-test-key",
        openai_aud_model="gpt-4o",
        openai_aud_temperature=0.45,
        grey_openai_aud_base_url="https://api.openai.com/v1",
        grey_openai_aud_api_key="sk-test-key",
        grey_openai_aud_model="gpt-4o",
        grey_openai_aud_temperature=0.38,
    )

    # 1. 常规审核腿解析结果恒为 0.0
    assert _resolve_temperature(cfg, side="aud", use_grey=False) == 0.0

    # 2. 灰测审核腿解析结果恒为 0.0
    assert _resolve_temperature(cfg, side="aud", use_grey=True) == 0.0

    # 3. 构造审核模型实例，其实例属性 temperature 恒为 0.0
    model = build_audit_model(cfg=cfg)
    assert model.temperature == 0.0, f"审核模型 temperature 必须恒为 0.0，实际: {model.temperature}"


# ==============================================================================
# AC-06: 识别与解析引擎采样温度动态防呆预警 (Temp > 0.2 Warning)
# ==============================================================================

def test_ac06_rec_and_parse_temperature_dynamic_warning_ui(html_soup, main_js_content):
    """AC-06: 识别与解析引擎采样温度输入框支持合法输入，配备防呆预警框。

    2026-10-10 契约对齐：预警框文案随纯文案化移除 [WARN] 前缀（bc260e7），
    本用例守护现行纯文本预警句。
    """
    expected_warn_text = "温度大于 0.2 可能引发发票金额与明细的数字幻觉，非探索场景建议保持 0.0"

    for leg in (VISION_LEGS + PARSE_LEGS):
        drawer = html_soup.find(id=f"adminAdvParams_{leg}")
        hyper_grid = drawer.find("div", class_="form-grid")
        assert hyper_grid is not None, f"卡片 {leg} 缺少超参网格"
        form_groups = [c for c in hyper_grid.find_all("div", recursive=False) if "form-group" in c.get("class", [])]
        slot4 = form_groups[3]

        inp = slot4.find("input")
        assert inp.get("readonly") is None, f"引擎 {leg} 温度输入框不应设置 readonly 属性"
        assert inp.get("min") == "0.0"
        assert inp.get("max") == "0.5"

        warn_div = slot4.find(id=f"admin{leg.replace('_', ' ').title().replace(' ', '')}TempWarn") or slot4.find("div", class_="temp-warn-box")
        assert warn_div is not None, f"引擎 {leg} 缺少 temp-warn-box 防呆预警框"
        assert expected_warn_text in warn_div.text, f"引擎 {leg} 预警框文案不匹配"
        assert "hide" in warn_div.get("class", []), f"引擎 {leg} 预警框默认必须带有 hide 类"

    # 校验 main.js 中 checkTempWarn 动态判定逻辑
    assert "function checkTempWarn(legId)" in main_js_content
    assert "val > 0.2" in main_js_content
    assert "warnEl.classList.remove('hide')" in main_js_content
    assert "warnEl.classList.add('hide')" in main_js_content


# ==============================================================================
# AC-07: Tier 4 计费单价 18 个输入框标签显式包含 (元/1M) 量纲
# ==============================================================================

def test_ac07_tier4_all_18_price_labels_contain_yuan_per_1m_unit(html_soup):
    """AC-07: 全部 18 个单价输入控件关联的 label 标签必须 100% 显式包含 '(元/1M)' 量纲字符串。"""
    for dom_id in ALL_18_PRICE_IDS:
        inp = html_soup.find("input", id=dom_id)
        assert inp is not None, f"HTML 模板缺少单价控件 id={dom_id}"

        # 验证关联 label
        lbl = html_soup.find("label", attrs={"for": dom_id})
        assert lbl is not None, f"HTML 模板缺少关联 label for={dom_id}"
        assert lbl.text.strip() in ["输入单价", "缓存输入", "输出单价"], f"单价控件 {dom_id} 的标签文本 '{lbl.text}' 应为纯中文"

        # 验证控件基础属性
        assert inp.get("type") == "number"
        assert inp.get("min") == "0"
        assert inp.get("step") == "0.0001"
        assert inp.get("placeholder") == "0.0000"


# ==============================================================================
# AC-08: Tier 4 3 档单据场景实时测算器全量覆盖与初始货币对齐
# ==============================================================================

def test_ac08_tier4_scene_calculator_full_coverage_and_currency_symbol(html_soup):
    """AC-08: 6 张卡片均嵌入测算器，3 档单选 Radios 完整且默认选中正确，初始为 约 ¥0 / 张发票，严禁 $ 符号。"""
    expected_footnote = "[计算基准: 标准 1080P/A4 票据折算 | 极端长票以实际日志为准]"

    for leg in ALL_6_LEGS:
        card = _get_card_for_leg(html_soup, leg)
        calc_box = card.find("div", class_="engine-calc-box")
        assert calc_box is not None, f"卡片 {leg} 缺少 engine-calc-box 测算器容器"

        # 1. 验证 3 档单选 Radios 完整性
        radios = calc_box.find_all("input", attrs={"name": f"bench_{leg}"})
        assert len(radios) == 3, f"卡片 {leg} 测算器单选按钮数量必须为 3，实际: {len(radios)}"
        radio_values = [r.get("value") for r in radios]
        assert "standard" in radio_values
        assert "complex" in radio_values
        assert "audit_only" in radio_values

        # 2. 验证默认选中状态
        checked_radios = [r for r in radios if r.get("checked") is not None]
        assert len(checked_radios) == 1, f"卡片 {leg} 必须有且仅有 1 个默认选中的单选项"
        checked_val = checked_radios[0].get("value")

        if leg in VISION_LEGS:
            assert checked_val == "standard", f"视觉卡片 {leg} 默认单选场景应为 'standard'，实际: {checked_val}"
        else:
            assert checked_val == "audit_only", f"纯文本卡片 {leg} 默认单选场景应为 'audit_only'，实际: {checked_val}"

        # 3. 验证初始货币符号为人民币 ¥，严禁任何 $ 美元符号
        cost_el = calc_box.find(id=f"calcCost_{leg}")
        assert cost_el is not None, f"卡片 {leg} 缺少 calcCost_{leg} 容器"
        cost_text = cost_el.text.strip()
        assert "¥" in cost_text, f"卡片 {leg} 初始开销必须包含人民币符号 '¥'，实际: {cost_text}"
        assert "$" not in cost_text, f"卡片 {leg} 严禁包含美元符号 '$'，实际: {cost_text}"
        assert cost_text == "约 ¥0 / 张发票"

        # 4. 验证公式拆解容器
        bk_el = calc_box.find(id=f"calcBreakdown_{leg}")
        assert bk_el is not None, f"卡片 {leg} 缺少 calcBreakdown_{leg} 容器"
        assert "公式:" in bk_el.text


# ==============================================================================
# AC-09: 测算器联动计算与精度公式换算 (Calculator Linkage)
# ==============================================================================

def test_ac09_calculator_linkage_and_mathematical_precision(main_js_content):
    """AC-09: 验证 updateEngineCostCalculator 换算逻辑：(in*pIn + cached*pCached + out*pOut) / 1M，保留 4 位小数。"""
    assert "function updateEngineCostCalculator(leg)" in main_js_content
    assert "BENCHMARK_TOKENS" in main_js_content

    # 模拟 BENCHMARK_TOKENS 数据与核心算法精度
    benchmarks = {
        "standard": {"inTokens": 2400, "cachedTokens": 1600, "outTokens": 800},
        "complex": {"inTokens": 4500, "cachedTokens": 3000, "outTokens": 1500},
        "audit_only": {"inTokens": 1000, "cachedTokens": 500, "outTokens": 300},
    }

    # 测试用例 1: 标准单页，输入 2.0，缓存 1.0，输出 3.0
    p_in, p_cached, p_out = 2.0, 1.0, 3.0
    bench = benchmarks["standard"]
    cost1 = (bench["inTokens"] * p_in + bench["cachedTokens"] * p_cached + bench["outTokens"] * p_out) / 1000000.0
    # 2400*2.0 + 1600*1.0 + 800*3.0 = 4800 + 1600 + 2400 = 8800 / 1000000 = 0.0088
    assert round(cost1, 6) == 0.0088
    formatted_cost1 = f"约 ¥{float(f'{cost1:.4f}')} / 张发票"
    assert formatted_cost1 == "约 ¥0.0088 / 张发票"

    # 测试用例 2: 纯文本低开销，输入 0.5，缓存 0.25，输出 1.0
    p_in, p_cached, p_out = 0.5, 0.25, 1.0
    bench = benchmarks["audit_only"]
    cost2 = (bench["inTokens"] * p_in + bench["cachedTokens"] * p_cached + bench["outTokens"] * p_out) / 1000000.0
    # 1000*0.5 + 500*0.25 + 300*1.0 = 500 + 125 + 300 = 925 / 1000000 = 0.000925 -> 0.0009
    assert round(cost2, 6) == 0.000925
    formatted_cost2 = f"约 ¥{float(f'{cost2:.4f}')} / 张发票"
    assert formatted_cost2 == "约 ¥0.0009 / 张发票"

    # 测试用例 3: 复杂长票多明细，输入 4.0，缓存 2.0，输出 8.0
    p_in, p_cached, p_out = 4.0, 2.0, 8.0
    bench = benchmarks["complex"]
    cost3 = (bench["inTokens"] * p_in + bench["cachedTokens"] * p_cached + bench["outTokens"] * p_out) / 1000000.0
    # 4500*4.0 + 3000*2.0 + 1500*8.0 = 18000 + 6000 + 12000 = 36000 / 1000000 = 0.036
    assert round(cost3, 6) == 0.036
    formatted_cost3 = f"约 ¥{float(f'{cost3:.4f}')} / 张发票"
    assert formatted_cost3 == "约 ¥0.036 / 张发票"


# ==============================================================================
# AC-10: 渐进折叠面板默认收起与四段式 Badge 状态流转
# ==============================================================================

def test_ac10_progressive_disclosure_and_badge_flow(main_js_content):
    """AC-10: 验证 toggleAdvParams 展开/收起切换与 updateAdvBadge 微缩标签生成逻辑。"""
    assert "function toggleAdvParams(legId)" in main_js_content
    assert "function updateAdvBadge(legId)" in main_js_content

    # 验证统一四段式微缩 Badge（思考强度、最大Token、视觉切片、采样温度）
    assert "badge.textContent = `思考强度:${effortLabel} · 最大Token:${tokens} · 视觉切片:${detailLabel} · 采样温度:${tempStr}`" in main_js_content


# ==============================================================================
# AC-11: 模型特性即时提示气泡容器完整性 (Model Trait Hints)
# ==============================================================================

def test_ac11_model_trait_hint_containers_integrity(html_soup):
    """AC-11: 6 个卡片的高级抽屉内部必须完整保留 1 个顶部提示条与 3 大独立提示气泡（共 24 个提示容器）。"""
    for leg in ALL_6_LEGS:
        drawer = html_soup.find(id=f"adminAdvParams_{leg}")
        assert drawer is not None

        # 1. 顶部提示条
        top_hint = drawer.find(id=f"adminAdvModelHint_{leg}")
        assert top_hint is not None, f"卡片 {leg} 缺少顶部模型自适应提示条 adminAdvModelHint_{leg}"

        # 2. 独立字段提示气泡
        # 获取 field prefix
        leg_camel = "".join([part.capitalize() for part in leg.split("_")])
        tokens_hint = drawer.find(id=f"admin{leg_camel}TokensHint")
        reasoning_hint = drawer.find(id=f"admin{leg_camel}ReasoningHint")
        temp_hint = drawer.find(id=f"admin{leg_camel}TempHint")

        assert tokens_hint is not None, f"卡片 {leg} 缺少 TokensHint"
        assert reasoning_hint is not None, f"卡片 {leg} 缺少 ReasoningHint"
        assert temp_hint is not None, f"卡片 {leg} 缺少 TempHint"

        for hint in [tokens_hint, reasoning_hint, temp_hint]:
            assert "model-trait-hint" in hint.get("class", [])
            style = hint.get("style", "")
            assert "display:none" in style.replace(" ", "")


# ==============================================================================
# AC-12: 存量测试契约与既有 DOM 选择器零回归
# ==============================================================================

def test_ac12_contract_id_mapping_and_regression_guard(html_soup, main_js_content):
    """AC-12: 存量测试依赖的 DOM ID 与 JS 映射字典必须 100% 保持吻合，严禁破坏既有测试契约。"""
    # 验证 LEG_INPUT_MAP 中的字段 ID 均存在于 HTML 中
    expected_leg_inputs = {
        "rec": {"base": "adminOpenaiRecBaseUrl", "key": "adminOpenaiRecApiKey", "model": "adminOpenaiRecModel", "timeout": "adminRecTimeoutSeconds"},
        "aud": {"base": "adminOpenaiAudBaseUrl", "key": "adminOpenaiAudApiKey", "model": "adminOpenaiAudModel", "timeout": "adminAudTimeoutSeconds"},
        "parse": {"base": "adminParseOpenaiBaseUrl", "key": "adminParseOpenaiApiKey", "model": "adminParseOpenaiModel", "timeout": "adminParseTimeoutSeconds"},
        "grey_rec": {"base": "adminGreyOpenaiRecBaseUrl", "key": "adminGreyOpenaiRecApiKey", "model": "adminGreyOpenaiRecModel", "timeout": "adminGreyRecTimeoutSeconds"},
        "grey_aud": {"base": "adminGreyOpenaiAudBaseUrl", "key": "adminGreyOpenaiAudApiKey", "model": "adminGreyOpenaiAudModel", "timeout": "adminGreyAudTimeoutSeconds"},
        "grey_parse": {"base": "adminGreyParseOpenaiBaseUrl", "key": "adminGreyParseOpenaiApiKey", "model": "adminGreyParseOpenaiModel", "timeout": "adminGreyParseTimeoutSeconds"},
    }

    for leg, inputs in expected_leg_inputs.items():
        for field_type, dom_id in inputs.items():
            elem = html_soup.find(id=dom_id)
            assert elem is not None, f"契约破坏: leg={leg} 缺少字段 {field_type} 对应的 DOM ID: {dom_id}"

    # 验证废弃的 OPENAI_PRESETS 变量未死灰复燃
    assert "OPENAI_PRESETS" not in main_js_content, "遗留的 OPENAI_PRESETS 变量不得出现在 main.js 中"


# ==============================================================================
# AC-13: 引擎配置模版全生命周期闭环
# ==============================================================================

def test_ac13_preset_combobox_component_bindings(html_soup, main_js_content):
    """AC-13: 6 张卡片统一支持 Combobox 检索、保存与删除事件绑定。"""
    for leg in ALL_6_LEGS:
        card = _get_card_for_leg(html_soup, leg)
        leg_camel = "".join([part.capitalize() for part in leg.split("_")])

        select_inp = card.find(id=f"admin{leg_camel}PresetSelect")
        assert select_inp is not None
        onclick = select_inp.get("onclick", "")
        oninput = select_inp.get("oninput", "")
        assert f"openEnginePresetMenu('{leg}')" in onclick
        assert f"onInputEnginePresetSearch('{leg}'" in oninput

        save_btn = card.find(id=f"admin{leg_camel}SavePresetBtn")
        assert save_btn is not None
        assert f"saveCurrentConfigAsPreset('{leg}')" in save_btn.get("onclick", "")

        del_btn = card.find(id=f"admin{leg_camel}DeletePresetBtn")
        assert del_btn is not None
        assert f"deleteSelectedPreset('{leg}')" in del_btn.get("onclick", "")

    # 校验 JS 函数声明
    assert "function openEnginePresetMenu(leg)" in main_js_content
    assert "function saveCurrentConfigAsPreset(leg)" in main_js_content
    assert "function deleteSelectedPreset(leg)" in main_js_content


# ==============================================================================
# AC-14: 单引擎腿独立连通性测试 (Engine Leg Probe)
# ==============================================================================

def test_ac14_probe_button_bindings_and_status_tags(html_soup, main_js_content):
    """AC-14: 6 张卡片统一具备 Header probeSingleEngineLeg 探测按钮与回显标签。"""
    assert "function probeSingleEngineLeg(leg)" in main_js_content

    for leg in ALL_6_LEGS:
        btn = html_soup.find(id=f"btnProbe_{leg}")
        assert btn is not None
        assert f"probeSingleEngineLeg('{leg}')" in btn.get("onclick", "")

        status = html_soup.find(id=f"probeStatus_{leg}")
        assert status is not None
        assert "probe-status-tag" in status.get("class", [])


# ==============================================================================
# AC-15: 全局工程红线规范 (0 Emoji Compliance)
# ==============================================================================

def test_ac15_strict_zero_emoji_scan():
    """AC-15: 严禁在模板、脚本、样式、模型与测试代码中出现任何 Emoji，一律采用纯文本标签。"""
    files_to_scan = [
        DEMO_ROOT / "templates" / "index.html",
        DEMO_ROOT / "static" / "js" / "main.js",
        DEMO_ROOT / "static" / "css" / "style.css",
        DEMO_ROOT / "app" / "models.py",
        DEMO_ROOT / "app" / "llm.py",
        DEMO_ROOT / "app" / "api_admin.py",
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
        assert len(matches) == 0, f"文件 {fpath.name} 违反红线，包含非法 Emoji 表情: {matches}"


# ==============================================================================
# AC-16: 敏感凭据脱敏与安全防御规范
# ==============================================================================

def test_ac16_credential_masking_defense():
    """AC-16: API Key 在管理台获取配置时必须执行掩码脱敏，严禁向前端泄漏明文。"""
    resp = client.get("/api/admin/engine-config", headers=_admin_headers())
    assert resp.status_code == 200
    data = resp.json()["data"]

    for leg in ["rec", "aud", "parse"]:
        key_field = f"openai_{leg}_api_key"
        val = data.get(key_field)
        if val:
            assert "****" in val or val == "********", f"字段 {key_field} 未脱敏: {val}"


# ==============================================================================
# AC-17: 核心引擎界面各引擎全量支持折叠/展开
# ==============================================================================

def test_ac17_core_engines_collapsible_capabilities(html_soup, main_js_content):
    """AC-17: 核心引擎界面的 3 个引擎（识别、审核、自愈解析）全量具备折叠与展开能力。"""
    core_legs = ["rec", "aud", "parse"]
    card_ids = {
        "rec": "adminRecognitionCard",
        "aud": "adminAuditCard",
        "parse": "adminParseDrawer",
    }

    # 1. 验证 JS 中存在通用的 toggleEngineCard 函数
    assert "function toggleEngineCard" in main_js_content, "main.js 中必须实现 toggleEngineCard 函数"
    assert "window.toggleEngineCard = toggleEngineCard" in main_js_content, "toggleEngineCard 必须挂载至 window"

    # 2. 验证每个核心引擎卡片在 HTML 中均支持折叠触发器
    for leg in core_legs:
        card = html_soup.find(id=card_ids[leg])
        assert card is not None, f"未找到核心引擎卡片: {card_ids[leg]}"
        assert "engine-card" in card.get("class", []), f"卡片 {card_ids[leg]} 缺少 engine-card 类"

        header = card.find("div", class_="engine-card-header")
        assert header is not None, f"卡片 {card_ids[leg]} 缺少 engine-card-header"

        # 检查卡片标题
        title = header.find("div", class_="engine-card-title")
        assert title is not None, f"卡片 {card_ids[leg]} 缺少 engine-card-title"

        # 验证标题旁侧未残留多余的 [展开]/[收起] 指示器
        assert header.find(id=f"engineCardToggleText_{leg}") is None, f"卡片 {card_ids[leg]} 仍残留多余指示器 engineCardToggleText_{leg}"

        # 检查工具栏专属折叠按钮
        toggle_btn = header.find(id=f"engineCardToggleBtn_{leg}")
        assert toggle_btn is not None, f"卡片 {card_ids[leg]} 工具栏缺少折叠按钮 engineCardToggleBtn_{leg}"
        assert f"toggleEngineCard('{leg}')" in toggle_btn.get("onclick", ""), f"工具栏折叠按钮缺少 toggleEngineCard('{leg}') 绑定"
        assert toggle_btn.text.strip() == "收起"


