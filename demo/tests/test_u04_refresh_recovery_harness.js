// U04 刷新找回 / 跨身份隔离 / 409 双方版本 —— node 验收脚手架。
// 在 node vm 沙箱里载入真实生产源码（upload.js / workbench_queue.js /
// version_conflict.js / photo_sider.js），零真实网络、零真实 DB：
//   - fetch/apiFetch 全部由本脚本注入的调度器接管；
//   - localStorage 是内存桶，可跨「刷新」复用（模拟同一浏览器重新加载）。
// 覆盖：
//   ① 刷新恢复：client task ref / 批次 manifest / 工作台快照按身份键找回，
//      含 receipt_id/job_id/request_key/文件名/状态/tenant/account；
//   ② 跨身份隔离：换账号/换租户看不到他人草稿；403 时草稿保留但本会话隔离；
//   ③ 409 冲突：本地与服务端双方版本号 + 字段级差异可见，本地编辑不被清空；
//   ④ 慢加载/无 AI：只有 ref 没有详情时仍保留服务端原图；
//   ⑤ 批量逐张 key 不合并、同 key 重试不重复建 ref。
// 用法（cwd 任意）：node demo/tests/test_u04_refresh_recovery_harness.js
// 退出码 0 = 全部通过；非 0 = 有断言失败。
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const JS_DIR = path.join(__dirname, '..', 'static', 'js');
const SOURCE_FILES = ['upload.js', 'workbench_queue.js', 'version_conflict.js', 'photo_sider.js'];
const SOURCES = SOURCE_FILES.map(f => ({
    name: f,
    src: fs.readFileSync(path.join(JS_DIR, f), 'utf8'),
}));

let pass = 0;
let fail = 0;
function ok(name, cond, detail) {
    if (cond) { pass++; console.log('PASS ' + name); }
    else { fail++; console.log('FAIL ' + name + ' -- ' + (detail || '')); }
}

// ---- 共享 localStorage（同一浏览器刷新后仍在；不同身份靠键分桶） ----
const store = { demo_tenant_id: 't-hk' };
const localStorageStub = {
    getItem: k => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: k => { delete store[k]; },
    clear: () => { Object.keys(store).forEach(k => delete store[k]); },
};

function mkEl(props) {
    return Object.assign({
        id: '', className: '', textContent: '', innerText: '', innerHTML: '', value: '',
        tagName: 'DIV', style: {}, dataset: {}, children: [], disabled: false,
        listeners: {}, offsetParent: null, parentElement: { style: {} },
        classList: {
            _s: new Set(),
            add(...c) { c.forEach(x => this._s.add(x)); },
            remove(...c) { c.forEach(x => this._s.delete(x)); },
            toggle(c, on) {
                if (on === undefined) { this._s.has(c) ? this._s.delete(c) : this._s.add(c); }
                else if (on) { this._s.add(c); } else { this._s.delete(c); }
            },
            contains(c) { return this._s.has(c); },
        },
        addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
        removeEventListener() {},
        appendChild(c) { this.children.push(c); return c; },
        insertBefore(c) { this.children.push(c); return c; },
        remove() {}, focus() {}, click() {},
        setAttribute(k, v) { this[k] = v; },
        getAttribute(k) { return Object.prototype.hasOwnProperty.call(this, k) ? this[k] : null; },
        closest() { return null; },
        querySelector() { return null; },
        querySelectorAll() { return []; },
        contains() { return false; },
        scrollIntoView() {},
        getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 100 }; },
    }, props || {});
}

function mkRes(status, body) {
    return {
        ok: status >= 200 && status < 300,
        status: status,
        json: () => Promise.resolve(body),
        blob: () => Promise.resolve(null),
        clone: () => mkRes(status, body),
    };
}

function defaultFetcher() {
    return Promise.reject(new Error('harness: offline'));
}

// 每个 runtime = 一次「页面加载」；localStorage 共享，身份由 opts 决定。
function createRuntime(opts) {
    opts = opts || {};
    const els = {};
    const documentStub = {
        readyState: 'complete',
        hidden: false,
        body: mkEl({ tagName: 'BODY' }),
        getElementById(id) {
            if (!els[id]) els[id] = mkEl({ id: id });
            return els[id];
        },
        querySelector() { return null; },
        querySelectorAll() { return []; },
        createElement(tag) { return mkEl({ tagName: String(tag || 'div').toUpperCase() }); },
        addEventListener() {},
        removeEventListener() {},
    };
    const toasts = [];
    const errors = [];
    const fetcher = opts.apiFetch || defaultFetcher;
    const ctx = {
        console: {
            log() {}, info() {}, debug() {}, warn() {},
            error(...a) { errors.push(a.map(String).join(' ')); },
        },
        setTimeout, clearTimeout, setInterval, clearInterval,
        localStorage: localStorageStub,
        document: documentStub,
        AuthState: { enabled: true, account: opts.account ? { email: opts.account } : null },
        apiFetch: (url, init) => fetcher(url, init),
        showToast: (msg, type) => toasts.push({ msg: msg, type: type }),
        escapeHtml: s => String(s == null ? '' : s),
        openAccessibleModal(modal) { this.__openedModal = modal; },
        confirm: () => true,
        fetchReceiptListShared: opts.fetchReceiptListShared || (() => Promise.reject(new Error('offline'))),
        cachedWorkbenchQueueData: null,
        currentWorkbenchQueueFilter: 'todo',
        currentRole: 'owner',
        currentReceiptId: null,
        currentReceiptData: null,
        renderEditForm() {},
        setMainPreview() {},   // common.js 的预览 helper（恢复路径只做 DOM 渲染）
        syncFeedbackVisibility() {},   // dish_bom.js 的显隐 helper
        syncRecoveryVisibility() {},
        collectReviewFormData: () => ({}),
        refreshReviewFormFromServer() {},
        submitSaveEdited() {},
    };
    ctx.window = ctx;
    ctx.self = ctx;
    ctx.globalThis = ctx;
    ctx.addEventListener = function () {};
    ctx.removeEventListener = function () {};
    ctx.__toasts = toasts;
    ctx.__errors = errors;
    ctx.__els = els;

    vm.createContext(ctx);
    SOURCES.forEach(item => {
        vm.runInContext(item.src, ctx, { filename: item.name });
    });
    return ctx;
}

// 访问脚本内 const 声明的变量（如 BatchUploader）需在同一 realm 里求值
function evalIn(ctx, code) {
    return vm.runInContext(code, ctx);
}

(async function main() {
    // =========================================================
    // ① 刷新恢复：client task ref（同身份重新加载后仍在）
    // =========================================================
    const ownerA = { account: 'owner-a@demo.hk' };
    const rtA1 = createRuntime(ownerA);
    rtA1.upsertClientTaskRef(rtA1.buildClientTaskRef({
        receipt_id: 101, job_id: 'job-101', request_key: 'rk-101',
        file_name: 'a.jpg', status: 'uploading', image_url: '/api/receipt/101/image',
    }));
    rtA1.upsertClientTaskRef(rtA1.buildClientTaskRef({
        receipt_id: 102, job_id: 'job-102', request_key: 'rk-102',
        file_name: 'b.jpg', status: 'error', image_url: '/api/receipt/102/image',
    }));
    rtA1.upsertClientTaskRef(rtA1.buildClientTaskRef({
        receipt_id: 103, request_key: 'rk-103', file_name: 'c.jpg', status: 'parsed',
    }));
    // 同 request_key 重试（同 ID 回放）：不应新增条目
    rtA1.upsertClientTaskRef(rtA1.buildClientTaskRef({
        receipt_id: 101, job_id: 'job-101', request_key: 'rk-101',
        file_name: 'a.jpg', status: 'parsed',
    }));

    const rtA2 = createRuntime(ownerA);   // 模拟刷新后重新加载
    const refs = rtA2.loadClientTaskRefs();
    ok('刷新恢复：同身份找回 3 条任务引用（同 key 不重复）', refs.length === 3, 'got ' + refs.length);
    const ref101 = refs.find(r => String(r.receipt_id) === '101') || {};
    ok('刷新恢复：任务引用含 receipt_id/job_id/request_key/文件名/状态/租户/账号',
        String(ref101.receipt_id) === '101'
        && ref101.job_id === 'job-101'
        && ref101.request_key === 'rk-101'
        && ref101.file_name === 'a.jpg'
        && ref101.status === 'parsed'
        && ref101.tenant === 't-hk'
        && ref101.account === 'owner-a@demo.hk',
        JSON.stringify(ref101));
    const restorable = rtA2.listRestorableClientTaskRefs();
    ok('刷新恢复：处理中/失败/待复核队列可恢复', restorable.length === 3,
        restorable.map(r => r.status).join(','));

    // 批量逐张不同 key（不同文件/供应商）各自独立，不误合并
    const rtBatch = createRuntime(ownerA);
    rtBatch.upsertClientTaskRef(rtBatch.buildClientTaskRef({
        receipt_id: 201, request_key: 'bk-1', file_name: 'vendor-a-1.jpg', status: 'parsed',
    }));
    rtBatch.upsertClientTaskRef(rtBatch.buildClientTaskRef({
        receipt_id: 202, request_key: 'bk-2', file_name: 'vendor-b-2.jpg', status: 'parsed',
    }));
    const batchRefs = rtBatch.loadClientTaskRefs().filter(r => r.request_key === 'bk-1' || r.request_key === 'bk-2');
    ok('批量：逐张 request_key 各自归属，不自动合并', batchRefs.length === 2,
        JSON.stringify(batchRefs.map(r => r.request_key)));
    const keyPhoto = { file: { name: 'same.jpg' } };
    const keyOnce = rtBatch.ensurePhotoUploadRequestKey(keyPhoto);
    const keyRetry = rtBatch.ensurePhotoUploadRequestKey(keyPhoto);
    keyPhoto.file = { name: 'cropped.jpg' };
    const keyAfterReplace = rtBatch.ensurePhotoUploadRequestKey(keyPhoto);
    ok('批量：同文件重试复用同 key，换文件即换 key（不误判 409）',
        keyOnce === keyRetry && keyOnce !== keyAfterReplace
        && rtBatch.buildClientTaskRef({ request_key: keyOnce }).request_key === keyOnce,
        keyOnce + ' / ' + keyAfterReplace);

    // =========================================================
    // ② 批次 manifest + 工作台快照刷新恢复；无 AI 时保图
    // =========================================================
    evalIn(rtA2, 'BatchUploader.photos = ' + JSON.stringify([{
        localId: 'local-a1',
        receiptId: 101,
        jobId: 'job-101',
        requestKey: 'rk-101',
        imageUrl: '/api/receipt/101/image',
        status: 'uploading',
        fileName: 'a.jpg',
    }]) + '; BatchUploader.activeIndex = 0;');
    rtA2.persistManifest();
    rtA2.persistWorkbenchQueueSnapshot([
        { id: 101, supplier_name: '供应商A', receipt_date: '2026-10-01', total_amount: 100, status: 'uploading' },
        { id: 102, supplier_name: '供应商B', receipt_date: null, total_amount: 120, status: 'error' },
        { id: 103, supplier_name: '供应商C', receipt_date: null, total_amount: 80, status: 'parsed' },
    ]);

    const rtA3 = createRuntime(ownerA);   // 再次刷新
    const manifest = rtA3.loadManifest();
    ok('刷新恢复：批次 manifest 取回（含 request_key/图片/身份）',
        !!manifest && manifest.items.length === 1
        && manifest.items[0].requestKey === 'rk-101'
        && manifest.items[0].jobId === 'job-101'
        && manifest.items[0].imageUrl === '/api/receipt/101/image'
        && manifest.items[0].tenant === 't-hk'
        && manifest.items[0].account === 'owner-a@demo.hk',
        JSON.stringify(manifest));
    ok('刷新恢复：慢加载时工作台快照先渲染队列',
        rtA3.restoreWorkbenchQueueSnapshot() === true
        && evalIn(rtA3, 'cachedWorkbenchQueueData.length') === 3,
        'snapshot restore failed');

    const refOnlyPhoto = rtA3.buildRestoredPhotoFromRef({
        receiptId: 301, jobId: 'job-301', requestKey: 'rk-301', fileName: 'slow.jpg',
        imageUrl: '/api/receipt/301/image', status: 'uploading',
        tenant: 't-hk', account: 'owner-a@demo.hk',
    }, null);
    ok('慢加载/无 AI：只凭 ref 恢复时保留服务端原图与处理中状态',
        !!refOnlyPhoto && refOnlyPhoto.imageUrl === '/api/receipt/301/image'
        && refOnlyPhoto.status === 'uploading' && refOnlyPhoto.jobId === 'job-301',
        JSON.stringify(refOnlyPhoto));
    const refOnlyParsed = rtA3.buildRestoredPhotoFromRef({
        receiptId: 302, requestKey: 'rk-302', fileName: 'noai.jpg',
        imageUrl: '/api/receipt/302/image', status: 'parsed',
        tenant: 't-hk', account: 'owner-a@demo.hk',
    }, null);
    ok('无 AI/详情失败：待复核回落可重试态但仍保留原图',
        !!refOnlyParsed && refOnlyParsed.status === 'error'
        && refOnlyParsed.imageUrl === '/api/receipt/302/image'
        && !!refOnlyParsed.errorMsg,
        JSON.stringify(refOnlyParsed));

    // =========================================================
    // ②b 同页重登 / 换账号（不整页刷新）：登录成功触发的隔离重置与恢复
    // =========================================================
    rtA3.markClientTasksIsolated();                 // 模拟一次 403 后的会话隔离
    rtA3.AuthState.account = null;                  // 退出
    rtA3.AuthState.account = { email: 'owner-a@demo.hk' };  // 同页重登同账号
    await rtA3.restoreClientTasksForCurrentIdentity(true);
    ok('同页重登：解除本会话隔离', rtA3.isClientTasksIsolated() === false);
    ok('同页重登：本账号任务引用可再恢复',
        rtA3.listRestorableClientTaskRefs().length >= 3,
        'restorable=' + rtA3.listRestorableClientTaskRefs().length);
    const reloginPhotos = evalIn(rtA3, 'BatchUploader.photos');
    ok('同页重登：恢复本账号批次照片与原图',
        reloginPhotos.length === 1 && reloginPhotos[0].receiptId === 101
        && reloginPhotos[0].imageUrl === '/api/receipt/101/image',
        JSON.stringify(reloginPhotos));

    // 同页切账号：旧账号的内存批次与本地引用都不得带入新账号
    rtA3.AuthState.account = { email: 'staff-b@demo.hk' };
    await rtA3.restoreClientTasksForCurrentIdentity(true);
    ok('切账号：不恢复旧账号批次照片（内存已清空）',
        evalIn(rtA3, 'BatchUploader.photos.length') === 0);
    ok('切账号：不恢复旧账号任务引用',
        rtA3.loadClientTaskRefs().length === 0
        && rtA3.listRestorableClientTaskRefs().length === 0);
    ok('切账号：B 仍看不到 A 的批次 manifest', rtA3.loadManifest() === null);

    // 切回原账号：数据仍在自己的身份桶里，可再次恢复
    rtA3.AuthState.account = { email: 'owner-a@demo.hk' };
    await rtA3.restoreClientTasksForCurrentIdentity(true);
    ok('切回原账号：再次恢复本账号批次照片',
        evalIn(rtA3, 'BatchUploader.photos.length') === 1
        && evalIn(rtA3, 'BatchUploader.photos[0].receiptId') === 101);

    const commonSrc = fs.readFileSync(path.join(JS_DIR, 'common.js'), 'utf8');
    const mainSrc = fs.readFileSync(path.join(JS_DIR, 'main.js'), 'utf8');
    ok('登录成功接线：probeAuthAndEnter 调用身份恢复编排',
        commonSrc.indexOf('restoreClientTasksForCurrentIdentity(!!reloadData)') !== -1);
    ok('启动接线：main.js 在身份探测后走统一恢复编排',
        mainSrc.indexOf('restoreClientTasksForCurrentIdentity()') !== -1);

    // =========================================================
    // ③ 跨身份隔离：换账号 / 换租户看不到他人草稿
    // =========================================================
    const ownerB = { account: 'staff-b@demo.hk' };
    const rtB = createRuntime(ownerB);
    ok('跨账号隔离：B 账号读不到 A 的任务引用', rtB.loadClientTaskRefs().length === 0,
        JSON.stringify(rtB.loadClientTaskRefs()));
    ok('跨账号隔离：B 看不到 A 的批次 manifest', rtB.loadManifest() === null);
    ok('跨账号隔离：B 看不到 A 的工作台快照', rtB.loadWorkbenchQueueSnapshot() === null
        && rtB.restoreWorkbenchQueueSnapshot() === false);
    ok('跨账号隔离：B 无法用 A 的身份重建照片', rtB.buildRestoredPhotoFromRef({
        receiptId: 101, status: 'parsed', tenant: 't-hk', account: 'owner-a@demo.hk',
    }, null) === null);

    // =========================================================
    // ④ 403 失效：草稿保留、本会话隔离、不流向他人
    // =========================================================
    const manifestKey = rtA3.manifestStorageKey();
    ok('存储分键：批次 manifest 与 client task ref 不共用键',
        manifestKey !== rtA3.clientTaskStorageKey()
        && !!store[manifestKey] && Object.prototype.hasOwnProperty.call(store, rtA3.clientTaskStorageKey()),
        manifestKey);
    const manifestBefore = store[manifestKey];
    const rt403 = createRuntime({
        account: 'owner-a@demo.hk',
        apiFetch: () => Promise.resolve(mkRes(403, { status: 'error', code: 'FORBIDDEN' })),
    });
    await rt403.restoreBatchFromManifest();
    ok('403：本会话标记隔离（不再自动恢复）', rt403.isClientTasksIsolated() === true);
    ok('403：不把未授权数据渲染进队列', evalIn(rt403, 'BatchUploader.photos.length') === 0);
    ok('403：非敏感草稿原样保留（manifest 未清空且仍含图片）',
        store[manifestKey] === manifestBefore
        && JSON.parse(store[manifestKey]).items[0].imageUrl === '/api/receipt/101/image',
        'manifest changed/lost');
    const _restorableAfter403 = rt403.listRestorableClientTaskRefs();
    const _rawAfter403 = rt403.loadClientTaskRefs();
    ok('403：隔离期间可恢复队列为空，但原始引用仍在本地',
        _restorableAfter403.length === 0 && _rawAfter403.length >= 3,
        'restorable=' + _restorableAfter403.length + ' raw=' + _rawAfter403.length
        + ' identity=' + JSON.stringify(rt403.resolveClientIdentity()));
    const rtB2 = createRuntime(ownerB);
    ok('403：隔离草稿仍不流向其他账号', rtB2.loadClientTaskRefs().length === 0);
    rt403.resetClientTasksIsolation();
    ok('重新登录（身份确认）后可再次恢复', rt403.listRestorableClientTaskRefs().length >= 3,
        'restorable=' + rt403.listRestorableClientTaskRefs().length);

    // =========================================================
    // ⑤ 409 冲突：双方版本可见 + 字段级差异 + 本地编辑不清空
    // =========================================================
    const rtC = createRuntime(ownerA);
    const localData = {
        id: 9001, version: 3, supplier_name: '供应商A', date: '2026-10-01',
        total_amount: 100, settlement_type: 'cash',
        items: [{ name: '牛肉', quantity: 2, unit_price: 50, amount: 100 }],
    };
    const serverData = {
        id: 9001, version: 5, supplier_name: '供应商B', date: '2026-10-02',
        total_amount: 120, settlement_type: 'credit',
        items: [{ name: '牛肉', quantity: 2, unit_price: 60, amount: 120 }],
    };
    const localSnapshot = JSON.stringify(localData);
    rtC.showConflictDiffModal(9001, localData, serverData, null);
    const localText = rtC.document.getElementById('conflictLocalDiffContent').textContent;
    const serverText = rtC.document.getElementById('conflictServerDiffContent').textContent;
    ok('409：本地版本号可见', localText.indexOf('本地提交版本: v3') !== -1, localText);
    ok('409：服务端版本号可见', serverText.indexOf('服务端版本: v5') !== -1, serverText);
    ok('409：字段级差异可见（供应商/金额/明细单价）',
        localText.indexOf('供应商: 供应商A → 供应商B') !== -1
        && localText.indexOf('整单金额: $100.00 → $120.00') !== -1
        && localText.indexOf('明细 1 单价: $50.00 → $60.00') !== -1,
        localText);
    ok('409：版本号差异明确展示', localText.indexOf('版本号: v3 → v5') !== -1, localText);
    ok('409：本地编辑内容未被清空/改写', JSON.stringify(localData) === localSnapshot);
    ok('409：本地冲突草稿留底可读回',
        (() => {
            const d = rtC.loadConflictLocalDraft(9001);
            return !!d && d.version === 3 && !!d.data && d.data.total_amount === 100;
        })());
    const rtC2 = createRuntime(ownerB);
    ok('409：冲突草稿不跨账号可见', rtC2.loadConflictLocalDraft(9001) === null);

    // 服务端数据取不到时不伪造差异结论：本地内容仍可见，且不追加字段差异
    const rtC3 = createRuntime({
        account: ownerA.account,
        apiFetch: () => Promise.reject(new Error('offline')),
    });
    rtC3.showConflictDiffModal(9002, Object.assign({}, localData, { id: 9002 }), null, null);
    await new Promise(resolve => setTimeout(resolve, 0));
    const noServerLocalText = rtC3.document.getElementById('conflictLocalDiffContent').textContent;
    ok('409：服务端不可用时保留本地内容且不臆造字段差异',
        noServerLocalText.indexOf('本地提交版本: v3') !== -1
        && noServerLocalText.indexOf('字段级差异') === -1,
        noServerLocalText);

    console.log('\nU04 harness: ' + pass + ' passed, ' + fail + ' failed');
    process.exit(fail === 0 ? 0 : 1);
})().catch(err => {
    console.error('HARNESS ERROR:', err && err.stack ? err.stack : err);
    process.exit(2);
});
