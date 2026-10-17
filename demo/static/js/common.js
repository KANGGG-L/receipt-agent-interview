// demo/static/js/common.js
// 公共工具：双模态/确认/Prompt 组件、转义与图片预览 helpers、鉴权 fetch 包装与
// 登录面板、埋点 track、全局 Toast 与错误人性化、Tab 切换、可拖拽分栏（Category 04）、
// demo RBAC 角色切换与可访问名扫描。
// F5·S4-11 从 main.js 按域抽离，逐字节一致；加载顺序在 main.js 之后（apiFetch
// 装配先于 DOMContentLoaded 的各数据拉取，语义与原位一致）。
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

// AC-06-04: 切换单据前如有未保存修改，给「保存 / 保留本地(留在当前) / 放弃并切换」三种
// 可执行语义，而不是静默丢弃。返回 true 表示已呈现守卫弹窗（调用方应放弃本次切换）。
function showUnsavedChangesGuard({ onSave, onDiscard, receiptLabel }) {
    const overlay = document.createElement('div');
    overlay.className = 'modal-backdrop custom-modal-overlay';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.style.cssText = 'position:fixed; top:0; left:0; width:100vw; height:100vh; background:rgba(0,0,0,0.5); z-index:999999; display:flex; align-items:center; justify-content:center;';

    const card = document.createElement('div');
    card.className = 'card';
    card.style.cssText = 'width:460px; max-width:92vw; background:var(--card-bg, #fff); border-radius:10px; padding:20px; box-shadow:0 8px 24px rgba(0,0,0,0.25);';

    const titleEl = document.createElement('h4');
    titleEl.style.cssText = 'margin:0 0 10px 0; font-size:1.1rem; color:var(--text-main, #333); font-weight:600;';
    titleEl.textContent = '当前单据有未保存的修改';
    card.appendChild(titleEl);

    const msgEl = document.createElement('div');
    msgEl.style.cssText = 'font-size:0.9rem; color:var(--text-main, #444); margin-bottom:18px; line-height:1.5;';
    msgEl.textContent = '切换到' + (receiptLabel || '其他单据') + '会丢失这些修改。请选择：';
    card.appendChild(msgEl);

    const btnRow = document.createElement('div');
    btnRow.style.cssText = 'display:flex; justify-content:flex-end; gap:10px; flex-wrap:wrap;';

    const keepBtn = document.createElement('button');
    keepBtn.type = 'button';
    keepBtn.id = 'unsavedGuardKeep';
    keepBtn.className = 'btn btn-secondary';
    keepBtn.style.cssText = 'min-height:40px; padding:8px 14px; cursor:pointer;';
    keepBtn.textContent = '保留本地修改 (留在当前单据)';

    const discardBtn = document.createElement('button');
    discardBtn.type = 'button';
    discardBtn.id = 'unsavedGuardDiscard';
    discardBtn.className = 'btn btn-danger';
    discardBtn.style.cssText = 'min-height:40px; padding:8px 14px; cursor:pointer;';
    discardBtn.textContent = '放弃修改并切换';

    const saveBtn = document.createElement('button');
    saveBtn.type = 'button';
    saveBtn.id = 'unsavedGuardSave';
    saveBtn.className = 'btn btn-primary';
    saveBtn.style.cssText = 'min-height:40px; padding:8px 14px; cursor:pointer;';
    saveBtn.textContent = '保存后再切换';

    btnRow.appendChild(keepBtn);
    btnRow.appendChild(discardBtn);
    btnRow.appendChild(saveBtn);
    card.appendChild(btnRow);
    overlay.appendChild(card);
    document.body.appendChild(overlay);

    const keyHandler = (e) => {
        if (e.key === 'Escape') {
            e.preventDefault();
            keepBtn.click();
        }
    };
    window.addEventListener('keydown', keyHandler);

    function close() {
        window.removeEventListener('keydown', keyHandler);
        if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay);
    }

    keepBtn.onclick = (e) => { e.preventDefault(); close(); };
    discardBtn.onclick = (e) => {
        e.preventDefault();
        close();
        if (typeof onDiscard === 'function') onDiscard();
    };
    saveBtn.onclick = (e) => {
        e.preventDefault();
        close();
        if (typeof onSave === 'function') onSave();
    };

    keepBtn.focus();
    return true;
}
window.showUnsavedChangesGuard = showUnsavedChangesGuard;

function showCustomPromptModal({ title, message, defaultValue = '', placeholder = '', maxLength = 20, confirmText = '确定', cancelText = '取消', onConfirm, onCancel }) {
    const overlay = document.createElement('div');
    overlay.className = 'modal-backdrop custom-modal-overlay';
    overlay.style.cssText = 'position:fixed; top:0; left:0; width:100vw; height:100vh; background:rgba(0,0,0,0.5); z-index:999999; display:flex; align-items:center; justify-content:center;';

    const card = document.createElement('div');
    card.className = 'card';
    card.style.cssText = 'width:420px; max-width:90vw; background:var(--card-bg, #fff); border-radius:10px; padding:20px; box-shadow:0 8px 24px rgba(0,0,0,0.25);';

    const titleEl = document.createElement('h4');
    titleEl.style.cssText = 'margin:0 0 10px 0; font-size:1.1rem; color:var(--text-main, #333); font-weight:600;';
    titleEl.textContent = title || '请输入';
    card.appendChild(titleEl);

    if (message) {
        const msgEl = document.createElement('div');
        msgEl.style.cssText = 'font-size:0.88rem; color:var(--text-muted, #666); margin-bottom:12px; white-space:pre-wrap; line-height:1.5; word-break:break-word;';
        msgEl.textContent = message;
        card.appendChild(msgEl);
    }

    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'form-control';
    input.value = defaultValue || '';
    input.placeholder = placeholder || '';
    if (maxLength) input.maxLength = maxLength;
    input.style.cssText = 'width:100%; box-sizing:border-box; margin-bottom:18px; padding:8px 12px; font-size:0.9rem;';
    card.appendChild(input);

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

    input.focus();
    input.select();

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
        const val = input.value;
        close();
        if (typeof onConfirm === 'function') onConfirm(val);
    };
}
// ---- 转义小工具（全文件共用；函数声明提升，供全文调用） ----
function w2Escape(s) {
    return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// 本地日期（YYYY-MM-DD）：避免 toISOString() 取 UTC 日期导致 UTC+8 凌晨 0:00-8:00
// 「今天/昨天」错位一天；一律用本地 getFullYear/getMonth+1/getDate 补零。
function fmtLocalDateStr(d) {
    const dt = d ? new Date(d) : new Date();
    const y = dt.getFullYear();
    const m = String(dt.getMonth() + 1).padStart(2, '0');
    const day = String(dt.getDate()).padStart(2, '0');
    return y + '-' + m + '-' + day;
}
function todayLocalStr() {
    return fmtLocalDateStr(new Date());
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
    // 无扩展名的取图 URL（签名端点 /api/receipt/{id}/image?v=...&sig=...）兜底：
    // 无明确非 web 后缀则允许尝试
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
    return JSON.stringify(String(v ?? '')).replace(/&/g, '\\u0026').replace(/</g, '\\u003c').replace(/>/g, '\\u003e').replace(/'/g, '\\u0027');
}

// =====================================================================
// P1-12：前端鉴权集成（AUTH_ENABLED=1 可用，R4）
//   - 启动探测 GET /api/auth/me：auth_enabled=false 或 200 → 正常进入；401 → 登录面板
//   - 身份头（X-Role + Authorization）由 attachAuthInit 显式合入 init，
//     经 apiFetch 发出：全局 /api/ 调用的唯一出口。
//   - window.fetch 包装仅作未迁移调用点（其他脚本/第三方）的兜底，
//     不可依赖：任何后加载脚本重新赋值 window.fetch 都会剥掉隐式注入。
//   - 任何 /api/ 响应再 401 → 回登录面板
// 纯函数（buildAuthHeaders/attachAuthInit/decideAuthProbe/createAuthFetchWrapper）
// 供 demo/tests/frontend_auth_headers_test.js 在 node vm 中直接断言。
// =====================================================================
const AUTH_TOKEN_KEY = 'receipt_auth_token';
const AUTH_EXEMPT_FRONTEND = ['/api/auth/login', '/api/auth/register'];

const AuthState = {
    enabled: false,          // 服务端是否开启鉴权（/api/auth/me 的 auth_enabled）
    account: null,           // 当前账号信息
    loginPanelShowing: false,
};

function getAuthToken() {
    return null;
}
function setAuthToken(_token) {
    try { localStorage.removeItem(AUTH_TOKEN_KEY); } catch (e) {}
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

// 当前角色名（admin / owner / staff）：优先 /me 回填的服务端角色，其次 demo_role
function currentRoleName() {
    const r = AuthState && AuthState.account && AuthState.account.role;
    if (r) return String(r);
    try { return localStorage.getItem('demo_role') || 'admin'; } catch (e) { return 'admin'; }
}

// 纯函数：给定 url 与 token，返回应注入的请求头对象；不注入时返回 null
function buildAuthHeaders(url, token) {
    if (!isAuthInjectableUrl(url)) return null;
    const headers = {};
    // demo 无密码 RBAC：始终附带当前角色（select 下拉切换，存 localStorage）
    const demoRole = (() => { try { return localStorage.getItem('demo_role'); } catch (e) { return null; } })();
    const role = (demoRole === 'admin' || demoRole === 'owner' || demoRole === 'staff') ? demoRole : 'owner';
    headers['X-Role'] = role;
    headers['X-Requested-With'] = 'fetch';
    if (token) headers['Authorization'] = 'Bearer ' + token;
    return headers;
}

// 纯函数：把 Authorization 附加到 fetch init（不修改传入对象，已有显式
// Authorization 时尊重调用方不覆盖）
function attachAuthInit(init, url, token) {
    const extra = buildAuthHeaders(url, token);
    if (!extra) return init;
    const headers = (init && init.headers) || {};
    // Headers 实例：就地逐项补充（其内容本就属于本次请求构造）
    if (typeof Headers !== 'undefined' && headers instanceof Headers) {
        Object.keys(extra).forEach(key => {
            if (extra[key] != null && !headers.has(key)) headers.set(key, extra[key]);
        });
        return Object.assign({}, init || {}, { headers: headers });
    }
    // 普通对象：调用方显式给出的 Authorization 不覆盖，但其余身份头（X-Role）
    // 必须补上——早期实现在此整体 return init，角色头会随之丢失。
    const merged = Object.assign({}, extra);
    const keys = Object.keys(headers);
    for (let i = 0; i < keys.length; i++) {
        if (String(keys[i]).toLowerCase() === 'authorization') delete merged.Authorization;
    }
    return Object.assign({}, init || {}, { headers: Object.assign({}, headers, merged) });
}

// 纯工厂：包装 originalFetch 返回新 fetch——/api/ 注入令牌；/api/ 响应 401
// 触发 onUnauthorized（豁免路径除外：登录失败自身的 401 不回弹面板）
function createAuthFetchWrapper(originalFetch, opts) {
    const getToken = (opts && opts.getToken) || getAuthToken;
    const onUnauthorized = (opts && opts.onUnauthorized) || null;
    return function authWrappedFetch(input, init) {
        const url = typeof input === 'string' ? input : ((input && input.url) || '');
        let finalInit = attachAuthInit(init, url, getToken());
        if (isApiUrl(url)) {
            const base = finalInit || {};
            if (base.credentials == null) {
                finalInit = Object.assign({}, base, { credentials: 'same-origin' });
            }
        }
        const method = String((finalInit && finalInit.method) || 'GET').toUpperCase();
        return originalFetch(input, finalInit).then(res => {
            if (method !== 'GET' && method !== 'HEAD' && res && res.ok) {
                try {
                    const scope = (typeof window !== 'undefined') ? window : null;
                    if (scope && typeof scope.__invalidateReceiptList === 'function') scope.__invalidateReceiptList();
                } catch (e) {}
            }
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
// 脚本加载时捕获的原始 fetch：apiFetch 走它，不经 window.fetch，
// 因此后续被第三方脚本重新赋值 window.fetch 时身份头仍不会被剥掉。
const NATIVE_FETCH = (() => {
    const scope = (typeof window !== 'undefined') ? window : globalThis;
    return (scope && typeof scope.fetch === 'function') ? scope.fetch.bind(scope) : null;
})();

(function installAuthFetch() {
    const scope = (typeof window !== 'undefined') ? window : globalThis;
    if (!scope || !NATIVE_FETCH) return;
    scope.fetch = createAuthFetchWrapper(NATIVE_FETCH, {
        getToken: getAuthToken,
        onUnauthorized: () => showLoginPanel(),
    });
})();

// main.js 内所有 /api/ 调用的唯一出口：身份头（X-Role / Authorization）经
// attachAuthInit 显式合入 init，再由脚本加载时捕获的 NATIVE_FETCH 发出。
// 与 window.fetch 包装的区别只在于「不经全局」——后者可被任何后加载脚本覆盖。
const apiFetch = NATIVE_FETCH
    ? createAuthFetchWrapper(NATIVE_FETCH, {
        getToken: getAuthToken,
        onUnauthorized: () => showLoginPanel(),
    })
    : function (url, init) { return window.fetch(url, init); };
window.apiFetch = apiFetch;

// ---- 埋点（11-组件Spec-全链路埋点与体验反馈体系）----
// fire-and-forget：走 apiFetch 显式带 X-Role；keepalive 允许卸载期发送；失败静默
let parseStartTs = 0;

// Spec §3.0 公共上下文字段：会话标识首次生成后 localStorage 持久化，之后每次上报携带
function _demoSessionId() {
    try {
        let sid = localStorage.getItem('demo_session_id');
        if (!sid) {
            let rand = Math.random().toString(36).slice(2, 8);
            while (rand.length < 6) rand += '0';
            sid = 's_' + Date.now().toString(36) + '_' + rand;
            localStorage.setItem('demo_session_id', sid);
        }
        return sid;
    } catch (e) {
        // localStorage 不可用（隐私模式等）：退化为每次临时生成，不阻塞埋点
        let rand = Math.random().toString(36).slice(2, 8);
        while (rand.length < 6) rand += '0';
        return 's_' + Date.now().toString(36) + '_' + rand;
    }
}

function track(eventType, receiptId, properties) {
    try {
        apiFetch('/api/track', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                event_type: eventType,
                receipt_id: receiptId || null,
                session_id: _demoSessionId(),
                properties: properties || {},
            }),
            keepalive: true,
        }).catch(() => {});
    } catch (e) { /* 埋点永不阻塞业务 */ }
}
window.track = track;

// 纯函数：/api/auth/me 探测结果的进入决策
function decideAuthProbe(httpStatus, body) {
    if (httpStatus === 401) return 'login';
    // /api/auth/me 在鉴权白名单内（匿名恒 200）——只按 2xx 放行的话，「未登录 → 面板」会
    // 退化成依赖后续首屏数据端点 401 的时序。故首屏判定**直接**用 /me 已回显的
    // auth_enabled + account：鉴权已开启却没有身份 → 判「需要登录」。
    // auth_enabled=false（开发态）不受影响——免登录直通语义不变。
    if (httpStatus >= 200 && httpStatus < 300) {
        const b = body || {};
        if (b.auth_enabled && !b.account) return 'login';
    }
    // 其他状态（含网络层已 catch）不阻断进入，后端异常会自行暴露
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

// UAT-010：生产会话可见退出登录入口
// - 仅 AUTH_ENABLED=1 且已登录时显示按钮；demo 角色下拉不是生产注销
// - 取消确认 / 网络失败不得假退出（不清 AuthState、不弹登录面板）
// - 成功后清客户端凭据键、吊销服务端会话，再显示登录面板
let _authLogoutInFlight = false;
let _authLogoutBtnBound = false;

function clearClientAuthCredentials() {
    // 生产会话凭据在 HttpOnly Cookie；本地仅清历史 Bearer 键，绝不写入密码/token。
    try { localStorage.removeItem(AUTH_TOKEN_KEY); } catch (e) {}
    try { sessionStorage.removeItem(AUTH_TOKEN_KEY); } catch (e) {}
    setAuthToken(null);
}

function storageHasAuthCredentials() {
    try {
        if (localStorage.getItem(AUTH_TOKEN_KEY)) return true;
    } catch (e) {}
    try {
        if (sessionStorage.getItem(AUTH_TOKEN_KEY)) return true;
    } catch (e) {}
    return false;
}

function syncAuthChrome() {
    const loggedIn = !!(AuthState.enabled && AuthState.account);
    const logoutWrap = document.getElementById('authLogoutWrap');
    const logoutBtn = document.getElementById('authLogoutBtn');
    if (logoutWrap) logoutWrap.style.display = loggedIn ? '' : 'none';
    if (logoutBtn) {
        logoutBtn.style.display = loggedIn ? '' : 'none';
        logoutBtn.disabled = !!_authLogoutInFlight;
        if (!_authLogoutBtnBound) {
            _authLogoutBtnBound = true;
            logoutBtn.addEventListener('click', requestAuthLogout);
        }
    }
    // 生产鉴权开启时隐藏 demo 角色下拉，避免把「切换角色」误当成退出登录
    const roleSwitch = document.getElementById('demoRoleSwitch');
    if (roleSwitch) {
        roleSwitch.style.display = AuthState.enabled ? 'none' : '';
    }
}

function requestAuthLogout() {
    if (!AuthState.enabled) return; // demo 免密模式无生产注销语义
    if (_authLogoutInFlight) return;
    showCustomConfirmModal({
        title: '退出登录',
        message: '确定退出当前账号吗？退出后需要重新登录才能继续操作。',
        confirmText: '退出登录',
        cancelText: '取消',
        onConfirm: function () { performAuthLogout(); },
        onCancel: function () { /* 取消：保持会话，不假退出 */ }
    });
}

function performAuthLogout() {
    if (_authLogoutInFlight) return;
    _authLogoutInFlight = true;
    syncAuthChrome();
    const btn = document.getElementById('authLogoutBtn');
    if (btn) btn.disabled = true;

    apiFetch('/api/auth/logout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
        credentials: 'same-origin',
        body: '{}'
    })
    .then(function (res) {
        return Promise.all([res.status, res.json().catch(function () { return null; })]);
    })
    .then(function (pair) {
        const httpStatus = pair[0];
        const body = pair[1];
        _authLogoutInFlight = false;
        if (httpStatus >= 200 && httpStatus < 300 && body && body.status === 'success') {
            clearClientAuthCredentials();
            AuthState.account = null;
            syncAuthChrome();
            showLoginPanel('已退出登录');
            if (typeof showToast === 'function') showToast('已退出登录', 'info');
            return;
        }
        // 非成功：不清除本地会话态，避免假退出
        syncAuthChrome();
        const msg = (body && body.msg) ? body.msg : ('退出失败（HTTP ' + httpStatus + '）');
        if (typeof showToast === 'function') showToast(msg, 'error');
    })
    .catch(function (err) {
        _authLogoutInFlight = false;
        syncAuthChrome();
        console.error('退出登录请求异常', err);
        if (typeof showToast === 'function') {
            showToast('网络异常，未能退出。请检查网络后重试。', 'error');
        }
    });
}

window.requestAuthLogout = requestAuthLogout;
window.performAuthLogout = performAuthLogout;
window.syncAuthChrome = syncAuthChrome;
window.clearClientAuthCredentials = clearClientAuthCredentials;
window.storageHasAuthCredentials = storageHasAuthCredentials;

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

    apiFetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email, password: password })
    })
    .then(res => Promise.all([res.status, res.json().catch(() => null)]))
    .then(([httpStatus, ret]) => {
        if (btn) btn.disabled = false;
        if (httpStatus === 200 && ret && ret.status === 'success') {
            clearClientAuthCredentials();
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
    return apiFetch('/api/auth/me')
        .then(res => Promise.all([res.status, res.json().catch(() => null)]))
        .then(([httpStatus, body]) => {
            if (decideAuthProbe(httpStatus, body) === 'login') {
                clearClientAuthCredentials();
                AuthState.enabled = !!(body && body.auth_enabled);
                AuthState.account = null;
                syncAuthChrome();
                showLoginPanel('请先登录');
                return;
            }
            AuthState.enabled = !!(body && body.auth_enabled);
            AuthState.account = (body && body.account) || null;
            if (AuthState.account && AuthState.account.role) {
                if (typeof window.setRole === 'function') {
                    window.setRole(AuthState.account.role);
                }
            }
            const banner = document.getElementById('demoModeBanner');
            if (banner) {
                banner.style.display = AuthState.enabled ? 'none' : 'block';
            }
            // 生产 Cookie 会话：确保 localStorage 无残留 Bearer；同步退出按钮可见性
            clearClientAuthCredentials();
            syncAuthChrome();
            hideLoginPanel();
            syncManualEntryVisibility();   // D12：按角色显隐「新建手工单」入口
            // QA-P1-03: 登录后强制刷新。首屏探测成功时复用已经发出的列表，避免同一页打两次 /api/receipts。
            if (typeof loadWorkbenchQueue === 'function') {
                loadWorkbenchQueue(!!reloadData);
            }
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

// -------------------------------------------------------------
// 1. Tab 切换与自动设置
// -------------------------------------------------------------
function initTabs() {
    const sidebarBtns = document.querySelectorAll('.sidebar-btn, .nav-tab');
    sidebarBtns.forEach(btn => {
        btn.addEventListener('click', (e) => {
            const targetId = btn.getAttribute('data-target');

            // 导航守卫 (Navigation Guard)：离开 tab-engine 且存在未保存草稿时拦截确认
            const activeTab = document.querySelector('.tab-content.active');
            if (activeTab && activeTab.id === 'tab-engine' && targetId !== 'tab-engine' && typeof _engineIsDirty !== 'undefined' && _engineIsDirty) {
                showCustomConfirmModal({
                    title: '存在未保存的配置修改',
                    message: '您在系统与引擎配置中有未保存的修改。离开此页面将丢失修改，是否确定离开？',
                    confirmText: '确定离开',
                    cancelText: '留在本页',
                    onConfirm: () => {
                        _engineIsDirty = false;
                        btn.click();
                    }
                });
                return;
            }

            sidebarBtns.forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));

            btn.classList.add('active');
            const navTitle = btn.getAttribute('data-title') || btn.innerText;

            document.getElementById(targetId).classList.add('active');

            const bcTitle = document.getElementById('bcActiveTitle');
            if (bcTitle) {
                bcTitle.removeAttribute('data-i18n');
                const labelEl = btn.querySelector('[data-i18n]');
                const labelText = labelEl ? (labelEl.textContent || '').replace(/\s+/g, ' ').trim() : '';
                bcTitle.innerText = labelText || navTitle;
            }

            if (targetId === 'tab-inventory') loadInventoryData();
            if (targetId === 'tab-dishes') initDishTab(); // Task 4: 餐品与消耗管理 Tab 激活加载
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
            if (targetId === 'tab-export') {
                if (typeof initExportCenter === 'function') initExportCenter();
            }
            if (targetId === 'tab-engine') {
                loadAdminEngineConfig();
                loadAdminSettings();   // T10：系统配置（阈值规则 + 预处理纠偏开关）
                loadMemoryMetrics();   // P2-T5：记忆度量看板（admin 只读，四项指标 + 总开关状态）
                loadVendorMemoryGovernance();   // P8/AC2：已生效记忆治理区（只读 + 调已有删除端点）
                initEngineSubViewOnTabEnter();
            }
            if (targetId === 'tab-analytics') {
                loadAnalyticsBoard();    // 观测中心（按当前活跃子面板条件调度）
            }
            if (typeof window.onAppTabActivated === 'function') window.onAppTabActivated(targetId);
            syncFeedbackVisibility();
            syncRecoveryVisibility();
        });
    });

    window.addEventListener('hashchange', () => {
        const engineTab = document.getElementById('tab-engine');
        if (engineTab && engineTab.classList.contains('active')) {
            const targetSub = (typeof getEngineSubViewFromUrl === 'function') ? getEngineSubViewFromUrl() : '01';
            if (typeof switchEngineSubView === 'function') {
                switchEngineSubView(targetSub, { scroll: false, updateHash: false });
            }
        }
    });
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

function clearAllToasts(immediate = true) {
    for (const key of Array.from(_toastLive.keys())) {
        _toastDismiss(key, immediate);
    }
}
window.clearAllToasts = clearAllToasts;

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
    if (raw.includes('权限不足')) return raw.replace(/[（(](staff|owner|admin)[）)]/gi, '').replace(/\b(staff|owner|admin)\b/gi, '');
    if (status != null && status !== 403) return null;
    if (raw.includes('仅 admin')) {
        return '这个操作只有系统管理员能做，请联系管理员。';
    }
    if (raw && /[\u4e00-\u9fff]/.test(raw) && !/[A-Za-z]{3,}/.test(raw)) return '权限不足：' + raw + '。请联系老板处理。';
    return '你的角色不能做这个操作，请联系老板处理。';
}

// U-10 文案去技术化：将 Pydantic 422 / 后端 400 / 500 / 堆栈错误翻译为店员人话
function humanizeBackendError(status, body) {
    if (status === 403) {
        const msg = (body && (body.detail || body.msg)) || '';
        return humanizeForbidden(msg, status);
    }
    // S1-P2-02：后端错误码统一映射成人话（换算缺失保留后端带单位名的原句）
    if (body && body.code && typeof TERMS !== 'undefined') {
        const _c = String(body.code).toUpperCase();
        if (_c !== 'UNIT_CONVERSION_MISSING' && (TERMS.codeText[_c] || body.msg)) {
            return TERMS.humanizeCode(_c, body.msg);
        }
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
    if (body && body.code && typeof TERMS !== 'undefined') {
        return TERMS.humanizeCode(body.code, raw);
    }
    if (raw && typeof TERMS !== 'undefined' && !/[\u4e00-\u9fff]/.test(raw) && /[A-Za-z]{3,}/.test(raw)) {
        return TERMS.genericError;
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

// =============================================================
// Category 04: 可拖拽分栏与窄屏切换（AC-04-01 至 AC-04-03）
// =============================================================

function initSplitViewDivider() {
    const divider = document.getElementById('splitViewDivider');
    const leftCard = document.getElementById('leftCard');
    const splitArea = document.getElementById('splitViewArea');
    if (!divider || !leftCard || !splitArea) return;

    // 恢复持久化分栏比例
    try {
        const savedRatio = parseFloat(localStorage.getItem('receiptSplitRatio'));
        if (!isNaN(savedRatio) && savedRatio >= 0.25 && savedRatio <= 0.75) {
            leftCard.style.flex = `0 0 ${(savedRatio * 100).toFixed(1)}%`;
            leftCard.style.width = `${(savedRatio * 100).toFixed(1)}%`;
        }
    } catch(e){}

    let isDragging = false;

    divider.addEventListener('mousedown', (e) => {
        isDragging = true;
        divider.classList.add('dragging');
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
    });

    document.addEventListener('mousemove', (e) => {
        if (!isDragging) return;
        const rect = splitArea.getBoundingClientRect();
        const offsetX = e.clientX - rect.left;
        let ratio = offsetX / rect.width;
        // 夹紧在 25% ~ 75% 之间，防止面板消失 (AC-04-03)
        ratio = Math.max(0.25, Math.min(0.75, ratio));

        leftCard.style.flex = `0 0 ${(ratio * 100).toFixed(1)}%`;
        leftCard.style.width = `${(ratio * 100).toFixed(1)}%`;
        try { localStorage.setItem('receiptSplitRatio', ratio.toFixed(3)); } catch(e){}
    });

    document.addEventListener('mouseup', () => {
        if (isDragging) {
            isDragging = false;
            divider.classList.remove('dragging');
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
        }
    });
}

let mobileScrollCache = { img: 0, form: 0 };
function switchMobileSplitView(mode) {
    const left = document.getElementById('leftCard');
    const right = document.getElementById('rightPanel');
    const btnImg = document.getElementById('btnShowMobileImg');
    const btnForm = document.getElementById('btnShowMobileForm');
    if (!left || !right) return;

    if (mode === 'img') {
        mobileScrollCache.form = right.scrollTop || window.scrollY;
        left.style.display = 'block';
        right.style.display = 'none';
        if (btnImg) btnImg.classList.add('active');
        if (btnForm) btnForm.classList.remove('active');
        setTimeout(() => { if (left.scrollTop !== undefined) left.scrollTop = mobileScrollCache.img; }, 10);
    } else {
        mobileScrollCache.img = left.scrollTop || window.scrollY;
        left.style.display = 'none';
        right.style.display = 'block';
        if (btnForm) btnForm.classList.add('active');
        if (btnImg) btnImg.classList.remove('active');
        setTimeout(() => { if (right.scrollTop !== undefined) right.scrollTop = mobileScrollCache.form; }, 10);
    }
}
window.switchMobileSplitView = switchMobileSplitView;
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
        const roleName = i18nLabel('role.' + role, role);
        showToast(i18nLabel('role.switched', '角色已切换为：{name}').replace('{name}', roleName), 'info');
        // 重探 /me 刷新 AuthState.account → isOwnerRole() 生效
        probeAuthAndEnter(true);
        // B1：即将整页重载，在途请求被浏览器中断属预期行为；
        // 置位后各加载器 catch 跳过 console.error，避免每次切换刷屏假错误
        window.__roleSwitchPending = true;
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

window.setRole = function(role) {
    const sel = document.getElementById('demoRoleSelect');
    if (sel) sel.value = role;
    try { localStorage.setItem('demo_role', role); } catch (e) {}
    applyDemoRoleColor(role);
};

// U-01：按角色控制 admin 专属导航入口可见性（API 层 owner/staff 均 403，前端同步隐藏）
// 店员隐藏部门花销报表（cost_report 为 owner 域接口，避免进入即 403 弹窗）
function applyRoleVisibility(role) {
    const isAdmin = role === 'admin';
    ['adminSidebarSection', 'adminEngineBtn', 'analyticsBoardBtn'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.display = isAdmin ? '' : 'none';
    });
    // 通用可见性收敛（class 声明，供静态模板使用；动态行内按钮由 render* 按 isOwnerRoleNow 直接不输出）：
    //   .admin-only → 仅超管可见（F1 RAG data_only 调试入口）
    //   .owner-only → 超管/老板可见、店员隐藏（F5 库存/供应商 owner 级工具条；后端均 require_role("owner")）
    // 记住模板原始内联 display（如 form-group 的 flex），恢复时回写原值，避免改坏布局。
    document.querySelectorAll('.admin-only').forEach(el => {
        if (el.dataset.baseDisplay === undefined) el.dataset.baseDisplay = el.style.display || '';
        el.style.display = isAdmin ? el.dataset.baseDisplay : 'none';
    });
    document.querySelectorAll('.owner-only').forEach(el => {
        if (el.dataset.baseDisplay === undefined) el.dataset.baseDisplay = el.style.display || '';
        el.style.display = (role === 'staff') ? 'none' : el.dataset.baseDisplay;
    });
    const reportBtn = document.querySelector('.sidebar-btn[data-target="tab-report"]');
    if (reportBtn) reportBtn.style.display = (role === 'staff') ? 'none' : '';
    // 店员隐藏数据导出中心（PRO 域，店员无导出场景）
    const exportBtn = document.querySelector('.sidebar-btn[data-target="tab-export"]');
    if (exportBtn) exportBtn.style.display = (role === 'staff') ? 'none' : '';
    // 当前停留在已隐藏的页签时回落到收据识别（admin 不受影响）
    // 评测中心（tab-analytics）为 admin 专属入口，黄金基准集与 GT 抽检确权均在其功能区 02/03 内
    // 店员只保留「识别复核」和「库存查看」：餐品成本、归档对账、部门花销、数据中心、设置、今日待办都隐藏
    ['tab-dishes', 'tab-archive'].forEach(t => {
        const b = document.querySelector('.sidebar-btn[data-target="' + t + '"]');
        if (b) b.style.display = (role === 'staff') ? 'none' : '';
    });
    if (document.body) document.body.dataset.role = role;
    const hiddenTabs = (role === 'staff') ? ['tab-engine', 'tab-report', 'tab-analytics', 'tab-export', 'tab-dishes', 'tab-archive', 'tab-settings', 'tab-today']
                     : (role === 'owner') ? ['tab-engine'] : [];
    const active = document.querySelector('.tab-content.active');
    if (active && hiddenTabs.indexOf(active.id) !== -1) {
        const scanBtn = document.querySelector('.sidebar-btn[data-target="tab-scan"]');
        if (scanBtn) scanBtn.click();
    }
    // WS1：按 class 收敛 owner-only / admin-only 元素（每次调用都执行）。
    // 先捕获 baseDisplay 原值再改写（=== undefined 守卫保证幂等，重复调用/角色切换不破坏恢复）。
    document.querySelectorAll('.admin-only').forEach(el => {
        if (el.dataset.baseDisplay === undefined) el.dataset.baseDisplay = el.style.display || '';
        el.style.display = (role === 'admin') ? el.dataset.baseDisplay : 'none';
    });
    document.querySelectorAll('.owner-only').forEach(el => {
        if (el.dataset.baseDisplay === undefined) el.dataset.baseDisplay = el.style.display || '';
        el.style.display = (role === 'staff') ? 'none' : el.dataset.baseDisplay;
    });
    applyModalRoleVisibility(role);
    applyDishRoleVisibility(role);
    if (typeof window.refreshMobileNav === 'function') window.refreshMobileNav();
}

// D-5：餐品域老板级按钮的视觉级隐藏。
// staff 视角隐藏「+ 新建餐品」「管理分类」；「冲销作废」「停用/删除」为动态渲染，
// 由渲染函数按 isOwnerRole() 直接不输出（见 renderDailyHistoryTable / renderDishCards / renderDishTable）。
function applyDishRoleVisibility(optionalRole) {
    const isStaff = optionalRole ? (optionalRole === 'staff') : isStaffRoleNow();
    ['btnOpenAddDishModal', 'btnManageDishCategories'].forEach(id => {
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
    if (dishLibraryCache && dishLibraryCache.length > 0) {
        filterDishBomLibrary();
    }
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


document.addEventListener('DOMContentLoaded', function () { ensureAccessibleNames(document); });
