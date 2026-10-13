// demo/static/js/upload.js
// 上传识别族：vendor_hint、图片选择与本地预览、HEIC/TIFF 转码、画质预检、异步 Job
// 轮询、共享纯函数、识别失败错误卡片与双出口、U-8 递进引导、手工录入、定点裁剪旋转、
// 以及 error-card/manual-entry 的 window 导出尾段。
// F5·S4-11 从 main.js 按域抽离，逐字节一致；加载顺序在 main.js 与 common.js 之后。
// -------------------------------------------------------------
// 上传前可选供应商（vendor_hint）
// why：供应商记忆注入此前只有 vendor_hint 一条通道，而后端 Form 形参虽然存在
// （api_receipts.py 的 vendor_hint: str = Form("")），前端却从不发送该字段
// （grep vendor_hint demo/static demo/templates 零命中）→ 首轮恒不检索，命中率长期极低。
// 这里补上成本最低的一条来源：上传前手选供应商，选项复用既有数据源 GET /api/suppliers，
// 该设备上次的选择用 localStorage 记住（key：vendor_hint_last）。
// 范围：只接单张上传（triggerAnalysisNow）；批量路径未纳入本任务（批量端点不读 vendor_hint）。
// -------------------------------------------------------------
const VENDOR_HINT_STORAGE_KEY = 'vendor_hint_last';
// 该设备上次选择的供应商名。选项列表是异步加载的（loadSuppliersData），
// 先用它记住取值，待选项就绪后再回填，避免「记住上次选择」在首屏被静默重置。
let _vendorHintSaved = '';

/** 读取当前手选的供应商名。未选择或元素缺失一律返回空串，与后端 Form 缺省值同口径。 */
function getVendorHintValue() {
    const sel = document.getElementById('vendorHintSelect');
    return (sel && sel.value) ? sel.value : '';
}

/** 用供应商列表填充上传卡片的下拉。软停用的供应商不进下拉（与复核 combobox U-06 同口径）。 */
function populateVendorHintOptions(suppliers) {
    const sel = document.getElementById('vendorHintSelect');
    if (!sel) return;
    const names = [];
    (suppliers || []).forEach(sup => {
        if (!sup || !sup.name) return;
        if (sup.active === 0 || sup.active === '0' || sup.active === false) return;
        names.push(sup.name);
    });
    sel.innerHTML = '';
    const blank = document.createElement('option');
    blank.value = '';
    blank.textContent = '未选择（自动识别）';
    sel.appendChild(blank);
    names.forEach(name => {
        const opt = document.createElement('option');
        opt.value = name;
        opt.textContent = name;
        sel.appendChild(opt);
    });
    // 回填上次选择；若该供应商已不在列表（停用/改名/合并），补一个同名选项兜底，
    // 否则浏览器的 select 会把不存在的值落回空选项 = 静默丢失用户的选择。
    if (_vendorHintSaved) {
        if (names.indexOf(_vendorHintSaved) === -1) {
            const keep = document.createElement('option');
            keep.value = _vendorHintSaved;
            keep.textContent = _vendorHintSaved;
            sel.appendChild(keep);
        }
        sel.value = _vendorHintSaved;
    }
}

function initVendorHintSelect() {
    const sel = document.getElementById('vendorHintSelect');
    if (!sel) return;
    try { _vendorHintSaved = localStorage.getItem(VENDOR_HINT_STORAGE_KEY) || ''; } catch (e) { _vendorHintSaved = ''; }
    // 选项可能尚未加载：此处赋值对不存在的选项无效，靠 populateVendorHintOptions 再回填一次
    if (_vendorHintSaved) sel.value = _vendorHintSaved;
    sel.addEventListener('change', () => {
        _vendorHintSaved = sel.value || '';
        try { localStorage.setItem(VENDOR_HINT_STORAGE_KEY, _vendorHintSaved); } catch (e) { /* 隐私模式等 localStorage 不可用：仅本次会话生效，不影响上传 */ }
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

    // S3-26：手机「拍照」入口（capture=environment，一次一张），拍完直接并入本批
    const cameraInput = document.getElementById('receiptCamera');
    if (cameraInput) {
        cameraInput.addEventListener('change', () => {
            const files = Array.from(cameraInput.files || []);
            cameraInput.value = '';
            if (files.length > 0) queueFilesSelect(files);
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
        const res = await apiFetch('/api/convert-image', {
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
        revokeBakedPreviewUrl();

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

        // S3-19：默认直接分析，只有画质预检不合格才停在确认卡让店员先看图
        if (!qualityWarn) {
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
                // 边长不足 8px 时拉普拉斯没有样本，不能据此挡住上传。
                const naturalW = img.naturalWidth || 0;
                const naturalH = img.naturalHeight || 0;
                if (naturalW < 8 || naturalH < 8) {
                    resolve({ ok: true, reasons: [], skipped: 'too_small' });
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

// 后端没有分步进度，所以不画百分比和步骤高亮，只更新一句说明。
function updateLoadingStage(pct, stageName) {
    const stageNameEl = document.getElementById('loadingStageName');
    if (stageNameEl && stageName) stageNameEl.textContent = stageName;
}

const OCR_SLOW_HINT_SEC = 30;

function startOcrTimer() {
    stopOcrTimer();
    ocrStartTime = Date.now();
    const timerElem = document.getElementById('ocrTimer');
    const slowHint = document.getElementById('loadingSlowHint');
    if (timerElem) timerElem.innerText = '0 秒';
    if (slowHint) slowHint.classList.add('hide');
    updateLoadingStage(null, '正在识别这张单据...');

    ocrTimerInterval = setInterval(() => {
        const sec = Math.floor((Date.now() - ocrStartTime) / 1000);
        if (timerElem) timerElem.innerText = sec + ' 秒';
        if (slowHint && sec >= OCR_SLOW_HINT_SEC) {
            slowHint.classList.remove('hide');
            slowHint.textContent = '仍在处理。已经等了一阵了，可以转手工录入，原图会留着。';
            updateLoadingStage(null, '仍在处理这张单据...');
        }
    }, 1000);
}

function stopOcrTimer() {
    if (ocrTimerInterval) {
        clearInterval(ocrTimerInterval);
        ocrTimerInterval = null;
    }
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
    let netFails = 0;
    const fetchJob = () => {
        const fn = (typeof window !== 'undefined' && typeof window.apiFetch === 'function')
            ? window.apiFetch : apiFetch;
        return fn(`/api/job/${jobId}`);
    };
    const pauseWhileHidden = () => {
        const resume = () => {
            if (typeof document !== 'undefined' && document.hidden) return;
            document.removeEventListener('visibilitychange', resume);
            setTimeout(tick, intervalMs);
        };
        document.addEventListener('visibilitychange', resume);
    };

    function tick() {
        const verdict = jobPollVerdict(token, startTs, Date.now(), timeoutMs);
        if (verdict === 'cancelled') {
            onSettled({ status: 'cancelled', msg: '轮询已取消' });
            return;
        }
        if (verdict === 'timeout') {
            onSettled({ status: 'error',
                msg: '等了太久还没读完，已经停止等待。请点「转手工录入」自己填，原图会保留。', code: 'ENGINE_TIMEOUT' });
            return;
        }
        if (typeof document !== 'undefined' && document.hidden) {
            pauseWhileHidden();
            return;
        }
        fetchJob()
            .then(res => res.json())
            .then(job => {
                netFails = 0;
                if (job && job.job_status === 'done') {
                    updateLoadingStage(null, '读完了，正在打开核对表单...');
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
                        error_code: job.error_code,
                        experiment_override: job.experiment_override,
                    });
                } else if (job && job.status === 'error') {
                    // HTTP 级错误（如 404 任务不存在）
                    onSettled({ status: 'error', msg: job.msg || '查询识别任务失败' });
                } else {
                    if (job && job.job_status) {
                        const stText = job.job_status === 'queued' ? '正在排队，马上开始...' : '正在认字、对数、配食材...';
                        const stageNameEl = document.getElementById('loadingStageName');
                        if (stageNameEl) stageNameEl.textContent = stText;
                    }
                    // 若已有转码后的 image_url，提前通知（HEIC 预览）
                    if (job && job.image_url && typeof opts?.onProgress === 'function') {
                        opts.onProgress(job);
                    }
                    setTimeout(tick, intervalMs);
                }
            })
            .catch(err => {
                if (typeof document !== 'undefined' && document.hidden) {
                    pauseWhileHidden();
                    return;
                }
                netFails += 1;
                if (netFails < 3) {
                    setTimeout(tick, intervalMs);
                    return;
                }
                console.error('轮询任务状态异常', err);
                onSettled({ status: 'error', msg: '查询识别进度失败，请稍后重试' });
            });
    }
    tick();
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
    let list = [];
    if (ret && Array.isArray(ret.quality_warnings)) list = ret.quality_warnings.slice();
    else if (ret && ret.data && Array.isArray(ret.data.quality_warnings)) list = ret.data.quality_warnings.slice();
    
    return list;
}

// P5：A/B 实验覆盖管理台引擎配置的可见化提示。后端在识别结果里透传
// experiment_override={experiment_id, grp, model, base_url}，这里把「本次识别
// 实际用的是实验配置」明确告知用户，避免管理员误判「改了引擎没生效」。
// 返回是否真的弹了提示：批量路径据此去重（同一批量内只提示一次，避免逐张重复弹）。
function notifyExperimentOverrideIfApplied(ret) {
    const ov = (ret && ret.experiment_override)
        || (ret && ret.data && ret.data.experiment_override);
    if (!ov || !ov.experiment_id) return false;
    const grpLabel = ov.grp === 'treatment' ? '实验组' : '对照组';
    showToast(`本次识别由实验 #${ov.experiment_id} 的${grpLabel}配置覆盖`, 'warning', 6000);
    return true;
}
window.notifyExperimentOverrideIfApplied = notifyExperimentOverrideIfApplied;

// 画质预检问题精准映射与人话化引导（一事一词，精准归因，绝无冗余堆砌）
const QUALITY_PROBLEM_MAP = {
    'image_blur': {
        title: '画面模糊',
        tip: '照片对焦不清晰，请持稳手机对焦重拍，或点「继续 AI 解析」尝试识别'
    },
    'image_dark': {
        title: '光线过暗',
        tip: '环境光线不足，请移至明亮处平放单据后重新拍摄'
    },
    'image_empty_or_corrupted': {
        title: '图片损坏或过小',
        tip: '图片无法正常读取，请用相机拍摄原图重新上传'
    },
    'duplicate': {
        title: '疑似重复单据',
        tip: '该图片疑似与历史单据重复，请核对单号避免重复入账'
    }
};

function formatQualityWarning(w) {
    if (!w) return null;
    const raw = String(w).trim();
    if (!raw) return null;
    if (QUALITY_PROBLEM_MAP[raw]) {
        return QUALITY_PROBLEM_MAP[raw];
    }
    const lower = raw.toLowerCase();
    if (lower.includes('模糊') || lower.includes('blur')) {
        return QUALITY_PROBLEM_MAP['image_blur'];
    }
    if (lower.includes('暗') || lower.includes('dark') || lower.includes('弱光') || lower.includes('low_light')) {
        return QUALITY_PROBLEM_MAP['image_dark'];
    }
    if (lower.includes('损坏') || lower.includes('corrupt') || lower.includes('empty') || lower.includes('过小')) {
        return QUALITY_PROBLEM_MAP['image_empty_or_corrupted'];
    }
    if (lower.includes('重复') || lower.includes('duplicate')) {
        return QUALITY_PROBLEM_MAP['duplicate'];
    }
    return {
        title: '系统提示',
        tip: raw.replace(/^提示[：:]\s*/, '')
    };
}

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
        return;
    }

    // 逐条精准归因并去重
    const items = [];
    const seenTitles = new Set();
    for (const w of list) {
        const item = formatQualityWarning(w);
        if (item && !seenTitles.has(item.title)) {
            seenTitles.add(item.title);
            items.push(item);
        }
    }

    if (items.length === 0) {
        banner.classList.add('hide');
        return;
    }

    // 单条与多条格式化呈现（按具体碰到的问题精准输出）
    let contentHtml = '';
    if (items.length === 1) {
        contentHtml = `<span style="font-weight:600;">[${escapeHtml(items[0].title)}] 提示：</span>${escapeHtml(items[0].tip)}`;
    } else {
        const lines = items.map((it, idx) => `<div><strong>${idx + 1}. [${escapeHtml(it.title)}]</strong> ${escapeHtml(it.tip)}</div>`).join('');
        contentHtml = `<div style="font-weight:600; margin-bottom:4px;">画质预检提示：</div>${lines}`;
    }

    // AC-05-05：非阻断提醒的人工确认入口（来源/时间/规则由后端持久留痕；不静默改金额）
    _lastQualityWarningRules = list.map(w => (w && typeof w === 'object')
        ? (w.rule || w.code || w.type || JSON.stringify(w)) : String(w)).filter(Boolean);
    contentHtml += `<div style="margin-top:8px;">`
        + `<button type="button" id="btnAcknowledgeWarnings" class="btn btn-secondary" `
        + `style="min-height:40px; padding:6px 14px;" onclick="acknowledgeCurrentWarnings()" `
        + `title="确认已人工核对上方提醒（会记录操作者、时间与规则，用于审计）">确认已核对提醒</button>`
        + `</div>`;

    banner.innerHTML = contentHtml;
    banner.classList.remove('hide');

    // 错误态且 prefillFormCard 隐藏时，克隆置顶到 rightPanel 顶部
    const prefill = document.getElementById('prefillFormCard');
    const rightPanel = document.getElementById('rightPanel');
    if (prefill && prefill.classList.contains('hide') && rightPanel) {
        const clone = banner.cloneNode(true);
        clone.id = 'qualityWarningsBannerClone';
        clone.classList.remove('hide');
        if (rightPanel.firstChild) rightPanel.insertBefore(clone, rightPanel.firstChild);
        else rightPanel.appendChild(clone);
    }
    if (prefill && !prefill.classList.contains('hide')) {
        const c = document.getElementById('qualityWarningsBannerClone');
        if (c && c.parentNode) c.parentNode.removeChild(c);
    }
}

// AC-05-05：当前提醒的规则标识（供人工确认入口留痕用）
let _lastQualityWarningRules = [];
function acknowledgeCurrentWarnings() {
    if (!currentReceiptId) {
        showToast('请先打开一张单据再确认提醒', 'warning');
        return;
    }
    const rules = _lastQualityWarningRules.length ? _lastQualityWarningRules : ['unspecified'];
    const ruleText = rules.join('; ');
    const btn = document.getElementById('btnAcknowledgeWarnings');
    if (btn) { btn.disabled = true; }
    apiFetch(`/api/receipt/${currentReceiptId}/acknowledge_warning`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            warning_id: rules[0],
            rule: ruleText,
            version: (currentReceiptData && currentReceiptData.version != null) ? currentReceiptData.version : null
        })
    })
    .then(res => Promise.all([res.status, res.json().catch(() => null)]))
    .then(([status, ret]) => {
        if (status === 200 && ret && ret.status === 'success') {
            showToast('已记录提醒确认（操作者 / 时间 / 规则已留痕，不修改任何金额）', 'success');
        } else {
            showToast('提醒确认未记录：' + ((ret && ret.msg) || ('HTTP ' + status)), 'error');
            if (btn) { btn.disabled = false; }
        }
    })
    .catch(err => {
        console.warn('[warn-ack] 提醒确认请求失败:', err);
        showToast('提醒确认请求失败，请重试', 'error');
        if (btn) { btn.disabled = false; }
    });
}
window.acknowledgeCurrentWarnings = acknowledgeCurrentWarnings;

// P2-1：图内隐藏文字检测提示（只告警不拦截）。命中时显示人话横幅；未命中什么都不显示
// （不出现「未检出」噪声）。reasons 来自后端，一律过 w2Escape 再插 innerHTML（P0 硬规则）。
function showImageGuardNotice(guard) {
    const banner = document.getElementById('imageGuardBanner');
    if (!banner) return;
    banner.innerHTML = '';
    const reasons = (guard && guard.flagged && Array.isArray(guard.reasons))
        ? guard.reasons.filter(r => r != null && String(r).trim() !== '')
        : [];
    if (reasons.length === 0) {
        banner.classList.add('hide');
        return;
    }
    banner.innerHTML = reasons.map(r => '<div>[提示] ' + w2Escape(String(r)) + '</div>').join('');
    banner.classList.remove('hide');
}
window.showImageGuardNotice = showImageGuardNotice;

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

    // 旋转烤入竞态防护：若存在未固化的 CSS 旋转，先同步烤入再上传，避免上传原文件
    if (currentRotation !== 0) {
        if (_isBakingRotation) {
            showToast('正在固化旋转，请稍候再解析', 'info');
            return;
        }
        showToast('检测到未固化的旋转，正在先固化后再上传…', 'info');
        if (typeof bakeCurrentRotation === 'function') {
            bakeCurrentRotation().then((ok) => {
                if (ok) triggerAnalysisNow(forceFlag);
                else showToast('旋转固化失败，请重试', 'error');
            });
        } else {
            showToast('旋转固化函数缺失，请刷新页面重试', 'error');
        }
        return;
    }
    // 自检：FormData 中 selectedFile 的尺寸/类型（旋转后 w/h 已互换可在控制台核验）

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
    // B3：上传前手选的供应商作为识别先验（vendor_hint）。未选择时传空串，
    // 与后端 Form("") 缺省同口径 = 行为与接线前完全一致。
    formData.append('vendor_hint', getVendorHintValue());

    const useTreatment = document.getElementById('chkTreatment').checked;

    document.getElementById('preConfirmCard').classList.add('hide');
    document.getElementById('errorCard').classList.add('hide');
    document.getElementById('loadingCard').classList.remove('hide');
    document.getElementById('prefillFormCard').classList.add('hide');
    startOcrTimer();
    parseStartTs = Date.now();

    // Q28: 代际标记 + 轮询令牌——被新上传替换后旧轮询作废、旧结果丢弃
    const gen = ++singleUploadGen;
    if (singlePollToken) singlePollToken.cancelled = true;
    singlePollToken = { cancelled: false };

    // Wave 3（T9）：async=true 立即返回 job_id，再轮询 /api/job/{id}
    apiFetch(`/api/upload?treatment=${useTreatment}&async=true&force=${isForce ? 'true' : 'false'}`, {
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

            // 门禁/引擎失败绝不阻断在错误卡片：统一分流（门禁/引擎 → 人话化提示 + 自动转手工补录；
            // 仅画质问题保留可自救的错误卡片）
            if (routeRecognitionFailure(ret, ret.receipt_id || null)) return;

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
        notifyExperimentOverrideIfApplied(ret);
        // Q34: 同步成功路径的重复关联动作（queued 路径在轮询发起前已提示）
        offerDuplicateAction(ret);
        document.getElementById('prefillFormCard').classList.remove('hide');
        syncFeedbackVisibility();
        syncRecoveryVisibility();
        if (typeof scrollReviewIntoView === 'function') scrollReviewIntoView();
    }
}

// -------------------------------------------------------------
// 2c. 识别失败错误卡片与双出口 (D28 显式 error + D33 失败双出口)
// U-3: 错误卡归因三分类 quality(画质) > gate(数字/契约门禁) > engine(引擎兜底)。
//      正文一律固定文案（engine 不透出原始 str(e)）；gate 原因经人话化包装。
// -------------------------------------------------------------

// 门禁文案人话化统一在 terms.js（S2-12）；后端给了 gate_issues 的地方优先用 TERMS.formatGateIssues
function humanizeGateMsg(msg) {
    if (typeof TERMS !== 'undefined' && TERMS.humanizeGateText) return TERMS.humanizeGateText(msg);
    return msg ? '这张单据有数字对不上，请对照原图核对' : '';
}

/**
 * 失败结果分流（绝不阻断原则）：
 * - 画质问题 → 返回 false，由调用方展示可自救的错误卡片；
 * - 门禁/数字疑问 → 人话化 Toast + 自动转手工补录（绝不阻断在错误卡片）；
 * - 引擎异常 → 按是否真实降级精准措辞 + 自动转手工补录。
 * 返回 true 表示已转入手工补录，调用方无需再展示错误卡片。
 */
function routeRecognitionFailure(ret, receiptId) {
    const msg = (ret && ret.msg) || '';
    const code = ret && ret.code;
    const isQuality = (code === 'IMAGE_QUALITY_ERROR') || /模糊|画质|曝光|分辨率|image_blur|quality/i.test(msg);
    if (isQuality) return false;

    lastErrorReceiptId = receiptId || (ret && ret.receipt_id) || null;

    const isGate = /算术门禁|契约校验|门禁|明细为空|总额不能|供应商为空|日期格式非法|数量非法|单价非法/.test(msg);
    if (isGate) {
        const issues = (ret && Array.isArray(ret.gate_issues) && typeof TERMS !== 'undefined')
            ? TERMS.formatGateIssues(ret.gate_issues) : [];
        const reason = issues.length ? issues.join('；') : (humanizeGateMsg(msg) || '单据数字存在疑问');
        convertManualFromErrorCard({
            toastText: reason + '。已转到手工录入，请对照左边原图核对金额。',
            toastType: 'warning',
        });
        return true;
    }

    // S3-04：只有「没开通 / 密钥有问题」才自动转手工；繁忙或原因不明停在错误卡，让店员自己选重试或手工
    const kind = (typeof TERMS !== 'undefined') ? TERMS.classifyEngineFailure(ret) : 'other';
    if (kind !== 'not_configured' && kind !== 'auth') return false;

    let text = '自动识别暂时用不了，已转到手工录入。请对照左边原图填写，原图已保留。';
    if (currentRoleName() === 'admin') text += '（管理员：请到「引擎配置」检查识别服务是否已开通、密钥是否有效）';
    // 失败卡先留下。转手工的请求还没回来时，不把「已经转好」说成既成事实。
    showErrorCard(msg, lastErrorReceiptId, code);
    convertManualFromErrorCard({ toastText: text, toastType: 'warning' });
    return true;
}
window.routeRecognitionFailure = routeRecognitionFailure;

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

function showErrorCard(msg, receiptId, code, opts) {
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

        if (titleElem) titleElem.innerText = '照片画质提示（可以继续）';

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
        if (titleElem) titleElem.innerText = '单据上的数字对不上';
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
        // S3-04：按原因分三类，不再把「没开通 / 密钥错」说成「繁忙，等 1 分钟」
        const kind = (typeof TERMS !== 'undefined')
            ? TERMS.classifyEngineFailure({ code: code, msg: rawMsg }) : 'other';
        const unavailable = (kind === 'not_configured' || kind === 'auth');
        if (titleElem) titleElem.innerText = unavailable ? '自动识别暂时用不了' : (kind === 'busy' ? '自动识别现在比较忙' : '这张单没能自动读出来');
        if (badgeElem) {
            badgeElem.innerText = '不是照片的问题';
            badgeElem.className = 'badge badge-danger';
        }
        if (headingElem) {
            headingElem.innerText = unavailable ? '自动识别暂时用不了，请手工录入'
                : (kind === 'busy' ? '自动识别现在很忙，这张单还没读完' : '这张单没能自动读出来');
        }
        if (msgElem) {
            msgElem.innerText = unavailable
                ? '这不是照片的问题，不用重新拍。原图已保留在左边，请点「转手工录入」自己填。'
                : (kind === 'busy'
                    ? '这不是照片的问题，不用重新拍。原图已保留。可以等一会再点「重试」，也可以直接点「转手工录入」。'
                    : '这不是照片的问题，不用重新拍。原图已保留。可以点「重试」再读一次，也可以直接点「转手工录入」。');
        }
        if (btnForce) {
            btnForce.style.display = 'none';
            btnForce.title = '重新提交给 AI 解析';
        }
        if (btnRetry) {
            btnRetry.style.display = unavailable ? 'none' : '';
            btnRetry.title = '重新发起识别';
        }
        if (unavailable && convertBtn) {
            convertBtn.className = 'btn btn-primary';
            convertBtn.style.fontWeight = '600';
        }
        if (opts && opts.plain) {
            if (titleElem) titleElem.innerText = '没能转成手工录入';
            if (headingElem) headingElem.innerText = '转手工录入没有成功';
            if (msgElem) msgElem.innerText = rawMsg;
            if (btnRetry) btnRetry.style.display = 'none';
            if (convertBtn) {
                convertBtn.className = 'btn btn-primary';
                convertBtn.style.fontWeight = '600';
            }
        }
    }

    // 转手工录入需要已有单据行；没有时只允许重新上传/重试（画质 400 无 receiptId 场景仍隐藏）
    // 转手工录入逃生通道随时可用，保留原图
    if (convertBtn) convertBtn.style.display = '';

    document.getElementById('errorCard').classList.remove('hide');
    lastFeedbackForCurrent = null;
    syncFeedbackButtonsUI(null);
    syncFeedbackVisibility();
    syncRecoveryVisibility();
}

function retryFromErrorCard(force = true) {
    const ph = getActivePhoto();
    if (ph) ph.force = !!force;
    window._selectedFileForce = !!force;

    const rid = lastErrorReceiptId || currentReceiptId;
    if (_hasRotatedFile && selectedFile && rid) {
        submitRotatedImage(rid, selectedFile);
        return;
    }

    if (lastErrorReceiptId) {
        // D13: 复用原单据重跑识别
        retryReceiptRecognition(lastErrorReceiptId, force);
    } else {
        // 未生成单据行（如网络异常或画质拦截）：重新上传并显式指定 force
        triggerAnalysisNow(force);
    }
}

function retryRotatedFromErrorCard() {
    const rid = lastErrorReceiptId || currentReceiptId;
    if (!rid) {
        triggerAnalysisNow(true);
        return;
    }
    if (selectedFile) {
        submitRotatedImage(rid, selectedFile);
    } else {
        retryFromErrorCard(true);
    }
}
window.retryRotatedFromErrorCard = retryRotatedFromErrorCard;

// 失败态「重新上传」：移除坏图并回到标准上传入口流程
function reuploadFromErrorCard() {
    track('reupload_after_fail', lastErrorReceiptId, {});
    const failIdx = (typeof BatchUploader !== 'undefined' && BatchUploader.photos)
        ? BatchUploader.photos.findIndex(p => p.receiptId === lastErrorReceiptId)
        : -1;
    if (failIdx >= 0) removePhotoFromSider(failIdx);
    currentReceiptId = null;
    currentReceiptData = null;
    isManualEntry = false;
    ['errorCard', 'loadingCard', 'prefillFormCard', 'preConfirmCard'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.classList.add('hide');
    });
    if (typeof renderSider === 'function') renderSider();
    syncFeedbackVisibility();
    syncRecoveryVisibility();
    const ua = document.getElementById('uploadArea');
    if (ua) ua.scrollIntoView({ block: 'center' });
    // 真实浏览器直接弹文件选择；内置环境也可走「选择收据图片」/拖拽入口
    const fi = document.getElementById('receiptFile');
    if (fi) fi.click();
}
window.reuploadFromErrorCard = reuploadFromErrorCard;

// opts.toastText：转手工成功后的唯一一条提示；失败只更新错误卡，不再另弹 toast
function convertManualFromErrorCard(opts) {
    const o = (opts && typeof opts === 'object' && !(opts instanceof Event)) ? opts : {};
    if (lastErrorReceiptId) {
        const receiptId = lastErrorReceiptId;

        apiFetch(`/api/receipt/${receiptId}/convert_manual`, { method: 'POST' })
        .then(res => Promise.all([res.status, res.json().catch(() => null)]))
        .then(([httpStatus, ret]) => {
            const loading = document.getElementById('loadingCard');
            if (loading) loading.classList.add('hide');
            if (!ret || ret.status !== 'success') {
                const why = (typeof TERMS !== 'undefined')
                    ? TERMS.humanizeCode(ret && ret.code, ret && ret.msg) : '没能转成手工录入，请稍后再试';
                showErrorCard(why + '。原图已保留，可以再点一次「转手工录入」。', receiptId, null, { plain: true });
                return;
            }
            const card = document.getElementById('errorCard');
            if (card) card.classList.add('hide');
            // D33: 转手工录入成功——保留原图，按返回数据渲染复核表单
            applyRecognizedResult(ret, receiptId);
            showToast(o.toastText || '已转为手工录入，请对照左侧原图补录字段', o.toastType || 'info', 7000);
        })
        .catch(err => {
            document.getElementById('loadingCard').classList.add('hide');
            console.error('转手工录入请求异常', err);
            showErrorCard('网络不通。原图已保留，请检查网络后再点一次「转手工录入」。', receiptId, null, { plain: true });
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

    apiFetch(`/api/receipt/${receiptId}/retry?force=${force ? 'true' : 'false'}`, { method: 'POST' })
    .then(res => res.json())
    .then(ret => {
        if (gen !== singleUploadGen) return;
        if (ret.status === 'queued' && ret.job_id) {
            pollAndApply(ret, receiptId);
            return;
        }
        stopOcrTimer();
        document.getElementById('loadingCard').classList.add('hide');
        if (ret.status !== 'success') {
            if (routeRecognitionFailure(ret, receiptId)) return;
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

// 挽回链路公共段（重新解析 / 重拍共用）：轮询识别任务并应用结果。
// 调用前须已：切到 loadingCard、startOcrTimer()、作废并重建 singlePollToken。
// 代际校验取调用时刻的 singleUploadGen，后续新上传会使旧结果静默丢弃。
function pollAndApply(postRet, receiptId) {
    const gen = singleUploadGen;
    pollReceiptJob(postRet.job_id, (jobRet) => {
        if (gen !== singleUploadGen) return;
        stopOcrTimer();
        document.getElementById('loadingCard').classList.add('hide');
        if (jobRet.status === 'cancelled') return;
        if (jobRet.status !== 'success') {
            if (routeRecognitionFailure(jobRet, receiptId)) return;
            showErrorCard(jobRet.msg || '识别失败', receiptId);
            return;
        }
        applyRecognizedResult(jobRet, receiptId);
    }, { token: singlePollToken });
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
    // 新识别结果产生 → 本会话对该单的点赞/点踩态清零（挽回埋点 prior_feedback 口径）
    lastFeedbackForCurrent = null;
    syncFeedbackButtonsUI(null);
    _hasRotatedFile = false;
    _hasRotatedAfterParse = false;
    const btnRot = document.getElementById('btnReparseRotated');
    if (btnRot) btnRot.style.display = 'none';
    const btnErrRot = document.getElementById('btnRetryRotated');
    if (btnErrRot) btnErrRot.classList.add('hide');

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
    notifyExperimentOverrideIfApplied(ret);
    document.getElementById('preConfirmCard').classList.add('hide');
    document.getElementById('errorCard').classList.add('hide');
    document.getElementById('prefillFormCard').classList.remove('hide');
    syncFeedbackVisibility();
    syncRecoveryVisibility();
    if (typeof scrollReviewIntoView === 'function') scrollReviewIntoView();

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
    const aiBadge = document.getElementById('aiPrefillNoticeBadge');
    if (aiBadge) aiBadge.classList.toggle('hide', !!on);
    const title = document.getElementById('manualEntryFormTitle');
    if (title) {
        title.textContent = on
            ? '步骤 2/2：填写单据内容'
            : '步骤 2/2：核对单据内容';
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
    const _loading = document.getElementById('loadingCard');
    track('manual_entry_start', null, {
        source: (_loading && !_loading.classList.contains('hide')) ? 'abort' : 'new',
    });
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
    // 新建手工单空态：付款标记徽章复位为「未付款」
    if (markInput && markInput.tagName === 'BUTTON') setPaymentMarkBadge(false);
    populateDeptSelect(document.getElementById('inpDepartmentId'), null);
    showQualityWarnings([]);
    document.getElementById('itemTableBody').innerHTML = '';
    if (typeof resetTotalOverride === 'function') resetTotalOverride();
    addEmptyRow({ silent: true });

    // 卡片切换：显示复核表单空态
    document.getElementById('preConfirmCard').classList.add('hide');
    document.getElementById('loadingCard').classList.add('hide');
    document.getElementById('errorCard').classList.add('hide');
    document.getElementById('prefillFormCard').classList.remove('hide');
    document.getElementById('splitViewArea').classList.remove('hide');

    showToast('已进入新建手工单：请填写供应商、开单日期与明细后保存', 'info', TOAST_DURATION.guide);
    lastFeedbackForCurrent = null;
    syncFeedbackButtonsUI(null);
    syncFeedbackVisibility();
    syncRecoveryVisibility();

    // 绑定事件与初始化问题摘要 (AC-05-01)
    ['inpSupplier', 'inpDate', 'inpTotal'].forEach(id => {
        const inp = document.getElementById(id);
        if (inp && !inp.__issueListenerBound) {
            inp.__issueListenerBound = true;
            inp.addEventListener('input', () => refreshReceiptIssueSummary());
        }
    });
    const itemTableBody = document.getElementById('itemTableBody');
    if (itemTableBody && !itemTableBody.__issueListenerBound) {
        itemTableBody.__issueListenerBound = true;
        itemTableBody.addEventListener('input', () => refreshReceiptIssueSummary());
        itemTableBody.addEventListener('change', () => refreshReceiptIssueSummary());
    }
    refreshReceiptIssueSummary();
    if (typeof scrollReviewIntoView === 'function') scrollReviewIntoView();
}

// U-11: 识别过程随时转手工逃生通道（保留原图，中止识别，直接切入复核/手工表单）
function abortLoadingAndSwitchToManual() {
    track('parse_abandoned_for_manual', currentReceiptId, {
        waited_ms: parseStartTs ? Date.now() - parseStartTs : null,
        pending_photos: (typeof BatchUploader !== 'undefined' && BatchUploader.photos)
            ? BatchUploader.photos.filter(p => p.status === 'uploading').length : 0,
    });
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
    if (title) title.textContent = '步骤 2/2：填写单据内容';
    // 中途放弃等待转手工：原图还在左边，这时是「AI 没读出来、店员补录」，不是纯手工单
    const badge = document.getElementById('manualEntryFormBadge');
    if (badge) badge.classList.remove('hide');
    const aiBadgeAbort = document.getElementById('aiPrefillNoticeBadge');
    if (aiBadgeAbort) aiBadgeAbort.classList.add('hide');

    // 7. 表单字段初始化/保底（若为空则赋默认值，保留已有输入）
    const inpSupplier = document.getElementById('inpSupplier');
    const inpDate = document.getElementById('inpDate');
    const inpSheet = document.getElementById('inpSheet');
    const inpTotal = document.getElementById('inpTotal');
    const inpSettlement = document.getElementById('inpSettlementType');
    const inpPaymentMark = document.getElementById('inpPaymentMark');
    const inpDept = document.getElementById('inpDepartmentId');
    if (inpDate && !inpDate.value) {
        const todayStr = todayLocalStr();
        inpDate.value = todayStr;
        if (inpSheet && !inpSheet.value) {
            inpSheet.value = todayStr.slice(0, 7);
        }
    }
    if (inpTotal && (!inpTotal.value || inpTotal.value === '0.00')) {
        inpTotal.value = '0.00';
    }
    if (inpPaymentMark && inpPaymentMark.tagName !== 'BUTTON' && !inpPaymentMark.value) {
        inpPaymentMark.value = '未付款';
    }
    // 付款标记切换徽章恒有确定 state（HTML 默认「未付款」；识别已渲染的 state 保留不动）
    if (inpDept && !inpDept.value) {
        populateDeptSelect(inpDept, null);
    }
    showQualityWarnings([]);

    const tbody = document.getElementById('itemTableBody');
    if (tbody && tbody.children.length === 0) {
        addEmptyRow({ silent: true });
    }

    renderCurrencySymbol();

    // 8. 友好人话提示
    showToast('已停止等待，原图已在左侧保留，请直接手工录入', 'info', TOAST_DURATION.guide);
    lastFeedbackForCurrent = null;
    syncFeedbackButtonsUI(null);
    syncFeedbackVisibility();
    syncRecoveryVisibility();
    if (typeof scrollReviewIntoView === 'function') scrollReviewIntoView();
}

// 工具激活状态: null (未选中任何工具), 'zoom' (定点放大模式), 'crop' (裁剪模式)
let activeTool = null;

function updateToolbarButtonStates() {
    const photo = getActivePhoto();
    const cropDisabled = !isPhotoSelectable(photo);  // 已解析/已保存不可裁剪
    // 删除图片 / 更改图片：pending 与失败态可见（失败态提供换图重传出口），解析开始后隐藏避免改动原图
    const allowImageEdit = !!photo && (photo.status === 'pending' || photo.status === 'error');
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
    // 方案 A（推荐）：先烤入再裁剪 —— 旋转态下裁剪坐标系错位（CSS rotate 不影响 naturalWidth，且 getBoundingClientRect 已含旋转后的视口尺寸，直接按 scale 计算会错位）
    // 最简且不易错：检测到 currentRotation!=0 时强制先固化旋转，清空选区要求用户重新框选，避免逆旋转矩阵映射的复杂实现。
    // 若需支持旋转态直接裁剪，需将 cropRect 逆旋转映射回原图坐标系（按 cos/sin 变换），实现较复杂，此处选 A 并在注释说明。
    if (currentRotation !== 0) {
        if (_isBakingRotation) {
            showToast('正在固化旋转，请稍候再裁剪', 'info');
            return;
        }
        showToast('检测到图片已旋转，需先固化旋转后再裁剪', 'warning');
        // 自动固化当前旋转，完成后清空选区并提示重选
        if (typeof bakeCurrentRotation === 'function') {
            bakeCurrentRotation().then(() => {
                activeTool = null;
                const ov = document.getElementById('cropOverlay');
                if (ov) ov.classList.add('hide');
                cropRect = { left: 0, top: 0, width: 0, height: 0 };
                updateToolbarButtonStates();
                showToast('旋转已固化，请重新拖拽框选裁剪区域', 'info');
            });
        } else {
            showToast('请先点击“旋转 90°”完成固化或点“重置”后再框选裁剪', 'warning');
        }
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
            // 非批量：记录烤后预览 URL 到 _bakedPreviewUrl 体系，reset 时可回收；同时兼容旧逻辑直接写 img.src
            revokeBakedPreviewUrl();
            _bakedPreviewUrl = croppedUrl;
            img.src = croppedUrl;
            setNonWebPreviewHint(false, croppedFile);
        }

        activeTool = null;
        updateToolbarButtonStates();
    }, 'image/jpeg', 0.95);
}

/**
 * 将当前 visual 旋转（currentRotation）烤入文件，避免仅 CSS 导致上传仍是原文件。
 * - 按 0°/180° 保持 w/h，90°/270° 交换 w/h 创建 canvas
 * - ctx.translate(w/2,h/2); ctx.rotate(angle); ctx.drawImage(img, -nw/2, -nh/2)
 * - toBlob → new File 覆盖 selectedFile 与 photo.file，预览更新为 blob URL，currentRotation 归 0
 * - 保留 originalFile/originalObjectUrl 与 photo.originalFile 不被覆盖，reset 可回原
 * - HEIC 等 isNonWebImageFile 已转 JPEG 后才可 canvas；否则仅重置视觉
 * - 烤入期间禁用“开始 AI 解析”等按钮防竞态
 */
function bakeCurrentRotation() {
    return new Promise((resolve) => {
        // F4：守卫置位于入口——img 等加载的异步间隙里二次旋转也可能并发烘焙，
        // 只在 toBlob 前置位太晚；done() 统一清位。
        _isBakingRotation = true;
        const done = (v) => { _isBakingRotation = false; resolve(v); };
        if (currentRotation === 0) { done(true); return; }
        const img = document.getElementById('previewImg');
        const photo = (typeof getActivePhoto === 'function') ? getActivePhoto() : null;
        let currFile = (photo && photo.file) || selectedFile || originalFile;
        if (!currFile && img && img.src && !img.src.startsWith('data:image/svg')) {
            const defaultName = (typeof currentReceiptId !== 'undefined' && currentReceiptId) ? `receipt_${currentReceiptId}.jpg` : 'receipt.jpg';
            currFile = new File([], defaultName, { type: 'image/jpeg' });
        }
        if (!currFile) {
            currentRotation = 0;
            if (img) applyImgTransform();
            done(true);
            return;
        }
        if (isNonWebImageFile(currFile)) {
            showToast('该格式浏览器无法直接旋转预览，已重置旋转角度；解析时服务端会自动处理', 'info');
            currentRotation = 0;
            if (img) applyImgTransform();
            done(true);
            return;
        }
        if (!img || !img.src || img.src.startsWith('data:image/svg')) {
            currentRotation = 0;
            if (img) applyImgTransform();
            done(true);
            return;
        }
        if (!img.complete || !img.naturalWidth || !img.naturalHeight) {
            const onLoad = () => {
                img.removeEventListener('load', onLoad);
                img.removeEventListener('error', onError);
                doBake();
            };
            const onError = () => {
                img.removeEventListener('load', onLoad);
                img.removeEventListener('error', onError);
                showToast('图片加载失败，无法旋转', 'error');
                currentRotation = 0;
                applyImgTransform();
                done(false);
            };
            img.addEventListener('load', onLoad);
            img.addEventListener('error', onError);
            // 若已 complete 但 naturalWidth 仍 0，延迟一帧再试
            if (img.complete) setTimeout(onLoad, 50);
            return;
        }
        doBake();
        function doBake() {
            const nw = img.naturalWidth;
            const nh = img.naturalHeight;
            if (!nw || !nh) {
                currentRotation = 0;
                applyImgTransform();
                done(true);
                return;
            }
            const angle = ((currentRotation % 360) + 360) % 360;
            const isSwap = angle === 90 || angle === 270;
            const canvasW = isSwap ? nh : nw;
            const canvasH = isSwap ? nw : nh;
            const canvas = document.createElement('canvas');
            canvas.width = canvasW;
            canvas.height = canvasH;
            const ctx = canvas.getContext('2d');
            if (!ctx) {
                currentRotation = 0;
                applyImgTransform();
                done(false);
                return;
            }
            ctx.fillStyle = '#ffffff';
            ctx.fillRect(0, 0, canvasW, canvasH);
            ctx.translate(canvasW / 2, canvasH / 2);
            ctx.rotate(angle * Math.PI / 180);
            try {
                ctx.drawImage(img, -nw / 2, -nh / 2, nw, nh);
            } catch (e) {
                console.error('bake rotate drawImage failed', e);
                currentRotation = 0;
                applyImgTransform();
                done(false);
                return;
            }
            _isBakingRotation = true;
            const triggerBtns = document.querySelectorAll('button[data-csp-click*="triggerAnalysisNow"]');
            triggerBtns.forEach(b => { b.disabled = true; });
            const rotateBtn = document.getElementById('btnRotate');
            if (rotateBtn) rotateBtn.disabled = true;
            canvas.toBlob((blob) => {
                _isBakingRotation = false;
                triggerBtns.forEach(b => { b.disabled = false; });
                if (rotateBtn) rotateBtn.disabled = false;
                if (!blob) {
                    console.warn('canvas.toBlob returned null');
                    currentRotation = 0;
                    applyImgTransform();
                    done(false);
                    return;
                }
                _hasRotatedFile = true;
                const baseName = (currFile.name || 'receipt').replace(/\.[^.]+$/, '') || 'receipt';
                const newFile = new File([blob], baseName + '.jpg', { type: 'image/jpeg', lastModified: Date.now() });
                if (currFile._convertedFromHeic) newFile._convertedFromHeic = true;
                if (currFile._originalName) newFile._originalName = currFile._originalName;
                selectedFile = newFile;
                if (photo) {
                    if (photo.croppedObjectUrl) {
                        try { URL.revokeObjectURL(photo.croppedObjectUrl); } catch (e) {}
                    }
                    const newUrl = URL.createObjectURL(blob);
                    photo.croppedObjectUrl = newUrl;
                    photo.file = newFile;
                    setMainPreview(photo);
                } else {
                    revokeBakedPreviewUrl();
                    _bakedPreviewUrl = URL.createObjectURL(blob);
                    const imgEl = document.getElementById('previewImg');
                    if (imgEl) imgEl.src = _bakedPreviewUrl;
                    setNonWebPreviewHint(false, newFile);
                }
                currentRotation = 0;
                applyImgTransform();
                updateToolbarButtonStates();
                activeTool = null;
                const overlay = document.getElementById('cropOverlay');
                if (overlay) overlay.classList.add('hide');
                cropRect = { left: 0, top: 0, width: 0, height: 0 };
                showToast('已固化旋转并更新待上传文件', 'success');
                updateRotationRecoveryUI();
                done(true);
            }, 'image/jpeg', 0.95);
        }
    });
}

function updateRotationRecoveryUI() {
    const prefillCard = document.getElementById('prefillFormCard');
    const isPrefillVisible = prefillCard && !prefillCard.classList.contains('hide') && prefillCard.style.display !== 'none';
    if (isPrefillVisible) {
        _hasRotatedAfterParse = true;
        const btnReparseRotated = document.getElementById('btnReparseRotated');
        if (btnReparseRotated) {
            btnReparseRotated.style.display = '';
        }
        showToast('已旋转至新视角，可点击按当前视角重新识别', 'info');
    }

    const errorCard = document.getElementById('errorCard');
    const isErrorVisible = errorCard && !errorCard.classList.contains('hide') && errorCard.style.display !== 'none';
    if (isErrorVisible) {
        const btnRetryRotated = document.getElementById('btnRetryRotated');
        if (btnRetryRotated) {
            btnRetryRotated.classList.remove('hide');
        }
        showToast('已旋转至新视角，点击按当前视角重试', 'info');
    }
}
window.updateRotationRecoveryUI = updateRotationRecoveryUI;

function rotateImg() {
    if (_isBakingRotation) {
        showToast('正在处理旋转，请稍候', 'info');
        return;
    }
    const photo = (typeof getActivePhoto === 'function') ? getActivePhoto() : null;
    const currFile = (photo && photo.file) || selectedFile;
    if (currFile && isNonWebImageFile(currFile)) {
        currentRotation = (currentRotation + 90) % 360;
        applyImgTransform();
        showToast('HEIC 照片已记录旋转，解析时将自动校正', 'info');
        return;
    }
    currentRotation = (currentRotation + 90) % 360;
    // 即时视觉反馈，再异步烤入固化（避免 toBlob 期间黑屏）
    applyImgTransform();
    bakeCurrentRotation();
}

function resetImgTransform() {
    currentZoom = 1.0;
    currentRotation = 0;
    panX = 0;
    panY = 0;
    isFocalZoomed = false;
    activeTool = null;
    _hasRotatedFile = false;
    _hasRotatedAfterParse = false;
    const btnRot = document.getElementById('btnReparseRotated');
    if (btnRot) btnRot.style.display = 'none';
    const btnErrRot = document.getElementById('btnRetryRotated');
    if (btnErrRot) btnErrRot.classList.add('hide');

    // 核心重置逻辑：在启动分析之前，按重置可还原为原始未裁剪照片
    // 注意：HEIC 等绝不能写回 objectUrl（浏览器黑屏）；走 setMainPreview 保持常驻提示
    // 烤入后 reset 仍能回到原始 originalFile（保留 originalFile/originalObjectUrl 不被覆盖，photo.originalFile 同理）
    const img = document.getElementById('previewImg');

    const photo = (typeof BatchUploader !== 'undefined') ? getActivePhoto() : null;
    if (photo) {
        if (photo.croppedObjectUrl) {
            try { URL.revokeObjectURL(photo.croppedObjectUrl); } catch(e) {}
            photo.croppedObjectUrl = null;
            photo.cropped = false;
            photo.file = photo.originalFile || photo.file;
        }
        // 清理非批量路径的烤后 URL 残留（若曾混用）
        revokeBakedPreviewUrl();
        selectedFile = photo.file;
        setMainPreview(photo);
    } else if (originalFile) {
        revokeBakedPreviewUrl();
        selectedFile = originalFile;
        setMainPreviewFromFile(originalFile, originalObjectUrl);
    } else {
        // 无 photo 也无 originalFile 但有 _bakedPreviewUrl 残留，清理
        revokeBakedPreviewUrl();
    }

    if (img) {
        img.style.transformOrigin = 'center center';
        applyImgTransform();
    }
    cropRect = { left: 0, top: 0, width: 0, height: 0 };
    const overlay = document.getElementById('cropOverlay');
    if (overlay) overlay.classList.add('hide');
    updateToolbarButtonStates();
}

function applyImgTransform() {
    const img = document.getElementById('previewImg');
    if (!img) return;
    img.style.transform = `translate(${panX}px, ${panY}px) scale(${currentZoom}) rotate(${currentRotation}deg)`;
}

window.retryFromErrorCard = retryFromErrorCard;
window.convertManualFromErrorCard = convertManualFromErrorCard;
// D12：新建手工单入口（index.html 内联 onclick 引用）
window.startManualEntry = startManualEntry;
window.abortLoadingAndSwitchToManual = abortLoadingAndSwitchToManual;
window.syncManualEntryVisibility = syncManualEntryVisibility;
// Q35：归档 error 行入口 —— retryReceiptFromArchive / convertReceiptFromArchive 已随域
// 迁至 archive_list.js，导出随迁（留在 main.js 会因函数不在本文件而加载期 ReferenceError）。
