# -*- coding: utf-8 -*-
"""繁體中文（香港）界面端到端验收（真实浏览器 + 独立服务实例）。

覆盖：
  - ?lang=zh-Hant-HK 打开首页，逐个切换主 Tab 与 admin 区域，关键文案为繁体；
  - 用 opencc s2hk 复核 document.body.innerText：界面上没有残留的简体字；
  - <html lang>、刷新后语言保持、切换语言后保持当前 Tab、切回简体；
  - 识别复核表单的 input 值不被繁体转换改写，保存回传的仍是原始数据；
  - en-US 浏览器默认落到简体（保证既有 Playwright 用例不受影响）；
  - evalset.html 的语言切换器；
  - 1000 行表格的转换耗时；
  - CSV 导出的可选 lang 参数（缺省行为不变）；
  - 前端 JS 转换器与后端 app/i18n_text.py 结果一致。

服务经 `_playwright_server.start_pinned_server()` 在独立端口（默认 15023，可用 I18N_TEST_PORT
覆盖）以 live 库只读副本启动，用例写入的测试单据只落在副本里。
"""

import difflib
import json
import os
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

opencc = pytest.importorskip("opencc")
playwright_api = pytest.importorskip("playwright.sync_api")

from _playwright_server import _copy_live_db, start_pinned_server  # noqa: E402

PORT = int(os.environ.get("I18N_TEST_PORT", "15023"))
BASE = "http://127.0.0.1:%d" % PORT
HEADERS = {"X-Role": "admin", "Content-Type": "application/json"}

TABS = ["tab-scan", "tab-inventory", "tab-dishes", "tab-archive", "tab-report",
        "tab-export", "tab-engine", "tab-analytics"]
# 每个 Tab 激活后必须出现的繁体文案（侧栏 + 面包屑 + Tab 内标题）
EXPECTED = {
    "tab-scan": ["識別覆核", "上載/拖拽收據", "當天收據處理隊列"],
    "tab-inventory": ["實時庫存與價格"],
    "tab-dishes": ["餐品成本"],
    "tab-archive": ["歸檔對賬", "歷史收據與供應商歸檔記錄"],
    "tab-report": ["部門花銷報表"],
    "tab-export": ["數據中心"],
    "tab-engine": ["系統與引擎設定"],
    "tab-analytics": ["觀測中心"],
}
SIMPLIFIED_DATA = {"supplier": "嘉顿面包厂", "item": "鸡蛋面", "unit": "包"}


def _opencc_dict(name):
    path = os.path.join(os.path.dirname(opencc.__file__), "dictionary", name)
    out = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if "\t" in line:
                k, v = line.rstrip("\n").split("\t", 1)
                out[k] = v.split(" ")
    return out


_ST = _opencc_dict("STCharacters.txt")
_HK_OUTPUTS = {c for vals in _opencc_dict("HKVariants.txt").values() for c in vals}
# 纯简体字：在 STCharacters 中有映射、且候选里不包含自身（排除「干/里/后」这类简繁同形字），
# 再排除香港字形表的输出字（如「温」「为」是港式标准字形）。
PURE_SIMPLIFIED = {k for k, v in _ST.items() if len(k) == 1 and k not in v} - _HK_OUTPUTS
_S2HK = opencc.OpenCC("s2hk")


def unconverted_simplified(text):
    """用 s2hk 复核文本，返回仍是简体的位置（字, 上下文）。

    只把「纯简体字」处的差异计为失败：s2hk 对简繁同形字（干、里、准……）脱离原短语时会
    按简体处理，那类差异不代表界面上有简体残留。
    """
    conv = _S2HK.convert(text)
    if conv == text:
        return []
    bad = []
    sm = difflib.SequenceMatcher(a=text, b=conv, autojunk=False)
    for tag, i1, i2, _j1, _j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        for i in range(i1, i2):
            if text[i] in PURE_SIMPLIFIED:
                bad.append((text[i], text[max(0, i - 10): i + 10].replace("\n", " ")))
    return bad


def _api(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, dict(resp.headers), resp.read()


@pytest.fixture(scope="module")
def server():
    data_dir, db_path = _copy_live_db()
    handle = start_pinned_server(PORT, data_dir, db_path)
    try:
        yield handle
    finally:
        handle.stop()
        shutil.rmtree(data_dir, ignore_errors=True)


@pytest.fixture(scope="module")
def receipt_id(server):
    status, _h, body = _api("POST", "/api/save_edited", {
        "supplier_name": SIMPLIFIED_DATA["supplier"], "date": "2026-10-01", "total_amount": 20,
        "settlement_type": "cash",
        "items": [{"name": SIMPLIFIED_DATA["item"], "quantity": 2, "unit": SIMPLIFIED_DATA["unit"],
                   "unit_price": 10, "amount": 20}],
    })
    assert status == 200
    return json.loads(body)["receipt_id"]


@pytest.fixture(scope="module")
def browser(server):
    with playwright_api.sync_playwright() as p:
        b = p.chromium.launch()
        try:
            yield b
        finally:
            b.close()


@pytest.fixture()
def page(browser):
    ctx = browser.new_context(locale="en-US", viewport={"width": 1440, "height": 1000})
    pg = ctx.new_page()
    pg._i18n_dialogs = []
    pg.on("dialog", lambda d: (pg._i18n_dialogs.append(d.message), d.dismiss()))
    try:
        yield pg
    finally:
        ctx.close()


def _visible_text(page):
    """body.innerText 去掉 translate="no" 的元素（语言切换器里刻意保留的「简体中文」）。"""
    return page.evaluate("""() => {
        let text = document.body.innerText;
        document.querySelectorAll('[translate="no"]').forEach(el => {
            const t = el.innerText;
            if (t) text = text.split(t).join('');
        });
        return text;
    }""")


def _open(page, query=""):
    page.goto(BASE + "/" + query)
    page.wait_for_load_state("networkidle")


def _click_tab(page, tab):
    page.locator('.sidebar-btn[data-target="%s"]' % tab).click()
    page.wait_for_function("id => document.getElementById(id).classList.contains('active')", arg=tab)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)


def test_hant_all_tabs_fully_converted(page):
    _open(page, "?lang=zh-Hant-HK")
    assert page.evaluate("document.documentElement.lang") == "zh-Hant-HK"
    assert page.title() == "香港餐飲 AI 進貨收據識別與庫存管理系統"
    failures = {}
    for tab in TABS:
        _click_tab(page, tab)
        text = _visible_text(page)
        for phrase in EXPECTED[tab]:
            assert phrase in text, "%s 缺少繁体文案「%s」" % (tab, phrase)
        bad = unconverted_simplified(text)
        if bad:
            failures[tab] = bad[:10]
    assert not failures, "以下 Tab 仍有简体字残留：%r" % failures
    # 动态弹出的原生对话框文字同样被转换
    page.evaluate("() => alert('保存成功，数据已上传')")
    page.wait_for_timeout(200)
    assert page._i18n_dialogs and page._i18n_dialogs[-1] == "儲存成功，數據已上載"


def test_review_form_inputs_not_rewritten(page, receipt_id):
    _open(page, "?lang=zh-Hant-HK")
    page.evaluate("id => openReceiptInWorkbench(id)", receipt_id)
    page.wait_for_selector("#prefillFormCard:not(.hide)", timeout=15000)
    page.wait_for_function("() => document.querySelectorAll('#itemTableBody .inp-name').length > 0")
    vals = page.evaluate("""() => ({
        supplier: document.getElementById('inpSupplier').value,
        name: document.querySelector('#itemTableBody .inp-name').value,
        unit: document.querySelector('#itemTableBody .inp-unit').value,
        label: document.getElementById('prefillFormCard').innerText,
    })""")
    assert vals["supplier"] == SIMPLIFIED_DATA["supplier"]
    assert vals["name"] == SIMPLIFIED_DATA["item"]
    assert vals["unit"] == SIMPLIFIED_DATA["unit"]
    # 复核区重设计（2026-10-13 同步）：标题已由「单据内容复核与修改」改为
    # 「步骤 2/2：核对单据内容」（index.html:350 / upload.js:1622）。
    # 判别力不降：既断言页面出现**繁体转换后**标题（转换生效），也断言简体源文案
    # 不残留（转换不是「没跑」）。
    assert "供應商" in vals["label"] and "核對單據內容" in vals["label"], vals["label"][:200]
    assert "核对单据内容" not in vals["label"], "页面文案应以繁体呈现，简体源文案不得残留"
    # 供应商下拉里的原始数据（data-i18n-skip）也原样显示
    page.click("#inpSupplier")
    page.wait_for_timeout(300)
    menu = page.evaluate("document.getElementById('supplierDropdownMenu').innerText")
    assert SIMPLIFIED_DATA["supplier"] in menu
    assert "使用新供應商" in menu
    # 保存回传的仍是原始简体数据
    with page.expect_response(lambda r: "/api/save_edited" in r.url, timeout=15000) as info:
        page.click("#btnSaveReview")
    payload = json.loads(info.value.request.post_data)
    assert payload["supplier_name"] == SIMPLIFIED_DATA["supplier"]
    assert payload["items"][0]["name"] == SIMPLIFIED_DATA["item"]
    assert payload["items"][0]["unit"] == SIMPLIFIED_DATA["unit"]
    _s, _h, body = _api("GET", "/api/receipt/%d" % receipt_id)
    data = json.loads(body)["data"]
    assert data["supplier_name"] == SIMPLIFIED_DATA["supplier"]
    assert data["items"][0]["name"] == SIMPLIFIED_DATA["item"]


def test_switcher_persists_locale_and_tab_then_back_to_hans(page):
    _open(page)
    assert page.evaluate("document.documentElement.lang") == "zh-Hans"
    switch = page.locator('#langSwitch button[data-locale="zh-Hant-HK"]')
    assert switch.bounding_box()["height"] >= 38
    assert switch.get_attribute("aria-label")
    _click_tab(page, "tab-inventory")
    with page.expect_navigation():
        switch.click()
    page.wait_for_load_state("networkidle")
    page.wait_for_function("() => document.getElementById('tab-inventory').classList.contains('active')")
    assert page.evaluate("document.documentElement.lang") == "zh-Hant-HK"
    assert page.evaluate("localStorage.getItem('app.locale')") == "zh-Hant-HK"
    assert "實時庫存與價格" in page.inner_text("#bcActiveTitle")
    # 刷新后保持
    page.reload()
    page.wait_for_load_state("networkidle")
    assert page.evaluate("document.documentElement.lang") == "zh-Hant-HK"
    assert "實時庫存與價格" in _visible_text(page)
    # 键盘操作切回简体
    back = page.locator('#langSwitch button[data-locale="zh-Hans"]')
    assert back.get_attribute("aria-pressed") == "false"
    _click_tab(page, "tab-archive")
    back.focus()
    with page.expect_navigation():
        page.keyboard.press("Enter")
    page.wait_for_load_state("networkidle")
    page.wait_for_function("() => document.getElementById('tab-archive').classList.contains('active')")
    assert page.evaluate("document.documentElement.lang") == "zh-Hans"
    assert page.evaluate("localStorage.getItem('app.locale')") == "zh-Hans"
    text = page.evaluate("document.body.innerText")
    assert "实时库存与价格" in text and "归档对账" in text
    assert "實時庫存" not in text


def test_url_param_overrides_storage_and_is_rewritten_on_switch(page):
    _open(page, "?lang=zh-Hant-HK")
    with page.expect_navigation():
        page.locator('#langSwitch button[data-locale="zh-Hans"]').click()
    page.wait_for_load_state("networkidle")
    assert "lang=zh-Hans" in page.url
    assert page.evaluate("document.documentElement.lang") == "zh-Hans"


def test_en_us_browser_defaults_to_simplified(browser):
    ctx = browser.new_context(locale="en-US")
    pg = ctx.new_page()
    try:
        _open(pg)
        assert pg.evaluate("document.documentElement.lang") == "zh-Hans"
        assert pg.evaluate("I18N.getLocale()") == "zh-Hans"
        assert "识别复核" in pg.inner_text(".sidebar-nav")
    finally:
        ctx.close()


def test_zh_hk_browser_defaults_to_traditional(browser):
    ctx = browser.new_context(locale="zh-HK")
    pg = ctx.new_page()
    try:
        _open(pg)
        assert pg.evaluate("document.documentElement.lang") == "zh-Hant-HK"
        assert "識別覆核" in pg.inner_text(".sidebar-nav")
    finally:
        ctx.close()


def test_evalset_page_switcher(page):
    page.goto(BASE + "/evalset?lang=zh-Hant-HK")
    page.wait_for_load_state("networkidle")
    assert page.evaluate("document.documentElement.lang") == "zh-Hant-HK"
    assert page.title() == "GT 抽檢確權台"
    btns = page.locator("#langSwitch button")
    assert btns.count() == 2
    assert btns.nth(0).inner_text() == "简体中文" and btns.nth(1).inner_text() == "繁體中文"
    assert btns.nth(1).bounding_box()["height"] >= 38
    assert not unconverted_simplified(_visible_text(page))


def test_large_table_conversion_performance(page):
    _open(page, "?lang=zh-Hant-HK")
    timing = page.evaluate("""async () => {
        const html = [];
        for (let i = 0; i < 1000; i++) {
            html.push('<tr><td>第' + i + '行</td><td>嘉顿面包厂</td><td>鸡蛋面 ' + i + '</td><td>已入账</td>'
                + '<td title="点击查看供应商历史报价">单价 $' + (i * 1.5).toFixed(2) + '</td><td>默认部门</td></tr>');
        }
        const table = document.createElement('table');
        table.id = 'i18nPerfTable';
        const t0 = performance.now();
        table.innerHTML = '<tbody>' + html.join('') + '</tbody>';
        document.body.appendChild(table);
        await new Promise(r => setTimeout(r, 0));
        const observerMs = performance.now() - t0;
        const ok = table.rows[999].cells[3].textContent === '已入賬'
            && table.rows[0].cells[4].getAttribute('title') === '點擊查看供應商歷史報價';
        table.remove();
        const t2 = document.createElement('table');
        t2.innerHTML = '<tbody>' + html.join('') + '</tbody>';
        const t1 = performance.now();
        I18N._translateSubtree(t2);
        const directMs = performance.now() - t1;
        return { observerMs, directMs, ok };
    }""")
    print("\n[i18n perf] 1000-row table: MutationObserver path %.1f ms (incl. innerHTML parse), "
          "translate only %.1f ms" % (timing["observerMs"], timing["directMs"]))
    assert timing["ok"]
    assert timing["observerMs"] < 1500


def test_export_download_lang_param(server):
    _s, _h, body = _api("GET", "/api/export/reports")
    reports = json.loads(body)["data"]
    rid = (reports[0]["id"] if isinstance(reports, list) else None)
    assert rid
    base = {"report_id": rid, "params": {}}
    s1, h1, b1 = _api("POST", "/api/export/download", base)
    s2, h2, b2 = _api("POST", "/api/export/download", dict(base, lang="zh-Hant-HK"))
    s3, h3, b3 = _api("POST", "/api/export/download", dict(base, lang="zh-Hans"))
    assert s1 == s2 == s3 == 200
    head1 = b1.decode("utf-8-sig").splitlines()[0]
    head2 = b2.decode("utf-8-sig").splitlines()[0]
    head3 = b3.decode("utf-8-sig").splitlines()[0]
    assert head3 == head1, "lang=zh-Hans 必须与缺省行为一致"
    assert head2 != head1 and not unconverted_simplified(head2)
    fn1 = urllib.parse.unquote(h1["content-disposition"].split("''", 1)[1])
    fn2 = urllib.parse.unquote(h2["content-disposition"].split("''", 1)[1])
    assert fn1 != fn2 and not unconverted_simplified(fn2)
    # 行数据不转换
    assert b1.decode("utf-8-sig").splitlines()[1:] == b2.decode("utf-8-sig").splitlines()[1:]


def test_js_and_python_converters_agree():
    from app.i18n_text import to_hant_hk
    samples = ["默认设置", "上传文件失败，请检查网络", "人工干预", "财务审核批准", "面条 拉面 牛肉面",
               "识别复核表单", "供应商归档", "部门花销报表", "退出登录", "用户账号", "图像质量预检",
               "菜单工程", "下拉菜单", "嘉顿面包厂 鸡蛋面 3 包 HK$12.50"]
    src = os.path.join(_DEMO_DIR, "static", "js", "i18n")
    script = (
        "global.window=globalThis;globalThis.__I18N_NO_AUTOBOOT=true;"
        "for (const f of ['zh-hant-data.js','zh-hant-hk-glossary.js','i18n.js']) require(%r + '/' + f);"
        "const c=I18N._createConverter(I18N_HANT_DATA,I18N_HK_GLOSSARY);"
        "process.stdout.write(JSON.stringify(JSON.parse(process.argv[1]).map(c)));" % src
    )
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用")
    out = subprocess.run([node, "-e", script, json.dumps(samples, ensure_ascii=False)],
                         capture_output=True, text=True, check=True).stdout
    js = json.loads(out)
    py = [to_hant_hk(s) for s in samples]
    assert js == py


def test_generated_hant_data_is_up_to_date_and_deterministic():
    script = os.path.join(_DEMO_DIR, "scripts", "build_i18n_hant.py")
    res = subprocess.run([sys.executable, script, "--check"], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr or res.stdout
    stats = json.loads(res.stdout[res.stdout.index("{"):])
    assert stats["non_idempotent_corpus_runs"] == 0
    assert stats["bytes"] < 150 * 1024
