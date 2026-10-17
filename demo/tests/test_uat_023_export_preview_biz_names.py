# -*- coding: utf-8 -*-
"""UAT-023 DEV：繁体报表预览不得 OpenCC 转换业务品名（与 CSV 原值对齐）。

本文件只做源码接线与契约静态门禁；MANUAL_UI（8 种 CSV × Hans→Hant→Hans）留给独立 QA/UAT。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPORT_JS = ROOT / "demo/static/js/export_center.js"
COST_JS = ROOT / "demo/static/js/cost_report.js"
I18N_JS = ROOT / "demo/static/js/i18n/i18n.js"
API_EXPORT = ROOT / "demo/app/api_export.py"


def test_export_center_marks_business_identity_cells_with_i18n_skip():
    src = EXPORT_JS.read_text(encoding="utf-8")
    assert "function isExportBusinessIdentityHeader" in src
    assert "data-i18n-skip" in src
    assert "preserveBiz" in src
    # 品名 / 供应商 / 部门 必须纳入身份列判定
    for token in ("品名", "名称", "供应商", "往来", "部门"):
        assert token in src
    # UAT-023 fix2：旧谓词 h==='部门' || includes('部门名称') 漏掉「归属部门」
    fn = src[src.index("function isExportBusinessIdentityHeader") : src.index("function buildExportRowsHtml")]
    assert "h.includes('部门')" in fn or 'h.includes("部门")' in fn
    assert "归属部门" in fn or "h.includes('部门')" in fn
    assert "销售对象" in fn
    # 表头渲染路径不得给 <th> 加 skip（表头仍翻译）
    thead_block = src[src.index("function renderExportPreviewUI") : src.index("function executeExportDownload")]
    assert "<th" in thead_block
    assert "data-i18n-skip" not in thead_block


def test_export_identity_header_covers_guishu_bumen():
    """Regression: 归属部门 must be treated as identity (skip OpenCC).

    Runtime Hans→Hant paint equality for 归属部门 is gated by
    demo/tests/frontend_uat023_export_preview_harness.js
    (PASS identity header 归属部门 / Hant: 归属部门 cell == CSV Hans).
    """
    src = EXPORT_JS.read_text(encoding="utf-8")
    fn = src[src.index("function isExportBusinessIdentityHeader") : src.index("function buildExportRowsHtml")]
    # Must NOT rely on the leaky old predicate alone
    assert "h === '部门'" not in fn or "h.includes('部门')" in fn
    assert "h.includes('部门')" in fn
    # 旧漏洞：h === '部门' || includes('部门名称') 对「归属部门」为 false
    assert "归属部门" in fn or "h.includes('部门')" in fn
    # 与 QA/UAT identity index 对齐的邻近业务身份列
    assert "销售对象" in fn


def test_cost_report_preserves_department_supplier_and_raw_name():
    src = COST_JS.read_text(encoding="utf-8")
    assert '<span data-i18n-skip>${w2Escape(dep.name)}</span>' in src
    assert '供应商：<span data-i18n-skip>${w2Escape(suppName)}</span>' in src
    assert '<td data-i18n-skip>${w2Escape(r.raw_name || \'-\')}</td>' in src
    # 「已停用」等 UI chrome 不在 skip 内，仍可被转换
    assert "已停用" in src


def test_i18n_skip_mechanism_intact_not_globally_disabled():
    src = I18N_JS.read_text(encoding="utf-8")
    assert 'getAttribute(\'data-i18n-skip\')' in src
    assert "zh-Hant-HK" in src
    assert "openccPass" in src or "OpenCC" in src
    # 负向：不得出现全局关闭转换的开关被默认打开
    assert "DISABLE_HANT" not in src
    assert "convertDisabled" not in src


def test_api_export_preview_rows_remain_unconverted_contract():
    src = API_EXPORT.read_text(encoding="utf-8")
    assert "UAT-023" in src
    assert "data-i18n-skip" in src
    # download 仍只 localize 表头/文件名
    assert 'headers = [localize(h, req.lang) for h in data.get("headers", [])]' in src
    # preview 返回的 headers/rows 不做 localize 行转换
    preview = src[src.index("def preview_report") : src.index("def download_report")]
    assert "localize(" not in preview
    assert 'preview_rows' in preview
