"""
自动化验证套件：全局数据表格完整展示与排版治理规范检验
验证点包括：
1. CSS 规范：解除全局 table.data-table 的 table-layout: fixed，杜绝单元格粗暴 ellipsis 截断；
2. 容器规范：.table-container 配置平滑横向滚动与轻量滚动条；
3. 模板规范：#archiveTable, #payablesTable 等主要展示表格彻底移除 table-layout: fixed 行内样式；
4. JS 动态渲染规范：
   - 挽回与点踩指标 (#analyticsRecentEventsTable)：日期时间无 T、完整业务说明不截断、properties 完整展示、小卡片等宽弹性布局；
   - 埋点事件分布 (#analyticsEventDistTable)：自动撑宽、中文说明自适应折行无截断；
   - A/B 实验列表 (#analyticsExperimentsTable)：auto 布局、列宽自适应、明确对齐规则；
5. 全局红线：代码、模板与数据中零 Emoji 出现。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def test_css_table_data_table_governance():
    css_path = ROOT / "demo" / "static" / "css" / "style.css"
    content = css_path.read_text(encoding="utf-8")

    # 1. table.data-table 默认布局必须为 auto
    match_dt = re.search(r"table\.data-table\s*\{([^}]+)\}", content)
    assert match_dt, "table.data-table 规则必须存在"
    dt_block = match_dt.group(1)
    assert "table-layout: auto" in dt_block, "table.data-table 默认 table-layout 必须为 auto，严禁 fixed 压缩列宽"
    assert "table-layout: fixed" not in dt_block, "table.data-table 严禁写死 table-layout: fixed"

    # 2. table.data-table td 默认不得出现强制 ellipsis 截断
    match_td = re.search(r"table\.data-table td\s*\{([^}]+)\}", content)
    assert match_td, "table.data-table td 规则必须存在"
    td_block = match_td.group(1)
    assert "text-overflow: clip" in td_block or "overflow: visible" in td_block, "td 必须保持 overflow: visible 或 text-overflow: clip"
    assert "text-overflow: ellipsis" not in td_block, "td 默认规则严禁包含 text-overflow: ellipsis"
    assert "white-space: nowrap" not in td_block, "td 默认规则严禁包含 white-space: nowrap，长文本必须允许自适应折行"

    # 3. .table-container 必须支持平滑横向滚动条
    match_tc = re.search(r"\.table-container\s*\{([^}]+)\}", content)
    assert match_tc, ".table-container 必须存在"
    tc_block = match_tc.group(1)
    assert "overflow-x: auto" in tc_block, ".table-container 必须具备 overflow-x: auto"
    assert "-webkit-overflow-scrolling: touch" in tc_block, ".table-container 必须支持 iOS/macOS 平滑触控滑动"


def test_html_templates_table_fixed_removed():
    # F3 S4-07 强制 CSP 起 index.html 的 style 属性与内联 <style> 全部外移
    #（元素挂 csp-s-N 类，规则在 static/css/csp-html-styles.css）。等效判据：
    # 表格元素携带的 csp-s-N 类必须在外链样式中声明 table-layout: auto，
    # 且模板 + 外链样式联合文本中不得出现 table-layout: fixed。
    import re as _re
    tpl_path = ROOT / "demo" / "templates" / "index.html"
    content = tpl_path.read_text(encoding="utf-8")
    css = (ROOT / "demo" / "static" / "css" / "csp-html-styles.css").read_text(encoding="utf-8")
    css_flat = css.replace(" ", "")

    for tbl_id in ("archiveTable", "payablesTable"):
        _i = content.find('id="%s"' % tbl_id)
        assert _i != -1, "#%s 必须存在" % tbl_id
        _tag = content[content.rfind("<table", 0, _i):content.find(">", _i)]
        _m = _re.search(r'csp-s-(\d+)', _tag)
        assert _m, "#%s 必须携带 csp-s-N 布局类" % tbl_id
        assert ('.csp-s-%s{' % _m.group(1)) in css_flat, \
            "#%s 的 csp-s-%s 规则必须在外链样式中" % (tbl_id, _m.group(1))
        _rule = css_flat.split('.csp-s-%s{' % _m.group(1), 1)[1].split('}', 1)[0]
        assert 'table-layout:auto' in _rule, \
            "#%s 的外链规则必须显式声明 table-layout: auto" % tbl_id

    assert 'table-layout: fixed' not in (content + css).replace(" ", ""), \
        "index.html 与外链样式中所有主要展示表格严禁使用 table-layout: fixed"

    # tab-analytics 容器宽度已放宽至 1440px（规则随 CSP 外移至 csp-html-styles.css）
    assert "max-width:1440px" in css_flat, "tab-analytics 容器最大宽度必须放宽至 1440px 以容纳宽表"


def test_js_dynamic_tables_governance():
    # F4/F5 S4-11：观测台动态表格已随域拆分（事件流/分布表在 admin_observation.js、
    # 实验列表在 kpi_trend.js）；联合全文口径（main.js + 观测 + KPI 域文件）。
    content = "\n".join((ROOT / "demo" / "static" / "js" / f).read_text(encoding="utf-8")
                        for f in ("main.js", "admin_observation.js", "kpi_trend.js"))

    # 1. 挽回与点踩指标：最近事件流表格
    assert 'id="analyticsRecentEventsTable"' in content, "必须生成专属 ID 的 analyticsRecentEventsTable"
    assert 'min-width:1160px' in content, "analyticsRecentEventsTable 必须具备足够安全最小宽度"
    assert 'fmtTs = ev.ts ? String(ev.ts).replace(\'T\', \' \') : \'\'' in content or "replace('T', ' ')" in content, "时间戳必须去掉原生 T"
    # properties 取消了暴力 slice(0, 80)
    assert "propsShow = propsFull.length > 80" not in content, "最近事件流属性明细严禁被 80 字符硬性截断"

    # 2. 全量埋点分布表格
    assert 'id="analyticsEventDistTable"' in content, "必须生成专属 ID 的 analyticsEventDistTable"
    assert 'min-width:980px' in content, "analyticsEventDistTable 必须具备足够安全最小宽度"

    # 3. A/B 实验列表表格
    assert 'id="analyticsExperimentsTable"' in content, "必须生成专属 ID 的 analyticsExperimentsTable"
    assert 'min-width:980px' in content, "analyticsExperimentsTable 必须具备足够安全最小宽度"

    # 4. 指标小卡片 flex-wrap 弹性布局
    assert "flex:1 1 180px" in content, "_analyticsStatHtml 必须使用 flex 弹性均分排版"


def test_export_center_table_governance():
    css_path = ROOT / "demo" / "static" / "css" / "style.css"
    content_css = css_path.read_text(encoding="utf-8")

    # 1. 导出中心表格 CSS 布局与自适应断言
    assert "#exportPreviewTable" in content_css, "必须包含 #exportPreviewTable 专用样式"
    assert "table.data-table.export-preview-table" in content_css, "必须包含 export-preview-table 样式类"
    assert "export-col-desc" in content_css, "必须包含长文本列自适应折行样式 export-col-desc"
    assert "export-col-nowrap" in content_css, "必须包含短字段单行样式 export-col-nowrap"

    # 2. 导出中心 JS 渲染逻辑断言
    js_path = ROOT / "demo" / "static" / "js" / "export_center.js"
    content_js = js_path.read_text(encoding="utf-8")
    assert "export-col-desc" in content_js, "export_center.js 必须对品名/说明动态注入 export-col-desc 类"
    assert "export-col-nowrap" in content_js, "export_center.js 必须对数值/日期动态注入 export-col-nowrap 类"
    assert "h.includes('ID') || h.includes('编号')" in content_js, "export_center.js 必须支持 ID/编号列居中对齐"


def test_zero_emoji_in_governed_files():
    # 扫描修改的文件，确保 0 Emoji 违规
    emoji_regex = re.compile(
        r"[\U0001F300-\U0001F5FF]|[\U0001F600-\U0001F64F]|[\U0001F680-\U0001F6FF]|[\U0001F900-\U0001F9FF]|[\U0001FA70-\U0001FAFF]|[\u2600-\u26FF]|[\u2700-\u27BF]"
    )
    for rel_path in ["demo/static/css/style.css", "demo/templates/index.html", "demo/static/js/main.js", "demo/static/js/export_center.js"]:
        text = (ROOT / rel_path).read_text(encoding="utf-8")
        matches = emoji_regex.findall(text)
        assert len(matches) == 0, f"{rel_path} 中存在 Emoji 字符: {matches}"

