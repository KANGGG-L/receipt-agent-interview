// i18n 转换器与 DOM 翻译器单测（纯 node，无第三方依赖）。
// 用法（cwd 任意）：node demo/tests/frontend_i18n_converter_harness.js
// 全部断言通过时退出码 0，并打印 JSON 摘要；任一失败退出码 1。
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const I18N_DIR = path.join(__dirname, '..', 'static', 'js', 'i18n');

function loadI18n(extra) {
    const sandbox = Object.assign({ console, setTimeout, clearTimeout, Map, URLSearchParams, URL, Intl }, extra || {});
    sandbox.window = sandbox;
    sandbox.globalThis = sandbox;
    sandbox.__I18N_NO_AUTOBOOT = true;
    vm.createContext(sandbox);
    for (const f of ['zh-hant-data.js', 'zh-hant-hk-glossary.js', 'locales/zh-Hans.js', 'locales/zh-Hant-HK.js', 'i18n.js']) {
        vm.runInContext(fs.readFileSync(path.join(I18N_DIR, f), 'utf8'), sandbox, { filename: f });
    }
    return sandbox;
}

const results = [];
let failed = 0;
function check(name, cond, detail) {
    results.push({ name, ok: !!cond, detail: cond ? undefined : detail });
    if (!cond) failed++;
}
function eq(name, actual, expected) {
    check(name, actual === expected, { actual, expected });
}

const sb = loadI18n();
const I18N = sb.I18N;
const realConvert = I18N._createConverter(sb.I18N_HANT_DATA, sb.I18N_HK_GLOSSARY);

// ---- 1. 算法：短语优先于单字（合成数据，精确验证最长匹配） ----
{
    const c = I18N._createConverter({ chars: '干幹发發头頭', phrases: { '干货': '乾貨', '头发': '頭髮' }, hkChars: '', hkPhrases: {} }, []);
    eq('phrase beats char: 干货', c('干货'), '乾貨');
    eq('char fallback: 干', c('干'), '幹');
    eq('phrase beats char: 头发', c('头发'), '頭髮');
    eq('longest match left-to-right: 干货头发', c('干货头发'), '乾貨頭髮');
}
// 真实数据：找一个短语结果不同于逐字结果的词条，验证短语生效
{
    const phrases = sb.I18N_HANT_DATA.phrases;
    const charOnly = I18N._createConverter({ chars: sb.I18N_HANT_DATA.chars, phrases: {}, hkChars: sb.I18N_HANT_DATA.hkChars, hkPhrases: {} }, []);
    let sample = null;
    for (const k of Object.keys(phrases)) {
        if (charOnly(k) !== realConvert(k) && !sb.I18N_HK_GLOSSARY.some(g => k.includes(g.s))) { sample = k; break; }
    }
    check('real data has a phrase that differs from char-by-char', !!sample, 'none found');
    if (sample) check('real phrase applied: ' + sample + ' → ' + realConvert(sample), realConvert(sample) !== charOnly(sample), charOnly(sample));
    eq('干货 (real data)', realConvert('干货'), '乾貨');
}

// ---- 2. 港式用语表优先于 OpenCC ----
{
    const c = I18N._createConverter({ chars: '认認设設置置', phrases: { '默认': '默認' }, hkChars: '', hkPhrases: {} }, [{ s: '默认', t: '預設' }]);
    eq('glossary beats OpenCC phrase', c('默认'), '預設');
    eq('glossary output not re-processed by HK pass', I18N._createConverter({ chars: '', phrases: {}, hkChars: '戶户', hkPhrases: {} }, [{ s: '用户', t: '用戶' }])('用户'), '用戶');
    const cases = {
        '默认设置': '預設設定', '默认值': '預設值', '退出登录': '登出', '登录': '登入', '上传文件': '上載檔案',
        '用户': '用戶', '账号': '帳戶', '视频': '影片', '打印': '列印', '网络': '網絡', '搜索': '搜尋',
        '屏幕': '螢幕', '数据库': '數據庫', '下拉菜单': '下拉選單', '支持多选': '支援多選', '图像质量': '圖像質素',
        '面条': '麵條', '面包': '麵包', '识别复核': '識別覆核', '信息': '資訊',
    };
    for (const [s, t] of Object.entries(cases)) eq('glossary: ' + s, realConvert(s), t);
    // 业务名词不被误改
    eq('ingredient kept: 菜心', realConvert('菜心'), '菜心');
    eq('menu board kept: 餐牌', realConvert('餐牌'), '餐牌');
    eq('menu engineering keeps 菜單', realConvert('菜单工程'), '菜單工程');
    check('no 麪 in noodle outputs', !/麪/.test(realConvert('面条 拉面 方便面 面粉 牛肉面 炒面')), realConvert('面条 拉面 方便面 面粉 牛肉面 炒面'));
    eq('戶 kept (variant override)', realConvert('客户'), '客戶');
    eq('HK variant: 为→為', realConvert('为'), '為');
}

// ---- 3. 幂等：对已转换（繁体）文本再转一次不变 ----
{
    const htmlSrc = fs.readFileSync(path.join(__dirname, '..', 'templates', 'index.html'), 'utf8');
    const jsSrc = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'main.js'), 'utf8');
    const runs = (htmlSrc + jsSrc).match(/[\u3400-\u9fff][\u3400-\u9fff\u3000-\u303f\uff00-\uffef0-9A-Za-z ·、]*/g) || [];
    const uniq = Array.from(new Set(runs));
    const bad = [];
    // 第二遍用全新的转换器：不借助「产出过的文本原样返回」的缓存，直接验证数据本身是不动点。
    const second = I18N._createConverter(sb.I18N_HANT_DATA, sb.I18N_HK_GLOSSARY);
    for (const s of uniq) {
        const once = realConvert(s);
        const twice = second(once);
        if (once !== twice) bad.push([s, once, twice]);
    }
    check('idempotent over ' + uniq.length + ' corpus fragments', bad.length === 0, bad.slice(0, 10));
    eq('already-traditional text unchanged', realConvert('繁體中文已轉換，請檢查單據上的數字'), '繁體中文已轉換，請檢查單據上的數字');
    eq('non-CJK passthrough', realConvert('HK$ 1,234.50 / 2026-10-11'), 'HK$ 1,234.50 / 2026-10-11');
}

// ---- 4. DOM 翻译器：跳过 input/textarea 值、contenteditable、skip 子树 ----
function mkText(data) { return { nodeType: 3, nodeName: '#text', data, parentNode: null }; }
function mkEl(tag, attrs, children) {
    const el = {
        nodeType: 1, nodeName: tag.toUpperCase(), parentNode: null, _attrs: Object.assign({}, attrs || {}), childNodes: [],
        getAttribute(n) { return Object.prototype.hasOwnProperty.call(this._attrs, n) ? this._attrs[n] : null; },
        setAttribute(n, v) { this._attrs[n] = String(v); },
        get textContent() { return this.childNodes.map(c => c.nodeType === 3 ? c.data : c.textContent).join(''); },
    };
    if (el.nodeName === 'INPUT' || el.nodeName === 'TEXTAREA') el.value = (attrs && attrs.value) || '';
    (children || []).forEach(c => { c.parentNode = el; el.childNodes.push(c); });
    return el;
}
{
    I18N._setLocaleForTest('zh-Hant-HK');
    const inputText = mkEl('input', { type: 'text', value: '鸡蛋面', placeholder: '品名(如走地鸡)', title: '数量' });
    inputText.value = '嘉顿面包厂';
    const inputBtn = mkEl('input', { type: 'button', value: '保存' });
    const textarea = mkEl('textarea', { placeholder: '备注' }, [mkText('原始备注内容')]);
    const skipSpan = mkEl('span', { 'data-i18n-skip': '' }, [mkText('嘉顿面包厂')]);
    const noTrans = mkEl('button', { translate: 'no' }, [mkText('简体中文')]);
    const editable = mkEl('div', { contenteditable: 'true' }, [mkText('用户手写的简体')]);
    const optNoValue = mkEl('option', {}, [mkText('肉类')]);
    const optWithValue = mkEl('option', { value: '海鲜' }, [mkText('海鲜')]);
    const script = mkEl('script', {}, [mkText('var a = "简体";')]);
    const label = mkEl('label', { 'aria-label': '整单总金额' }, [mkText('供应商')]);
    const root = mkEl('div', {}, [label, inputText, inputBtn, textarea, skipSpan, noTrans, editable, optNoValue, optWithValue, script]);
    I18N._translateSubtree(root);
    eq('label text converted', label.childNodes[0].data, '供應商');
    eq('aria-label converted', label.getAttribute('aria-label'), '整單總金額');
    eq('input value attribute untouched', inputText.getAttribute('value'), '鸡蛋面');
    eq('input value property untouched', inputText.value, '嘉顿面包厂');
    eq('input placeholder converted', inputText.getAttribute('placeholder'), '品名(如走地雞)');
    eq('input title converted', inputText.getAttribute('title'), '數量');
    eq('button-type input value converted', inputBtn.getAttribute('value'), '儲存');
    eq('textarea content untouched', textarea.childNodes[0].data, '原始备注内容');
    eq('textarea placeholder converted', textarea.getAttribute('placeholder'), '備註');
    eq('data-i18n-skip subtree untouched', skipSpan.childNodes[0].data, '嘉顿面包厂');
    eq('translate=no untouched', noTrans.childNodes[0].data, '简体中文');
    eq('contenteditable untouched', editable.childNodes[0].data, '用户手写的简体');
    eq('option without value: value pinned to source', optNoValue.getAttribute('value'), '肉类');
    eq('option without value: label converted', optNoValue.childNodes[0].data, '肉類');
    eq('option with value: value untouched', optWithValue.getAttribute('value'), '海鲜');
    eq('script untouched', script.childNodes[0].data, 'var a = "简体";');

    // MutationObserver 记录：characterData / attributes / childList
    const t1 = mkText('保存成功'); const p1 = mkEl('p', {}, [t1]);
    const t2 = mkText('嘉顿'); const skip2 = mkEl('span', { 'data-i18n-skip': '' }, [t2]);
    const wrap = mkEl('div', {}, [p1, skip2, inputText]);
    I18N._handleMutations([
        { type: 'characterData', target: t1 },
        { type: 'characterData', target: t2 },
        { type: 'attributes', target: inputText, attributeName: 'value' },
    ]);
    eq('mutation characterData converted', t1.data, '儲存成功');
    eq('mutation characterData inside skip untouched', t2.data, '嘉顿');
    eq('mutation value attribute on text input untouched', inputText.getAttribute('value'), '鸡蛋面');
    const added = mkEl('td', {}, [mkText('已入账')]);
    const tr = mkEl('tr', {}, []); added.parentNode = tr; tr.childNodes.push(added); tr.parentNode = wrap;
    I18N._handleMutations([{ type: 'childList', target: tr, addedNodes: [added] }]);
    eq('mutation childList converted', added.childNodes[0].data, '已入賬');
    const before = I18N._stats.textWrites;
    I18N._translateSubtree(wrap);
    eq('second pass writes nothing (no self-trigger loop)', I18N._stats.textWrites, before);
    I18N._setLocaleForTest('zh-Hans');
    const hansNode = mkText('保存');
    I18N._translateSubtree(mkEl('div', {}, [hansNode]));
    // _translateSubtree 只在繁体模式下由 boot 调用；简体模式下 convert() 恒等
    eq('I18N.convert is identity in zh-Hans', I18N.convert('保存'), '保存');
}

// ---- 5. 语言解析优先级 ----
{
    const mkLs = v => ({ getItem: () => v, setItem() {} });
    eq('url beats storage', I18N._resolveLocale({ search: '?lang=zh-Hant-HK', localStorage: mkLs('zh-Hans'), navigator: { languages: ['zh-CN'] } }).locale, 'zh-Hant-HK');
    eq('storage beats navigator', I18N._resolveLocale({ search: '', localStorage: mkLs('zh-Hant-HK'), navigator: { languages: ['zh-CN'] } }).locale, 'zh-Hant-HK');
    eq('invalid url value ignored', I18N._resolveLocale({ search: '?lang=fr', localStorage: mkLs(null), navigator: { languages: ['en-US'] } }).locale, 'zh-Hans');
    const nav = l => I18N._localeFromNavigator({ languages: l });
    eq('navigator en-US → zh-Hans', nav(['en-US']), 'zh-Hans');
    eq('navigator zh-HK → zh-Hant-HK', nav(['zh-HK', 'en']), 'zh-Hant-HK');
    eq('navigator zh-MO → zh-Hant-HK', nav(['zh-MO']), 'zh-Hant-HK');
    eq('navigator zh-TW → zh-Hant-HK', nav(['zh-TW']), 'zh-Hant-HK');
    eq('navigator zh-Hant → zh-Hant-HK', nav(['zh-Hant']), 'zh-Hant-HK');
    eq('navigator zh-CN → zh-Hans', nav(['zh-CN', 'zh-HK']), 'zh-Hans');
    eq('navigator en then zh-HK → zh-Hant-HK', nav(['en-US', 'zh-HK']), 'zh-Hant-HK');
}

// ---- 6. 键值层 ----
{
    I18N._setLocaleForTest('zh-Hans');
    eq('t() zh-Hans', I18N.t('nav.inventory'), '实时库存与价格');
    eq('t() params', I18N.t('role.switched', { name: '餐厅店员' }), '角色已切换为：餐厅店员');
    eq('t() missing key returns fallback', I18N.t('no.such.key', null, '默认文案'), '默认文案');
    I18N._setLocaleForTest('zh-Hant-HK');
    eq('t() zh-Hant-HK', I18N.t('nav.inventory'), '實時庫存與價格');
    eq('t() missing hant key falls back to converted source', I18N.t('no.such.key', null, '默认文案'), '預設文案');
    const hans = sb.I18N_LOCALES['zh-Hans'];
    const hant = sb.I18N_LOCALES['zh-Hant-HK'];
    const missing = Object.keys(hans).filter(k => !(k in hant));
    check('zh-Hant-HK catalog covers every zh-Hans key', missing.length === 0, missing);
    const leftovers = Object.entries(hant).filter(([k, v]) => realConvert(v) !== v);
    check('zh-Hant-HK catalog values contain no unconverted simplified text', leftovers.length === 0, leftovers);
    eq('fmtMoney HKD', I18N.fmtMoney(1234.5), 'HK$1,234.50');
    I18N._setLocaleForTest('zh-Hans');
    eq('fmtDate zh-Hans uses y/m/d', I18N.fmtDate('2026-08-01'), '2026/08/01');
    eq('fmtMoney zh-Hans HKD', I18N.fmtMoney(1234.5), 'HK$1,234.50');
    I18N._setLocaleForTest('zh-Hant-HK');
    eq('fmtNumber grouping', I18N.fmtNumber(1234567.891), '1,234,567.89');
    eq('fmtDate zh-Hant-HK uses HK day/month order', I18N.fmtDate('2026-08-01'), '01/08/2026');
    I18N._setLocaleForTest('zh-Hans');
}

// ---- 7. 体积与性能 ----
{
    const size = fs.statSync(path.join(I18N_DIR, 'zh-hant-data.js')).size;
    check('zh-hant-data.js < 150KB (' + size + ' bytes)', size < 150 * 1024, size);
    const fresh = I18N._createConverter(sb.I18N_HANT_DATA, sb.I18N_HK_GLOSSARY);
    const rows = [];
    for (let i = 0; i < 1000; i++) rows.push('第' + i + '行 嘉顿面包厂 鸡蛋面 已入账 待核对 单价 $' + (i * 1.5).toFixed(2) + ' 供应商归档 默认设置 ' + i);
    const t0 = process.hrtime.bigint();
    for (const r of rows) fresh(r);
    const coldMs = Number(process.hrtime.bigint() - t0) / 1e6;
    const t1 = process.hrtime.bigint();
    for (const r of rows) fresh(r);
    const warmMs = Number(process.hrtime.bigint() - t1) / 1e6;
    results.push({ name: 'perf: 1000 distinct row strings', ok: true, coldMs: +coldMs.toFixed(2), cachedMs: +warmMs.toFixed(2) });
    check('perf: 1000 rows converted < 200ms', coldMs < 200, coldMs);
}

const summary = { passed: results.filter(r => r.ok).length, failed, total: results.length };
console.log(JSON.stringify({ summary, failures: results.filter(r => !r.ok), perf: results.filter(r => r.name.startsWith('perf:') && r.coldMs !== undefined) }, null, 1));
process.exit(failed ? 1 : 0);
