/*
 * 前端国际化核心（无构建步骤，纯静态脚本，必须在 main.js 之前同步加载）。
 *
 * 两层：
 *   1. 键值层：I18N.t(key, params)，语言目录在 i18n/locales/*.js，通过 I18N.registerLocale 注册。
 *      给外壳（导航、语言切换器、通用按钮、状态名、角色名）和新代码使用。
 *   2. 文字转换层：语言为 zh-Hant-HK 时，把页面上所有简体文本按「港式用语表 > OpenCC 短语 > OpenCC 单字
 *      > 香港字形」转换为繁体。覆盖 main.js 等 innerHTML 拼接出的动态内容与后端返回的提示。
 *      永不改写 input/textarea 的值，也跳过 contenteditable、script/style 与
 *      [translate="no"] / [data-i18n-skip] 子树。
 *
 * 新增语言：在 locales/ 下增加目录文件并在 SUPPORTED 中登记；非中文语言不启用文字转换层。
 */
(function (root) {
    'use strict';

    var SOURCE_LOCALE = 'zh-Hans';
    var HANT = 'zh-Hant-HK';
    var SUPPORTED = [SOURCE_LOCALE, HANT];
    var STORAGE_KEY = 'app.locale';
    var RESTORE_KEY = 'app.locale.restoreTab';
    var HTML_LANG = { 'zh-Hans': 'zh-Hans', 'zh-Hant-HK': 'zh-Hant-HK' };
    var NATIVE_NAMES = { 'zh-Hans': '简体中文', 'zh-Hant-HK': '繁體中文' };
    var INTL_LOCALE = { 'zh-Hans': 'zh-Hans', 'zh-Hant-HK': 'zh-Hant-HK' };

    // 语言目录文件先于本文件加载，统一挂在 I18N_LOCALES 上，这里接管。
    var catalogs = {};
    var preloaded = root.I18N_LOCALES || {};
    Object.keys(preloaded).forEach(function (k) { catalogs[k] = Object.assign({}, preloaded[k]); });
    var listeners = [];
    var currentLocale = SOURCE_LOCALE;

    // ------------------------------------------------------------------
    // 语言解析：URL ?lang= > localStorage > navigator.languages > zh-Hans
    // ------------------------------------------------------------------
    function normalizeLocale(raw) {
        if (!raw) return null;
        var s = String(raw).trim().toLowerCase().replace(/_/g, '-');
        if (!s) return null;
        if (s === 'zh-hant-hk' || s === 'zh-hk' || s === 'zh-mo' || s === 'zh-tw' || s === 'hk'
            || s.indexOf('zh-hant') === 0) return HANT;
        if (s === 'zh-hans' || s === 'zh-cn' || s === 'zh-sg' || s === 'zh' || s === 'cn'
            || s.indexOf('zh-hans') === 0) return SOURCE_LOCALE;
        return null;
    }

    function localeFromNavigator(nav) {
        var langs = [];
        if (nav) {
            if (nav.languages && nav.languages.length) langs = Array.prototype.slice.call(nav.languages);
            else if (nav.language) langs = [nav.language];
        }
        for (var i = 0; i < langs.length; i++) {
            var l = String(langs[i] || '').toLowerCase();
            if (/^zh-(hk|mo|tw)\b/.test(l) || l.indexOf('zh-hant') === 0) return HANT;
            if (l.indexOf('zh') === 0) return SOURCE_LOCALE;
        }
        return SOURCE_LOCALE;
    }

    function safeStorage(kind) {
        try { return root[kind] || null; } catch (e) { return null; }
    }

    function resolveLocale(env) {
        env = env || {};
        var search = env.search !== undefined ? env.search
            : (root.location ? root.location.search : '');
        var fromUrl = null;
        try {
            fromUrl = normalizeLocale(new URLSearchParams(search || '').get('lang'));
        } catch (e) { fromUrl = null; }
        if (fromUrl) return { locale: fromUrl, source: 'url' };
        var stored = null;
        var ls = env.localStorage !== undefined ? env.localStorage : safeStorage('localStorage');
        try { stored = ls ? normalizeLocale(ls.getItem(STORAGE_KEY)) : null; } catch (e) { stored = null; }
        if (stored) return { locale: stored, source: 'storage' };
        var nav = env.navigator !== undefined ? env.navigator : root.navigator;
        if (nav) return { locale: localeFromNavigator(nav), source: 'navigator' };
        return { locale: SOURCE_LOCALE, source: 'default' };
    }

    // ------------------------------------------------------------------
    // 文字转换器（最长匹配，与 OpenCC 的正向最大匹配一致）
    // ------------------------------------------------------------------
    var CJK_RE = /[\u3400-\u9fff\uf900-\ufaff]/;

    function buildPairMap(pairs) {
        var m = new Map();
        for (var i = 0; i + 1 < pairs.length; i += 2) m.set(pairs.charAt(i), pairs.charAt(i + 1));
        return m;
    }

    function buildPhraseIndex(obj) {
        var map = new Map();
        var maxLen = new Map();
        Object.keys(obj || {}).forEach(function (k) {
            if (!k) return;
            map.set(k, obj[k]);
            var f = k.charAt(0);
            if ((maxLen.get(f) || 0) < k.length) maxLen.set(f, k.length);
        });
        return { map: map, maxLen: maxLen };
    }

    function createConverter(data, glossary) {
        data = data || {};
        var chars = buildPairMap(data.chars || '');
        var phrases = buildPhraseIndex(data.phrases);
        var hkChars = buildPairMap(data.hkChars || '');
        var hkPhrases = buildPhraseIndex(data.hkPhrases);
        var glossObj = {};
        (glossary || []).forEach(function (e) { if (e && e.s && e.t) glossObj[e.s] = e.t; });
        var gloss = buildPhraseIndex(glossObj);

        function longest(index, text, i, minLen) {
            var max = index.maxLen.get(text.charAt(i));
            if (!max) return null;
            var lim = Math.min(max, text.length - i);
            for (var L = lim; L >= minLen; L--) {
                var sub = text.substr(i, L);
                var hit = index.map.get(sub);
                if (hit !== undefined) return { len: L, out: hit };
            }
            return null;
        }

        function hkPass(s) {
            if (!s) return s;
            var out = '';
            var i = 0;
            while (i < s.length) {
                var m = longest(hkPhrases, s, i, 1);
                if (m) { out += m.out; i += m.len; continue; }
                var c = s.charAt(i);
                var v = hkChars.get(c);
                out += (v === undefined ? c : v);
                i++;
            }
            return out;
        }

        function openccPass(s) {
            var out = '';
            var i = 0;
            while (i < s.length) {
                var p = longest(phrases, s, i, 2);
                if (p) { out += p.out; i += p.len; continue; }
                var c = s.charAt(i);
                var v = chars.get(c);
                out += (v === undefined ? c : v);
                i++;
            }
            return hkPass(out);
        }

        // 与 demo/app/i18n_text.py 的 Converter 同一算法：先按用语表切分，用语表词条之间的
        // 片段再走 OpenCC，保证用语表优先且 OpenCC 短语不会吞掉用语表词条的首字。
        function convertRaw(text) {
            var out = '';
            var gap = '';
            var i = 0;
            var n = text.length;
            while (i < n) {
                var g = longest(gloss, text, i, 1);
                if (g) {
                    if (gap) { out += openccPass(gap); gap = ''; }
                    out += g.out;
                    i += g.len;
                    continue;
                }
                gap += text.charAt(i);
                i++;
            }
            if (gap) out += openccPass(gap);
            return out;
        }

        var cache = new Map();
        var produced = new Set();
        var CACHE_MAX = 20000;
        function convert(text) {
            if (text === null || text === undefined) return text;
            var s = String(text);
            if (!CJK_RE.test(s)) return s;
            var hit = cache.get(s);
            if (hit !== undefined) return hit;
            // 本转换器产出过的文本原样返回：脚本把已转换的文字读回再写入时不会被二次改写。
            if (produced.has(s)) return s;
            var r = convertRaw(s);
            if (cache.size >= CACHE_MAX) { cache.clear(); produced.clear(); }
            cache.set(s, r);
            produced.add(r);
            return r;
        }
        convert.cacheSize = function () { return cache.size; };
        return convert;
    }

    var hantConvert = null;
    function getHantConverter() {
        if (!hantConvert) {
            hantConvert = createConverter(root.I18N_HANT_DATA, root.I18N_HK_GLOSSARY);
        }
        return hantConvert;
    }

    function convertText(text) {
        if (currentLocale !== HANT) return text;
        return getHantConverter()(text);
    }

    // ------------------------------------------------------------------
    // 键值层
    // ------------------------------------------------------------------
    function registerLocale(locale, messages) {
        catalogs[locale] = Object.assign(catalogs[locale] || {}, messages || {});
    }

    function interpolate(str, params) {
        if (!params) return str;
        return String(str).replace(/\{(\w+)\}/g, function (m, k) {
            return Object.prototype.hasOwnProperty.call(params, k) && params[k] !== null && params[k] !== undefined
                ? String(params[k]) : m;
        });
    }

    function t(key, params, fallback) {
        var cat = catalogs[currentLocale];
        var msg = cat ? cat[key] : undefined;
        if (msg === undefined) {
            var src = catalogs[SOURCE_LOCALE] ? catalogs[SOURCE_LOCALE][key] : undefined;
            if (src === undefined) src = (fallback !== undefined ? fallback : key);
            msg = (currentLocale === HANT) ? getHantConverter()(src) : src;
        }
        return interpolate(msg, params);
    }

    // ------------------------------------------------------------------
    // 格式化（Intl，货币默认 HKD）
    // ------------------------------------------------------------------
    var nfCache = {};
    function numberFormat(opts) {
        var k = currentLocale + JSON.stringify(opts || {});
        if (!nfCache[k]) {
            try { nfCache[k] = new Intl.NumberFormat(INTL_LOCALE[currentLocale] || 'zh-HK', opts); }
            catch (e) { nfCache[k] = new Intl.NumberFormat('en-HK', opts); }
        }
        return nfCache[k];
    }

    function fmtNumber(v, opts) {
        var n = Number(v);
        if (v === null || v === undefined || v === '' || !isFinite(n)) return '—';
        return numberFormat(opts || { maximumFractionDigits: 2 }).format(n);
    }

    function fmtMoney(v, currency, opts) {
        var n = Number(v);
        if (v === null || v === undefined || v === '' || !isFinite(n)) return '—';
        var o = Object.assign({ style: 'currency', currency: currency || 'HKD',
            currencyDisplay: 'symbol', minimumFractionDigits: 2, maximumFractionDigits: 2 }, opts || {});
        return numberFormat(o).format(n);
    }

    function fmtDate(v, opts) {
        if (v === null || v === undefined || v === '') return '—';
        var d = (v instanceof Date) ? v : null;
        if (!d) {
            var s = String(v);
            // 纯日期按本地日期解析，避免 new Date('2026-08-01') 被当作 UTC 而跨日。
            var m = s.match(/^(\d{4})-(\d{2})-(\d{2})$/);
            d = m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : new Date(s);
        }
        if (isNaN(d.getTime())) return String(v);
        var o = opts || { year: 'numeric', month: '2-digit', day: '2-digit' };
        try { return new Intl.DateTimeFormat(INTL_LOCALE[currentLocale] || 'zh-HK', o).format(d); }
        catch (e) { return d.toISOString().slice(0, 10); }
    }

    // ------------------------------------------------------------------
    // DOM 翻译器（仅 zh-Hant-HK 启用）
    // ------------------------------------------------------------------
    var TEXT_ATTRS = ['placeholder', 'title', 'aria-label', 'alt', 'aria-placeholder',
        'aria-roledescription', 'aria-valuetext', 'data-tooltip', 'label'];
    var BUTTON_INPUT_TYPES = { button: 1, submit: 1, reset: 1 };
    var SKIP_TAGS = { SCRIPT: 1, STYLE: 1, NOSCRIPT: 1, TEMPLATE: 1 };

    // 整棵子树（含自身属性）都不处理的元素。
    function isSkipElement(el) {
        if (!el || el.nodeType !== 1) return false;
        if (SKIP_TAGS[el.nodeName] === 1) return true;
        if (!el.getAttribute) return false;
        if (el.getAttribute('data-i18n-skip') !== null) return true;
        if (el.getAttribute('translate') === 'no') return true;
        var ce = el.getAttribute('contenteditable');
        if (ce !== null && ce !== 'false') return true;
        return false;
    }

    // textarea 的子文本就是它的值，只处理属性（placeholder/title），不处理内容。
    function isValueContainer(el) {
        return !!el && el.nodeType === 1 && el.nodeName === 'TEXTAREA';
    }

    var SKIP_SELECTOR = 'script,style,noscript,template,[data-i18n-skip],[translate="no"],'
        + '[contenteditable]:not([contenteditable="false"])';

    function inSkippedSubtree(node) {
        var n = node && node.nodeType === 1 ? node : (node ? node.parentNode : null);
        if (!n || n.nodeType !== 1) return false;
        // 浏览器里用原生 closest（解析期每秒上万条记录，逐级 getAttribute 太慢）；无 closest 的环境逐级判断。
        if (typeof n.closest === 'function') return n.closest(SKIP_SELECTOR) !== null;
        while (n && n.nodeType === 1) {
            if (isSkipElement(n)) return true;
            n = n.parentNode;
        }
        return false;
    }

    function textParentSkipped(parent) {
        return isValueContainer(parent) || inSkippedSubtree(parent);
    }

    var stats = { textWrites: 0, attrWrites: 0 };

    function translateTextNode(node) {
        var v = node.data;
        if (!v || !CJK_RE.test(v)) return;
        var r = getHantConverter()(v);
        if (r !== v) { node.data = r; stats.textWrites++; }
    }

    function translateAttr(el, name) {
        if (!el.getAttribute) return;
        var v = el.getAttribute(name);
        if (!v || !CJK_RE.test(v)) return;
        var r = getHantConverter()(v);
        if (r !== v) { el.setAttribute(name, r); stats.attrWrites++; }
    }

    function translateElementAttrs(el) {
        for (var i = 0; i < TEXT_ATTRS.length; i++) translateAttr(el, TEXT_ATTRS[i]);
        var tag = el.nodeName;
        if (tag === 'INPUT') {
            var type = String(el.getAttribute('type') || '').toLowerCase();
            if (BUTTON_INPUT_TYPES[type]) {
                // 按钮的 value 只是显示文字；其余 input 的 value 一律不碰。
                translateAttr(el, 'value');
                if (el.value && CJK_RE.test(el.value)) {
                    var r = getHantConverter()(el.value);
                    if (r !== el.value) el.value = r;
                }
            }
        } else if (tag === 'OPTION') {
            // 没有 value 属性的 option 以文字作为提交值；先把原文固化为 value，再改显示文字。
            if (el.getAttribute('value') === null) {
                var txt = el.textContent;
                if (txt && CJK_RE.test(txt)) el.setAttribute('value', txt);
            }
        }
    }

    function translateSubtree(rootNode) {
        if (!rootNode) return;
        if (rootNode.nodeType === 3) {
            if (!textParentSkipped(rootNode.parentNode)) translateTextNode(rootNode);
            return;
        }
        if (rootNode.nodeType !== 1 && rootNode.nodeType !== 11 && rootNode.nodeType !== 9) return;
        var stack = [rootNode];
        while (stack.length) {
            var n = stack.pop();
            if (n.nodeType === 3) { translateTextNode(n); continue; }
            if (n.nodeType === 1) {
                if (isSkipElement(n)) continue;
                translateElementAttrs(n);
                if (isValueContainer(n)) continue;
            } else if (n.nodeType !== 11 && n.nodeType !== 9) {
                continue;
            }
            var kids = n.childNodes;
            if (kids) for (var i = kids.length - 1; i >= 0; i--) stack.push(kids[i]);
        }
    }

    function translateDocumentTitle(doc) {
        doc = doc || root.document;
        if (!doc) return;
        var t0 = doc.title;
        if (t0 && CJK_RE.test(t0)) {
            var r = getHantConverter()(t0);
            if (r !== t0) doc.title = r;
        }
    }

    function handleMutations(records) {
        for (var i = 0; i < records.length; i++) {
            var rec = records[i];
            if (rec.type === 'childList') {
                var target = rec.target;
                if (target && target.nodeName === 'TITLE') { translateDocumentTitle(); continue; }
                if (!rec.addedNodes || !rec.addedNodes.length) continue;
                if (textParentSkipped(target)) continue;
                for (var j = 0; j < rec.addedNodes.length; j++) translateSubtree(rec.addedNodes[j]);
            } else if (rec.type === 'characterData') {
                var tn = rec.target;
                if (tn.parentNode && tn.parentNode.nodeName === 'TITLE') { translateDocumentTitle(); continue; }
                if (!textParentSkipped(tn.parentNode)) translateTextNode(tn);
            } else if (rec.type === 'attributes') {
                var el = rec.target;
                if (inSkippedSubtree(el)) continue;
                var name = rec.attributeName;
                if (name === 'value') {
                    if (el.nodeName === 'INPUT' && BUTTON_INPUT_TYPES[String(el.getAttribute('type') || '').toLowerCase()]) {
                        translateAttr(el, 'value');
                    }
                } else {
                    translateAttr(el, name);
                }
            }
        }
    }

    var observer = null;
    // 解析期（DOMContentLoaded 之前）丢弃记录：页面此时隐藏，DOMContentLoaded 时整页转换一次即可；
    // 逐条处理解析器产生的近万条插入记录会让首屏慢一倍。兜底显示或整页转换之后切到实时模式。
    var observerLive = false;
    function startObserver(doc) {
        doc = doc || root.document;
        if (observer || !doc || typeof root.MutationObserver !== 'function') return;
        observer = new root.MutationObserver(function (records) {
            if (!observerLive) return;
            handleMutations(records);
            // 本回调内写回产生的记录全部来自自身（回调同步执行期间无其他脚本插入），
            // 直接丢弃，避免「写回→触发→再转换」的空转。
            observer.takeRecords();
        });
        observer.observe(doc.documentElement, {
            childList: true, subtree: true, characterData: true,
            attributes: true, attributeFilter: TEXT_ATTRS.concat(['value'])
        });
    }

    function flushObserver() {
        if (!observer) return;
        var pending = observer.takeRecords();
        if (pending && pending.length) {
            handleMutations(pending);
            observer.takeRecords();
        }
    }

    function translateDocument(doc) {
        if (currentLocale !== HANT) return;
        doc = doc || root.document;
        if (!doc || !doc.body) return;
        translateSubtree(doc.body);
        translateDocumentTitle(doc);
        if (observer && doc === root.document) observer.takeRecords();
    }

    function wrapDialogs() {
        ['alert', 'confirm'].forEach(function (name) {
            var orig = root[name];
            if (typeof orig !== 'function' || orig.__i18nWrapped) return;
            var wrapped = function (msg) {
                return orig.call(root, (msg === undefined || msg === null) ? msg : convertText(String(msg)));
            };
            wrapped.__i18nWrapped = true;
            root[name] = wrapped;
        });
        var origPrompt = root.prompt;
        if (typeof origPrompt === 'function' && !origPrompt.__i18nWrapped) {
            // 只转换提示语；第二个参数是输入框默认值，属于数据，原样传入。
            var wp = function (msg, def) {
                var m = (msg === undefined || msg === null) ? msg : convertText(String(msg));
                return arguments.length > 1 ? origPrompt.call(root, m, def) : origPrompt.call(root, m);
            };
            wp.__i18nWrapped = true;
            root.prompt = wp;
        }
    }

    // ------------------------------------------------------------------
    // 声明式绑定：data-i18n="key"、data-i18n-attr="placeholder:key;title:key"
    // ------------------------------------------------------------------
    function applyBindings(scope) {
        var doc = root.document;
        scope = scope || (doc ? doc : null);
        if (!scope || !scope.querySelectorAll) return;
        var els = scope.querySelectorAll('[data-i18n]');
        for (var i = 0; i < els.length; i++) {
            var key = els[i].getAttribute('data-i18n');
            if (key) {
                var v = t(key);
                if (els[i].textContent !== v) els[i].textContent = v;
            }
        }
        var attrEls = scope.querySelectorAll('[data-i18n-attr]');
        for (var j = 0; j < attrEls.length; j++) {
            var spec = attrEls[j].getAttribute('data-i18n-attr') || '';
            spec.split(';').forEach(function (pair) {
                var idx = pair.indexOf(':');
                if (idx <= 0) return;
                var attr = pair.slice(0, idx).trim();
                var k = pair.slice(idx + 1).trim();
                if (attr && k) {
                    var val = t(k);
                    if (attrEls[j].getAttribute(attr) !== val) attrEls[j].setAttribute(attr, val);
                }
            });
        }
    }

    // ------------------------------------------------------------------
    // 语言切换
    // ------------------------------------------------------------------
    function getLocale() { return currentLocale; }
    function getSupportedLocales() { return SUPPORTED.slice(); }
    function getNativeName(loc) { return NATIVE_NAMES[loc] || loc; }

    function onChange(fn) {
        if (typeof fn === 'function') listeners.push(fn);
        return function () { listeners = listeners.filter(function (f) { return f !== fn; }); };
    }

    function rememberActiveTab() {
        var doc = root.document;
        var ss = safeStorage('sessionStorage');
        if (!doc || !ss) return;
        var active = doc.querySelector('.tab-content.active');
        try {
            if (active && active.id) ss.setItem(RESTORE_KEY, active.id);
        } catch (e) { /* ignore */ }
    }

    function restoreActiveTab() {
        var doc = root.document;
        var ss = safeStorage('sessionStorage');
        if (!doc || !ss) return;
        var id = null;
        try { id = ss.getItem(RESTORE_KEY); ss.removeItem(RESTORE_KEY); } catch (e) { id = null; }
        if (!id || !/^[\w-]+$/.test(id)) return;
        var current = doc.querySelector('.tab-content.active');
        if (current && current.id === id) return;
        var btn = doc.querySelector('.sidebar-btn[data-target="' + id + '"]');
        if (btn && btn.offsetParent !== null) btn.click();
    }

    function setLocale(next, opts) {
        var loc = normalizeLocale(next) || null;
        if (!loc || SUPPORTED.indexOf(loc) === -1) return false;
        opts = opts || {};
        var ls = safeStorage('localStorage');
        try { if (ls) ls.setItem(STORAGE_KEY, loc); } catch (e) { /* ignore */ }
        if (loc === currentLocale) return true;
        var prev = currentLocale;
        listeners.forEach(function (fn) { try { fn(loc, prev); } catch (e) { /* ignore */ } });
        if (opts.reload === false) {
            currentLocale = loc;
            return true;
        }
        rememberActiveTab();
        var loc0 = root.location;
        if (loc0) {
            try {
                var url = new URL(loc0.href);
                if (url.searchParams.has('lang')) {
                    url.searchParams.set('lang', loc);
                    loc0.replace(url.toString());
                    return true;
                }
            } catch (e) { /* ignore */ }
            loc0.reload();
        }
        return true;
    }

    // ------------------------------------------------------------------
    // 语言切换器（与角色切换器同风格；选项名各自用本语言显示，不参与转换）
    // ------------------------------------------------------------------
    function mountSwitcher(container) {
        var doc = root.document;
        if (!doc || !container) return null;
        container.innerHTML = '';
        container.classList.add('lang-switch');
        var label = doc.createElement('span');
        label.className = 'lang-switch-label';
        label.id = (container.id || 'langSwitch') + 'Label';
        label.setAttribute('data-i18n', 'lang.label');
        label.textContent = t('lang.label');
        container.appendChild(label);
        var group = doc.createElement('div');
        group.className = 'lang-switch-group';
        group.setAttribute('role', 'group');
        group.setAttribute('aria-labelledby', label.id);
        SUPPORTED.forEach(function (loc) {
            var b = doc.createElement('button');
            b.type = 'button';
            b.className = 'lang-switch-btn' + (loc === currentLocale ? ' active' : '');
            b.setAttribute('lang', HTML_LANG[loc]);
            b.setAttribute('translate', 'no');
            b.setAttribute('data-locale', loc);
            b.setAttribute('aria-pressed', loc === currentLocale ? 'true' : 'false');
            b.setAttribute('aria-label', t('lang.switchTo', { name: NATIVE_NAMES[loc] }));
            b.textContent = NATIVE_NAMES[loc];
            b.addEventListener('click', function () { setLocale(loc); });
            group.appendChild(b);
        });
        container.appendChild(group);
        return container;
    }

    // ------------------------------------------------------------------
    // 启动
    // ------------------------------------------------------------------
    var bootInfo = { source: 'default', revealAt: null };

    function boot() {
        var doc = root.document;
        var r = resolveLocale();
        currentLocale = r.locale;
        bootInfo.source = r.source;
        if (r.source === 'url') {
            var ls = safeStorage('localStorage');
            try { if (ls) ls.setItem(STORAGE_KEY, currentLocale); } catch (e) { /* ignore */ }
        }
        if (!doc) return;
        var html = doc.documentElement;
        html.setAttribute('lang', HTML_LANG[currentLocale] || currentLocale);
        html.setAttribute('data-locale', currentLocale);

        if (currentLocale === HANT) {
            // 转换完成前短暂隐藏页面，避免简体闪现；最多 300ms 后无条件显示。
            if (doc.head && !doc.getElementById('i18nPendingStyle')) {
                var st = doc.createElement('style');
                st.id = 'i18nPendingStyle';
                st.textContent = 'html.i18n-pending body{visibility:hidden}';
                doc.head.appendChild(st);
            }
            html.classList.add('i18n-pending');
            var goLive = function () {
                if (observerLive) return;
                if (observer) observer.takeRecords();
                translateDocument(doc);
                observerLive = true;
            };
            var reveal = function () {
                if (!html.classList.contains('i18n-pending')) return;
                goLive();
                html.classList.remove('i18n-pending');
                bootInfo.revealAt = (root.performance && root.performance.now) ? root.performance.now() : Date.now();
            };
            root.setTimeout(reveal, 300);
            wrapDialogs();
            startObserver(doc);
            translateDocumentTitle(doc);
            var onReady = function () {
                applyBindings(doc);
                if (observerLive) {
                    flushObserver();
                    translateDocument(doc);
                } else {
                    goLive();
                }
                reveal();
            };
            if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', onReady);
            else onReady();
        } else {
            var onReadyHans = function () { applyBindings(doc); };
            if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', onReadyHans);
            else onReadyHans();
        }

        var onReadyCommon = function () {
            var mounts = doc.querySelectorAll('[data-i18n-switcher]');
            for (var i = 0; i < mounts.length; i++) mountSwitcher(mounts[i]);
        };
        if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', onReadyCommon);
        else onReadyCommon();

        // main.js 的初始化（角色可见性、首屏数据）跑完后再恢复切换语言前的 Tab。
        root.addEventListener('load', function () { root.setTimeout(restoreActiveTab, 0); });
    }

    var I18N = {
        SOURCE_LOCALE: SOURCE_LOCALE,
        HANT: HANT,
        t: t,
        getLocale: getLocale,
        setLocale: setLocale,
        onChange: onChange,
        registerLocale: registerLocale,
        getSupportedLocales: getSupportedLocales,
        getNativeName: getNativeName,
        fmtMoney: fmtMoney,
        fmtNumber: fmtNumber,
        fmtDate: fmtDate,
        convert: convertText,
        applyBindings: applyBindings,
        translateDocument: translateDocument,
        mountSwitcher: mountSwitcher,
        // 以下供测试与诊断使用
        _normalizeLocale: normalizeLocale,
        _resolveLocale: resolveLocale,
        _localeFromNavigator: localeFromNavigator,
        _createConverter: createConverter,
        _translateSubtree: translateSubtree,
        _handleMutations: handleMutations,
        _setLocaleForTest: function (loc) { currentLocale = normalizeLocale(loc) || SOURCE_LOCALE; },
        _stats: stats,
        _bootInfo: bootInfo,
        _boot: boot
    };
    root.I18N = I18N;

    if (root.document && !root.__I18N_NO_AUTOBOOT) boot();
})(typeof window !== 'undefined' ? window : globalThis);
