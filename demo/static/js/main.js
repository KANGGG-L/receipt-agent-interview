
// 内置弹窗组件（替代 window.prompt 与 window.confirm）
function showCustomInputModal({ title, message, defaultValue, placeholder, onConfirm, onCancel }) {
    const overlay = document.createElement('div');
    overlay.className = 'modal-backdrop';
    overlay.style.cssText = 'position:fixed; top:0; left:0; width:100vw; height:100vh; background:rgba(0,0,0,0.5); z-index:99999; display:flex; align-items:center; justify-content:center;';
    
    const card = document.createElement('div');
    card.className = 'card';
    card.style.cssText = 'width:420px; max-width:90vw; background:var(--card-bg, #fff); border-radius:10px; padding:20px; box-shadow:0 8px 24px rgba(0,0,0,0.2); animation:modalFadeIn 0.2s ease;';

    const titleEl = document.createElement('h4');
    titleEl.style.cssText = 'margin:0 0 10px 0; font-size:1.1rem; color:var(--text-main, #333); font-weight:600;';
    titleEl.textContent = title || '请输入';
    card.appendChild(titleEl);

    if (message) {
        const msgEl = document.createElement('div');
        msgEl.style.cssText = 'font-size:0.85rem; color:var(--text-secondary, #666); margin-bottom:12px; white-space:pre-wrap; line-height:1.4;';
        msgEl.textContent = message;
        card.appendChild(msgEl);
    }

    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'form-control';
    input.style.cssText = 'width:100%; box-sizing:border-box; margin-bottom:16px; font-size:0.9rem; padding:8px 10px;';
    input.value = defaultValue || '';
    if (placeholder) input.placeholder = placeholder;
    card.appendChild(input);

    const btnRow = document.createElement('div');
    btnRow.style.cssText = 'display:flex; justify-content:flex-end; gap:10px;';

    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'btn btn-secondary';
    cancelBtn.style.cssText = 'padding:6px 14px; font-size:0.85rem; cursor:pointer;';
    cancelBtn.textContent = '取消';

    const confirmBtn = document.createElement('button');
    confirmBtn.type = 'button';
    confirmBtn.className = 'btn btn-primary';
    confirmBtn.style.cssText = 'padding:6px 14px; font-size:0.85rem; cursor:pointer;';
    confirmBtn.textContent = '确定';

    btnRow.appendChild(cancelBtn);
    btnRow.appendChild(confirmBtn);
    card.appendChild(btnRow);
    overlay.appendChild(card);
    document.body.appendChild(overlay);

    input.focus();
    input.select();

    function close() {
        if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay);
    }

    cancelBtn.onclick = () => { close(); if (onCancel) onCancel(); };
    confirmBtn.onclick = () => { const val = input.value; close(); if (onConfirm) onConfirm(val); };
    input.onkeydown = (e) => {
        if (e.key === 'Enter') { confirmBtn.click(); }
        else if (e.key === 'Escape') { cancelBtn.click(); }
    };
}

function showCustomConfirmModal({ title, message, confirmText = '确定', cancelText = '取消', onConfirm, onCancel }) {
    const overlay = document.createElement('div');
    overlay.className = 'modal-backdrop custom-modal-overlay';
    overlay.style.cssText = 'position:fixed; top:0; left:0; width:100vw; height:100vh; background:rgba(0,0,0,0.5); z-index:999999; display:flex; align-items:center; justify-content:center;';
    
    const card = document.createElement('div');
    card.className = 'card';
    card.style.cssText = 'width:420px; max-width:90vw; background:var(--card-bg, #fff); border-radius:10px; padding:20px; box-shadow:0 8px 24px rgba(0,0,0,0.25);';

    const titleEl = document.createElement('h4');
    titleEl.style.cssText = 'margin:0 0 10px 0; font-size:1.1rem; color:var(--text-main, #333); font-weight:600;';
    titleEl.textContent = title || '请确认';
    card.appendChild(titleEl);

    if (message) {
        const msgEl = document.createElement('div');
        msgEl.style.cssText = 'font-size:0.9rem; color:var(--text-main, #444); margin-bottom:18px; white-space:pre-wrap; line-height:1.5; word-break:break-word;';
        msgEl.textContent = message;
        card.appendChild(msgEl);
    }

    const btnRow = document.createElement('div');
    btnRow.style.cssText = 'display:flex; justify-content:flex-end; gap:10px;';

    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'btn btn-secondary';
    cancelBtn.style.cssText = 'padding:6px 14px; font-size:0.85rem; cursor:pointer;';
    cancelBtn.textContent = cancelText;

    const confirmBtn = document.createElement('button');
    confirmBtn.type = 'button';
    confirmBtn.className = 'btn btn-primary';
    confirmBtn.style.cssText = 'padding:6px 14px; font-size:0.85rem; cursor:pointer;';
    confirmBtn.textContent = confirmText;

    btnRow.appendChild(cancelBtn);
    btnRow.appendChild(confirmBtn);
    card.appendChild(btnRow);
    overlay.appendChild(card);
    document.body.appendChild(overlay);

    confirmBtn.focus();

    const keyHandler = (e) => {
        if (e.key === 'Escape') {
            cancelBtn.click();
        } else if (e.key === 'Enter') {
            confirmBtn.click();
        }
    };
    window.addEventListener('keydown', keyHandler);

    function close() {
        window.removeEventListener('keydown', keyHandler);
        if (overlay && overlay.parentNode) {
            overlay.parentNode.removeChild(overlay);
        }
    }

    cancelBtn.onclick = (e) => {
        e.preventDefault();
        e.stopPropagation();
        close();
        if (typeof onCancel === 'function') onCancel();
    };

    confirmBtn.onclick = (e) => {
        e.preventDefault();
        e.stopPropagation();
        close();
        if (typeof onConfirm === 'function') onConfirm();
    };
}

// ==========================================================================
// 香港餐饮 AI 收据与库存管理系统 - 前端交互逻辑
// ==========================================================================
//
// 【硬规则（R0 修复后设立，P0-1..P0-4 系统性整改）】
// 一切进入 innerHTML 的动态插值必须过转义：
//   - HTML 文本/属性上下文 → w2Escape(...)
//   - 行内 JS 字符串字面量   → jsStr(...)（或直接改事件委托 + dataset，首选）
// 禁止把后端字段（供应商名/品名/单位/邮箱 operator/履历 details 等）
// 裸拼进 innerHTML：这些值可被 OCR 输出、文件名、注册邮箱等外部输入污染。
// 下拉选项等交互一律事件委托 + data-* 传值，不写内联 onXxx="fn(this,'插值')"。

// ---- 转义小工具（全文件共用；函数声明提升，供全文调用） ----
function w2Escape(s) {
    return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function makeSvgDataUrl(text) {
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80"><rect width="80" height="80" fill="#e2e8f0"/><text x="50%" y="55%" dominant-baseline="middle" text-anchor="middle" fill="#475569" font-size="11" font-family="sans-serif">${text}</text></svg>`;
    return 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(svg)));
}

function makeLargeSvgDataUrl(title, subtitle) {
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300" viewBox="0 0 400 300"><rect width="400" height="300" fill="#f1f5f9"/><text x="50%" y="45%" dominant-baseline="middle" text-anchor="middle" fill="#334155" font-size="16" font-weight="bold" font-family="sans-serif">${title}</text><text x="50%" y="58%" dominant-baseline="middle" text-anchor="middle" fill="#64748b" font-size="13" font-family="sans-serif">${subtitle}</text></svg>`;
    return 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(svg)));
}

/** 浏览器无法直接预览的格式（需服务端转 JPEG 后再展示） */
function isNonWebImageFile(file) {
    if (!file) return false;
    const name = typeof file === 'string' ? file : (file.name || '');
    const type = (typeof file === 'object' && file && file.type || '').toLowerCase();
    const ext = name.includes('.') ? name.split('.').pop().toLowerCase() : '';
    if (['heic', 'heif', 'tif', 'tiff'].includes(ext)) return true;
    if (type && /^image\/(heic|heif|tiff)$/i.test(type)) return true;
    return false;
}

/** URL 是否为浏览器可直接渲染的图片（排除 HEIC/HEIF/TIFF 路径） */
function isBrowserDisplayableImageUrl(url) {
    if (!url || typeof url !== 'string') return false;
    if (url.startsWith('data:image/svg')) return false; // 占位 SVG 不算「真实原图」
    if (url.startsWith('data:image/')) return true;
    // blob: 仅当本地文件本身是 web 格式时使用（由调用方保证）
    if (url.startsWith('blob:')) return true;
    const path = url.split('?')[0].toLowerCase();
    if (/\.(heic|heif|tif|tiff)$/.test(path)) return false;
    if (/\.(jpe?g|png|webp|gif|bmp)$/.test(path)) return true;
    // /uploads/xxx_norm.jpg 等无扩展名兜底：无明确非 web 后缀则允许尝试
    return !/\.(heic|heif|tif|tiff)(\.|$)/i.test(path);
}

function nonWebFormatLabel(file) {
    if (!file) return 'HEIC';
    const name = file.name || '';
    const ext = name.includes('.') ? name.split('.').pop().toUpperCase() : '';
    if (ext && ['HEIC', 'HEIF', 'TIF', 'TIFF'].includes(ext)) return ext;
    const type = (file.type || '').toLowerCase();
    if (type.includes('heic')) return 'HEIC';
    if (type.includes('heif')) return 'HEIF';
    if (type.includes('tiff')) return 'TIFF';
    return ext || 'HEIC';
}

/** 按格式生成「如何自己转成 JPEG」操作提示（面向非技术用户） */
function buildNonWebConvertHowtoItems(fmt) {
    const f = (fmt || 'HEIC').toUpperCase();
    if (f === 'TIF' || f === 'TIFF') {
        return [
            { strong: 'Mac', text: '预览 App 打开 → 文件 → 导出 → 格式选 JPEG' },
            { strong: 'Windows', text: '用「照片」或画图打开 → 另存为 / 导出为 JPG' },
            { strong: '手机', text: '用系统相册「导出 / 分享」并选择 JPEG 或「储存图片」' },
            { strong: '本系统', text: '也可直接点解析，服务端会自动转为 JPEG' },
        ];
    }
    // HEIC / HEIF 默认
    return [
        { strong: 'iPhone 以后拍照', text: '设置 → 相机 → 格式 → 选「兼容性最佳」' },
        { strong: 'iPhone 导出这张', text: '照片 App 选图 → 分享 → 储存到文件 / 隔空投送' },
        { strong: 'Mac', text: '预览 App 打开 → 文件 → 导出 → 格式选 JPEG' },
        { strong: 'Windows', text: '用「照片」打开后另存，或在线工具导出为 JPG' },
        { strong: '本系统', text: '也可直接点解析，服务端会自动转为 JPEG，无需先转换' },
    ];
}

/**
 * 展示/隐藏「浏览器无法直接预览」常驻提示。
 * 有可显示 URL（服务端 JPEG / 裁剪图）时隐藏；否则对 HEIC 等保持展示，不被 reset 冲掉。
 */
function setNonWebPreviewHint(visible, file) {
    const hint = document.getElementById('previewNonWebHint');
    const container = document.getElementById('imgViewerContainer');
    if (!hint) return;
    if (visible) {
        const fmt = nonWebFormatLabel(file);
        const titleEl = document.getElementById('previewNonWebHintTitle');
        const subEl = document.getElementById('previewNonWebHintSub');
        const listEl = document.getElementById('previewNonWebHintHowtoList');
        if (titleEl) titleEl.textContent = `${fmt} 照片`;
        if (subEl) {
            subEl.textContent = (fmt === 'HEIC' || fmt === 'HEIF')
                ? '浏览器无法直接预览 HEIC，解析时会自动转为 JPEG'
                : `浏览器无法直接预览 ${fmt}，解析时会自动转为 JPEG`;
        }
        if (listEl) {
            const items = buildNonWebConvertHowtoItems(fmt);
            // 用 textContent 写入，避免文件名/文案注入
            listEl.innerHTML = '';
            items.forEach(({ strong, text }) => {
                const li = document.createElement('li');
                const b = document.createElement('strong');
                b.textContent = strong;
                li.appendChild(b);
                li.appendChild(document.createTextNode('：' + text));
                listEl.appendChild(li);
            });
        }
        hint.classList.remove('hide');
        if (container) container.classList.add('nonweb-preview');
    } else {
        hint.classList.add('hide');
        if (container) container.classList.remove('nonweb-preview');
    }
}

/**
 * 解析一张照片应使用的预览 URL + 是否显示非 Web 提示。
 * 永不返回 HEIC blob / HEIC 服务端路径（浏览器会黑屏）。
 */
function resolvePhotoPreview(photo, fallbackFile) {
    const file = (photo && photo.file) || fallbackFile || null;
    const isNonWeb = isNonWebImageFile(file);
    if (photo && photo.croppedObjectUrl) {
        return { url: photo.croppedObjectUrl, showNonWebHint: false, file };
    }
    if (photo && photo.imageUrl && isBrowserDisplayableImageUrl(photo.imageUrl)) {
        return { url: photo.imageUrl, showNonWebHint: false, file };
    }
    // 服务端 image_url 仍是 heic 路径：当不可预览
    if (isNonWeb) {
        return {
            url: makeLargeSvgDataUrl(
                `${nonWebFormatLabel(file)} 照片`,
                '浏览器无法直接预览 HEIC，解析时会自动转为 JPEG'
            ),
            showNonWebHint: true,
            file,
        };
    }
    if (photo && photo.objectUrl) {
        return { url: photo.objectUrl, showNonWebHint: false, file };
    }
    return { url: '', showNonWebHint: false, file };
}

/** 统一写入主预览：可显示图 或 常驻 HEIC 提示（不被后续 transform 重置清掉） */
function setMainPreview(photoOrNull, fallbackFile) {
    const img = document.getElementById('previewImg');
    if (!img) return;
    const { url, showNonWebHint, file } = resolvePhotoPreview(photoOrNull, fallbackFile);
    if (url) img.src = url;
    setNonWebPreviewHint(showNonWebHint, file);
}

/** 单张模式：本地 File 选中后的预览 */
function setMainPreviewFromFile(file, objectUrl) {
    const img = document.getElementById('previewImg');
    if (!img) return;
    if (isNonWebImageFile(file)) {
        img.src = makeLargeSvgDataUrl(
            `${nonWebFormatLabel(file)} 照片`,
            '浏览器无法直接预览 HEIC，解析时会自动转为 JPEG'
        );
        setNonWebPreviewHint(true, file);
        return;
    }
    if (objectUrl) img.src = objectUrl;
    setNonWebPreviewHint(false, file);
}

// 生成可安全嵌入单引号 HTML 属性（onclick='...'）的 JS 字符串字面量
function jsStr(v) {
    return JSON.stringify(String(v ?? '')).replace(/</g, '\\u003c').replace(/'/g, '\\u0027');
}

// =====================================================================
// P1-12：前端鉴权集成（AUTH_ENABLED=1 可用，R4）
//   - 启动探测 GET /api/auth/me：auth_enabled=false 或 200 → 正常进入；401 → 登录面板
//   - 全局 fetch 包装：/api/ 开头请求自动注入 Authorization: Bearer <token>
//     （保留原 window.fetch；登录/注册豁免注入）
//   - 任何 /api/ 响应再 401 → 回登录面板
// 纯函数（buildAuthHeaders/attachAuthInit/decideAuthProbe/createAuthFetchWrapper）
// 供 tests/frontend/r4_logic_test.js 在 node vm 中直接断言。
// =====================================================================
const AUTH_TOKEN_KEY = 'receipt_auth_token';
const AUTH_EXEMPT_FRONTEND = ['/api/auth/login', '/api/auth/register'];

const AuthState = {
    enabled: false,          // 服务端是否开启鉴权（/api/auth/me 的 auth_enabled）
    account: null,           // 当前账号信息
    loginPanelShowing: false,
};

function getAuthToken() {
    try { return localStorage.getItem(AUTH_TOKEN_KEY) || null; } catch (e) { return null; }
}
function setAuthToken(token) {
    try {
        if (token) localStorage.setItem(AUTH_TOKEN_KEY, token);
        else localStorage.removeItem(AUTH_TOKEN_KEY);
    } catch (e) { /* localStorage 不可用时降级为会话内失忆 */ }
}

// 仅本站相对路径的 /api/ 前缀注入令牌——外链/绝对 URL 一律不带 token
function isApiUrl(url) {
    return typeof url === 'string' && url.indexOf('/api/') === 0;
}
function isAuthInjectableUrl(url) {
    return isApiUrl(url) && AUTH_EXEMPT_FRONTEND.indexOf(url) === -1;
}

// 当前是否店员角色（与 buildAuthHeaders 同源：localStorage demo_role，同步可用，
// 不依赖异步 /me 探测）——店员不发老板域请求（财务/对账/AI洞察/成本报表），避免全局 403 弹窗
function isStaffRoleNow() {
    try { return localStorage.getItem('demo_role') === 'staff'; } catch (e) { return false; }
}

// 纯函数：给定 url 与 token，返回应注入的请求头对象；不注入时返回 null
function buildAuthHeaders(url, token) {
    if (!isAuthInjectableUrl(url)) return null;
    const headers = {};
    // demo 无密码 RBAC：始终附带当前角色（select 下拉切换，存 localStorage）
    const demoRole = (() => { try { return localStorage.getItem('demo_role'); } catch (e) { return null; } })();
    const role = (demoRole === 'admin' || demoRole === 'owner' || demoRole === 'staff') ? demoRole : 'owner';
    headers['X-Role'] = role;
    if (token) headers['Authorization'] = 'Bearer ' + token;
    return headers;
}

// 纯函数：把 Authorization 附加到 fetch init（不修改传入对象，已有显式
// Authorization 时尊重调用方不覆盖）
function attachAuthInit(init, url, token) {
    const extra = buildAuthHeaders(url, token);
    if (!extra) return init;
    const headers = (init && init.headers) || {};
    // Headers 实例：就地补充（其内容本就属于本次请求构造）
    if (typeof Headers !== 'undefined' && headers instanceof Headers) {
        if (!headers.has('Authorization')) headers.set('Authorization', extra.Authorization);
        return Object.assign({}, init || {}, { headers: headers });
    }
    const keys = Object.keys(headers);
    for (let i = 0; i < keys.length; i++) {
        if (String(keys[i]).toLowerCase() === 'authorization') return init;
    }
    return Object.assign({}, init || {}, { headers: Object.assign({}, headers, extra) });
}

// 纯工厂：包装 originalFetch 返回新 fetch——/api/ 注入令牌；/api/ 响应 401
// 触发 onUnauthorized（豁免路径除外：登录失败自身的 401 不回弹面板）
function createAuthFetchWrapper(originalFetch, opts) {
    const getToken = (opts && opts.getToken) || getAuthToken;
    const onUnauthorized = (opts && opts.onUnauthorized) || null;
    return function authWrappedFetch(input, init) {
        const url = typeof input === 'string' ? input : ((input && input.url) || '');
        const finalInit = attachAuthInit(init, url, getToken());
        return originalFetch(input, finalInit).then(res => {
            if (onUnauthorized && res && res.status === 401
                && isApiUrl(url) && AUTH_EXEMPT_FRONTEND.indexOf(url) === -1) {
                try { onUnauthorized(); } catch (e) { /* 面板异常不影响响应传递 */ }
            }
            if (res && res.status === 403 && isApiUrl(url)) {
                // E-P1-4 克隆响应读取 detail 以人话 toast，不消费原响应
                try { res.clone().json().then(b => { toastHttpError(403, b); }).catch(()=>{}); } catch(e) {}
            }
            return res;
        });
    };
}

// 安装全局 fetch 包装（脚本加载即生效，先于 DOMContentLoaded 的各数据拉取）
(function installAuthFetch() {
    const scope = (typeof window !== 'undefined') ? window : globalThis;
    if (!scope || typeof scope.fetch !== 'function') return;
    const originalFetch = scope.fetch.bind(scope);
    scope.fetch = createAuthFetchWrapper(originalFetch, {
        getToken: getAuthToken,
        onUnauthorized: () => showLoginPanel(),
    });
})();

// 纯函数：/api/auth/me 探测结果的进入决策
function decideAuthProbe(httpStatus, _body) {
    if (httpStatus === 401) return 'login';
    // 2xx 正常进入；其他状态（含网络层已 catch）不阻断进入，后端异常会自行暴露
    return 'proceed';
}

// 登录面板（JS 动态创建，全屏遮罩；文案一律 textContent，无插值注入面）
function showLoginPanel(hintMsg) {
    if (AuthState.loginPanelShowing) {
        if (hintMsg) {
            const e = document.getElementById('authLoginError');
            if (e && !e.textContent) e.textContent = hintMsg;
        }
        return;
    }
    AuthState.loginPanelShowing = true;

    const overlay = document.createElement('div');
    overlay.id = 'authLoginOverlay';
    overlay.style.cssText = 'position:fixed; inset:0; z-index:9999; background:rgba(243,242,241,0.55); backdrop-filter:blur(6px); display:flex; align-items:center; justify-content:center;';

    const card = document.createElement('div');
    card.style.cssText = 'width:360px; max-width:92vw; background:var(--bg-card); border:1px solid var(--border-color); border-radius:16px; padding:28px 26px; box-shadow:0 12px 32px rgba(0,0,0,0.12);';

    const title = document.createElement('h3');
    title.textContent = ' 系统登录';
    title.style.cssText = 'margin:0 0 6px; font-size:1.15rem; font-family:var(--font-display); color:var(--text-main);';

    const sub = document.createElement('div');
    sub.textContent = '系统已开启鉴权，请使用注册邮箱与密码登录后继续。';
    sub.style.cssText = 'color:var(--text-muted); font-size:0.85rem; margin-bottom:14px;';

    const errBox = document.createElement('div');
    errBox.id = 'authLoginError';
    errBox.style.cssText = 'color:var(--danger); font-size:0.85rem; margin-bottom:10px; min-height:1em;';
    if (hintMsg) errBox.textContent = hintMsg;

    const emailInput = document.createElement('input');
    emailInput.type = 'email';
    emailInput.id = 'authLoginEmail';
    emailInput.placeholder = '邮箱';
    emailInput.autocomplete = 'username';
    emailInput.className = 'form-control';
    emailInput.style.cssText = 'width:100%; margin-bottom:10px; box-sizing:border-box;';

    const pwdInput = document.createElement('input');
    pwdInput.type = 'password';
    pwdInput.id = 'authLoginPassword';
    pwdInput.placeholder = '密码';
    pwdInput.autocomplete = 'current-password';
    pwdInput.className = 'form-control';
    pwdInput.style.cssText = 'width:100%; margin-bottom:14px; box-sizing:border-box;';

    const btn = document.createElement('button');
    btn.className = 'btn btn-primary';
    btn.id = 'authLoginSubmit';
    btn.style.cssText = 'width:100%; padding:10px; font-size:0.95rem;';
    btn.textContent = '登 录';
    btn.addEventListener('click', submitAuthLogin);
    const onEnter = (e) => { if (e && e.key === 'Enter') submitAuthLogin(); };
    emailInput.addEventListener('keydown', onEnter);
    pwdInput.addEventListener('keydown', onEnter);

    card.appendChild(title);
    card.appendChild(sub);
    card.appendChild(errBox);
    card.appendChild(emailInput);
    card.appendChild(pwdInput);
    card.appendChild(btn);
    overlay.appendChild(card);
    document.body.appendChild(overlay);
    if (emailInput.focus) emailInput.focus();
}

function hideLoginPanel() {
    AuthState.loginPanelShowing = false;
    const overlay = document.getElementById('authLoginOverlay');
    if (overlay && overlay.remove) overlay.remove();
}

function submitAuthLogin() {
    const emailElem = document.getElementById('authLoginEmail');
    const pwdElem = document.getElementById('authLoginPassword');
    const errBox = document.getElementById('authLoginError');
    const email = emailElem ? String(emailElem.value || '').trim() : '';
    const password = pwdElem ? String(pwdElem.value || '') : '';
    if (!email || !password) {
        if (errBox) errBox.textContent = '请输入邮箱和密码';
        return;
    }
    const btn = document.getElementById('authLoginSubmit');
    if (btn) btn.disabled = true;

    fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email, password: password })
    })
    .then(res => Promise.all([res.status, res.json().catch(() => null)]))
    .then(([httpStatus, ret]) => {
        if (btn) btn.disabled = false;
        if (httpStatus === 200 && ret && ret.status === 'success' && ret.token) {
            setAuthToken(ret.token);
            AuthState.account = ret.account || null;
            if (errBox) errBox.textContent = '';
            // 登录成功 → 重探 /me 校验令牌并恢复正常界面
            probeAuthAndEnter(true);
            return;
        }
        if (errBox) errBox.textContent = (ret && ret.msg) || ('登录失败（HTTP ' + httpStatus + '）');
    })
    .catch(err => {
        if (btn) btn.disabled = false;
        console.error('登录请求异常', err);
        if (errBox) errBox.textContent = '登录失败，请稍后重试';
    });
}

// 启动/登录后探测 /api/auth/me。reloadData=true（登录成功后）重载首屏数据。
function probeAuthAndEnter(reloadData) {
    return fetch('/api/auth/me')
        .then(res => Promise.all([res.status, res.json().catch(() => null)]))
        .then(([httpStatus, body]) => {
            if (decideAuthProbe(httpStatus, body) === 'login') {
                setAuthToken(null);
                showLoginPanel('请先登录');
                return;
            }
            AuthState.enabled = !!(body && body.auth_enabled);
            AuthState.account = (body && body.account) || null;
            hideLoginPanel();
            syncManualEntryVisibility();   // D12：按角色显隐「新建手工单」入口
            if (reloadData) {
                // 登录前首屏拉取均 401 空手，登录后补拉
                loadInventoryData();
                loadSuppliersData();
                loadReceiptsHistory();
                loadFinancePanel();     // Wave 1：付款域提醒（红点/横幅）
                showToast('登录成功', 'success');
            }
        })
        .catch(() => {
            // 网络异常不阻断进入（后端不可用时各面板会自行报错）
            hideLoginPanel();
            syncManualEntryVisibility();   // D12：无鉴权部署时 account=null → 视为 owner
        });
}

let currentReceiptId = null;
let currentReceiptData = null;

// D12：手工录入态（新建手工单：无原图、无 receipt_id；保存前不触发既有单校验）
let isManualEntry = false;

// 识别失败错误卡片 (D28/D33)：记录失败单据 id（null 表示未生成单据行，仅可重新上传）
let lastErrorReceiptId = null;

// -------------------------------------------------------------
// 质量预检阈值 (D21 客户端)：从宽设定——过底纸/热敏纸天然偏淡
// 亮度低于阈值视为过暗；拉普拉斯方差低于阈值视为疑似模糊
// -------------------------------------------------------------
const QUALITY_BRIGHTNESS_MIN = 45;
const QUALITY_SHARPNESS_MIN = 20;

// 原始未裁剪文件与当前选定文件
let originalFile = null;
let originalObjectUrl = null;
let selectedFile = null;

// U-12：文件选择与拖放并发锁 / 代数标识及防抖定时器
let fileSelectionGen = 0;
let _fileSelectDebounceTimer = null;
function nextFileSelectionGen() {
    fileSelectionGen++;
    if (typeof window !== 'undefined') {
        window.fileSelectionGen = fileSelectionGen;
    }
    return fileSelectionGen;
}
if (typeof window !== 'undefined') {
    window.fileSelectionGen = 0;
    window.nextFileSelectionGen = nextFileSelectionGen;
}

let currentZoom = 1.0;
let currentRotation = 0;
let isFocalZoomed = false;

// 裁剪控制变量
let isCropDragging = false;
let cropStartX = 0;
let cropStartY = 0;
let cropRect = { left: 0, top: 0, width: 0, height: 0 };

// 拖拽平移控制变量
let panX = 0;
let panY = 0;
let isMouseDown = false;
let isDragging = false;
let startMouseX = 0;
let startMouseY = 0;

// 可复用的计量单位池 (预置香港餐饮常用单位)
let availableUnits = [
    "kg", "g", "司马斤", "斤", "两", "磅",
    "箱", "包", "件", "罐", "隻", "只", "瓶", "盒", "桶", "扎", "把", "袋"
];

// =====================================================================
// Wave 2（D44）：部门归属与花销——全局部门数据 + 下拉/一键填充工具
// =====================================================================
let allDepartments = [];          // GET /api/departments 全量（含停用）；下拉/报表自行过滤 active

// 只列 active 部门（停用部门不进"选部门"下拉，见 04 章二）
function activeDepartments() {
    return (allDepartments || []).filter(d => Number(d.active) === 1);
}

function loadDepartmentsAll() {
    return fetch('/api/departments')
        .then(res => res.json())
        .then(ret => {
            if (ret && ret.status === 'success') allDepartments = ret.data || [];
        })
        .catch(err => { console.error('加载部门列表失败:', err); allDepartments = []; });
}

// 生成部门下拉 options：空选项（未分配）+ active 部门；若 selectedId 是已停用部门
// （历史成本补打场景），追加并选中它——保证编辑已停用部门单据不静默丢值。
function deptSelectOptionsHtml(selectedId) {
    const cur = (selectedId != null && String(selectedId).trim() !== '')
        ? Number(selectedId) : null;
    let html = '<option value="">未分配</option>';
    activeDepartments().forEach(d => {
        const sel = (cur !== null && Number(d.id) === cur) ? ' selected' : '';
        html += `<option value="${Number(d.id)}"${sel}>${w2Escape(d.name)}</option>`;
    });
    if (cur !== null && !activeDepartments().some(d => Number(d.id) === cur)) {
        const inactiveDept = (allDepartments || []).find(d => Number(d.id) === cur);
        if (inactiveDept) {
            html += `<option value="${Number(cur)}" selected>${w2Escape(inactiveDept.name)} 已停用</option>`;
        }
    }
    return html;
}

// 把部门 options 填充进指定 <select>（单据级下拉用），保留已停用部门的当前选择
function populateDeptSelect(selectEl, selectedId) {
    if (!selectEl) return;
    selectEl.innerHTML = deptSelectOptionsHtml(selectedId);
}

// 一键填充：单据级部门 → 所有"未手动改过"的明细行（04 章三：data-manual 行跳过）
function applyDeptToAll(tableBodyId, headerSelectId) {
    const headerSel = document.getElementById(headerSelectId);
    const deptId = headerSel ? (headerSel.value || '') : '';
    const deptName = (headerSel && headerSel.selectedOptions && headerSel.selectedOptions[0])
        ? headerSel.selectedOptions[0].text : '未分配';
    const tbody = document.getElementById(tableBodyId);
    if (!tbody) return;
    let applied = 0;
    tbody.querySelectorAll('tr').forEach(tr => {
        if (tr.dataset && tr.dataset.manual === '1') return;   // 已手动改过 → 不动
        const sel = tr.querySelector('.inp-dept');
        if (sel) { sel.value = deptId; applied++; }
    });
    showToast(applied > 0
        ? `已将部门「${deptName}」应用到 ${applied} 行明细`
        : '没有可应用的明细行', 'info');
}

// ---------------------------------------------------------------------
// Wave 2（D44）：部门管理 UI（契约①–④；owner 专属增/改/停用，staff 只读）
// ---------------------------------------------------------------------
function loadDepartmentAdmin() {
    const owner = isOwnerRole();
    const addBtn = document.getElementById('btnAddDepartment');
    if (addBtn) addBtn.classList.toggle('hide', !owner);

    return fetch('/api/departments')
        .then(res => res.json())
        .then(ret => {
            if (!ret || ret.status !== 'success') return;
            allDepartments = ret.data || [];   // 同步全局部门数据，供下拉/报表复用
            const body = document.getElementById('departmentAdminBody');
            if (!body) return;
            body.innerHTML = '';
            const depts = allDepartments;
            if (depts.length === 0) {
                body.innerHTML = '<tr><td colspan="3" style="text-align:center; color:var(--text-muted); padding:24px;">暂无部门，管理者可点击上方「新增部门」创建</td></tr>';
                return;
            }
            depts.forEach(d => {
                const id = Number(d.id);
                const active = Number(d.active) === 1;
                const statusBadge = active
                    ? '<span class="badge badge-success">启用中</span>'
                    : '<span class="badge badge-secondary">已停用</span>';
                let actions = '<span style="color:var(--text-muted); font-size:0.75rem;">只读</span>';
                if (owner) {
                    actions = `<button class="btn btn-secondary" style="padding:2px 8px; font-size:0.75rem;"
                            onclick="renameDepartment(${id})" title="重命名部门">改名</button> `;
                    if (active) {
                        actions += `<button class="btn btn-danger" style="padding:2px 8px; font-size:0.75rem;"
                            onclick="deactivateDepartment(${id})"
                            title="停用后不再出现在选部门下拉，历史花销仍留在报表">停用</button>`;
                    } else {
                        actions += '<span style="color:var(--text-muted); font-size:0.72rem;">已停用</span>';
                    }
                }
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td>${w2Escape(d.name)}${active ? '' : ' <span style="color:var(--text-muted); font-size:0.72rem;">已停用</span>'}</td>
                    <td class="col-center">${statusBadge}</td>
                    <td class="col-center" style="white-space:nowrap;">${actions}</td>`;
                body.appendChild(tr);
            });
        })
        .catch(err => { console.error('加载部门列表失败', err); showToast('加载部门列表失败' + toastFailDetail(err), 'error'); });
}

// 契约②：新增部门（owner）
function addDepartment() {
    const modal = document.getElementById('departmentModal');
    const title = document.getElementById('departmentModalTitle');
    const input = document.getElementById('inpDepartmentName');
    const idInput = document.getElementById('modalDepartmentId');
    if (modal && input) {
        if (title) title.innerText = '新增部门';
        input.value = '';
        if (idInput) idInput.value = '';
        modal.classList.remove('hide');
        setTimeout(() => input.focus(), 50);
        return;
    }
    const name = prompt('新增部门名称：');
    if (name === null) return;
    const n = String(name).trim();
    if (!n) { showToast('部门名称不能为空', 'warning'); return; }
    _saveDepartmentApi('', n);
}

// 契约③：重命名部门（owner）
function renameDepartment(id) {
    const dept = (allDepartments || []).find(d => Number(d.id) === Number(id));
    const cur = dept ? dept.name : '';
    const modal = document.getElementById('departmentModal');
    const title = document.getElementById('departmentModalTitle');
    const input = document.getElementById('inpDepartmentName');
    const idInput = document.getElementById('modalDepartmentId');
    if (modal && input) {
        if (title) title.innerText = '重命名部门';
        input.value = cur;
        if (idInput) idInput.value = String(id);
        modal.classList.remove('hide');
        setTimeout(() => input.focus(), 50);
        return;
    }
    const name = prompt('重命名部门（原名：' + cur + '）：', cur);
    if (name === null) return;
    const n = String(name).trim();
    if (!n) { showToast('部门名称不能为空', 'warning'); return; }
    _saveDepartmentApi(id, n);
}

function submitDepartmentCreate() {
    const input = document.getElementById('inpDepartmentName');
    const idInput = document.getElementById('modalDepartmentId');
    if (!input) return;
    const n = input.value.trim();
    if (!n) { showToast('部门名称不能为空', 'warning'); return; }
    const id = idInput ? idInput.value.trim() : '';
    _saveDepartmentApi(id, n);
}

function _saveDepartmentApi(id, n) {
    const isEdit = !!id;
    const url = isEdit ? ('/api/departments/' + Number(id)) : '/api/departments';
    const method = isEdit ? 'PATCH' : 'POST';

    fetch(url, {
        method: method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: n }),
    })
    .then(res => Promise.all([res.status, res.json().catch(() => null)]))
    .then(([httpStatus, ret]) => {
        if (!ret || ret.status !== 'success') {
            console.error('[department] 保存失败', httpStatus, ret);
            showToast((isEdit ? '重命名' : '新增') + '部门失败，请稍后重试', 'error');
            return;
        }
        closeModalById('departmentModal');
        showToast((isEdit ? '已重命名为「' : '已新增部门「') + n + '」', 'success');
        const details = document.getElementById('deptAdminDetails');
        if (details) details.open = true;
        loadDepartmentAdmin();
        loadDepartmentsAll();
    })
    .catch(err => {
        console.error('部门保存异常', err);
        showToast('保存部门失败' + toastFailDetail(err), 'error');
    });
}

// 契约④：停用部门（owner，软删除；不得停用最后一个启用部门——后端 400 兜底）
function deactivateDepartment(id) {
    const dept = (allDepartments || []).find(d => Number(d.id) === Number(id));
    const label = dept ? dept.name : ('#' + Number(id));
    if (!confirm('确定停用部门「' + label + '」？\n\n停用后它不再出现在「选部门」下拉中，'
        + '但历史明细成本仍归属该部门并照常出现在花销报表。')) return;
    fetch('/api/departments/' + Number(id), { method: 'DELETE' })
    .then(res => Promise.all([res.status, res.json().catch(() => null)]))
    .then(([httpStatus, ret]) => {
        if (!ret || ret.status !== 'success') {
            console.error('[department] 停用失败', httpStatus, ret);
            showToast('停用部门失败，请稍后重试', 'error');
            return;
        }
        showToast('已停用部门「' + label + '」', 'success');
        loadDepartmentAdmin();
        loadDepartmentsAll();
    })
    .catch(err => { console.error('停用部门请求异常', err); showToast('停用部门失败' + toastFailDetail(err), 'error'); });
}

// ---------------------------------------------------------------------
// Wave 2（D44）：部门花销报表（契约⑤ 月×部门 汇总 + 契约⑥ 下钻）
// 口径：明细行 amount 按 cost_center_id 归属；月份一律以 receipt_date 为准。
// ---------------------------------------------------------------------
let lastCostReportQuery = '';
let lastCostReportData = null;

function currentMonthStr() {
    const now = new Date();
    return now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0');
}

function onCostReportMonthChange() {
    const startInput = document.getElementById('costReportStartDate');
    const endInput = document.getElementById('costReportEndDate');
    if (startInput) startInput.value = '';
    if (endInput) endInput.value = '';
}

function setCostReportCurrentMonth() {
    const monthInput = document.getElementById('costReportMonth');
    const startInput = document.getElementById('costReportStartDate');
    const endInput = document.getElementById('costReportEndDate');
    if (monthInput) monthInput.value = currentMonthStr();
    if (startInput) startInput.value = '';
    if (endInput) endInput.value = '';
    loadCostReport();
}

// U-07：月份未选时，默认跳到最近有已入库（approved）单据的月份；全库为空才回落当前月
function loadCostReportAutoMonth() {
    const monthInput = document.getElementById('costReportMonth');
    if (monthInput && monthInput.value.trim()) { loadCostReport(); return; }
    fetch('/api/receipts')
    .then(res => res.json())
    .then(ret => {
        let target = null;
        if (ret && ret.status === 'success' && Array.isArray(ret.data)) {
            const months = ret.data
                .filter(r => r && r.status === 'approved' && r.receipt_date)
                .map(r => String(r.receipt_date).slice(0, 7))
                .sort();
            if (months.length) target = months[months.length - 1];
        }
        const cur = currentMonthStr();
        const finalMonth = target || cur;
        if (monthInput) monthInput.value = finalMonth;
        loadCostReport();
        // 仅在发生自动切换（最近有记录月 ≠ 当前月）时提示
        if (target && target !== cur) {
            showToast('该月暂无进货记录，已为你切换到最近有记录的月份：' + target, 'info');
        }
    })
    .catch(() => loadCostReport());
}

function loadCostReport() {
    const monthInput = document.getElementById('costReportMonth');
    const startInput = document.getElementById('costReportStartDate');
    const endInput = document.getElementById('costReportEndDate');
    const incCheck = document.getElementById('costReportIncNonApproved');

    const month = monthInput ? monthInput.value.trim() : '';
    const startDate = startInput ? startInput.value.trim() : '';
    const endDate = endInput ? endInput.value.trim() : '';
    const incNonApp = incCheck && incCheck.checked ? 1 : 0;

    if (startDate || endDate) {
        if (!startDate || !endDate) {
            showToast('开始日期与结束日期必须成对选择', 'warning');
            return;
        }
        if (startDate > endDate) {
            showToast('开始日期不能大于结束日期', 'warning');
            return;
        }
    }

    const params = new URLSearchParams();
    if (incNonApp) params.append('include_non_approved', '1');
    if (startDate && endDate) {
        params.append('start_date', startDate);
        params.append('end_date', endDate);
        if (month) params.append('month', month);
    } else {
        let m = month || currentMonthStr();
        if (monthInput) monthInput.value = m;
        params.append('month', m);
    }

    lastCostReportQuery = params.toString();
    const summary = document.getElementById('costReportSummary');

    fetch('/api/cost_report?' + lastCostReportQuery)
    .then(res => Promise.all([res.status, res.json().catch(() => null)]))
    .then(([httpStatus, ret]) => {
        if (!ret || ret.status !== 'success') {
            if (summary) summary.textContent = '查询失败：' + ((ret && ret.msg) || ('HTTP ' + httpStatus));
            return;
        }
        const d = ret.data || {};
        lastCostReportData = d;
        const body = document.getElementById('costReportBody');
        if (!body) return;
        body.innerHTML = '';

        const periodLabel = (d.period && d.period.label) ? d.period.label : (d.month || '');
        const depts = d.departments || [];
        const unalloc = d.unallocated || {};
        const unallocCount = Number(unalloc.count) || 0;

        if (depts.length === 0 && Number(d.month_total) === 0 && unallocCount === 0) {
            body.innerHTML = '<tr><td colspan="4" style="text-align:center; color:var(--text-muted); padding:28px;">这段时间还没有进货记录</td></tr>';
            if (summary) {
                summary.textContent = '时段 ' + periodLabel + '：暂无进货记录。';
            }
            return;
        }

        if (depts.length > 0) {
            depts.forEach(dep => {
                const active = Number(dep.active) === 1;
                const tr = document.createElement('tr');
                tr.style.cursor = 'pointer';
                tr.title = '点击查看构成该部门成本的单据与明细';
                tr.addEventListener('click', () => openCostDrilldown(dep.id, dep.name, periodLabel));

                const prevVal = Number(dep.prev_total) || 0;
                const prevText = '$' + fmtMoney(prevVal);
                let deltaHtml = '<span style="color:var(--text-muted);">$0.00</span>';
                if (dep.delta > 0) {
                    const tag = prevVal === 0 ? ' <span style="font-size:0.72rem; color:var(--primary); font-weight:normal;">新增</span>' : '';
                    deltaHtml = `<span style="color:#ef4444; font-weight:600;">+$${fmtMoney(dep.delta)}</span>${tag}`;
                } else if (dep.delta < 0) {
                    deltaHtml = `<span style="color:#10b981; font-weight:600;">-$${fmtMoney(Math.abs(dep.delta))}</span>`;
                }

                tr.innerHTML = `
                    <td>${w2Escape(dep.name)}${active ? '' : ' <span style="color:var(--text-muted); font-size:0.72rem;">已停用</span>'}</td>
                    <td class="col-right" style="font-weight:600;">$${fmtMoney(dep.total)}</td>
                    <td class="col-right" style="color:var(--text-muted);">${prevText}</td>
                    <td class="col-right">${deltaHtml}</td>`;
                body.appendChild(tr);
            });
        }

        // 未分配桶
        const unallocSubtext = unallocCount > 0
            ? `未分配`
            : '未分配';

        const unallocTr = document.createElement('tr');
        unallocTr.style.cursor = 'pointer';
        unallocTr.title = '未打部门的明细行成本';
        unallocTr.addEventListener('click', () => openCostDrilldown(null, '未分配', periodLabel));

        const unallocPrevVal = Number(unalloc.prev_total) || 0;
        const unallocPrevText = '$' + fmtMoney(unallocPrevVal);
        let unallocDeltaHtml = '<span style="color:var(--text-muted);">$0.00</span>';
        if (unalloc.delta > 0) {
            const tag = unallocPrevVal === 0 ? ' <span style="font-size:0.72rem; color:var(--primary); font-weight:normal;">新增</span>' : '';
            unallocDeltaHtml = `<span style="color:#ef4444; font-weight:600;">+$${fmtMoney(unalloc.delta)}</span>${tag}`;
        } else if (unalloc.delta < 0) {
            unallocDeltaHtml = `<span style="color:#10b981; font-weight:600;">-$${fmtMoney(Math.abs(unalloc.delta))}</span>`;
        }

        unallocTr.innerHTML = `
            <td style="color:var(--warning);">${w2Escape(unallocSubtext)}</td>
            <td class="col-right" style="font-weight:600; color:var(--warning);">$${fmtMoney(unalloc.total)}</td>
            <td class="col-right" style="color:var(--text-muted);">${unallocPrevText}</td>
            <td class="col-right">${unallocDeltaHtml}</td>`;
        body.appendChild(unallocTr);

        // 合计
        const totalTr = document.createElement('tr');
        totalTr.style.background = 'var(--bg-hover)';
        const totalPrevVal = Number(d.prev_total) || 0;
        const totalPrevText = '$' + fmtMoney(totalPrevVal);
        let totalDeltaHtml = '<span style="color:var(--text-muted);">$0.00</span>';
        if (d.delta > 0) {
            const tag = totalPrevVal === 0 ? ' <span style="font-size:0.72rem; color:var(--primary); font-weight:normal;">新增</span>' : '';
            totalDeltaHtml = `<span style="color:#ef4444; font-weight:600;">+$${fmtMoney(d.delta)}</span>${tag}`;
        } else if (d.delta < 0) {
            totalDeltaHtml = `<span style="color:#10b981; font-weight:600;">-$${fmtMoney(Math.abs(d.delta))}</span>`;
        }

        totalTr.innerHTML = `
            <td style="font-weight:700;">合计</td>
            <td class="col-right" style="font-weight:700; color:var(--primary);">$${fmtMoney(d.month_total)}</td>
            <td class="col-right" style="font-weight:700; color:var(--text-muted);">${totalPrevText}</td>
            <td class="col-right" style="font-weight:700;">${totalDeltaHtml}</td>`;
        body.appendChild(totalTr);

        if (summary) {
            summary.textContent = '时段 ' + periodLabel + '：进货总额 $' + fmtMoney(d.month_total);
        }
    })
    .catch(err => {
        console.error('加载报表失败', err);
        if (summary) summary.textContent = '加载报表失败，请稍后重试';
    });
}

function openCostDrilldown(deptId, deptName, displayPeriod) {
    const periodStr = displayPeriod || (lastCostReportData && lastCostReportData.period ? lastCostReportData.period.label : currentMonthStr());
    const title = document.getElementById('costDrilldownTitle');
    if (title) title.textContent = (deptName || '未分配') + ' · ' + periodStr + ' · 花销明细';

    const notice = document.getElementById('costDrilldownNotice');
    if (notice) {
        if (deptId == null) {
            notice.style.display = 'block';
            notice.textContent = '提示：点击单据号可打开原单据详情。在原单里选择部门并保存后，点击下方「刷新报表」即可更新。';
        } else {
            notice.style.display = 'none';
        }
    }

    let url = '/api/cost_report/items?' + lastCostReportQuery;
    if (deptId != null) {
        url += '&department_id=' + encodeURIComponent(deptId);
    } else {
        // 未分配下钻：显式传 0，让后端按未归属过滤（部门分摊空的精准回退）
        url += '&department_id=0';
    }
    const body = document.getElementById('costDrilldownBody');
    if (body) body.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--text-muted); padding:24px;">加载中…</td></tr>';

    fetch(url)
    .then(res => Promise.all([res.status, res.json().catch(() => null)]))
    .then(([httpStatus, ret]) => {
        if (body) body.innerHTML = '';
        if (!ret || ret.status !== 'success') {
            if (body) {
                body.innerHTML = '<tr><td colspan="5" style="text-align:center; color:#f87171; padding:24px;">'
                    + w2Escape((ret && ret.msg) || ('HTTP ' + httpStatus)) + '</td></tr>';
            }
            showModalAndScroll();
            return;
        }
        const rows = ret.data || [];
        if (rows.length === 0) {
            if (body) body.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--text-muted); padding:24px;">这段时间该部门还没有进货记录</td></tr>';
            showModalAndScroll();
            return;
        }

        // 按供应商分组 (Option A)
        const groups = {};
        rows.forEach(r => {
            const supp = (r.supplier_name || '').trim() || '未填写供应商';
            if (!groups[supp]) groups[supp] = [];
            groups[supp].push(r);
        });

        Object.keys(groups).forEach(suppName => {
            const groupItems = groups[suppName];
            const subtotal = groupItems.reduce((sum, item) => sum + Number(item.amount || 0), 0);

            const headerTr = document.createElement('tr');
            headerTr.style.background = 'var(--bg-hover, #f8fafc)';
            headerTr.style.fontWeight = '600';
            headerTr.innerHTML = `
                <td colspan="5" style="color:var(--primary); padding:8px 12px;">
                    供应商：${w2Escape(suppName)} · 小计 $${fmtMoney(subtotal)} · ${groupItems.length} 条
                </td>`;
            body.appendChild(headerTr);

            groupItems.forEach(r => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td>${renderDateCell(r.receipt_date)}</td>
                    <td>
                        <button class="btn-link" style="background:none; border:none; color:var(--primary); cursor:pointer; text-decoration:underline; padding:0; font-family:inherit; font-size:inherit;" onclick="openReceiptFromDrilldown(${Number(r.receipt_id)})">
                            #${Number(r.receipt_id)}
                        </button>
                    </td>
                    <td>${w2Escape(r.raw_name || '-')}</td>
                    <td>${Number(r.quantity)}</td>
                    <td class="col-right">$${fmtMoney(r.amount)}</td>`;
                body.appendChild(tr);
            });
        });
        showModalAndScroll();
    })
    .catch(err => {
        console.error('加载花销明细失败', err);
        if (body) {
            body.innerHTML = '<tr><td colspan="5" style="text-align:center; color:#f87171; padding:24px;">加载明细失败，请稍后重试</td></tr>';
        }
        showModalAndScroll();
    });

    function showModalAndScroll() {
        const modal = document.getElementById('costDrilldownModal');
        if (modal) modal.classList.remove('hide');
    }
}

function openReceiptFromDrilldown(receiptId) {
    closeModalById('costDrilldownModal');
    loadReceiptDetail(receiptId);
}

function exportCostReportCSV() {
    if (!lastCostReportData || !lastCostReportData.departments) {
        showToast('暂无报表数据可导出', 'warning');
        return;
    }
    const d = lastCostReportData;
    const label = (d.period && d.period.label) ? d.period.label : (d.month || 'cost_report');
    let csvContent = '\uFEFF';
    csvContent += '部门,本期进货,上期对照,变化\n';

    (d.departments || []).forEach(dep => {
        const name = dep.name + (Number(dep.active) === 1 ? '' : ' 已停用');
        const prev = Number(dep.prev_total || 0).toFixed(2);
        const deltaVal = Number(dep.delta) || 0;
        const deltaStr = (deltaVal >= 0 ? '+' : '') + deltaVal.toFixed(2);
        csvContent += `"${name.replace(/"/g, '""')}",${Number(dep.total || 0).toFixed(2)},${prev},${deltaStr}\n`;
    });

    const unalloc = d.unallocated || {};
    const unName = '未分配';
    const unPrev = Number(unalloc.prev_total || 0).toFixed(2);
    const unDeltaVal = Number(unalloc.delta) || 0;
    const unDeltaStr = (unDeltaVal >= 0 ? '+' : '') + unDeltaVal.toFixed(2);
    csvContent += `"${unName}",${(Number(unalloc.total) || 0).toFixed(2)},${unPrev},${unDeltaStr}\n`;

    const totPrev = Number(d.prev_total || 0).toFixed(2);
    const totDeltaVal = Number(d.delta) || 0;
    const totDeltaStr = (totDeltaVal >= 0 ? '+' : '') + totDeltaVal.toFixed(2);
    csvContent += `"合计",${(Number(d.month_total) || 0).toFixed(2)},${totPrev},${totDeltaStr}\n`;

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.setAttribute('href', url);
    link.setAttribute('download', `部门花销报表_${label}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
}

function initDeptAdminDetails() {
    const details = document.getElementById('deptAdminDetails');
    if (!details) return;
    if (localStorage.getItem('dept_admin_open') === '1') {
        details.open = true;
    }
    details.addEventListener('toggle', () => {
        localStorage.setItem('dept_admin_open', details.open ? '1' : '0');
    });
}

 document.addEventListener('DOMContentLoaded', () => {
     initDemoRoleSwitch();       // demo：无密码 RBAC 角色下拉
     probeAuthAndEnter(false);   // P1-12: 启动鉴权探测（401 → 登录面板；AUTH_ENABLED=0 直通）
    initTabs();
    initUpload();
    restoreBatchFromManifest(); // 刷新浏览器不丢失解析进度（从 manifest 恢复单据及解析状态）
    initFocalZoomAndCrop();
    initAutoAnalyzeSetting();
    updateGlobalDatalistUnits();
    loadInventoryData();
    loadSuppliersData();
    loadReceiptsHistory();
    loadDepartmentsAll();        // Wave 2（D44）：部门下拉/管理/报表共用数据源
    loadFinancePanel();         // Wave 1：首屏即拉付款域，供导航红点/顶部横幅提醒
    initDeptAdminDetails();
    // F-P1-3 多币种：币种切换联动金额符号渲染
    ['inpCurrency', 'arcCurrency'].forEach(id => {
        const sel = document.getElementById(id);
        if (sel) sel.addEventListener('change', renderCurrencySymbol);
    });
    renderCurrencySymbol();

});

// -------------------------------------------------------------
// 1. Tab 切换与自动设置
// -------------------------------------------------------------
function initTabs() {
    const sidebarBtns = document.querySelectorAll('.sidebar-btn, .nav-tab');
    sidebarBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            sidebarBtns.forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));

            btn.classList.add('active');
            const targetId = btn.getAttribute('data-target');
            const navTitle = btn.getAttribute('data-title') || btn.innerText;

            document.getElementById(targetId).classList.add('active');

            const bcTitle = document.getElementById('bcActiveTitle');
            if (bcTitle) bcTitle.innerText = navTitle;

            if (targetId === 'tab-inventory') loadInventoryData();
            if (targetId === 'tab-archive') {
                loadSuppliersData();
                loadReceiptsHistory();
                loadFinancePanel();      // Wave 2 支付与对账
                loadSupplierAdmin();     // Wave 2 供应商管理（含合并）

            }
            if (targetId === 'tab-report') {
                // U-07：首次进入默认查最近有已入库单据的月份，避免空态误导
                loadCostReportAutoMonth();
                loadDepartmentAdmin();   // Wave 2 部门管理
            }
            if (targetId === 'tab-engine') loadAdminEngineConfig();
            if (targetId === 'tab-golden') {
                loadGoldenBoard();       // E-P1-2 黄金样本 57 看板
                loadPValueCards();       // E-P1-3 p-value 显著性卡片
            }
        });
    });
}

function initAutoAnalyzeSetting() {
    const chkAuto = document.getElementById('chkAutoAnalyze');
    const savedAuto = localStorage.getItem('auto_analyze_receipt');
    if (savedAuto !== null) {
        chkAuto.checked = (savedAuto === 'true');
    }
    chkAuto.addEventListener('change', () => {
        localStorage.setItem('auto_analyze_receipt', chkAuto.checked);
    });
}

// -------------------------------------------------------------
// 2. 收据图片选择与本地实时预览
// -------------------------------------------------------------

/** U-12：大图/HEIC 准备中轻量指示器（非阻断式） */
function showImagePrepIndicator(show, text = '正在准备照片，请稍候...') {
    const el1 = document.getElementById('imagePrepIndicator');
    const txt1 = document.getElementById('imagePrepText');
    if (el1) {
        if (show) {
            if (txt1) txt1.textContent = text;
            el1.classList.remove('hide');
        } else {
            el1.classList.add('hide');
        }
    }
    const el2 = document.getElementById('imgViewerPrepIndicator');
    const txt2 = document.getElementById('imgViewerPrepText');
    if (el2) {
        if (show) {
            if (txt2) txt2.textContent = text;
            el2.classList.remove('hide');
        } else {
            el2.classList.add('hide');
        }
    }
}

/** U-12：文件选择/拖拽事件防抖与并发控制（150ms 窗口） */
function queueFilesSelect(files) {
    if (!files || files.length === 0) return;
    const currentGen = nextFileSelectionGen();

    // 若包含大图或 HEIC，立即给予视觉反馈，无需等待 150ms debounce 窗口结束
    const hasLargeOrHeic = files.some(f => isNonWebImageFile(f) || (f && f.size > 1.5 * 1024 * 1024));
    if (hasLargeOrHeic) {
        showImagePrepIndicator(true, '正在准备照片，请稍候...');
    }

    if (_fileSelectDebounceTimer) {
        clearTimeout(_fileSelectDebounceTimer);
        _fileSelectDebounceTimer = null;
    }

    _fileSelectDebounceTimer = setTimeout(() => {
        _fileSelectDebounceTimer = null;
        if (currentGen !== fileSelectionGen) return;
        handleFilesSelect(files, currentGen);
    }, 150);
}

function initUpload() {
    const fileInput = document.getElementById('receiptFile');
    const uploadArea = document.getElementById('uploadArea');
    const previewImg = document.getElementById('previewImg');

    // 预览加载失败时：若当前是 HEIC 等非 Web 格式，立刻恢复常驻提示（避免黑屏「收据原图」）
    if (previewImg) {
        previewImg.addEventListener('error', () => {
            const photo = (typeof BatchUploader !== 'undefined') ? getActivePhoto() : null;
            const file = (photo && photo.file) || selectedFile || originalFile;
            if (isNonWebImageFile(file)) {
                // 若已有可显示的服务端 JPEG，勿覆盖；仅当仍无 web 图时回落提示
                if (photo && photo.imageUrl && isBrowserDisplayableImageUrl(photo.imageUrl)) {
                    return;
                }
                setMainPreviewFromFile(file, null);
            }
        });
    }

    if (fileInput) {
        fileInput.addEventListener('change', () => {
            // 取消选择（无文件）时复位替换标记，避免污染后续普通添加
            if (fileInput.files.length === 0) { _reselectReplaceMode = false; return; }
            if (_reselectReplaceMode) {
                // 先移除当前正在预览的照片，再以新选照片替换（而非追加）
                const idx = BatchUploader.activeIndex;
                const photo = BatchUploader.photos[idx];
                if (photo) removePhotoFromSider(idx);
                _reselectReplaceMode = false;
            }
            const files = Array.from(fileInput.files);
            fileInput.value = '';
            queueFilesSelect(files);
        });
    }

    if (uploadArea) {
        uploadArea.addEventListener('dragover', (e) => {
            e.preventDefault();
            uploadArea.style.borderColor = '#3b82f6';
            uploadArea.classList.add('drag-over');
        });
        uploadArea.addEventListener('dragleave', () => {
            uploadArea.style.borderColor = '#475569';
            uploadArea.classList.remove('drag-over');
        });
        uploadArea.addEventListener('drop', (e) => {
            e.preventDefault();
            uploadArea.style.borderColor = '#475569';
            uploadArea.classList.remove('drag-over');
            if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                queueFilesSelect(Array.from(e.dataTransfer.files));
            }
        });
    }
}

// -------------------------------------------------------------
// HEIC / TIFF 自动即时转码为 JPEG 辅助函数
// -------------------------------------------------------------
async function ensureWebDisplayableImageFile(file, gen) {
    if (!file || !isNonWebImageFile(file)) {
        return file;
    }
    const currentGen = (typeof gen === 'number') ? gen : fileSelectionGen;
    showToast(`检测到 ${escapeHtml(file.name)} 为 HEIC/非标准格式，正在自动转为 JPEG...`, 'info');
    try {
        const formData = new FormData();
        formData.append('file', file);
        const res = await fetch('/api/convert-image', {
            method: 'POST',
            body: formData,
        });
        if (currentGen !== fileSelectionGen) {
            return null;
        }
        if (!res.ok) {
            console.warn('转码接口异常，降级保留原文件');
            return file;
        }
        const blob = await res.blob();
        if (currentGen !== fileSelectionGen) {
            return null;
        }
        const baseName = file.name.replace(/\.(heic|heif|tif|tiff)$/i, '') || 'receipt';
        const convertedFile = new File([blob], `${baseName}.jpg`, {
            type: 'image/jpeg',
            lastModified: Date.now()
        });
        convertedFile._originalName = file.name;
        convertedFile._convertedFromHeic = true;
        if (currentGen !== fileSelectionGen) {
            return null;
        }
        showToast(`${escapeHtml(file.name)} 已自动转为 JPEG，可直接预览、旋转与裁剪`, 'success');
        return convertedFile;
    } catch (err) {
        if (currentGen !== fileSelectionGen) {
            return null;
        }
        console.error('自动转码失败:', err);
        return file;
    }
}

async function handleFileSelect(file, gen) {
    const currentGen = (typeof gen === 'number') ? gen : nextFileSelectionGen();

    const isLargeOrHeic = file && (isNonWebImageFile(file) || file.size > 1.5 * 1024 * 1024);
    if (isLargeOrHeic) {
        showImagePrepIndicator(true, '正在准备照片，请稍候...');
    }

    try {
        // 自动将 HEIC/HEIF/TIFF 转码为 JPEG 便于预览和调整
        if (isNonWebImageFile(file)) {
            file = await ensureWebDisplayableImageFile(file, currentGen);
            if (!file || currentGen !== fileSelectionGen) return;
        }

        // D12：选择真实照片上传 → 退出新建手工单态（恢复左栏原图区/标题徽章）
        resetManualEntryMode();

        // D21 质量预检：亮度/模糊度本地预检（温和提示，不阻断）
        const quality = await checkImageQuality(file, currentGen);
        if (currentGen !== fileSelectionGen) return;

        let userForce = false;
        let qualityWarn = false;
        if (!quality.ok) {
            qualityWarn = true;
        }

        originalFile = file;
        selectedFile = file;
        window._selectedFileForce = false;

        // 释放先前分配的 ObjectURL，防止内存泄漏
        if (Array.isArray(BatchUploader.photos)) {
            BatchUploader.photos.forEach(p => {
                if (p && p.objectUrl) {
                    try { URL.revokeObjectURL(p.objectUrl); } catch (e) {}
                    p.objectUrl = null;
                }
                if (p && p.croppedObjectUrl) {
                    try { URL.revokeObjectURL(p.croppedObjectUrl); } catch (e) {}
                    p.croppedObjectUrl = null;
                }
            });
        }

        if (originalObjectUrl) {
            try { URL.revokeObjectURL(originalObjectUrl); } catch (e) {}
            originalObjectUrl = null;
        }

        const newObjUrl = isNonWebImageFile(file) ? null : URL.createObjectURL(file);
        originalObjectUrl = newObjUrl;

        // 将单张照片也登记进“本批照片”（长度 1）
        const photo = {
            localId: `local-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
            file,
            originalFile: file,
            force: false,
            objectUrl: newObjUrl,
            croppedObjectUrl: null,
            cropped: false,
            status: 'pending',
            receiptId: null,
            imageUrl: null,
            data: null,
            errorMsg: null,
            qualityWarnings: quality.reasons || [],
        };
        BatchUploader.photos = [photo];
        BatchUploader.activeIndex = 0;
        BatchUploader.selected.clear();
        const countEl = document.getElementById('siderToggleCount');
        if (countEl) countEl.innerText = '1';
        updateSiderVisibility();
        renderSider();

        // 统一走 setActivePhoto 预览
        setActivePhoto(0);
        resetImgTransform();

        const splitViewArea = document.getElementById('splitViewArea');
        if (splitViewArea) splitViewArea.classList.remove('hide');

        // 温和画质提示
        const preConfirmWarn = document.getElementById('preConfirmQualityWarn');
        if (preConfirmWarn) {
            if (qualityWarn && quality.reasons && quality.reasons.length > 0) {
                preConfirmWarn.textContent = `画质提示：检测到该照片${quality.reasons.join('、')}，已自动加载就绪，点击「继续 AI 智能解析」将由模型尽力识别。`;
                preConfirmWarn.classList.remove('hide');
            } else {
                preConfirmWarn.classList.add('hide');
            }
        }

        const autoAnalyze = document.getElementById('chkAutoAnalyze')?.checked;
        if (autoAnalyze) {
            triggerAnalysisNow(false);
        }
    } finally {
        if (currentGen === fileSelectionGen) {
            showImagePrepIndicator(false);
        }
    }
}

async function handleSingleUploadSelection(file, gen) {
    return await handleFileSelect(file, gen);
}

// -------------------------------------------------------------
// 2b. 质量预检 (D21 客户端)：canvas 缩样计算亮度均值与模糊度
//     亮度 = 灰度均值；模糊度 = 拉普拉斯方差近似（越小越模糊）
//     阈值从宽：仅提示，用户确认后可强制上传，绝不阻断
// -------------------------------------------------------------
function checkImageQuality(file, gen) {
    return new Promise((resolve) => {
        const currentGen = (typeof gen === 'number') ? gen : fileSelectionGen;
        if (currentGen !== fileSelectionGen) {
            resolve({ ok: true, reasons: [], obsolete: true });
            return;
        }
        if (!file || !file.name) {
            resolve({ ok: true, reasons: [] });
            return;
        }
        // HEIC/TIFF 等浏览器 canvas 无法解码，跳过客户端预检（服务端 S1 仍会复核）
        if (isNonWebImageFile(file)) {
            resolve({ ok: true, reasons: [] });
            return;
        }
        const url = URL.createObjectURL(file);
        const img = new Image();
        img.onload = () => {
            try {
                if (currentGen !== fileSelectionGen) {
                    resolve({ ok: true, reasons: [], obsolete: true });
                    return;
                }
                // 缩样到长边 256px，够算亮度/模糊度且几乎零耗时
                const maxSide = 256;
                const scale = Math.min(1, maxSide / Math.max(img.naturalWidth || 1, img.naturalHeight || 1));
                const w = Math.max(2, Math.round((img.naturalWidth || 0) * scale));
                const h = Math.max(2, Math.round((img.naturalHeight || 0) * scale));
                const canvas = document.createElement('canvas');
                canvas.width = w;
                canvas.height = h;
                const ctx = canvas.getContext('2d');
                ctx.drawImage(img, 0, 0, w, h);
                const pixelData = ctx.getImageData(0, 0, w, h).data;

                // 灰度化并计算亮度均值
                const gray = new Float32Array(w * h);
                let sum = 0;
                for (let i = 0; i < w * h; i++) {
                    const v = 0.299 * pixelData[i * 4] + 0.587 * pixelData[i * 4 + 1] + 0.114 * pixelData[i * 4 + 2];
                    gray[i] = v;
                    sum += v;
                }
                const brightness = sum / (w * h);

                // 拉普拉斯方差近似：方差越小越模糊
                let lapSum = 0;
                let lapSqSum = 0;
                let n = 0;
                for (let y = 1; y < h - 1; y++) {
                    for (let x = 1; x < w - 1; x++) {
                        const i = y * w + x;
                        const lap = -4 * gray[i] + gray[i - 1] + gray[i + 1] + gray[i - w] + gray[i + w];
                        lapSum += lap;
                        lapSqSum += lap * lap;
                        n++;
                    }
                }
                const lapMean = n ? lapSum / n : 0;
                const sharpness = n ? Math.max(0, lapSqSum / n - lapMean * lapMean) : 0;

                const reasons = [];
                if (brightness < QUALITY_BRIGHTNESS_MIN) reasons.push('可能过暗');
                if (sharpness < QUALITY_SHARPNESS_MIN) reasons.push('疑似模糊');
                resolve({ ok: reasons.length === 0, reasons, brightness, sharpness });
            } catch (e) {
                // 预检失败不阻断上传
                resolve({ ok: true, reasons: [] });
            } finally {
                URL.revokeObjectURL(url);
            }
        };
        img.onerror = () => {
            URL.revokeObjectURL(url);
            resolve({ ok: true, reasons: [] });
        };
        img.src = url;
    });
}

let ocrTimerInterval = null;
let ocrStartTime = 0;

function updateLoadingStage(pct, stageName, stepNum) {
    const bar = document.getElementById('loadingProgressBar');
    if (bar) bar.style.width = pct + '%';
    const stageNameEl = document.getElementById('loadingStageName');
    if (stageNameEl) stageNameEl.textContent = stageName;
    for (let i = 1; i <= 4; i++) {
        const stepEl = document.getElementById('loadingStep' + i);
        if (stepEl) {
            if (i < stepNum) {
                stepEl.className = 'loading-step-item done';
            } else if (i === stepNum) {
                stepEl.className = 'loading-step-item active';
            } else {
                stepEl.className = 'loading-step-item';
            }
        }
    }
}

function startOcrTimer() {
    stopOcrTimer();
    ocrStartTime = Date.now();
    const timerElem = document.getElementById('ocrTimer');
    if (timerElem) timerElem.innerText = '00:00.0';
    updateLoadingStage(25, '视觉语义读取中 (25%)...', 1);

    ocrTimerInterval = setInterval(() => {
        const elapsedMs = Date.now() - ocrStartTime;
        const totalSec = Math.floor(elapsedMs / 1000);
        const mins = Math.floor(totalSec / 60).toString().padStart(2, '0');
        const secs = (totalSec % 60).toString().padStart(2, '0');
        const tenths = Math.floor((elapsedMs % 1000) / 100);
        if (timerElem) {
            timerElem.innerText = `${mins}:${secs}.${tenths}`;
        }

        // 4 阶段清晰感知过渡与长等待预期提示
        if (elapsedMs < 1200) {
            updateLoadingStage(25, '视觉语义读取中 (25%)...', 1);
        } else if (elapsedMs < 2800) {
            updateLoadingStage(50, '契约提取与字段归一中 (50%)...', 2);
        } else if (elapsedMs < 4500) {
            updateLoadingStage(75, '算术守恒与异常自检中 (75%)...', 3);
        } else if (elapsedMs < 25000) {
            updateLoadingStage(95, 'SKU智能匹配中 (95%)...', 4);
        } else {
            // U-8: 耗时 >= 25s 时的长等待预期提示
            updateLoadingStage(95, '本单明细较多，AI 正在加紧核对，预计还需 10 秒…', 4);
        }
    }, 100);
}

function stopOcrTimer() {
    if (ocrTimerInterval) {
        clearInterval(ocrTimerInterval);
        ocrTimerInterval = null;
    }
    updateLoadingStage(100, '识别完成 (100%)', 4);
}

// -------------------------------------------------------------
// Wave 3 异步 Job（T9/D28）：上传 async=true 即返 job_id，
// 前端轮询 GET /api/job/{id} 直至 done/error（loading timer 保留）
// Q28（R4）：轮询加总超时（JOB_POLL_TIMEOUT_MS）+ 取消令牌
// （删照片/新上传替换时作废），超时/取消均显式 settle 不悬挂界面。
// -------------------------------------------------------------
const JOB_POLL_INTERVAL_MS = 800;
const JOB_POLL_TIMEOUT_MS = 5 * 60 * 1000;   // 5 分钟总超时

// 纯函数：本轮轮询判定——cancelled（令牌作废）/ timeout（超总时长）/ poll（继续）
function jobPollVerdict(token, startTs, nowMs, timeoutMs) {
    if (token && token.cancelled) return 'cancelled';
    if (timeoutMs > 0 && (nowMs - startTs) >= timeoutMs) return 'timeout';
    return 'poll';
}

// opts（可选）：{ token, timeoutMs, intervalMs }
//   token = {cancelled:false} 取消令牌（照片移除/被新上传替换时置 cancelled）
function pollReceiptJob(jobId, onSettled, opts) {
    const token = opts && opts.token;
    const timeoutMs = (opts && opts.timeoutMs != null) ? opts.timeoutMs : JOB_POLL_TIMEOUT_MS;
    const intervalMs = (opts && opts.intervalMs != null) ? opts.intervalMs : JOB_POLL_INTERVAL_MS;
    const startTs = Date.now();

    (function tick() {
        const verdict = jobPollVerdict(token, startTs, Date.now(), timeoutMs);
        if (verdict === 'cancelled') {
            onSettled({ status: 'cancelled', msg: '轮询已取消' });
            return;
        }
        if (verdict === 'timeout') {
            onSettled({ status: 'error',
                msg: '识别任务轮询超时：超过设定时限仍未完成，已停止等待。请重试或检查后端服务状态。' });
            return;
        }
        fetch(`/api/job/${jobId}`)
            .then(res => res.json())
            .then(job => {
                if (job && job.job_status === 'done') {
                    // done → result 与同步 upload 成功响应同构
                    const result = job.result || { status: 'error', msg: '识别结果为空' };
                    // 顶层 image_url 兜底（部分路径 result 可能缺字段）
                    if (!result.image_url && job.image_url) result.image_url = job.image_url;
                    onSettled(result);
                } else if (job && job.job_status === 'error') {
                    onSettled({
                        status: 'error',
                        msg: job.error_msg || '识别失败',
                        receipt_id: job.receipt_id,
                        image_url: job.image_url || null,
                    });
                } else if (job && job.status === 'error') {
                    // HTTP 级错误（如 404 任务不存在）
                    onSettled({ status: 'error', msg: job.msg || '查询识别任务失败' });
                } else {
                    // queued / running → 若已有转码后的 image_url，提前通知（HEIC 预览）
                    if (job && job.image_url && typeof opts?.onProgress === 'function') {
                        opts.onProgress(job);
                    }
                    setTimeout(tick, intervalMs);
                }
            })
            .catch(err => {
                console.error('轮询任务状态异常', err);
                onSettled({ status: 'error', msg: '查询识别进度失败，请稍后重试' });
            });
    })();
}

/** 服务端已返回可预览 image_url（如 HEIC→JPEG）时刷新主图与 sider 缩略图 */
function applyServerPreviewIfAny(photo, imageUrl) {
    if (!photo || !imageUrl) return;
    // 仍是 heic 路径则不切换主图，保持「浏览器无法直接预览」提示
    if (!isBrowserDisplayableImageUrl(imageUrl)) return;
    photo.imageUrl = imageUrl;
    renderSider();
    const curIdx = findPhotoIndex(photo);
    if (curIdx >= 0 && BatchUploader.activeIndex === curIdx) {
        setMainPreview(photo);
    }
}

// -------------------------------------------------------------
// R4 共享纯函数（供 triggerAnalysisNow/批量路径复用，
// tests/frontend/r4_logic_test.js 直接断言）
// -------------------------------------------------------------

// P1-16：retry 路由前校验——仅当 selectedFile 仍是该 error 照片自己的文件
// （含裁剪后 file：裁剪时 photo.file 与 selectedFile 同步替换）才允许复用原单重试，
// 否则说明用户已换新文件 → 走新上传，防止误重试旧错误单吞掉新文件。
function shouldRetryPhoto(currentFile, photo) {
    if (!photo || photo.status !== 'error' || !photo.receiptId) return false;
    if (!currentFile || !photo.file) return false;
    return currentFile === photo.file;
}

// P1-13：识别/保存/审批成功后从响应提取新版本号并回写到 data（便于连续保存/审批携带）。
// 契约（Track A）：upload/job/batch/retry/convert/save/approve 响应带 version；
// 后端未返回时返回 null 不副作用。
function captureResultVersion(ret) {
    const v = extractVersionFromResponse(ret);
    if (v == null) return null;
    const d = ret && ret.data;
    if (d && typeof d === 'object') d.version = v;
    return v;
}

// Q29：quality_warnings 收集——优先响应顶层（后端已并入 duplicate_warning），
// 退回 data 内（ai_prefill 自带）
function collectQualityWarnings(ret) {
    if (ret && Array.isArray(ret.quality_warnings)) return ret.quality_warnings;
    if (ret && ret.data && Array.isArray(ret.data.quality_warnings)) return ret.data.quality_warnings;
    return [];
}

// F-P1-4 弱光/模糊重拍引导：按警告类型给出可执行的重拍建议（纯文本，无 Emoji）
// 最严格人话：含“图像模糊/过暗，请到更亮处重拍，无需打字，点框选裁剪重试”
const RESHOOT_GUIDE = {
    dark: [
        '图像模糊/过暗，请到更亮处重拍，无需打字，点框选裁剪重试',
        '光线不足：请在明亮环境下拍摄，单据平放避免阴影遮挡',
        '打开手机闪光灯或移至灯光正下方后重新拍摄',
    ],
    blur: [
        '图像模糊/过暗，请到更亮处重拍，无需打字，点框选裁剪重试',
        '画面模糊：请持稳手机，对焦清楚后再拍',
        '尽量让单据充满取景框，避免远距离拍摄',
    ],
    small_or_corrupted: [
        '图像模糊/过暗，请到更亮处重拍，无需打字，点框选裁剪重试',
        '图片损坏或体积过小：请用相机重新拍摄原图，勿发送压缩图',
    ],
};

// 纯函数：警告文本 → 重拍引导条目（供 node vm 逻辑测断言）
function buildReshootGuideItems(warnings) {
    const list = (Array.isArray(warnings) ? warnings : []).map(w => String(w || '').toLowerCase());
    const items = [];
    if (list.some(w => w.includes('过暗') || w.includes('dark') || w.includes('low_light') || w.includes('弱光'))) {
        items.push(...RESHOOT_GUIDE.dark);
    }
    if (list.some(w => w.includes('模糊') || w.includes('blur'))) {
        items.push(...RESHOOT_GUIDE.blur);
    }
    if (list.some(w => w.includes('损坏') || w.includes('corrupt') || w.includes('empty') || w.includes('过小'))) {
        items.push(...RESHOOT_GUIDE.small_or_corrupted);
    }
    return Array.from(new Set(items));
}

// Q29：质量预检警告渲染（多条可见；一律 textContent，无插值注入面）
// P0-1 极模糊置顶：image_blur → 中文"图像模糊度过高"，并保证在错误卡片中也置顶可见
// 最严格文案：含“图像模糊/过暗，请到更亮处重拍，无需打字，点框选裁剪重试” 无 Emoji
const QUALITY_WARNING_LABELS = {
    'image_blur': '图像模糊/过暗，请到更亮处重拍，无需打字，点框选裁剪重试',
    'image_empty_or_corrupted': '图像模糊/过暗，请到更亮处重拍，无需打字，点框选裁剪重试（图片损坏或体积过小）',
    'duplicate': '疑似重复上传',
};

// F-P1-4：命中弱光/模糊时附加重拍引导（最严格人话，无 Emoji）
function showQualityWarnings(warnings) {
    const banner = document.getElementById('qualityWarningsBanner');
    if (!banner) return;
    // 清理旧克隆（错误态置顶克隆）
    const oldClone = document.getElementById('qualityWarningsBannerClone');
    if (oldClone && oldClone.parentNode) oldClone.parentNode.removeChild(oldClone);
    const list = Array.isArray(warnings)
        ? warnings.filter(w => w != null && String(w).trim() !== '')
        : [];
    banner.innerHTML = '';
    if (list.length === 0) {
        banner.classList.add('hide');
        // 若曾置顶克隆也隐藏
        return;
    }
    const msgs = list.map(w => {
        const raw = String(w);
        const label = QUALITY_WARNING_LABELS[raw] || raw;
        return (raw === 'image_blur' && !label.includes('模糊')) ? QUALITY_WARNING_LABELS['image_blur'] : label;
    });
    // 最严格：自动附加重拍引导，确保“重拍/更亮处/框选裁剪”人话必现
    const guideItems = buildReshootGuideItems(list);
    // 若原始 warnings 未触发 guide 但包含 blur 语义，强制附加最严格文案
    const hasStrictPhrase = msgs.some(m => m.includes('重拍')) || guideItems.some(g => g.includes('重拍'));
    const strictGuide = hasStrictPhrase ? [] : ['图像模糊/过暗，请到更亮处重拍，无需打字，点框选裁剪重试'];
    const extraGuides = guideItems.concat(strictGuide);
    const allParts = extraGuides.length ? msgs.concat(['—— ' + extraGuides.join('；')]) : msgs;
    banner.innerHTML = `<span style="font-weight:600;">提示：</span>${allParts.join(' · ')}`;
    banner.classList.remove('hide');
    // P0-1 置顶保证：若 banner 所在 prefillFormCard 隐藏（错误态），克隆一份到 rightPanel 顶部置顶
    const prefill = document.getElementById('prefillFormCard');
    const rightPanel = document.getElementById('rightPanel');
    if (prefill && prefill.classList.contains('hide') && rightPanel) {
        const clone = banner.cloneNode(true);
        clone.id = 'qualityWarningsBannerClone';
        // 保持与原 banner 相同样式，已含 border/background
        clone.classList.remove('hide');
        // 置顶插入到 rightPanel 首位（最顶部）
        if (rightPanel.firstChild) rightPanel.insertBefore(clone, rightPanel.firstChild);
        else rightPanel.appendChild(clone);
    }
    // 成功态若 banner 曾被克隆到顶部且当前 prefill 可见，移除克隆避免重复
    if (prefill && !prefill.classList.contains('hide')) {
        const c = document.getElementById('qualityWarningsBannerClone');
        if (c && c.parentNode) c.parentNode.removeChild(c);
    }
}

// Q34：上传响应带 duplicate_of → 给可点击关联动作（toast + 确认「查看原单」）
function offerDuplicateAction(ret) {
    if (!ret || ret.duplicate_of == null) return;
    const dupId = Number(ret.duplicate_of);
    if (!dupId) return;
    const hint = ret.duplicate_warning || `该图片疑似与单据 #${dupId} 重复`;
    showToast(hint.trim(), 'info', TOAST_DURATION.long);
    const go = confirm(`${hint}\n\n点「确定」查看原单据 #${dupId} 详情，点「取消」继续当前处理。`);
    if (go) loadReceiptDetail(dupId);
}

// P1-14：批次定位一律按 photo 对象引用 + localId 校验（不用数组索引），
// 解析中删照片不再导致索引漂移写错对象
function isPhotoInBatch(photo, photosArr) {
    const arr = photosArr || BatchUploader.photos;
    return !!(photo && photo.localId && arr && arr.some(p => p.localId === photo.localId));
}
function findPhotoIndex(photo, photosArr) {
    const arr = photosArr || BatchUploader.photos;
    if (!photo || !photo.localId || !arr) return -1;
    return arr.findIndex(p => p.localId === photo.localId);
}

// 单张上传代际标记与轮询令牌（Q28）：新上传开始时作废旧轮询、旧结果丢弃
let singleUploadGen = 0;
let singlePollToken = null;

function triggerAnalysisNow(forceFlag = false) {
    if (!selectedFile) {
        showToast('请先选择一张收据图片', 'warning');
        return;
    }

    // D13: 若当前是已有单据行的失败照片（批量/重试场景），复用原单据走 retry 端点，不新建记录
    // P1-16: 路由前校验 selectedFile 仍是该照片的文件（含裁剪后 file）；
    // 用户已换新文件时不走 retry，落入下方新上传，避免误重试旧错误单吞新文件
    const activePhoto = getActivePhoto();
    if (shouldRetryPhoto(selectedFile, activePhoto)) {
        retryReceiptRecognition(activePhoto.receiptId);
        return;
    }

    const isForce = forceFlag === true || (activePhoto && activePhoto.force) || !!window._selectedFileForce;

    const formData = new FormData();
    formData.append('receipt', selectedFile);
    formData.append('force', isForce ? 'true' : 'false');

    const useCodebuddy = document.getElementById('chkCodebuddy').checked;

    document.getElementById('preConfirmCard').classList.add('hide');
    document.getElementById('errorCard').classList.add('hide');
    document.getElementById('loadingCard').classList.remove('hide');
    document.getElementById('prefillFormCard').classList.add('hide');
    startOcrTimer();

    // Q28: 代际标记 + 轮询令牌——被新上传替换后旧轮询作废、旧结果丢弃
    const gen = ++singleUploadGen;
    if (singlePollToken) singlePollToken.cancelled = true;
    singlePollToken = { cancelled: false };

    // Wave 3（T9）：async=true 立即返回 job_id，再轮询 /api/job/{id}
    fetch(`/api/upload?codebuddy=${useCodebuddy}&async=true&force=${isForce ? 'true' : 'false'}`, {
        method: 'POST',
        body: formData
    })
    .then(res => res.json())
    .then(ret => {
        if (gen !== singleUploadGen) return;   // 已被更新的上传替换
        if (ret.status === 'queued') {
            // 单张路径：服务端已转 JPEG 则切换预览；否则保持 HEIC 常驻提示
            if (ret.image_url && isBrowserDisplayableImageUrl(ret.image_url)) {
                const ph = getActivePhoto();
                if (ph) {
                    ph.imageUrl = ret.image_url;
                    if (ret.receipt_id) ph.receiptId = ret.receipt_id;
                    renderSider();
                    setMainPreview(ph);
                } else {
                    setNonWebPreviewHint(false, selectedFile);
                    const imgElem = document.getElementById('previewImg');
                    if (imgElem) imgElem.src = ret.image_url;
                }
            }
            offerDuplicateAction(ret);
            pollReceiptJob(ret.job_id,
                r => { if (gen === singleUploadGen) settleUploadResult(r); },
                {
                    token: singlePollToken,
                    onProgress: (job) => {
                        if (gen !== singleUploadGen || !job.image_url) return;
                        if (!isBrowserDisplayableImageUrl(job.image_url)) return;
                        const ph = getActivePhoto();
                        if (ph) {
                            ph.imageUrl = job.image_url;
                            renderSider();
                            setMainPreview(ph);
                        } else {
                            setNonWebPreviewHint(false, selectedFile);
                            const imgElem = document.getElementById('previewImg');
                            if (imgElem) imgElem.src = job.image_url;
                        }
                    },
                });
            return;
        }
        // 上传安全校验失败 / 402 订阅到期等同步拒绝路径
        settleUploadResult(ret);
    })
    .catch(err => {
        console.error('识别请求异常', err);
        if (gen === singleUploadGen) settleUploadResult({ status: 'error', msg: '识别失败，请稍后重试' });
    });

    function settleUploadResult(ret) {
        if (gen !== singleUploadGen) return;
        stopOcrTimer();
        document.getElementById('loadingCard').classList.add('hide');
        if (ret.status === 'cancelled') return;   // Q28: 已作废的轮询静默丢弃
        if (ret.status !== 'success') {
            const ph = getActivePhoto();
            if (ph && !ph.receiptId) {
                // D13 复用原单据：已有单据行的旧照片不回写错误（保留原 status/errorMsg），仅未入账新照片才落错误态
                if (ret.receipt_id) ph.receiptId = ret.receipt_id;
                if (ret.image_url && isBrowserDisplayableImageUrl(ret.image_url)) {
                    ph.imageUrl = ret.image_url;
                }
                ph.status = 'error';
                ph.errorMsg = ret.msg || '识别失败';
                renderSider();
                setMainPreview(ph, selectedFile);
            } else if (ph) {
                // 已有单据行：错误不覆盖其状态，仅在有服务端新图时更新预览
                if (ret.image_url && isBrowserDisplayableImageUrl(ret.image_url)) {
                    ph.imageUrl = ret.image_url;
                }
            } else if (ret.image_url && isBrowserDisplayableImageUrl(ret.image_url)) {
                setNonWebPreviewHint(false, selectedFile);
                const imgElem = document.getElementById('previewImg');
                if (imgElem) imgElem.src = ret.image_url;
            } else if (isNonWebImageFile(selectedFile)) {
                setMainPreviewFromFile(selectedFile, null);
            }
            showErrorCard(ret.msg || '识别失败', ret.receipt_id || null, ret.code);
            // P0-1 极模糊置顶：即使错误态也展示 quality_warnings 并置顶（<1s 快速失败不悬挂）
            const qw = collectQualityWarnings(ret);
            if (qw && qw.length) {
                // 低置信度提示（≤0.40）与模糊警告一并置顶
                showQualityWarnings(qw);
            } else if (ret && ret.code === 'IMAGE_QUALITY_ERROR') {
                // 兜底：code 为质量异常但顶层未带 warnings 时按 image_blur 展示
                showQualityWarnings(['image_blur']);
            }
            return;
        }

        // U-8: 成功解析完成，重置画质连续失败计数
        resetQualityFailCount();

        // P1-13: 成功后提取 version 回写 data（后端未返回时无副作用）
        captureResultVersion(ret);

        currentReceiptId = ret.receipt_id;
        currentReceiptData = ret.data;

        // 分析完成后锁定为最终状态（仅 web 可显示 URL）
        if (ret.image_url && isBrowserDisplayableImageUrl(ret.image_url)) {
            setNonWebPreviewHint(false, selectedFile);
            document.getElementById('previewImg').src = ret.image_url;
        }

        // 批量模式：把本次上传结果同步回本批照片，避免状态漂移
        const photo = getActivePhoto();
        if (photo) {
            photo.receiptId = ret.receipt_id;
            photo.status = 'parsed';
            photo.data = ret.data;
            photo.errorMsg = null;
            if (ret.image_url && isBrowserDisplayableImageUrl(ret.image_url)) {
                photo.imageUrl = ret.image_url;
            }
            if (Array.isArray(ret.quality_warnings)) photo.qualityWarnings = ret.quality_warnings;
            renderSider();
            // 解析完成即自动写库（无“全部保存”入口）
            autoSaveParsedPhoto(BatchUploader.activeIndex);
        }

        renderEditForm(ret.data);
        // Q29: 顶层 quality_warnings（含 duplicate_warning）多条可见
        showQualityWarnings(collectQualityWarnings(ret));
        // Q34: 同步成功路径的重复关联动作（queued 路径在轮询发起前已提示）
        offerDuplicateAction(ret);
        document.getElementById('prefillFormCard').classList.remove('hide');
    }
}

// -------------------------------------------------------------
// 2c. 识别失败错误卡片与双出口 (D28 显式 error + D33 失败双出口)
// U-3: 错误卡归因三分类 quality(画质) > gate(数字/契约门禁) > engine(引擎兜底)。
//      正文一律固定文案（engine 不透出原始 str(e)）；gate 原因经人话化包装。
// -------------------------------------------------------------

// U-3: 门禁 error_msg 最小改写为店员可读的「人话」（纯文本替换，输出仅经 innerText 文本节点渲染）
function humanizeGateMsg(msg) {
    let s = (msg == null) ? '' : String(msg);
    // 1) 去掉门禁前缀（算术门禁: / 契约校验失败:，容忍中英文冒号与空白）
    s = s.replace(/^\s*(算术门禁|契约校验失败|契约门禁)\s*[:：]\s*/, '');
    // 2) 契约字段名映射（未知字段原样保留）
    s = s.replace(/payment_marked/g, 'AI 填写的“是否已付款”一项');
    // 3) 英文校验短语映射（AC2-a：不残留裸英文吓用户；先具体短语，后 Input should be 兜底）
    s = s.replace(/Input should be a valid boolean/g, '填写的内容格式不对');
    s = s.replace(/Input should be/gi, '填写的内容格式不对');
    s = s.replace(/unable to interpret/gi, '无法理解该内容');
    s = s.replace(/missing/gi, '缺少必填内容');
    s = s.replace(/field required/gi, '缺少必填内容');
    // 4) 算术门禁话术人话化（预期总额= 连等号一起替换，避免「应为=265」磕绊）
    s = s.replace(/明细合计=/g, '明细合计');
    s = s.replace(/预期总额=/g, '应为 ');
    s = s.replace(/，但总额=/g, '，但单据写着');
    s = s.replace(/（差(-?[0-9.]+)）/g, '，相差 $1');
    return s.trim();
}

// -------------------------------------------------------------
// U-8: 连续画质/识别失败递进引导与计数追踪
// -------------------------------------------------------------
let _memQualityFailCount = 0;

function getQualityFailCount() {
    try {
        if (typeof window !== 'undefined' && window.sessionStorage) {
            const val = sessionStorage.getItem('receipt_quality_fail_count');
            if (val !== null) {
                const parsed = parseInt(val, 10);
                return isNaN(parsed) ? 0 : parsed;
            }
            return 0;
        }
    } catch (e) {
        // sessionStorage 不可用时兜底到内存变量
    }
    return _memQualityFailCount;
}

function incrementQualityFailCount() {
    const nextCount = getQualityFailCount() + 1;
    try {
        if (typeof window !== 'undefined' && window.sessionStorage) {
            sessionStorage.setItem('receipt_quality_fail_count', String(nextCount));
        }
    } catch (e) {
        // 兜底到内存变量
    }
    _memQualityFailCount = nextCount;
    return nextCount;
}

function resetQualityFailCount() {
    try {
        if (typeof window !== 'undefined' && window.sessionStorage) {
            sessionStorage.removeItem('receipt_quality_fail_count');
        }
    } catch (e) {
        // 兜底到内存变量
    }
    _memQualityFailCount = 0;
}

window.getQualityFailCount = getQualityFailCount;
window.incrementQualityFailCount = incrementQualityFailCount;
window.resetQualityFailCount = resetQualityFailCount;
window.updateOcrProgress = updateLoadingStage;
window.renderPrefillForm = function(data) {
    resetQualityFailCount();
    if (typeof renderEditForm === 'function') renderEditForm(data || {});
};

function showErrorCard(msg, receiptId, code) {
    lastErrorReceiptId = receiptId || null;
    const splitView = document.getElementById('splitViewArea');
    if (splitView) splitView.classList.remove('hide');
    document.getElementById('preConfirmCard').classList.add('hide');
    document.getElementById('loadingCard').classList.add('hide');
    document.getElementById('prefillFormCard').classList.add('hide');

    // U-3: msg 为 undefined/null/'' 时安全兜底为空串，归入 engine
    const rawMsg = (msg == null) ? '' : String(msg);

    // 归因优先级：quality(画质，用户可自救) > gate(数字/契约门禁，需人工核对) > engine(引擎繁忙/异常兜底)
    const isQualityIssue = (code === 'IMAGE_QUALITY_ERROR') ||
        /模糊|画质|曝光|分辨率|image_blur|quality/i.test(rawMsg);
    const isGateIssue = !isQualityIssue &&
        /算术门禁|契约校验|门禁|明细为空|总额不能|供应商为空|日期格式非法|数量非法|单价非法/.test(rawMsg);

    const titleElem = document.getElementById('errorCardTitle');
    const badgeElem = document.getElementById('errorCardBadge');
    const headingElem = document.getElementById('errorCardHeading');
    const msgElem = document.getElementById('errorMsgText');
    const btnForce = document.getElementById('btnForceRetry');
    const btnRetry = document.getElementById('btnRetryNormal');
    const convertBtn = document.getElementById('btnConvertManual');

    // 辅助函数：重置转手工按钮样式为标准次要按钮
    function resetConvertBtnStyle() {
        if (convertBtn) {
            convertBtn.className = 'btn btn-secondary';
            convertBtn.style.order = '';
            convertBtn.style.fontWeight = '';
            convertBtn.style.boxShadow = '';
        }
    }

    if (isQualityIssue) {
        // U-8: 连续画质/识别失败递进引导
        const count = incrementQualityFailCount();

        if (titleElem) titleElem.innerText = '[提示] 图像画质预检未达标（可强制继续）';

        if (count === 1) {
            // Level 1: 初次失败，温和提示
            if (badgeElem) {
                badgeElem.innerText = '用户自主决定';
                badgeElem.className = 'badge badge-warning';
            }
            if (headingElem) headingElem.innerText = '照片有点模糊，可能影响识别';
            if (msgElem) {
                msgElem.innerText = '系统已保留这张原图。您可以再拍一张更清晰的照片，或点下方「继续 AI 解析」让 AI 尽力识别，也可以转为手工录入。';
            }
            resetConvertBtnStyle();
        } else if (count === 2) {
            // Level 2: 再次失败，给出拍摄技巧指导
            if (badgeElem) {
                badgeElem.innerText = '拍摄技巧提示';
                badgeElem.className = 'badge badge-warning';
            }
            if (headingElem) headingElem.innerText = '照片还是有些模糊';
            if (msgElem) {
                msgElem.innerText = '拍摄小贴士：请把单据摊平、光线充足、手机平行正对拍摄，避免反光或阴影。您也可以点「继续 AI 解析」尝试，或转为手工录入。';
            }
            resetConvertBtnStyle();
        } else {
            // Level 3+: 连续多次失败，高亮推荐转手工录入
            if (badgeElem) {
                badgeElem.innerText = '建议转手工';
                badgeElem.className = 'badge badge-danger';
            }
            if (headingElem) headingElem.innerText = '连续多次无法清晰识别';
            if (msgElem) {
                msgElem.innerText = '连续多次未能清晰识别。为避免耽误时间，建议直接点击下方「转手工补录」快速录入单据，原图已在左侧为您展示。';
            }
            if (convertBtn) {
                convertBtn.className = 'btn btn-warning';
                convertBtn.style.order = '-1';
                convertBtn.style.fontWeight = '600';
                convertBtn.style.boxShadow = '0 0 0 2px rgba(217, 119, 6, 0.4)';
            }
        }

        // 画质问题用户可自主决定是否强制继续：三按钮齐备
        if (btnForce) {
            btnForce.style.display = '';
            btnForce.title = '忽略画质警告，继续 AI 智能解析';
        }
        if (btnRetry) {
            btnRetry.style.display = '';
            btnRetry.title = '重新发起识别';
        }
    } else if (isGateIssue) {
        resetConvertBtnStyle();
        if (titleElem) titleElem.innerText = '[拦截] AI 发现单据数字有疑问';
        if (badgeElem) {
            badgeElem.innerText = '需人工核对';
            badgeElem.className = 'badge badge-warning';
        }
        if (headingElem) headingElem.innerText = '单据上的数字对不上，AI 已暂停录入';
        if (msgElem) {
            // 门禁原因人话化后展示；原因为空时给默认话术
            const reason = humanizeGateMsg(rawMsg) || 'AI 多次核对仍未通过';
            msgElem.innerText = 'AI 核对时发现这张单据的数字互相矛盾，为避免记错账已暂停。\n' +
                '具体原因：' + reason + '\n' +
                '请对照左侧原图核对金额；确认无误可点「重试」让 AI 再核一遍，或转为手工录入。';
        }
        // 门禁拦截是防记错账：不允许「忽略警告继续」，只能重核或转手工；
        // 隐藏时 title 同步复位为中性文案，消除隐藏元素上的画质语义残留
        if (btnForce) {
            btnForce.style.display = 'none';
            btnForce.title = '重新提交给 AI 解析';
        }
        if (btnRetry) {
            btnRetry.style.display = '';
            btnRetry.title = '让 AI 重新核对这张单据';
        }
    } else {
        resetConvertBtnStyle();
        if (titleElem) titleElem.innerText = '[稍后重试] AI 服务暂时繁忙';
        if (badgeElem) {
            badgeElem.innerText = '非照片问题';
            badgeElem.className = 'badge badge-danger';
        }
        if (headingElem) headingElem.innerText = 'AI 服务现在很忙，这张单据还没识别完';
        if (msgElem) {
            // 引擎异常不透出原始 str(e)，避免吓到用户
            msgElem.innerText = '这不是照片的问题，请不要重新拍照。原图已保留。请稍等 1 分钟后点「重试」；如果连续失败，请转为手工录入，或联系店长。';
        }
        // 引擎繁忙与画质无关：强制继续无意义，稍后重试或转手工；
        // 隐藏时 title 同步复位为中性文案，消除隐藏元素上的画质语义残留
        if (btnForce) {
            btnForce.style.display = 'none';
            btnForce.title = '重新提交给 AI 解析';
        }
        if (btnRetry) {
            btnRetry.style.display = '';
            btnRetry.title = '重新发起识别';
        }
    }

    // 转手工录入需要已有单据行；没有时只允许重新上传/重试（画质 400 无 receiptId 场景仍隐藏）
    // 转手工录入逃生通道随时可用，保留原图
    if (convertBtn) convertBtn.style.display = '';

    document.getElementById('errorCard').classList.remove('hide');
}

function retryFromErrorCard(force = true) {
    const ph = getActivePhoto();
    if (ph) ph.force = !!force;
    window._selectedFileForce = !!force;

    if (lastErrorReceiptId) {
        // D13: 复用原单据重跑识别
        retryReceiptRecognition(lastErrorReceiptId, force);
    } else {
        // 未生成单据行（如网络异常或画质拦截）：重新上传并显式指定 force
        triggerAnalysisNow(force);
    }
}

function convertManualFromErrorCard() {
    if (lastErrorReceiptId) {
        const receiptId = lastErrorReceiptId;

        document.getElementById('errorCard').classList.add('hide');
        document.getElementById('loadingCard').classList.remove('hide');

        fetch(`/api/receipt/${receiptId}/convert_manual`, { method: 'POST' })
        .then(res => res.json())
        .then(ret => {
            document.getElementById('loadingCard').classList.add('hide');
            if (ret.status !== 'success') {
                showToast('转手工录入失败：' + (ret.msg || '请稍后重试'), 'error');
                showErrorCard(ret.msg || '转手工录入失败', receiptId);
                return;
            }
            // D33: 转手工录入成功——保留原图，按返回数据渲染复核表单
            applyRecognizedResult(ret, receiptId);
            showToast('已转为手工录入，请对照左侧原图补录字段', 'info');
        })
        .catch(err => {
            document.getElementById('loadingCard').classList.add('hide');
            console.error('转手工录入请求异常', err);
            showToast('转手工录入请求失败，请检查网络后重试', 'error');
            showErrorCard('转手工录入失败，请稍后重试', receiptId);
        });
    } else {
        // 无单据行时（如画质 400 快速失败）：保留原图直接切入手工录入表单
        abortLoadingAndSwitchToManual();
    }
}

// 对已有单据行调用 retry 端点（复用原单据，attempt+1 由后端入审计）
function retryReceiptRecognition(receiptId, force = false) {
    if (!receiptId) {
        triggerAnalysisNow(force);
        return;
    }
    document.getElementById('errorCard').classList.add('hide');
    document.getElementById('preConfirmCard').classList.add('hide');
    document.getElementById('prefillFormCard').classList.add('hide');
    document.getElementById('loadingCard').classList.remove('hide');
    startOcrTimer();

    const gen = ++singleUploadGen;
    if (singlePollToken) singlePollToken.cancelled = true;
    singlePollToken = { cancelled: false };

    fetch(`/api/receipt/${receiptId}/retry?force=${force ? 'true' : 'false'}`, { method: 'POST' })
    .then(res => res.json())
    .then(ret => {
        if (gen !== singleUploadGen) return;
        if (ret.status === 'queued' && ret.job_id) {
            pollReceiptJob(ret.job_id, (jobRet) => {
                if (gen !== singleUploadGen) return;
                stopOcrTimer();
                document.getElementById('loadingCard').classList.add('hide');
                if (jobRet.status === 'cancelled') return;
                if (jobRet.status !== 'success') {
                    showErrorCard(jobRet.msg || '重试识别失败', receiptId);
                    return;
                }
                applyRecognizedResult(jobRet, receiptId);
            }, { token: singlePollToken });
            return;
        }
        stopOcrTimer();
        document.getElementById('loadingCard').classList.add('hide');
        if (ret.status !== 'success') {
            showErrorCard(ret.msg || '重试识别失败', receiptId);
            return;
        }
        // 重试成功：走正常 prefill 渲染
        applyRecognizedResult(ret, receiptId);
    })
    .catch(err => {
        if (gen !== singleUploadGen) return;
        stopOcrTimer();
        document.getElementById('loadingCard').classList.add('hide');
        showErrorCard("重试请求异常: " + err, receiptId);
    });
}

// 重试/转手工录入成功后的统一渲染入口（单张与批量共用）
function applyRecognizedResult(ret, fallbackReceiptId) {
    // U-8: 解析/转录成功重置连续失败计数
    resetQualityFailCount();
    // D12：真实单据接管主区域 → 退出新建手工单态
    resetManualEntryMode();
    // P1-13: 成功后提取 version 回写 data（retry/convert 响应契约同 upload）
    captureResultVersion(ret);

    const rid = ret.receipt_id || fallbackReceiptId || null;
    currentReceiptId = rid;
    currentReceiptData = ret.data || null;

    if (ret.image_url && isBrowserDisplayableImageUrl(ret.image_url)) {
        setNonWebPreviewHint(false, selectedFile);
        document.getElementById('previewImg').src = ret.image_url;
        const ph = getActivePhoto();
        if (ph) ph.imageUrl = ret.image_url;
    } else {
        const ph = getActivePhoto();
        if (ph) setMainPreview(ph);
        else if (isNonWebImageFile(selectedFile)) setMainPreviewFromFile(selectedFile, null);
    }

    renderEditForm(ret.data || {});
    // Q29: 质量预检警告多条可见
    showQualityWarnings(collectQualityWarnings(ret));
    document.getElementById('preConfirmCard').classList.add('hide');
    document.getElementById('errorCard').classList.add('hide');
    document.getElementById('prefillFormCard').classList.remove('hide');

    // 批量模式：同步当前激活照片状态
    const photo = getActivePhoto();
    if (photo && (!photo.receiptId || photo.receiptId === rid)) {
        photo.status = 'parsed';
        photo.receiptId = rid;
        photo.data = ret.data || photo.data;
        if (ret.image_url) photo.imageUrl = ret.image_url;
        if (Array.isArray(ret.quality_warnings)) photo.qualityWarnings = ret.quality_warnings;
        photo.errorMsg = null;
        renderSider();
        // 解析完成即自动写库（无“全部保存”入口）
        autoSaveParsedPhoto(findPhotoIndex(photo));
    }
}

// -------------------------------------------------------------
// D12：新建手工单（第八形态 manual_entry，仅 owner 可建）
//   无原图、不走 OCR；保存走 /api/save_edited 无 receipt_id 分支
//   （后端校验：日期 YYYY-MM-DD 真实、供应商非空非默认占位、明细非空）。
//   独立于照片批次：不进 BatchUploader.photos，不影响批量侧栏逻辑。
// -------------------------------------------------------------

// 手工录入前端校验：与后端 save_edited 手工分支 400 语义对齐。
// 返回错误文案（空串 = 通过）。
function validateManualEntry(data) {
    const supplier = String((data && data.supplier_name) || '').trim();
    if (!supplier || supplier === '通用供应商') {
        return '请填写供应商名称';
    }
    const dateStr = String((data && data.date) || '').trim();
    if (!dateStr || !/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) {
        return '请选择开单日期';
    }
    const m = dateStr.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    const y = Number(m[1]), mo = Number(m[2]), d = Number(m[3]);
    const dt = new Date(y, mo - 1, d);
    if (dt.getFullYear() !== y || dt.getMonth() !== mo - 1 || dt.getDate() !== d) {
        return '开单日期不正确，请选择真实存在的日历日期';
    }
    const totalAmt = Number((data && data.total_amount) || 0);
    if (isNaN(totalAmt) || totalAmt <= 0) {
        return '单据总金额必须大于 0';
    }
    const items = (data && data.items) || [];
    if (items.length === 0) {
        return '请至少输入一行消费明细';
    }
    let hasRealItem = false;
    for (let i = 0; i < items.length; i++) {
        const it = items[i];
        const name = String((it && (it.name || it.raw_name)) || '').trim();
        if (!name) {
            return `第 ${i + 1} 行的品名不能为空，请填写或删除该行`;
        }
        const qty = Number(it && it.quantity);
        const price = Number(it && it.unit_price);
        if (isNaN(qty) || qty <= 0 || isNaN(price) || price < 0) {
            return `第 ${i + 1} 行的单价或数量不是有效数字，请重新输入`;
        }
        hasRealItem = true;
    }
    if (!hasRealItem) {
        return '请至少输入一行消费明细';
    }
    return '';
}

// 手工录入左栏 UI 态：隐藏原图 + 图像工具，显示「无原图」占位
function setManualEntryLeftPanel(on) {
    const container = document.getElementById('imgViewerContainer');
    const noImage = document.getElementById('manualEntryNoImage');
    const toolbar = document.getElementById('imgToolbar');
    const actions = document.getElementById('leftCardTitleActions');
    if (container) container.classList.toggle('manual-entry-active', on);
    if (noImage) noImage.classList.toggle('hide', !on);
    if (toolbar) toolbar.classList.toggle('hide', on);
    if (actions) actions.classList.toggle('hide', on);
    if (on) {
        const img = document.getElementById('previewImg');
        if (img) img.src = '';
        setNonWebPreviewHint(false, null);
        const cropOverlay = document.getElementById('cropOverlay');
        if (cropOverlay) cropOverlay.classList.add('hide');
    }
}

// 手工录入表单标题/徽章：区分「手工录入复核」与正常 AI Prefill 复核
function setManualEntryFormBadge(on) {
    const badge = document.getElementById('manualEntryFormBadge');
    if (badge) badge.classList.toggle('hide', !on);
    const title = document.getElementById('manualEntryFormTitle');
    if (title) {
        title.textContent = on
            ? ' 步骤 2/2：手工录入复核与修改'
            : ' 步骤 2/2：AI Prefill 字段复核与修改';
    }
}

// 退出新建手工单态（真实照片/单据接管主区域时调用，恢复左栏与标题）
function resetManualEntryMode() {
    isManualEntry = false;
    setManualEntryLeftPanel(false);
    setManualEntryFormBadge(false);
}

// 新建手工单入口可见性：staff 及以上均可见
function syncManualEntryVisibility() {
    const btn = document.getElementById('btnNewManualEntry');
    if (!btn) return;
    btn.style.display = '';
}

// 进入新建手工单空态：无原图（左栏占位）、表单清空、明细可加行
function startManualEntry() {
    isManualEntry = true;
    currentReceiptId = null;
    currentReceiptData = null;

    // 清理遗留的原图/文件态（不触碰 BatchUploader.photos）
    if (originalObjectUrl) {
        try { URL.revokeObjectURL(originalObjectUrl); } catch (e) {}
        originalObjectUrl = null;
    }
    originalFile = null;
    selectedFile = null;
    currentZoom = 1.0;
    currentRotation = 0;
    panX = 0;
    panY = 0;
    isFocalZoomed = false;
    activeTool = null;

    // 左栏：「无原图」占位 + 隐藏图像工具
    setManualEntryLeftPanel(true);
    setManualEntryFormBadge(true);

    // 清空复核表单，进入空态（单据字段 + 明细一行）
    document.getElementById('inpSupplier').value = '';
    document.getElementById('inpDate').value = '';
    document.getElementById('inpSheet').value = '';
    document.getElementById('inpTotal').value = '0.00';
    const settlement = document.getElementById('inpSettlementType');
    if (settlement) settlement.value = '';
    const markInput = document.getElementById('inpPaymentMark');
    if (markInput) markInput.value = '无';
    populateDeptSelect(document.getElementById('inpDepartmentId'), null);
    showQualityWarnings([]);
    document.getElementById('itemTableBody').innerHTML = '';
    addEmptyRow();

    // 卡片切换：显示复核表单空态
    document.getElementById('preConfirmCard').classList.add('hide');
    document.getElementById('loadingCard').classList.add('hide');
    document.getElementById('errorCard').classList.add('hide');
    document.getElementById('prefillFormCard').classList.remove('hide');
    document.getElementById('splitViewArea').classList.remove('hide');

    showToast('已进入新建手工单：请填写供应商、开单日期与明细后保存', 'info', TOAST_DURATION.guide);
}

// U-11: 识别过程随时转手工逃生通道（保留原图，中止识别，直接切入复核/手工表单）
function abortLoadingAndSwitchToManual() {
    // 1. 停止识别计时器与进度
    stopOcrTimer();
    stopBatchTimer();

    // 2. 作废单张/批量轮询令牌与代际标记，丢弃任何迟到的识别响应
    singleUploadGen++;
    if (singlePollToken) singlePollToken.cancelled = true;

    const curPhoto = (typeof getActivePhoto === 'function') ? getActivePhoto() : null;
    if (curPhoto) {
        if (curPhoto.pollToken) curPhoto.pollToken.cancelled = true;
        if (curPhoto.status === 'uploading') {
            curPhoto.status = 'pending';
        }
        if (typeof renderSider === 'function') renderSider();
    }

    // 3. 隐藏 Loading / PreConfirm / Error 卡片
    const loadingCard = document.getElementById('loadingCard');
    if (loadingCard) loadingCard.classList.add('hide');
    const preConfirmCard = document.getElementById('preConfirmCard');
    if (preConfirmCard) preConfirmCard.classList.add('hide');
    const errorCard = document.getElementById('errorCard');
    if (errorCard) errorCard.classList.add('hide');

    // 4. 确保左侧原图完好保留，不清除图片与工具栏
    setManualEntryLeftPanel(false);
    const noImage = document.getElementById('manualEntryNoImage');
    if (noImage) noImage.classList.add('hide');
    const previewImg = document.getElementById('previewImg');
    if (previewImg) previewImg.style.display = '';
    updateToolbarButtonStates();

    // 5. 显示分栏与表单卡片
    const splitView = document.getElementById('splitViewArea');
    if (splitView) splitView.classList.remove('hide');
    const prefillCard = document.getElementById('prefillFormCard');
    if (prefillCard) prefillCard.classList.remove('hide');

    // 6. 表单标题与徽章设置
    if (!currentReceiptId) {
        isManualEntry = true;
    }
    const title = document.getElementById('manualEntryFormTitle');
    if (title) title.textContent = ' 步骤 2/2：手工录入与复核';
    const badge = document.getElementById('manualEntryFormBadge');
    if (badge) badge.classList.add('hide');

    // 7. 表单字段初始化/保底（若为空则赋默认值，保留已有输入）
    const inpSupplier = document.getElementById('inpSupplier');
    const inpDate = document.getElementById('inpDate');
    const inpSheet = document.getElementById('inpSheet');
    const inpTotal = document.getElementById('inpTotal');
    const inpSettlement = document.getElementById('inpSettlementType');
    const inpPaymentMark = document.getElementById('inpPaymentMark');
    const inpDept = document.getElementById('inpDepartmentId');

    if (inpDate && !inpDate.value) {
        const todayStr = new Date().toISOString().slice(0, 10);
        inpDate.value = todayStr;
        if (inpSheet && !inpSheet.value) {
            inpSheet.value = todayStr.slice(0, 7);
        }
    }
    if (inpTotal && (!inpTotal.value || inpTotal.value === '0.00')) {
        inpTotal.value = '0.00';
    }
    if (inpPaymentMark && !inpPaymentMark.value) {
        inpPaymentMark.value = '无';
    }
    if (inpDept && !inpDept.value) {
        populateDeptSelect(inpDept, null);
    }
    showQualityWarnings([]);

    const tbody = document.getElementById('itemTableBody');
    if (tbody && tbody.children.length === 0) {
        addEmptyRow();
    }

    renderCurrencySymbol();

    // 8. 友好人话提示
    showToast('已停止等待，原图已在左侧保留，请直接手工录入', 'info', TOAST_DURATION.guide);
}

// 工具激活状态: null (未选中任何工具), 'zoom' (定点放大模式), 'crop' (裁剪模式)
let activeTool = null;

function updateToolbarButtonStates() {
    const photo = getActivePhoto();
    const cropDisabled = !isPhotoSelectable(photo);  // 已解析/已保存不可裁剪
    // 删除图片 / 更改图片：解析开始（非 pending）后隐藏，避免解析中改动原图
    const allowImageEdit = !!photo && photo.status === 'pending';
    const btnDelete = document.getElementById('btnDeleteImage');
    const btnReload = document.getElementById('btnReloadImage');
    if (btnDelete) btnDelete.style.display = allowImageEdit ? '' : 'none';
    if (btnReload) btnReload.style.display = allowImageEdit ? '' : 'none';
    const btnZoom = document.getElementById('btnZoomToggle');
    const btnCrop = document.getElementById('btnCropToggle');
    const btnApply = document.getElementById('btnApplyCrop');
    const btnOpenOriginal = document.getElementById('btnOpenOriginal');
    const container = document.getElementById('imgViewerContainer');
    const cropOverlay = document.getElementById('cropOverlay');

    // 「原图新窗」：只要有选中的照片就启用；真正无可打开原图时由函数用 toast 提示
    if (btnOpenOriginal) {
        btnOpenOriginal.disabled = !photo;
    }

    if (btnCrop) btnCrop.disabled = cropDisabled;
    if (btnApply) btnApply.disabled = cropDisabled;
    // 已解析/已保存照片：强制退出裁剪态，避免残留选区工具
    if (cropDisabled && activeTool === 'crop') {
        activeTool = null;
        if (cropOverlay) cropOverlay.classList.add('hide');
    }

    btnZoom.classList.remove('active-zoom');
    btnCrop.classList.remove('active-crop');
    if (btnApply) btnApply.classList.add('hide');

    if (activeTool === 'zoom') {
        btnZoom.classList.add('active-zoom');
        container.style.cursor = isFocalZoomed ? 'grab' : 'zoom-in';
        if (cropOverlay) cropOverlay.classList.add('hide');
    } else if (activeTool === 'crop') {
        btnCrop.classList.add('active-crop');
        if (btnApply) btnApply.classList.remove('hide');
        container.style.cursor = 'crosshair';
        // A-P1 显性入口：点击框选后立即显示虚线黄半透明 overlay（即使未拖拽，hide 移除以满足可视态验证）
        if (cropOverlay) cropOverlay.classList.remove('hide');
    } else {
        container.style.cursor = 'default';
        if (cropOverlay) cropOverlay.classList.add('hide');
    }
}

// -------------------------------------------------------------
// 3. 定点放大、平移与交互式裁剪机制 (Focal Zoom & Crop)
// -------------------------------------------------------------
function initFocalZoomAndCrop() {
    const container = document.getElementById('imgViewerContainer');
    const img = document.getElementById('previewImg');
    const cropOverlay = document.getElementById('cropOverlay');

    img.addEventListener('dragstart', (e) => e.preventDefault());
    container.addEventListener('dragstart', (e) => e.preventDefault());

    container.addEventListener('mousedown', (e) => {
        if (e.target.tagName === 'BUTTON' || e.target.closest('.img-toolbar')) return;
        if (!activeTool) return;
        
        isMouseDown = true;
        isDragging = false;
        startMouseX = e.clientX;
        startMouseY = e.clientY;

        const rect = container.getBoundingClientRect();
        if (activeTool === 'crop') {
            isCropDragging = true;
            cropStartX = e.clientX - rect.left;
            cropStartY = e.clientY - rect.top;
            
            cropOverlay.style.left = `${cropStartX}px`;
            cropOverlay.style.top = `${cropStartY}px`;
            cropOverlay.style.width = '0px';
            cropOverlay.style.height = '0px';
            cropOverlay.classList.remove('hide');
        }
    });

    window.addEventListener('mousemove', (e) => {
        if (!isMouseDown || !activeTool) return;

        const rect = container.getBoundingClientRect();
        const mouseX = e.clientX - rect.left;
        const mouseY = e.clientY - rect.top;

        if (activeTool === 'crop' && isCropDragging) {
            const width = Math.abs(mouseX - cropStartX);
            const height = Math.abs(mouseY - cropStartY);
            const left = Math.min(mouseX, cropStartX);
            const top = Math.min(mouseY, cropStartY);

            cropRect = { left, top, width, height };

            cropOverlay.style.left = `${left}px`;
            cropOverlay.style.top = `${top}px`;
            cropOverlay.style.width = `${width}px`;
            cropOverlay.style.height = `${height}px`;
            return;
        }

        const dx = e.clientX - startMouseX;
        const dy = e.clientY - startMouseY;
        if (Math.abs(dx) > 3 || Math.abs(dy) > 3) {
            isDragging = true;
            if (activeTool === 'zoom' && currentZoom > 1.0) {
                panX += dx;
                panY += dy;
                startMouseX = e.clientX;
                startMouseY = e.clientY;
                applyImgTransform();
            }
        }
    });

    window.addEventListener('mouseup', (e) => {
        isMouseDown = false;
        if (activeTool === 'crop') {
            isCropDragging = false;
        }
    });

    container.addEventListener('click', (e) => {
        if (e.target.tagName === 'BUTTON' || e.target.closest('.img-toolbar')) return;
        if (!activeTool || activeTool !== 'zoom') return;
        if (isDragging) return;

        const rect = container.getBoundingClientRect();
        const clickX = e.clientX - rect.left;
        const clickY = e.clientY - rect.top;

        const percentX = (clickX / rect.width) * 100;
        const percentY = (clickY / rect.height) * 100;

        if (!isFocalZoomed) {
            currentZoom = 2.5;
            panX = 0;
            panY = 0;
            img.style.transformOrigin = `${percentX}% ${percentY}%`;
            isFocalZoomed = true;
        } else {
            currentZoom = 1.0;
            panX = 0;
            panY = 0;
            img.style.transformOrigin = `center center`;
            isFocalZoomed = false;
        }
        applyImgTransform();
        updateToolbarButtonStates();
    });
}

function toggleZoomMode() {
    if (activeTool === 'zoom') {
        activeTool = null;
        currentZoom = 1.0;
        panX = 0;
        panY = 0;
        isFocalZoomed = false;
    } else {
        activeTool = 'zoom';
    }
    applyImgTransform();
    updateToolbarButtonStates();
}

/**
 * 解析「原始收据原图」的最佳 URL：
 * 优先服务端原图 imageUrl > 本地原图 objectUrl（均排除裁剪结果 croppedObjectUrl），
 * 最后回退到当前预览图 previewImg.src（排除 HEIC 占位 SVG）。
 */
function getOriginalReceiptUrl(photo) {
    if (!photo) return null;
    if (photo.imageUrl && isBrowserDisplayableImageUrl(photo.imageUrl)) return photo.imageUrl;
    if (photo.objectUrl) return photo.objectUrl;
    const pi = document.getElementById('previewImg');
    if (pi && pi.src && isBrowserDisplayableImageUrl(pi.src) && !pi.src.startsWith('data:image/svg')) {
        return pi.src;
    }
    return null;
}

/**
 * 在新窗口打开「原始收据原图」（用户点此按钮前的未裁剪 / 未旋转版本）。
 * 直接把新标签页导航到图片绝对 URL（浏览器原生展示图片，最稳、不会被当空白弹窗），
 * 并全程 try/catch —— 任何异常都会以 toast 暴露，不再「静默无效果」。
 */
function openOriginalInNewWindow() {
    try {
        const photo = getActivePhoto();
        if (!photo) {
            showToast('请先选择一张收据照片', 'warning');
            return;
        }
        const originalUrl = getOriginalReceiptUrl(photo);

        if (!originalUrl) {
            // HEIC 等不可预览原图：解析完成后服务端会转成 JPEG，届时再打开
            const isNonWeb = photo.file ? isNonWebImageFile(photo.file) : false;
            showToast(
                isNonWeb
                    ? '该原图为 HEIC 等不可预览格式，解析完成并转成 JPEG 后即可在新窗打开'
                    : '暂无可打开的原始收据原图',
                'info'
            );
            return;
        }

        // 解析为绝对 URL：相对路径需基于当前页面 origin，而非新窗口的 about:blank
        const absUrl = new URL(originalUrl, window.location.href).href;
        const newWin = window.open(absUrl, '_blank');
        if (!newWin) {
            showToast('浏览器拦截了新窗口，请允许弹出窗口 / 关闭拦截器后重试', 'warning');
            return;
        }
        newWin.focus();
    } catch (e) {
        console.error('openOriginalInNewWindow error:', e);
        showToast('打开原图失败：' + (e && e.message ? e.message : String(e)), 'error');
    }
}

function toggleCropMode() {
    if (!isPhotoSelectable(getActivePhoto())) {
        showToast('该照片已完成解析，不可再裁剪', 'warning');
        return;
    }
    if (activeTool === 'crop') {
        activeTool = null;
    } else {
        activeTool = 'crop';
        currentZoom = 1.0;
        panX = 0;
        panY = 0;
        isFocalZoomed = false;
        applyImgTransform();
    }
    updateToolbarButtonStates();
}

function applyCropSelection() {
    if (!isPhotoSelectable(getActivePhoto())) {
        showToast('该照片已完成解析，不可再裁剪', 'warning');
        return;
    }
    const img = document.getElementById('previewImg');
    const container = document.getElementById('imgViewerContainer');
    const overlay = document.getElementById('cropOverlay');

    if (!cropRect.width || cropRect.width < 10 || overlay.classList.contains('hide')) {
        showToast('请先在图片上拖动框选裁剪区域', 'warning');
        return;
    }

    const cRect = container.getBoundingClientRect();
    const iRect = img.getBoundingClientRect();

    const scaleX = img.naturalWidth / iRect.width;
    const scaleY = img.naturalHeight / iRect.height;

    const sourceX = (cropRect.left - (iRect.left - cRect.left)) * scaleX;
    const sourceY = (cropRect.top - (iRect.top - cRect.top)) * scaleY;
    const sourceW = cropRect.width * scaleX;
    const sourceH = cropRect.height * scaleY;

    if (sourceW <= 0 || sourceH <= 0) {
        showToast('框选区域无效，请重新拖拽选区', 'warning');
        return;
    }

    const canvas = document.createElement('canvas');
    canvas.width = sourceW;
    canvas.height = sourceH;
    const ctx = canvas.getContext('2d');

    ctx.drawImage(
        img,
        Math.max(0, sourceX), Math.max(0, sourceY), sourceW, sourceH,
        0, 0, sourceW, sourceH
    );

    canvas.toBlob((blob) => {
        if (!blob) return;
        const croppedFile = new File([blob], selectedFile ? selectedFile.name : "cropped_receipt.jpg", { type: "image/jpeg" });
        selectedFile = croppedFile;

        const croppedUrl = URL.createObjectURL(blob);

        // 批量模式：把裁剪结果持久化到 photo 对象，切换照片不丢失
        const photo = getActivePhoto();
        if (photo) {
            if (photo.croppedObjectUrl) {
                try { URL.revokeObjectURL(photo.croppedObjectUrl); } catch(e) {}
            }
            photo.croppedObjectUrl = croppedUrl;
            photo.file = croppedFile;       // 后续上传用裁剪后的文件
            photo.cropped = true;
            setMainPreview(photo);
        } else {
            img.src = croppedUrl;
            setNonWebPreviewHint(false, croppedFile);
        }

        activeTool = null;
        updateToolbarButtonStates();
    }, 'image/jpeg', 0.95);
}

function zoomImg(delta) {
    currentZoom = Math.max(0.5, Math.min(4.0, currentZoom + delta));
    applyImgTransform();
    updateToolbarButtonStates();
}

function rotateImg() {
    currentRotation = (currentRotation + 90) % 360;
    applyImgTransform();
}

function resetImgTransform() {
    currentZoom = 1.0;
    currentRotation = 0;
    panX = 0;
    panY = 0;
    isFocalZoomed = false;
    activeTool = null;

    // 核心重置逻辑：在启动分析之前，按【重置】可还原为原始未裁剪照片
    // 注意：HEIC 等绝不能写回 objectUrl（浏览器黑屏）；走 setMainPreview 保持常驻提示
    const img = document.getElementById('previewImg');

    const photo = (typeof BatchUploader !== 'undefined') ? getActivePhoto() : null;
    if (photo) {
        if (photo.croppedObjectUrl) {
            try { URL.revokeObjectURL(photo.croppedObjectUrl); } catch(e) {}
            photo.croppedObjectUrl = null;
            photo.cropped = false;
            photo.file = photo.originalFile || photo.file;
        }
        selectedFile = photo.file;
        setMainPreview(photo);
    } else if (originalFile) {
        selectedFile = originalFile;
        setMainPreviewFromFile(originalFile, originalObjectUrl);
    }

    if (img) {
        img.style.transformOrigin = 'center center';
        applyImgTransform();
    }
    updateToolbarButtonStates();
}

function applyImgTransform() {
    const img = document.getElementById('previewImg');
    img.style.transform = `translate(${panX}px, ${panY}px) scale(${currentZoom}) rotate(${currentRotation}deg)`;
}

// -------------------------------------------------------------
// 5. 单位滑动展示、直接输入、相似匹配与新单位自动保存复用逻辑
// -------------------------------------------------------------
function updateGlobalDatalistUnits() {
    const datalist = document.getElementById('dlUnits');
    if (!datalist) return;
    datalist.innerHTML = '';
    availableUnits.forEach(u => {
        const opt = document.createElement('option');
        opt.value = u;
        datalist.appendChild(opt);
    });
}

function openUnitMenu(inputElem) {
    document.querySelectorAll('.unit-dropdown-menu').forEach(m => m.classList.add('hide'));
    const wrap = inputElem.closest('.unit-combobox-wrap');
    if (!wrap) return;
    const menu = wrap.querySelector('.unit-dropdown-menu');
    if (!menu) return;

    renderUnitMenuItems(inputElem, menu);
    menu.classList.remove('hide');
}

function renderUnitMenuItems(inputElem, menuElem) {
    const currentVal = (inputElem.value || "").trim().toLowerCase();
    
    const recommendedList = [];
    const regularList = [];

    availableUnits.forEach(u => {
        const uLower = u.toLowerCase();
        const isExactMatch = (uLower === currentVal);
        const isSimilarMatch = currentVal && (uLower.includes(currentVal) || currentVal.includes(uLower));

        const itemData = {
            unit: u,
            isExactMatch,
            isSimilarMatch
        };

        if (isExactMatch || isSimilarMatch) {
            recommendedList.push(itemData);
        } else {
            regularList.push(itemData);
        }
    });

    // 核心规则：将 Highlight 推荐匹配单位强制排列在下拉列表的最最顶端！
    const sortedList = [...recommendedList, ...regularList];

    let html = "";
    sortedList.forEach(item => {
        const u = item.unit;
        let highlightClass = "";
        let badgeHTML = "";
        if (item.isExactMatch) {
            highlightClass = "highlight";
            badgeHTML = `<span class="badge-matched">已选择</span>`;
        } else if (item.isSimilarMatch) {
            highlightClass = "highlight";
            badgeHTML = `<span class="badge-matched"> 推荐匹配</span>`;
        }

        html += `
            <div class="unit-dropdown-item ${highlightClass}" data-unit="${w2Escape(u)}">
                <span>${w2Escape(u)}</span>
                ${badgeHTML}
            </div>
        `;
    });

    menuElem.innerHTML = html;
}

// P0-2：单位下拉改事件委托 + dataset 取值。
// 原 onmousedown="selectUnitItem(this,'${u}')" 把单位值裸拼进行内 JS 字符串，
// 单位含引号即可逃逸注入（单位来自 OCR/用户输入，经 registerNewUnitsFromRows 入库池）。
document.addEventListener('mousedown', (e) => {
    const item = e.target && e.target.closest
        ? e.target.closest('.unit-dropdown-item[data-unit]') : null;
    if (item) selectUnitItem(item);
});

function selectUnitItem(itemElem) {
    const unitValue = itemElem.dataset.unit || '';
    const wrap = itemElem.closest('.unit-combobox-wrap');
    if (wrap) {
        const input = wrap.querySelector('.inp-unit');
        if (input) {
            input.value = unitValue;
            input.dispatchEvent(new Event('input', { bubbles: true }));
        }
        const menu = wrap.querySelector('.unit-dropdown-menu');
        if (menu) menu.classList.add('hide');
    }
}

function closeUnitMenuDelay(inputElem) {
    setTimeout(() => {
        const wrap = inputElem.closest('.unit-combobox-wrap');
        if (wrap) {
            const menu = wrap.querySelector('.unit-dropdown-menu');
            if (menu) menu.classList.add('hide');
        }
    }, 200);
}

function registerNewUnitsFromRows(rowElements) {
    rowElements.forEach(tr => {
        const unitInput = tr.querySelector('.inp-unit');
        if (unitInput && unitInput.value) {
            const uVal = unitInput.value.trim();
            if (uVal && !availableUnits.includes(uVal)) {
                availableUnits.push(uVal);
            }
        }
    });
    updateGlobalDatalistUnits();
}

// -------------------------------------------------------------
// 5.5 供应商 Combobox 下拉框逻辑 (Highlight 推荐项目强行置顶)
// -------------------------------------------------------------
let availableSuppliers = [];

function loadSuppliersData() {
    fetch('/api/suppliers')
    .then(res => res.json())
    .then(ret => {
        if (ret.status === 'success') {
            availableSuppliers = ret.data || [];
        }
    })
    .catch(err => console.error("加载供应商列表失败:", err));
}

let isSelectingSupplier = false;

function openSupplierMenu(inputElem) {
    if (isSelectingSupplier) return;
    document.querySelectorAll('.unit-dropdown-menu').forEach(m => m.classList.add('hide'));
    const wrap = inputElem.closest('.unit-combobox-wrap');
    if (!wrap) return;
    const menu = wrap.querySelector('.unit-dropdown-menu');
    if (!menu) return;

    renderSupplierMenuItems(inputElem, menu);
    menu.classList.remove('hide');
}

function closeSupplierMenuDelay(inputElem) {
    setTimeout(() => {
        if (isSelectingSupplier) return;
        const wrap = inputElem.closest('.unit-combobox-wrap');
        if (wrap) {
            const menu = wrap.querySelector('.unit-dropdown-menu');
            if (menu) menu.classList.add('hide');
        }
    }, 200);
}

function selectSupplierItem(itemElem) {
    isSelectingSupplier = true;
    const supplierName = itemElem.getAttribute('data-supplier-name') || '';
    const wrap = itemElem.closest('.unit-combobox-wrap');
    if (wrap) {
        const input = wrap.querySelector('input');
        if (input) {
            input.value = supplierName;
        }
        const menu = wrap.querySelector('.unit-dropdown-menu');
        if (menu) {
            menu.classList.add('hide');
        }
    }
    setTimeout(() => {
        isSelectingSupplier = false;
    }, 300);
}

function renderSupplierMenuItems(inputElem, menuElem) {
    if (!availableSuppliers || availableSuppliers.length === 0) {
        fetch('/api/suppliers')
        .then(res => res.json())
        .then(ret => {
            if (ret.status === 'success' && ret.data) {
                availableSuppliers = ret.data;
                renderSupplierMenuItems(inputElem, menuElem);
            }
        })
        .catch(err => console.error(err));
    }

    const rawVal = (inputElem.value || "").trim();
    const currentVal = rawVal.toLowerCase();
    
    const recommendedList = [];
    const regularList = [];

    availableSuppliers.forEach(sup => {
        // U-06：软停用（active=0）的供应商不进联想下拉
        if (sup.active === 0 || sup.active === '0' || sup.active === false) return;
        const supNameLower = (sup.name || "").toLowerCase();
        const supCodeLower = (sup.supplier_code || "").toLowerCase();

        const isExactMatch = currentVal && (supNameLower === currentVal || supCodeLower === currentVal);
        const isSimilarMatch = currentVal && (
            supNameLower.includes(currentVal) || 
            currentVal.includes(supNameLower) ||
            supCodeLower.includes(currentVal)
        );

        const itemData = {
            ...sup,
            isExactMatch,
            isSimilarMatch
        };

        if (isExactMatch || isSimilarMatch) {
            recommendedList.push(itemData);
        } else {
            regularList.push(itemData);
        }
    });

    const sortedList = [...recommendedList, ...regularList];

    let html = "";

    // 若用户输入/预填了系统库以外的新供应商，在顶部展示一键使用新名称选项
    if (rawVal && recommendedList.length === 0) {
        // P0-1：属性与文本插值统一过 w2Escape（原仅替换双引号，文本位裸拼可注入）
        const safeRaw = w2Escape(rawVal);
        html += `
            <div class="unit-dropdown-item highlight" data-supplier-name="${safeRaw}" onmousedown="selectSupplierItem(this)">
                <div>
                    <span>使用新供应商: <strong>"${safeRaw}"</strong></span>
                </div>
                <span class="badge-matched" style="color:#38bdf8; border-color:rgba(56,189,248,0.4);"> 确认使用新供应商</span>
            </div>
        `;
    }

    sortedList.forEach(sup => {
        let highlightClass = "";
        let badgeHTML = "";
        if (sup.isExactMatch) {
            highlightClass = "highlight";
            badgeHTML = `<span class="badge-matched">已选择</span>`;
        } else if (sup.isSimilarMatch) {
            highlightClass = "highlight";
            badgeHTML = `<span class="badge-matched"> 推荐匹配</span>`;
        }

        // P0-1：供应商名/编号均来自后端（可被 OCR/注册输入污染），全插值点过 w2Escape
        const codeSpan = sup.supplier_code ? `<span style="font-size:0.75rem; color:#94a3b8; font-family:monospace; margin-left:6px;">[${w2Escape(sup.supplier_code)}]</span>` : '';
        const safeName = w2Escape(sup.name || '');

        html += `
            <div class="unit-dropdown-item ${highlightClass}" data-supplier-name="${safeName}" onmousedown="selectSupplierItem(this)">
                <div>
                    <span>${safeName}</span>
                    ${codeSpan}
                </div>
                ${badgeHTML}
            </div>
        `;
    });

    if (sortedList.length === 0 && !rawVal) {
        html = `<div style="padding:10px; color:var(--text-muted); font-size:0.85rem; text-align:center;">暂无供应商</div>`;
    }

    menuElem.innerHTML = html;
}

// 智能剥离品名中混写的数量与单位（如 "菜心 20斤" -> {name: "菜心", qty: 20, unit: "斤"}）
function smartSplitItemName(rawText) {
    if (!rawText || typeof rawText !== 'string') return null;
    const text = rawText.trim();
    if (!text) return null;

    // 模式 1：乘法复合包装（如 "大豆油 5L*2樽" / "可乐 330ml*6罐" / "鲜鸡蛋 30只*3盘"）
    // 对齐后端 smart_splitter v1_2_0 MULTI_PACK_PATTERN（支持 5L*2樽回归）
    const multMatch = text.match(/^(.*?)\s*(\d+(?:\.\d+)?)\s*(ml|mL|L|l|g|G|kg|KG|斤|两|磅|lbs|oz|豪升|升|克|千克|只|粒|头|片|包|袋|瓶|听)?\s*[*xX×]\s*(\d+(?:\.\d+)?)\s*([斤公斤磅箱包罐樽扎打板只盘袋条桶瓶盒支听件]|[一-龥a-zA-Z]+)?$/i);
    if (multMatch) {
        const baseName = multMatch[1].trim();
        const specNum = multMatch[2];
        const specUnit = multMatch[3] || '';
        const count = parseFloat(multMatch[4]) || 1;
        const mainUnit = multMatch[5] || specUnit || '箱';
        const cleanName = baseName + (specNum ? ` ${specNum}${specUnit}` : '');
        return {
            cleanName: cleanName.trim(),
            quantity: count,
            unit: mainUnit
        };
    }

    // 模式 2：末尾带数量与常见单位（如 "本地菜心 20斤" / "鲜牛肉 2.5kg" / "干贝 8两"）
    const tailMatch = text.match(/^(.*?)\s+(\d+(?:\.\d+)?)\s*(司马斤|公斤|斤|两|磅|kg|g|L|ml|mL|升|箱|包|件|罐|隻|只|瓶|盒|桶|扎|把|袋|条|个)\s*$/i);
    if (tailMatch && tailMatch[1].trim()) {
        return {
            cleanName: tailMatch[1].trim(),
            quantity: parseFloat(tailMatch[2]) || 1,
            unit: tailMatch[3]
        };
    }

    return null;
}

// 行内触发智能剥离魔棒
function triggerSmartSplitRow(btnElem) {
    const tr = btnElem.closest('tr');
    if (!tr) return;
    const nameInput = tr.querySelector('.inp-name');
    if (!nameInput) return;
    const rawVal = nameInput.value || '';
    const res = smartSplitItemName(rawVal);
    if (!res) {
        showToast('未在品名中检测到明确的数量与单位', 'info');
        return;
    }

    nameInput.value = res.cleanName;
    const qtyInput = tr.querySelector('.inp-qty');
    const unitInput = tr.querySelector('.inp-unit');
    if (qtyInput) qtyInput.value = res.quantity;
    if (unitInput) unitInput.value = res.unit;

    // 自动重算单价 (unit_price = amount / quantity)
    const amtInput = tr.querySelector('.inp-amount');
    const priceInput = tr.querySelector('.inp-price');
    const amt = parseFloat(amtInput ? amtInput.value : 0) || 0;
    if (amt > 0 && res.quantity > 0 && priceInput) {
        priceInput.value = (amt / res.quantity).toFixed(2);
    }

    tr.dataset.manual = '1';
    showToast(`已成功分离：【${res.cleanName}】 数量：${res.quantity} ${res.unit}`, 'success');
}

// 明细行内联快捷新建 SKU
let activeInlineSkuInput = null;

function openInlineAddSkuModal(prefillName, inputElem) {
    activeInlineSkuInput = inputElem;
    openAddSkuModal();
    const nameInput = document.getElementById('skuModalName');
    if (nameInput) nameInput.value = prefillName || '';
    const unitInput = document.getElementById('skuModalUnit');
    const tr = inputElem ? inputElem.closest('tr') : null;
    const rowUnit = tr ? (tr.querySelector('.inp-unit')?.value || '') : '';
    if (unitInput && rowUnit) unitInput.value = rowUnit;
}

// 代理行内快捷新建点击
function triggerInlineAddSkuFromMenu(itemElem) {
    isSelectingSku = true;
    const wrap = itemElem.closest('.sku-combobox-wrap');
    const skuInput = wrap ? wrap.querySelector('.inp-sku') : null;
    const name = itemElem.getAttribute('data-prefill-name') || '';
    const menu = wrap ? wrap.querySelector('.unit-dropdown-menu') : null;
    if (menu) menu.classList.add('hide');
    openInlineAddSkuModal(name, skuInput);
    setTimeout(() => { isSelectingSku = false; }, 300);
}

// -------------------------------------------------------------
// 5.6 明细行 SKU 内联指定（D19：复核当场指定 SKU，保存后自动学别称）
// 候选来源优先级：① 该行 OCR 结果 fuzzy_candidates/entity_candidates（仅精确未命中非空）；
//               ② 输入品名/SKU 后 GET /api/inventory?q= 模糊搜索；
//               ③ 恒备「不关联（保持未关联）」。
// 安全纪律：候选文本来自 OCR/DB，一律 w2Escape；交互事件委托 + data-* 传值。
// -------------------------------------------------------------
let isSelectingSku = false;
let itemNameDebounceTimer = null;

function onItemNameInput(inputElem) {
    const tr = inputElem.closest('tr');
    if (!tr) return;
    tr.dataset.manual = '1';
    const val = (inputElem.value || '').trim();
    const wrap = tr.querySelector('.sku-combobox-wrap');
    if (!wrap) return;
    const skuInput = wrap.querySelector('.inp-sku');
    const idInput = wrap.querySelector('.inp-sku-id');
    const pill = wrap.querySelector('.sku-pill');

    if (itemNameDebounceTimer) clearTimeout(itemNameDebounceTimer);
    if (!val) {
        if (idInput) idInput.value = '';
        if (skuInput) skuInput.value = '';
        if (pill) {
            pill.className = 'sku-pill sku-pill-unlinked';
            pill.title = '未关联SKU (点击搜索关联)';
            const nameSpan = pill.querySelector('.sku-pill-name');
            if (nameSpan) nameSpan.textContent = '未关联SKU';
        }
        return;
    }

    itemNameDebounceTimer = setTimeout(() => {
        fetch('/api/inventory?q=' + encodeURIComponent(val))
        .then(res => res.json())
        .then(ret => {
            if (ret.status !== 'success') return;
            const searched = (ret.data || []).map(sku => ({
                sku_id: sku.id,
                sku_name: sku.name,
                sku_code: sku.sku_code,
                score: null,
            }));
            if (skuInput) {
                skuInput.__skuCandidates = searched;
            }
            // 若完全同名，自动静默关联
            const exact = searched.find(s => s.sku_name === val);
            if (exact) {
                if (idInput) idInput.value = exact.sku_id;
                if (skuInput) skuInput.value = exact.sku_name;
                if (pill) {
                    pill.className = 'sku-pill sku-pill-matched';
                    pill.title = '已自动匹配: ' + exact.sku_name;
                    const nameSpan = pill.querySelector('.sku-pill-name');
                    if (nameSpan) nameSpan.textContent = exact.sku_name;
                }
            }
        })
        .catch(err => console.error('SKU 联想失败:', err));
    }, 200);
}
window.onItemNameInput = onItemNameInput;

function skuMenuOf(inputElem) {
    const wrap = inputElem && inputElem.closest ? inputElem.closest('.sku-combobox-wrap') : null;
    return wrap && wrap.querySelector ? wrap.querySelector('.unit-dropdown-menu') : null;
}

function mergeSkuCandidates(primary, secondary) {
    const seen = {};
    const out = [];
    [].concat(primary || [], secondary || []).forEach(c => {
        if (!c || c.sku_id == null) return;
        const key = String(c.sku_id);
        if (seen[key]) return;
        seen[key] = true;
        out.push(c);
    });
    return out;
}

function toggleSkuMenu(pillElem) {
    const wrap = pillElem.closest('.sku-combobox-wrap');
    if (!wrap) return;
    const menu = wrap.querySelector('.unit-dropdown-menu');
    if (!menu) return;
    const isHidden = menu.classList.contains('hide');
    document.querySelectorAll('.unit-dropdown-menu').forEach(m => m.classList.add('hide'));
    if (isHidden) {
        const inpSku = wrap.querySelector('.inp-sku') || pillElem;
        renderSkuMenuItems(inpSku, menu);
        menu.classList.remove('hide');
        const searchInput = menu.querySelector('.inp-sku-search');
        if (searchInput) {
            setTimeout(() => { searchInput.focus(); searchInput.select(); }, 40);
        }
    }
}
window.toggleSkuMenu = toggleSkuMenu;

let skuSearchDebounceTimer = null;

function onSkuSearchInput(searchInput) {
    const wrap = searchInput.closest('.sku-combobox-wrap');
    if (!wrap) return;
    const inpSku = wrap.querySelector('.inp-sku');
    const val = searchInput.value;
    if (inpSku) {
        inpSku.__skuSearchQuery = val;
    }
    const menu = wrap.querySelector('.unit-dropdown-menu');
    if (!menu) return;

    if (skuSearchDebounceTimer) clearTimeout(skuSearchDebounceTimer);
    skuSearchDebounceTimer = setTimeout(() => {
        if (inpSku) renderSkuMenuItems(inpSku, menu);
    }, 100);
}
window.onSkuSearchInput = onSkuSearchInput;

function onSkuSearchKeydown(event, searchInput) {
    if (event.key === 'Enter') {
        event.preventDefault();
        const wrap = searchInput.closest('.sku-combobox-wrap');
        if (!wrap) return;
        const menu = wrap.querySelector('.unit-dropdown-menu');
        if (!menu) return;
        const firstCandidate = menu.querySelector('.unit-dropdown-item[data-sku-id]:not([data-sku-id=""])');
        if (firstCandidate) {
            selectSkuItem(firstCandidate);
        } else {
            const quickAdd = menu.querySelector('.sku-quick-add-item');
            if (quickAdd) quickCreateAndBindSku(quickAdd);
        }
    } else if (event.key === 'Escape') {
        const menu = searchInput.closest('.unit-dropdown-menu');
        if (menu) menu.classList.add('hide');
    }
}
window.onSkuSearchKeydown = onSkuSearchKeydown;

function selectSkuItem(itemElem) {
    isSelectingSku = true;
    const skuId = String(itemElem.getAttribute('data-sku-id') || '').trim();
    const skuName = itemElem.getAttribute('data-sku-name') || '';
    const wrap = itemElem.closest('.sku-combobox-wrap');
    if (wrap) {
        const skuInput = wrap.querySelector('.inp-sku');
        const idInput = wrap.querySelector('.inp-sku-id');
        if (skuInput) skuInput.value = skuId ? skuName : '';
        if (idInput) idInput.value = skuId;
        const badge = wrap.querySelector('.sku-badge');
        if (badge) {
            badge.textContent = skuId ? '已匹配SKU' : '未关联';
            badge.className = 'badge ' + (skuId ? 'badge-success' : 'badge-warning') + ' sku-badge hide';
        }
        const pill = wrap.querySelector('.sku-pill');
        if (pill) {
            pill.className = 'sku-pill ' + (skuId ? 'sku-pill-matched' : 'sku-pill-unlinked');
            pill.title = skuId ? ('已匹配: ' + skuName) : '未关联SKU (点击搜索关联)';
            const nameSpan = pill.querySelector('.sku-pill-name');
            if (nameSpan) {
                nameSpan.textContent = skuId ? (skuName || '已匹配SKU') : '未关联SKU';
            }
        }
        const menu = wrap.querySelector('.unit-dropdown-menu');
        if (menu) menu.classList.add('hide');
    }
    setTimeout(() => { isSelectingSku = false; }, 200);
}

// 1-Click 极速一键建档绑定（无需打开繁琐 Modal）
function quickCreateAndBindSku(itemElem) {
    isSelectingSku = true;
    const rawName = itemElem.getAttribute('data-prefill-name') || '';
    const cleanName = rawName.trim();
    if (!cleanName) return;
    const wrap = itemElem.closest('.sku-combobox-wrap');
    const tr = itemElem.closest('tr');
    const rowUnit = tr ? (tr.querySelector('.inp-unit')?.value || '斤') : '斤';

    fetch('/api/inventory/skus', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            name: cleanName,
            category: '食材',
            base_unit: rowUnit,
            min_stock_alert: 5.0
        })
    })
    .then(res => res.json())
    .then(ret => {
        const targetId = ret.id || ret.existing_id;
        if (ret.status === 'success' || targetId) {
            if (wrap) {
                const skuInput = wrap.querySelector('.inp-sku');
                const idInput = wrap.querySelector('.inp-sku-id');
                if (skuInput) skuInput.value = cleanName;
                if (idInput) idInput.value = targetId || '';
                const pill = wrap.querySelector('.sku-pill');
                if (pill) {
                    pill.className = 'sku-pill sku-pill-matched';
                    pill.title = '已建档: ' + cleanName;
                    const nameSpan = pill.querySelector('.sku-pill-name');
                    if (nameSpan) nameSpan.textContent = cleanName;
                }
                const menu = wrap.querySelector('.unit-dropdown-menu');
                if (menu) menu.classList.add('hide');
            }
            showToast(`已一键建档并关联「${cleanName}」`, 'success');
            if (typeof loadInventoryData === 'function') loadInventoryData();
        } else {
            showToast('建档提示: ' + (ret.message || '请重试'), 'warning');
        }
    })
    .catch(err => {
        console.error('一键建档失败:', err);
        showToast('建档网络异常', 'error');
    })
    .finally(() => {
        setTimeout(() => { isSelectingSku = false; }, 200);
    });
}
window.quickCreateAndBindSku = quickCreateAndBindSku;

function formatSkuScore(score) {
    const n = Number(score);
    if (!isFinite(n) || n == null) return '';
    if (n >= 0 && n <= 1) return ' 匹配' + Math.round(n * 100) + '%';
    return ' 相似度' + Math.round(n) + '%';
}

function buildSkuDropdownItemsHtml(inputElem, candidates) {
    const query = (inputElem.__skuSearchQuery !== undefined ? inputElem.__skuSearchQuery : (inputElem.value || '')).trim();
    const rows = candidates || [];
    const tr = inputElem.closest('tr');
    const rowName = query || (tr ? (tr.querySelector('.inp-name')?.value || '') : '');
    const cleanRowName = rowName ? rowName.trim() : '';

    let itemsHtml = '';
    if (cleanRowName) {
        const safeName = w2Escape(cleanRowName);
        itemsHtml += `
            <div class="unit-dropdown-item sku-quick-add-item" data-prefill-name="${safeName}" onmousedown="quickCreateAndBindSku(this)" style="background:rgba(47,107,79,0.06); border-bottom:1px solid #e2e8f0;">
                <div><span style="font-weight:600; color:var(--primary);">＋ 一键为「${safeName}」建档入库</span></div>
                <span class="badge-matched" style="background:var(--primary); color:#fff; font-size:0.72rem; padding:2px 8px; border-radius:4px;">1秒建档</span>
            </div>
        `;
    }

    if (rows.length > 0) {
        rows.forEach(c => {
            const codeSpan = c.sku_code
                ? `<span style="font-size:0.75rem; color:#94a3b8; font-family:monospace; margin-left:6px;">[${w2Escape(c.sku_code)}]</span>`
                : '';
            const scoreSpan = formatSkuScore(c.score)
                ? `<span style="font-size:0.72rem; color:#2f6b4f; font-weight:600; margin-left:6px;">${w2Escape(formatSkuScore(c.score))}</span>`
                : '';
            itemsHtml += `
                <div class="unit-dropdown-item" data-sku-id="${w2Escape(c.sku_id)}" data-sku-name="${w2Escape(c.sku_name || '')}" onmousedown="selectSkuItem(this)">
                    <div>
                        <span style="font-weight:500;">${w2Escape(c.sku_name || '')}</span>
                        ${codeSpan}
                        ${scoreSpan}
                    </div>
                    <span class="badge-matched" style="font-size:0.72rem; padding:1px 6px;">选择</span>
                </div>
            `;
        });
    }

    itemsHtml += `
        <div class="unit-dropdown-item" data-sku-id="" data-sku-name="" onmousedown="selectSkuItem(this)" style="border-top:1px solid #f1f5f9; color:var(--text-muted);">
            <div><span>设为未关联 (临时消费单)</span></div>
        </div>
    `;
    return itemsHtml;
}

function renderSkuDropdownScrollOnly(inputElem, menuElem, candidates) {
    const scrollElem = menuElem.querySelector('.sku-dropdown-scroll');
    if (scrollElem) {
        scrollElem.innerHTML = buildSkuDropdownItemsHtml(inputElem, candidates);
    }
}

function renderSkuMenuItems(inputElem, menuElem) {
    const query = (inputElem.__skuSearchQuery !== undefined ? inputElem.__skuSearchQuery : (inputElem.value || '')).trim();

    if (query && query !== inputElem.__skuLastFetchedQuery) {
        inputElem.__skuLastFetchedQuery = query;
        fetch('/api/inventory?q=' + encodeURIComponent(query))
        .then(res => res.json())
        .then(ret => {
            if (ret.status !== 'success') return;
            const searched = (ret.data || []).map(sku => ({
                sku_id: sku.id,
                sku_name: sku.name,
                sku_code: sku.sku_code,
                score: null,
            }));
            inputElem.__skuCandidates = searched;
            if (!menuElem.classList.contains('hide')) {
                renderSkuDropdownScrollOnly(inputElem, menuElem, inputElem.__skuCandidates);
            }
        })
        .catch(err => console.error('SKU 候选搜索失败:', err));
    }

    renderSkuDropdownHtml(inputElem, menuElem, inputElem.__skuCandidates || []);
}

function renderSkuDropdownHtml(inputElem, menuElem, candidates) {
    const query = (inputElem.__skuSearchQuery !== undefined ? inputElem.__skuSearchQuery : (inputElem.value || '')).trim();
    const existingSearch = menuElem.querySelector('.inp-sku-search');
    if (existingSearch && !menuElem.classList.contains('hide')) {
        renderSkuDropdownScrollOnly(inputElem, menuElem, candidates);
        return;
    }

    const itemsHtml = buildSkuDropdownItemsHtml(inputElem, candidates);
    let html = `
        <div class="sku-search-header" style="padding:6px 8px; border-bottom:1px solid var(--border-color, #e5e7eb);" onclick="event.stopPropagation()">
            <input type="text" class="form-control inp-sku-search" placeholder="输入名称或回车快速匹配..."
                   value="${w2Escape(query)}"
                   oninput="onSkuSearchInput(this)" onkeydown="onSkuSearchKeydown(event, this)" onclick="event.stopPropagation()"
                   style="font-size:0.82rem; padding:5px 8px; width:100%; box-sizing:border-box; border-radius:6px;">
        </div>
        <div class="sku-dropdown-scroll" style="max-height:220px; overflow-y:auto;">
            ${itemsHtml}
        </div>
    `;
    menuElem.innerHTML = html;
}

// -------------------------------------------------------------
// 6. 右侧 Prefill 渲染与动态表格编辑
// -------------------------------------------------------------
function renderEditForm(data) {
    if (!data) data = {};
    document.getElementById('inpSupplier').value = data.supplier_name || '';
    document.getElementById('inpDate').value = data.date || '';
    document.getElementById('inpSheet').value = data.sheet_name || (data.date ? data.date.slice(0, 7) : '');
    const rawTotal = (data.total_amount != null) ? data.total_amount : ((data.total != null) ? data.total : 0);
    document.getElementById('inpTotal').value = Number(rawTotal).toFixed(2);

    // 单据形态与币种
    const docFormElem = document.getElementById('inpDocForm');
    if (docFormElem) {
        docFormElem.value = data.doc_form || 'printed_delivery_note';
    }
    const curElem = document.getElementById('inpCurrency');
    if (curElem) {
        curElem.value = data.currency || 'HKD';
    }

    // 附加费用
    const discElem = document.getElementById('inpDiscount');
    if (discElem) discElem.value = (data.discount_amount != null && data.discount_amount > 0) ? Number(data.discount_amount).toFixed(2) : '0.00';
    const delivElem = document.getElementById('inpDeliveryFee');
    if (delivElem) delivElem.value = (data.delivery_fee != null && data.delivery_fee > 0) ? Number(data.delivery_fee).toFixed(2) : '0.00';
    const depElem = document.getElementById('inpDeposit');
    if (depElem) depElem.value = (data.deposit_amount != null && data.deposit_amount > 0) ? Number(data.deposit_amount).toFixed(2) : '0.00';
    const roundElem = document.getElementById('inpRounding');
    if (roundElem) roundElem.value = (data.rounding_adjustment != null && data.rounding_adjustment > 0) ? Number(data.rounding_adjustment).toFixed(2) : '0.00';

    updateFeesSummaryBadge();

    // M3/D4: 从 prefill 数据初始化结算方式与付款标记
    applySettlementToForm('inp', data);

    // Wave 2（D44）：单据级部门下拉
    populateDeptSelect(document.getElementById('inpDepartmentId'), data.department_id);

    const alertBanner = document.getElementById('alertBanner');
    if (data.math_warnings && data.math_warnings.length > 0) {
        alertBanner.innerText = data.math_warnings.join(" | ");
        alertBanner.classList.remove('hide');
    } else {
        alertBanner.classList.add('hide');
    }

    const tbody = document.getElementById('itemTableBody');
    tbody.innerHTML = '';

    const items = (data.items || []).filter(item =>
        String((item && (item.raw_name || item.name)) || '').trim() !== '');
    items.forEach((item) => {
        appendTableRow(item);
    });

    showQualityWarnings(data.quality_warnings);
    renderCurrencySymbol();
}

function toggleFeesDrawer() {
    const drawer = document.getElementById('feesDrawerContent');
    const icon = document.getElementById('feesToggleIcon');
    if (!drawer) return;
    const isHidden = drawer.classList.contains('hide');
    if (isHidden) {
        drawer.classList.remove('hide');
        if (icon) icon.textContent = '▲';
    } else {
        drawer.classList.add('hide');
        if (icon) icon.textContent = '▾';
    }
}
window.toggleFeesDrawer = toggleFeesDrawer;

function updateFeesSummaryBadge() {
    const disc = parseFloat(document.getElementById('inpDiscount')?.value) || 0;
    const deliv = parseFloat(document.getElementById('inpDeliveryFee')?.value) || 0;
    const dep = parseFloat(document.getElementById('inpDeposit')?.value) || 0;
    const round = parseFloat(document.getElementById('inpRounding')?.value) || 0;
    const badge = document.getElementById('feesSummaryBadge');
    if (!badge) return;
    const hasFees = (disc > 0 || deliv > 0 || dep > 0 || round > 0);
    if (hasFees) {
        const netFee = (deliv + dep - disc - round);
        const sign = netFee >= 0 ? '+' : '';
        badge.textContent = `差额: ${sign}${netFee.toFixed(2)}`;
        badge.classList.remove('hide');
    } else {
        badge.classList.add('hide');
    }
}
window.updateFeesSummaryBadge = updateFeesSummaryBadge;

function autoFillSheetNameFromDate(dateVal) {
    const sheetInp = document.getElementById('inpSheet');
    if (sheetInp && (!sheetInp.value || sheetInp.value.trim() === '')) {
        if (dateVal && dateVal.length >= 7) {
            sheetInp.value = dateVal.slice(0, 7);
        }
    }
}
window.autoFillSheetNameFromDate = autoFillSheetNameFromDate;

// -------------------------------------------------------------
// 6b. 结算方式与付款标记 (M3/D4)：字段可能暂时缺失，须容错
// -------------------------------------------------------------

// 付款标记展示映射：兼容后端可能返回的中/英文枚举
function formatPaymentMark(mark) {
    if (mark == null || mark === '') return '无';
    const labelMap = {
        'stamp': '印章', 'seal': '印章', '印章': '印章',
        'handwritten': '手写批注', 'handwritten_note': '手写批注', 'annotation': '手写批注', '手写批注': '手写批注',
        'signature_only': '仅签名', 'signature': '仅签名', '仅签名': '仅签名',
        'paid': '已付款', '已付款': '已付款',
        'none': '无', '无': '无'
    };
    const key = String(mark).trim().toLowerCase();
    return labelMap[key] || String(mark).trim();
}

// prefix='inp' → Tab1 复核表单；prefix='arc' → 归档弹窗
function applySettlementToForm(prefix, data) {
    const d = data || {};
    const sel = document.getElementById(prefix + 'SettlementType');
    if (sel) {
        // AI 判定 cash/credit 原样回填，其余（null/缺失/未知枚举）一律显示"未知"
        sel.value = (d.settlement_type === 'cash' || d.settlement_type === 'credit') ? d.settlement_type : '';
    }
    const markInput = document.getElementById(prefix + 'PaymentMark');
    if (markInput) {
        // U-05：付款标记枚举下拉——后端值映射到最近枚举项，未匹配则追加临时 option 显示原值
        const mapped = formatPaymentMark(d.payment_mark);
        if (markInput.options) {
            if (![...markInput.options].some(o => o.value === mapped)) {
                markInput.appendChild(new Option(mapped, mapped, true, true));
            }
        }
        markInput.value = mapped;
    }
}

// -------------------------------------------------------------
// 6c. 全局轻量 Toast（渐进提示/轻提示用，不打断当前操作）
// 产品规范 §5/§7：时长走命名常量，禁止调用点发明魔法数；
// 运行时统一做 trim / type 白名单 / 去重 / 队列上限 / 文本转义。
// -------------------------------------------------------------

// §5 命名时长表（毫秒）。long / guide 为白名单场景：
//   long  → 合并完成、对账确认等需要多读一眼的结果类提示
//   guide → 成功 + 一句「接下来做什么」的引导
const TOAST_DURATION = Object.freeze({
    success: 2500,
    info: 3500,
    warning: 4000,
    error: 5000,
    long: 6000,
    guide: 5000,
});

// §6.2 失败提示：以固定短句为主，后端 msg 只作补充——截断 80 字，
// 且疑似堆栈/多行/技术前缀一律丢弃（详情走 console，不抛给用户）。
function toastFailDetail(msg, limit = 80) {
    const s = String(msg ?? '').trim();
    if (!s || s.indexOf('\n') !== -1 || /(^|\s)(Error|Traceback|at\s+\w+)/.test(s)) return '';
    return '：' + (s.length > limit ? s.slice(0, limit) + '…' : s);
}

const TOAST_TYPES = Object.freeze(['info', 'success', 'warning', 'error']);
const TOAST_MAX_VISIBLE = 3;      // §7.2 队列上限：同时最多 3 条
const TOAST_OUT_MS = 300;         // 与 .app-toast-out 的 transition 对齐

// 展示窗口内在场的 toast：key(type+message 或显式 id) → { el, timer }
const _toastLive = new Map();

function _toastContainer() {
    let container = document.getElementById('toastContainer');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toastContainer';
        container.className = 'toast-container';
        document.body.appendChild(container);
    }
    // §7.3 a11y：屏幕阅读器以「礼貌」方式播报，不抢断当前朗读
    container.setAttribute('role', 'status');
    container.setAttribute('aria-live', 'polite');
    container.setAttribute('aria-atomic', 'false');
    return container;
}

function _toastDismiss(key, immediate = false) {
    const rec = _toastLive.get(key);
    if (!rec) return;
    _toastLive.delete(key);
    if (rec.timer) clearTimeout(rec.timer);
    const el = rec.el;
    if (!el) return;
    if (immediate) { el.remove(); return; }
    el.classList.add('app-toast-out');
    setTimeout(() => el.remove(), TOAST_OUT_MS);
}

// 超出上限时移除最旧的一条（按进入顺序，Map 保序）
function _toastEnforceLimit() {
    while (_toastLive.size > TOAST_MAX_VISIBLE) {
        const oldest = _toastLive.keys().next();
        if (oldest.done) break;
        _toastDismiss(oldest.value);
    }
}

/**
 * 全局轻提示。
 * @param {string} message 文本（自动 trim；空串不展示）
 * @param {'info'|'success'|'warning'|'error'} [type='info'] 非白名单值降级为 info
 * @param {number|{duration?:number, action?:{label?:string,text?:string,onClick?:Function,handler?:Function}, id?:string}} [durationOrOpts]
 *        number 形态为历史签名（时长覆盖，逐步淘汰）；新调用点请用 TOAST_DURATION 常量或对象形态。
 * @returns {HTMLElement|null} 展示的条目；未展示时返回 null
 */
function showToast(message, type = 'info', durationOrOpts) {
    // §7.2 trim：空串不展示
    const text = String(message ?? '').trim();
    if (!text) return null;

    // §7.2 type 白名单：非法 → info
    const safeType = TOAST_TYPES.indexOf(type) !== -1 ? type : 'info';

    // §7 向后兼容：number = 时长覆盖；对象 = { duration, action, id }
    let opts = {};
    if (typeof durationOrOpts === 'number') opts = { duration: durationOrOpts };
    else if (durationOrOpts && typeof durationOrOpts === 'object') opts = durationOrOpts;

    const override = Number(opts.duration);
    const duration = (Number.isFinite(override) && override > 0)
        ? override
        : (TOAST_DURATION[safeType] || TOAST_DURATION.info);

    const key = opts.id ? ('id:' + opts.id) : (safeType + '\u0000' + text);

    // §7.2 去重：同一 type+message 在展示窗口内只刷新计时，不叠第二条
    const exist = _toastLive.get(key);
    if (exist && exist.el && exist.el.isConnected) {
        if (exist.timer) clearTimeout(exist.timer);
        exist.el.classList.remove('app-toast-out');
        exist.timer = setTimeout(() => _toastDismiss(key), duration);
        return exist.el;
    }
    if (exist) _toastLive.delete(key);

    const container = _toastContainer();

    const toast = document.createElement('div');
    toast.className = `app-toast app-toast-${safeType}`;

    // §7.2 安全：一律走文本节点 / innerText，永不拼 innerHTML
    const msgEl = document.createElement('span');
    msgEl.className = 'app-toast-msg';
    msgEl.innerText = text;
    toast.appendChild(msgEl);

    // 可选行动点：标签同样只用文本节点转义
    const action = opts.action;
    if (action && typeof action === 'object') {
        const label = String(action.label ?? action.text ?? '').trim();
        const onClick = action.onClick || action.handler;
        if (label && typeof onClick === 'function') {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'app-toast-action';
            btn.innerText = label;
            btn.addEventListener('click', () => {
                _toastDismiss(key);
                try { onClick(); } catch (e) { console.error('[toast] action failed', e); }
            });
            toast.appendChild(btn);
        }
    }

    // 可选关闭：只有 × 吃 pointer-events，正文照旧「不打断」
    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'app-toast-close';
    closeBtn.setAttribute('aria-label', '关闭提示');
    closeBtn.innerText = '\u00d7';
    closeBtn.addEventListener('click', () => _toastDismiss(key));
    toast.appendChild(closeBtn);

    container.appendChild(toast);

    const rec = { el: toast, timer: null };
    rec.timer = setTimeout(() => _toastDismiss(key), duration);
    _toastLive.set(key, rec);
    _toastEnforceLimit();

    return toast;
}

// E-P1-4 403 人话统一：后端返回含所需角色指引的 detail，前端统一按此文案提示
function humanizeForbidden(detail, status) {
    const raw = String(detail || '').trim();
    // 已是后端人话文案（含角色指引）→ 原样透传
    if (raw.includes('权限不足')) return raw;
    if (status != null && status !== 403) return null;
    if (raw.includes('仅 admin')) {
        return '权限不足：此操作仅限超管（admin）执行，请切换为 admin 角色。';
    }
    if (raw) return '权限不足：' + raw + '。请切换角色或联系管理员。';
    return '权限不足：当前角色无权执行此操作，请切换角色或联系管理员。';
}

// U-10 文案去技术化：将 Pydantic 422 / 后端 400 / 500 / 堆栈错误翻译为店员人话
function humanizeBackendError(status, body) {
    if (status === 403) {
        const msg = (body && (body.detail || body.msg)) || '';
        return humanizeForbidden(msg, status);
    }
    // 422 校验错误 (FastAPI / Pydantic)
    if (status === 422 || (body && Array.isArray(body.detail))) {
        const details = Array.isArray(body.detail) ? body.detail : [];
        if (details.length > 0) {
            const first = details[0];
            const loc = Array.isArray(first.loc) ? first.loc.map(String) : [];
            const field = loc[loc.length - 1] || '';
            const parentField = loc[loc.length - 2] || '';
            const rowIdx = !isNaN(Number(parentField)) ? Number(parentField) + 1 : (!isNaN(Number(field)) ? Number(field) + 1 : null);

            if (field === 'supplier_name') return '请填写供应商名称';
            if (field === 'date' || field === 'receipt_date') return '请选择开单日期';
            if (field === 'total_amount') return '单据总金额必须大于 0';
            if (field === 'items') return '请至少输入一行消费明细';
            if (field === 'settlement_type') return '请选择结算方式（如现结或挂账）';
            if (field === 'name' || field === 'raw_name') {
                return rowIdx ? `第 ${rowIdx} 行的品名不能为空，请填写或删除该行` : '请填写明细品名';
            }
            if (field === 'quantity' || field === 'unit_price' || field === 'amount') {
                return rowIdx ? `第 ${rowIdx} 行的单价或数量不是有效数字，请重新输入` : '明细单价或数量不是有效数字，请重新输入';
            }
            if (first.msg) {
                const m = String(first.msg);
                if (m.includes('greater than 0')) return '单据总金额必须大于 0';
                if (m.includes('Field required') || m.includes('missing')) return '请完整填写必填项';
                if (m.includes('valid boolean')) return '输入类型有误，请核对后重试';
            }
        }
        return '输入内容格式有误，请核对后重试';
    }
    // 400 / 业务错误
    if (status === 400 || (body && body.code)) {
        if (body && body.code === 'SETTLEMENT_REQUIRED') {
            return '请选择结算方式（如现结或挂账）';
        }
        if (body && body.code === 'VERSION_REQUIRED') {
            return '缺少版本号，已重新加载最新内容';
        }
        if (body && body.code === 'VERSION_CONFLICT') {
            return '该单据已被他人修改，请重新加载最新内容';
        }
        if (body && body.code === 'IMAGE_QUALITY_ERROR') {
            return '图片有点模糊或光线不足，请重新拍摄或调整后再试';
        }
    }
    // 检查文案中的技术术语或英文错误，转化为通俗人话
    let raw = '';
    if (body) {
        if (typeof body === 'string') raw = body;
        else raw = String(body.msg || body.detail || body.message || '');
    }
    if (raw) {
        if (raw.includes('supplier_name') || raw.includes('真实供应商名称') || raw.includes('通用供应商')) {
            return '请填写供应商名称';
        }
        if (raw.includes('合法开单日期') || raw.includes('未填写开单日期') || raw.includes('receipt_date')) {
            return '请选择开单日期';
        }
        if (raw.includes('不是真实存在的日期') || raw.includes('开单日期不正确')) {
            return '开单日期不正确，请选择真实存在的日历日期';
        }
        if (raw.includes('total_amount') || raw.includes('总金额必须大于 0') || raw.includes('<= 0')) {
            return '单据总金额必须大于 0';
        }
        if (raw.includes('至少一条消费明细') || raw.includes('至少包含一条') || raw.includes('至少输入一行') || raw.includes('至少保留一条')) {
            return '请至少输入一行消费明细';
        }
        if (raw.includes('结算方式')) {
            return '请选择结算方式（如现结或挂账）';
        }
        if (raw.includes('品名不能为空')) {
            return raw;
        }
        if (raw.includes('不是有效数字')) {
            return raw;
        }
        if (/Traceback|JSONDecodeError|pydantic|ValidationError|Internal Server/i.test(raw)) {
            return '服务处理异常，请稍后重试';
        }
    }
    if (status >= 500) {
        return 'AI 服务现在很忙，请稍后重试';
    }
    return null;
}

function toastHttpError(status, body) {
    const human = humanizeBackendError(status, body);
    if (human) { showToast(human, 'error'); return true; }
    return false;
}

// §7.1 语义糖（可选、向后兼容）：toast.success('已保存') / toast.error(msg, TOAST_DURATION.long)
const toast = {
    info: (message, durationOrOpts) => showToast(message, 'info', durationOrOpts),
    success: (message, durationOrOpts) => showToast(message, 'success', durationOrOpts),
    warning: (message, durationOrOpts) => showToast(message, 'warning', durationOrOpts),
    error: (message, durationOrOpts) => showToast(message, 'error', durationOrOpts),
    dismissAll: () => { Array.from(_toastLive.keys()).forEach(k => _toastDismiss(k)); },
};

function appendTableRow(item = {}) {
    const tbody = document.getElementById('itemTableBody');

    const rawName = item.name || item.raw_name || '';
    const qty = Number(item.quantity || 1.0);
    const unit = item.raw_unit || item.unit || 'kg';
    const price = Number(item.unit_price || 0.00);
    const amount = Number(item.amount != null ? item.amount : (qty * price));
    const skuId = item.sku_id || '';
    const isAnomaly = item.price_anomaly || false;
    const actualQtyVal = (item.actual_qty != null && item.actual_qty !== '') ? String(item.actual_qty) : '';

    const rowWarnings = [];
    if (typeof item.confidence === 'number' && item.confidence <= 0.40) {
        rowWarnings.push('低置信度');
    }
    if (item.unit_conversion_warning) {
        rowWarnings.push('单位不可折算');
    }
    if (item.matched === false && !item.sku_id) {
        rowWarnings.push('SKU未匹配');
    }

    try {
        const _qws = (typeof currentReceiptData !== 'undefined' && currentReceiptData && Array.isArray(currentReceiptData.quality_warnings)) ? currentReceiptData.quality_warnings : null;
        if (_qws && _qws.length > 0) {
            rowWarnings.push('质量预检');
        }
        const _rps = (typeof currentReceiptData !== 'undefined' && currentReceiptData && typeof currentReceiptData.review_priority_score === 'number') ? currentReceiptData.review_priority_score : null;
        if (_rps != null && _rps > 0.6) {
            rowWarnings.push('重点复核');
        }
    } catch (e) {}

    const rowWarnClass = rowWarnings.length ? ' row-warning' : '';
    const tr = document.createElement('tr');
    tr.className = rowWarnClass;

    const skuName = item.sku_name || '';
    const skuCandidates = mergeSkuCandidates(
        (item.fuzzy_candidates || []).map(c => ({
            sku_id: c.sku_id,
            sku_name: c.sku_name || c.matched_text || '',
            sku_code: c.sku_code,
            score: c.score,
        })),
        (item.entity_candidates || []).map(c => ({
            sku_id: c.sku_id,
            sku_name: c.sku_name || '',
            sku_code: c.sku_code,
            score: c.score,
        }))
    );

    if (unit && !availableUnits.includes(unit)) {
        availableUnits.push(unit);
        updateGlobalDatalistUnits();
    }

    let finalRawName = rawName;
    let finalQty = qty;
    let finalUnit = unit;
    let finalPrice = price;
    const splitRes = smartSplitItemName(rawName);
    if (splitRes && (qty === 1 || !item.quantity)) {
        finalRawName = splitRes.cleanName;
        finalQty = splitRes.quantity;
        finalUnit = splitRes.unit || unit;
        if (amount > 0 && finalQty > 0) {
            finalPrice = parseFloat((amount / finalQty).toFixed(2));
        }
    }

    const isVoidMain = !!(item.is_void);
    if (isVoidMain) {
        tr.dataset.isVoid = '1';
        tr.style.opacity = '0.5';
    }

    const anomalyHtml = isAnomaly ? `
        <span class="badge ${item.price_anomaly_direction === 'down' ? 'badge-info' : 'badge-danger'} anomaly-pill" title="价格偏离历史均值">
            ${item.price_anomaly_direction === 'down' ? '↓ 偏低' : '↑ 偏高'}${Number(item.price_diff_percent || 10).toFixed(1)}%
        </span>
    ` : '';

    tr.innerHTML = `
        <td>
            <div class="item-name-sku-stack">
                <div class="item-name-top">
                    <input type="text" class="inp-name form-control" value="${w2Escape(finalRawName)}" placeholder="品名(如走地鸡)" autocomplete="off" oninput="onItemNameInput(this)" ${isVoidMain ? 'style="text-decoration:line-through; color:#94a3b8;"' : ''}>
                </div>
                <div class="sku-capsule-wrapper sku-combobox-wrap">
                    <div class="sku-pill ${skuId ? 'sku-pill-matched' : 'sku-pill-unlinked'}" onclick="toggleSkuMenu(this)" title="${skuId ? ('已匹配: ' + w2Escape(skuName)) : '未关联SKU (点击搜索关联)'}" ${isVoidMain ? 'style="pointer-events:none; opacity:0.6;"' : ''}>
                        <span class="sku-pill-dot"></span>
                        <span class="sku-pill-name">${w2Escape(skuId ? (skuName ? skuName : '已匹配SKU') : '未关联SKU')}</span>
                        <span class="sku-pill-arrow">▾</span>
                    </div>
                    <input type="hidden" class="inp-sku" value="${w2Escape(skuName)}">
                    <input type="hidden" class="inp-sku-id" value="${skuId || ''}">
                    <span class="badge ${skuId ? 'badge-success' : 'badge-warning'} sku-badge hide">${skuId ? '已匹配SKU' : '未关联'}</span>
                    <div class="unit-dropdown-menu sku-dropdown-floating hide"></div>
                </div>
            </div>
        </td>
        <td>
            <div style="display:flex; flex-direction:column; gap:4px;">
                <input type="number" step="0.01" class="inp-qty form-control text-right" value="${finalQty}" oninput="recalcRow(this)" title="数量" placeholder="数量" ${isVoidMain ? 'disabled style="text-decoration:line-through;"' : ''}>
                <input type="number" step="0.01" class="inp-actual-qty form-control text-right" value="${w2Escape(actualQtyVal)}" placeholder="实收" title="实际数量（手写改量，留空则按数量计）" style="font-size:0.78rem; padding:4px 6px;" oninput="recalcRow(this)" ${isVoidMain ? 'disabled style="text-decoration:line-through;"' : ''}>
            </div>
        </td>
        <td>
            <div class="unit-combobox-wrap">
                <input type="text" class="inp-unit form-control text-center" value="${w2Escape(finalUnit)}" placeholder="单位" onfocus="this.select(); openUnitMenu(this)" onclick="openUnitMenu(this)" oninput="renderUnitMenuItems(this, this.nextElementSibling)" onblur="closeUnitMenuDelay(this)" ${isVoidMain ? 'style="text-decoration:line-through; color:#94a3b8;"' : ''}>
                <div class="unit-dropdown-menu hide"></div>
            </div>
        </td>
        <td>
            <div class="price-input-wrap">
                <input type="number" step="0.01" class="inp-price form-control text-right" value="${finalPrice}" oninput="recalcRow(this)" ${isVoidMain ? 'disabled' : ''}>
                ${anomalyHtml}
            </div>
        </td>
        <td>
            <input type="number" step="0.01" class="inp-amount form-control text-right" value="${amount.toFixed(2)}" oninput="recalcTotalSum()" ${isVoidMain ? 'disabled' : ''}>
        </td>
        <td>
            <select class="inp-dept form-control" title="本行归属部门" style="width:100%; padding:6px; font-size:0.82rem;" ${isVoidMain ? 'disabled' : ''}>
                ${deptSelectOptionsHtml(item.cost_center_id)}
            </select>
        </td>
        <td style="text-align:center;">
            <div class="row-actions">
                <button type="button" class="btn-action-void ${isVoidMain ? 'is-void' : ''}" onclick="toggleRowVoid(this)" title="${isVoidMain ? '恢复此行' : '作废此行 (划线不计入总额)'}">${isVoidMain ? '恢复' : '作废'}</button>
                <button type="button" class="btn-action-delete" onclick="removeRow(this)" title="删除此明细行">删除</button>
            </div>
        </td>
    `;
    tbody.appendChild(tr);

    const skuInput = tr.querySelector('.inp-sku');
    if (skuInput) skuInput.__skuCandidates = skuCandidates;

    tr.addEventListener('input', () => { tr.dataset.manual = '1'; });
    tr.addEventListener('change', () => { tr.dataset.manual = '1'; });
}

function addEmptyRow() {
    appendTableRow({ raw_name: '', quantity: 1.0, raw_unit: 'kg', unit_price: 0.0, amount: 0.0 });
    const container = document.querySelector('#prefillFormCard .table-container');
    if (container) {
        container.scrollTop = container.scrollHeight;
    }
}

function removeRow(btn) {
    const tr = btn.closest('tr');
    if (tr) tr.remove();
    recalcTotalSum();
}

function toggleRowVoid(btn) {
    const tr = btn.closest('tr');
    if (!tr) return;
    const isVoid = tr.dataset && tr.dataset.isVoid === '1';
    const newVoid = !isVoid;
    tr.dataset.isVoid = newVoid ? '1' : '0';
    tr.style.opacity = newVoid ? '0.5' : '';

    btn.classList.toggle('is-void', newVoid);
    btn.textContent = newVoid ? '恢复' : '作废';
    btn.title = newVoid ? '恢复此行' : '作废此行 (划线不计入总额)';

    tr.querySelectorAll('.inp-name, .inp-unit').forEach(inp => {
        inp.style.textDecoration = newVoid ? 'line-through' : '';
        inp.style.color = newVoid ? '#94a3b8' : '';
    });
    tr.querySelectorAll('.inp-qty, .inp-actual-qty, .inp-price, .inp-amount, .inp-dept').forEach(inp => {
        inp.disabled = newVoid;
        if (inp.classList.contains('inp-qty') || inp.classList.contains('inp-actual-qty')) {
            inp.style.textDecoration = newVoid ? 'line-through' : '';
        }
    });
    const pill = tr.querySelector('.sku-pill');
    if (pill) {
        pill.style.pointerEvents = newVoid ? 'none' : '';
        pill.style.opacity = newVoid ? '0.6' : '';
    }

    recalcTotalSum();
}
window.toggleRowVoid = toggleRowVoid;

function toggleMainVoid(cb) {
    const tr = cb.closest('tr');
    if (!tr) return;
    const voidBtn = tr.querySelector('.btn-action-void');
    if (voidBtn) {
        toggleRowVoid(voidBtn);
    } else {
        const isVoid = cb.checked;
        tr.dataset.isVoid = isVoid ? '1' : '0';
        tr.style.opacity = isVoid ? '0.55' : '';
        recalcTotalSum();
    }
}

function recalcRow(inputElem) {
    const tr = inputElem.closest('tr');
    const qty = parseFloat(tr.querySelector('.inp-qty').value) || 0;
    const price = parseFloat(tr.querySelector('.inp-price').value) || 0;

    const amtInput = tr.querySelector('.inp-amount');
    amtInput.value = (qty * price).toFixed(2);

    recalcTotalSum();
}

function recalcTotalSum() {
    const tbody = document.getElementById('itemTableBody');
    const rows = tbody.querySelectorAll('tr');
    let itemsSum = 0.0;
    rows.forEach(tr => {
        if (tr.dataset && tr.dataset.isVoid === '1') return;
        const amt = parseFloat(tr.querySelector('.inp-amount')?.value) || 0;
        itemsSum += amt;
    });

    const discount = parseFloat(document.getElementById('inpDiscount')?.value) || 0;
    const delivery = parseFloat(document.getElementById('inpDeliveryFee')?.value) || 0;
    const deposit = parseFloat(document.getElementById('inpDeposit')?.value) || 0;
    const rounding = parseFloat(document.getElementById('inpRounding')?.value) || 0;

    const netTotal = Math.max(0, itemsSum - discount - rounding + delivery + deposit);
    document.getElementById('inpTotal').value = netTotal.toFixed(2);
    updateFeesSummaryBadge();
    renderCurrencySymbol();
}

// 展开/收起 Prefill 表头更多元数据
function togglePrefillMoreMeta() {
    const box = document.getElementById('prefillMoreMetaBox');
    const text = document.getElementById('moreMetaToggleText');
    const icon = document.getElementById('moreMetaToggleIcon');
    if (!box) return;
    const isHidden = box.classList.contains('hide');
    if (isHidden) {
        box.classList.remove('hide');
        if (text) text.textContent = '收起辅助字段';
        if (icon) icon.textContent = '▲';
    } else {
        box.classList.add('hide');
        if (text) text.textContent = '更多字段 (币种/付款/归档)';
        if (icon) icon.textContent = '▼';
    }
}
window.togglePrefillMoreMeta = togglePrefillMoreMeta;

// F-P1-3 币种符号渲染：按当前选择币种刷新金额前缀符号 (U-13)
const CURRENCY_SYMBOLS = {
    HKD: 'HK$', CNY: '¥', USD: '$', EUR: '€', JPY: 'JP¥', GBP: '£', MOP: 'MOP$', SGD: 'S$'
};
function currentCurrencyCode() {
    const sel = document.getElementById('inpCurrency') || document.getElementById('arcCurrency');
    return (sel && sel.value ? sel.value : 'HKD').toUpperCase();
}
function currencySymbol(code) {
    return CURRENCY_SYMBOLS[(code || 'HKD').toUpperCase()] || (code || 'HKD') + ' ';
}
function formatCurrency(amount, currency = 'HKD') {
    const num = Number(amount);
    const validNum = (amount == null || isNaN(num) || !isFinite(num)) ? 0.00 : num;
    const cur = (currency || 'HKD').toString().trim().toUpperCase();
    const sym = CURRENCY_SYMBOLS[cur] || (cur ? cur + ' ' : 'HK$ ');
    const prefix = sym.endsWith(' ') || sym.endsWith('$') ? sym + ' ' : sym + ' ';
    return prefix + validNum.toFixed(2);
}
window.formatCurrency = formatCurrency;
window.currencySymbol = currencySymbol;
window.currentCurrencyCode = currentCurrencyCode;

function renderCurrencySymbol() {
    const inpCur = (document.getElementById('inpCurrency')?.value || 'HKD').toUpperCase();
    const arcCur = (document.getElementById('arcCurrency')?.value || 'HKD').toUpperCase();

    const inpTotal = document.getElementById('inpTotal');
    if (inpTotal) {
        const label = inpTotal.closest('.form-group')?.querySelector('label');
        if (label) {
            label.textContent = '整单总金额（' + currencySymbol(inpCur) + '）';
        }
    }
    const arcTotal = document.getElementById('arcTotal');
    if (arcTotal) {
        const label = arcTotal.closest('.form-group')?.querySelector('label');
        if (label) {
            label.textContent = '整单总金额（' + currencySymbol(arcCur) + '）';
        }
    }
}
window.renderCurrencySymbol = renderCurrencySymbol;

// -------------------------------------------------------------
// FR-8 反馈飞轮：点赞/点踩 + textarea 乐观 UI 与 qualityWarnings 联动
// -------------------------------------------------------------
function _feedbackTenantId() {
    try { return localStorage.getItem('demo_tenant_id') || 'default'; } catch (e) { return 'default'; }
}

// U-08：反馈收进弹层后，行内以小徽标呈现已反馈状态
function _syncFeedbackBadge(cell) {
    if (!cell) return;
    const td = cell.closest('td') || _rowFeedbackHomeTd;
    const badge = td ? td.querySelector('.feedback-badge') : null;
    if (!badge) return;
    const like = cell.dataset.like;
    if (like === '1') {
        badge.textContent = '已点赞';
        badge.style.display = '';
        badge.style.background = '#e8f5e9';
        badge.style.color = '#2e7d32';
    } else if (like === '-1') {
        badge.textContent = '已点踩';
        badge.style.display = '';
        badge.style.background = '#fdecea';
        badge.style.color = '#c62828';
    } else {
        badge.textContent = '';
        badge.style.display = 'none';
    }
}

// U-08：反馈弹层——点击行内「反馈」按钮，把该行 feedback-cell 移入 modal；关闭时移回
let _rowFeedbackHomeTd = null;

function openRowFeedbackModal(btn) {
    const td = btn.closest('td');
    const cell = td ? td.querySelector('.feedback-cell') : null;
    const body = document.getElementById('rowFeedbackModalBody');
    const modal = document.getElementById('rowFeedbackModal');
    if (!cell || !body || !modal) return;
    _rowFeedbackHomeTd = td;
    body.innerHTML = '';
    body.appendChild(cell);
    cell.style.display = '';
    cell.style.minWidth = '260px';
    modal.classList.remove('hide');
}

function closeRowFeedbackModal() {
    const body = document.getElementById('rowFeedbackModalBody');
    const cell = body ? body.querySelector('.feedback-cell') : null;
    if (cell && _rowFeedbackHomeTd) {
        _rowFeedbackHomeTd.appendChild(cell);
        cell.style.display = 'none';
    }
    _rowFeedbackHomeTd = null;
    closeModalById('rowFeedbackModal');
}

function _applyOptimisticLike(btn, likeVal) {
    const cell = btn.closest('.feedback-cell');
    if (!cell) return;
    const likeBtn = cell.querySelector('.btn-like');
    const dislikeBtn = cell.querySelector('.btn-dislike');
    const statusEl = cell.querySelector('.feedback-status');
    // 记录回滚前状态
    cell.dataset.prevLike = cell.dataset.like || '';
    cell.dataset.like = String(likeVal);
    if (likeVal === 1) {
        if (likeBtn) likeBtn.classList.add('active');
        if (dislikeBtn) dislikeBtn.classList.remove('active');
        if (statusEl) statusEl.textContent = '已点赞';
    } else if (likeVal === -1) {
        if (dislikeBtn) dislikeBtn.classList.add('active');
        if (likeBtn) likeBtn.classList.remove('active');
        if (statusEl) statusEl.textContent = '已点踩';
    }
    _syncFeedbackBadge(cell);
}

function _revertOptimisticLike(cell) {
    if (!cell) return;
    const prev = cell.dataset.prevLike;
    const likeBtn = cell.querySelector('.btn-like');
    const dislikeBtn = cell.querySelector('.btn-dislike');
    const statusEl = cell.querySelector('.feedback-status');
    if (likeBtn) likeBtn.classList.remove('active');
    if (dislikeBtn) dislikeBtn.classList.remove('active');
    if (prev === '1' && likeBtn) likeBtn.classList.add('active');
    if (prev === '-1' && dislikeBtn) dislikeBtn.classList.add('active');
    cell.dataset.like = prev || '';
    if (statusEl) statusEl.textContent = prev ? (prev === '1' ? '已点赞' : '已点踩') : '';
    _syncFeedbackBadge(cell);
}

function _qualityWarningsLink(comment, likeVal) {
    if (likeVal !== -1) return;
    const text = String(comment || '');
    const triggers = ['模糊', '过暗', '遮挡', '算术', '金额', '单价', '数量', '错', '漏', '重'];
    const hit = triggers.some(k => text.includes(k));
    if (!hit && !text.trim()) return;
    const banner = document.getElementById('qualityWarningsBanner');
    if (!banner) return;
    const existing = [];
    try {
        banner.querySelectorAll('div').forEach(d => {
            const t = d.textContent || '';
            if (t && t.indexOf('•') === 0) existing.push(t.slice(2).trim());
        });
    } catch (e) {}
    const newWarning = text.trim() ? ('用户反馈：' + text.trim().slice(0, 80)) : '用户反馈：点踩';
    if (existing.indexOf(newWarning) !== -1) return;
    const merged = existing.concat([newWarning]);
    showQualityWarnings(merged);
}

function _postFeedback(receiptId, likeVal, comment, itemIndex) {
    const tenantId = _feedbackTenantId();
    return fetch('/api/receipt/' + Number(receiptId) + '/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Tenant-Id': tenantId },
        body: JSON.stringify({ like: likeVal, comment: comment || '', item_index: itemIndex, tenant_id: tenantId })
    }).then(r => Promise.all([r.status, r.json().catch(() => null)])).then(([s, j]) => ({ status: s, body: j }));
}

function handleRowFeedback(btn, likeVal) {
    const cell = btn.closest('.feedback-cell');
    if (!cell) return;
    _applyOptimisticLike(btn, likeVal);
    // 仅切换选中态，不自动提交；用户可在 textarea 补充后点提交反馈
    const textarea = cell.querySelector('.feedback-comment');
    if (textarea && likeVal === -1) textarea.focus();
}

function submitRowFeedback(btn) {
    const cell = btn.closest('.feedback-cell');
    if (!cell) return;
    const wrap = btn.closest('tr');
    const rowIdx = cell.dataset.rowIndex != null ? Number(cell.dataset.rowIndex) : null;
    const textarea = cell.querySelector('.feedback-comment');
    const comment = textarea ? String(textarea.value || '').trim() : '';
    const likeVal = cell.dataset.like ? Number(cell.dataset.like) : null;
    const receiptId = currentReceiptId || (currentReceiptData && currentReceiptData.receipt_id) || (currentReceiptData && currentReceiptData.id);
    if (!receiptId) {
        showToast('请先完成识别或保存后再反馈', 'warning');
        return;
    }
    if (likeVal == null && !comment) {
        showToast('请选择点赞或点踩，或填写反馈原因', 'warning');
        return;
    }
    const statusEl = cell.querySelector('.feedback-status');
    const prevStatus = statusEl ? statusEl.textContent : '';
    if (statusEl) statusEl.textContent = '提交中...';
    btn.disabled = true;
    _postFeedback(receiptId, likeVal, comment, rowIdx).then(({ status, body }) => {
        btn.disabled = false;
        if (status >= 200 && status < 300 && body && body.status === 'success') {
            if (statusEl) statusEl.textContent = likeVal === 1 ? '已点赞' : (likeVal === -1 ? '已点踩' : '已提交');
            showToast('反馈已提交', 'success');
            _qualityWarningsLink(comment, likeVal);
            if (body.distilled) {
                showToast('已沉淀到供应商记忆（连续3次纠偏触发）', 'info', TOAST_DURATION.long);
            }
        } else {
            if (statusEl) statusEl.textContent = prevStatus || '提交失败';
            _revertOptimisticLike(cell);
            const msg = (body && body.msg) ? body.msg : ('HTTP ' + status);
            showToast('反馈提交失败：' + msg, 'error');
        }
    }).catch(err => {
        btn.disabled = false;
        if (statusEl) statusEl.textContent = prevStatus || '提交失败';
        _revertOptimisticLike(cell);
        showToast('反馈提交异常' + toastFailDetail(err), 'error');
    });
}

function handleArcFeedback(btn, likeVal) {
    const cell = btn.closest('.feedback-cell');
    if (!cell) return;
    _applyOptimisticLike(btn, likeVal);
    const textarea = cell.querySelector('.feedback-comment');
    if (textarea && likeVal === -1) textarea.focus();
}

function submitArcFeedback(btn) {
    const cell = btn.closest('.feedback-cell');
    if (!cell) return;
    const rowIdx = cell.dataset.rowIndex != null ? Number(cell.dataset.rowIndex) : null;
    const textarea = cell.querySelector('.feedback-comment');
    const comment = textarea ? String(textarea.value || '').trim() : '';
    const likeVal = cell.dataset.like ? Number(cell.dataset.like) : null;
    const receiptId = currentArchiveReceiptId || (currentArchiveDetailData && currentArchiveDetailData.receipt_id);
    if (!receiptId) {
        showToast('归档单据 ID 缺失，无法提交反馈', 'warning');
        return;
    }
    if (likeVal == null && !comment) {
        showToast('请选择点赞或点踩，或填写反馈原因', 'warning');
        return;
    }
    const statusEl = cell.querySelector('.feedback-status');
    const prevStatus = statusEl ? statusEl.textContent : '';
    if (statusEl) statusEl.textContent = '提交中...';
    btn.disabled = true;
    _postFeedback(receiptId, likeVal, comment, rowIdx).then(({ status, body }) => {
        btn.disabled = false;
        if (status >= 200 && status < 300 && body && body.status === 'success') {
            if (statusEl) statusEl.textContent = likeVal === 1 ? '已点赞' : (likeVal === -1 ? '已点踩' : '已提交');
            showToast('归档反馈已提交', 'success');
            _qualityWarningsLink(comment, likeVal);
            if (body.distilled) {
                showToast('已沉淀到供应商记忆（连续3次纠偏触发）', 'info', TOAST_DURATION.long);
            }
        } else {
            if (statusEl) statusEl.textContent = prevStatus || '提交失败';
            _revertOptimisticLike(cell);
            const msg = (body && body.msg) ? body.msg : ('HTTP ' + status);
            showToast('归档反馈提交失败：' + msg, 'error');
        }
    }).catch(err => {
        btn.disabled = false;
        if (statusEl) statusEl.textContent = prevStatus || '提交失败';
        _revertOptimisticLike(cell);
        showToast('归档反馈提交异常' + toastFailDetail(err), 'error');
    });
}

const FEEDBACK_COMMENT_MAXLEN_FRONTEND = 2000;   // 与后端 FEEDBACK_COMMENT_MAXLEN 对齐

function onFeedbackCommentInput(textarea) {
    // 即时提示：字数统计 + 接近上限告警；点踩态下联动 qualityWarnings 预览
    const cell = textarea.closest('.feedback-cell');
    if (!cell) return;
    const len = String(textarea.value || '').length;
    const charEl = cell.querySelector('.feedback-charcount');
    if (charEl) {
        const remain = FEEDBACK_COMMENT_MAXLEN_FRONTEND - len;
        charEl.textContent = `${len}/${FEEDBACK_COMMENT_MAXLEN_FRONTEND}`;
        if (remain <= 100) {
            charEl.style.color = '#c62828';
            charEl.textContent = `仅剩 ${remain} 字`;
        } else {
            charEl.style.color = '';
        }
    }
    const likeVal = cell.dataset.like ? Number(cell.dataset.like) : null;
    if (likeVal === -1 && len > 6) {
        _qualityWarningsLink(textarea.value, likeVal);
    }
}

// -------------------------------------------------------------
// 7. 提交人工复核结果保存入库
// -------------------------------------------------------------

// 读取 Tab1 复核表单当前内容，返回 prefill 风格数据对象
function collectReviewFormData() {
    const supplierName = document.getElementById('inpSupplier').value.trim();
    const dateStr = document.getElementById('inpDate').value.trim();
    const sheetName = document.getElementById('inpSheet').value.trim();
    const totalAmt = parseFloat(document.getElementById('inpTotal').value) || 0.00;

    const tbody = document.getElementById('itemTableBody');
    const rows = tbody.querySelectorAll('tr');
    registerNewUnitsFromRows(rows);

    const items = [];
    rows.forEach(tr => {
        const deptInput = tr.querySelector('.inp-dept');
        const actualQtyRaw = tr.querySelector('.inp-actual-qty') ? tr.querySelector('.inp-actual-qty').value.trim() : '';
        const item = {
            name: tr.querySelector('.inp-name').value.trim(),
            quantity: parseFloat(tr.querySelector('.inp-qty').value) || 1.0,
            unit: tr.querySelector('.inp-unit').value.trim(),
            unit_price: parseFloat(tr.querySelector('.inp-price').value) || 0.00,
            amount: parseFloat(tr.querySelector('.inp-amount').value) || 0.00,
            // Gap 6：手写实收数量（actual_qty），留空则按 quantity 计
            actual_qty: actualQtyRaw !== '' && !isNaN(parseFloat(actualQtyRaw)) ? parseFloat(actualQtyRaw) : null,
            // Wave 2（D44）：明细行成本中心——行内下拉实际显示值（未打部门 → null）
            cost_center_id: (deptInput && deptInput.value) ? Number(deptInput.value) : null,
            // D-P1-4：划线作废状态随行保存（后端算术门禁剔除 is_void 行）
            is_void: !!(tr.dataset && tr.dataset.isVoid === '1')
        };
        // D19：行内显式指定 SKU → 携带 sku_id；未指定（保持未关联）→ 缺省不传
        const skuIdInput = tr.querySelector('.inp-sku-id');
        const skuIdVal = skuIdInput && String(skuIdInput.value || '').trim();
        if (skuIdVal) item.sku_id = Number(skuIdVal);
        items.push(item);
    });

    const settlementSel = document.getElementById('inpSettlementType');
    const src = currentReceiptData || {};
    // Wave 2（D44）：单据级部门（一键填充目标）；未打部门 → null 可空保存
    const deptSel = document.getElementById('inpDepartmentId');
    const departmentId = (deptSel && deptSel.value) ? Number(deptSel.value) : null;
    // F-P1-3 多币种
    const curSel = document.getElementById('inpCurrency');
    const docFormSel = document.getElementById('inpDocForm');

    const data = {
        supplier_name: supplierName || '通用供应商',
        date: dateStr,
        sheet_name: sheetName,
        total_amount: totalAmt,
        items: items,
        settlement_type: settlementSel ? (settlementSel.value || null) : null,
        department_id: departmentId,
        doc_form: (docFormSel && docFormSel.value ? docFormSel.value : (src.doc_form || 'printed_delivery_note')),
        currency: (curSel && curSel.value ? curSel.value : (src.currency || 'HKD')),
        discount_amount: parseFloat(document.getElementById('inpDiscount')?.value) || 0.00,
        delivery_fee: parseFloat(document.getElementById('inpDeliveryFee')?.value) || 0.00,
        deposit_amount: parseFloat(document.getElementById('inpDeposit')?.value) || 0.00,
        rounding_adjustment: parseFloat(document.getElementById('inpRounding')?.value) || 0.00,
    };
    // U-05：付款标记改为枚举下拉，保存以用户选择为准；表单缺失时回落 AI 原值
    const inpMarkSel = document.getElementById('inpPaymentMark');
    if (inpMarkSel && inpMarkSel.value) data.payment_mark = inpMarkSel.value;
    else if (src.payment_mark != null) data.payment_mark = src.payment_mark;
    if (src.layout_type != null) data.layout_type = src.layout_type;
    // D17: 乐观锁版本号（加载/保存成功后记录）
    if (src.version != null) data.version = src.version;
    return data;
}

// 由 prefill 风格数据构造 save_edited payload（单张复核/归档弹窗/批量全部保存共用）
function buildSavePayloadFromData(data, receiptId) {
    const d = data || {};
    const payload = {
        receipt_id: receiptId,
        source: d.source || 'manual',
        supplier_name: d.supplier_name || '通用供应商',
        date: d.date || '',
        sheet_name: d.sheet_name || '',
        total_amount: parseFloat(d.total_amount) || 0.00,
        items: (d.items || []).map(it => {
            const itemPayload = {
                name: it.name != null ? it.name : (it.raw_name || ''),
                quantity: parseFloat(it.quantity) || 1.0,
                unit: it.unit != null ? it.unit : (it.raw_unit || ''),
                unit_price: parseFloat(it.unit_price) || 0.00,
                amount: parseFloat(it.amount) || 0.00,
                // Gap 6：手写实收数量 actual_qty 可见
                actual_qty: (it.actual_qty != null && String(it.actual_qty).trim() !== '' && !isNaN(Number(it.actual_qty))) ? Number(it.actual_qty) : null,
                // Wave 2（契约⑦）：明细行成本中心——行内实际显示值；未打部门 → null
                cost_center_id: (it.cost_center_id != null && String(it.cost_center_id).trim() !== ''
                    && Number(it.cost_center_id) > 0) ? Number(it.cost_center_id) : null,
                // D-P1-4：划线作废透传（后端算术门禁剔除 is_void 行）
                is_void: !!(it.is_void)
            };
            // D19：行内显式指定的 sku_id 透传（未指定不携带，后端走安全精确匹配）
            if (it.sku_id != null && String(it.sku_id).trim() !== ''
                && Number(it.sku_id) > 0) {
                itemPayload.sku_id = Number(it.sku_id);
            }
            return itemPayload;
        }),
        // M3/D4: 结算方式必填——提交前已由 requireSettlementMarked 校验；
        // 此处仅保留 cash/credit，未知兜底 null（后端对非手工单 400 SETTLEMENT_REQUIRED）
        settlement_type: (d.settlement_type === 'cash' || d.settlement_type === 'credit') ? d.settlement_type : null,
        doc_form: d.doc_form || 'printed_delivery_note',
        currency: String(d.currency || 'HKD').toUpperCase(),
        discount_amount: parseFloat(d.discount_amount) || 0.00,
        delivery_fee: parseFloat(d.delivery_fee) || 0.00,
        deposit_amount: parseFloat(d.deposit_amount) || 0.00,
        rounding_adjustment: parseFloat(d.rounding_adjustment) || 0.00,
    };
    // Wave 2（契约⑦）：单据级部门归属——未打部门可空保存（null 不覆盖旧值语义）
    if (d.department_id != null && String(d.department_id).trim() !== ''
        && Number(d.department_id) > 0) {
        payload.department_id = Number(d.department_id);
    } else {
        payload.department_id = null;
    }
    if (d.payment_mark != null) payload.payment_mark = d.payment_mark;
    if (d.layout_type != null) payload.layout_type = d.layout_type;
    // D17/W6 Q1：更新已有单据必须携带 version（缺失 → 后端 400 VERSION_REQUIRED）
    // 新建手工单（receiptId 为空）不要求 version
    if (receiptId != null && receiptId !== '') {
        if (d.version != null) payload.version = d.version;
    }
    return payload;
}

// 从保存成功响应中提取新版本号（容错：后端可能暂未返回）
function extractVersionFromResponse(ret) {
    if (!ret) return null;
    if (ret.version != null) return ret.version;
    if (ret.data && ret.data.version != null) return ret.data.version;
    return null;
}

// 结算方式必填化（PRD M3 / D4）：结算方式未标记（未知）→ 阻断保存并明确提示；
// 返回 true 表示已标记（可继续提交），false 表示未标记（应中断，不发请求）
function requireSettlementMarked(settlementType) {
    if (settlementType === 'cash' || settlementType === 'credit') return true;
    showToast('请选择结算方式（如现结或挂账）', 'error');
    return false;
}

// P3/R5：版本冲突重载保护——冲突时不再"提示后静默重载丢编辑"：
// 先把本地未保存编辑留副本（会话内 window.lastVersionConflictCopy + console），
// 再由用户显式确认是否重载最新内容。取消则保留当前画面编辑。
let lastVersionConflictCopy = null;
function handleVersionConflictReload(receiptId, localData, reloadFn) {
    lastVersionConflictCopy = {
        receipt_id: receiptId,
        data: localData,
        at: new Date().toISOString(),
    };
    try {
        console.log('[VERSION_CONFLICT] 本地编辑副本已保留',
                    lastVersionConflictCopy);
    } catch (e) { /* console 不可用不影响主流程 */ }
    const ok = confirm(
        '该单据已被他人修改，无法直接覆盖保存。\n\n' +
        '您当前的未保存编辑已留副本。\n' +
        '点「确定」重新加载服务端最新内容；\n' +
        '点「取消」保留当前画面，您可自行记录编辑内容后再处理。'
    );
    if (ok && typeof reloadFn === 'function') {
        reloadFn();
    }
}

// D34: 放弃上传/放弃解析——彻底从数据库物理删除未审核单据并从侧栏/前端移除
async function discardCurrentReceipt() {
    const photo = typeof getActivePhoto === 'function' ? getActivePhoto() : null;
    const targetReceiptId = (photo && photo.receiptId) || currentReceiptId;
    const displayName = photo ? (photo.fileName || (photo.file ? photo.file.name : null) || `单据 #${targetReceiptId || ''}`) : (targetReceiptId ? `单据 #${targetReceiptId}` : '当前单据');

    if (!confirm(`确定要放弃上传并彻底删除「${displayName}」的解析结果吗？\n\n放弃后该单据将从系统及数据库中彻底物理删除，且在归档与历史中无法再查询到。`)) {
        return;
    }

    const btnDiscard = document.getElementById('btnDiscardReceipt');
    if (btnDiscard) btnDiscard.disabled = true;

    try {
        if (targetReceiptId) {
            const res = await fetch(`/api/receipt/${targetReceiptId}/discard`, { method: 'POST' });
            const ret = await res.json().catch(() => null);
            if (!res.ok && res.status !== 404) {
                const errorMsg = (ret && ret.msg) || `物理删除单据失败 HTTP ${res.status}`;
                showToast(errorMsg, 'error');
                if (btnDiscard) btnDiscard.disabled = false;
                return;
            }
        }

        // 成功删除，开始从前端列表移除
        const idx = photo ? BatchUploader.photos.indexOf(photo) : -1;
        if (idx >= 0) {
            if (photo.pollToken) photo.pollToken.cancelled = true;
            try { URL.revokeObjectURL(photo.objectUrl); } catch(e) {}
            BatchUploader.photos.splice(idx, 1);

            if (BatchUploader.photos.length === 0) {
                BatchUploader.activeIndex = 0;
                document.getElementById('splitViewArea').classList.add('hide');
                const btn = document.getElementById('btnSiderToggle');
                if (btn) btn.classList.add('hide');
                const drawer = document.getElementById('photoSider');
                if (drawer) drawer.classList.remove('open');
                BatchUploader.siderOpen = false;
                document.getElementById('prefillFormCard').classList.add('hide');
                document.getElementById('preConfirmCard').classList.add('hide');
                document.getElementById('loadingCard').classList.add('hide');
                document.getElementById('errorCard').classList.add('hide');
                currentReceiptId = null;
                currentReceiptData = null;
                clearManifest();
                resetManualEntryMode();   // D12：放弃手工单草稿 → 恢复左栏/标题
            } else {
                BatchUploader.activeIndex = Math.min(idx, BatchUploader.photos.length - 1);
                pruneUnselectableFromSelection();
                renderSider();
                updateSelectionUI();
                setActivePhoto(BatchUploader.activeIndex);
                persistManifest();
            }
        } else {
            // 单张照片场景
            document.getElementById('splitViewArea').classList.add('hide');
            document.getElementById('prefillFormCard').classList.add('hide');
            document.getElementById('preConfirmCard').classList.add('hide');
            document.getElementById('loadingCard').classList.add('hide');
            document.getElementById('errorCard').classList.add('hide');
            currentReceiptId = null;
            currentReceiptData = null;
            clearManifest();
            resetManualEntryMode();   // D12：放弃手工单草稿 → 恢复左栏/标题
        }

        showToast('已放弃并彻底删除单据解析结果', 'info');
    } catch (err) {
        console.error('放弃单据异常:', err);
        showToast('放弃单据请求失败，请检查网络设置', 'error');
    } finally {
        if (btnDiscard) btnDiscard.disabled = false;
    }
}
window.discardCurrentReceipt = discardCurrentReceipt;

// Q32: 单张保存互斥旗标——保存中禁止再次触发（防双击重复提交）
let isSavingReview = false;

function submitSaveEdited() {
    if (isSavingReview) return;   // Q32: 保存进行中，忽略重复点击

    const data = collectReviewFormData();

    if (isManualEntry) {
        // D12：手工新建前端校验——与后端 save_edited 手工分支 400 语义一致
        // （日期必填且 YYYY-MM-DD 真实、供应商必填且非默认占位、明细至少一行）
        const manualErr = validateManualEntry(data);
        if (manualErr) {
            showToast(manualErr, 'error');
            return;
        }
    } else {
        const supplier = String((data && data.supplier_name) || '').trim();
        if (!supplier || supplier === '通用供应商') {
            showToast('请填写供应商名称', 'error');
            return;
        }
        const dateStr = String((data && data.date) || '').trim();
        if (!dateStr || !/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) {
            showToast('请选择开单日期', 'error');
            return;
        }
        const m = dateStr.match(/^(\d{4})-(\d{2})-(\d{2})$/);
        const y = Number(m[1]), mo = Number(m[2]), d = Number(m[3]);
        const dt = new Date(y, mo - 1, d);
        if (dt.getFullYear() !== y || dt.getMonth() !== mo - 1 || dt.getDate() !== d) {
            showToast('开单日期不正确，请选择真实存在的日历日期', 'error');
            return;
        }
        const totalAmt = Number((data && data.total_amount) || 0);
        if (isNaN(totalAmt) || totalAmt <= 0) {
            showToast('单据总金额必须大于 0', 'error');
            return;
        }
        if (!data.items || data.items.length === 0) {
            showToast('请至少输入一行消费明细', 'warning');
            return;
        }
        for (let i = 0; i < data.items.length; i++) {
            const it = data.items[i];
            const name = String((it && (it.name || it.raw_name)) || '').trim();
            if (!name) {
                showToast(`第 ${i + 1} 行的品名不能为空，请填写或删除该行`, 'error');
                return;
            }
            const qty = Number(it && it.quantity);
            const price = Number(it && it.unit_price);
            if (isNaN(qty) || qty <= 0 || isNaN(price) || price < 0) {
                showToast(`第 ${i + 1} 行的单价或数量不是有效数字，请重新输入`, 'error');
                return;
            }
        }
    }

    // M3/D4 结算方式必填化：未标记 → 阻断提交，不发请求。
    // D12 手工新建单（isManualEntry）豁免——与后端 manual_entry 豁免（app.py
    // save_edited 分支）语义对齐：人工逐字录入，结算可稍后补标。
    if (!isManualEntry && !requireSettlementMarked(data.settlement_type)) {
        return;
    }

    const payload = buildSavePayloadFromData(data, currentReceiptId);
    payload.source = 'manual';

    isSavingReview = true;
    const saveBtn = document.getElementById('btnSaveReview');
    if (saveBtn) saveBtn.disabled = true;

    fetch('/api/save_edited', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
    .then(res => Promise.all([res.status, res.json().catch(() => null)]))
    .then(([httpStatus, ret]) => {
        // D17: 版本冲突——单据已被他人修改；留副本 + 显式确认后重载（P3/R5）
        if (httpStatus === 409 && ret && ret.code === 'VERSION_CONFLICT') {
            handleVersionConflictReload(currentReceiptId, data,
                () => refreshReviewFormFromServer(currentReceiptId));
            return;
        }
        // W6 Q1：更新已有单但未携带 version
        if (httpStatus === 400 && ret && ret.code === 'VERSION_REQUIRED') {
            showToast('缺少版本号，已重新加载最新内容', 'warning');
            if (currentReceiptId) refreshReviewFormFromServer(currentReceiptId);
            return;
        }
        // E-P1-4：403 人话统一（同步表单提交路径，与 fetch 包装双保险，去重由 toast key 保证）
        if (toastHttpError(httpStatus, ret)) return;
        if (!ret || ret.status !== 'success') {
            showToast('保存失败：' + ((ret && ret.msg) || '请稍后重试'), 'error');
            return;
        }

        // U-8: 保存成功，重置连续失败计数
        resetQualityFailCount();

        // 乐观锁：保存成功后刷新本地版本号，便于连续保存
        const newVersion = extractVersionFromResponse(ret);
        if (newVersion != null) {
            if (currentReceiptData) currentReceiptData.version = newVersion;
            else currentReceiptData = { version: newVersion };
            const ap = getActivePhoto();
            if (ap && ap.data) ap.data.version = newVersion;
            // 同步回 data（批量模式后续用 data 构造 payload）
            data.version = newVersion;
        }

        // 手工新建（无 receipt_id）成功后：拿到 receipt_id 进入正常复核态——
        // 保留表单供继续编辑/再次保存（此时已是带 version 的既有单，走常规乐观锁路径）
        if (isManualEntry) {
            isManualEntry = false;
            currentReceiptId = ret.receipt_id;
            currentReceiptData = data;
            showToast('已保存手工录入单据 #' + ret.receipt_id, 'success', TOAST_DURATION.guide);
        } else if (BatchUploader.photos.length > 0) {
            // 批量：仅标记当前项为已保存，可切换其它照片继续保存
            const active = getActivePhoto();
            if (active) {
                active.status = 'saved';
                active.data = data;   // 保存以表单当前内容为准，同步回 photo
            }
            renderSider();
            showToast('已保存当前照片，可在照片抽屉切换其它照片继续保存', 'success', TOAST_DURATION.guide);
        } else {
            // 单张：保存后清空界面回到上传卡片
            showToast('已保存单据，请到「供应商与归档」完成审核', 'success', TOAST_DURATION.guide);

            // 重置收据界面
            document.getElementById('receiptFile').value = '';
            document.getElementById('splitViewArea').classList.add('hide');
            currentReceiptId = null;
            currentReceiptData = null;
            selectedFile = null;
            originalFile = null;
        }

        // 刷新后台库存数据
        loadInventoryData();
        loadSuppliersData();
        loadFinancePanel();     // Wave 1：save_edited 快照钩子可能写预期付款日 → 提醒联动
    })
    .catch(err => {
        console.error('保存单据请求失败:', err);
        showToast('保存请求失败，请检查网络后重试', 'error');
    })
    .finally(() => {
        // Q32: 无论成功/失败/冲突，恢复保存按钮可再次触发
        isSavingReview = false;
        const saveBtn = document.getElementById('btnSaveReview');
        if (saveBtn) saveBtn.disabled = false;
    });
}

// 冲突或重载场景：从服务端拉取最新详情并重渲染 Tab1 复核表单
function refreshReviewFormFromServer(receiptId) {
    if (!receiptId) return;
    resetManualEntryMode();   // D12：真实单据重载 → 退出新建手工单态
    fetch(`/api/receipt/${receiptId}`)
    .then(res => res.json())
    .then(ret => {
        if (ret.status !== 'success') {
            showToast('重新加载单据详情失败：' + (ret.msg || '请稍后重试'), 'error');
            return;
        }
        currentReceiptId = ret.receipt_id || receiptId;
        currentReceiptData = ret.data || {};
        renderEditForm(currentReceiptData);

        const photo = getActivePhoto();
        if (photo && photo.receiptId === receiptId) {
            photo.data = currentReceiptData;
            if (photo.status === 'error') photo.status = 'parsed';
            renderSider();
        }
        showToast('已从服务器重新加载单据最新内容', 'info');
    })
    .catch(err => {
        console.error('重新加载单据详情请求失败:', err);
        showToast('重新加载失败，请检查网络后重试', 'error');
    });
}

// -------------------------------------------------------------
// 8. 库存看板与供应商统计获取
// -------------------------------------------------------------
const InventoryUI = {
    q: '',
    category: '',
    stock: 'all',      // all | low
    price: 'all',      // all | anomaly
    includeInactive: false,
    debounceTimer: null,
};
if (typeof window !== 'undefined') {
    window.InventoryUI = InventoryUI;
}

let inventorySkusMap = new Map();

function onInventorySearchInput(val) {
    const inputQ = document.getElementById('invSearchInput');
    if (inputQ) inputQ.value = val || '';
    InventoryUI.q = (val || '').trim();
    if (InventoryUI.debounceTimer) clearTimeout(InventoryUI.debounceTimer);
    InventoryUI.debounceTimer = setTimeout(() => {
        loadInventoryData();
    }, 200);
}

function onInventoryCategoryChange(val) {
    const selCat = document.getElementById('invCategorySelect');
    if (selCat) selCat.value = val || '';
    InventoryUI.category = val || '';
    loadInventoryData();
}

function onInventoryFilterSelectChange(val) {
    const selFlt = document.getElementById('invFilterSelect');
    if (selFlt) selFlt.value = val || 'all';
    if (val === 'low') {
        InventoryUI.stock = 'low';
        InventoryUI.price = 'all';
    } else if (val === 'anomaly') {
        InventoryUI.stock = 'all';
        InventoryUI.price = 'anomaly';
    } else {
        InventoryUI.stock = 'all';
        InventoryUI.price = 'all';
    }
    loadInventoryData();
}

function onInventoryIncludeInactiveChange(checked) {
    const chkInc = document.getElementById('invIncludeInactive');
    if (chkInc) chkInc.checked = !!checked;
    InventoryUI.includeInactive = !!checked;
    loadInventoryData();
}

function setInventoryStockFilter(val) {
    InventoryUI.stock = val;
    InventoryUI.price = 'all';
    const select = document.getElementById('invFilterSelect');
    if (select) select.value = val === 'low' ? 'low' : 'all';
    loadInventoryData();
}

function setInventoryPriceFilter(val) {
    InventoryUI.price = val;
    InventoryUI.stock = 'all';
    const select = document.getElementById('invFilterSelect');
    if (select) select.value = val === 'anomaly' ? 'anomaly' : 'all';
    loadInventoryData();
}

function resetInventoryFilters() {
    InventoryUI.q = '';
    InventoryUI.category = '';
    InventoryUI.stock = 'all';
    InventoryUI.price = 'all';
    InventoryUI.includeInactive = false;

    const inputQ = document.getElementById('invSearchInput');
    if (inputQ) inputQ.value = '';
    const selCat = document.getElementById('invCategorySelect');
    if (selCat) selCat.value = '';
    const selFlt = document.getElementById('invFilterSelect');
    if (selFlt) selFlt.value = 'all';
    const chkIn = document.getElementById('invIncludeInactive');
    if (chkIn) chkIn.checked = false;

    loadInventoryData();
}

function loadInventoryData() {
    const inputQ = document.getElementById('invSearchInput');
    const selCat = document.getElementById('invCategorySelect');
    const selFlt = document.getElementById('invFilterSelect');
    const chkInc = document.getElementById('invIncludeInactive');

    if (inputQ) InventoryUI.q = inputQ.value.trim();
    if (selCat) InventoryUI.category = selCat.value;
    if (selFlt) {
        const val = selFlt.value;
        if (val === 'low') {
            InventoryUI.stock = 'low';
            InventoryUI.price = 'all';
        } else if (val === 'anomaly') {
            InventoryUI.stock = 'all';
            InventoryUI.price = 'anomaly';
        } else {
            InventoryUI.stock = 'all';
            InventoryUI.price = 'all';
        }
    }
    if (chkInc) InventoryUI.includeInactive = !!chkInc.checked;

    const params = new URLSearchParams();
    if (InventoryUI.q) params.set('q', InventoryUI.q);
    if (InventoryUI.category) params.set('category', InventoryUI.category);
    if (InventoryUI.stock !== 'all') params.set('stock', InventoryUI.stock);
    if (InventoryUI.price !== 'all') params.set('price', InventoryUI.price);
    if (InventoryUI.includeInactive) params.set('include_inactive', '1');

    fetch('/api/inventory?' + params.toString())
    .then(res => res.json())
    .then(ret => {
        if (ret.status !== 'success') return;

        const skus = ret.data || [];
        const meta = ret.meta || {};

        inventorySkusMap.clear();
        skus.forEach(s => inventorySkusMap.set(s.id, s));

        const totalEl = document.getElementById('valTotalSKU');
        if (totalEl) totalEl.innerText = meta.total_active != null ? meta.total_active : skus.length;

        const lowEl = document.getElementById('valLowStock');
        if (lowEl) lowEl.innerText = meta.low_stock_count != null ? meta.low_stock_count : skus.filter(s => s.is_low_stock).length;

        const priceEl = document.getElementById('valPriceAnomaly');
        if (priceEl) priceEl.innerText = meta.price_anomaly_count != null ? meta.price_anomaly_count : skus.filter(s => s.price_anomaly).length;

        // 同步加载极简 AI 发现胶囊
        loadAiLeanInsights();

        const tbody = document.getElementById('inventoryTableBody');
        if (!tbody) return;
        tbody.innerHTML = '';

        if (skus.length === 0) {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td colspan="7" class="col-center" style="padding: 32px 16px; color: var(--text-muted, #6b7280);">
                    <div style="font-size: 1rem; margin-bottom: 8px;">没有符合条件的食材</div>
                    <div style="display: flex; gap: 8px; justify-content: center; margin-top: 8px;">
                        <button class="btn btn-secondary" style="padding: 4px 12px; font-size: 0.82rem;" onclick="resetInventoryFilters()">清除筛选</button>
                        <button class="btn btn-primary" style="padding: 4px 12px; font-size: 0.82rem;" onclick="openAddSkuModal()">新增食材</button>
                    </div>
                </td>
            `;
            tbody.appendChild(tr);
            return;
        }

        skus.forEach(sku => {
            if (sku.base_unit && !availableUnits.includes(sku.base_unit)) {
                availableUnits.push(sku.base_unit);
            }

            const tr = document.createElement('tr');
            const skuStock = Number(sku.current_stock || 0);
            const alertVal = Number(sku.min_stock_alert || 0);
            const priceAnomaly = !!sku.price_anomaly;
            const vsAvgPct = (sku.vs_avg_pct != null && Number.isFinite(Number(sku.vs_avg_pct)))
                ? Number(sku.vs_avg_pct) : null;
            const priceText = sku.last_unit_price
                ? `$${Number(sku.last_unit_price).toFixed(2)}`
                : '-';

            const isActive = sku.active !== 0;
            if (!isActive) tr.classList.add('inv-row-inactive');
            if (priceAnomaly) tr.classList.add('inv-row-price-hot');
            if (sku.is_low_stock) tr.classList.add('inv-row-low-stock');

            // 统一胶囊机制：所有库存表告警胶囊都用 .badge + 颜色修饰符，
            // 数值类胶囊（涨价 / 低库存）统一堆叠在数值「上方」并与数值右边缘对齐。
            let priceAnomalyBadge = '';
            if (priceAnomaly) {
                const pctLabel = vsAvgPct != null
                    ? `涨价 +${vsAvgPct.toFixed(1)}%`
                    : '涨价异动';
                priceAnomalyBadge = `<span class="badge badge-danger" title="相对 30 日均价涨幅超过 10%">${w2Escape(pctLabel)}</span>`;
            }
            const priceBtnCls = priceAnomaly ? 'btn btn-price-anomaly' : 'btn btn-secondary';

            // 已停用 / 分类 等同属标签胶囊，统一走 .badge.badge-secondary（去掉内联自定义样式与间距，间距交由 .inv-name-cell 的 gap 控制）
            const activeBadge = !isActive
                ? `<span class="badge badge-secondary">已停用</span>`
                : '';
            // kg 列：仅重量单位显示折算（司马斤 0.6048），计件单位显示 -
            let kgCell = '<span style="color:var(--text-muted);">-</span>';
            if (sku.standard_kg != null && Number.isFinite(Number(sku.standard_kg))) {
                const kgVal = Number(sku.standard_kg);
                kgCell = `${kgVal.toFixed(3)} kg`;
                // 司马斤特殊提示：若原单位含斤/两/磅则 hover 显示换算率
                const weightHint = (sku.base_unit === '斤' || sku.base_unit === '司马斤' || sku.base_unit === '司馬斤')
                    ? ' title="司马斤 x 0.6048 = kg"'
                    : '';
                kgCell = `<span${weightHint}>${w2Escape(kgCell)}</span>`;
            }
            tr.innerHTML = `
                <td class="inv-name-cell"><div class="inv-name-wrap"><span class="inv-name-text"><strong>${w2Escape(sku.name)}</strong></span>${activeBadge}</div></td>
                <td><span class="badge badge-secondary">${w2Escape(sku.category || 'N/A')}</span></td>
                <td class="col-right inv-metric-cell">
                    <div class="inv-metric-stack">
                        ${sku.is_low_stock ? `<span class="badge badge-danger">低库存</span>` : ''}
                        <span class="inv-metric-value ${sku.is_low_stock ? 'is-low' : (skuStock < 1 ? 'is-zero' : 'is-ok')}"><strong>${skuStock}</strong> ${w2Escape(sku.base_unit)}</span>
                    </div>
                </td>
                <td class="col-right">${kgCell}</td>
                <td class="col-right">
                    <span class="${sku.is_low_stock ? 'inv-alert-danger' : ''}">${alertVal} ${w2Escape(sku.base_unit)}</span>
                </td>
                <td class="col-right inv-metric-cell">
                    <div class="inv-metric-stack">
                        ${priceAnomalyBadge}
                        <span class="inv-price-value${priceAnomaly ? ' inv-price-hot' : ''}">${w2Escape(priceText)}</span>
                    </div>
                </td>
                <td class="col-center">
                    <div class="inv-actions">
                        <button class="${priceBtnCls}" style="padding:6px 10px; font-size:1rem; min-height:44px; min-width:44px;" onclick="viewPriceHistory(${Number(sku.id)})">价格走势</button>
                        <button class="btn btn-secondary" style="padding:6px 10px; font-size:1rem; min-height:44px; min-width:44px;"
                            onclick='openStocktakeModal(${Number(sku.id)}, ${jsStr(sku.name)}, ${skuStock}, ${jsStr(sku.base_unit)})'>盘点</button>
                        <button class="btn btn-secondary" style="padding:6px 10px; font-size:1rem; min-height:44px; min-width:44px;"
                            onclick='openEditSkuModal(${Number(sku.id)})'>编辑</button>
                        <button class="btn btn-secondary" style="padding:6px 10px; font-size:1rem; min-height:44px; min-width:44px;"
                            onclick='toggleInventoryMoreMenu(this, ${Number(sku.id)})'>更多 ▼</button>
                    </div>
                </td>
            `;
            tbody.appendChild(tr);
        });
    });
}

function deduplicateSkus() {
    if (!confirm('确认一键清理重复食材？将按 canonical 归一合并所有 _\\d{10} 流水号变体（幂等、迁移流水、停用副 SKU、审计留痕）。')) return;
    fetch('/api/admin/maintenance/deduplicate', {
        method: 'POST',
        headers: _authHeaders({'Content-Type': 'application/json'})
    }).then(r => r.json()).then(ret => {
        if (ret.status === 'success') {
            const cnt = ret.merged_skus || 0;
            const groups = ret.merged_groups || 0;
            showToast(cnt > 0 ? `已清理 ${cnt} 个重复 SKU（${groups} 组）` : '无重复 SKU 需清理', 'success');
            loadInventoryData();
        } else {
            showToast(ret.msg || ret.detail || '清理失败', 'error');
        }
    }).catch(e => showToast('清理请求失败: ' + e, 'error'));
}

function _resolveSkuObject(skuTarget) {
    if (!skuTarget) return null;
    if (typeof skuTarget === 'object') return skuTarget;
    if (typeof skuTarget === 'number') {
        return inventorySkusMap.get(skuTarget) || null;
    }
    if (typeof skuTarget === 'string') {
        const num = Number(skuTarget);
        if (!isNaN(num) && inventorySkusMap.has(num)) {
            return inventorySkusMap.get(num);
        }
        try { return JSON.parse(skuTarget); } catch(e) { return null; }
    }
    return null;
}

function toggleInventoryMoreMenu(btn, skuTarget) {
    const sku = _resolveSkuObject(skuTarget);
    if (!sku) {
        showToast('无法读取食材档案信息', 'error');
        return;
    }
    const skuId = sku.id;
    const skuName = sku.name;
    const skuStock = Number(sku.current_stock || 0);
    const baseUnit = sku.base_unit;
    const isActive = sku.active !== 0;

    let menu = document.getElementById('invMoreMenu');
    if (!menu) {
        menu = document.createElement('div');
        menu.id = 'invMoreMenu';
        menu.className = 'unit-dropdown-menu';
        document.body.appendChild(menu);
    }
    if (!menu.classList.contains('hide') && menu.dataset.activeId == skuId) {
        menu.classList.add('hide');
        return;
    }
    const rect = btn.getBoundingClientRect();
    menu.style.position = 'fixed';
    menu.style.top = (rect.bottom + 4) + 'px';
    menu.style.left = Math.max(10, rect.left - 40) + 'px';
    menu.style.width = '140px';
    menu.style.minWidth = '140px';
    menu.style.zIndex = '99999';
    menu.dataset.activeId = skuId;

    const toggleText = isActive ? '停用' : '启用';

    // 产品合并：计用量/报损耗 → 统一出库（内部分类型），停用/删除保留在更多作为快捷，编辑内已含停用开关
    menu.innerHTML = `
        <div class="unit-dropdown-item" onclick='closeInvMoreMenu(); openOutboundModal(${Number(skuId)}, ${jsStr(skuName)}, ${skuStock}, ${jsStr(baseUnit)})'>出库</div>
        <div class="unit-dropdown-item" style="color:${isActive ? '#ef4444' : '#10b981'};" onclick='closeInvMoreMenu(); toggleSkuActive(${Number(skuId)})'>${toggleText}</div>
        <div class="unit-dropdown-item" style="color:#ef4444; border-top:1px dashed var(--border-color);" onclick='closeInvMoreMenu(); deleteSkuDirect(${Number(skuId)}, ${jsStr(skuName)})'>删除/清理</div>
    `;
    menu.classList.remove('hide');
    setTimeout(() => {
        const closeHandler = (e) => {
            if (!menu.contains(e.target) && e.target !== btn) {
                menu.classList.add('hide');
                document.removeEventListener('click', closeHandler);
            }
        };
        document.addEventListener('click', closeHandler);
    }, 10);
}

function closeInvMoreMenu() {
    const menu = document.getElementById('invMoreMenu');
    if (menu) menu.classList.add('hide');
}

function openAddSkuModal() {
    const titleEl = document.getElementById('skuModalTitle');
    if (titleEl) titleEl.innerText = '新增食材';
    document.getElementById('skuModalId').value = '';
    document.getElementById('skuModalName').value = '';
    document.getElementById('skuModalCategory').value = '肉类';

    const unitInput = document.getElementById('skuModalUnit');
    if (unitInput) {
        unitInput.value = 'kg';
        unitInput.disabled = false;
    }
    const noticeEl = document.getElementById('skuModalUnitNotice');
    if (noticeEl) noticeEl.style.display = 'none';

    document.getElementById('skuModalAlert').value = '5.0';

    const activeGrp = document.getElementById('skuModalActiveGroup');
    if (activeGrp) activeGrp.style.display = 'none';

    const codeGrp = document.getElementById('skuModalAdvancedCode');
    if (codeGrp) codeGrp.style.display = 'none';

    openModalById('skuModal');
}

function openEditSkuModal(skuTarget) {
    const sku = _resolveSkuObject(skuTarget);
    if (!sku) {
        showToast('无法读取食材档案信息，请刷新后再试', 'error');
        return;
    }
    const titleEl = document.getElementById('skuModalTitle');
    if (titleEl) titleEl.innerText = '编辑食材档案';
    document.getElementById('skuModalId').value = sku.id;
    document.getElementById('skuModalName').value = sku.name || '';
    document.getElementById('skuModalCategory').value = sku.category || '其他';

    const unitInput = document.getElementById('skuModalUnit');
    const noticeEl = document.getElementById('skuModalUnitNotice');
    if (unitInput) {
        unitInput.value = sku.base_unit || 'kg';
        const stock = Math.abs(Number(sku.current_stock || 0));
        if (stock > 1e-9) {
            unitInput.disabled = true;
            if (noticeEl) {
                noticeEl.innerText = `账面库存不为 0，禁止修改单位。请先通过盘点或记用量/报损耗将库存调整为 0。`;
                noticeEl.style.display = 'block';
            }
        } else {
            unitInput.disabled = false;
            if (noticeEl) noticeEl.style.display = 'none';
        }
    }

    document.getElementById('skuModalAlert').value = sku.min_stock_alert != null ? sku.min_stock_alert : 5.0;

    const activeGrp = document.getElementById('skuModalActiveGroup');
    if (activeGrp) {
        activeGrp.style.display = 'block';
        document.getElementById('skuModalActive').value = sku.active !== 0 ? '1' : '0';
    }

    const codeGrp = document.getElementById('skuModalAdvancedCode');
    if (codeGrp) {
        codeGrp.style.display = 'block';
        document.getElementById('skuModalCodeDisplay').innerText = sku.sku_code || '';
    }

    openModalById('skuModal');
}

function submitSkuModal() {
    const skuId = document.getElementById('skuModalId').value;
    const name = document.getElementById('skuModalName').value.trim();
    const category = document.getElementById('skuModalCategory').value.trim() || '其他';
    const base_unit = document.getElementById('skuModalUnit').value.trim() || 'kg';
    const alertRaw = document.getElementById('skuModalAlert').value.trim();

    if (!name) {
        showToast('请填写真实品名', 'warning');
        return;
    }
    if (name.length > 64) {
        showToast('品名不能超过 64 个字符', 'warning');
        return;
    }

    const min_stock_alert = parseFloat(alertRaw);
    if (isNaN(min_stock_alert) || min_stock_alert < 0) {
        showToast('补货警戒线必须为大于或等于 0 的数值', 'warning');
        return;
    }

    const btn = document.getElementById('skuModalSubmitBtn');
    if (btn) btn.disabled = true;

    if (!skuId) {
        // Create
        fetch('/api/inventory/skus', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                name,
                category,
                base_unit,
                min_stock_alert
            })
        })
        .then(res => res.json())
        .then(ret => {
            if (btn) btn.disabled = false;
            if (ret.status === 'success') {
                closeModalById('skuModal');
                loadInventoryData();
                showToast(`食材 SKU【${name}】创建成功`, 'success');

                // 若由明细行内联发起，自动绑定回该行
                if (activeInlineSkuInput) {
                    const wrap = activeInlineSkuInput.closest('.sku-combobox-wrap');
                    if (wrap) {
                        const skuInput = wrap.querySelector('.inp-sku');
                        const idInput = wrap.querySelector('.inp-sku-id');
                        const badge = wrap.querySelector('.sku-badge');
                        if (skuInput) skuInput.value = name;
                        if (idInput) idInput.value = ret.id;
                        if (badge) {
                            badge.textContent = '已匹配SKU';
                            badge.className = 'badge badge-success sku-badge';
                        }
                    }
                    activeInlineSkuInput = null;
                }
            } else {
                if (ret.code === 'SKU_NAME_CONFLICT') {
                    showToast('已存在同名的启用食材，请修改品名', 'warning');
                } else {
                    showToast('创建失败：' + (ret.message || ret.msg || '请稍后重试'), 'error');
                }
            }
        })
        .catch(err => {
            if (btn) btn.disabled = false;
            console.error('创建食材请求失败:', err);
            showToast('创建失败，请检查网络后重试', 'error');
        });
    } else {
        // Edit
        const bodyData = {
            name,
            category,
            min_stock_alert,
            active: parseInt(document.getElementById('skuModalActive').value || '1')
        };
        const unitInput = document.getElementById('skuModalUnit');
        if (unitInput && !unitInput.disabled) {
            bodyData.base_unit = base_unit;
        }

        fetch('/api/inventory/skus/' + skuId, {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(bodyData)
        })
        .then(res => res.json())
        .then(ret => {
            if (btn) btn.disabled = false;
            if (ret.status === 'success') {
                closeModalById('skuModal');
                loadInventoryData();
            } else {
                if (ret.code === 'UNIT_CHANGE_BLOCKED') {
                    showToast('账面库存不为 0，请先盘点调整为 0 再改单位', 'warning');
                } else if (ret.code === 'SKU_NAME_CONFLICT') {
                    showToast('已存在同名的启用食材，请修改品名', 'warning');
                } else {
                    showToast('更新失败：' + (ret.message || ret.msg || '请稍后重试'), 'error');
                }
            }
        })
        .catch(err => {
            if (btn) btn.disabled = false;
            console.error('更新食材请求失败:', err);
            showToast('更新失败，请检查网络后重试', 'error');
        });
    }
}

// -------------------------------------------------------------
// SKU 合并管理 (SKU Merge Modal)
// -------------------------------------------------------------
function openSkuMergeModalFromInv() {
    fetch('/api/inventory?include_inactive=0')
    .then(res => res.json())
    .then(ret => {
        if (ret.status !== 'success' || !ret.data || ret.data.length < 2) {
            showToast('现有启用食材少于 2 种，无需合并', 'info');
            return;
        }
        const skus = ret.data;
        const sel = document.getElementById('skuMergePrimarySelect');
        const listDiv = document.getElementById('skuMergeSecondaryList');
        if (!sel || !listDiv) return;

        sel.innerHTML = '';
        skus.forEach(s => {
            const opt = document.createElement('option');
            opt.value = s.id;
            opt.textContent = `${s.name} (${s.category || '未分类'} · 当前库存 ${s.current_stock}${s.base_unit || ''})`;
            sel.appendChild(opt);
        });

        function updateSecondaryCheckboxes() {
            const primaryId = parseInt(sel.value);
            let html = '';
            skus.forEach(s => {
                if (s.id !== primaryId) {
                    html += `
                        <label style="display:flex; align-items:center; gap:8px; margin:4px 0; cursor:pointer;">
                            <input type="checkbox" class="chk-sku-sec" value="${s.id}">
                            <span>${w2Escape(s.name)} <small style="color:var(--text-muted);">[库存:${s.current_stock}${s.base_unit}]</small></span>
                        </label>
                    `;
                }
            });
            listDiv.innerHTML = html;
        }

        sel.onchange = updateSecondaryCheckboxes;
        updateSecondaryCheckboxes();
        openModalById('skuMergeModal');
    })
    .catch(err => {
        console.error('加载合并食材列表异常:', err);
        showToast('加载食材列表失败', 'error');
    });
}

function submitSkuMerge() {
    const primaryId = parseInt(document.getElementById('skuMergePrimarySelect').value);
    const secCheckboxes = document.querySelectorAll('.chk-sku-sec:checked');
    const secIds = Array.from(secCheckboxes).map(c => parseInt(c.value));
    const syncMemory = document.getElementById('skuMergeSyncMemory')?.checked ?? true;

    if (!primaryId || secIds.length === 0) {
        showToast('请至少选择一个被合并的副食材', 'warning');
        return;
    }

    const btn = document.getElementById('skuMergeSubmitBtn');
    if (btn) btn.disabled = true;

    fetch('/api/inventory/skus/merge', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            primary_sku_id: primaryId,
            secondary_sku_ids: secIds,
            sync_vendor_memory: syncMemory
        })
    })
    .then(res => res.json())
    .then(ret => {
        if (btn) btn.disabled = false;
        if (ret.status === 'success') {
            closeModalById('skuMergeModal');
            loadInventoryData();
            showToast('食材合并成功，历史流水与别名映射已转移', 'success');
        } else {
            showToast('合并失败：' + (ret.msg || ret.message || '请稍后重试'), 'error');
        }
    })
    .catch(err => {
        if (btn) btn.disabled = false;
        console.error('合并食材异常:', err);
        showToast('合并请求失败，请检查网络', 'error');
    });
}

function deleteSkuDirect(skuId, skuName) {
    if (!confirm(`确定要删除/停用食材【${skuName}】吗？\n\n系统会自动判断：若无历史进货单据则彻底删除，若有单据关联则安全停用。`)) {
        return;
    }

    fetch('/api/inventory/skus/' + skuId, {
        method: 'DELETE'
    })
    .then(res => res.json())
    .then(ret => {
        if (ret.status === 'success') {
            loadInventoryData();
            showToast(ret.msg || '食材已处理', 'success');
        } else {
            showToast('删除失败：' + (ret.msg || '请稍后重试'), 'error');
        }
    })
    .catch(err => {
        console.error('删除食材异常:', err);
        showToast('删除请求失败', 'error');
    });
}

// -------------------------------------------------------------
// 极简 AI 发现逻辑 (Lean AI Insights Banner & Cards)
// -------------------------------------------------------------
let aiLeanInsightsData = null;
let aiLeanIgnoredSkuIds = new Set();

function loadAiLeanInsights() {
    const banner = document.getElementById('aiLeanBanner');
    if (!banner) return;
    // 店员不发 AI 洞察（owner 域接口），横幅保持隐藏
    if (isStaffRoleNow()) { banner.classList.add('hide'); return; }

    fetch('/api/ai-insights')
    .then(res => res.json())
    .then(ret => {
        if (ret.status !== 'success' || !ret.data) {
            banner.classList.add('hide');
            return;
        }
        const data = ret.data;
        aiLeanInsightsData = data;
        const rawItems = data.top_price_risers || [];
        const activeItems = rawItems.filter(it => !aiLeanIgnoredSkuIds.has(it.sku_id));

        if (activeItems.length === 0) {
            banner.classList.add('hide');
            return;
        }

        const sumText = document.getElementById('aiLeanSummaryText');
        if (sumText) {
            sumText.innerText = `检测到 ${activeItems.length} 项食材价格异常上涨，预估影响成本 HK$ ${data.total_impact_amount.toFixed(2)}`;
        }

        renderAiLeanCards(activeItems);
        banner.classList.remove('hide');
    })
    .catch(err => {
        console.error('加载极简 AI 发现异常:', err);
        if (banner) banner.classList.add('hide');
    });
}

function toggleAiLeanDetails() {
    const cards = document.getElementById('aiLeanCardsContainer');
    const btn = document.getElementById('btnToggleAiLean');
    if (!cards || !btn) return;
    const isHidden = cards.classList.contains('hide');
    if (isHidden) {
        cards.classList.remove('hide');
        btn.innerText = '收起';
    } else {
        cards.classList.add('hide');
        btn.innerText = '展开查看';
    }
}

function renderAiLeanCards(items) {
    const container = document.getElementById('aiLeanCardsContainer');
    if (!container) return;
    container.innerHTML = '';

    items.forEach(it => {
        const card = document.createElement('div');
        card.className = 'ai-lean-card-item';
        card.id = `aiLeanCard_${it.sku_id}`;

        card.innerHTML = `
            <div class="ai-lean-card-left">
                <div class="ai-lean-card-title">
                    <span style="color:#dc2626;">${w2Escape(it.name)}</span>
                    <span style="font-size:0.75rem; color:var(--text-muted);">· ${w2Escape(it.vendor)}</span>
                    <span class="badge badge-danger" style="font-size:0.7rem; padding:1px 6px;">涨幅 +${it.change_pct}%</span>
                </div>
                <div class="ai-lean-card-desc">
                    单价 $${it.earliest_price} -&gt; <strong style="color:#dc2626;">$${it.latest_price}</strong>/${w2Escape(it.unit)} · 累计多支出 <strong style="color:var(--primary);">HK$ ${it.impact_amount}</strong>
                </div>
            </div>
            <div class="ai-lean-card-actions">
                ${it.sku_id ? `<button type="button" class="btn-lean-act" onclick="viewPriceHistory(${Number(it.sku_id)})">查看走势</button>` : ''}
                <button type="button" class="btn-lean-act" onclick="copyAiLeanEvidence(${Number(it.sku_id)})" title="复制异动记录明细">复制记录</button>
                <button type="button" class="btn-lean-act" style="color:var(--text-muted);" onclick="ignoreAiLeanItem(${Number(it.sku_id)})">忽略</button>
            </div>
        `;
        container.appendChild(card);
    });
}

function copyAiLeanEvidence(skuId) {
    if (!aiLeanInsightsData || !aiLeanInsightsData.top_price_risers) return;
    const item = aiLeanInsightsData.top_price_risers.find(x => x.sku_id === skuId);
    if (!item || !item.evidence_text) return;

    navigator.clipboard.writeText(item.evidence_text).then(() => {
        showToast('已复制价格异动明细至剪贴板', 'success');
    }).catch(() => {
        showToast(item.evidence_text, 'info', TOAST_DURATION.long);
    });
}

function ignoreAiLeanItem(skuId) {
    aiLeanIgnoredSkuIds.add(skuId);
    const card = document.getElementById(`aiLeanCard_${skuId}`);
    if (card) card.style.opacity = '0';
    setTimeout(() => {
        loadAiLeanInsights();
        showToast('已忽略本次异动提醒', 'info');
    }, 200);
}

function toggleSkuActive(skuTarget) {
    const sku = _resolveSkuObject(skuTarget);
    if (!sku) {
        showToast('无法读取食材档案信息', 'error');
        return;
    }
    const isActive = sku.active !== 0;
    const targetActive = isActive ? 0 : 1;

    if (isActive) {
        if (!confirm(`确定要停用食材【${sku.name}】吗？\n\n停用后进货将不再自动匹配到此项，默认列表也不再显示。`)) {
            return;
        }
    }

    fetch('/api/inventory/skus/' + sku.id, {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ active: targetActive })
    })
    .then(res => res.json())
    .then(ret => {
        if (ret.status === 'success') {
            loadInventoryData();
        } else {
            showToast('操作失败：' + (ret.message || ret.msg || '请稍后重试'), 'error');
        }
    })
    .catch(err => {
        console.error('食材启停用请求失败:', err);
        showToast('操作失败，请检查网络后重试', 'error');
    });
}

function closeInvMoreMenu() {
    const menu = document.getElementById('invMoreMenu');
    if (menu) menu.classList.add('hide');
}

function loadSuppliersData() {
    fetch('/api/suppliers')
    .then(res => res.json())
    .then(ret => {
        if (ret.status !== 'success') return;
        const suppliers = ret.data || [];
        // 供应商 combobox 联想数据源
        availableSuppliers = suppliers;
        // 兼容旧 datalist（可能不存在于当前页面，做 null 防护）
        const datalist = document.getElementById('dlSuppliers');
        if (datalist) {
            datalist.innerHTML = '';
            suppliers.forEach(s => {
                const opt = document.createElement('option');
                opt.value = s.name;
                datalist.appendChild(opt);
            });
        }
    })
    .catch(err => console.error("加载供应商列表失败:", err));
}

let allArchiveReceipts = [];

function loadReceiptsHistory() {
    fetch('/api/receipts')
    .then(res => res.json())
    .then(ret => {
        if (ret.status !== 'success') return;

        allArchiveReceipts = ret.data || [];
        populateFilterSuppliers();
        applyArchiveFilters();
        updateTodoBar();
    });
}

function populateFilterSuppliers() {
    const sel = document.getElementById('fltSupplier');
    if (!sel) return;
    const currentVal = sel.value;
    sel.innerHTML = `<option value="">全部供应商</option>`;
    
    const set = new Map();
    if (typeof availableSuppliers !== 'undefined' && availableSuppliers) {
        // U-06：过滤软停用（active=0）供应商，脏名不进归档筛选下拉
        availableSuppliers.forEach(sup => {
            if (sup.active === 0 || sup.active === '0' || sup.active === false) return;
            set.set(sup.name, sup.supplier_code);
        });
    }
    allArchiveReceipts.forEach(r => {
        if (r.supplier_name && !set.has(r.supplier_name)) {
            set.set(r.supplier_name, r.supplier_code);
        }
    });

    set.forEach((code, name) => {
        const opt = document.createElement('option');
        opt.value = name;
        opt.textContent = code ? `${name} [${code}]` : name;
        if (name === currentVal) opt.selected = true;
        sel.appendChild(opt);
    });
}

function getTodayDateStr() {
    const d = new Date();
    const yyyy = d.getFullYear();
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `${yyyy}-${mm}-${dd}`;
}

let archiveFilterTimer = null;

function applyArchiveFilters(delay = 60) {
    if (!allArchiveReceipts) return;

    if (archiveFilterTimer) clearTimeout(archiveFilterTimer);

    archiveFilterTimer = setTimeout(() => {
        const supplierFilter = (document.getElementById('fltSupplier')?.value || '').trim().toLowerCase();
        const statusFilter = (document.getElementById('fltStatus')?.value || '').trim();
        // Wave 1：轻量 paid/unpaid 筛选（03 章六）
        const payStateFilter = (document.getElementById('fltPayState')?.value || '').trim();

        const todayStr = getTodayDateStr();

        let receiptDateFrom = document.getElementById('fltReceiptDateFrom')?.value || '';
        let receiptDateTo = document.getElementById('fltReceiptDateTo')?.value || '';
        if (receiptDateFrom && !receiptDateTo) {
            receiptDateTo = todayStr;
        }
        
        let uploadDateFrom = document.getElementById('fltUploadDateFrom')?.value || '';
        let uploadDateTo = document.getElementById('fltUploadDateTo')?.value || '';
        if (uploadDateFrom && !uploadDateTo) {
            uploadDateTo = todayStr;
        }
        
        let updateDateFrom = document.getElementById('fltUpdateDateFrom')?.value || '';
        let updateDateTo = document.getElementById('fltUpdateDateTo')?.value || '';
        if (updateDateFrom && !updateDateTo) {
            updateDateTo = todayStr;
        }
        
        const minAmtVal = document.getElementById('fltAmountMin')?.value;
        const maxAmtVal = document.getElementById('fltAmountMax')?.value;
        const amountMin = minAmtVal !== '' ? parseFloat(minAmtVal) : NaN;
        const amountMax = maxAmtVal !== '' ? parseFloat(maxAmtVal) : NaN;
        
        const sortMode = document.getElementById('fltSort')?.value || 'id_desc';

        let filtered = allArchiveReceipts.filter(r => {
            // 1. 供应商 Filter
            if (supplierFilter) {
                const supName = (r.supplier_name || '').toLowerCase();
                const supCode = (r.supplier_code || '').toLowerCase();
                if (supName !== supplierFilter && supCode !== supplierFilter && !supName.includes(supplierFilter)) {
                    return false;
                }
            }

            // 2. 状态 Filter
            if (statusFilter && (r.status || 'uploaded') !== statusFilter) {
                return false;
            }

            // 2a. 状态聚合筛选（老板口语，D-SUP-10 / 4.3）
            if (ArchiveUI.statusGroup) {
                const grp = STATUS_GROUP_MAP[ArchiveUI.statusGroup] || [];
                if (!grp.includes(r.status || 'uploaded')) return false;
            }

            // 2a-2. 关键字搜索：供应商名 / 单号 sheet_name / 单据 #id（M5）
            // 去掉用户按 placeholder「单据 #id」输入的前导 # 再比，#12 与 12 等价
            if (ArchiveUI.q) {
                const q = ArchiveUI.q.replace(/^#+/, '').toLowerCase();
                if (q) {
                    const hit =
                        (r.supplier_name || '').toLowerCase().includes(q) ||
                        (r.sheet_name || '').toLowerCase().includes(q) ||
                        String(r.id) === q ||
                        String(r.id).includes(q);
                    if (!hit) return false;
                }
            }

            // 2b. Wave 1 付款状态 Filter：unpaid=未付(含逾期)，paid=已付(含现结)
            if (payStateFilter === 'unpaid') {
                if (r.payment_status !== 'unpaid' && r.payment_status !== 'overdue') return false;
            } else if (payStateFilter === 'paid') {
                if (r.payment_status !== 'paid' && r.payment_status !== 'paid_at_delivery') return false;
            }

            // 3. 开单日期区间 Filter
            if (r.receipt_date) {
                const rDate = r.receipt_date.trim();
                if (receiptDateFrom && rDate < receiptDateFrom) return false;
                if (receiptDateTo && rDate > receiptDateTo) return false;
            } else if (receiptDateFrom || receiptDateTo) {
                return false;
            }

            // 4. 上传日期区间 Filter
            if (r.upload_date) {
                const uDate = r.upload_date.split(' ')[0].split('T')[0];
                if (uploadDateFrom && uDate < uploadDateFrom) return false;
                if (uploadDateTo && uDate > uploadDateTo) return false;
            } else if (uploadDateFrom || uploadDateTo) {
                return false;
            }

            // 5. 编辑日期区间 Filter
            if (r.updated_date) {
                const upDate = r.updated_date.split(' ')[0].split('T')[0];
                if (updateDateFrom && upDate < updateDateFrom) return false;
                if (updateDateTo && upDate > updateDateTo) return false;
            } else if (updateDateFrom || updateDateTo) {
                return false;
            }

            // 6. 金额区间 Filter
            const total = r.total_amount || 0.0;
            if (!isNaN(amountMin) && total < amountMin) return false;
            if (!isNaN(amountMax) && total > amountMax) return false;

            return true;
        });

        // 7. 排序 (Sort)
        filtered.sort((a, b) => {
            if (sortMode === 'amount_desc') {
                return (b.total_amount || 0) - (a.total_amount || 0);
            } else if (sortMode === 'amount_asc') {
                return (a.total_amount || 0) - (b.total_amount || 0);
            } else if (sortMode === 'receipt_date_desc') {
                return (b.receipt_date || '').localeCompare(a.receipt_date || '');
            } else if (sortMode === 'receipt_date_asc') {
                return (a.receipt_date || '').localeCompare(b.receipt_date || '');
            } else {
                return (b.id || 0) - (a.id || 0);
            }
        });

        renderArchiveTable(filtered);
    }, delay);
}

function resetArchiveFilters() {
    ['fltSupplier', 'fltStatus', 'fltPayState', 'fltReceiptDateFrom', 'fltReceiptDateTo', 'fltUploadDateFrom', 'fltUploadDateTo', 'fltUpdateDateFrom', 'fltUpdateDateTo', 'fltAmountMin', 'fltAmountMax', 'fltKeyword'].forEach(id => {
        const elem = document.getElementById(id);
        if (elem) elem.value = '';
    });
    const sortElem = document.getElementById('fltSort');
    if (sortElem) sortElem.value = 'id_desc';
    // 重置口语化聚合状态（09 方案）
    ArchiveUI.q = '';
    setArchiveStatusGroup('');
    setArchiveQuickRange('');
    if (typeof toggleArchiveAdvanced === 'function') {
        const adv = document.getElementById('archiveAdvanced');
        if (adv && !adv.classList.contains('hide')) toggleArchiveAdvanced();
    }
    applyArchiveFilters();
}

function renderStatusBadge(st) {
    const _SUB_LABEL = {
        uploaded: '已上传', parsing: '解析中', parsed: '待核对',
        edited: '已修改', flagged: '有问题', approved: '已入账', error: '失败',
    };
    if (st === 'uploaded' || st === 'parsing') {
        return '<span class="badge badge-warning" title="' + w2Escape(_SUB_LABEL[st] || st) + '">' + w2Escape(_SUB_LABEL[st] || '待处理') + '</span>';
    } else if (st === 'parsed') {
        return '<span class="badge badge-warning" title="AI自动识别落库">待核对</span>';
    } else if (st === 'edited') {
        return '<span class="badge badge-info" title="店员手工修改保存">已修改</span>';
    } else if (st === 'approved') {
        return '<span class="badge badge-success" title="老板审核通过">已入账</span>';
    } else if (st === 'flagged') {
        return '<span class="badge badge-danger" title="已标记">有问题</span>';
    } else if (st === 'error') {
        return '<span class="badge badge-danger" title="失败">失败</span>';
    } else {
        return '<span class="badge badge-secondary">' + w2Escape(st) + '</span>';
    }
}
window.renderStatusBadge = renderStatusBadge;

// 渲染归档表格逻辑（锁高防抖动与焦点恢复优化）
function renderArchiveTable(receipts) {
    const recordBadge = document.getElementById('archiveRecordCount');
    if (recordBadge) {
        recordBadge.textContent = '共 ' + receipts.length + ' 条记录';
    }

    const tbody = document.getElementById('archiveTableBody');
    if (!tbody) return;

    // 关键修复 1：重绘前锁定表格容器当前实际高度，防止 DOM 瞬间塌陷 (Collapsing) 导致视口跳晃
    const tableContainer = tbody.closest('.table-container');
    if (tableContainer) {
        const currentH = tableContainer.offsetHeight;
        if (currentH > 0) {
            tableContainer.style.minHeight = currentH + 'px';
        }
    }

    tbody.innerHTML = '';

    if (receipts.length === 0) {
        tbody.innerHTML = '<tr><td colspan="10" style="text-align:center; color:var(--text-muted); padding:36px;">未找到符合筛选条件的归档记录</td></tr>';
    } else {
        let html = '';
        receipts.forEach(function(r) {
            const st = r.status || 'uploaded';
            const statusBadgeHTML = renderStatusBadge(st);

            const supCode = r.supplier_code
                ? '<span class="badge badge-secondary" style="font-size:0.72rem; font-family:monospace; margin-left:4px; opacity:0.85;">' + w2Escape(r.supplier_code) + '</span>'
                : '';

            const rid = Number(r.id);
            let actionBtns = '<button class="btn btn-primary" style="padding:2px 6px; font-size:0.75rem; line-height:1.4;" onclick="loadReceiptDetail(' + rid + ')">详情</button>' +
                ' <a href="' + w2Escape(r.image_url || '#') + '" target="_blank" class="btn btn-secondary" style="padding:2px 6px; font-size:0.75rem; line-height:1.4;">原图</a>';
            if (st === 'error') {
                actionBtns += ' <button class="btn btn-secondary" style="padding:2px 6px; font-size:0.75rem; line-height:1.4;" onclick="retryReceiptFromArchive(' + rid + ')">重试</button>' +
                    ' <button class="btn btn-secondary" style="padding:2px 6px; font-size:0.75rem; line-height:1.4;" onclick="convertReceiptFromArchive(' + rid + ')">转手工</button>';
            }

            const upDate = renderDateCell(r.upload_date);
            const editDate = renderDateCell(r.updated_date);
            const recDate = renderDateCell(r.receipt_date);

            // P1-2：黄底 row-warning 绑定 quality_warnings / review_priority>0.6
            const _hasQw = Array.isArray(r.quality_warnings) && r.quality_warnings.length > 0;
            const _rps = typeof r.review_priority_score === 'number' ? r.review_priority_score : 0;
            const _rowWarn = (_hasQw || _rps > 0.6) ? ' class="row-warning"' : '';
            html += '<tr' + _rowWarn + '>' +
                '<td style="vertical-align:middle;">#' + rid + '</td>' +
                '<td style="vertical-align:middle;"><strong>' + w2Escape(r.supplier_name || '-') + '</strong>' + supCode + greyBadgeHtml(r.use_grey) + '</td>' +
                '<td style="vertical-align:middle;"><span style="font-size:0.82rem; color:var(--text-muted);">' + upDate + '</span></td>' +
                '<td style="vertical-align:middle;"><span style="font-size:0.82rem; color:#60a5fa;">' + editDate + '</span></td>' +
                '<td style="vertical-align:middle;"><span style="font-size:0.82rem; color:var(--text-muted);">' + recDate + '</span></td>' +
                '<td style="vertical-align:middle; text-align:center;"><strong style="font-size:1.02rem; font-weight:700;">' + formatCurrency(r.total_amount, r.currency) + '</strong></td>' +
                // Wave 2（D44）：归档列表展示单据级部门（未打 → 未分配）
                '<td class="col-center" style="vertical-align:middle;">' + (r.department_name
                    ? '<span class="badge badge-secondary" style="font-size:0.75rem;">' + w2Escape(r.department_name) + '</span>'
                    : '<span style="color:var(--text-muted); font-size:0.75rem;">未分配</span>') + '</td>' +
                '<td class="col-center" style="vertical-align:middle;">' + statusBadgeHTML + '</td>' +
                '<td class="col-center" style="vertical-align:middle;">' + payStatusLiteBadgeHtml(r) + '</td>' +
                '<td class="col-center" style="vertical-align:middle; white-space:nowrap; padding-right:4px;">' + actionBtns + '</td>' +
            '</tr>';
        });
        tbody.innerHTML = html;
    }

    // 关键修复 2：待 Chrome 浮层动画和焦点计算平滑完成后，在下一帧解锁恢复默认 minHeight
    requestAnimationFrame(() => {
        setTimeout(() => {
            if (tableContainer) {
                tableContainer.style.minHeight = '480px';
            }
        }, 200);
    });
}

// 彻底解决 Chrome 日历弹窗关闭后自动 scrollIntoView() 引发页面跳晃的问题
(function setupDateScrollLock() {
    let lockedScrollTop = null;

    function captureScroll() {
        const wrapper = document.querySelector('.main-wrapper');
        lockedScrollTop = wrapper ? wrapper.scrollTop : window.scrollY;
    }

    function restoreScroll() {
        if (lockedScrollTop === null) return;
        const wrapper = document.querySelector('.main-wrapper');
        if (wrapper) {
            wrapper.scrollTop = lockedScrollTop;
        }
        window.scrollTo(0, lockedScrollTop);
    }

    document.addEventListener('DOMContentLoaded', () => {
        const dateInputs = document.querySelectorAll('input[type="date"], .archive-date-grid button');
        dateInputs.forEach(el => {
            el.addEventListener('focus', captureScroll, { passive: true });
            el.addEventListener('mousedown', captureScroll, { passive: true });
            el.addEventListener('click', captureScroll, { passive: true });
            el.addEventListener('change', () => {
                restoreScroll();
                setTimeout(restoreScroll, 50);
                setTimeout(restoreScroll, 150);
                setTimeout(restoreScroll, 250);
            });
            el.addEventListener('blur', () => {
                setTimeout(restoreScroll, 50);
                setTimeout(restoreScroll, 150);
            });
        });
    });
})();


// Q33/W6 Q1：approve 必须携带 version（缺失 → 400 VERSION_REQUIRED）；
// 处理 409 VERSION_CONFLICT（提示 + 重载详情）；返回 Promise<boolean> 供弹窗决定是否关闭。
function approveReceipt(receiptId, knownVersion) {
    if (!confirm(`确定要把单据 #${receiptId} 审核通过吗？`)) return Promise.resolve(false);

    // D17/W6 Q1：始终提交 JSON body 含 version（详情加载时记录于 currentArchiveDetailData）
    const body = { version: knownVersion };
    return fetch(`/api/receipt/${receiptId}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    })
    .then(res => Promise.all([res.status, res.json().catch(() => null)]))
    .then(([httpStatus, ret]) => {
        if (httpStatus === 409 && ret && ret.code === 'VERSION_CONFLICT') {
            showToast('单据已被他人修改，已重新加载', 'warning');
            loadReceiptDetail(receiptId);   // 重载详情（含最新 version），弹窗保持打开
            return false;
        }
        if (httpStatus === 400 && ret && ret.code === 'VERSION_REQUIRED') {
            showToast('缺少版本号，已重新加载最新内容', 'warning');
            loadReceiptDetail(receiptId);
            return false;
        }
        // E-P1-4：403 人话统一（审批为 owner 专属，staff 触发时给出角色指引）
        if (toastHttpError(httpStatus, ret)) return false;
        if (!ret || ret.status !== 'success') {
            showToast('审核失败：' + ((ret && ret.msg) || '请稍后重试'), 'error');
            return false;
        }
        // P1-13: approve 成功后更新本地 version（响应契约带 version）
        if (currentArchiveDetailData) {
            const nv = extractVersionFromResponse(ret);
            if (nv != null) currentArchiveDetailData.version = nv;
        }
        showToast(`已审核通过单据 #${receiptId}`, 'success');
        loadReceiptsHistory();
        loadFinancePanel();     // Wave 1：approve 落库钩子可能写预期付款日快照 → 提醒联动
        return true;
    })
    .catch(err => {
        console.error('审核请求异常:', err);
        showToast('审核请求失败，请检查网络后重试', 'error');
        return false;
    });
}

function flagReceipt(receiptId) {
    if (!confirm(`确定要把单据 #${receiptId} 标记为异常吗？`)) return;
    fetch(`/api/receipt/${receiptId}/flag`, { method: 'POST' })
    .then(res => res.json())
    .then(ret => {
        if (ret.status !== 'success') {
            showToast('标记失败：' + (ret.msg || '请稍后重试'), 'error');
            return;
        }
        showToast(`已标记单据 #${receiptId} 为异常`, 'success');
        loadReceiptsHistory();
        loadFinancePanel();     // Wave 1：flagged 单据移出应付口径 → 提醒联动
    });
}

function modalApproveReceipt() {
    if (!currentArchiveReceiptId) return;
    // D16: 有未保存修改时审批不执行——保存是保存，审批是审批
    if (hasUnsavedArcChanges) {
        showToast('有未保存修改，请先保存单据再审批', 'warning');
        return;
    }
    // Q33: 携带详情加载时记录的 version；成功后才关闭弹窗
    const knownVersion = currentArchiveDetailData ? currentArchiveDetailData.version : null;
    approveReceipt(currentArchiveReceiptId, knownVersion).then(ok => {
        if (ok) closeArchiveModal(true);
    });
}

function modalFlagReceipt() {
    if (!currentArchiveReceiptId) return;
    flagReceipt(currentArchiveReceiptId);
    closeArchiveModal(true);
}

// ---- Q35：归档列表 error 行的「重试 / 转手工」入口 ----
// rid 在 renderArchiveTable 已经过 Number() 强制为数字；此处再做防御性校验。
function retryReceiptFromArchive(receiptId) {
    const rid = Number(receiptId);
    if (!Number.isFinite(rid) || rid <= 0) return;
    if (!confirm(`确定对单据 #${rid} 重新发起识别吗？`)) return;
    showToast(`单据 #${rid} 重试已提交…`, 'info');
    fetch(`/api/receipt/${rid}/retry`, { method: 'POST' })
        .then(res => res.json())
        .then(ret => {
            if (ret.status === 'success' || ret.status === 'queued') {
                showToast(`单据 #${rid} 已重新进入识别流程`, 'success');
                loadReceiptsHistory();   // 刷新归档列表（状态变为 parsing/uploaded）
            } else {
                showToast('重试失败：' + (ret.msg || '请稍后重试'), 'error');
            }
        })
        .catch(err => {
            console.error('重试请求失败:', err);
            showToast('重试请求失败，请检查网络后重试', 'error');
        });
}

function convertReceiptFromArchive(receiptId) {
    const rid = Number(receiptId);
    if (!Number.isFinite(rid) || rid <= 0) return;
    if (!confirm(`确定将单据 #${rid} 转为手工录入吗？将保留原图与已识别预填。`)) return;
    showToast(`单据 #${rid} 转手工请求已提交…`, 'info');
    fetch(`/api/receipt/${rid}/convert_manual`, { method: 'POST' })
        .then(res => res.json())
        .then(ret => {
            if (ret.status === 'success') {
                showToast(`单据 #${rid} 已转为手工录入，可进入详情编辑`, 'success');
                loadReceiptsHistory();   // 刷新归档列表（状态变为 edited）
            } else {
                showToast('转手工失败：' + (ret.msg || '请稍后重试'), 'error');
            }
        })
        .catch(err => {
            console.error('转手工请求失败:', err);
            showToast('转手工请求失败，请检查网络后重试', 'error');
        });
}

let currentArchiveReceiptId = null;
// 归档详情完整 data（含 version/doc_form/settlement_type/payment_mark，字段可能暂时缺失）
let currentArchiveDetailData = null;
let arcZoom = 1.0;
let arcRotation = 0;
let arcPanX = 0;
let arcPanY = 0;
let arcActiveTool = null;
let hasUnsavedArcChanges = false;

function markArcDirty() {
    hasUnsavedArcChanges = true;
}

function isRagDataOnlyEnabled() {
    const cb = document.getElementById('arcDataOnlyToggle');
    return !!(cb && cb.checked);
}
function toggleRagDataOnly() {
    if (currentArchiveReceiptId) loadReceiptDetail(currentArchiveReceiptId);
}
function loadReceiptDetail(receiptId) {
    const qs = isRagDataOnlyEnabled() ? '?data_only=true' : '';
    fetch(`/api/receipt/${receiptId}${qs}`)
    .then(res => res.json())
    .then(ret => {
        if (ret.status !== 'success') {
            showToast('无法加载单据详情：' + (ret.msg || '请稍后重试'), 'error');
            return;
        }

        currentArchiveReceiptId = ret.receipt_id;
        currentArchiveDetailData = ret.data;   // D17: 记下 version 等字段，保存时随提交做乐观锁
        hasUnsavedArcChanges = false;
        const data = ret.data;

        document.getElementById('archiveModalTitle').innerText = ` 历史收据明细校对 - 单据 #${ret.receipt_id}`;
        document.getElementById('archivePreviewImg').src = ret.image_url;
        document.getElementById('btnArchiveOpenRaw').href = ret.image_url;
        resetArchiveImgTransform();

        renderArchiveForm(data);

        // 绑定输入监听器，检测未保存修改（结算方式下拉变更同样算未保存修改）
        document.querySelectorAll('#arcSupplier, #arcDate, #arcSheet, #arcTotal').forEach(inp => {
            inp.oninput = markArcDirty;
        });
        const arcSettlementSel = document.getElementById('arcSettlementType');
        if (arcSettlementSel) arcSettlementSel.onchange = markArcDirty;
        // Wave 2（D44）：归档单据级部门下拉变更同样算未保存修改
        const arcDeptSel = document.getElementById('arcDepartmentId');
        if (arcDeptSel) arcDeptSel.onchange = markArcDirty;

        applyModalRoleVisibility();
        document.getElementById('archiveDetailModal').classList.remove('hide');
    })
    .catch(err => {
        console.error('加载单据详情请求失败:', err);
        showToast('加载单据详情失败，请检查网络后重试', 'error');
    });
}

function closeArchiveModal(force = false) {
    if (!force && hasUnsavedArcChanges) {
        const confirmExit = confirm("检测到您有未保存的修改，确定要放弃修改并退出吗？");
        if (!confirmExit) return;
    }
    document.getElementById('archiveDetailModal').classList.add('hide');
    currentArchiveReceiptId = null;
    currentArchiveDetailData = null;
    hasUnsavedArcChanges = false;
}

// P3/R5：归档履历徽章按真实 action_type 映射，消除"upload/其余一律校对编辑"
// 的二分失真（approve/flag/retry 等曾被误标为"校对编辑"）。未知类型回退显示
// 原始值，保证徽章语义不失真。
const AUDIT_ACTION_BADGES = {
    'upload':           { label: '初始上传',   cls: 'badge-secondary' },
    'auto_save':        { label: 'AI自动入库', cls: 'badge-warning' },
    'save_edited':      { label: '店员人工修改', cls: 'badge-info' },
    'edit':             { label: '店员人工修改', cls: 'badge-info' },
    'approve':          { label: '审批通过',   cls: 'badge-success' },
    'approve_receipt':  { label: '审批通过',   cls: 'badge-success' },
    'flag':             { label: '标记异常',   cls: 'badge-warning' },
    'flag_receipt':     { label: '标记异常',   cls: 'badge-warning' },
    'retry':            { label: '重试识别',   cls: 'badge-secondary' },
    'convert_manual':   { label: '转手工录入', cls: 'badge-secondary' },
    'discard_receipt':  { label: '放弃单据',   cls: 'badge-secondary' },
    'ocr_parsed':       { label: '识别完成',   cls: 'badge-success' },
    'ocr_error':        { label: '识别失败',   cls: 'badge-danger' },
    'ocr_attempt':      { label: '识别尝试',   cls: 'badge-secondary' },
    'async_parse':      { label: '异步解析',   cls: 'badge-secondary' },
    'startup_recovery': { label: '启动恢复',   cls: 'badge-secondary' },
    'recon_resolve':    { label: '对账处理',   cls: 'badge-secondary' },
    'recon_confirm':    { label: '对账确认',   cls: 'badge-success' },
    'supplier_merge':   { label: '供应商合并', cls: 'badge-secondary' },
    'state_change':     { label: '状态变更',   cls: 'badge-secondary' },
    'feedback':         { label: '用户反馈',   cls: 'badge-secondary' },
    'feedback_distilled': { label: '先验提炼', cls: 'badge-secondary' },
    'timeout':          { label: '识别超时',   cls: 'badge-danger' },
};

function auditActionBadge(actionType) {
    const spec = AUDIT_ACTION_BADGES[actionType];
    if (spec) {
        return `<span class="badge ${spec.cls}">${w2Escape(spec.label)}</span>`;
    }
    return `<span class="badge badge-secondary">${w2Escape(String(actionType || '未知'))}</span>`;
}

function renderArchiveForm(data) {
    document.getElementById('arcSupplier').value = data.supplier_name || '';
    document.getElementById('arcDate').value = data.date || '';
    document.getElementById('arcSheet').value = data.sheet_name || '';
    document.getElementById('arcTotal').value = data.total_amount ? data.total_amount.toFixed(2) : '0.00';

    // M3/D4: 初始化结算方式与付款标记（字段可能缺失，容错）
    applySettlementToForm('arc', data);

    // F-P1-3 多币种
    const arcCur = document.getElementById('arcCurrency');
    if (arcCur) arcCur.value = (data.currency || 'HKD').toUpperCase();
    const inpCur = document.getElementById('inpCurrency');
    if (inpCur && data.currency) inpCur.value = (data.currency || 'HKD').toUpperCase();
    renderCurrencySymbol();

    // E-P1-1 灰测 Tag：标题旁徽标
    const titleEl = document.getElementById('archiveModalTitle');
    if (titleEl) {
        // 移除旧徽标
        const old = titleEl.querySelector('.badge-grey');
        if (old) old.remove();
        if (data.use_grey) {
            const badge = document.createElement('span');
            badge.className = 'badge-grey';
            badge.style.marginLeft = '8px';
            badge.textContent = '灰测组';
            badge.title = '灰测组处理';
            titleEl.appendChild(badge);
        }
    }

    // D-P1-3 RAG data_only 调试卡可见性（截断显示，完整见接口 data_only=true）
    const ragCard = document.getElementById('arcRagContextCard');
    const ragPre = document.getElementById('arcRagContextPre');
    if (ragCard && ragPre) {
        if (isRagDataOnlyEnabled() && (data.rag_context != null)) {
            const ctxRaw = String(data.rag_context || '').trim();
            let display = ctxRaw ? ctxRaw : '（空）无 RAG 上下文（冷启动或未检索）';
            // 截断：前端调试卡最多显示 800 字符，避免超长撑满弹窗
            if (display.length > 800) {
                display = display.slice(0, 800) + '\n...（已截断，完整 rag_context_json 见 GET /api/receipt/{id}?data_only=true 审计日志）';
            }
            ragPre.textContent = display;
            ragCard.classList.remove('hide');
        } else {
            ragCard.classList.add('hide');
        }
    }

    // Wave 2（D44）：归档单据级部门下拉（只列 active；历史停用部门选择仍保留显示）
    populateDeptSelect(document.getElementById('arcDepartmentId'), data.department_id);

    const tbody = document.getElementById('arcTableBody');
    tbody.innerHTML = '';

    const items = data.items || [];
    items.forEach(item => appendArcTableRow(item));

    // 渲染操作与编辑改动履历 Logs
    const logContainer = document.getElementById('arcAuditLogsContainer');
    if (logContainer) {
        const logs = data.audit_logs || [];
        if (logs.length === 0) {
            logContainer.innerHTML = `<div style="color:var(--text-muted);">暂无编辑履历记录</div>`;
        } else {
            let html = "";
            logs.forEach(l => {
                // P3/R5：按真实 action_type 映射徽章（消除二分失真）
                const actionType = l.action_type || l.action;
                const typeBadge = auditActionBadge(actionType);
                const operator = l.operator || l.who || 'unknown';
                const ts = l.timestamp || l.ts || '';
                let details = l.details;
                if (!details) {
                    if (l.field && l.field !== 'receipt' && l.field !== 'status') {
                        details = `${l.field}: ${JSON.stringify(l.old)} -> ${JSON.stringify(l.new)}`;
                    } else if (l.field === 'receipt') {
                        details = `新建单据 #${l.new || ''}`;
                    } else if (l.field === 'status') {
                        details = `状态: ${l.old || ''} -> ${l.new || ''}`;
                    } else {
                        details = '';
                    }
                }
                html += `
                    <div style="padding:4px 0; border-bottom:1px solid rgba(255,255,255,0.05); display:flex; justify-content:space-between; align-items:center;">
                        <div>
                            <strong> ${w2Escape(operator)}</strong> ${typeBadge}
                            <span style="margin-left:8px; color:var(--text-main);">${w2Escape(details)}</span>
                        </div>
                        <div style="color:var(--text-muted); font-size:0.75rem; white-space:nowrap; margin-left:12px;"> ${renderDateCell(ts)}</div>
                    </div>
                `;
            });
            logContainer.innerHTML = html;
        }
    }

    // U-2：渲染 AI 决策履历（extract 各轮 + audit 交叉审核）
    renderArcAiDecisions(data.ai_decisions);
}

// U-2：AI 决策履历徽章映射
// extract_ok→绿「识别完成」/ gate_reject→红「门禁拦截」/ extract_fail→灰「识别失败」
// audit→蓝「交叉审核」/ 审核跳过→「审核跳过」/ auto_save→黄「AI自动入库」
function aiDecisionBadge(decisionType, p) {
    if (decisionType === 'audit') {
        const skipped = !!(p && (p.skipped || /^audit_(disabled|error)/.test(String(p.reason || ''))));
        if (skipped) {
            return `<span class="badge badge-secondary">${w2Escape('审核跳过')}</span>`;
        }
        return `<span class="badge" style="background:#cce5ff; color:#004085;">${w2Escape('交叉审核')}</span>`;
    }
    if (decisionType === 'auto_save') {
        return `<span class="badge badge-warning">${w2Escape('AI自动入库')}</span>`;
    }
    const status = String((p && p.status) || '');
    if (status === 'extract_ok') return `<span class="badge badge-success">${w2Escape('识别完成')}</span>`;
    if (status === 'gate_reject') return `<span class="badge badge-danger">${w2Escape('门禁拦截')}</span>`;
    if (status === 'extract_fail') return `<span class="badge badge-secondary">${w2Escape('识别失败')}</span>`;
    return `<span class="badge badge-secondary">${w2Escape(String(decisionType || 'AI 决策'))}</span>`;
}

// U-2：AI 决策履历渲染（arcAiDecisionsContainer）
// 安全纪律（AC-D3 阻断级）：所有动态文本一律 w2Escape 后再插 innerHTML
function renderArcAiDecisions(decisions) {
    const container = document.getElementById('arcAiDecisionsContainer');
    if (!container) return;
    const list = Array.isArray(decisions) ? decisions : [];
    if (list.length === 0) {
        container.innerHTML = `<div style="color:var(--text-muted);">暂无 AI 决策记录</div>`;
        return;
    }
    let html = '';
    list.forEach(d => {
        let p = {};
        try {
            p = JSON.parse(d.ai_value || '{}');
            // 兼容历史 audit 行的双重编码（log_ai_decision 曾收 json.dumps 字符串）
            if (typeof p === 'string') p = JSON.parse(p);
        } catch (e) { p = {}; }
        if (!p || typeof p !== 'object') p = {};
        const badge = aiDecisionBadge(d.decision_type, p);
        let summary;
        if (d.decision_type === 'audit') {
            summary = [w2Escape(String(d.engine || '')), w2Escape(String(p.reason || ''))]
                .filter(Boolean).join(' · ');
        } else if (d.decision_type === 'auto_save') {
            summary = 'AI自动识别落库';
        } else {
            const attemptNo = Number(p.attempt || 0);
            const segs = [
                `第${w2Escape(String(attemptNo))}轮`,
                w2Escape(String(p.engine || d.engine || '')),
                w2Escape(String(p.status || '')),
            ];
            if (p.gate_err) segs.push(w2Escape(String(p.gate_err)));
            summary = segs.filter(Boolean).join(' · ');
        }
        html += `
            <div style="padding:4px 0; border-bottom:1px solid rgba(255,255,255,0.05); display:flex; justify-content:space-between; align-items:center;">
                <div>
                    ${badge}
                    <span style="margin-left:8px; color:var(--text-main);">${summary}</span>
                </div>
                <div style="color:var(--text-muted); font-size:0.75rem; white-space:nowrap; margin-left:12px;">${w2Escape(d.ts || '')}</div>
            </div>
        `;
    });
    container.innerHTML = html;
}

function appendArcTableRow(item = {}) {
    const tbody = document.getElementById('arcTableBody');
    const tr = document.createElement('tr');

    const rawName = item.raw_name || item.name || '';
    const qty = Number(item.quantity || 1.0);
    const unit = item.raw_unit || item.unit || 'kg';
    const price = Number(item.unit_price || 0.00);
    const amount = Number(item.amount || (qty * price));
    const skuId = item.sku_id || '';

    if (unit && !availableUnits.includes(unit)) {
        availableUnits.push(unit);
        updateGlobalDatalistUnits();
    }

    const arcRowIdx = tbody.children.length;
    // D-P1-4 划线作废：is_void 行置灰 + 删除线 + 作废徽标 + 联动保存
    const isVoid = !!(item.is_void);
    const actualQtyArc = (item.actual_qty != null && item.actual_qty !== '') ? String(item.actual_qty) : '';
    if (isVoid) tr.style.opacity = '0.55';
    // P0-2：归档弹窗明细行与 Tab1 同口径——品名/单位属性插值过 w2Escape
    tr.innerHTML = `
        <td>
            <input type="text" class="inp-name" value="${w2Escape(rawName)}" placeholder="品名" oninput="markArcDirty()" style="${isVoid ? 'text-decoration:line-through; color:#6c757d;' : ''}">
            ${skuId ? `<span class="badge badge-success">已关联</span>` : ''}
            ${isVoid ? `<span class="badge badge-secondary" title="划线作废，不计入总额">作废</span>` : ''}
        </td>
        <td><div style="display:flex; flex-direction:column; gap:4px;"><input type="number" step="0.01" class="inp-qty" value="${qty}" oninput="markArcDirty(); recalcArcRow(this)" title="数量" placeholder="数量" ${isVoid ? 'disabled style="text-decoration:line-through;"' : ''}><input type="number" step="0.01" class="inp-actual-qty" value="${w2Escape(actualQtyArc)}" placeholder="实收" title="实际数量（手写改量，留空则按数量计）" style="padding:4px; font-size:0.78rem;" oninput="markArcDirty(); recalcArcRow(this)" ${isVoid ? 'disabled style="text-decoration:line-through;"' : ''}></div></td>
        <td>
            <div class="unit-combobox-wrap">
                <input type="text" class="inp-unit form-control" value="${w2Escape(unit)}" placeholder="单位" style="padding:4px; font-size:0.8rem; ${isVoid ? 'text-decoration:line-through; color:#6c757d;' : ''}" onfocus="this.select(); openUnitMenu(this)" onclick="openUnitMenu(this)" oninput="markArcDirty(); renderUnitMenuItems(this, this.nextElementSibling)" onblur="closeUnitMenuDelay(this)" ${isVoid ? 'disabled' : ''}>
                <div class="unit-dropdown-menu hide"></div>
            </div>
        </td>
        <td><input type="number" step="0.01" class="inp-price" value="${price}" oninput="markArcDirty(); recalcArcRow(this)" ${isVoid ? 'disabled' : ''}></td>
        <td><input type="number" step="0.01" class="inp-amount" value="${amount}" oninput="markArcDirty(); recalcArcTotalSum()" ${isVoid ? 'disabled' : ''}></td>
        <td>
            <select class="inp-dept form-control" title="本行归属部门" style="width:100%; padding:4px; font-size:0.8rem;">
                ${deptSelectOptionsHtml(item.cost_center_id)}
            </select>
        </td>
        <td style="text-align:center; white-space:nowrap;">
            <label style="font-size:0.72rem; display:inline-flex; align-items:center; gap:3px; cursor:pointer; margin-right:4px;"><input type="checkbox" class="inp-void" ${isVoid ? 'checked' : ''} onchange="toggleArcVoid(this)">作废</label>
            <button class="btn btn-danger" style="padding:2px 6px; font-size:0.75rem;" onclick="removeArcRow(this)">删除</button>
        </td>
        <td style="text-align:center; white-space:nowrap;">
            <button type="button" class="btn btn-secondary" style="padding:3px 8px; font-size:0.78rem;" onclick="openRowFeedbackModal(this)">反馈</button>
            <span class="feedback-badge badge" style="font-size:0.68rem; display:none;"></span>
            <div class="feedback-cell" data-row-index="${arcRowIdx}" style="display:none;">
                <div class="feedback-actions">
                    <button type="button" class="btn-like" data-like="1" onclick="handleArcFeedback(this, 1)">点赞</button>
                    <button type="button" class="btn-dislike" data-like="-1" onclick="handleArcFeedback(this, -1)">点踩</button>
                </div>
                <textarea class="feedback-comment" placeholder="反馈原因（选填）" rows="2" maxlength="2000" oninput="onFeedbackCommentInput(this)"></textarea>
                <span class="feedback-charcount" style="font-size:0.70rem; color:var(--text-muted); align-self:flex-end;"></span>
                <button type="button" class="btn btn-secondary feedback-submit" onclick="submitArcFeedback(this)">提交反馈</button>
                <span class="feedback-status" style="font-size:0.72rem; color:var(--text-muted);"></span>
            </div>
        </td>
    `;
    tr.dataset.isVoid = isVoid ? '1' : '0';
    tbody.appendChild(tr);

    // Wave 2（D44）：归档行内任何手动改动（含部门下拉）→ data-manual + 未保存脏标记；
    // "应用到全部明细"跳过已手动改过的行（04 章三）
    tr.addEventListener('input', () => { tr.dataset.manual = '1'; markArcDirty(); });
    tr.addEventListener('change', () => { tr.dataset.manual = '1'; markArcDirty(); });
}

function addArcEmptyRow() {
    appendArcTableRow({ raw_name: '', quantity: 1.0, raw_unit: 'kg', unit_price: 0.0, amount: 0.0 });
    const container = document.querySelector('#archiveDetailModal .table-container');
    if (container) {
        container.scrollTop = container.scrollHeight;
    }
}

function removeArcRow(btn) {
    btn.closest('tr').remove();
    markArcDirty();
    recalcArcTotalSum();
}

function recalcArcRow(inputElem) {
    const tr = inputElem.closest('tr');
    const qty = parseFloat(tr.querySelector('.inp-qty').value) || 0;
    const price = parseFloat(tr.querySelector('.inp-price').value) || 0;

    const amtInput = tr.querySelector('.inp-amount');
    amtInput.value = (qty * price).toFixed(2);

    recalcArcTotalSum();
}

function recalcArcTotalSum() {
    const tbody = document.getElementById('arcTableBody');
    const rows = tbody.querySelectorAll('tr');
    let sum = 0.0;
    rows.forEach(tr => {
        // D-P1-4：划线作废行不计入总额（与后端算术门禁口径一致）
        if (tr.dataset && tr.dataset.isVoid === '1') return;
        const amt = parseFloat(tr.querySelector('.inp-amount').value) || 0;
        sum += amt;
    });
    document.getElementById('arcTotal').value = sum.toFixed(2);
}

// D-P1-4：归档明细行作废勾选联动（置灰 + 总额重算）
function toggleArcVoid(cb) {
    const tr = cb.closest('tr');
    if (!tr) return;
    tr.dataset.isVoid = cb.checked ? '1' : '0';
    tr.style.opacity = cb.checked ? '0.55' : '';
    tr.querySelectorAll('.inp-name, .inp-unit').forEach(inp => {
        inp.style.textDecoration = cb.checked ? 'line-through' : '';
        if (!cb.checked) inp.style.color = '';
        else inp.style.color = '#6c757d';
    });
    tr.querySelectorAll('.inp-qty, .inp-actual-qty, .inp-price, .inp-amount').forEach(inp => {
        inp.disabled = cb.checked;
        if (inp.classList.contains('inp-qty') || inp.classList.contains('inp-actual-qty')) {
            inp.style.textDecoration = cb.checked ? 'line-through' : '';
        }
    });
    const voidBadge = tr.querySelector('.void-badge-arc');
    if (cb.checked && !voidBadge) {
        const span = document.createElement('span');
        span.className = 'badge badge-secondary void-badge-arc';
        span.textContent = '作废';
        span.title = '划线作废，不计入总额';
        const nameTd = tr.querySelector('td');
        if (nameTd) nameTd.appendChild(span);
    } else if (!cb.checked && voidBadge) {
        voidBadge.remove();
    }
    markArcDirty();
    recalcArcTotalSum();
}

// Q32: 归档弹窗保存互斥旗标——保存中禁止再次触发（防双击重复提交）
let isSavingArchive = false;

function submitSaveArchiveEdited() {
    if (isSavingArchive) return;   // Q32: 保存进行中，忽略重复点击

    const supplierName = document.getElementById('arcSupplier').value.trim();
    const dateStr = document.getElementById('arcDate').value.trim();
    const sheetName = document.getElementById('arcSheet').value.trim();
    const totalAmt = parseFloat(document.getElementById('arcTotal').value) || 0.00;

    if (!supplierName || supplierName === '通用供应商') {
        showToast('请填写供应商名称', 'error');
        return;
    }
    if (!dateStr || !/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) {
        showToast('请选择开单日期', 'error');
        return;
    }
    const m = dateStr.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    const y = Number(m[1]), mo = Number(m[2]), d = Number(m[3]);
    const dt = new Date(y, mo - 1, d);
    if (dt.getFullYear() !== y || dt.getMonth() !== mo - 1 || dt.getDate() !== d) {
        showToast('开单日期不正确，请选择真实存在的日历日期', 'error');
        return;
    }
    if (isNaN(totalAmt) || totalAmt <= 0) {
        showToast('单据总金额必须大于 0', 'error');
        return;
    }

    const tbody = document.getElementById('arcTableBody');
    const rows = tbody.querySelectorAll('tr');
    registerNewUnitsFromRows(rows);

    const items = [];
    let rowErr = null;
    let rowIndex = 0;
    rows.forEach(tr => {
        rowIndex++;
        const nameVal = tr.querySelector('.inp-name').value.trim();
        const qtyVal = parseFloat(tr.querySelector('.inp-qty').value);
        const priceVal = parseFloat(tr.querySelector('.inp-price').value);
        if (!nameVal && rows.length === 1) {
            rowErr = `第 ${rowIndex} 行的品名不能为空，请填写或删除该行`;
            return;
        }
        if (nameVal) {
            if (isNaN(qtyVal) || qtyVal <= 0 || isNaN(priceVal) || priceVal < 0) {
                if (!rowErr) rowErr = `第 ${rowIndex} 行的单价或数量不是有效数字，请重新输入`;
            }
            const deptInput = tr.querySelector('.inp-dept');
            const actualInput = tr.querySelector('.inp-actual-qty');
            const actualRaw = actualInput ? actualInput.value.trim() : '';
            items.push({
                name: nameVal,
                quantity: isNaN(qtyVal) ? 1.0 : qtyVal,
                unit: tr.querySelector('.inp-unit').value.trim(),
                unit_price: isNaN(priceVal) ? 0.00 : priceVal,
                amount: parseFloat(tr.querySelector('.inp-amount').value) || 0.00,
                actual_qty: actualRaw !== '' && !isNaN(parseFloat(actualRaw)) ? parseFloat(actualRaw) : null,
                // Wave 2（D44）：明细行成本中心——行内下拉实际显示值（未打部门 → null）
                cost_center_id: (deptInput && deptInput.value) ? Number(deptInput.value) : null,
                // D-P1-4：划线作废状态随行保存
                is_void: !!(tr.dataset && tr.dataset.isVoid === '1')
            });
        }
    });

    if (rowErr) {
        showToast(rowErr, 'error');
        return;
    }

    if (items.length === 0) {
        showToast('请至少输入一行消费明细', 'warning');
        return;
    }

    // 变动提交二次确认（避免误触保存）
    if (hasUnsavedArcChanges) {
        const confirmSave = confirm("检测到您对单据内容进行了修改，确定要提交保存并写入履历日志吗？");
        if (!confirmSave) {
            return; // 用户选择取消，中断保存
        }
    }

    const arcSettlementSel = document.getElementById('arcSettlementType');
    const settlementType = arcSettlementSel ? (arcSettlementSel.value || null) : null;
    // M3/D4 结算方式必填化：归档保存同样要求已标记结算方式，未标记 → 阻断
    if (!requireSettlementMarked(settlementType)) {
        return;
    }

    const src = currentArchiveDetailData || {};
    // Wave 2（D44）：归档单据级部门（一键填充目标）；未打部门 → null 可空保存
    const arcDeptSel = document.getElementById('arcDepartmentId');
    const departmentId = (arcDeptSel && arcDeptSel.value) ? Number(arcDeptSel.value) : null;
    // F-P1-3 多币种
    const arcCurSel = document.getElementById('arcCurrency');

    const data = {
        supplier_name: supplierName || '通用供应商',
        date: dateStr,
        sheet_name: sheetName,
        total_amount: totalAmt,
        items: items,
        settlement_type: settlementType,
        department_id: departmentId,
        currency: (arcCurSel && arcCurSel.value ? arcCurSel.value : (src.currency || 'HKD'))
    };
    // U-05：付款标记改为枚举下拉，保存以用户选择为准；弹窗缺失时回落 AI 原值
    const arcMarkSel = document.getElementById('arcPaymentMark');
    if (arcMarkSel && arcMarkSel.value) data.payment_mark = arcMarkSel.value;
    else if (src.payment_mark != null) data.payment_mark = src.payment_mark;
    if (src.doc_form != null) data.doc_form = src.doc_form;
    if (src.layout_type != null) data.layout_type = src.layout_type;
    // D17: 乐观锁版本号（加载详情时记录）
    if (src.version != null) data.version = src.version;

    const payload = buildSavePayloadFromData(data, currentArchiveReceiptId);
    payload.source = 'manual';

    isSavingArchive = true;
    const arcSaveBtn = document.getElementById('btnSaveArchive');
    if (arcSaveBtn) arcSaveBtn.disabled = true;

    fetch('/api/save_edited', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
    .then(res => Promise.all([res.status, res.json().catch(() => null)]))
    .then(([httpStatus, ret]) => {
        // D17: 版本冲突——单据已被他人修改；留副本 + 显式确认后重载（P3/R5）
        if (httpStatus === 409 && ret && ret.code === 'VERSION_CONFLICT') {
            handleVersionConflictReload(currentArchiveReceiptId, data,
                () => loadReceiptDetail(currentArchiveReceiptId));
            return;
        }
        if (httpStatus === 400 && ret && ret.code === 'VERSION_REQUIRED') {
            showToast('缺少版本号，已重新加载最新内容', 'warning');
            if (currentArchiveReceiptId) loadReceiptDetail(currentArchiveReceiptId);
            return;
        }
        // E-P1-4：403 人话统一（同步表单提交路径，与 fetch 包装双保险，去重由 toast key 保证）
        if (toastHttpError(httpStatus, ret)) return;
        if (!ret || ret.status !== 'success') {
            showToast('保存失败：' + ((ret && ret.msg) || '请稍后重试'), 'error');
            return;
        }
        closeArchiveModal(true); // 强制关闭弹窗并重置状态

        loadReceiptsHistory();
        loadInventoryData();
        loadSuppliersData();
        loadFinancePanel();     // Wave 1：save_edited 快照钩子可能写预期付款日 → 提醒联动
    })
    .catch(err => {
        console.error('保存归档单据请求失败:', err);
        showToast('保存请求失败，请检查网络后重试', 'error');
    })
    .finally(() => {
        // Q32: 无论成功/失败/冲突，恢复保存按钮可再次触发
        isSavingArchive = false;
        const arcSaveBtn2 = document.getElementById('btnSaveArchive');
        if (arcSaveBtn2) arcSaveBtn2.disabled = false;
    });
}

function toggleArchiveZoomMode() {
    if (arcActiveTool === 'zoom') {
        arcActiveTool = null;
        arcZoom = 1.0;
    } else {
        arcActiveTool = 'zoom';
        arcZoom = 2.0;
    }
    applyArchiveImgTransform();
}

function rotateArchiveImg() {
    arcRotation = (arcRotation + 90) % 360;
    applyArchiveImgTransform();
}

function resetArchiveImgTransform() {
    arcZoom = 1.0;
    arcRotation = 0;
    arcPanX = 0;
    arcPanY = 0;
    arcActiveTool = null;
    applyArchiveImgTransform();
}

function applyArchiveImgTransform() {
    const img = document.getElementById('archivePreviewImg');
    if (img) {
        img.style.transform = `translate(${arcPanX}px, ${arcPanY}px) scale(${arcZoom}) rotate(${arcRotation}deg)`;
    }
}

// -------------------------------------------------------------
// M5 价格走势：专业 Modal（曲线 + 30 日均价/异动指标 + 明细表）
// 替代原生 alert；口径对齐 PRD >10% / 技术方案「价格走势可视化」
// 纯函数 computePriceHistorySummary / buildPriceChartSvg 可测
// -------------------------------------------------------------
// var：便于 node vm 逻辑测读取；与 code_engine.check_price_anomaly 阈值一致
var PRICE_ANOMALY_THRESHOLD = 0.10;

/** 解析 YYYY-MM-DD；非标准标签返回 null */
function parsePriceHistoryDate(dateStr) {
    const s = String(dateStr || '').trim();
    if (s.length < 10 || s[4] !== '-' || s[7] !== '-') return null;
    const d = new Date(s.slice(0, 10) + 'T00:00:00');
    return Number.isNaN(d.getTime()) ? null : d;
}

/**
 * 从价格点序列计算看板摘要（与后端 summary 同语义，前端可独立重算/回退）。
 * points: [{date, unit_price, ...}] 已按日期升序
 */
function computePriceHistorySummary(points, opts) {
    const threshold = (opts && opts.threshold != null) ? opts.threshold : PRICE_ANOMALY_THRESHOLD;
    const windowDays = (opts && opts.windowDays != null) ? opts.windowDays : 30;
    const now = (opts && opts.now) ? opts.now : new Date();
    const list = Array.isArray(points) ? points : [];
    const prices = list
        .map(p => Number(p.unit_price))
        .filter(n => Number.isFinite(n) && n > 0);

    if (prices.length === 0) {
        return {
            count: 0,
            latest_price: null,
            avg_30d: null,
            min_price: null,
            max_price: null,
            vs_avg_pct: null,
            is_anomaly: false,
            threshold_pct: Math.round(threshold * 1000) / 10,
        };
    }

    const latest = prices[prices.length - 1];
    const minP = Math.min.apply(null, prices);
    const maxP = Math.max.apply(null, prices);

    const cutoff = new Date(now.getTime() - windowDays * 86400000);
    const cutoffStr = [
        cutoff.getFullYear(),
        String(cutoff.getMonth() + 1).padStart(2, '0'),
        String(cutoff.getDate()).padStart(2, '0'),
    ].join('-');

    const windowPrices = [];
    list.forEach(p => {
        const d = String(p.date || '');
        if (d.length >= 10 && d[4] === '-' && d.slice(0, 10) >= cutoffStr) {
            const n = Number(p.unit_price);
            if (Number.isFinite(n) && n > 0) windowPrices.push(n);
        }
    });
    const avgBase = windowPrices.length ? windowPrices : prices;
    const avg30 = avgBase.reduce((a, b) => a + b, 0) / avgBase.length;

    let vsAvgPct = null;
    let isAnomaly = false;
    if (avg30 > 0) {
        vsAvgPct = Math.round(((latest - avg30) / avg30) * 1000) / 10;
        isAnomaly = vsAvgPct > threshold * 100;
    }

    return {
        count: prices.length,
        latest_price: Math.round(latest * 100) / 100,
        avg_30d: Math.round(avg30 * 100) / 100,
        min_price: Math.round(minP * 100) / 100,
        max_price: Math.round(maxP * 100) / 100,
        vs_avg_pct: vsAvgPct,
        is_anomaly: isAnomaly,
        threshold_pct: Math.round(threshold * 1000) / 10,
    };
}

/**
 * 生成纯 SVG 价格曲线（零依赖）。avgLine 为 30 日均价水平线。
 * 返回 SVG 字符串（调用方负责插入 DOM；数值均已 sanitize）。
 */
function buildPriceChartSvg(points, opts) {
    const width = (opts && opts.width) || 640;
    const height = (opts && opts.height) || 200;
    const padL = 48;
    const padR = 16;
    const padT = 16;
    const padB = 32;
    const avgLine = (opts && opts.avgLine != null) ? Number(opts.avgLine) : null;
    const threshold = (opts && opts.threshold != null) ? opts.threshold : PRICE_ANOMALY_THRESHOLD;

    const list = (Array.isArray(points) ? points : [])
        .map(p => ({
            date: String(p.date || ''),
            price: Number(p.unit_price),
        }))
        .filter(p => Number.isFinite(p.price) && p.price > 0);

    if (list.length === 0) return '';

    let minY = Math.min.apply(null, list.map(p => p.price));
    let maxY = Math.max.apply(null, list.map(p => p.price));
    if (avgLine != null && Number.isFinite(avgLine)) {
        minY = Math.min(minY, avgLine);
        maxY = Math.max(maxY, avgLine);
    }
    if (maxY - minY < 1e-9) {
        minY = Math.max(0, minY * 0.9);
        maxY = maxY * 1.1 || 1;
    }
    const yPad = (maxY - minY) * 0.12;
    minY = Math.max(0, minY - yPad);
    maxY = maxY + yPad;

    const plotW = width - padL - padR;
    const plotH = height - padT - padB;
    const n = list.length;

    function xAt(i) {
        if (n === 1) return padL + plotW / 2;
        return padL + (i / (n - 1)) * plotW;
    }
    function yAt(price) {
        return padT + (1 - (price - minY) / (maxY - minY)) * plotH;
    }

    const coords = list.map((p, i) => ({ x: xAt(i), y: yAt(p.price), ...p }));
    const linePts = coords.map(c => `${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(' ');
    const areaPts = [
        `${coords[0].x.toFixed(1)},${(padT + plotH).toFixed(1)}`,
        linePts,
        `${coords[n - 1].x.toFixed(1)},${(padT + plotH).toFixed(1)}`,
    ].join(' ');

    // Y 轴刻度（3 档）
    const yTicks = [minY, (minY + maxY) / 2, maxY];
    let yGrid = '';
    yTicks.forEach(v => {
        const y = yAt(v);
        yGrid += `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${width - padR}" y2="${y.toFixed(1)}" class="ph-grid"/>`;
        yGrid += `<text x="${padL - 8}" y="${(y + 4).toFixed(1)}" class="ph-axis-label" text-anchor="end">$${v.toFixed(1)}</text>`;
    });

    // X 轴标签：首 / 中 / 末
    const xIdx = n === 1 ? [0] : (n === 2 ? [0, 1] : [0, Math.floor((n - 1) / 2), n - 1]);
    let xLabels = '';
    xIdx.forEach(i => {
        const c = coords[i];
        const label = formatMonthDay(list[i].date);
        xLabels += `<text x="${c.x.toFixed(1)}" y="${(height - 10).toFixed(1)}" class="ph-axis-label" text-anchor="middle">${w2Escape(label)}</text>`;
    });

    let avgSvg = '';
    if (avgLine != null && Number.isFinite(avgLine) && avgLine > 0) {
        const y = yAt(avgLine);
        avgSvg = `
            <line x1="${padL}" y1="${y.toFixed(1)}" x2="${width - padR}" y2="${y.toFixed(1)}" class="ph-avg-line"/>
            <text x="${width - padR}" y="${(y - 6).toFixed(1)}" class="ph-avg-label" text-anchor="end">均 $${avgLine.toFixed(2)}</text>
        `;
    }

    let dots = '';
    coords.forEach((c, i) => {
        let anomaly = false;
        if (i > 0 && list[i - 1].price > 0) {
            const ratio = (c.price - list[i - 1].price) / list[i - 1].price;
            anomaly = ratio > threshold;
        }
        const cls = anomaly ? 'ph-dot-point ph-dot-up' : 'ph-dot-point';
        const title = `${list[i].date}: $${c.price.toFixed(2)}`;
        dots += `<circle cx="${c.x.toFixed(1)}" cy="${c.y.toFixed(1)}" r="${i === n - 1 ? 5 : 3.5}" class="${cls}"><title>${w2Escape(title)}</title></circle>`;
    });

    return `
<svg class="ph-svg" viewBox="0 0 ${width} ${height}" width="100%" height="${height}" preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="phAreaGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#032425" stop-opacity="0.18"/>
      <stop offset="100%" stop-color="#032425" stop-opacity="0.02"/>
    </linearGradient>
  </defs>
  ${yGrid}
  <polygon points="${areaPts}" fill="url(#phAreaGrad)" class="ph-area"/>
  <polyline points="${linePts}" class="ph-line" fill="none"/>
  ${avgSvg}
  ${dots}
  ${xLabels}
</svg>`.trim();
}

function formatHkd(price) {
    if (price == null || !Number.isFinite(Number(price))) return '—';
    return '$' + Number(price).toFixed(2);
}

// 完整日期规范化：任意来源（YYYY-MM-DD / YYYY-M-D / 含时间 / / 分隔）统一为 YYYY-MM-DD（供 hover 弹出）
function formatDateStandard(dateStr) {
    if (!dateStr) return '—';
    const s = String(dateStr).trim();
    const m = s.match(/(\d{4})[-/](\d{1,2})[-/](\d{1,2})/);
    if (!m) return s;
    return `${m[1]}-${String(m[2]).padStart(2, '0')}-${String(m[3]).padStart(2, '0')}`;
}

// 进价明细日期：默认只显示「月-日」，完整日期通过 hover (title) 弹出
function formatMonthDay(dateStr) {
    if (!dateStr) return '—';
    const s = String(dateStr).trim();
    const m = s.match(/(\d{4})-(\d{2})-(\d{2})/);
    if (!m) return s;
    return `${m[2]}-${m[3]}`;
}

// 全局日期单元格标准：默认短「MM-DD」，hover 弹出完整日期（统一入口，所有日期展示走这里）
function renderDateCell(dateStr) {
    if (dateStr == null || String(dateStr).trim() === '') return '—';
    const full = w2Escape(formatDateStandard(dateStr));
    const short = w2Escape(formatMonthDay(dateStr));
    return `<span class="ph-date-cell" data-full-date="${full}" onmouseenter="showDateTooltip(this)" onmouseleave="hideDateTooltip()">${short}</span>`;
}

// 自定义 tooltip：hover 日期单元格时立即弹出完整日期（渲染到 body，规避表格滚动容器裁剪）
let _dateTooltipEl = null;
function showDateTooltip(cell) {
    const text = cell && cell.dataset ? cell.dataset.fullDate : '';
    if (!text) return;
    if (!_dateTooltipEl) {
        _dateTooltipEl = document.createElement('div');
        _dateTooltipEl.className = 'cell-tooltip';
        _dateTooltipEl.setAttribute('role', 'tooltip');
        document.body.appendChild(_dateTooltipEl);
    }
    _dateTooltipEl.textContent = text;
    _dateTooltipEl.style.display = 'block';
    const rect = cell.getBoundingClientRect();
    const tw = _dateTooltipEl.offsetWidth;
    const th = _dateTooltipEl.offsetHeight;
    let left = rect.left + rect.width / 2 - tw / 2;
    let top = rect.top - th - 6;
    if (top < 8) top = rect.bottom + 6;  // 顶部空间不足则翻到下方
    left = Math.max(8, Math.min(left, window.innerWidth - tw - 8));
    _dateTooltipEl.style.left = left + 'px';
    _dateTooltipEl.style.top = top + 'px';
}
function hideDateTooltip() {
    if (_dateTooltipEl) _dateTooltipEl.style.display = 'none';
}

function formatPctSigned(pct) {
    if (pct == null || !Number.isFinite(Number(pct))) return '—';
    const n = Number(pct);
    const sign = n > 0 ? '+' : '';
    return sign + n.toFixed(1) + '%';
}

function setPriceHistoryViewState(state) {
    // state: 'loading' | 'empty' | 'content'
    const loading = document.getElementById('priceHistoryLoading');
    const empty = document.getElementById('priceHistoryEmpty');
    const content = document.getElementById('priceHistoryContent');
    if (loading) loading.classList.toggle('hide', state !== 'loading');
    if (empty) empty.classList.toggle('hide', state !== 'empty');
    if (content) content.classList.toggle('hide', state !== 'content');
}

function renderPriceHistoryModal(ret) {
    const skuName = ret.sku_name || '商品';
    const skuCode = ret.sku_code || '';
    const baseUnit = ret.base_unit || '';
    const points = Array.isArray(ret.data) ? ret.data : [];
    const summary = ret.summary && typeof ret.summary === 'object'
        ? ret.summary
        : computePriceHistorySummary(points);

    const titleEl = document.getElementById('priceHistoryTitle');
    const subEl = document.getElementById('priceHistorySubtitle');
    if (titleEl) titleEl.textContent = `${skuName} · 价格走势`;
    if (subEl) {
        const bits = [];
        if (baseUnit) bits.push(`单位 ${baseUnit}`);
        bits.push('进货单价历史');
        subEl.textContent = bits.join(' · ');
    }

    if (points.length === 0) {
        setPriceHistoryViewState('empty');
        return;
    }
    setPriceHistoryViewState('content');

    // 指标卡
    const metricsEl = document.getElementById('priceHistoryMetrics');
    if (metricsEl) {
        const vs = summary.vs_avg_pct;
        const anomaly = !!summary.is_anomaly;
        const vsClass = anomaly ? 'ph-metric-danger' : (vs != null && vs < 0 ? 'ph-metric-ok' : '');
        metricsEl.innerHTML = `
            <div class="ph-metric">
                <div class="ph-metric-label">最新进价</div>
                <div class="ph-metric-value">${w2Escape(formatHkd(summary.latest_price))}</div>
            </div>
            <div class="ph-metric">
                <div class="ph-metric-label">30 日均价</div>
                <div class="ph-metric-value">${w2Escape(formatHkd(summary.avg_30d))}</div>
            </div>
            <div class="ph-metric ${vsClass}">
                <div class="ph-metric-label">较均价${anomaly ? ' · 异动' : ''}</div>
                <div class="ph-metric-value">${w2Escape(formatPctSigned(vs))}</div>
            </div>
            <div class="ph-metric">
                <div class="ph-metric-label">区间低 / 高</div>
                <div class="ph-metric-value ph-metric-range">${w2Escape(formatHkd(summary.min_price))} <span class="ph-range-sep">–</span> ${w2Escape(formatHkd(summary.max_price))}</div>
            </div>
        `;
    }

    // 曲线
    const chartEl = document.getElementById('priceHistoryChart');
    if (chartEl) {
        const hasReceiptPoints = points.some(p => p.receipt_id || p.source === '进货单据' || p.source === '收据入库' || p.source === '已审核进货');
        const noticeHtml = !hasReceiptPoints ? `
            <div style="background-color:#eff6ff; border:1px solid #bfdbfe; color:#1e40af; padding:8px 12px; border-radius:6px; font-size:0.85rem; margin-bottom:10px;">
                <strong>提示：</strong>当前为系统初始基准单价，暂无已审核入库的实际进货记录。审批进货单后将自动形成动态价格走势。
            </div>
        ` : '';
        chartEl.innerHTML = noticeHtml + buildPriceChartSvg(points, {
            avgLine: summary.avg_30d,
            threshold: PRICE_ANOMALY_THRESHOLD,
        });
    }

    // 明细表（新在上）；先升序算「较上笔」；供应商 + 单据钻取（P0）
    const tbody = document.getElementById('priceHistoryTableBody');
    const countEl = document.getElementById('priceHistoryCount');
    if (countEl) countEl.textContent = `${points.length} 笔`;
    if (tbody) {
        tbody.innerHTML = '';
        const deltaByIdx = {};
        for (let i = 0; i < points.length; i++) {
            const price = Number(points[i].unit_price);
            const prev = i > 0 ? Number(points[i - 1].unit_price) : NaN;
            if (i === 0 || !Number.isFinite(price) || !Number.isFinite(prev) || prev <= 0) {
                deltaByIdx[i] = null;
            } else {
                deltaByIdx[i] = Math.round(((price - prev) / prev) * 1000) / 10;
            }
        }
        for (let revI = 0; revI < points.length; revI++) {
            const origIdx = points.length - 1 - revI;
            const item = points[origIdx];
            const price = Number(item.unit_price);
            const qty = item.qty != null && Number.isFinite(Number(item.qty)) ? Number(item.qty) : null;
            const delta = deltaByIdx[origIdx];
            let deltaCell = '<span class="ph-delta-na">—</span>';
            if (delta != null) {
                const up = delta > 0;
                const big = delta > PRICE_ANOMALY_THRESHOLD * 100;
                const cls = big ? 'ph-delta-up ph-delta-hot' : (up ? 'ph-delta-up' : (delta < 0 ? 'ph-delta-down' : 'ph-delta-flat'));
                deltaCell = `<span class="${cls}">${w2Escape(formatPctSigned(delta))}</span>`;
            }
            const supName = (item.supplier_name && String(item.supplier_name).trim())
                ? String(item.supplier_name).trim()
                : '—';
            const rid = item.receipt_id != null ? Number(item.receipt_id) : null;
            let receiptCell = '<span class="ph-delta-na">—</span>';
            if (rid && Number.isFinite(rid) && rid > 0) {
                receiptCell = `<button type="button" class="btn btn-secondary ph-receipt-btn" `
                    + `style="padding:2px 8px; font-size:0.75rem;" `
                    + `onclick="openReceiptFromPriceHistory(${rid})">查看</button>`;
            }
            const tr = document.createElement('tr');
            if (delta != null && delta > PRICE_ANOMALY_THRESHOLD * 100) tr.classList.add('ph-row-hot');
            if (rid && Number.isFinite(rid) && rid > 0) {
                tr.classList.add('ph-row-clickable');
                tr.title = '双击打开原单';
                tr.ondblclick = () => openReceiptFromPriceHistory(rid);
            }
            tr.innerHTML = `
                <td>${renderDateCell(item.date)}</td>
                <td class="ph-supplier-cell" title="${w2Escape(supName)}">${w2Escape(supName)}</td>
                <td class="col-right"><strong>${w2Escape(Number.isFinite(price) ? price.toFixed(2) : '—')}</strong></td>
                <td class="col-right">${qty != null ? w2Escape(String(qty)) : '—'}</td>
                <td><span class="badge badge-secondary">${w2Escape(item.source || '—')}</span></td>
                <td class="col-right">${deltaCell}</td>
                <td class="col-center">${receiptCell}</td>
            `;
            tbody.appendChild(tr);
        }
    }
}

/** P0：从价格走势钻取原单 —— 关闭走势 Modal，打开归档详情 */
function openReceiptFromPriceHistory(receiptId) {
    const rid = Number(receiptId);
    if (!Number.isFinite(rid) || rid <= 0) {
        showToast('无效的单据 ID', 'error');
        return;
    }
    closeModalById('priceHistoryModal');
    if (typeof loadReceiptDetail === 'function') {
        loadReceiptDetail(rid);
    } else {
        showToast('无法打开单据详情', 'error');
    }
}

function viewPriceHistory(skuId) {
    const id = Number(skuId);
    if (!Number.isFinite(id) || id <= 0) {
        showToast('无效的 SKU', 'error');
        return;
    }
    const modal = document.getElementById('priceHistoryModal');
    if (!modal) {
        showToast('价格走势组件未就绪', 'error');
        return;
    }
    const titleEl = document.getElementById('priceHistoryTitle');
    if (titleEl) titleEl.textContent = '价格走势';
    const subEl = document.getElementById('priceHistorySubtitle');
    if (subEl) subEl.textContent = '加载中…';
    setPriceHistoryViewState('loading');
    modal.classList.remove('hide');

    fetch(`/api/price_history/${id}`)
        .then(res => res.json().then(body => ({ ok: res.ok, body })))
        .then(({ ok, body }) => {
            if (!ok || !body || body.status !== 'success') {
                setPriceHistoryViewState('empty');
                const empty = document.getElementById('priceHistoryEmpty');
                if (empty) empty.textContent = (body && body.msg) || '加载价格走势失败';
                showToast((body && body.msg) || '加载价格走势失败', 'error');
                return;
            }
            renderPriceHistoryModal(body);
        })
        .catch(err => {
            setPriceHistoryViewState('empty');
            const empty = document.getElementById('priceHistoryEmpty');
            if (empty) empty.textContent = '加载价格走势失败';
            console.error('加载价格走势失败', err);
            showToast('加载价格走势失败' + toastFailDetail(err), 'error');
        });
}

// =========================================================
// 抽屉式本批照片 Sider 状态机
// 单张布局保持原样不动；>=2 张才进入 BatchUploader
// 角色权限：userRole='admin' 可解析/删除；'chef' 仅查看
// =========================================================
const BatchUploader = {
    photos: [],
    activeIndex: 0,
    timerInterval: null,
    siderOpen: false,
    selected: new Set(),
    userRole: 'admin',  // 验证性项目：硬编码 admin，后续可扩展为多角色
};

function getActivePhoto() {
    return BatchUploader.photos[BatchUploader.activeIndex] || null;
}

const MANIFEST_KEY = 'finsaro_batch_manifest_v1';

function mapServerStatus(serverStatus) {
    const s = (serverStatus || '').toLowerCase().trim();
    if (s === 'parsing' || s === 'uploaded') {
        return 'uploading';
    }
    if (s === 'parsed') {
        return 'parsed';
    }
    if (s === 'edited' || s === 'approved' || s === 'saved' || s === 'flagged') {
        return 'saved';
    }
    if (s === 'error') {
        return 'error';
    }
    return 'parsed';
}

function persistManifest() {
    try {
        if (typeof BatchUploader === 'undefined' || !Array.isArray(BatchUploader.photos)) return;
        const validItems = [];
        BatchUploader.photos.forEach(photo => {
            if (photo && photo.receiptId) {
                validItems.push({
                    localId: photo.localId,
                    fileName: photo.fileName || (photo.file ? photo.file.name : null) || `receipt_${photo.receiptId}.jpg`,
                    receiptId: photo.receiptId,
                    jobId: photo.jobId || null,
                    status: photo.status || 'parsed'
                });
            }
        });
        if (validItems.length === 0) {
            clearManifest();
            return;
        }
        const activePhoto = getActivePhoto();
        const manifestData = {
            v: 1,
            lastActiveLocalId: activePhoto ? activePhoto.localId : null,
            items: validItems
        };
        localStorage.setItem(MANIFEST_KEY, JSON.stringify(manifestData));
    } catch (e) {
        console.warn('persistManifest 写入异常:', e);
    }
}

function loadManifest() {
    try {
        const str = localStorage.getItem(MANIFEST_KEY);
        if (!str) return null;
        const parsed = JSON.parse(str);
        if (parsed && parsed.v === 1 && Array.isArray(parsed.items)) {
            return parsed;
        }
        return null;
    } catch (e) {
        return null;
    }
}

function clearManifest() {
    try {
        localStorage.removeItem(MANIFEST_KEY);
    } catch (e) {}
}

async function restoreBatchFromManifest() {
    const manifest = loadManifest();
    if (!manifest || !manifest.items || manifest.items.length === 0) return;

    const items = manifest.items.filter(item => item && item.receiptId);
    if (items.length === 0) {
        clearManifest();
        return;
    }

    const restoredPhotos = [];
    let isUnauthorized = false;

    for (const item of items) {
        try {
            const res = await fetch(`/api/receipt/${item.receiptId}`);
            if (res.status === 401) {
                isUnauthorized = true;
                break;
            }
            if (!res.ok) continue; // 404 等忽略
            const ret = await res.json();
            if (ret.status === 'success' && ret.data) {
                const detail = ret.data;
                const serverStatus = mapServerStatus(detail.status);
                const photo = {
                    localId: item.localId || `local-restored-${item.receiptId}`,
                    fileName: item.fileName || (detail.supplier_name ? `${detail.supplier_name}.jpg` : `单据 #${item.receiptId}.jpg`),
                    file: null,
                    originalFile: null,
                    objectUrl: null,
                    croppedObjectUrl: null,
                    cropped: false,
                    status: serverStatus,
                    receiptId: ret.receipt_id || item.receiptId,
                    jobId: item.jobId || null,
                    imageUrl: ret.image_url || null,
                    data: detail,
                    errorMsg: detail.status === 'error' ? (detail.ai_prefill?.ocr_error || '识别失败') : null,
                    pollToken: { cancelled: false }
                };
                restoredPhotos.push(photo);
            }
        } catch (e) {
            console.warn(`恢复单据 #${item.receiptId} 异常:`, e);
        }
    }

    if (isUnauthorized) {
        clearManifest();
        return;
    }

    if (restoredPhotos.length === 0) {
        clearManifest();
        return;
    }

    BatchUploader.photos = restoredPhotos;

    let activeIdx = 0;
    if (manifest.lastActiveLocalId) {
        const found = restoredPhotos.findIndex(p => p.localId === manifest.lastActiveLocalId);
        if (found >= 0) activeIdx = found;
    }
    BatchUploader.activeIndex = activeIdx;

    const splitView = document.getElementById('splitViewArea');
    if (splitView) splitView.classList.remove('hide');
    const toggleCount = document.getElementById('siderToggleCount');
    if (toggleCount) toggleCount.innerText = BatchUploader.photos.length;
    updateSiderVisibility();
    renderSider();
    setActivePhoto(activeIdx);
    updateSelectionUI();

    let unfinishedCount = 0;
    let hasUploading = false;
    restoredPhotos.forEach((photo) => {
        if (photo.status === 'uploading') {
            unfinishedCount++;
            hasUploading = true;
            if (photo.jobId) {
                pollReceiptJob(photo.jobId, settled => {
                    const curIdx = findPhotoIndex(photo);
                    if (settled.status === 'success' || settled.status === 'parsed') {
                        photo.status = 'parsed';
                        photo.data = settled.data;
                        if (settled.image_url) photo.imageUrl = settled.image_url;
                        renderSider();
                        if (curIdx >= 0) autoSaveParsedPhoto(curIdx);
                        if (curIdx >= 0 && BatchUploader.activeIndex === curIdx) {
                            applyPhotoToMainArea(photo);
                        }
                    } else if (settled.status === 'error') {
                        photo.status = 'error';
                        photo.errorMsg = settled.msg || '识别失败';
                        renderSider();
                        if (curIdx >= 0 && BatchUploader.activeIndex === curIdx) {
                            showErrorCard(photo.errorMsg, photo.receiptId);
                        }
                    }
                    if (!BatchUploader.photos.some(p => p.status === 'uploading')) {
                        stopBatchTimer();
                        hideLoadingCard();
                    }
                }, {
                    token: photo.pollToken,
                    onProgress: (job) => applyServerPreviewIfAny(photo, job.image_url)
                });
            } else {
                photo.status = 'error';
                photo.errorMsg = '解析打断，请点击重试';
            }
        } else if (photo.status === 'error') {
            unfinishedCount++;
        }
    });

    if (hasUploading) {
        showLoadingCard();
        startBatchTimer();
    }

    if (unfinishedCount > 0) {
        showToast(`已恢复 ${unfinishedCount} 张未完成单据解析状态`, 'info');
    }
}

function isAdmin() { return BatchUploader.userRole === 'admin'; }

// 已解析 / 已保存的照片不可再被选中（全选也排除）
function isPhotoSelectable(p) {
    return p && p.status !== 'parsed' && p.status !== 'saved';
}

// 将已解析/已保存的照片从选择集中剔除，保持选择集始终是“可选”项
function pruneUnselectableFromSelection() {
    for (const idx of Array.from(BatchUploader.selected)) {
        const p = BatchUploader.photos[idx];
        if (!isPhotoSelectable(p)) BatchUploader.selected.delete(idx);
    }
}

// 抽屉 sider 显示/隐藏：弹出时隐藏入口按钮，收起时恢复（空批次本就隐藏）
// 仅当本批照片 >= 2 张时，才展示“本批照片”抽屉与入口按钮；否则一律收起
function updateSiderVisibility() {
    const show = BatchUploader.photos.length >= 2;
    const btn = document.getElementById('btnSiderToggle');
    const drawer = document.getElementById('photoSider');
    if (!show) {
        BatchUploader.siderOpen = false;
        if (btn) btn.classList.add('hide');
        if (drawer) drawer.classList.remove('open');
        return;
    }
    if (btn) btn.classList.toggle('hide', BatchUploader.siderOpen);
    if (drawer) drawer.classList.toggle('open', BatchUploader.siderOpen);
}

function togglePhotoSider() {
    BatchUploader.siderOpen = !BatchUploader.siderOpen;
    updateSiderVisibility();
}

// 判断点击是否落在“功能性”元素上（按钮/图片/链接/输入等）——这些不算空白区域
function isInteractiveTarget(target) {
    if (!target || !target.closest) return false;
    return !!target.closest(
        'button, a, input, select, textarea, label, img, canvas, svg, video, audio, [onclick], [role="button"], [contenteditable="true"]'
    );
}

// 拖拽进行中标记：文件/照片拖拽时（即便鼠标移出画框）也不收起 sider
// 1) 原生 HTML5 拖拽（如从系统拖入文件）：用 dragover/dragenter 标记
// 2) 鼠标按住拖动（mousedown + 移动 >5px）：用指针位移标记，覆盖未触发 HTML5 事件的拖拽
let _isDraggingPhoto = false;
let _dragEndAt = 0;          // 原生拖拽刚结束（含移出画框松开）的防抖时间戳
let _pointerDown = false;
let _pointerMoved = false;   // 按住后发生位移（视为拖拽）
let _pointerStart = null;
let _pointerDragEndAt = 0;   // 鼠标拖拽刚松开的防抖时间戳

function _markDragStart() { _isDraggingPhoto = true; }
document.addEventListener('dragover', _markDragStart);
document.addEventListener('dragenter', _markDragStart);
document.addEventListener('drop', () => { _isDraggingPhoto = false; _dragEndAt = Date.now(); });
document.addEventListener('dragend', () => { _isDraggingPhoto = false; _dragEndAt = Date.now(); });

document.addEventListener('mousedown', (e) => {
    _pointerDown = true;
    _pointerMoved = false;
    _pointerStart = { x: e.clientX, y: e.clientY };
});
document.addEventListener('mousemove', (e) => {
    if (_pointerDown && _pointerStart) {
        const dx = e.clientX - _pointerStart.x;
        const dy = e.clientY - _pointerStart.y;
        if (dx * dx + dy * dy > 25) _pointerMoved = true;  // 位移 >5px 视为拖拽
    }
});
document.addEventListener('mouseup', () => {
    if (_pointerDown && _pointerMoved) _pointerDragEndAt = Date.now();
    _pointerDown = false;
    _pointerStart = null;
});

// 点击本批照片抽屉之外“空白区域”才收起；点中按钮/图片等功能性元素不收起
// 用捕获阶段（capture）触发，早于 sider 内点击的 onclick 重渲染，
// 此时 e.target 仍挂在 DOM 中，drawer.contains 判定才准确；
// 否则重渲染会把被点节点摘离文档，误判为“点在外面”而错误收起。
document.addEventListener('click', function (e) {
    if (!BatchUploader.siderOpen) return;
    if (_isDraggingPhoto) return;   // 原生拖拽中（含移出画框）不收起
    if (_pointerDown || _pointerMoved) return;   // 鼠标按住拖拽中（含移出画框）不收起
    if (Date.now() - _dragEndAt < 200) return;   // 原生拖拽刚松开（移出画框释放）不收起
    if (Date.now() - _pointerDragEndAt < 200) return; // 鼠标拖拽刚松开不收起
    const drawer = document.getElementById('photoSider');
    const toggleBtn = document.getElementById('btnSiderToggle');
    if (drawer && drawer.contains(e.target)) return;
    if (toggleBtn && toggleBtn.contains(e.target)) return;
    // 照片展示画框内（含照片未填满画框时的留白区域）一律不收起
    const frame = document.getElementById('imgViewerContainer');
    if (frame && frame.contains(e.target)) return;
    // 点中功能性元素（按钮/图片/链接/输入等）不收起，仅空白区域收起
    if (isInteractiveTarget(e.target)) return;
    BatchUploader.siderOpen = false;
    updateSiderVisibility();
}, true);

// 多文件入口（>=2 触发批量；P1-15：本批已存在时单张也路由进批次）
async function handleFilesSelect(fileList, gen) {
    if (!fileList || fileList.length === 0) return;

    const currentGen = (typeof gen === 'number') ? gen : nextFileSelectionGen();

    const hasLargeOrHeic = fileList.some(f => isNonWebImageFile(f) || (f && f.size > 1.5 * 1024 * 1024));
    if (hasLargeOrHeic) {
        showImagePrepIndicator(true, '正在准备照片，请稍候...');
    }

    try {
        // 若包含 HEIC / TIFF 等非 web 格式，批量即时自动转码为 JPEG
        if (fileList.some(f => isNonWebImageFile(f))) {
            const convertedList = await Promise.all(fileList.map(f => ensureWebDisplayableImageFile(f, currentGen)));
            if (currentGen !== fileSelectionGen) return;
            fileList = convertedList.filter(Boolean);
            if (fileList.length === 0) return;
        }

        if (fileList.length === 1 && (BatchUploader.photos.length === 0 || (BatchUploader.photos.length === 1 && BatchUploader.photos[0].status !== 'uploading'))) {
            // 纯单张且无在途多图批次 → 走原 handleFileSelect（内部已含质量预检与预览替换）
            if (typeof handleFileSelect === 'function') await handleFileSelect(fileList[0], currentGen);
            return;
        }
        // P1-15: 批量存在时单张文件选择路由进批次（handleFilesSelect([file]) 语义）——
        // 原单张直传路径的 settle 会把结果误记到当前激活的旧照片（混合污染），
        // 并进批次后由照片对象引用承载各自结果，互不串扰。

        // D21 质量预检：批量模式对每张检查、汇总提示（仅警告，确认后可继续）
        const checks = await Promise.all(fileList.map(f => checkImageQuality(f, currentGen)));
        if (currentGen !== fileSelectionGen) return;

        const badList = [];
        const badIndices = new Set();
        checks.forEach((c, i) => {
            if (c && !c.ok) {
                badList.push({ file: fileList[i], reasons: c.reasons });
                badIndices.add(i);
            }
        });
        let batchForced = false;
        if (badList.length > 0) {
            const detail = badList.map(b => `• ${b.file.name}：${b.reasons.join('、')}`).join('\n');
            const proceed = confirm(`质量预检：${fileList.length} 张照片中有 ${badList.length} 张可能存在问题：\n${detail}\n\n可能影响识别质量，仍要全部上传吗？`);
            if (currentGen !== fileSelectionGen) return;
            if (!proceed) {
                const fileInput = document.getElementById('receiptFile');
                if (fileInput) fileInput.value = '';
                return;
            }
            batchForced = true;
        }

        if (currentGen !== fileSelectionGen) return;

        const newPhotos = fileList.map((file, i) => ({
            localId: `local-${Date.now()}-${i}-${Math.random().toString(36).slice(2, 7)}`,
            file,
            originalFile: file,
            force: batchForced || badIndices.has(i),
            objectUrl: isNonWebImageFile(file) ? null : URL.createObjectURL(file),
            croppedObjectUrl: null,
            cropped: false,
            status: 'pending',
            receiptId: null,
            imageUrl: null,
            data: null,
            errorMsg: null,
        }));

        const startIndex = BatchUploader.photos.length;
        BatchUploader.photos = BatchUploader.photos.concat(newPhotos);

        document.getElementById('splitViewArea').classList.remove('hide');
        document.getElementById('siderToggleCount').innerText = BatchUploader.photos.length;
        // 统一由 updateSiderVisibility 决定入口按钮是否展示（仅本批 >= 2 张时）
        updateSiderVisibility();

        renderSider();

        // 默认激活第一张新加入的照片
        if (BatchUploader.activeIndex < 0 || BatchUploader.activeIndex >= BatchUploader.photos.length - newPhotos.length) {
            setActivePhoto(startIndex);
        }
        updateSelectionUI();

        // 自动展开 sider 让用户看到 batch（仅当本批 >= 2 张时才弹出）
        if (BatchUploader.photos.length >= 2 && !BatchUploader.siderOpen) togglePhotoSider();

        const autoAnalyze = document.getElementById('chkAutoAnalyze')?.checked;
        if (autoAnalyze) {
            triggerBatchAnalysis(false);
        }
    } finally {
        if (currentGen === fileSelectionGen) {
            showImagePrepIndicator(false);
        }
    }
}

function uploadSinglePhoto(idx) {
    const photo = BatchUploader.photos[idx];
    if (!photo) return;
    photo.status = 'uploading';
    photo.pollToken = { cancelled: false };   // Q28: 删照片时作废轮询
    renderSider();
    updateSelectionUI();
    setActivePhoto(idx);

    const formData = new FormData();
    formData.append('receipt', photo.file);
    formData.append('force', photo.force ? 'true' : 'false');
    const useCodebuddy = document.getElementById('chkCodebuddy').checked;

    showLoadingCard();
    startBatchTimer();

    // Wave 3（T9）：async=true 立即返回 job_id，再轮询 /api/job/{id}
    fetch(`/api/upload?codebuddy=${useCodebuddy}&async=true&force=${photo.force ? 'true' : 'false'}`, { method: 'POST', body: formData })
        .then(res => res.json())
        .then(ret => {
            if (ret.status === 'queued') {
                if (ret.receipt_id) photo.receiptId = ret.receipt_id;
                // HEIC 等已在服务端转 JPEG：立刻切换预览，避免黑屏/收据原图占位
                applyServerPreviewIfAny(photo, ret.image_url);
                offerDuplicateAction(ret);
                pollReceiptJob(ret.job_id, settle, {
                    token: photo.pollToken,
                    onProgress: (job) => applyServerPreviewIfAny(photo, job.image_url),
                });
                return;
            }
            settle(ret);
        })
        .catch(err => {
            console.error('批量识别请求异常', err);
            settle({ status: 'error', msg: '识别失败，请稍后重试' });
        });

    // P1-14: settle 闭包捕获 photo 对象引用（localId 校验仍在列表中），
    // 解析中删照片导致的索引漂移不再把结果写错对象；照片已移除则丢弃结果
    function settle(ret) {
        if (!isPhotoInBatch(photo) || ret.status === 'cancelled') {
            if (!BatchUploader.photos.some(p => p.status === 'uploading')) {
                stopBatchTimer();
                hideLoadingCard();
            }
            return;
        }
        stopBatchTimer();
        hideLoadingCard();
        const curIdx = findPhotoIndex(photo);
        if (ret.status !== 'success') {
            photo.status = 'error';
            photo.errorMsg = ret.msg || '识别失败';
            // 后端补丁可能在失败响应中携带单据 id（重试复用原单据，D13）
            if (ret.receipt_id) photo.receiptId = ret.receipt_id;
            if (ret.image_url && isBrowserDisplayableImageUrl(ret.image_url)) {
                photo.imageUrl = ret.image_url;
            }
            renderSider();
            // D28/D33: 错误卡片双出口；有 JPEG 则显示，否则保持 HEIC 常驻提示
            if (BatchUploader.activeIndex === curIdx) {
                setMainPreview(photo);
                showErrorCard(photo.errorMsg, photo.receiptId || null);
            } else {
                showToast(`第 ${curIdx + 1} 张识别失败：${photo.errorMsg}`, 'error');
            }
            return;
        }
        photo.status = 'parsed';
        photo.receiptId = ret.receipt_id;
        if (ret.image_url && isBrowserDisplayableImageUrl(ret.image_url)) {
            photo.imageUrl = ret.image_url;
        }
        photo.data = ret.data;
        captureResultVersion(ret);   // P1-13: version 回写 photo.data
        if (Array.isArray(ret.quality_warnings)) photo.qualityWarnings = ret.quality_warnings;
        renderSider();
        // 解析完成即自动写库（无“全部保存”入口）
        autoSaveParsedPhoto(curIdx);
        if (BatchUploader.activeIndex === curIdx) applyPhotoToMainArea(photo);
    }
}

function uploadBatch(batchPhotos) {
    // Wave 3（T9）：批量解析改为逐张 async=true Job + 轮询——后端管线进程内锁串行执行，
    // 前端每完成一张即渐进提示（D32 逻辑不变），无需等待整批同步返回。
    // P1-14: 入参为 photo 对象数组；settle 闭包捕获 photo 对象引用并以 localId
    // 校验是否仍在列表中——解析中删照片不再因索引漂移把结果写错对象，
    // 照片已移除则直接丢弃结果。
    const useCodebuddy = document.getElementById('chkCodebuddy').checked;

    const targets = (batchPhotos || []).filter(p => p && isPhotoInBatch(p));
    targets.forEach(photo => {
        photo.status = 'uploading';
        photo.pollToken = { cancelled: false };   // Q28: 删照片时作废轮询
    });
    renderSider();
    updateSelectionUI();
    if (targets.length === 0) return;
    showLoadingCard();
    startBatchTimer();

    let pending = targets.length;

    targets.forEach(photo => {
        const formData = new FormData();
        formData.append('receipt', photo.file);
        formData.append('force', photo.force ? 'true' : 'false');

        fetch(`/api/upload?codebuddy=${useCodebuddy}&async=true&force=${photo.force ? 'true' : 'false'}`, { method: 'POST', body: formData })
            .then(res => res.json())
            .then(ret => {
                if (ret.status === 'queued') {
                    if (ret.receipt_id) photo.receiptId = ret.receipt_id;
                    applyServerPreviewIfAny(photo, ret.image_url);
                    offerDuplicateAction(ret);
                    pollReceiptJob(ret.job_id, settled => settle(photo, settled), {
                        token: photo.pollToken,
                        onProgress: (job) => applyServerPreviewIfAny(photo, job.image_url),
                    });
                    return;
                }
                settle(photo, ret);
            })
            .catch(err => {
                console.error('批量上传识别异常', err);
                settle(photo, { status: 'error', msg: '识别失败，请稍后重试' });
            });
    });

    function settle(photo, ret) {
        pending -= 1;
        // P1-14: 照片已被移出批次（或轮询已作废）→ 丢弃结果
        if (!isPhotoInBatch(photo) || ret.status === 'cancelled') {
            finishBatchIfDone();
            return;
        }
        const idx = findPhotoIndex(photo);
        if (ret.status === 'success' || ret.status === 'parsed') {
            photo.status = 'parsed';
            photo.receiptId = ret.receipt_id;
            if (ret.image_url && isBrowserDisplayableImageUrl(ret.image_url)) {
                photo.imageUrl = ret.image_url;
            }
            photo.data = ret.data;
            photo.errorMsg = null;
            captureResultVersion(ret);   // P1-13: version 回写 photo.data
            if (Array.isArray(ret.quality_warnings)) photo.qualityWarnings = ret.quality_warnings;
            renderSider();
            // 解析完成即自动写库（无“全部保存”入口）
            autoSaveParsedPhoto(idx);
            // D32 批量渐进提示：状态变为 parsed 且非当前激活照片 → sider 高亮 + 顶部 toast，不打断当前操作
            if (idx !== BatchUploader.activeIndex) {
                showToast(`第 ${idx + 1} 张已识别完成，可切换复核`, 'success');
                flashSiderItem(idx);
            } else {
                applyPhotoToMainArea(photo);
            }
        } else {
            photo.status = 'error';
            photo.errorMsg = ret.msg || '识别失败';
            // 批量失败响应携带单据 id：重试时复用原单据（D13）
            if (ret.receipt_id) photo.receiptId = ret.receipt_id;
            if (ret.image_url && isBrowserDisplayableImageUrl(ret.image_url)) {
                photo.imageUrl = ret.image_url;
            }
            // P0-1 极模糊批量路径：留存 quality_warnings 供切换照片时置顶
            if (Array.isArray(ret.quality_warnings)) photo.qualityWarnings = ret.quality_warnings;
            else if (ret && ret.code === 'IMAGE_QUALITY_ERROR') photo.qualityWarnings = ['image_blur'];
            renderSider();
            if (BatchUploader.activeIndex === idx) {
                setMainPreview(photo);
                showErrorCard(photo.errorMsg, photo.receiptId || null);
                const qw = collectQualityWarnings(ret) || photo.qualityWarnings;
                if (qw && qw.length) showQualityWarnings(qw);
                else if (ret && ret.code === 'IMAGE_QUALITY_ERROR') showQualityWarnings(['image_blur']);
            } else {
                showToast(`第 ${idx + 1} 张识别失败：${photo.errorMsg}`, 'error');
            }
        }
        finishBatchIfDone();
    }

    function finishBatchIfDone() {
        if (pending > 0) return;
        // 仍有其他路径（如单张重试）在途时不收起 loading
        if (BatchUploader.photos.some(p => p.status === 'uploading')) return;
        stopBatchTimer();
        hideLoadingCard();
        const firstParsed = BatchUploader.photos.findIndex(p => p.status === 'parsed');
        if (firstParsed >= 0) setActivePhoto(firstParsed);
    }
}

// sider 顶部「 解析选中」按钮
// onlySelected=true → 仅解析 selected 中 pending/error 的；false → 解析所有 pending/error（兜底）
function triggerBatchAnalysis(onlySelected = false) {
    if (!isAdmin()) {
        showToast('当前角色无解析权限，请联系管理员', 'warning');
        return;
    }
    let targets;
    if (onlySelected) {
        targets = Array.from(BatchUploader.selected)
            .filter(idx => idx >= 0 && idx < BatchUploader.photos.length)
            .map(idx => ({ p: BatchUploader.photos[idx], idx }))
            .filter(({ p }) => p.status === 'pending' || p.status === 'error');
    } else {
        targets = BatchUploader.photos
            .map((p, idx) => ({ p, idx }))
            .filter(({ p }) => p.status === 'pending' || p.status === 'error');
    }

    if (targets.length === 0) {
        showToast(onlySelected ? '所选照片没有待解析项' : '没有待解析的照片', 'warning');
        return;
    }

    targets.forEach(({ p }) => { p.status = 'uploading'; p.errorMsg = null; p.force = true; });
    renderSider();
    updateSelectionUI();

    if (targets.length === 1) {
        setActivePhoto(targets[0].idx);
        uploadSinglePhoto(targets[0].idx);
    } else {
        // P1-14: 传 photo 对象引用（目标可能非连续索引，不能按 startIdx+count 取片）
        uploadBatch(targets.map(t => t.p));
    }
}

function updateSelectionUI() {
    pruneUnselectableFromSelection();
    const sel = BatchUploader.selected;
    const count = sel.size;
    const total = BatchUploader.photos.length;
    const selectableTotal = BatchUploader.photos.filter(isPhotoSelectable).length;

    // 入口按钮徽章
    const toggleCount = document.getElementById('siderToggleCount');
    if (toggleCount) toggleCount.innerText = total;

    // 解析/删除按钮状态（admin 才能操作）
    const btnAnalyze = document.getElementById('btnAnalyzeSelected');
    const btnRemove = document.getElementById('btnRemoveSelected');
    const siderAnalyzeCount = document.getElementById('siderSelectedCount');
    const siderRemoveCount = document.getElementById('siderRemoveBadge');

    const admin = isAdmin();
    if (btnAnalyze) {
        btnAnalyze.disabled = !admin || count === 0;
        btnAnalyze.style.display = admin ? '' : 'none';
        if (siderAnalyzeCount) siderAnalyzeCount.innerText = count;
    }
    if (btnRemove) {
        btnRemove.disabled = !admin || count === 0;
        btnRemove.style.display = admin ? '' : 'none';
        if (siderRemoveCount) siderRemoveCount.innerText = count;
    }

    // 全选 checkbox 状态：基于“可选中”的总数；已解析/已保存项不计入
    const selectAll = document.getElementById('siderSelectAll');
    if (selectAll) {
        selectAll.checked = selectableTotal > 0 && count === selectableTotal;
        selectAll.indeterminate = count > 0 && count < selectableTotal;
        selectAll.disabled = selectableTotal === 0;
        selectAll.parentElement.style.display = admin ? '' : 'none';
    }

}

// 全选 checkbox 回调（已解析/已保存项不参与全选）
function onSiderSelectAll(checked) {
    BatchUploader.selected.clear();
    if (checked) {
        BatchUploader.photos.forEach((p, idx) => {
            if (isPhotoSelectable(p)) BatchUploader.selected.add(idx);
        });
    }
    renderSider();
}

// 切换单个 photo 的选中
function togglePhotoSelection(idx, checked) {
    const p = BatchUploader.photos[idx];
    if (!isPhotoSelectable(p)) return;   // 已解析/已保存项不可选中
    if (checked) BatchUploader.selected.add(idx);
    else BatchUploader.selected.delete(idx);
    // 不需要重渲染整个列表，只更新 UI 计数 + 单项 .selected class + 前导勾选框
    const item = document.querySelector(`#photoSiderList li[data-idx="${idx}"]`);
    if (item) {
        item.classList.toggle('selected', checked);
        const cb = item.querySelector('.check input[type="checkbox"]');
        if (cb) cb.checked = checked;
    }
    updateSelectionUI();
}

// 批量删除选中照片
function removeSelectedPhotos() {
    if (!isAdmin()) { showToast('当前角色无删除权限', 'warning'); return; }
    const ids = Array.from(BatchUploader.selected).sort((a, b) => b - a); // 从大到小删除
    if (ids.length === 0) return;
    const ok = confirm(`确定从本批中移除选中的 ${ids.length} 张照片吗？`);
    if (!ok) return;
    ids.forEach(idx => removePhotoAtIndex(idx));
    BatchUploader.selected.clear();
    renderSider();
    updateSelectionUI();
}

// 内部：按 index 删除单个（与 removePhotoFromSider 类似但跳过 confirm）
function removePhotoAtIndex(idx) {
    if (idx < 0 || idx >= BatchUploader.photos.length) return;
    const photo = BatchUploader.photos[idx];
    if (photo.pollToken) photo.pollToken.cancelled = true;   // Q28: 作废在途轮询（P1-14 丢弃结果）
    try { URL.revokeObjectURL(photo.objectUrl); } catch(e) {}
    BatchUploader.photos.splice(idx, 1);
    if (BatchUploader.photos.length === 0) {
        BatchUploader.activeIndex = 0;
        document.getElementById('splitViewArea').classList.add('hide');
        const btn = document.getElementById('btnSiderToggle');
        if (btn) btn.classList.add('hide');
        const drawer = document.getElementById('photoSider');
        if (drawer) drawer.classList.remove('open');
        BatchUploader.siderOpen = false;
        clearManifest();
        return;
    }
    if (idx === BatchUploader.activeIndex) {
        BatchUploader.activeIndex = Math.min(idx, BatchUploader.photos.length - 1);
        setActivePhoto(BatchUploader.activeIndex);
    } else if (idx < BatchUploader.activeIndex) {
        BatchUploader.activeIndex -= 1;
    }
    // 修正 selected 中的索引
    const newSel = new Set();
    BatchUploader.selected.forEach(s => {
        if (s === idx) return;
        newSel.add(s > idx ? s - 1 : s);
    });
    BatchUploader.selected = newSel;
}

function showLoadingCard() {
    document.getElementById('preConfirmCard').classList.add('hide');
    document.getElementById('errorCard').classList.add('hide');
    document.getElementById('loadingCard').classList.remove('hide');
    document.getElementById('prefillFormCard').classList.add('hide');
}
function hideLoadingCard() {
    document.getElementById('loadingCard').classList.add('hide');
}

// D32: 批量识别完成项的 sider 高亮闪烁（数秒后自动移除）
function flashSiderItem(idx) {
    const li = document.querySelector(`#photoSiderList li[data-idx="${idx}"]`);
    if (!li) return;
    li.classList.add('just-parsed');
    setTimeout(() => li.classList.remove('just-parsed'), 5000);
}

function startBatchTimer() {
    stopBatchTimer();
    const timerElem = document.getElementById('ocrTimer');
    if (timerElem) timerElem.innerText = '00:00.0';
    BatchUploader.timerInterval = setInterval(() => {
        const u = BatchUploader.photos.find(p => p.status === 'uploading');
        if (!u) return;
        if (!u.startedAt) u.startedAt = Date.now();
        const elapsed = Date.now() - u.startedAt;
        const totalSec = Math.floor(elapsed / 1000);
        const mins = Math.floor(totalSec / 60).toString().padStart(2, '0');
        const secs = (totalSec % 60).toString().padStart(2, '0');
        const tenths = Math.floor((elapsed % 1000) / 100);
        if (timerElem) timerElem.innerText = `${mins}:${secs}.${tenths}`;
    }, 100);
}
function stopBatchTimer() {
    if (BatchUploader.timerInterval) {
        clearInterval(BatchUploader.timerInterval);
        BatchUploader.timerInterval = null;
    }
}

// P1-4：批量聚合进度条——聚合本批上传/解析进度，更新顶部进度组件
window.updateBatchAggregateProgress = updateBatchAggregateProgress;
function updateBatchAggregateProgress() {
    const wrap = document.getElementById('batchAggregateProgress');
    const bar = document.getElementById('batchProgressBar');
    const text = document.getElementById('batchProgressText');
    const detail = document.getElementById('batchProgressDetail');
    if (!wrap || !bar || !text) return;
    const total = BatchUploader.photos.length;
    if (total < 2) {
        wrap.classList.add('hide');
        return;
    }
    wrap.classList.remove('hide');
    let done = 0, uploading = 0, failed = 0, pending = 0;
    BatchUploader.photos.forEach(p => {
        if (p.status === 'parsed' || p.status === 'saved') done += 1;
        else if (p.status === 'uploading') uploading += 1;
        else if (p.status === 'error') failed += 1;
        else pending += 1;
    });
    const pct = total ? Math.round((done / total) * 100) : 0;
    bar.style.width = pct + '%';
    text.textContent = done + ' / ' + total + ' 完成 (' + pct + '%)';
    if (detail) {
        const parts = [];
        if (uploading) parts.push(uploading + ' 解析中');
        if (pending) parts.push(pending + ' 待解析');
        if (failed) parts.push(failed + ' 失败');
        if (done) parts.push(done + ' 已完成');
        detail.textContent = parts.join(' · ') || '等待上传';
    }
}

function setActivePhoto(idx) {
    if (idx < 0 || idx >= BatchUploader.photos.length) return;
    resetManualEntryMode();   // D12：切换照片 → 退出新建手工单态（恢复左栏原图区/标题徽章）
    BatchUploader.activeIndex = idx;
    renderSider();
    const photo = BatchUploader.photos[idx];
    if (!photo) return;

    // 同步 selectedFile 以便裁剪/上传使用正确文件
    if (photo.file) selectedFile = photo.file;

    if (photo.status === 'pending') {
        // 先清 transform 状态，再设预览——reset 内部走 setMainPreview，不会冲掉 HEIC 提示
        currentZoom = 1.0;
        currentRotation = 0;
        panX = 0;
        panY = 0;
        isFocalZoomed = false;
        activeTool = null;
        setMainPreview(photo);
        const img = document.getElementById('previewImg');
        if (img) {
            img.style.transformOrigin = 'center center';
            applyImgTransform();
        }
        updateToolbarButtonStates();
        document.getElementById('preConfirmCard').classList.remove('hide');
        document.getElementById('loadingCard').classList.add('hide');
        document.getElementById('prefillFormCard').classList.add('hide');
        document.getElementById('errorCard').classList.add('hide');
        currentReceiptId = null;
        currentReceiptData = null;
        return;
    }
    if (photo.status === 'uploading') {
        setMainPreview(photo);
        showLoadingCard();
        startBatchTimer();
        currentReceiptId = null;
        currentReceiptData = null;
        return;
    }
    if (photo.status === 'error') {
        currentZoom = 1.0;
        currentRotation = 0;
        panX = 0;
        panY = 0;
        isFocalZoomed = false;
        activeTool = null;
        setMainPreview(photo);
        const img = document.getElementById('previewImg');
        if (img) {
            img.style.transformOrigin = 'center center';
            applyImgTransform();
        }
        updateToolbarButtonStates();
        // D28/D33: 失败照片展示错误卡片双出口（有单据行则可重试/转手工录入）
        showErrorCard(photo.errorMsg || '识别失败', photo.receiptId || null);
        currentReceiptId = photo.receiptId || null;
        currentReceiptData = null;
        return;
    }
    applyPhotoToMainArea(photo);
}

function applyPhotoToMainArea(photo) {
    if (!photo) return;
    resetManualEntryMode();   // D12：真实单据接管主区域 → 退出新建手工单态
    if (photo.file) selectedFile = photo.file;
    // 预览：有 JPEG/裁剪图则显示；否则 HEIC 常驻提示（不经 objectUrl）
    currentZoom = 1.0;
    currentRotation = 0;
    panX = 0;
    panY = 0;
    isFocalZoomed = false;
    activeTool = null;
    setMainPreview(photo);
    const img = document.getElementById('previewImg');
    if (img) {
        img.style.transformOrigin = 'center center';
        applyImgTransform();
    }
    updateToolbarButtonStates();
    currentReceiptId = photo.receiptId;
    currentReceiptData = photo.data;
    if (photo.data) {
        renderEditForm(photo.data);
        // Q29：切换照片时优先展示该照片留存的合并警告（上传时顶层 quality_warnings）
        if (photo.qualityWarnings) showQualityWarnings(photo.qualityWarnings);
        document.getElementById('preConfirmCard').classList.add('hide');
        document.getElementById('loadingCard').classList.add('hide');
        document.getElementById('errorCard').classList.add('hide');
        document.getElementById('prefillFormCard').classList.remove('hide');
    } else {
        // 无数据：清空质量警告横幅，避免展示上一张照片的警告
        showQualityWarnings([]);
        document.getElementById('preConfirmCard').classList.remove('hide');
        document.getElementById('loadingCard').classList.add('hide');
        document.getElementById('errorCard').classList.add('hide');
        document.getElementById('prefillFormCard').classList.add('hide');
    }
}

function renderSider() {
    pruneUnselectableFromSelection();
    const list = document.getElementById('photoSiderList');
    const counter = document.getElementById('siderCount');
    if (!list || !counter) return;
    counter.innerText = BatchUploader.photos.length;
    // 仅本批 >= 2 张才展示抽屉与入口按钮；<= 1 张时一律收起
    if (BatchUploader.photos.length <= 1) {
        list.innerHTML = '';
        updateSiderVisibility();
        updateSelectionUI();
        updateBatchAggregateProgress();
        return;
    }

    // A2/D（批4）：按 review_priority_score 降序渲染侧栏（高优先级排前）。
    // 排序只影响显示顺序，data-idx 与 onclick 仍用原始 index 保持交互稳定；
    // 无分值（待解析/失败/手工单）排最后，顺序保持上传先后。
    const SCORE_HIGHLIGHT_THRESHOLD = 60;
    const scoreOf = (p) => {
        const s = p && p.data && p.data.review_priority_score;
        return (typeof s === 'number' && Number.isFinite(s)) ? s : null;
    };
    const sorted = BatchUploader.photos
        .map((p, i) => ({ p, i }))
        .sort((a, b) => {
            const sa = scoreOf(a.p), sb = scoreOf(b.p);
            if (sa == null && sb == null) return a.i - b.i;
            if (sa == null) return 1;
            if (sb == null) return -1;
            return sb - sa;
        });

    const html = sorted.map(({ p, i: idx }) => {
        const score = scoreOf(p);
        const isHigh = score != null && score >= SCORE_HIGHLIGHT_THRESHOLD;
        const scoreHtml = score != null
            ? `<span class="sider-score${isHigh ? ' sider-score-high' : ''}" title="复核优先级分 ${score}">${Math.round(score)}</span>`
            : '';
        const statusLabel = (
            p.status === 'parsed' ? ' 已解析' :
            p.status === 'saved' ? ' 已保存' :
            p.status === 'error' ? '失败' :
            p.status === 'uploading' ? '解析中' : '待解析'
        );
        const isSelected = BatchUploader.selected.has(idx);
        const isActive = idx === BatchUploader.activeIndex;
        const cls = `photo-sider-item ${p.status} ${isActive ? 'active' : ''} ${isSelected ? 'selected' : ''} ${isHigh ? 'high-priority' : ''}`;
        // 回归排查（R0）：文件名用户可控——title 属性与文本位统一 w2Escape
        const fileName = p.fileName || (p.file ? p.file.name : null) || (p.data && p.data.supplier_name ? `${p.data.supplier_name}.jpg` : `单据 #${p.receiptId || (idx + 1)}`);
        const safeName = w2Escape(fileName);
        const checkboxHtml = isAdmin()
            ? `<div class="check" onclick="event.stopPropagation()"><input type="checkbox" ${isSelected ? 'checked' : ''} ${isPhotoSelectable(p) ? '' : 'disabled'} onchange="togglePhotoSelection(${idx}, this.checked)"></div>`
            : '';
        // D28: 失败项提供显式重试入口（复用原单据，D13）
        const retryBtnHtml = p.status === 'error'
            ? `<div class="sider-item-actions"><button class="btn-sider-retry" title="复用原单据重试识别" onclick="event.stopPropagation(); retryPhotoFromSider(${idx})"> 重试</button></div>`
            : '';
        const isNonWeb = isNonWebImageFile(p.file || p.fileName);
        const bgUrl = p.imageUrl || p.croppedObjectUrl || (isNonWeb ? makeSvgDataUrl('HEIC') : p.objectUrl) || makeSvgDataUrl('收据');
        const activeBadgeHtml = isActive
            ? `<span class="sider-active-badge" title="当前正在查看">
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="vertical-align:middle;">
                    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
                    <circle cx="12" cy="12" r="3"></circle>
                </svg>当前
               </span>`
            : '';
        const viewingTagHtml = isActive ? `<span class="sider-viewing-tag">当前查看</span>` : '';

        return `
            <li class="${cls}" data-idx="${idx}" onclick="onPhotoSiderItemClick(${idx})">
                ${checkboxHtml}
                <div class="thumb" style="background-image:url('${bgUrl}')">
                    ${activeBadgeHtml}
                    ${scoreHtml}
                </div>
                <div class="meta">
                    <div class="name" title="${safeName}">${safeName}</div>
                    <div class="status">${statusLabel}${viewingTagHtml}</div>
                </div>
                ${retryBtnHtml}
            </li>
        `;
    }).join('');
    list.innerHTML = html;
    updateSelectionUI();
    updateToolbarButtonStates();
    updateBatchAggregateProgress();
    persistManifest();
}

// 从 batch 中移除第 idx 张照片
function removePhotoFromSider(idx) {
    if (idx < 0 || idx >= BatchUploader.photos.length) return;
    const photo = BatchUploader.photos[idx];

    // 如果已解析/已保存，给个确认
    if (photo.status === 'parsed' || photo.status === 'saved') {
        const displayName = photo.fileName || (photo.file ? photo.file.name : '此单据');
        const ok = confirm(`确定从本批列表中移除「${displayName}」吗？`);
        if (!ok) return;
    }

    // 释放 object URL
    try { URL.revokeObjectURL(photo.objectUrl); } catch(e) {}
    if (photo.pollToken) photo.pollToken.cancelled = true;   // Q28: 作废在途轮询（P1-14 丢弃结果）

    // 删除数组项 + 修正 activeIndex
    BatchUploader.photos.splice(idx, 1);
    if (BatchUploader.photos.length === 0) {
        BatchUploader.activeIndex = 0;
        // 完全清空：隐藏 splitView、入口按钮、收起抽屉
        document.getElementById('splitViewArea').classList.add('hide');
        const btn = document.getElementById('btnSiderToggle');
        if (btn) btn.classList.add('hide');
        const drawer = document.getElementById('photoSider');
        if (drawer) drawer.classList.remove('open');
        BatchUploader.siderOpen = false;
        renderSider(); // 会处理空列表隐藏
        return;
    }
    if (idx === BatchUploader.activeIndex) {
        BatchUploader.activeIndex = Math.min(idx, BatchUploader.photos.length - 1);
        setActivePhoto(BatchUploader.activeIndex);
    } else if (idx < BatchUploader.activeIndex) {
        BatchUploader.activeIndex -= 1;
    }
    renderSider();
}

// 删除当前正在预览的照片（sider 工具栏「删除图片」按钮）：复用批次移除逻辑
function deleteCurrentImage() {
    const idx = BatchUploader.activeIndex;
    const photo = BatchUploader.photos[idx];
    if (!photo) {
        showToast('当前没有可删除的照片', 'info');
        return;
    }
    removePhotoFromSider(idx);
}

// “重新选择图片”标记：选完新图后先移除当前预览照片，再添加新图（替换而非追加）
let _reselectReplaceMode = false;
function startReselectImage() {
    _reselectReplaceMode = true;
    const fi = document.getElementById('receiptFile');
    if (fi) fi.click();
}

function onPhotoSiderClick(idx) {
    const photo = BatchUploader.photos[idx];
    if (!photo) return;
    if (photo.status === 'uploading') {
        showToast('该照片正在解析中，请稍候', 'info');
        return;
    }
    setActivePhoto(idx);
}

// 点击相片项任意区域：激活预览并切换选中状态（整行可点）
function onPhotoSiderItemClick(idx) {
    const photo = BatchUploader.photos[idx];
    if (!photo) return;
    if (photo.status === 'uploading') {
        showToast('该照片正在解析中，请稍候', 'info');
        return;
    }
    setActivePhoto(idx);
    const checked = !BatchUploader.selected.has(idx);
    togglePhotoSelection(idx, checked);
}

function retryPhotoFromSider(idx) {
    const photo = BatchUploader.photos[idx];
    if (!photo) return;

    // D13: 已有单据行（上传成功但识别失败）→ 走 retry 端点复用原单据，不新建记录
    if (photo.receiptId) {
        photo.status = 'uploading';
        photo.errorMsg = null;
        renderSider();
        updateSelectionUI();

        const wasActive = BatchUploader.activeIndex === idx;
        if (wasActive) showLoadingCard();
        startBatchTimer();

        fetch(`/api/receipt/${photo.receiptId}/retry`, { method: 'POST' })
            .then(res => res.json())
            .then(ret => {
                if (ret.status === 'queued' && ret.job_id) {
                    pollReceiptJob(ret.job_id, (jobRet) => {
                        stopBatchTimer();
                        if (!isPhotoInBatch(photo)) {
                            if (!BatchUploader.photos.some(p => p.status === 'uploading')) hideLoadingCard();
                            return;
                        }
                        const curIdx = findPhotoIndex(photo);
                        const isActive = BatchUploader.activeIndex === curIdx;
                        if (wasActive || isActive) hideLoadingCard();
                        if (jobRet.status === 'success') {
                            photo.status = 'parsed';
                            if (jobRet.receipt_id) photo.receiptId = jobRet.receipt_id;
                            if (jobRet.image_url) photo.imageUrl = jobRet.image_url;
                            photo.data = jobRet.data || photo.data;
                            captureResultVersion(jobRet);
                            if (Array.isArray(jobRet.quality_warnings)) photo.qualityWarnings = jobRet.quality_warnings;
                            renderSider();
                            autoSaveParsedPhoto(curIdx);
                            if (isActive) {
                                applyPhotoToMainArea(photo);
                            } else {
                                showToast(`第 ${curIdx + 1} 张重试识别完成，可切换复核`, 'success');
                                flashSiderItem(curIdx);
                            }
                        } else {
                            photo.status = 'error';
                            photo.errorMsg = jobRet.msg || '重试识别失败';
                            renderSider();
                            if (isActive) {
                                showErrorCard(photo.errorMsg, photo.receiptId);
                            } else {
                                showToast(`第 ${curIdx + 1} 张重试失败：${photo.errorMsg}`, 'error');
                            }
                        }
                    });
                    return;
                }
                stopBatchTimer();
                // P1-14: 回调时按 localId 校验照片仍在批次中，已移除则丢弃结果
                if (!isPhotoInBatch(photo)) {
                    if (!BatchUploader.photos.some(p => p.status === 'uploading')) hideLoadingCard();
                    return;
                }
                const curIdx = findPhotoIndex(photo);
                const isActive = BatchUploader.activeIndex === curIdx;
                if (wasActive || isActive) hideLoadingCard();
                if (ret.status === 'success') {
                    photo.status = 'parsed';
                    if (ret.receipt_id) photo.receiptId = ret.receipt_id;
                    if (ret.image_url) photo.imageUrl = ret.image_url;
                    photo.data = ret.data || photo.data;
                    captureResultVersion(ret);   // P1-13: version 回写 photo.data
                    if (Array.isArray(ret.quality_warnings)) photo.qualityWarnings = ret.quality_warnings;
                    renderSider();
                    // 解析完成即自动写库（无“全部保存”入口）
                    autoSaveParsedPhoto(curIdx);
                    if (isActive) {
                        applyPhotoToMainArea(photo);
                    } else {
                        showToast(`第 ${curIdx + 1} 张重试识别完成，可切换复核`, 'success');
                        flashSiderItem(curIdx);
                    }
                } else {
                    photo.status = 'error';
                    photo.errorMsg = ret.msg || '重试识别失败';
                    renderSider();
                    if (isActive) {
                        showErrorCard(photo.errorMsg, photo.receiptId);
                    } else {
                        showToast(`第 ${curIdx + 1} 张重试失败：${photo.errorMsg}`, 'error');
                    }
                }
            })
            .catch(err => {
                stopBatchTimer();
                if (wasActive) hideLoadingCard();
                if (!isPhotoInBatch(photo)) return;   // P1-14: 已移除则丢弃
                console.error('重试识别请求异常', err);
                photo.status = 'error';
                photo.errorMsg = '重试失败，请稍后重试';
                renderSider();
                if (BatchUploader.activeIndex === findPhotoIndex(photo)) {
                    showErrorCard(photo.errorMsg, photo.receiptId);
                }
            });
        return;
    }

    // 无单据行（落盘阶段即失败）：兜底走重新上传
    if (BatchUploader.photos.length === 1) {
        uploadSinglePhoto(idx);
    } else {
        photo.status = 'uploading';
        photo.errorMsg = null;
        renderSider();
        updateSelectionUI();
        const useCodebuddy = document.getElementById('chkCodebuddy').checked;
        const formData = new FormData();
        formData.append('files', photo.file);
        formData.append('force', photo.force ? 'true' : 'false');
        showLoadingCard();
        startBatchTimer();
        fetch(`/api/upload_batch?codebuddy=${useCodebuddy}&force=${photo.force ? 'true' : 'false'}`, { method: 'POST', body: formData })
            .then(res => res.json())
            .then(ret => {
                stopBatchTimer();
                hideLoadingCard();
                if (!isPhotoInBatch(photo)) return;   // P1-14: 已移除则丢弃结果
                const curIdx = findPhotoIndex(photo);
                const r = (ret.results || [])[0];
                if (r && r.status === 'parsed') {
                    photo.status = 'parsed';
                    photo.receiptId = r.receipt_id;
                    photo.imageUrl = r.image_url;
                    photo.data = r.data;
                    captureResultVersion(r);
                    renderSider();
                    // 解析完成即自动写库（无“全部保存”入口）
                    autoSaveParsedPhoto(curIdx);
                    setActivePhoto(curIdx);
                } else {
                    photo.status = 'error';
                    photo.errorMsg = (r && r.msg) || '识别失败';
                    if (r && r.receipt_id) photo.receiptId = r.receipt_id;
                    if (r && r.image_url) photo.imageUrl = r.image_url;
                    renderSider();
                    if (BatchUploader.activeIndex === curIdx) {
                        showErrorCard(photo.errorMsg, photo.receiptId || null);
                    }
                }
            })
            .catch(err => {
                stopBatchTimer();
                hideLoadingCard();
                if (!isPhotoInBatch(photo)) return;   // P1-14: 已移除则丢弃结果
                console.error('重新上传识别异常', err);
                photo.status = 'error';
                photo.errorMsg = '识别失败，请稍后重试';
                renderSider();
                if (BatchUploader.activeIndex === findPhotoIndex(photo)) {
                    showErrorCard(photo.errorMsg, photo.receiptId || null);
                }
            });
    }
}

// -------------------------------------------------------------
// 解析即自动写库：每张照片解析完成（status=parsed 且有预填数据）后，
// 以 AI 预填数据调 save_edited 落库；成功后状态变 saved（sider 显示「已保存」）。
// 发即弃、无确认弹窗；仅 admin 触发；失败仅提示，保留 parsed 供手动重试。
// -------------------------------------------------------------
function autoSaveParsedPhoto(idx) {
    const p = BatchUploader.photos[idx];
    if (!p || p.status !== 'parsed' || !p.data || p.autoSaving) return;
    if (!isAdmin()) return;   // 仅 admin 可写库（与旧「全部保存」角色限制一致）

    p.autoSaving = true;
    // 解析即按 AI 预填保存，不读取正在编辑的表单，避免覆盖用户复核中的内容
    const data = p.data;
    const payload = buildSavePayloadFromData(data, p.receiptId);
    payload.source = 'auto';

    fetch('/api/save_edited', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
        .then(res => res.json().then(body => ({ res, body })).catch(() => ({ res, body: null })))
        .then(({ res, body }) => {
            p.autoSaving = false;
            if (res.status === 409 && body && body.code === 'VERSION_CONFLICT') {
                // D17: 版本冲突，保留 parsed 供手动重试
                showToast(`第 ${idx + 1} 张保存冲突，请打开详情重载后再试`, 'warning');
                return;
            }
            if (res.status === 400 && body && body.code === 'VERSION_REQUIRED') {
                showToast(`第 ${idx + 1} 张缺少版本号，请打开详情重载后再试`, 'warning');
                return;
            }
            if (!res.ok || !body || body.status !== 'success') {
                showToast(`第 ${idx + 1} 张自动保存失败`, 'error');
                return;
            }
            // 保存成功
            resetQualityFailCount();
            p.status = 'saved';
            p.data = data;
            const newVersion = extractVersionFromResponse(body);
            if (newVersion != null && p.data) p.data.version = newVersion;
            if (idx === BatchUploader.activeIndex) currentReceiptData = p.data;
            renderSider();
            updateSelectionUI();
        })
        .catch(err => {
            p.autoSaving = false;
            console.error('自动保存异常', err);
            showToast(`第 ${idx + 1} 张自动保存失败`, 'error');
        });
}

window.togglePhotoSider = togglePhotoSider;
window.onPhotoSiderClick = onPhotoSiderClick;
window.onPhotoSiderItemClick = onPhotoSiderItemClick;
window.retryPhotoFromSider = retryPhotoFromSider;
window.triggerBatchAnalysis = triggerBatchAnalysis;
window.removePhotoFromSider = removePhotoFromSider;
window.removeSelectedPhotos = removeSelectedPhotos;
window.onSiderSelectAll = onSiderSelectAll;
window.togglePhotoSelection = togglePhotoSelection;
window.retryFromErrorCard = retryFromErrorCard;
window.convertManualFromErrorCard = convertManualFromErrorCard;
// D12：新建手工单入口（index.html 内联 onclick 引用）
window.startManualEntry = startManualEntry;
window.abortLoadingAndSwitchToManual = abortLoadingAndSwitchToManual;
window.syncManualEntryVisibility = syncManualEntryVisibility;
// Q35：归档 error 行入口
window.retryReceiptFromArchive = retryReceiptFromArchive;
window.convertReceiptFromArchive = convertReceiptFromArchive;
// Wave 1：应付清单行内操作与提醒入口（index.html 内联 onclick 引用）
window.gotoPayablesList = gotoPayablesList;
window.savePayDate = savePayDate;
window.openPaymentModalForReceipt = openPaymentModalForReceipt;
window.applyPayablesFilters = applyPayablesFilters;

// ==========================================================================
// Wave 2 财务闭环（T7/M5-M8，D15/D18/D20）
//   - CSV 导出（归档筛选参数透传）
//   - 支付登记 + 未付赊单/账期/逾期看板
//   - 月结对账引擎前端（任务列表 / 差异明细 / 处理 / 确认锁定）
//   - 供应商合并（D18）
//   - 盘点调整（D15）
// ==========================================================================

// ---- 小工具 ----（w2Escape/jsStr 已移至文件头部并立为硬规则，见文件开头注释）

function closeModalById(id) {
    const el = document.getElementById(id);
    if (el) el.classList.add('hide');
}

function openModalById(id) {
    const el = document.getElementById(id);
    if (el) el.classList.remove('hide');
}

function fmtMoney(v) {
    const n = Number(v || 0);
    return n.toFixed(2);
}

// ---- CSV 导出（D20/M6）：按当前归档筛选条件透传 ----
function exportArchiveCsv() {
    const params = new URLSearchParams();
    const supplier = (document.getElementById('fltSupplier')?.value || '').trim();
    const status = (document.getElementById('fltStatus')?.value || '').trim();
    if (supplier) params.set('supplier', supplier);
    if (status) params.set('status', status);

    const pairs = [
        ['fltReceiptDateFrom', 'receipt_date_from'], ['fltReceiptDateTo', 'receipt_date_to'],
        ['fltUploadDateFrom', 'upload_date_from'], ['fltUploadDateTo', 'upload_date_to'],
        ['fltUpdateDateFrom', 'update_date_from'], ['fltUpdateDateTo', 'update_date_to'],
    ];
    pairs.forEach(([elId, key]) => {
        const v = (document.getElementById(elId)?.value || '').trim();
        if (v) params.set(key, v);
    });
    const min = document.getElementById('fltAmountMin')?.value;
    const max = document.getElementById('fltAmountMax')?.value;
    if (min !== undefined && min !== '') params.set('amount_min', min);
    if (max !== undefined && max !== '') params.set('amount_max', max);

    // P1-12：改 fetch+blob 下载——window.open 不携带 Authorization，AUTH=1 时会 401
    showToast('正在按当前筛选条件导出 CSV…', 'info');
    fetch(`/api/receipts/export?${params.toString()}`)
        .then(res => {
            if (!res.ok) throw new Error('HTTP ' + res.status);
            return res.blob();
        })
        .then(blob => {
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'receipts_export.csv';
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(() => URL.revokeObjectURL(url), 2000);
        })
        .catch(err => {
            console.error('CSV 导出失败:', err);
            showToast('CSV 导出失败，请稍后重试', 'error');
        });
}

// ---- 支付与对账面板（Wave 1 升级：跨供应商应付清单，03 章） ----
const finState = {
    suppliers: [],
    supplierId: null,      // null = 全部供应商（全局视图，可按供应商收敛）
    receipts: [],          // 全量单据（/api/receipts，含契约①付款域派生字段）
    currentTaskId: null,
    currentDetail: null,
    resolveCtx: null,
};

const RECON_STATUS_BADGES = {
    unreconciled: ['badge-secondary', '未对账'],
    reconciling: ['badge-warning', '对账中'],
    disputed: ['badge-danger', '争议中'],
    confirmed: ['badge-success', '已确认'],
};
const MATCH_BADGES = {
    matched: ['badge-success', '已匹配'],
    pending: ['badge-warning', '待人工确认'],
    amount_mismatch: ['badge-danger', '金额不符'],
    supplier_only: ['badge-danger', '账单有·餐厅无'],
    restaurant_only: ['badge-warning', '餐厅有·账单无'],
};

// Wave 1：付款方式展示口径（契约① paid_method 四态；未付显示 —）
const PAY_METHOD_LABELS = {
    cash: ' 现金',
    bank_transfer: ' 银行转账',
    cheque: ' 支票',
    fps: ' FPS',
};

// owner 判定：AUTH_ENABLED=0 时 /api/auth/me 返回 owner；仅明确 staff 时隐藏资金类行内操作
function isOwnerRole() {
    const role = AuthState.account && AuthState.account.role;
    return role !== 'staff';
}

// ISO 日期（YYYY-MM-DD）距今天数：正数=未来，负数=已过；非法/空 → null
function daysFromToday(dateStr) {
    const m = String(dateStr || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!m) return null;
    const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
    const t = new Date();
    const today = new Date(t.getFullYear(), t.getMonth(), t.getDate());
    return Math.round((d - today) / 86400000);
}

// 应付清单行域（与 compute_supplier_payables 债务口径对齐）：
// 现金单/赊单；月结账单 statement 是下属赊单汇总单避免重复计债；
// 仅 parsed/edited/approved 有效态（flagged 已隔离、识别中/失败不入债务）。
function isPayableRow(r) {
    if (!r) return false;
    const st = r.settlement_type;
    if (st !== 'credit' && st !== 'cash') return false;
    if (r.doc_form === 'statement') return false;
    const s = r.status || '';
    return s === 'parsed' || s === 'edited' || s === 'approved';
}

// Wave 1 付款状态徽章（契约①五态）；未知值兜底过 w2Escape（P0 硬规则）
function payStatusBadgeHtml(r) {
    const st = r && r.payment_status;
    if (st === 'paid_at_delivery') return '<span class="badge badge-success">现结已付</span>';
    if (st === 'paid') return '<span class="badge badge-success">已付</span>';
    if (st === 'overdue') {
        const n = daysFromToday(r.expected_pay_date);
        const daysTxt = (n !== null && n < 0) ? ' ' + (-n) + '天' : '';
        return '<span class="badge badge-danger">逾期' + daysTxt + '</span>';
    }
    if (st === 'unpaid') {
        let badge = '<span class="badge badge-warning">未付</span>';
        if (r.due_soon) {
            const n = daysFromToday(r.expected_pay_date);
            const daysTxt = (n !== null) ? n + '天到期' : '快到期';
            badge += '<div style="font-size:0.72rem; color:#d97706; margin-top:2px;">' + daysTxt + '</div>';
        }
        return badge;
    }
    if (st === 'unknown') return '<span class="badge badge-secondary">未识别</span>';
    return '<span class="badge badge-secondary">' + w2Escape(st ?? '—') + '</span>';
}

// 归档列表轻量付款徽章（03 章六：只展示不编辑）；
// 未进入付款域的单据（结算未识别/缺失）显示弱化 —，避免满屏噪音
function payStatusLiteBadgeHtml(r) {
    const st = r && r.payment_status;
    if (st === 'paid_at_delivery' || st === 'paid' || st === 'unpaid' || st === 'overdue') {
        return payStatusBadgeHtml(r);
    }
    return '<span style="color:var(--text-muted); font-size:0.8rem;">—</span>';
}

// Wave 1 提醒统计（02 章：派生不存储；红点计数 = 逾期 + 快到期 单据数）
function computePayStats(receipts) {
    let unpaidTotal = 0, unpaidCount = 0, overdueCount = 0, dueSoonCount = 0;
    (receipts || []).forEach(r => {
        const st = r.payment_status;
        if (st === 'unpaid' || st === 'overdue') {
            unpaidTotal += Number(r.total_amount || 0);
            unpaidCount += 1;
        }
        if (st === 'overdue') overdueCount += 1;
        if (r.due_soon) dueSoonCount += 1;
    });
    return { unpaidTotal, unpaidCount, overdueCount, dueSoonCount };
}

function loadFinancePanel() {
    // 店员不发财务域请求（payments/reconciliation 为 owner 域），红点/横幅提醒属老板视角
    if (isStaffRoleNow()) return;
    // 全局视图：供应商 + 全量单据并拉（契约①③）
    Promise.all([
        fetch('/api/suppliers').then(res => res.json()),
        fetch('/api/receipts').then(res => res.json()),
    ])
        .then(([supRet, rcRet]) => {
            if (supRet && supRet.status === 'success') finState.suppliers = supRet.data || [];
            if (rcRet && rcRet.status === 'success') finState.receipts = rcRet.data || [];

            const sel = document.getElementById('finSupplier');
            if (!sel) return;
            const prev = sel.value || (finState.supplierId ? String(finState.supplierId) : '');
            sel.innerHTML = '<option value="">全部供应商</option>';
            finState.suppliers.forEach(s => {
                const opt = document.createElement('option');
                opt.value = String(s.id);
                const overdue = s.is_overdue ? ' 逾期' : '';
                opt.textContent = `${s.name} [${s.supplier_code || '-'}]${overdue}`;
                sel.appendChild(opt);
            });
            if (prev && finState.suppliers.some(s => String(s.id) === prev)) sel.value = prev;
            else sel.value = '';
            finState.supplierId = sel.value ? Number(sel.value) : null;

            renderPayablesAll();
        })
        .catch(err => console.error('加载财务面板失败:', err));
}

function renderPayablesAll() {
    renderPayablesSummary();
    applyPayablesFilters();
    updatePaymentReminders();
    loadPaymentHistory();
    loadReconTasks();
}

// 顶部汇总：未付总额 / 逾期数 / 7 天内到期数（选中供应商时补供应商口径）
function renderPayablesSummary() {
    const box = document.getElementById('financeSummary');
    if (!box) return;
    const stats = computePayStats((finState.receipts || []).filter(isPayableRow));
    const sup = finState.supplierId
        ? finState.suppliers.find(s => s.id === finState.supplierId) : null;

    let html = `
        <div style="display:flex; flex-wrap:wrap; gap:10px; align-items:center;">
            <span style="font-size:1.02rem;">未付总额：
                <strong style="color:${stats.unpaidTotal > 0 ? '#f59e0b' : '#10b981'};">
                    $${fmtMoney(stats.unpaidTotal)}</strong>
                （${stats.unpaidCount} 张）</span>
            <span class="badge ${stats.overdueCount ? 'badge-danger' : 'badge-success'}">[高风险] 逾期 ${stats.overdueCount} 张</span>
            <span class="badge ${stats.dueSoonCount ? 'badge-warning' : 'badge-success'}">[注意] 7 天内到期 ${stats.dueSoonCount} 张</span>
        </div>`;
    if (sup) {
        const overdueBadge = sup.is_overdue
            ? `<span class="badge badge-danger">该供应商存在逾期</span>`
            : '<span class="badge badge-success">该供应商账期内</span>';
        const termsTxt = (sup.payment_terms_days != null)
            ? w2Escape(String(sup.payment_terms_days)) + ' 天' : '未设置';
        html += `
        <div style="display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin-top:8px;">
            <span class="badge badge-secondary">当前筛选：${w2Escape(sup.name)}</span>
            <span class="badge badge-secondary">账期 ${termsTxt}</span>
            <span style="font-size:0.92rem;">未付赊单合计：
                <strong style="color:${sup.unpaid_credit_total > 0 ? '#f59e0b' : '#10b981'};">
                    $${fmtMoney(sup.unpaid_credit_total)}</strong>
                （${sup.unpaid_credit_count} 张）</span>
            ${overdueBadge}
        </div>`;
    }
    box.innerHTML = html;
}

// 筛选：供应商（收敛）/ 已付未付 / 付款方式 / 逾期·快到期·未设日期（03 章三）
function getFilteredPayables() {
    const fltPay = document.getElementById('fltFinPay')?.value || '';
    const fltMethod = document.getElementById('fltFinMethod')?.value || '';
    const fltDue = document.getElementById('fltFinDue')?.value || '';

    let rows = (finState.receipts || []).filter(isPayableRow);
    if (finState.supplierId) {
        const sup = finState.suppliers.find(s => s.id === finState.supplierId);
        if (sup) rows = rows.filter(r => r.supplier_name === sup.name);
    }
    if (fltPay === 'unpaid') {
        rows = rows.filter(r => r.payment_status === 'unpaid' || r.payment_status === 'overdue');
    } else if (fltPay === 'paid') {
        rows = rows.filter(r => r.payment_status === 'paid' || r.payment_status === 'paid_at_delivery');
    }
    if (fltMethod) rows = rows.filter(r => r.paid_method === fltMethod);
    if (fltDue === 'overdue') rows = rows.filter(r => r.payment_status === 'overdue');
    else if (fltDue === 'due_soon') rows = rows.filter(r => !!r.due_soon);
    else if (fltDue === 'no_date') rows = rows.filter(r => r.payment_status === 'unpaid' && !r.expected_pay_date);

    // 默认排序（03 章二）：未付在前、expected_pay_date 升序（最急在前）、无日期沉底；
    // 已付行沉底按支付时点倒序（现结无支付记录回退开单日期）
    rows.sort((a, b) => {
        const aPaid = a.payment_status === 'paid' || a.payment_status === 'paid_at_delivery';
        const bPaid = b.payment_status === 'paid' || b.payment_status === 'paid_at_delivery';
        if (aPaid !== bPaid) return aPaid ? 1 : -1;
        if (!aPaid) {
            const ad = a.expected_pay_date || '';
            const bd = b.expected_pay_date || '';
            if (ad && bd && ad !== bd) return ad < bd ? -1 : 1;
            if (ad && !bd) return -1;
            if (!ad && bd) return 1;
            return (b.id || 0) - (a.id || 0);
        }
        const at = a.paid_at || a.receipt_date || '';
        const bt = b.paid_at || b.receipt_date || '';
        if (at !== bt) return at < bt ? 1 : -1;
        return (b.id || 0) - (a.id || 0);
    });
    return rows;
}

function applyPayablesFilters() {
    renderPayablesTable(getFilteredPayables());
}

function renderPayablesTable(rows) {
    const body = document.getElementById('payablesBody');
    if (!body) return;
    body.innerHTML = '';
    if (!rows.length) {
        body.innerHTML = '<tr><td colspan="7" style="text-align:center; color:var(--text-muted); padding:28px;">当前筛选条件下没有应付单据</td></tr>';
        return;
    }
    const owner = isOwnerRole();
    rows.forEach(r => {
        const rid = Number(r.id);
        const tr = document.createElement('tr');
        // 逾期行整行标红、快到期行高亮（02 章三）
        if (r.payment_status === 'overdue') tr.className = 'payable-row-overdue';
        else if (r.due_soon) tr.className = 'payable-row-due-soon';

        const supCode = r.supplier_code
            ? ' <span class="badge badge-secondary" style="font-size:0.72rem; font-family:monospace; opacity:0.85;">' + w2Escape(r.supplier_code) + '</span>'
            : '';

        // 预期付款日：未付赊单行内日期可编辑（契约④，owner 专属）；其余只读
        let dateCell;
        const editable = owner && r.settlement_type === 'credit' && !r.payment_id &&
            (r.payment_status === 'unpaid' || r.payment_status === 'overdue');
        if (editable) {
            dateCell = '<input type="date" class="form-control" style="font-size:0.8rem; padding:3px 6px; width:100%; max-width:140px; box-sizing:border-box;" ' +
                'value="' + w2Escape(r.expected_pay_date || '') + '" ' +
                'title="设置预期付款日；清空即取消日期" ' +
                'onchange="savePayDate(' + rid + ', this.value)">';
        } else {
            dateCell = renderDateCell(r.expected_pay_date);
        }
        if ((r.payment_status === 'paid' || r.payment_status === 'paid_at_delivery') && r.paid_at) {
            dateCell += '<div style="font-size:0.72rem; color:var(--text-muted);">支付于 ' + renderDateCell(r.paid_at) + '</div>';
        }

        const methodCell = r.paid_method
            ? w2Escape(PAY_METHOD_LABELS[r.paid_method] || r.paid_method)
            : '<span style="color:var(--text-muted);">—</span>';

        // 操作：未付赊单可登记支付（沿用既有护栏：现结单/账单不可挂）
        let actionHtml;
        if (r.settlement_type === 'credit' && !r.payment_id) {
            actionHtml = '<button class="btn btn-primary" style="padding:2px 8px; font-size:0.75rem;" ' +
                'onclick="openPaymentModalForReceipt(' + rid + ')"> 登记支付</button>';
        } else {
            actionHtml = '<span style="color:var(--text-muted); font-size:0.78rem;">—</span>';
        }

        tr.innerHTML = `
            <td style="vertical-align:middle; word-break:break-all; white-space:normal; text-align:center;">#${rid}<br>
                <strong>${w2Escape(r.supplier_name || '-')}</strong>${supCode}</td>
            <td style="vertical-align:middle;">${renderDateCell(r.receipt_date)}</td>
            <td style="vertical-align:middle;"><strong>$${fmtMoney(r.total_amount)}</strong></td>
            <td style="vertical-align:middle;">${payStatusBadgeHtml(r)}</td>
            <td style="vertical-align:middle;">${dateCell}</td>
            <td style="vertical-align:middle;">${methodCell}</td>
            <td style="vertical-align:middle; white-space:nowrap;">${actionHtml}</td>`;
        body.appendChild(tr);
    });
}

// Wave 1 提醒（02 章）：导航红点 + 顶部横幅；逾期+快到期为 0 时全部隐藏
function updatePaymentReminders() {
    const stats = computePayStats((finState.receipts || []).filter(isPayableRow));
    const count = stats.overdueCount + stats.dueSoonCount;
    const dot = document.getElementById('navPayRedDot');
    if (dot) {
        dot.textContent = count > 99 ? '99+' : String(count);
        dot.classList.toggle('hide', count === 0);
    }
    const banner = document.getElementById('payReminderBanner');
    if (banner) {
        if (count === 0) {
            banner.classList.add('hide');
            banner.innerHTML = '';
        } else {
            const parts = [];
            if (stats.overdueCount > 0) parts.push(w2Escape(String(stats.overdueCount)) + ' 张赊单已逾期');
            if (stats.dueSoonCount > 0) parts.push(w2Escape(String(stats.dueSoonCount)) + ' 张 7 天内到期');
            banner.innerHTML = '你有 <strong>' + parts.join('、') + '</strong>，点击查看应付清单 →';
            banner.classList.remove('hide');
        }
    }
}

// 横幅/外部入口点击 → 切到归档 Tab 并滚动到应付清单
function gotoPayablesList() {
    const btn = document.querySelector('.sidebar-btn[data-target="tab-archive"]');
    if (btn && btn.click) btn.click();
    const card = document.getElementById('payablesCard');
    if (card && card.scrollIntoView) {
        setTimeout(() => card.scrollIntoView({ behavior: 'smooth', block: 'start' }), 80);
    }
}

// 行内设预期付款日（契约④）：空值 = 清空；成功后全量刷新状态徽章与提醒
function savePayDate(receiptId, dateStr) {
    const rid = Number(receiptId);
    if (!Number.isFinite(rid) || rid <= 0) return;
    const value = String(dateStr || '').trim();
    fetch(`/api/receipt/${rid}/pay_date`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ expected_pay_date: value || null }),
    })
        .then(res => res.json())
        .then(ret => {
            if (!ret || ret.status !== 'success') {
                showToast((ret && ret.msg) || '设置预期付款日失败', 'error');
                loadFinancePanel();   // 回滚输入框显示，与服务端对齐
                return;
            }
            showToast(value
                ? `单据 #${rid} 预期付款日已设为 ${value}`
                : `已清空单据 #${rid} 的预期付款日`, 'success');
            loadFinancePanel();       // 重算付款状态徽章 / 汇总 / 提醒
            loadReceiptsHistory();    // 归档列表付款状态徽章联动
        })
        .catch(err => { console.error('设置预期付款日异常', err); showToast('设置预期付款日失败' + toastFailDetail(err), 'error'); });
}

// 应付清单行级「登记支付」：定位到该单供应商后复用既有 Modal 与护栏
function openPaymentModalForReceipt(receiptId) {
    const rid = Number(receiptId);
    if (!Number.isFinite(rid) || rid <= 0) return;
    const r = (finState.receipts || []).find(x => Number(x.id) === rid);
    if (!r) { showToast('未找到该单据，请刷新后重试', 'warning'); return; }
    if (r.settlement_type !== 'credit' || r.payment_id) {
        showToast('仅未付赊单可登记支付', 'warning');
        return;
    }
    const sup = (finState.suppliers || []).find(s => s.name === r.supplier_name);
    if (!sup) { showToast('未找到该单据对应的供应商，请先在上方下拉选择供应商', 'warning'); return; }
    const sel = document.getElementById('finSupplier');
    if (sel) sel.value = String(sup.id);
    finState.supplierId = sup.id;
    openPaymentModal();
}

// 支付履历：选中供应商 → 该供应商；未选 → 全部
function loadPaymentHistory() {
    const payArea = document.getElementById('paymentHistoryArea');
    const body = document.getElementById('paymentHistoryBody');
    if (!payArea || !body) return;
    const sup = finState.supplierId
        ? finState.suppliers.find(s => s.id === finState.supplierId) : null;
    const url = sup ? `/api/payments?supplier_id=${sup.id}` : '/api/payments';
    fetch(url)
        .then(res => res.json())
        .then(ret => {
            if (!ret || ret.status !== 'success') return;
            const pays = ret.data || [];
            body.innerHTML = '';
            if (!pays.length) {
                payArea.classList.add('hide');
                return;
            }
            const title = document.getElementById('paymentHistoryTitle');
            if (title) title.textContent = '支付履历';
            payArea.classList.remove('hide');
            pays.forEach(p => {
                const voucher = p.voucher_image_url
                    ? `<a href="${w2Escape(p.voucher_image_url)}" target="_blank">查看</a>` : '-';
                const linked = Array.isArray(p.linked_receipt_ids) ? p.linked_receipt_ids : [];
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td>#${Number(p.id)}</td>
                    <td>${w2Escape(p.supplier_name || '-')}</td>
                    <td>${renderDateCell(p.paid_at)}</td>
                    <td><strong>$${fmtMoney(p.amount)}</strong></td>
                    <td>${w2Escape(p.method_label)}</td>
                    <td>${Number(p.linked_receipt_count) || 0} 张${linked.length
                        ? '（' + linked.map(x => '#' + Number(x)).join('、') + '）' : ''}</td>
                    <td>${voucher}</td>
                    <td>${w2Escape(p.notes || '-')}</td>`;
                body.appendChild(tr);
            });
        });
}

// 对账任务列表：选中供应商 → 该供应商；未选 → 全部
function loadReconTasks() {
    const sup = finState.supplierId
        ? finState.suppliers.find(s => s.id === finState.supplierId) : null;
    const url = sup ? `/api/reconciliation?supplier_id=${sup.id}` : '/api/reconciliation';
    fetch(url)
        .then(res => res.json())
        .then(ret => {
            if (!ret || ret.status !== 'success') return;
            const title = document.getElementById('reconTaskTitle');
            if (title) title.textContent = '对账任务';
            renderReconTasks(ret.data || []);
        });
}

function renderReconTasks(tasks) {
    const area = document.getElementById('reconTaskArea');
    const body = document.getElementById('reconTaskBody');
    body.innerHTML = '';
    if (!tasks.length) {
        area.classList.add('hide');
        document.getElementById('reconDetailArea')?.classList.add('hide');
        return;
    }
    area.classList.remove('hide');
    tasks.forEach(t => {
        // 兜底 label 过 w2Escape（状态值理论上受控，防御纵深；P0 硬规则）
        const [cls, label] = RECON_STATUS_BADGES[t.status] || ['badge-secondary', w2Escape(t.status)];
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>#${Number(t.id)}</td>
            <td>${w2Escape(t.supplier_name || '-')}</td>
            <td>${w2Escape(t.period_start)} ~ ${w2Escape(t.period_end)}</td>
            <td>${w2Escape(t.period_type_label)}</td>
            <td><span class="badge ${cls}">${label}</span></td>
            <td>${t.summary.total_lines}</td>
            <td style="color:#10b981;">${t.summary.matched}</td>
            <td style="color:${t.summary.open_issues ? '#f59e0b' : '#10b981'};">${t.summary.open_issues}</td>
            <td><button class="btn btn-secondary" style="padding:3px 10px; font-size:0.8rem;"
                onclick="viewReconTask(${Number(t.id)})"> 查看详情</button></td>`;
        body.appendChild(tr);
    });
}

// ---- 登记支付 Modal ----
function openPaymentModal() {
    if (!finState.supplierId) { showToast('请先选择供应商', 'warning'); return; }
    const sup = finState.suppliers.find(s => s.id === finState.supplierId);
    document.getElementById('paymentSupplierHint').innerText =
        `供应商：${sup.name}`;
    document.getElementById('payAmount').value = sup.unpaid_credit_total > 0 ? sup.unpaid_credit_total : '';
    document.getElementById('payDate').value = getTodayDateStr();
    document.getElementById('payNotes').value = '';
    document.getElementById('payVoucher').value = '';
    document.getElementById('payCheckAll').checked = false;

    // 拉取最新单据列表（payment_id 实时），列出该供应商未付赊单供勾选
    fetch('/api/receipts')
        .then(res => res.json())
        .then(ret => {
            if (ret.status !== 'success') return;
            const body = document.getElementById('payReceiptBody');
            body.innerHTML = '';
            const rows = (ret.data || []).filter(r =>
                r.supplier_name === sup.name && r.settlement_type === 'credit' && !r.payment_id);
            if (!rows.length) {
                body.innerHTML = '<tr><td colspan="5" style="color:var(--text-muted);">该供应商暂无未付月结赊单</td></tr>';
            }
            rows.forEach(r => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td><input type="checkbox" class="pay-receipt-check" value="${r.id}" checked></td>
                    <td>#${r.id}</td>
                    <td>${renderDateCell(r.receipt_date)}</td>
                    <td>$${fmtMoney(r.total_amount)}</td>
                    <td>${w2Escape(r.status)}</td>`;
                body.appendChild(tr);
            });
            document.getElementById('payCheckAll').checked = rows.length > 0;
        });
    document.getElementById('paymentModal').classList.remove('hide');
}

function togglePayCheckAll(cb) {
    document.querySelectorAll('.pay-receipt-check').forEach(c => { c.checked = cb.checked; });
}

function submitPayment() {
    const amount = parseFloat(document.getElementById('payAmount').value);
    if (!amount || amount <= 0) { showToast('请输入有效的支付金额', 'warning'); return; }

    const fd = new FormData();
    fd.append('supplier_id', String(finState.supplierId));
    fd.append('amount', String(amount));
    fd.append('method', document.getElementById('payMethod').value);
    const paidAt = document.getElementById('payDate').value;
    if (paidAt) fd.append('paid_at', paidAt);
    const notes = document.getElementById('payNotes').value.trim();
    if (notes) fd.append('notes', notes);
    document.querySelectorAll('.pay-receipt-check:checked').forEach(c => fd.append('receipt_ids', c.value));
    const voucherFile = document.getElementById('payVoucher').files[0];
    if (voucherFile) fd.append('voucher', voucherFile);

    fetch('/api/payments', { method: 'POST', body: fd })
        .then(res => res.json())
        .then(ret => {
            if (ret.status !== 'success') { showToast('登记支付失败' + toastFailDetail(ret.msg), 'error'); return; }
            console.log('[payment] 登记支付成功', ret.msg);
            showToast('已登记支付', 'success');
            closeModalById('paymentModal');
            loadFinancePanel();
            loadReceiptsHistory();       // 刷新 payment_id 标记
            loadSupplierAdmin();         // 未付合计联动
        })
        .catch(err => { console.error('登记支付异常', err); showToast('登记支付失败' + toastFailDetail(err), 'error'); });
}

// ---- 发起对账 Modal ----
function openReconModal() {
    if (!finState.supplierId) { showToast('请先选择供应商', 'warning'); return; }
    const sup = finState.suppliers.find(s => s.id === finState.supplierId);
    document.getElementById('reconSupplierHint').innerText = `供应商：${sup.name}`;
    document.getElementById('reconPeriodType').value = 'month';
    document.getElementById('reconStart').value = '';
    document.getElementById('reconEnd').value = '';
    document.getElementById('reconCreateModal').classList.remove('hide');
}

function reconPeriodTypeChanged() {
    // 周期变化时清空手工日期，避免残留误导（留空即走缺省）
    document.getElementById('reconStart').value = '';
    document.getElementById('reconEnd').value = '';
}

function submitReconCreate() {
    const payload = {
        supplier_id: finState.supplierId,
        period_type: document.getElementById('reconPeriodType').value,
    };
    const start = document.getElementById('reconStart').value;
    const end = document.getElementById('reconEnd').value;
    if (start) payload.start = start;
    if (end) payload.end = end;

    fetch('/api/reconciliation', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    })
        .then(res => res.json())
        .then(ret => {
            if (ret.status !== 'success') { showToast('发起对账失败' + toastFailDetail(ret.msg), 'error'); return; }
            console.log('[recon] 发起对账成功', ret.msg);
            showToast('已发起对账', 'success', TOAST_DURATION.long);
            closeModalById('reconCreateModal');
            const taskId = ret.task_id;
            loadFinancePanel();
            setTimeout(() => viewReconTask(taskId), 400);
        })
        .catch(err => { console.error('发起对账异常', err); showToast('发起对账失败' + toastFailDetail(err), 'error'); });
}

// ---- 对账差异明细 ----
function viewReconTask(taskId) {
    fetch(`/api/reconciliation/${taskId}`)
        .then(res => res.json())
        .then(ret => {
            if (ret.status !== 'success') { showToast('加载对账详情失败' + toastFailDetail(ret.msg), 'error'); return; }
            finState.currentTaskId = taskId;
            finState.currentDetail = ret;
            renderReconDetail();
        });
}

function renderReconDetail() {
    const detail = finState.currentDetail;
    const area = document.getElementById('reconDetailArea');
    if (!detail) { area.classList.add('hide'); return; }
    area.classList.remove('hide');

    const t = detail.task;
    const [cls, label] = RECON_STATUS_BADGES[t.status] || ['badge-secondary', t.status];
    document.getElementById('reconDetailTitle').innerHTML =
        `任务 #${t.id}｜${w2Escape(t.period_start)} ~ ${w2Escape(t.period_end)}｜` +
        `<span class="badge ${cls}">${label}</span>｜账单侧金额 $${fmtMoney(t.summary.statement_amount)}、` +
        `餐厅侧未入账单 $${fmtMoney(t.summary.restaurant_only_amount)}`;

    const locked = t.status === 'confirmed';
    document.getElementById('btnConfirmRecon').classList.toggle('hide', locked);

    const body = document.getElementById('reconLineBody');
    body.innerHTML = '';
    detail.lines.forEach(ln => {
        const [mCls, mLabel] = MATCH_BADGES[ln.match_status] || ['badge-secondary', ln.match_status];
        const sideLabel = ln.side === 'statement'
            ? '<span class="badge badge-secondary">账单行</span>'
            : '<span class="badge badge-warning">餐厅赊单</span>';
        const matchedDesc = ln.matched_receipt && ln.side === 'statement'
            ? ` ↔ 赊单 #${ln.matched_receipt_id}` : '';

        let actionHtml;
        if (locked) {
            actionHtml = '<span style="color:var(--text-muted); font-size:0.78rem;"> 已锁定</span>';
        } else if (ln.resolved) {
            actionHtml = '<span class="badge badge-success">已处理</span>';
        } else {
            actionHtml = `
                <button class="btn btn-success" style="padding:2px 8px; font-size:0.75rem; margin:1px;"
                    onclick="resolveLine(${ln.id}, 'confirm_match')">确认匹配</button>
                <button class="btn btn-secondary" style="padding:2px 8px; font-size:0.75rem; margin:1px;"
                    onclick="resolveLine(${ln.id}, 'write_off')">核销</button>
                <button class="btn btn-danger" style="padding:2px 8px; font-size:0.75rem; margin:1px;"
                    onclick="resolveLine(${ln.id}, 'dispute')">争议</button>`;
        }

        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>${sideLabel}</td>
            <td>${renderDateCell(ln.line_date)}</td>
            <td>${w2Escape(ln.docket_no || '-')}${matchedDesc}</td>
            <td>$${fmtMoney(ln.amount)}</td>
            <td><span class="badge ${mCls}">${mLabel}</span></td>
            <td style="font-size:0.8rem; color:var(--text-muted);">${w2Escape(ln.resolution_note || '-')}</td>
            <td style="white-space:nowrap;">${actionHtml}</td>`;
        body.appendChild(tr);
    });
}

function resolveLine(lineId, action) {
    const detail = finState.currentDetail;
    if (!detail) return;
    const line = detail.lines.find(l => l.id === lineId);
    if (!line) return;

    const titles = { confirm_match: ' 确认匹配', write_off: ' 核销差异', dispute: '[标记] 标记争议' };
    document.getElementById('reconResolveTitle').innerText = titles[action] || '处理对账差异';
    const esc = s => w2Escape(s == null ? '' : String(s));
    const sideDesc = line.side === 'statement'
        ? `账单行：${renderDateCell(line.line_date)}｜单号 ${esc(line.docket_no)}｜金额 $${fmtMoney(line.amount)}`
        : `餐厅留底赊单 #${esc(line.matched_receipt_id)}｜${renderDateCell(line.line_date)}｜金额 $${fmtMoney(line.amount)}`;
    document.getElementById('reconResolveDesc').innerHTML =
        sideDesc + '<br>当前状态：' + esc(line.match_status_label || line.match_status);
    document.getElementById('reconResolveNote').value = '';

    const grp = document.getElementById('reconResolveReceiptGroup');
    if (action === 'confirm_match') {
        grp.classList.remove('hide');
        const sel = document.getElementById('reconResolveReceipt');
        sel.innerHTML = '';
        // 候选 = 期间内尚未匹配的赊单 + 本行已有候选
        const candidates = (detail.restaurant_receipts || []).filter(r =>
            !r.matched || r.id === line.matched_receipt_id);
        if (!candidates.length) {
            sel.innerHTML = '<option value="">期间内无可选赊单</option>';
        }
        candidates.forEach(r => {
            const opt = document.createElement('option');
            opt.value = String(r.id);
            opt.textContent = `赊单 #${r.id}｜${formatMonthDay(r.receipt_date)}｜$${fmtMoney(r.total_amount)}`;
            sel.appendChild(opt);
        });
        if (line.matched_receipt_id) sel.value = String(line.matched_receipt_id);
    } else {
        grp.classList.add('hide');
    }

    finState.resolveCtx = { lineId, action };
    document.getElementById('reconResolveModal').classList.remove('hide');
}

function submitReconResolve() {
    const ctx = finState.resolveCtx;
    if (!ctx) return;
    const payload = { line_id: ctx.lineId, action: ctx.action };
    const note = document.getElementById('reconResolveNote').value.trim();
    if (note) payload.note = note;
    if (ctx.action === 'confirm_match') {
        const rid = document.getElementById('reconResolveReceipt').value;
        if (!rid) { showToast('请选择要匹配的赊单', 'warning'); return; }
        payload.receipt_id = Number(rid);
    }

    fetch(`/api/reconciliation/${finState.currentTaskId}/resolve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    })
        .then(res => res.json())
        .then(ret => {
            if (ret.status !== 'success') { showToast('处理差异行失败' + toastFailDetail(ret.msg), 'error'); return; }
            console.log('[recon] 差异行处理成功', ret.msg);
            showToast('已处理该差异行', 'success', TOAST_DURATION.long);
            closeModalById('reconResolveModal');
            loadFinancePanel();
            viewReconTask(finState.currentTaskId);
        })
        .catch(err => { console.error('对账处理异常', err); showToast('处理失败' + toastFailDetail(err), 'error'); });
}

function confirmReconTask() {
    const taskId = finState.currentTaskId;
    if (!taskId) return;
    if (!confirm(`确认对账任务 #${taskId} 完成？\n确认后该期间将锁定，差异行保留在履历中；后续如需改动单据请走冲销路径。`)) return;

    fetch(`/api/reconciliation/${taskId}/confirm`, { method: 'POST' })
        .then(res => res.json())
        .then(ret => {
            if (ret.status !== 'success') { showToast('确认对账失败' + toastFailDetail(ret.msg), 'error'); return; }
            console.log('[recon] 对账确认成功', ret.msg);
            showToast('已确认对账完成', 'success', TOAST_DURATION.long);
            loadFinancePanel();
            viewReconTask(taskId);
        })
        .catch(err => { console.error('确认对账异常', err); showToast('确认失败' + toastFailDetail(err), 'error'); });
}

// ---- 供应商档案与归档工作台（09 方案：M1/M2/M3/M4/M6/M7 + S1/S2/S4） ----
const SupplierUI = { q: '', includeInactive: false };
const ArchiveUI = { q: '', statusGroup: '', quickRange: '' };

// 状态聚合映射（UI 口语，D-SUP-10 / 4.3）
const STATUS_GROUP_MAP = {
    pending: ['uploaded', 'parsing', 'parsed', 'edited'],
    posted:  ['approved'],
    problem: ['flagged'],
    failed:  ['error'],
};

function loadSupplierAdmin() {
    // 与库存档案对称：从 checkbox 同步 includeInactive，避免「显示已停用」勾了却不带 query
    const chkInc = document.getElementById('supShowInactive');
    if (chkInc) SupplierUI.includeInactive = !!chkInc.checked;
    const params = new URLSearchParams();
    if (SupplierUI.q) params.set('q', SupplierUI.q);
    if (SupplierUI.includeInactive) params.set('include_inactive', '1');
    fetch('/api/suppliers?' + params.toString())
        .then(res => res.json())
        .then(ret => {
            if (ret.status !== 'success') return;
            window.__supplierCache = ret.data || [];
            renderSupplierAdminRows(window.__supplierCache);
            updateTodoBar();
        });
}

function renderSupplierAdminRows(suppliers) {
    const body = document.getElementById('supplierAdminBody');
    const empty = document.getElementById('supplierEmpty');
    if (!body) return;
    body.innerHTML = '';
    if (suppliers.length === 0) {
        if (empty) empty.classList.remove('hide');
        return;
    }
    if (empty) empty.classList.add('hide');

    suppliers.forEach(s => {
        const active = (s.active === 1 || s.active === undefined) ? 1 : 0;
        const statusBadge = active
            ? '<span class="badge badge-success">启用</span>'
            : '<span class="badge badge-secondary">已停用</span>';
        const overdueBadge = (s.is_overdue && active)
            ? ' <span class="badge badge-danger">逾期</span>' : '';

        const termsText = (s.payment_terms_days != null && s.payment_terms_days !== '')
            ? `${s.payment_terms_days} 天` : '现结';

        const prefText = ({cash:'现结', credit:'赊账', mixed:'混合'})[s.settlement_pref] || '';

        const tr = document.createElement('tr');
        if (!active) tr.className = 'sup-row-inactive';
        tr.innerHTML = `
            <td>
                <div style="font-weight:600;">${w2Escape(s.name)}</div>
                <div class="sup-code-weak">${w2Escape(s.supplier_code || '-')}${prefText ? ' · ' + w2Escape(prefText) : ''}</div>
            </td>
            <td class="col-center">${w2Escape(termsText)}</td>
            <td class="col-center">${Number(s.receipt_count) || 0}</td>
            <td>
                <div class="supplier-unpaid-stack">
                    <span class="supplier-unpaid-amount ${s.unpaid_credit_total > 0 ? 'is-unpaid' : 'is-paid'}">$${fmtMoney(s.unpaid_credit_total)}</span>
                    <span class="supplier-unpaid-count">${s.unpaid_credit_count} 张${overdueBadge}</span>
                </div>
            </td>
            <td>${w2Escape(s.contact_phone || '-')}</td>
            <td class="col-center">${statusBadge}</td>
            <td class="col-center">
                <button class="btn btn-secondary" style="padding:3px 8px; font-size:0.78rem;" onclick="viewSupplierReceipts(${Number(s.id)})">查看单据</button>
                <button class="btn btn-secondary" style="padding:3px 8px; font-size:0.78rem;" onclick="openSupplierModal(${Number(s.id)})">编辑</button>
                <button class="btn btn-secondary" style="padding:3px 8px; font-size:0.78rem;" onclick="event.stopPropagation(); toggleSupplierMore(this, ${Number(s.id)}, ${active})">更多 ▾</button>
            </td>`;
        body.appendChild(tr);
    });
}

function onSupplierSearchInput() {
    SupplierUI.q = (document.getElementById('supSearch')?.value || '').trim();
    loadSupplierAdmin();
}

// ---- 新增 / 编辑 Modal ----
let _editSupplierId = null;

function openSupplierModal(id) {
    _editSupplierId = (id != null && id !== undefined) ? Number(id) : null;
    const title = document.getElementById('supplierModalTitle');
    const errBox = document.getElementById('supplierModalError');
    if (errBox) errBox.classList.add('hide');
    ['supName', 'supPhone', 'supNotes', 'supPref', 'supTerms'].forEach(id => document.getElementById(id).value = '');
    document.getElementById('supCodePreview').innerHTML = '新增后自动生成';
    setSupplierTerms('30'); // 默认 30 天（与 upsert 一致）

    if (_editSupplierId != null) {
        const sup = (window.__supplierCache || []).find(s => s.id === _editSupplierId);
        if (sup) {
            title.textContent = '编辑供应商';
            document.getElementById('supName').value = sup.name || '';
            document.getElementById('supPhone').value = sup.contact_phone || '';
            document.getElementById('supNotes').value = sup.notes || '';
            document.getElementById('supPref').value = sup.settlement_pref || '';
            const t = (sup.payment_terms_days != null) ? String(sup.payment_terms_days) : '';
            document.getElementById('supTerms').value = t;
            setSupplierTermsActive(t);
            document.getElementById('supCodePreview').textContent = sup.supplier_code || '-';
        }
    } else {
        title.textContent = '新增供应商';
    }
    document.getElementById('supplierModal').classList.remove('hide');
    setTimeout(() => document.getElementById('supName').focus(), 50);
}

function setSupplierTerms(val) {
    const input = document.getElementById('supTerms');
    if (input) input.value = (val === '' || val == null) ? '' : String(val);
    setSupplierTermsActive(val);
}

function setSupplierTermsActive(val) {
    document.querySelectorAll('#supplierModal [data-terms]').forEach(c => {
        c.classList.toggle('active', c.getAttribute('data-terms') === String(val));
    });
}

function saveSupplier() {
    const name = (document.getElementById('supName').value || '').trim();
    const phone = (document.getElementById('supPhone').value || '').trim();
    const notes = (document.getElementById('supNotes').value || '').trim();
    const pref = document.getElementById('supPref').value || '';
    const termsRaw = (document.getElementById('supTerms').value || '').trim();
    const errBox = document.getElementById('supplierModalError');

    if (!name) {
        if (errBox) { errBox.textContent = '供应商名称必填'; errBox.classList.remove('hide'); }
        return;
    }
    let terms = null;
    if (termsRaw !== '') {
        const n = Number(termsRaw);
        if (!Number.isInteger(n) || n < 0) {
            if (errBox) { errBox.textContent = '账期必须为非负整数'; errBox.classList.remove('hide'); }
            return;
        }
        terms = n;
    }

    const payload = { name, contact_phone: phone || null, notes: notes || null,
                       settlement_pref: pref || null, payment_terms_days: terms };

    const isEdit = _editSupplierId != null;
    const origSup = isEdit ? (window.__supplierCache || []).find(s => s.id === _editSupplierId) : null;
    const origTerms = origSup ? origSup.payment_terms_days : undefined;
    const termsChanged = isEdit && ((origTerms ?? null) !== (terms ?? null));
    const url = isEdit ? `/api/suppliers/${_editSupplierId}` : '/api/suppliers';
    const method = isEdit ? 'PATCH' : 'POST';

    fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    })
        .then(res => res.json().then(d => ({ status: res.status, body: d })))
        .then(({ status, body }) => {
            if (status === 409) {
                if (errBox) { errBox.textContent = (body.msg || '已存在同名供应商') ; errBox.classList.remove('hide'); }
                return;
            }
            if (status === 400) {
                if (errBox) { errBox.textContent = body.msg || '参数有误'; errBox.classList.remove('hide'); }
                return;
            }
            if (!body || body.status !== 'success') {
                if (errBox) { errBox.textContent = (body && body.msg) || '保存失败'; errBox.classList.remove('hide'); }
                return;
            }
            let msg = isEdit ? '供应商已更新' : `供应商「${name}」已创建`;
            if (termsChanged) msg += '；账期仅影响之后的新单，不回溯旧单预期付款日';
            showToast(msg, 'success');
            closeModalById('supplierModal');
            loadSupplierAdmin();
            loadSuppliersData();   // 刷新复核 combobox
            loadReceiptsHistory(); // 表头供应商名联动
        })
        .catch(err => {
            console.error('保存供应商异常', err);
            if (errBox) { errBox.textContent = '保存失败，请稍后重试'; errBox.classList.remove('hide'); }
        });
}

function deactivateSupplier(id) {
    closeSupplierMore();
    if (!confirm('停用该供应商？\n停用后新单不会自动挂到此供应商；历史单据仍保留，可在「显示已停用」中查看。')) return;
    patchSupplierActive(id, 0);
}

function activateSupplier(id) {
    closeSupplierMore();
    patchSupplierActive(id, 1);
}

function patchSupplierActive(id, active) {
    fetch(`/api/suppliers/${Number(id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ active }),
    })
        .then(res => res.json())
        .then(ret => {
            if (!ret || ret.status !== 'success') { showToast((ret && ret.msg) || '操作失败', 'error'); return; }
            showToast(active ? '已启用供应商' : '已停用供应商', 'success');
            loadSupplierAdmin();
            loadSuppliersData();
            loadReceiptsHistory();
        })
        .catch(err => { console.error('供应商启停异常', err); showToast('操作失败' + toastFailDetail(err), 'error'); });
}

// ---- 更多菜单（停用/启用/合并，D-SUP-12） ----
let _supMoreMenuEl = null;

function toggleSupplierMore(btn, id, active) {
    if (_supMoreMenuEl) { closeSupplierMore(); return; }
    const menu = document.createElement('div');
    menu.id = 'supMoreMenu';
    menu.style.cssText = 'position:fixed; z-index:1200; background:#fff; border:1px solid var(--border-color,#d8dce0);'
        + ' border-radius:8px; box-shadow:0 6px 20px rgba(0,0,0,.15); min-width:140px; padding:6px;';
    const items = [];
    if (active === 1) {
        items.push(`<div class="sup-more-item" onclick="deactivateSupplier(${Number(id)})">停用</div>`);
    } else {
        items.push(`<div class="sup-more-item" onclick="activateSupplier(${Number(id)})">启用</div>`);
    }
    items.push(`<div class="sup-more-item" onclick="openMergeModal(${Number(id)})">合并…</div>`);
    menu.innerHTML = items.join('');
    document.body.appendChild(menu);
    const rect = btn.getBoundingClientRect();
    menu.style.top = (rect.bottom + 4) + 'px';
    menu.style.left = Math.max(4, rect.right - menu.offsetWidth) + 'px';
    menu.querySelectorAll('.sup-more-item').forEach(el => {
        el.style.cssText = 'padding:7px 10px; font-size:0.82rem; cursor:pointer; border-radius:6px;';
        el.onmouseenter = () => el.style.background = 'var(--hover-bg,#f1f5f9)';
        el.onmouseleave = () => el.style.background = 'transparent';
    });
    _supMoreMenuEl = menu;
    setTimeout(() => document.addEventListener('click', closeSupplierMoreOutside, true), 0);
}

function closeSupplierMoreOutside(e) {
    if (_supMoreMenuEl && !_supMoreMenuEl.contains(e.target)) closeSupplierMore();
}

function closeSupplierMore() {
    if (_supMoreMenuEl) { _supMoreMenuEl.remove(); _supMoreMenuEl = null; }
    document.removeEventListener('click', closeSupplierMoreOutside, true);
}

// ---- 查看单据：一键筛选该供应商并滚动（D-SUP-9） ----
function viewSupplierReceipts(id) {
    const sup = (window.__supplierCache || []).find(s => s.id === Number(id));
    const name = sup ? sup.name : '';
    const sel = document.getElementById('fltSupplier');
    if (sel) {
        // 确保选项存在（无单据的供应商也可能不在下拉里）
        let opt = Array.from(sel.options).find(o => o.value === name);
        if (!opt && name) {
            opt = document.createElement('option');
            opt.value = name; opt.textContent = name;
            sel.appendChild(opt);
        }
        sel.value = name;
    }
    // 清空其它归档筛选，只看该家
    document.getElementById('fltKeyword').value = '';
    ArchiveUI.q = '';
    setArchiveStatusGroup('');
    setArchiveQuickRange('');
    applyArchiveFilters();
    const arcCard = document.getElementById('archiveRecordCount');
    if (arcCard) arcCard.closest('.card').scrollIntoView({ behavior: 'smooth', block: 'start' });
    if (name) showToast(`已筛选「${name}」的单据`, 'info');
}

// ---- 待办条（D-SUP-10 / M7） ----
function updateTodoBar() {
    const recs = allArchiveReceipts || [];
    let pending = 0, failed = 0, overdue = 0;
    recs.forEach(r => {
        const st = r.status || 'uploaded';
        if (['uploaded', 'parsing', 'parsed', 'edited'].includes(st)) pending++;
        else if (st === 'error') failed++;
        if (r.payment_status === 'overdue') overdue++;
    });
    setText('todoPendingNum', pending);
    setText('todoFailedNum', failed);
    setText('todoOverdueNum', overdue);
}

function todoFilter(type) {
    if (type === 'overdue') {
        const sel = document.getElementById('fltFinDue');
        if (sel) { sel.value = 'overdue'; if (typeof applyPayablesFilters === 'function') applyPayablesFilters(); }
        const card = sel ? sel.closest('.card') : null;
        if (card) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
        return;
    }
    setArchiveStatusGroup(type === 'failed' ? 'failed' : 'pending');
    const arc = document.getElementById('archiveRecordCount');
    if (arc) arc.closest('.card').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ---- 归档关键字 / 状态聚合 / 时间快捷（M5 / S2 / S4） ----
function onArchiveKeywordInput() {
    ArchiveUI.q = (document.getElementById('fltKeyword')?.value || '').trim();
    applyArchiveFilters();
}

function setArchiveStatusGroup(g) {
    ArchiveUI.statusGroup = g;
    document.querySelectorAll('[data-group]').forEach(c =>
        c.classList.toggle('active', c.getAttribute('data-group') === g));
    applyArchiveFilters();
}

function setArchiveQuickRange(r) {
    ArchiveUI.quickRange = r;
    document.querySelectorAll('[data-range]').forEach(c =>
        c.classList.toggle('active', c.getAttribute('data-range') === r));
    applyQuickRange(r);
    applyArchiveFilters();
}

function applyQuickRange(r) {
    const from = document.getElementById('fltReceiptDateFrom');
    const to = document.getElementById('fltReceiptDateTo');
    if (!from || !to) return;
    const today = getTodayDateStr();
    if (!r) { from.value = ''; to.value = ''; return; }
    const d = new Date();
    let start;
    if (r === 'week') {
        const diff = (d.getDay() + 6) % 7; // 周一为一周起点
        start = new Date(d); start.setDate(d.getDate() - diff);
    } else if (r === 'month') {
        start = new Date(d.getFullYear(), d.getMonth(), 1);
    } else if (r === 'last_month') {
        const s = new Date(d.getFullYear(), d.getMonth() - 1, 1);
        const e = new Date(d.getFullYear(), d.getMonth(), 0);
        from.value = fmtDateLocal(s); to.value = fmtDateLocal(e);
        return;
    }
    from.value = fmtDateLocal(start); to.value = today;
}

function fmtDateLocal(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
}

function toggleArchiveAdvanced() {
    const el = document.getElementById('archiveAdvanced');
    const toggle = document.getElementById('archiveAdvancedToggle');
    if (!el) return;
    const hidden = el.classList.toggle('hide');
    if (toggle) toggle.textContent = hidden ? '高级筛选 ▾' : '高级筛选 ▴';
}

function setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
}

let mergeDropId = null;

function openMergeModal(dropId) {
    mergeDropId = dropId;
    fetch('/api/suppliers')
        .then(res => res.json())
        .then(ret => {
            if (ret.status !== 'success') return;
            const suppliers = ret.data || [];
            const drop = suppliers.find(s => s.id === dropId);
            if (!drop) { showToast('供应商不存在', 'error'); return; }

            document.getElementById('mergeDropHint').innerHTML =
                `被并供应商：<strong>${w2Escape(drop.name)}</strong>` +
                ` <span class="badge badge-secondary" style="font-family:monospace;">${w2Escape(drop.supplier_code || '-')}</span>` +
                `｜单据 ${drop.receipt_count} 张｜未付 $${fmtMoney(drop.unpaid_credit_total)}`;

            const sel = document.getElementById('mergeKeepSelect');
            sel.innerHTML = '';
            suppliers.filter(s => s.id !== dropId).forEach(s => {
                const opt = document.createElement('option');
                opt.value = String(s.id);
                opt.textContent = `${s.name} [${s.supplier_code || '-'}]`;
                sel.appendChild(opt);
            });
            document.getElementById('mergeConfirmCheck').checked = false;
            document.getElementById('mergeModal').classList.remove('hide');
        });
}

function submitMerge() {
    const keepId = Number(document.getElementById('mergeKeepSelect').value);
    if (!mergeDropId || !keepId) { showToast('请选择保留的供应商', 'warning'); return; }
    if (!document.getElementById('mergeConfirmCheck').checked) {
        showToast('请先勾选确认框，确认理解合并影响', 'warning');
        return;
    }
    const drop = (finState.suppliers || []).find(s => s.id === mergeDropId);
    const keep = (finState.suppliers || []).find(s => s.id === keepId);
    if (!confirm(`最后确认：把 [${drop ? drop.name : mergeDropId}] 并入 [${keep ? keep.name : keepId}]？\n` +
        '被并供应商的全部单据/别称/记忆/支付/对账任务将迁移，其档案将被永久删除。')) return;

    fetch('/api/suppliers/merge', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ keep_id: keepId, drop_id: mergeDropId }),
    })
        .then(res => res.json())
        .then(ret => {
            if (ret.status !== 'success') { showToast('合并供应商失败' + toastFailDetail(ret.msg), 'error'); return; }
            console.log('[supplier] 合并成功', ret.msg);
            showToast('已合并供应商', 'success', TOAST_DURATION.long);
            closeModalById('mergeModal');
            mergeDropId = null;
            loadSuppliersData();
            loadReceiptsHistory();
            loadSupplierAdmin();
            loadFinancePanel();
        })
        .catch(err => { console.error('合并供应商异常', err); showToast('合并失败' + toastFailDetail(err), 'error'); });
}

// ---- 盘点调整（D15/M5） ----
let stocktakeSkuId = null;

function openStocktakeModal(skuId, skuName, currentStock, baseUnit) {
    stocktakeSkuId = skuId;
    document.getElementById('stocktakeHint').innerHTML =
        `食材：<strong>${w2Escape(skuName)}</strong>｜当前账面 ` +
        `<strong>${Number(currentStock || 0)}</strong> ${w2Escape(baseUnit || '')}`;
    document.getElementById('stocktakeQty').value = currentStock ?? '';
    document.getElementById('stocktakeNote').value = '';
    document.getElementById('stocktakeModal').classList.remove('hide');
}

function submitStocktake() {
    if (!stocktakeSkuId) return;
    const qtyRaw = document.getElementById('stocktakeQty').value;
    const actualQty = parseFloat(qtyRaw);
    if (qtyRaw === '' || isNaN(actualQty) || actualQty < 0) {
        showToast('请输入有效的实盘数量', 'warning');
        return;
    }
    const payload = { actual_qty: actualQty };
    const note = document.getElementById('stocktakeNote').value.trim();
    if (note) payload.note = note;

    fetch(`/api/inventory/${stocktakeSkuId}/stocktake`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    })
        .then(res => res.json())
        .then(ret => {
            if (ret.status !== 'success') { showToast('盘点调整失败' + toastFailDetail(ret.msg), 'error'); return; }
            console.log('[inventory] 盘点成功', ret.msg);
            showToast('已完成盘点调整', 'success');
            closeModalById('stocktakeModal');
            stocktakeSkuId = null;
            loadInventoryData();
        })
        .catch(err => { console.error('盘点异常', err); showToast('盘点失败' + toastFailDetail(err), 'error'); });
}

// ---- W4 出库：消耗 / 损耗（B-30/B-31/B-32）合并为统一出库 ----
let outboundSkuId = null;
let outboundKind = null; // 'consume' | 'waste'

const OUTBOUND_META = {
    consume: {
        title: '出库登记 - 日常用量',
        hint: '登记厨房或营业日常使用量，同步扣减账面库存。',
        path: 'consume',
        btn: '确认出库（用量）',
    },
    waste: {
        title: '出库登记 - 报损耗',
        hint: '登记食材变质或损坏等损耗，同步扣减账面库存。',
        path: 'waste',
        btn: '确认出库（损耗）',
    },
};

function onOutboundKindChange() {
    const sel = document.getElementById('outboundKind');
    if (!sel) return;
    const kind = sel.value;
    outboundKind = kind;
    const meta = OUTBOUND_META[kind];
    if (!meta) return;
    const hintEl = document.getElementById('outboundKindHint');
    if (hintEl) hintEl.textContent = meta.hint;
    const btn = document.getElementById('outboundSubmitBtn');
    if (btn) btn.textContent = meta.btn;
}

function openOutboundModal(skuId, skuName, currentStock, baseUnit, kind) {
    // kind 可选：未传或非法时默认 consume，后续可通过下拉切换
    let initKind = kind && OUTBOUND_META[kind] ? kind : 'consume';
    outboundSkuId = skuId;
    outboundKind = initKind;
    const sel = document.getElementById('outboundKind');
    if (sel) sel.value = initKind;
    const meta = OUTBOUND_META[initKind];
    document.getElementById('outboundModalTitle').textContent = '出库登记';
    document.getElementById('outboundHint').innerHTML =
        `食材：<strong>${w2Escape(skuName)}</strong>｜当前账面 ` +
        `<strong>${Number(currentStock || 0)}</strong> ${w2Escape(baseUnit || '')}`;
    const hintEl = document.getElementById('outboundKindHint');
    if (hintEl) hintEl.textContent = meta.hint;
    document.getElementById('outboundQty').value = '';
    document.getElementById('outboundNote').value = '';
    document.getElementById('outboundSubmitBtn').textContent = meta.btn;
    document.getElementById('outboundModal').classList.remove('hide');
}

function submitOutbound() {
    if (!outboundSkuId) return;
    // 以下拉当前值为准，兼容旧调用仍传 kind 的场景
    const sel = document.getElementById('outboundKind');
    const kind = sel ? sel.value : outboundKind;
    if (!kind || !OUTBOUND_META[kind]) return;
    outboundKind = kind;
    const meta = OUTBOUND_META[kind];
    const qtyRaw = document.getElementById('outboundQty').value;
    const qty = parseFloat(qtyRaw);
    if (qtyRaw === '' || isNaN(qty) || qty <= 0) {
        showToast('请输入有效的出库数量', 'warning');
        return;
    }
    const payload = { quantity: qty };
    const notes = document.getElementById('outboundNote').value.trim();
    if (notes) payload.notes = notes;

    fetch(`/api/inventory/${outboundSkuId}/${meta.path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    })
        .then(res => res.json().then(body => ({ httpStatus: res.status, body })))
        .then(({ httpStatus, body }) => {
            if (!body || body.status !== 'success') {
                console.error('[inventory] 出库失败', httpStatus, body);
                showToast(`${meta.title}失败，请稍后重试`, 'error');
                return;
            }
            console.log('[inventory] 出库成功', body.msg);
            showToast(`已完成${meta.title}，账面库存已扣减`, 'success');
            closeModalById('outboundModal');
            outboundSkuId = null;
            outboundKind = null;
            loadInventoryData();
        })
        .catch(err => { console.error('出库异常', err); showToast('出库失败' + toastFailDetail(err), 'error'); });
}

// -------------------------------------------------------------
// demo：无密码 RBAC 角色切换（admin/owner/staff）
// 角色存 localStorage('demo_role')，随所有 /api/ 请求带 X-Role 头；
// 切换后刷新数据，让前端角色控制（isOwnerRole）与后端权限即时生效。
// -------------------------------------------------------------
function initDemoRoleSwitch() {
    const sel = document.getElementById('demoRoleSelect');
    if (!sel) return;
    const saved = (() => { try { return localStorage.getItem('demo_role'); } catch (e) { return null; } })();
    const initRole = (saved === 'admin' || saved === 'owner' || saved === 'staff') ? saved : 'admin';
    sel.value = initRole;
    try { localStorage.setItem('demo_role', initRole); } catch (e) { /* ignore */ }
    applyDemoRoleColor(initRole);
    sel.addEventListener('change', () => {
        const role = sel.value;
        try { localStorage.setItem('demo_role', role); } catch (e) { /* ignore */ }
        applyDemoRoleColor(role);
        showToast('角色已切换：' + role, 'info');
        // 重探 /me 刷新 AuthState.account → isOwnerRole() 生效
        probeAuthAndEnter(true);
        location.reload();
    });
}

// demo：角色 → 强调色（下拉 + 侧边栏品牌条随角色变色，一眼可见当前角色）
const DEMO_ROLE_COLORS = {
    admin: '#d80d0d',   // 红：超管
    owner: '#2f6b4f',   // 绿：老板
    staff: '#1565c0',   // 蓝：店员
};

function applyDemoRoleColor(role) {
    const color = DEMO_ROLE_COLORS[role] || DEMO_ROLE_COLORS.admin;
    const sel = document.getElementById('demoRoleSelect');
    if (sel) {
        sel.style.borderColor = color;
        sel.style.color = color;
        sel.style.fontWeight = '600';
    }
    const brand = document.querySelector('.sidebar-header .brand');
    if (brand) brand.style.borderLeft = '4px solid ' + color;
    document.documentElement.style.setProperty('--demo-role-accent', color);
    applyRoleVisibility(role);
}

// U-01：按角色控制 admin 专属导航入口可见性（API 层 owner/staff 均 403，前端同步隐藏）
// 店员隐藏部门花销报表（cost_report 为 owner 域接口，避免进入即 403 弹窗）
function applyRoleVisibility(role) {
    const isAdmin = role === 'admin';
    ['adminEngineBtn', 'goldenBoardBtn'].forEach(id => {
        const btn = document.getElementById(id);
        if (btn) btn.style.display = isAdmin ? '' : 'none';
    });
    const reportBtn = document.querySelector('.sidebar-btn[data-target="tab-report"]');
    if (reportBtn) reportBtn.style.display = (role === 'staff') ? 'none' : '';
    // 当前停留在已隐藏的页签时回落到收据识别（admin 不受影响）
    const hiddenTabs = (role === 'staff') ? ['tab-engine', 'tab-golden', 'tab-report']
                     : (role === 'owner') ? ['tab-engine', 'tab-golden'] : [];
    const active = document.querySelector('.tab-content.active');
    if (active && hiddenTabs.indexOf(active.id) !== -1) {
        const scanBtn = document.querySelector('.sidebar-btn[data-target="tab-scan"]');
        if (scanBtn) scanBtn.click();
    }
    applyModalRoleVisibility(role);
}

// U-6: 弹窗内老板级按钮的视觉级隐藏
// 针对 #archiveDetailModal，若当前角色为 staff（isStaffRoleNow() 或 role === 'staff'），
// 视觉上彻底隐藏「审核通过」(modalApproveBtn)、「标记为异常」(modalFlagBtn)、「成本分摊」(modalCostShareBtn / modalCostShareContainer)；
// 仅对 owner / admin 展现，杜绝店员误点击触发 403 越权。
function applyModalRoleVisibility(optionalRole) {
    const isStaff = optionalRole ? (optionalRole === 'staff') : isStaffRoleNow();
    const ownerElements = ['modalApproveBtn', 'modalFlagBtn', 'modalCostShareBtn', 'modalCostShareContainer'];
    ownerElements.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            if (isStaff) {
                el.classList.add('hide');
                el.style.display = 'none';
            } else {
                el.classList.remove('hide');
                el.style.display = '';
            }
        }
    });
}

// -------------------------------------------------------------
// admin：引擎配置 / 灰测管理界面
// -------------------------------------------------------------
const DEFAULT_ENGINE_MODELS = {
    opencode: [
        { value: 'opencode/mimo-v2.5-free', label: 'MiMo-V2.5 Free' },
        { value: 'opencode/longcat-2.0-free', label: 'LongCat-2.0 Free' },
        { value: 'opencode/deepseek-v4-flash-free', label: 'deepseek-v4-flash-free' },
        { value: 'opencode-go/deepseek-v4-flash', label: 'deepseek-v4-flash' },
        { value: 'opencode-go/glm-5v-turbo', label: 'glm-5v-turbo' },
    ],
    codebuddy: [
        { value: 'minimax-m3-pay', label: 'minimax-m3-pay' },
    ],
    openai: []
};

// 连通性测试通过的白名单模型库 (已验证通过的模型跳过重复测试)
function getVerifiedModels(engine) {
    try {
        const raw = localStorage.getItem('verified_models_' + engine);
        return raw ? JSON.parse(raw) : [];
    } catch (e) {
        return [];
    }
}

function markModelVerified(engine, modelName) {
    const trimmed = (modelName || '').trim();
    if (!trimmed) return;
    const verified = getVerifiedModels(engine);
    if (!verified.includes(trimmed)) {
        verified.push(trimmed);
        try {
            localStorage.setItem('verified_models_' + engine, JSON.stringify(verified));
        } catch (e) {}
    }
}

function isModelVerified(engine, modelName) {
    if (!modelName || !modelName.trim()) return true;
    const trimmed = modelName.trim();
    // 1. 系统内置默认模型默认视为已验证
    const defaults = (DEFAULT_ENGINE_MODELS[engine] || []).map(m => m.value);
    if (defaults.includes(trimmed)) return true;
    // 2. 之前测试通过并记录的模型
    const verified = getVerifiedModels(engine);
    return verified.includes(trimmed);
}

function getCustomModels(engine) {
    try {
        const raw = localStorage.getItem('custom_models_' + engine);
        return raw ? JSON.parse(raw) : [];
    } catch (e) {
        return [];
    }
}

function saveCustomModels(engine, list) {
    try {
        localStorage.setItem('custom_models_' + engine, JSON.stringify(list));
    } catch (e) {}
}

function addCustomModel(engine, modelName) {
    const trimmed = (modelName || '').trim();
    if (!trimmed) return false;
    const defaults = (DEFAULT_ENGINE_MODELS[engine] || []).map(m => m.value);
    if (defaults.includes(trimmed)) {
        return true;
    }
    const customs = getCustomModels(engine);
    if (!customs.includes(trimmed)) {
        customs.push(trimmed);
        saveCustomModels(engine, customs);
    }
    return true;
}

function removeCustomModel(engine, modelName) {
    let customs = getCustomModels(engine);
    customs = customs.filter(m => m !== modelName);
    saveCustomModels(engine, customs);
}

function fillModelOptions(selId, current, engine) {
    const sel = document.getElementById(selId);
    if (!sel) return;
    const eng = engine || 'opencode';
    if (eng === 'openai') {
        sel.innerHTML = '';
        return;
    }
    const defaultModels = DEFAULT_ENGINE_MODELS[eng] || [];
    const customModels = getCustomModels(eng);
    
    sel.innerHTML = '';
    // 1. 系统默认模型
    defaultModels.forEach((m) => {
        const opt = document.createElement('option');
        opt.value = m.value;
        opt.textContent = m.label;
        sel.appendChild(opt);
    });
    // 2. 用户自定义模型 (带 [自定义] 标记)
    customModels.forEach((m) => {
        const opt = document.createElement('option');
        opt.value = m;
        opt.textContent = m;
        opt.setAttribute('data-custom', 'true');
        sel.appendChild(opt);
    });
    // 3. 自定义输入入口
    const addOpt = document.createElement('option');
    addOpt.value = '__ADD_CUSTOM__';
    addOpt.textContent = '自定义输入模型...';
    sel.appendChild(addOpt);

    // 4. 若传入了 current：
    // 若在列表中直接选中；若不在（用户刚刚输入的新自定义模型），则作为临时选项插入并选中
    if (current) {
        sel.value = current;
        if (sel.value !== current) {
            const opt = document.createElement('option');
            opt.value = current;
            opt.textContent = current;
            opt.setAttribute('data-custom', 'true');
            sel.insertBefore(opt, addOpt);
            sel.value = current;
        }
    }
    if (!sel.value && sel.options.length > 0) {
        sel.selectedIndex = 0;
    }
    updateModelDeleteButtonVisibility(selId);
}

function updateModelDeleteButtonVisibility(selId) {
    const sel = document.getElementById(selId);
    if (!sel) return;
    const parentGroup = sel.closest('.form-group');
    if (!parentGroup) return;
    const delBtn = parentGroup.querySelector('.custom-del-btn');
    if (!delBtn) return;
    const selectedOpt = sel.options[sel.selectedIndex];
    const isCustom = selectedOpt && selectedOpt.getAttribute('data-custom') === 'true';
    delBtn.style.display = isCustom ? 'inline' : 'none';
}

function handleDeleteCurrentModel(selId, engineId) {
    const sel = document.getElementById(selId);
    const engineEl = document.getElementById(engineId);
    if (!sel || !engineEl) return;
    const val = sel.value;
    const engine = engineEl.value;
    if (!val) return;
    showCustomConfirmModal({
        title: '删除自定义模型',
        message: '确定要删除自定义模型 [' + val + '] 吗？系统默认模型不受影响。',
        confirmText: '确定删除',
        cancelText: '取消',
        onConfirm: () => {
            removeCustomModel(engine, val);
            showToast('已删除自定义模型：' + val, 'info');
            fillModelOptions(selId, null, engine);
            updateModelDeleteButtonVisibility(selId);
        }
    });
}

function handleModelSelectChange(selId, engineGetter) {
    const sel = document.getElementById(selId);
    if (!sel) return;
    const val = sel.value;
    const engine = typeof engineGetter === 'function' ? engineGetter() : engineGetter;
    if (val === '__ADD_CUSTOM__') {
        let exampleModel = 'provider/model-name';
        if (engine === 'opencode') {
            exampleModel = 'opencode-go/gpt-5.6-luna';
        } else if (engine === 'codebuddy') {
            exampleModel = 'hy3';
        }
        showCustomInputModal({
            title: '添加自定义模型',
            message: '请输入 ' + engine + ' 引擎的模型名称：\n参考格式案例：' + exampleModel,
            defaultValue: exampleModel,
            placeholder: exampleModel,
            onConfirm: (newModel) => {
                const trimmed = (newModel || '').trim();
                if (trimmed) {
                    // 仅填入当前下拉框作为临时选中值，不在此时写入 localStorage
                    fillModelOptions(selId, trimmed, engine);
                    showToast('已选择模型：' + trimmed + '（保存配置通过后将持久化保存）', 'info');
                } else {
                    fillModelOptions(selId, null, engine);
                }
                updateModelDeleteButtonVisibility(selId);
            },
            onCancel: () => {
                fillModelOptions(selId, null, engine);
                updateModelDeleteButtonVisibility(selId);
            }
        });
    }
    updateModelDeleteButtonVisibility(selId);
}

// 辅助函数：根据引擎是否为 openai，动态插入或移除临时 placeholder 选项并选中
function syncModelSelectOpenaiState(selId, isOpenai) {
    const sel = document.getElementById(selId);
    if (!sel) return;
    let ph = sel.querySelector('option[data-placeholder="openai"]');
    if (isOpenai) {
        if (!ph) {
            ph = document.createElement('option');
            ph.value = '';
            ph.setAttribute('data-placeholder', 'openai');
            ph.textContent = '— 无（使用下方 OpenAI 模型） —';
            sel.prepend(ph);
        }
        sel.value = '';
    } else {
        if (ph) {
            ph.remove();
            // 移除后若无选中，恢复首个有效模型
            if (!sel.value && sel.options.length > 0) {
                sel.selectedIndex = 0;
            }
        }
    }
}

// 预设 OpenAI 网关（选择后自动填入识别/审核参数）
const OPENAI_PRESETS = {
    agnes: {
        label: 'Agnes AI',
        base_url: 'https://apihub.agnes-ai.com/v1',
        api_key: 'sk-KnyyE7tPWC5VnZLnfSaeE5NdN5Mrymm5tjs8cdhSiLZAcoQl',
        rec_model: 'agnes-2.0-flash',
        aud_model: 'agnes-2.0-flash',
    },
    bailian: {
        label: '阿里云百炼 · DashScope',
        base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        api_key: 'sk-ws-H.EPEIEXY.YOjw.MEYCIQDD9x3fMZAG3kt9zbgY1_6_1cbr-zl7MKOghZiSpS79OgIhANDEX_nkT997LzjEvtPXhNemX3Gtax7zbDZUKf3-_-SI',
        rec_model: 'qwen3-vl-flash',
        aud_model: 'qwen3-vl-plus',
    },
    siliconflow: {
        label: 'SiliconFlow · 硅基流动',
        base_url: 'https://api.siliconflow.cn/v1',
        api_key: 'YOUR_SILICONFLOW_API_KEY',
        rec_model: 'Qwen/Qwen3-VL-8B-Instruct',
        aud_model: 'Qwen/Qwen3-VL-8B-Instruct',
    },
};

function applyBoxPreset(presetSelectId, baseUrlId, apiKeyId, modelId, isAud) {
    const sel = document.getElementById(presetSelectId);
    if (!sel) return;
    const p = OPENAI_PRESETS[sel.value];
    if (!p) return;
    const bEl = document.getElementById(baseUrlId);
    const kEl = document.getElementById(apiKeyId);
    const mEl = document.getElementById(modelId);
    if (bEl) bEl.value = p.base_url;
    if (kEl) kEl.value = p.api_key;
    if (mEl) mEl.value = isAud ? p.aud_model : p.rec_model;
    showToast('已填入预设：' + p.label, 'info');
}

let _adminEngineEventsBound = false;

function bindAdminEngineEventsOnce() {
    if (_adminEngineEventsBound) return;
    _adminEngineEventsBound = true;

    // 模型下拉变更（支持自定义添加）
    document.getElementById('adminRecognitionModel').addEventListener('change', () => handleModelSelectChange('adminRecognitionModel', () => document.getElementById('adminRecognitionEngine').value));
    document.getElementById('adminAuditModel').addEventListener('change', () => handleModelSelectChange('adminAuditModel', () => document.getElementById('adminAuditEngine').value));
    document.getElementById('adminParseModel').addEventListener('change', () => handleModelSelectChange('adminParseModel', () => document.getElementById('adminParseEngine').value));
    document.getElementById('adminGreyRecModel').addEventListener('change', () => handleModelSelectChange('adminGreyRecModel', () => document.getElementById('adminGreyRecEngine').value));
    document.getElementById('adminGreyAudModel').addEventListener('change', () => handleModelSelectChange('adminGreyAudModel', () => document.getElementById('adminGreyAudEngine').value));
    document.getElementById('adminGreyParseModel').addEventListener('change', () => handleModelSelectChange('adminGreyParseModel', () => document.getElementById('adminGreyParseEngine').value));

    // 审核开关控制审核引擎/模型置灰
    document.getElementById('adminAuditEnabled').addEventListener('change', updateAuditDisabledState);
    document.getElementById('adminGreyAuditEnabled').addEventListener('change', updateGreyAuditDisabledState);
    // 引擎选择 → OpenAI 参数区显隐（常规 + 灰测）
    document.getElementById('adminRecognitionEngine').addEventListener('change', updateOpenaiBoxes);
    document.getElementById('adminAuditEngine').addEventListener('change', updateOpenaiBoxes);
    document.getElementById('adminGreyRecEngine').addEventListener('change', updateGreyOpenaiBoxes);
    document.getElementById('adminGreyAudEngine').addEventListener('change', updateGreyOpenaiBoxes);
    // 各 OpenAI 参数区独立预设网关绑定
    [
        ['adminOpenaiRecPreset', 'adminOpenaiRecBaseUrl', 'adminOpenaiRecApiKey', 'adminOpenaiRecModel', false],
        ['adminOpenaiAudPreset', 'adminOpenaiAudBaseUrl', 'adminOpenaiAudApiKey', 'adminOpenaiAudModel', true],
        ['adminParseOpenaiPreset', 'adminParseOpenaiBaseUrl', 'adminParseOpenaiApiKey', 'adminParseOpenaiModel', false],
        ['adminGreyOpenaiRecPreset', 'adminGreyOpenaiRecBaseUrl', 'adminGreyOpenaiRecApiKey', 'adminGreyOpenaiRecModel', false],
        ['adminGreyOpenaiAudPreset', 'adminGreyOpenaiAudBaseUrl', 'adminGreyOpenaiAudApiKey', 'adminGreyOpenaiAudModel', true],
        ['adminGreyParseOpenaiPreset', 'adminGreyParseOpenaiBaseUrl', 'adminGreyParseOpenaiApiKey', 'adminGreyParseOpenaiModel', false]
    ].forEach(([presetId, bId, kId, mId, isAud]) => {
        const el = document.getElementById(presetId);
        if (el) {
            el.value = '';
            el.addEventListener('change', () => applyBoxPreset(presetId, bId, kId, mId, isAud));
        }
    });
    // 灰测停用 → 整块置灰
    document.getElementById('adminGreyEnabled').addEventListener('change', updateGreyDisabledState);
    // 解析 LLM 开关 → 置灰；引擎 → OpenAI 区显隐
    document.getElementById('adminParseEnabled').addEventListener('change', updateParseDisabledState);
    document.getElementById('adminParseEngine').addEventListener('change', updateParseOpenaiBox);
    document.getElementById('adminGreyParseEnabled').addEventListener('change', updateGreyParseDisabledState);
    document.getElementById('adminGreyParseEngine').addEventListener('change', updateGreyParseOpenaiBox);
}

function loadAdminEngineConfig() {
    // 仅 admin 可查看
    const role = (() => { try { return localStorage.getItem('demo_role'); } catch (e) { return null; } })();
    if (role !== 'admin') return;
    bindAdminEngineEventsOnce();

    fetch('/api/admin/engine-config')
        .then(res => res.json())
        .then(body => {
            if (!body || body.status !== 'success' || !body.data) {
                showToast('读取引擎配置失败', 'error');
                return;
            }
            const cfg = body.data;
            // 引擎类型
            document.getElementById('adminRecognitionEngine').value = cfg.recognition_engine || 'opencode';
            document.getElementById('adminAuditEngine').value = cfg.audit_engine || 'opencode';
            // 模型下拉（根据对应引擎渲染）
            fillModelOptions('adminRecognitionModel', cfg.recognition_model, cfg.recognition_engine || 'opencode');
            fillModelOptions('adminAuditModel', cfg.audit_model, cfg.audit_engine || 'opencode');
            document.getElementById('adminAuditEnabled').value = cfg.audit_enabled ? 'true' : 'false';
            // OpenAI 兼容参数（识别/审核各自独立）
            document.getElementById('adminOpenaiRecBaseUrl').value = cfg.openai_rec_base_url || '';
            document.getElementById('adminOpenaiRecApiKey').value = cfg.openai_rec_api_key || '';
            document.getElementById('adminOpenaiRecModel').value = cfg.openai_rec_model || '';
            document.getElementById('adminOpenaiAudBaseUrl').value = cfg.openai_aud_base_url || '';
            document.getElementById('adminOpenaiAudApiKey').value = cfg.openai_aud_api_key || '';
            document.getElementById('adminOpenaiAudModel').value = cfg.openai_aud_model || '';
            // 常规解析 LLM
            document.getElementById('adminParseEnabled').value = cfg.parse_llm_enabled ? 'true' : 'false';
            document.getElementById('adminParseEngine').value = cfg.parse_llm_engine || 'opencode';
            fillModelOptions('adminParseModel', cfg.parse_llm_model, cfg.parse_llm_engine || 'opencode');
            document.getElementById('adminParseOpenaiBaseUrl').value = cfg.openai_parse_base_url || '';
            document.getElementById('adminParseOpenaiApiKey').value = cfg.openai_parse_api_key || '';
            document.getElementById('adminParseOpenaiModel').value = cfg.openai_parse_model || '';
            // 灰测解析 LLM
            document.getElementById('adminGreyParseEnabled').value = cfg.grey_parse_llm_enabled ? 'true' : 'false';
            document.getElementById('adminGreyParseEngine').value = cfg.grey_parse_llm_engine || 'opencode';
            fillModelOptions('adminGreyParseModel', cfg.grey_parse_llm_model, cfg.grey_parse_llm_engine || 'opencode');
            document.getElementById('adminGreyParseOpenaiBaseUrl').value = cfg.grey_openai_parse_base_url || '';
            document.getElementById('adminGreyParseOpenaiApiKey').value = cfg.grey_openai_parse_api_key || '';
            document.getElementById('adminGreyParseOpenaiModel').value = cfg.grey_openai_parse_model || '';
            // 分组测试（灰测）
            document.getElementById('adminGreyEnabled').value = cfg.grey_enabled ? 'true' : 'false';
            document.getElementById('adminGreyPercent').value = cfg.grey_percent || 0;
            document.getElementById('adminGreyAssignMode').value = cfg.grey_assign_mode || 'receipt';
            document.getElementById('adminGreyRecEngine').value = cfg.grey_recognition_engine || 'opencode';
            document.getElementById('adminGreyAudEngine').value = cfg.grey_audit_engine || 'opencode';
            document.getElementById('adminGreyAuditEnabled').value = cfg.grey_audit_enabled ? 'true' : 'false';
            fillModelOptions('adminGreyRecModel', cfg.grey_recognition_model, cfg.grey_recognition_engine || 'opencode');
            fillModelOptions('adminGreyAudModel', cfg.grey_audit_model, cfg.grey_audit_engine || 'opencode');
            document.getElementById('adminGreyOpenaiRecBaseUrl').value = cfg.grey_openai_rec_base_url || '';
            document.getElementById('adminGreyOpenaiRecApiKey').value = cfg.grey_openai_rec_api_key || '';
            document.getElementById('adminGreyOpenaiRecModel').value = cfg.grey_openai_rec_model || '';
            document.getElementById('adminGreyOpenaiAudBaseUrl').value = cfg.grey_openai_aud_base_url || '';
            document.getElementById('adminGreyOpenaiAudApiKey').value = cfg.grey_openai_aud_api_key || '';
            document.getElementById('adminGreyOpenaiAudModel').value = cfg.grey_openai_aud_model || '';

            updateAuditDisabledState();
            updateGreyDisabledState();
            updateParseDisabledState();
            updateParseOpenaiBox();
            updateGreyParseDisabledState();
            updateGreyParseOpenaiBox();
            updateGreyAuditDisabledState();
            updateOpenaiBoxes();
            updateGreyOpenaiBoxes();
            // 自动加载脱敏样本观测数据
            try { loadAdminGreySamples(); } catch (e) { console.error(e); }
            // 灰测状态
            fetch('/api/admin/grey-test')
                .then(r => r.json())
                .then(gt => {
                    const el = document.getElementById('adminGreyStatus');
                    if (el && gt && gt.data) {
                        const cur = gt.data.current || {};
                        const greyState = cur.grey_enabled
                            ? ('<strong style="color:#b8860b;">灰测开启</strong> · 概率 <code>' + (cur.grey_percent || 0) +
                               '%</code> · 分配 <code>' + (cur.grey_assign_mode || '—') +
                               '</code><br>灰测识别 <code>' + (cur.grey_recognition_engine || '—') + '/' + (cur.grey_recognition_model || '—') +
                               '</code> · 灰测审核 <code>' + (cur.grey_audit_engine || '—') + '/' + (cur.grey_audit_model || '—'))
                            : '<strong>灰测停用</strong>';
                        el.innerHTML = '';
                    }
                })
                .catch(() => {});
        })
        .catch(() => showToast('读取引擎配置失败', 'error'));
}

function saveAdminEngineConfig() {
    const saveBtn = document.getElementById('btnSaveAdminEngine');
    const origText = saveBtn ? saveBtn.textContent : '保存配置';
    
    const body = {
        recognition_engine: document.getElementById('adminRecognitionEngine').value,
        recognition_model: document.getElementById('adminRecognitionModel').value,
        audit_engine: document.getElementById('adminAuditEngine').value,
        audit_model: document.getElementById('adminAuditModel').value,
        audit_enabled: document.getElementById('adminAuditEnabled').value === 'true',
        openai_rec_base_url: document.getElementById('adminOpenaiRecBaseUrl').value,
        openai_rec_api_key: document.getElementById('adminOpenaiRecApiKey').value,
        openai_rec_model: document.getElementById('adminOpenaiRecModel').value,
        openai_aud_base_url: document.getElementById('adminOpenaiAudBaseUrl').value,
        openai_aud_api_key: document.getElementById('adminOpenaiAudApiKey').value,
        openai_aud_model: document.getElementById('adminOpenaiAudModel').value,
        parse_llm_enabled: document.getElementById('adminParseEnabled').value === 'true',
        parse_llm_engine: document.getElementById('adminParseEngine').value,
        parse_llm_model: document.getElementById('adminParseModel').value,
        openai_parse_base_url: document.getElementById('adminParseOpenaiBaseUrl').value,
        openai_parse_api_key: document.getElementById('adminParseOpenaiApiKey').value,
        openai_parse_model: document.getElementById('adminParseOpenaiModel').value,
        grey_enabled: document.getElementById('adminGreyEnabled').value === 'true',
        grey_percent: parseInt(document.getElementById('adminGreyPercent').value, 10) || 0,
        grey_assign_mode: document.getElementById('adminGreyAssignMode').value,
        grey_recognition_engine: document.getElementById('adminGreyRecEngine').value,
        grey_recognition_model: document.getElementById('adminGreyRecModel').value,
        grey_audit_engine: document.getElementById('adminGreyAudEngine').value,
        grey_audit_enabled: document.getElementById('adminGreyAuditEnabled').value === 'true',
        grey_audit_model: document.getElementById('adminGreyAudModel').value,
        grey_openai_rec_base_url: document.getElementById('adminGreyOpenaiRecBaseUrl').value,
        grey_openai_rec_api_key: document.getElementById('adminGreyOpenaiRecApiKey').value,
        grey_openai_rec_model: document.getElementById('adminGreyOpenaiRecModel').value,
        grey_openai_aud_base_url: document.getElementById('adminGreyOpenaiAudBaseUrl').value,
        grey_openai_aud_api_key: document.getElementById('adminGreyOpenaiAudApiKey').value,
        grey_openai_aud_model: document.getElementById('adminGreyOpenaiAudModel').value,
        grey_parse_llm_enabled: document.getElementById('adminGreyParseEnabled').value === 'true',
        grey_parse_llm_engine: document.getElementById('adminGreyParseEngine').value,
        grey_parse_llm_model: document.getElementById('adminGreyParseModel').value,
        grey_openai_parse_base_url: document.getElementById('adminGreyParseOpenaiBaseUrl').value,
        grey_openai_parse_api_key: document.getElementById('adminGreyParseOpenaiApiKey').value,
        grey_openai_parse_model: document.getElementById('adminGreyParseOpenaiModel').value,
    };

    const skipTest = document.getElementById('adminSkipModelTest') && document.getElementById('adminSkipModelTest').checked;

    function doSave() {
        if (saveBtn) {
            saveBtn.disabled = true;
            saveBtn.textContent = '正在保存配置...';
        }
        return fetch('/api/admin/engine-config', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        })
            .then(r => r.json())
            .then(ret => {
                if (ret && ret.status === 'success') {
                    // 保存成功：将有效的自定义模型正式持久化到 localStorage
                    const recEngine = document.getElementById('adminRecognitionEngine').value;
                    const recModel = document.getElementById('adminRecognitionModel').value;
                    if (recEngine !== 'openai' && recModel) addCustomModel(recEngine, recModel);

                    const parseEngine = document.getElementById('adminParseEngine').value;
                    const parseModel = document.getElementById('adminParseModel').value;
                    if (parseEngine !== 'openai' && parseModel) addCustomModel(parseEngine, parseModel);

                    const audEngine = document.getElementById('adminAuditEngine').value;
                    const audModel = document.getElementById('adminAuditModel').value;
                    if (audEngine !== 'openai' && audModel) addCustomModel(audEngine, audModel);

                    // 灰测区域自定义模型同步持久化
                    const gRecEngine = document.getElementById('adminGreyRecEngine').value;
                    const gRecModel = document.getElementById('adminGreyRecModel').value;
                    if (gRecEngine !== 'openai' && gRecModel) addCustomModel(gRecEngine, gRecModel);

                    const gAudEngine = document.getElementById('adminGreyAudEngine').value;
                    const gAudModel = document.getElementById('adminGreyAudModel').value;
                    if (gAudEngine !== 'openai' && gAudModel) addCustomModel(gAudEngine, gAudModel);

                    const gParseEngine = document.getElementById('adminGreyParseEngine').value;
                    const gParseModel = document.getElementById('adminGreyParseModel').value;
                    if (gParseEngine !== 'openai' && gParseModel) addCustomModel(gParseEngine, gParseModel);

                    // 记录通过测试并保存的模型到白名单库
                    if (recEngine !== 'openai' && recModel) markModelVerified(recEngine, recModel);
                    if (parseEngine !== 'openai' && parseModel) markModelVerified(parseEngine, parseModel);
                    if (audEngine !== 'openai' && audModel) markModelVerified(audEngine, audModel);
                    if (gRecEngine !== 'openai' && gRecModel) markModelVerified(gRecEngine, gRecModel);
                    if (gAudEngine !== 'openai' && gAudModel) markModelVerified(gAudEngine, gAudModel);
                    if (gParseEngine !== 'openai' && gParseModel) markModelVerified(gParseEngine, gParseModel);

                    showToast('配置已成功保存', 'success');
                } else {
                    // E-P1-4：403 人话统一（FastAPI HTTPException 返回 detail 字段）
                    const errMsg = (ret && (ret.msg || ret.detail)) || '保存失败';
                    showToast(humanizeForbidden(errMsg) || errMsg, 'error');
                }
            })
            .catch(err => {
                showToast('保存异常: ' + (err.message || err), 'error');
            })
            .finally(() => {
                if (saveBtn) {
                    saveBtn.disabled = false;
                    saveBtn.textContent = origText;
                }
            });
    }

    // 判断当前启用的各模块模型是否均已通过测试（已验证过）
    const recEng = body.recognition_engine;
    const recMod = body.recognition_model;
    const parseEng = body.parse_llm_engine;
    const parseMod = body.parse_llm_model;
    const audEng = body.audit_engine;
    const audMod = body.audit_model;

    const gRecEng = body.grey_recognition_engine;
    const gRecMod = body.grey_recognition_model;
    const gParseEng = body.grey_parse_llm_engine;
    const gParseMod = body.grey_parse_llm_model;
    const gAudEng = body.grey_audit_engine;
    const gAudMod = body.grey_audit_model;

    let allVerified = true;
    if (recEng !== 'openai' && !isModelVerified(recEng, recMod)) allVerified = false;
    if (body.parse_llm_enabled && parseEng !== 'openai' && !isModelVerified(parseEng, parseMod)) allVerified = false;
    if (body.audit_enabled && audEng !== 'openai' && !isModelVerified(audEng, audMod)) allVerified = false;

    if (body.grey_enabled && body.grey_percent > 0) {
        if (gRecEng !== 'openai' && !isModelVerified(gRecEng, gRecMod)) allVerified = false;
        if (body.grey_parse_llm_enabled && gParseEng !== 'openai' && !isModelVerified(gParseEng, gParseMod)) allVerified = false;
        if (body.audit_enabled && gAudEng !== 'openai' && !isModelVerified(gAudEng, gAudMod)) allVerified = false;
    }

    // 若用户勾选跳过测试，或当前所有配置的模型此前已通过测试，则直接秒级保存
    if (skipTest || allVerified) {
        return doSave();
    }

    let countdown = 45;
    let timer = null;

    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.textContent = '正在测试引擎连接 (' + countdown + 's)...';
        timer = setInterval(() => {
            countdown -= 1;
            if (countdown > 0) {
                saveBtn.textContent = '正在测试引擎连接 (' + countdown + 's)...';
            } else {
                saveBtn.textContent = '测试即将完成，请稍候...';
            }
        }, 1000);
    }
    showToast('检测到新模型，正在测试连通性，最长需 45s...', 'info');

    // 1. 仅针对未验证的新模型进行连接自测
    fetch('/api/admin/test-engine-config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    })
        .then(res => res.json().then(data => ({ status: res.status, data: data })))
        .then(resObj => {
            if (timer) clearInterval(timer);
            if (resObj.status !== 200 || resObj.data.status !== 'success') {
                let errMsg = (resObj.data && (resObj.data.msg || resObj.data.detail)) || '连接测试未通过';
                // E-P1-4：403 人话统一（admin 专属接口被非 admin 触发时给出角色指引）
                errMsg = humanizeForbidden(errMsg, resObj.status) || errMsg;
                if (saveBtn) {
                    saveBtn.disabled = false;
                    saveBtn.textContent = origText;
                }
                showCustomConfirmModal({
                    title: '引擎配置测试未通过',
                    message: errMsg + '\n\n是否仍然强制保存当前配置？',
                    confirmText: '强制保存',
                    cancelText: '取消',
                    onConfirm: () => {
                        doSave();
                    },
                    onCancel: () => {
                        showToast('已取消保存配置，恢复已生效设置', 'info');
                        loadAdminEngineConfig();
                    }
                });
                return;
            }

            // 2. 测试通过，执行实际保存
            return doSave();
        })
        .catch(err => {
            if (timer) clearInterval(timer);
            console.error('测试异常', err);
            if (saveBtn) {
                saveBtn.disabled = false;
                saveBtn.textContent = origText;
            }
            showCustomConfirmModal({
                title: '测试请求异常',
                message: (err.message || err) + '\n\n是否仍然强制保存当前配置？',
                confirmText: '强制保存',
                cancelText: '取消',
                onConfirm: () => {
                    doSave();
                },
                onCancel: () => {
                    showToast('已取消保存配置', 'info');
                }
            });
        });
}

// 审核开关：关 → 审核引擎/模型置灰（disabled）并隐藏审核 OpenAI 参数窗口
function updateAuditDisabledState() {
    const enabled = document.getElementById('adminAuditEnabled').value === 'true';
    const audIsOpenai = document.getElementById('adminAuditEngine').value === 'openai';
    const engineEl = document.getElementById('adminAuditEngine');
    if (engineEl) {
        engineEl.disabled = !enabled;
        engineEl.style.opacity = enabled ? '1' : '0.45';
    }
    const modelEl = document.getElementById('adminAuditModel');
    if (modelEl) {
        const modelDisabled = !enabled || audIsOpenai;
        modelEl.disabled = modelDisabled;
        modelEl.style.opacity = modelDisabled ? '0.45' : '1';
        syncModelSelectOpenaiState('adminAuditModel', audIsOpenai);
    }
    const audBox = document.getElementById('adminOpenaiAuditBox');
    if (audBox) {
        audBox.style.display = (enabled && audIsOpenai) ? '' : 'none';
    }
}

// 引擎选择 → 重新填充模型列表（切换引擎时自动重置为该引擎默认模型） & OpenAI 参数区显隐 & 相应模型下拉置灰
function updateOpenaiBoxes(e) {
    const recEngine = document.getElementById('adminRecognitionEngine').value;
    const audEngine = document.getElementById('adminAuditEngine').value;
    const recIsOpenai = recEngine === 'openai';
    const auditEnabled = document.getElementById('adminAuditEnabled').value === 'true';
    const audIsOpenai = audEngine === 'openai';
    
    // 识别模型根据引擎动态渲染并联动置灰（切换引擎时不保留上一引擎旧模型）
    const recModelEl = document.getElementById('adminRecognitionModel');
    if (recModelEl) {
        const curVal = recModelEl.value;
        const validModels = (DEFAULT_ENGINE_MODELS[recEngine] || []).map(m => m.value).concat(getCustomModels(recEngine));
        const keepVal = (e && e.type === 'change' && e.target && e.target.id === 'adminRecognitionEngine')
            ? (validModels.includes(curVal) ? curVal : null)
            : (validModels.includes(curVal) ? curVal : null);
        fillModelOptions('adminRecognitionModel', keepVal, recEngine);
        recModelEl.disabled = recIsOpenai;
        recModelEl.style.opacity = recIsOpenai ? '0.45' : '1';
        syncModelSelectOpenaiState('adminRecognitionModel', recIsOpenai);
    }
    
    // 审核模型根据引擎动态渲染
    const audModelEl = document.getElementById('adminAuditModel');
    if (audModelEl) {
        const curVal = audModelEl.value;
        const validModels = (DEFAULT_ENGINE_MODELS[audEngine] || []).map(m => m.value).concat(getCustomModels(audEngine));
        const keepVal = validModels.includes(curVal) ? curVal : null;
        fillModelOptions('adminAuditModel', keepVal, audEngine);
    }
    
    // 审核模型与窗口显隐联动
    updateAuditDisabledState();
    
    document.getElementById('adminOpenaiRecognitionBox').style.display = recIsOpenai ? '' : 'none';
    document.getElementById('adminOpenaiAuditBox').style.display = (auditEnabled && audIsOpenai) ? '' : 'none';
}

// 灰测引擎选择 → 重新填充模型列表（切换引擎时自动重置为该引擎默认模型） & 灰测 OpenAI 参数区显隐
function updateGreyOpenaiBoxes() {
    const recEngine = document.getElementById('adminGreyRecEngine').value;
    const audEngine = document.getElementById('adminGreyAudEngine').value;
    const recIsOpenai = recEngine === 'openai';
    const audIsOpenai = audEngine === 'openai';

    const recModelEl = document.getElementById('adminGreyRecModel');
    if (recModelEl) {
        const curVal = recModelEl.value;
        const validModels = (DEFAULT_ENGINE_MODELS[recEngine] || []).map(m => m.value).concat(getCustomModels(recEngine));
        const keepVal = validModels.includes(curVal) ? curVal : null;
        fillModelOptions('adminGreyRecModel', keepVal, recEngine);
        recModelEl.disabled = recIsOpenai;
        recModelEl.style.opacity = recIsOpenai ? '0.45' : '1';
        syncModelSelectOpenaiState('adminGreyRecModel', recIsOpenai);
    }

    const audModelEl = document.getElementById('adminGreyAudModel');
    if (audModelEl) {
        const curVal = audModelEl.value;
        const validModels = (DEFAULT_ENGINE_MODELS[audEngine] || []).map(m => m.value).concat(getCustomModels(audEngine));
        const keepVal = validModels.includes(curVal) ? curVal : null;
        fillModelOptions('adminGreyAudModel', keepVal, audEngine);
        audModelEl.disabled = audIsOpenai;
        audModelEl.style.opacity = audIsOpenai ? '0.45' : '1';
        syncModelSelectOpenaiState('adminGreyAudModel', audIsOpenai);
    }

    document.getElementById('adminGreyOpenaiRecBox').style.display = recIsOpenai ? '' : 'none';
    // 灰测审核 OpenAI 参数区显隐由 updateGreyAuditDisabledState 统一管控（需 gate audit_enabled）
    updateGreyAuditDisabledState();
}

// 灰测审核开关：关 → 灰测审核引擎/模型置灰并隐藏灰测审核 OpenAI 参数区（对齐常规审核）
function updateGreyAuditDisabledState() {
    const enabled = document.getElementById('adminGreyAuditEnabled').value === 'true';
    const audIsOpenai = document.getElementById('adminGreyAudEngine').value === 'openai';
    const engineEl = document.getElementById('adminGreyAudEngine');
    if (engineEl) {
        engineEl.disabled = !enabled;
        engineEl.style.opacity = enabled ? '1' : '0.45';
    }
    const modelEl = document.getElementById('adminGreyAudModel');
    if (modelEl) {
        const modelDisabled = !enabled || audIsOpenai;
        modelEl.disabled = modelDisabled;
        modelEl.style.opacity = modelDisabled ? '0.45' : '1';
        syncModelSelectOpenaiState('adminGreyAudModel', audIsOpenai);
    }
    const audBox = document.getElementById('adminGreyOpenaiAudBox');
    if (audBox) {
        audBox.style.display = (enabled && audIsOpenai) ? '' : 'none';
    }
}
// 灰测停用 → 整块（adminGreyBody）置灰：全部控件 disabled + 半透明
function updateGreyDisabledState() {
    const enabled = document.getElementById('adminGreyEnabled').value === 'true';
    const body = document.getElementById('adminGreyBody');
    if (body) {
        body.querySelectorAll('input, select, button').forEach((el) => {
            el.disabled = !enabled;
        });
        body.style.opacity = enabled ? '1' : '0.45';
        body.style.pointerEvents = enabled ? 'auto' : 'none';
    }
}

// 解析 LLM：关 → 置灰（引擎/模型/OpenAI 区）；引擎选 openai → 模型置灰清空并显示参数区
function updateParseDisabledState() {
    const enabled = document.getElementById('adminParseEnabled').value === 'true';
    const isOpenai = document.getElementById('adminParseEngine').value === 'openai';
    const engineEl = document.getElementById('adminParseEngine');
    if (engineEl) {
        engineEl.disabled = !enabled;
        engineEl.style.opacity = enabled ? '1' : '0.45';
    }
    const modelEl = document.getElementById('adminParseModel');
    if (modelEl) {
        const modelDisabled = !enabled || isOpenai;
        modelEl.disabled = modelDisabled;
        modelEl.style.opacity = modelDisabled ? '0.45' : '1';
        syncModelSelectOpenaiState('adminParseModel', isOpenai);
    }
    const box = document.getElementById('adminParseOpenaiBox');
    if (box) {
        box.style.display = (enabled && isOpenai) ? '' : 'none';
    }
}
function updateParseOpenaiBox() {
    const enabled = document.getElementById('adminParseEnabled').value === 'true';
    const parseEngine = document.getElementById('adminParseEngine').value;
    const isOpenai = parseEngine === 'openai';
    const parseModelEl = document.getElementById('adminParseModel');
    if (parseModelEl) {
        const curVal = parseModelEl.value;
        const validModels = (DEFAULT_ENGINE_MODELS[parseEngine] || []).map(m => m.value).concat(getCustomModels(parseEngine));
        const keepVal = validModels.includes(curVal) ? curVal : null;
        fillModelOptions('adminParseModel', keepVal, parseEngine);
    }
    updateParseDisabledState();
    document.getElementById('adminParseOpenaiBox').style.display = (enabled && isOpenai) ? '' : 'none';
}

// 灰测解析 LLM：关 → 置灰；引擎选 openai → 显示参数区
function updateGreyParseDisabledState() {
    const enabled = document.getElementById('adminGreyParseEnabled').value === 'true';
    const body = document.getElementById('adminGreyParseBody');
    if (body) {
        body.querySelectorAll('input, select').forEach((el) => { el.disabled = !enabled; });
        body.style.opacity = enabled ? '1' : '0.45';
        body.style.pointerEvents = enabled ? 'auto' : 'none';
    }
    updateGreyParseOpenaiBox();
}
function updateGreyParseOpenaiBox() {
    const enabled = document.getElementById('adminGreyParseEnabled').value === 'true';
    const parseEngine = document.getElementById('adminGreyParseEngine').value;
    const isOpenai = parseEngine === 'openai';
    const modelEl = document.getElementById('adminGreyParseModel');
    if (modelEl) {
        const curVal = modelEl.value;
        const validModels = (DEFAULT_ENGINE_MODELS[parseEngine] || []).map(m => m.value).concat(getCustomModels(parseEngine));
        const keepVal = validModels.includes(curVal) ? curVal : null;
        fillModelOptions('adminGreyParseModel', keepVal, parseEngine);
        modelEl.disabled = isOpenai;
        modelEl.style.opacity = isOpenai ? '0.45' : '1';
        syncModelSelectOpenaiState('adminGreyParseModel', isOpenai);
    }
    document.getElementById('adminGreyParseOpenaiBox').style.display = (enabled && isOpenai) ? '' : 'none';
}

// ---------------------------------- 推全 / 回滚 / 对比 ----------------------------------

// 灰测↔常规配置对比：GET /api/admin/engine-config/diff
function fetchAndRenderDiff(onRendered) {
    const panel = document.getElementById('adminGreyDiffPanel');
    if (panel) {
        panel.classList.remove('hide');
        panel.dataset.loading = '1';
    }
    fetch('/api/admin/engine-config/diff')
        .then(r => r.json())
        .then(body => {
            if (!body || body.status !== 'success') {
                showToast((body && body.msg) || '读取对比失败', 'warning');
                if (panel) panel.classList.add('hide');
                if (onRendered) onRendered(false);
                return;
            }
            const { diffs, totalPairs, diffCount } = body.data || {};
            renderGreyDiffPanel({ diffs, totalPairs, diffCount });
            if (panel) panel.dataset.loading = '0';
            if (onRendered) onRendered(true);
        })
        .catch(err => {
            console.error('diff 请求异常', err);
            showToast('对比请求异常', 'error');
            if (panel) panel.classList.add('hide');
            if (onRendered) onRendered(false);
        });
}

// 渲染双列对照表：差异行高亮，敏感字段打码展示
function renderGreyDiffPanel({ diffs, totalPairs, diffCount }) {
    const panel = document.getElementById('adminGreyDiffPanel');
    if (!panel) return;
    const wrap = panel.querySelector('.diff-wrap');
    if (!wrap) return;

    const header = document.getElementById('adminGreyDiffHeader');
    if (header) {
        header.innerHTML = '配置对比 · 常规 <span style="color:#1565c0;">vs</span> 灰测 '
            + (diffCount > 0
                ? `<span style="color:#b8860b;">发现 <strong>${diffCount}</strong> 项差异（共 ${totalPairs} 项）</span>`
                : '<span style="color:#2f6b4f;">两组配置完全一致</span>');
    }

    let html = '<table class="diff-table"><thead><tr><th>配置项</th><th>常规组</th><th>灰测组</th><th>状态</th></tr></thead><tbody>';
    if (Array.isArray(diffs) && diffs.length > 0) {
        diffs.forEach(d => {
            const isDiff = !d.identical;
            const rowCls = isDiff ? 'diff-row' : '';
            const sensitive = d.sensitive;
            const reg = sensitive ? (d.regular || '—') : `<code>${escapeHtml(String(d.regular || '—'))}</code>`;
            const grey = sensitive ? (d.grey || '—') : `<code>${escapeHtml(String(d.grey || '—'))}</code>`;
            const status = isDiff ? '<span class="badge" style="color:#b8860b;">差异</span>' : '<span class="badge" style="color:#2f6b4f;">一致</span>';
            html += `<tr class="${rowCls}"><td>${escapeHtml(d.label)}</td><td>${reg}</td><td>${grey}</td><td>${status}</td></tr>`;
        });
    } else {
        html += '<tr><td colspan="4" style="text-align:center; color:var(--text-muted);">无差异项</td></tr>';
    }
    html += '</tbody></table>';
    wrap.innerHTML = html;
}

// 一键推全：PUT /api/admin/engine-config/promote
function promoteGreyConfig() {
    showCustomConfirmModal({
        title: '一键推全：灰测组 → 常规组',
        message: '确认将当前灰测组配置覆盖为常规组吗？\n\n推全将：\n① 记录当前常规组配置作为回滚快照\n② 将灰测组引擎/模型/OpenAI 参数/解析 LLM 全部覆盖到常规组\n③ 灰测组随后清空为默认态并停用灰测\n\n推全后如需回滚，可在同面板点击「一键回滚」。',
        confirmText: '确认推全',
        cancelText: '取消',
        onConfirm: () => {
            fetch('/api/admin/engine-config/promote', { method: 'PUT', headers: { 'Content-Type': 'application/json' } })
                .then(r => r.json())
                .then(body => {
                    if (!body || body.status !== 'success') {
                        showToast((body && body.msg) || '推全失败', 'error');
                        return;
                    }
                    showToast('推全成功：灰测组已覆盖为常规组，快照已保存', 'success');
                    const panel = document.getElementById('adminGreyDiffPanel');
                    if (panel) panel.classList.add('hide');
                    loadAdminEngineConfig();
                })
                .catch(err => { showToast('推全请求异常', 'error'); console.error(err); });
        },
        onCancel: () => { showToast('已取消推全', 'info'); }
    });
}

// 回滚前先渲染一次 Diff（推全确认步骤）：默认直接走 fetch+render
function preparePromoteWithDiff() {
    fetchAndRenderDiff((ok) => {
        if (!ok) {
            // 若灰测未启用 → 直接提示
            showToast('灰测未启用，无法推全', 'warning');
            return;
        }
        const panel = document.getElementById('adminGreyDiffPanel');
        const confirmBtn = document.getElementById('adminGreyPromoteConfirm');
        if (confirmBtn) {
            confirmBtn.classList.remove('hide');
            confirmBtn.onclick = () => {
                promoteGreyConfig();
            };
        }
    });
}

// 一键回滚：PUT /api/admin/engine-config/rollback
function rollbackEngineConfig() {
    showCustomConfirmModal({
        title: '一键回滚：恢复上次推全前的常规组',
        message: '确认回滚到上一次推全前的配置吗？\n\n回滚将：\n① 用快照中的常规组配置覆盖当前常规组\n② 清空回滚快照（一次快照，回滚后不再可回）',
        confirmText: '确认回滚',
        cancelText: '取消',
        onConfirm: () => {
            fetch('/api/admin/engine-config/rollback', { method: 'PUT', headers: { 'Content-Type': 'application/json' } })
                .then(r => r.json())
                .then(body => {
                    if (!body || body.status !== 'success') {
                        showToast((body && body.msg) || '回滚失败', 'error');
                        return;
                    }
                    showToast('回滚成功', 'success');
                    const panel = document.getElementById('adminGreyDiffPanel');
                    if (panel) panel.classList.add('hide');
                    loadAdminEngineConfig();
                })
                .catch(err => { showToast('回滚请求异常', 'error'); console.error(err); });
        },
        onCancel: () => { showToast('已取消回滚', 'info'); }
    });
}

// 辅助：转义 HTML
function escapeHtml(s) {
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

/* =============================================================
   阶段 1：AI 可见性与信任
   （PM 反馈：audit_reason 差异横幅与「AI 建议 vs 用户确认」对照面板已移除，
     AI 解析结果直接展示在步骤 2/2 的表单字段与明细表中）
   1.3  灰测组视觉标记（greyBadgeHtml）

   ============================================================= */

// 1.3 灰测组视觉标记：返回 <span class="badge-grey"> 或空串
function greyBadgeHtml(useGrey) {
    if (!useGrey) return '';
    return '<span class="badge-grey" title="灰测组处理（cross_audit 双模型）">灰测组</span>';
}

// -------------------------------------------------------------
// Admin 灰测用户使用状态与脱敏样本观测逻辑
// -------------------------------------------------------------
let _adminGreySamplesCache = [];

function loadAdminGreySamples() {
    const tbody = document.getElementById('adminGreySamplesBody');
    if (tbody) tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:#666;">正在拉取脱敏单据流并执行 PII 过滤与 AI 效果评估...</td></tr>';

    fetch('/api/admin/grey-test/samples')
        .then(r => r.json())
        .then(res => {
            if (!res || res.status !== 'success' || !res.samples) {
                if (tbody) tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:#c00;">拉取失败，请确认是否具备 admin 权限</td></tr>';
                return;
            }
            _adminGreySamplesCache = res.samples;
            
            // 更新指标
            const mTotal = document.getElementById('mGreyTotal');
            const mAppr = document.getElementById('mGreyApproved');
            const mEdit = document.getElementById('mGreyEdited');
            const mCanary = document.getElementById('mGreyCanary');
            const mPos = document.getElementById('mGreyPositive');
            const mMod = document.getElementById('mGreyModified');
            const mAvg = document.getElementById('mGreyAvgMatch');

            if (mTotal) mTotal.textContent = res.total_count || 0;
            if (mAppr) mAppr.textContent = res.user_approved_count || 0;
            if (mEdit) mEdit.textContent = res.user_edited_count || 0;
            if (mCanary) mCanary.textContent = res.grey_count || 0;
            if (mPos) mPos.textContent = res.positive_feedback_count || 0;
            if (mMod) mMod.textContent = res.modified_feedback_count || 0;
            if (mAvg) mAvg.textContent = (res.avg_match_rate != null ? res.avg_match_rate + '%' : '100%');

            if (res.samples.length === 0) {
                if (tbody) tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:#999;">暂无单据样本</td></tr>';
                return;
            }

            let html = '';
            res.samples.forEach((s, idx) => {
                const statusBadge = s.is_approved
                    ? '<span class="badge" style="background:#d4edda; color:#155724;">老板已入库</span>'
                    : (s.is_user_edited
                        ? '<span class="badge" style="background:#cce5ff; color:#004085;">店员已编辑</span>'
                        : '<span class="badge" style="background:#fff3cd; color:#856404;">待复核</span>');
                
                const engineBadge = s.use_grey
                    ? '<span class="badge-grey">灰测实验组</span>'
                    : '<span class="badge" style="background:#e9ecef; color:#495057;">常规线上组</span>';

                const evalData = s.effect_evaluation || {};
                const feedbackBadge = `<span class="badge" style="background:${evalData.feedback_badge_bg || '#e2e3e5'}; color:${evalData.feedback_badge_color || '#383d41'}; font-size:0.75rem;">${evalData.feedback_label || '待复核'} (${evalData.match_rate || 100}%)</span>`;

                html += `
                    <tr>
                        <td><strong>#${s.receipt_id}</strong></td>
                        <td><span style="color:#2f6b4f; font-weight:600;">${s.masked_vendor}</span> <small style="color:#999;">(已脱敏)</small></td>
                        <td><code>${s.doc_form}</code></td>
                        <td><code>${s.masked_total}</code></td>
                        <td>${engineBadge}</td>
                        <td>${statusBadge}</td>
                        <td>${feedbackBadge}</td>
                        <td>
                            <button class="btn btn-secondary" style="font-size:0.75rem; padding:3px 8px;" onclick="viewGreySampleDetail(${idx})">查看脱敏解析</button>
                        </td>
                    </tr>
                `;
            });
            if (tbody) tbody.innerHTML = html;
            showToast('已成功拉取 ' + res.samples.length + ' 份脱敏灰测样本与效果评估', 'success');
        })
        .catch(err => {
            console.error(err);
            if (tbody) tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:#c00;">网络异常，请重试</td></tr>';
        });
}

const DESENSITIZED_IMG_FALLBACK = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(`
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 360 480" width="100%" height="100%" fill="none">
    <rect width="360" height="480" rx="8" fill="#f8fafc" stroke="#cbd5e1" stroke-width="1.5"/>
    <g fill="#94a3b8">
        <rect x="30" y="36" width="140" height="14" rx="3" fill="#64748b"/>
        <rect x="30" y="60" width="220" height="8" rx="2" fill="#cbd5e1"/>
        <rect x="30" y="74" width="180" height="8" rx="2" fill="#cbd5e1"/>
        <line x1="30" y1="96" x2="330" y2="96" stroke="#cbd5e1" stroke-dasharray="4 4"/>
        <rect x="30" y="112" width="100" height="10" rx="2" fill="#94a3b8"/>
        <rect x="250" y="112" width="80" height="10" rx="2" fill="#94a3b8"/>
        <rect x="30" y="136" width="120" height="10" rx="2" fill="#cbd5e1"/>
        <rect x="260" y="136" width="70" height="10" rx="2" fill="#cbd5e1"/>
        <rect x="30" y="160" width="90" height="10" rx="2" fill="#cbd5e1"/>
        <rect x="250" y="160" width="80" height="10" rx="2" fill="#cbd5e1"/>
        <rect x="30" y="184" width="130" height="10" rx="2" fill="#cbd5e1"/>
        <rect x="270" y="184" width="60" height="10" rx="2" fill="#cbd5e1"/>
        <line x1="30" y1="210" x2="330" y2="210" stroke="#cbd5e1" stroke-dasharray="4 4"/>
        <rect x="30" y="226" width="80" height="12" rx="2" fill="#64748b"/>
        <rect x="240" y="226" width="90" height="12" rx="2" fill="#64748b"/>
    </g>
    <rect x="30" y="260" width="300" height="64" rx="6" fill="#f1f5f9" stroke="#e2e8f0"/>
    <text x="180" y="288" font-family="-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif" font-size="12" font-weight="600" fill="#475569" text-anchor="middle">脱敏单据切片原图保护</text>
    <text x="180" y="308" font-family="-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif" font-size="10" fill="#94a3b8" text-anchor="middle">原始单据图像已脱敏隔离存储</text>
</svg>
`);

function viewGreySampleDetail(idx) {
    const s = _adminGreySamplesCache[idx];
    if (!s) return;
    
    // 1. 填充 Modal
    const modal = document.getElementById('adminGreySampleModal');
    const mTitle = document.getElementById('adminGreyModalTitle');
    const mImg = document.getElementById('adminGreyModalImg');
    const mVendor = document.getElementById('mModalVendor');
    const mTotal = document.getElementById('mModalTotal');
    const mForm = document.getElementById('mModalForm');
    const mStatus = document.getElementById('mModalStatus');
    const mEngine = document.getElementById('mModalEngine');
    const mItemsBody = document.getElementById('adminGreyModalItemsBody');
    const mJson = document.getElementById('adminGreyModalJson');

    // 效果评估组件
    const mBadge = document.getElementById('mModalFeedbackBadge');
    const mMatchRate = document.getElementById('mModalMatchRate');
    const mMatchedFields = document.getElementById('mModalMatchedFields');
    const mModifiedFields = document.getElementById('mModalModifiedFields');
    const mCompareBody = document.getElementById('mModalFieldCompareBody');

    const evalData = s.effect_evaluation || {};

    if (mTitle) mTitle.textContent = `单据 #${s.receipt_id} 脱敏解析详情与图像切片（${s.masked_vendor} · ${s.user_status}）`;
    if (mImg) {
        mImg.onerror = function() {
            this.onerror = null;
            this.src = DESENSITIZED_IMG_FALLBACK;
        };
        mImg.src = s.image_url || DESENSITIZED_IMG_FALLBACK;
    }
    if (mVendor) mVendor.textContent = s.masked_vendor || '-';
    if (mTotal) mTotal.textContent = s.masked_total || '-';
    if (mForm) mForm.textContent = s.doc_form || '-';
    if (mStatus) {
        mStatus.innerHTML = s.is_approved
            ? '<span class="badge" style="background:#d4edda; color:#155724;">老板已入库</span>'
            : (s.is_user_edited
                ? '<span class="badge" style="background:#cce5ff; color:#004085;">店员已编辑</span>'
                : '<span class="badge" style="background:#fff3cd; color:#856404;">待复核</span>');
    }
    if (mEngine) mEngine.textContent = s.engine || '-';

    // 填充效果评估卡
    if (mBadge) {
        mBadge.textContent = evalData.feedback_label || '待复核';
        mBadge.style.background = evalData.feedback_badge_bg || '#e2e3e5';
        mBadge.style.color = evalData.feedback_badge_color || '#383d41';
    }
    if (mMatchRate) mMatchRate.textContent = (evalData.match_rate != null ? evalData.match_rate + '%' : '100%');
    if (mMatchedFields) mMatchedFields.textContent = evalData.matched_fields || 0;
    if (mModifiedFields) mModifiedFields.textContent = evalData.modified_fields || 0;

    // 渲染 AI vs 用户最终输入 逐项对照表
    if (mCompareBody) {
        const comparisons = evalData.field_comparisons || [];
        if (comparisons.length === 0) {
            mCompareBody.innerHTML = '<tr><td colspan="4" style="text-align:center; color:#999;">暂无字段对照数据</td></tr>';
        } else {
            let compHtml = '';
            comparisons.forEach(c => {
                const badge = c.is_match
                    ? '<span class="badge" style="background:#d4edda; color:#155724; font-size:0.7rem;">[完全采纳]</span>'
                    : '<span class="badge" style="background:#fff3cd; color:#856404; font-size:0.7rem;">[人工纠偏]</span>';
                compHtml += `
                    <tr>
                        <td><strong>${escapeHtml(c.field)}</strong></td>
                        <td><code>${escapeHtml(c.ai_value)}</code></td>
                        <td><strong style="color:${c.is_match ? '#2f6b4f' : '#b8860b'}">${escapeHtml(c.user_value)}</strong></td>
                        <td class="col-center">${badge}</td>
                    </tr>
                `;
            });
            mCompareBody.innerHTML = compHtml;
        }
    }

    // 渲染明细行
    if (mItemsBody) {
        if (!s.masked_items || s.masked_items.length === 0) {
            mItemsBody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:#999;">无明细行或未产生明细</td></tr>';
        } else {
            let itemHtml = '';
            s.masked_items.forEach(it => {
                itemHtml += `
                    <tr>
                        <td><strong>${escapeHtml(it.item_name || '-')}</strong></td>
                        <td class="col-right">${it.quantity || 1}</td>
                        <td>${escapeHtml(it.unit || '斤')}</td>
                        <td class="col-right"><code>${escapeHtml(it.unit_price || '-')}</code></td>
                        <td class="col-right"><code>${escapeHtml(it.amount || '-')}</code></td>
                    </tr>
                `;
            });
            mItemsBody.innerHTML = itemHtml;
        }
    }

    if (mJson) {
        mJson.textContent = JSON.stringify({
            "receipt_id": s.receipt_id,
            "masked_vendor": s.masked_vendor,
            "receipt_date": s.date,
            "doc_form": s.doc_form,
            "masked_total_amount": s.masked_total,
            "user_status_machine": s.user_status,
            "engine_routing": s.engine,
            "is_user_edited": s.is_user_edited,
            "is_approved_by_owner": s.is_approved,
            "math_gate_verified": s.math_gate_passed,
            "effect_evaluation": {
                "feedback_result": evalData.feedback_label,
                "is_exact_match": evalData.is_exact_match,
                "field_match_rate": evalData.match_rate + "%",
                "matched_fields_count": evalData.matched_fields,
                "user_modified_fields_count": evalData.modified_fields
            },
            "desensitized_items": s.masked_items
        }, null, 2);
    }

    if (modal) {
        modal.classList.remove('hide');
    }

    // 2. 同步填充底栏抽屉（备选展示）
    const card = document.getElementById('adminGreySampleDetailCard');
    const title = document.getElementById('adminGreySampleDetailTitle');
    const img = document.getElementById('adminGreyDetailImg');
    const jsonPre = document.getElementById('adminGreyDetailJson');

    if (title) title.textContent = `单据 #${s.receipt_id} 脱敏解析详情（${s.masked_vendor} · ${s.user_status}）`;
    if (img) {
        img.onerror = function() {
            this.onerror = null;
            this.src = DESENSITIZED_IMG_FALLBACK;
        };
        img.src = s.image_url || DESENSITIZED_IMG_FALLBACK;
    }
    if (jsonPre) {
        jsonPre.textContent = mJson ? mJson.textContent : '';
    }
    if (card) {
        card.classList.remove('hide');
    }
}

function closeGreySampleModal() {
    const modal = document.getElementById('adminGreySampleModal');
    if (modal) modal.classList.add('hide');
}

// =====================================================================
// E-P1-2 黄金样本 57 看板（Admin）
// =====================================================================
const GOLDEN_DOC_FORM_LABELS = {
    printed_delivery_note: '印刷送货单',
    ncr_handwritten: '手写街市单',
    thermal: '热敏机打',
    weigh_slip: '磅单',
    correction_note: '更正单',
    credit_note: 'Credit Note',
    monthly_statement: '月结单',
};

function loadGoldenBoard() {
    const body = document.getElementById('goldenBoardBody');
    const summary = document.getElementById('goldenBoardSummary');
    if (body) body.innerHTML = '<tr><td colspan="8" style="text-align:center; color:var(--text-muted); padding:18px;">加载中</td></tr>';
    fetch('/api/admin/golden-samples')
        .then(res => Promise.all([res.status, res.json().catch(() => null)]))
        .then(([httpStatus, ret]) => {
            if (!ret || ret.status !== 'success') {
                if (toastHttpError(httpStatus, ret)) return;
                if (body) body.innerHTML = '<tr><td colspan="8" style="text-align:center; color:#c00; padding:18px;">加载失败：' + w2Escape((ret && (ret.msg || ret.detail)) || ('HTTP ' + httpStatus)) + '</td></tr>';
                return;
            }
            const cov = ret.coverage || {};
            const covParts = Object.keys(cov).map(k => {
                const c = cov[k] || {};
                const label = GOLDEN_DOC_FORM_LABELS[k] || k;
                return label + ' ' + (c.actual || 0) + '/' + (c.target || 0);
            });
            if (summary) summary.textContent = '库内单据 ' + (ret.total || 0) + ' / 目标 57 张。形态覆盖：' + covParts.join(' · ');
            if (!body) return;
            const items = ret.items || [];
            if (items.length === 0) {
                body.innerHTML = '<tr><td colspan="8" style="text-align:center; color:var(--text-muted); padding:18px;">暂无单据，可点击下方按钮导入黄金样本</td></tr>';
                return;
            }
            let html = '';
            items.forEach(it => {
                const statusBadge = it.status === 'approved'
                    ? '<span class="badge badge-success">已入库</span>'
                    : (it.status === 'edited' ? '<span class="badge badge-warning">待审核</span>'
                        : '<span class="badge badge-secondary">' + w2Escape(it.status || '-') + '</span>');
                html += '<tr>'
                    + '<td>#' + Number(it.id) + '</td>'
                    + '<td>' + w2Escape(it.supplier_name || '-') + '</td>'
                    + '<td>' + w2Escape(it.receipt_date || '-') + '</td>'
                    + '<td>' + w2Escape(GOLDEN_DOC_FORM_LABELS[it.doc_form] || it.doc_form || '-') + '</td>'
                    + '<td class="col-right">' + currencySymbol(it.currency) + fmtMoney(it.total_amount) + '</td>'
                    + '<td>' + statusBadge + '</td>'
                    + '<td><code>' + w2Escape(it.currency || 'HKD') + '</code></td>'
                    + '<td><button class="btn btn-secondary" style="padding:2px 8px; font-size:0.72rem;" onclick="loadReceiptDetail(' + Number(it.id) + ')">详情</button></td>'
                    + '</tr>';
            });
            body.innerHTML = html;
        })
        .catch(err => {
            console.error('黄金样本看板加载失败', err);
            if (body) body.innerHTML = '<tr><td colspan="8" style="text-align:center; color:#c00; padding:18px;">加载失败，请稍后重试</td></tr>';
        });
}

function importGoldenSamples(limit) {
    if (!confirm('确认导入 ' + limit + ' 张黄金样本？将按 manifest 去重，已导入的自动跳过。')) return;
    showToast('正在导入黄金样本...', 'info');
    fetch('/api/admin/golden-samples/import?limit=' + Number(limit), { method: 'POST' })
        .then(res => Promise.all([res.status, res.json().catch(() => null)]))
        .then(([httpStatus, ret]) => {
            if (!ret || ret.status !== 'success') {
                if (toastHttpError(httpStatus, ret)) return;
                showToast('导入失败：' + ((ret && (ret.msg || ret.detail)) || ('HTTP ' + httpStatus)), 'error');
                return;
            }
            showToast('导入完成', 'success', TOAST_DURATION.long);
            loadGoldenBoard();
        })
        .catch(err => {
            console.error('导入黄金样本失败', err);
            showToast('导入请求失败，请稍后重试', 'error');
        });
}

// =====================================================================
// E-P1-3 p-value 卡片（A/B 显著性检验）
// 阈值说明：双侧双比例 z 检验，alpha=0.05；p<0.05 显著；N<30 低置信度
// =====================================================================
var PVALUE_ALPHA = 0.05;
var PVALUE_MIN_SAMPLE = 30;

function loadPValueCards() {
    const container = document.getElementById('pvalueCardsContainer');
    const select = document.getElementById('pvalueExperimentSelect');
    if (!container) return;
    fetch('/api/admin/experiments')
        .then(res => Promise.all([res.status, res.json().catch(() => null)]))
        .then(([httpStatus, ret]) => {
            if (!ret || ret.status !== 'success') {
                if (toastHttpError(httpStatus, ret)) return;
                container.innerHTML = '<div style="color:#c00; font-size:0.82rem;">实验列表加载失败</div>';
                return;
            }
            const exps = ret.data || [];
            if (select) {
                select.innerHTML = '';
                if (exps.length === 0) {
                    select.innerHTML = '<option value="">暂无实验</option>';
                } else {
                    exps.forEach(e => {
                        const opt = document.createElement('option');
                        opt.value = e.id;
                        opt.textContent = '#' + e.id + ' ' + (e.name || '') + ' (' + (e.status || '') + ')';
                        select.appendChild(opt);
                    });
                }
            }
            if (exps.length === 0) {
                container.innerHTML = '<div style="color:var(--text-muted); font-size:0.82rem;">暂无 A/B 实验。可在灰测开启后创建实验，样本回流后此处展示显著性卡片。</div>';
                return;
            }
            loadPValueCardsFor(Number(select.value || exps[0].id));
        })
        .catch(err => {
            console.error('实验列表加载失败', err);
            if (container) container.innerHTML = '<div style="color:#c00; font-size:0.82rem;">实验列表加载失败</div>';
        });
}

function loadPValueCardsFor(expId) {
    const container = document.getElementById('pvalueCardsContainer');
    if (!container || !expId) return;
    fetch('/api/admin/experiments/' + Number(expId) + '/pvalue')
        .then(res => Promise.all([res.status, res.json().catch(() => null)]))
        .then(([httpStatus, ret]) => {
            if (!ret || ret.status !== 'success') {
                if (toastHttpError(httpStatus, ret)) return;
                container.innerHTML = '<div style="color:#c00; font-size:0.82rem;">p-value 加载失败</div>';
                return;
            }
            renderPValueCards(ret.cards || [], !!ret.low_confidence);
        })
        .catch(err => {
            console.error('p-value 加载失败', err);
            if (container) container.innerHTML = '<div style="color:#c00; font-size:0.82rem;">p-value 加载失败</div>';
        });
}

function renderPValueCards(cards, lowConfidence) {
    const container = document.getElementById('pvalueCardsContainer');
    if (!container) return;
    const METRIC_LABELS = { accuracy: '准确率', hallucination_rate: '幻觉率', edit_rate: '人工修改率' };
    let html = '';
    cards.forEach(c => {
        const sig = c.significant;
        const sigBadge = (sig == null)
            ? '<span class="badge badge-secondary">无法判定</span>'
            : (sig
                ? '<span class="badge badge-success">显著 (p &lt; ' + PVALUE_ALPHA + ')</span>'
                : '<span class="badge badge-warning">不显著 (p >= ' + PVALUE_ALPHA + ')</span>');
        const pText = (c.p_value == null) ? '-' : Number(c.p_value).toFixed(4);
        const zText = (c.z == null) ? '-' : Number(c.z).toFixed(3);
        const effText = (c.effect_size_pp == null) ? '-' : ((c.effect_size_pp > 0 ? '+' : '') + Number(c.effect_size_pp).toFixed(2) + 'pp');
        html += '<div style="border:1px solid var(--border-color); border-radius:10px; padding:10px 12px; background:var(--bg-main);">'
            + '<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">'
            + '<strong style="font-size:0.85rem;">' + w2Escape(METRIC_LABELS[c.metric] || c.metric) + '</strong>' + sigBadge
            + '</div>'
            + '<div style="font-size:0.78rem; color:var(--text-muted); line-height:1.7;">'
            + 'p-value：<strong style="color:var(--text-main);">' + pText + '</strong><br>'
            + 'z 分数：' + zText + '　效应量：' + effText + '<br>'
            + '阈值：alpha=' + PVALUE_ALPHA + '（双侧）' + (lowConfidence ? '｜<span style="color:#b8860b;">低置信度（N&lt;' + PVALUE_MIN_SAMPLE + '）</span>' : '')
            + '</div>'
            + '</div>';
    });
    container.innerHTML = html;
}

// =====================================================================
// F-P1-5 成本分摊（归档弹窗）：整单金额按部门比例分摊到明细行部门
// =====================================================================
let costShareRows = [];

function openCostShareModal() {
    const totalAmt = parseFloat((document.getElementById('arcTotal') || {}).value) || 0;
    if (totalAmt <= 0) {
        showToast('本单总额为 0，无可分摊成本', 'warning');
        return;
    }
    costShareRows = [];
    // 默认两行：当前单据级部门 + 未分配
    const deptSel = document.getElementById('arcDepartmentId');
    const curDept = deptSel && deptSel.value ? Number(deptSel.value) : '';
    addCostShareRow(curDept, 70);
    addCostShareRow('', 30);
    renderCostShareRows();
    recalcCostShareSummary();
    const modal = document.getElementById('costShareModal');
    if (modal) modal.classList.remove('hide');
}

function addCostShareRow(deptId, percent) {
    costShareRows.push({ dept_id: deptId != null ? String(deptId) : '', percent: (percent != null ? Number(percent) : 0) });
}

function removeCostShareRow(idx) {
    costShareRows.splice(Number(idx), 1);
    renderCostShareRows();
    recalcCostShareSummary();
}

function equalizeCostShare() {
    const n = costShareRows.length;
    if (n === 0) return;
    const base = Math.floor(100 / n);
    let remainder = 100 - base * n;
    costShareRows.forEach(r => {
        r.percent = base + (remainder > 0 ? 1 : 0);
        if (remainder > 0) remainder--;
    });
    renderCostShareRows();
    recalcCostShareSummary();
}

function onCostShareInput(idx, field, value) {
    const row = costShareRows[Number(idx)];
    if (!row) return;
    if (field === 'percent') row.percent = parseFloat(value) || 0;
    else row.dept_id = String(value || '');
    recalcCostShareSummary();
}

function renderCostShareRows() {
    const body = document.getElementById('costShareBody');
    if (!body) return;
    let html = '';
    costShareRows.forEach((r, i) => {
        html += '<div style="display:flex; gap:8px; align-items:center; margin-bottom:8px;">'
            + '<select class="form-control" style="flex:1;" onchange="onCostShareInput(' + i + ', \'dept\', this.value)">'
            + deptSelectOptionsHtml(r.dept_id)
            + '</select>'
            + '<input type="number" class="form-control" style="width:90px;" min="0" max="100" step="0.1" value="' + r.percent + '" oninput="onCostShareInput(' + i + ', \'percent\', this.value)">'
            + '<span style="font-size:0.8rem; color:var(--text-muted);">%</span>'
            + '<button class="btn btn-danger" style="padding:2px 8px; font-size:0.75rem;" onclick="removeCostShareRow(' + i + ')">删除</button>'
            + '</div>';
    });
    body.innerHTML = html || '<div style="color:var(--text-muted); font-size:0.82rem;">暂无分摊行，请点击「增加部门」</div>';
}

function recalcCostShareSummary() {
    const totalAmt = parseFloat((document.getElementById('arcTotal') || {}).value) || 0;
    const sum = costShareRows.reduce((acc, r) => acc + (Number(r.percent) || 0), 0);
    const el = document.getElementById('costShareSummary');
    if (!el) return;
    const parts = costShareRows.map(r => {
        const amt = totalAmt * (Number(r.percent) || 0) / 100;
        const sel = document.querySelector('#costShareBody select');
        void sel;
        return (r.dept_id ? ('部门#' + r.dept_id) : '未分配') + ' ' + r.percent + '% = ' + currencySymbol(currentCurrencyCode()) + amt.toFixed(2);
    });
    el.textContent = '合计 ' + sum.toFixed(1) + '%' + (Math.abs(sum - 100) < 0.05 ? '' : '（警告：比例合计应为 100%）') + (parts.length ? ' ｜ ' + parts.join('，') : '');
}

function applyCostShare() {
    const sum = costShareRows.reduce((acc, r) => acc + (Number(r.percent) || 0), 0);
    if (costShareRows.length === 0) { showToast('请先添加分摊行', 'warning'); return; }
    if (Math.abs(sum - 100) > 0.05) { showToast('分摊比例合计须为 100%，当前 ' + sum.toFixed(1) + '%', 'error'); return; }

    const tbody = document.getElementById('arcTableBody');
    const rows = Array.from(tbody.querySelectorAll('tr')).filter(tr =>
        !(tr.dataset && tr.dataset.isVoid === '1'));
    if (rows.length === 0) { showToast('无有效明细行可分摊', 'warning'); return; }

    // 按比例把行分配到各部门（大百分比优先占满），保证每行都有归属且不重复
    const assignments = [];
    costShareRows.forEach(r => {
        const count = Math.round(rows.length * (Number(r.percent) || 0) / 100);
        for (let k = 0; k < count; k++) assignments.push(r.dept_id);
    });
    while (assignments.length < rows.length) assignments.push(costShareRows[0] ? costShareRows[0].dept_id : '');
    rows.forEach((tr, i) => {
        const sel = tr.querySelector('.inp-dept');
        if (sel) sel.value = assignments[i] || '';
    });
    markArcDirty();
    closeModalById('costShareModal');
    showToast('已按比例将 ' + rows.length + ' 行明细分摊至各部门，保存后生效', 'success', TOAST_DURATION.long);
}

// U-12：全局挂载辅助函数与并发锁
if (typeof window !== 'undefined') {
    window.handleFileSelect = handleFileSelect;
    window.handleSingleUploadSelection = handleSingleUploadSelection;
    window.handleFilesSelect = handleFilesSelect;
    window.showImagePrepIndicator = showImagePrepIndicator;
    window.queueFilesSelect = queueFilesSelect;
}

