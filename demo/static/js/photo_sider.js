// demo/static/js/photo_sider.js
// 抽屉式本批照片 Sider 状态机（BatchUploader）：manifest 持久化恢复、批量上传解析、
        // 选中/聚合进度/侧栏渲染/重试/移除。
// F5·S4-11 从 main.js 按域抽离，逐字节一致；加载顺序在 main.js 之后。

// -------------------------------------------------------------
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
    if (s === 'parsed' || s === 'parsed_with_warnings') {
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
            const res = await apiFetch(`/api/receipt/${item.receiptId}`);
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
        'button, a, input, select, textarea, label, img, canvas, svg, video, audio, [onclick], [data-csp-click], [data-csp-change], [data-csp-input], [data-csp-keydown], [data-csp-submit], [role="button"], [contenteditable="true"]'
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
    // 清理非批量路径残留的烤后预览 URL（切换至批量后不再使用）
    revokeBakedPreviewUrl();

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
            const proceed = (typeof uiConfirm === 'function')
                ? await uiConfirm({
                    title: '照片可能不清楚',
                    message: '这批里有 ' + badList.length + ' 张可能影响识别。仍要全部上传吗？\n' + detail,
                    confirmText: '仍然上传',
                    cancelText: '先不上传',
                })
                : false;
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

        triggerBatchAnalysis(false);
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
    const useTreatment = document.getElementById('chkTreatment').checked;

    showLoadingCard();
    startBatchTimer();

    // Wave 3（T9）：async=true 立即返回 job_id，再轮询 /api/job/{id}
    apiFetch(`/api/upload?treatment=${useTreatment}&async=true&force=${photo.force ? 'true' : 'false'}`, { method: 'POST', body: formData })
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
        // N5：本路径（批量模式下只分析 1 张）此前是提示盲区，与批量路径统一补上
        notifyExperimentOverrideIfApplied(ret);
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
    const useTreatment = document.getElementById('chkTreatment').checked;

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
    // N5：批量识别同样要告知「本次识别由实验配置覆盖」。实验是整批共用的同一份
    // 配置，故本批只提示一次（复用单张路径的 notifyExperimentOverrideIfApplied，
    // 不另造一套文案/机制）。
    let experimentOverrideNotified = false;

    targets.forEach(photo => {
        const formData = new FormData();
        formData.append('receipt', photo.file);
        formData.append('force', photo.force ? 'true' : 'false');

        apiFetch(`/api/upload?treatment=${useTreatment}&async=true&force=${photo.force ? 'true' : 'false'}`, { method: 'POST', body: formData })
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
        // N5：成功与失败都提示（失败响应同样带 experiment_override，实验配置可能
        // 正是失败原因），但同一批量内只提示一次。
        if (!experimentOverrideNotified) {
            experimentOverrideNotified = notifyExperimentOverrideIfApplied(ret);
        }
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
    syncFeedbackVisibility();
    syncRecoveryVisibility();
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
    const nameEl = document.getElementById('previewFileName');
    if (nameEl) {
        nameEl.textContent = photo.fileName || (photo.file && photo.file.name) || '';
    }

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
        lastFeedbackForCurrent = null;
        syncFeedbackButtonsUI(null);
        syncFeedbackVisibility();
        syncRecoveryVisibility();
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
        syncFeedbackVisibility();
        syncRecoveryVisibility();
    } else {
        // 无数据：清空质量警告横幅，避免展示上一张照片的警告
        showQualityWarnings([]);
        document.getElementById('preConfirmCard').classList.remove('hide');
        document.getElementById('loadingCard').classList.add('hide');
        document.getElementById('errorCard').classList.add('hide');
        document.getElementById('prefillFormCard').classList.add('hide');
        syncFeedbackVisibility();
        syncRecoveryVisibility();
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

        apiFetch(`/api/receipt/${photo.receiptId}/retry`, { method: 'POST' })
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
        const useTreatment = document.getElementById('chkTreatment').checked;
        const formData = new FormData();
        formData.append('files', photo.file);
        formData.append('force', photo.force ? 'true' : 'false');
        showLoadingCard();
        startBatchTimer();
        apiFetch(`/api/upload_batch?treatment=${useTreatment}&force=${photo.force ? 'true' : 'false'}`, { method: 'POST', body: formData })
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
                    // 空 image_path 的单据 image_url 为 ''：归一成 null（与 photo 初始化一致），
                    // 避免空串在后续 p.imageUrl || ... 链里被误判为「有值」
                    photo.imageUrl = r.image_url || null;
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
// 解析完成后的列表状态：服务端任务已经把识别结果存好，这里只把侧栏状态记为 saved
// （显示「已保存」），不再发任何写库请求。
// -------------------------------------------------------------
function autoSaveParsedPhoto(idx) {
    const p = BatchUploader.photos[idx];
    if (!p || p.status !== 'parsed' || !p.data) return;
    // 识别结果由服务端任务在读完时就已经保存；前端不能再以「自动识别」的名义写库
    // （后端只允许服务端任务这样写，人工录入必须走「保存并提交审核」）。
    p.status = 'saved';
    renderSider();
    updateSelectionUI();
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
