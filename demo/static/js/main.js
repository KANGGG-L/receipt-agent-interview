

// ==========================================================================
// 香港餐饮 AI 收据与库存管理系统 - 前端交互逻辑
// ==========================================================================
//
// 硬规则（R0 修复后设立，P0-1..P0-4 系统性整改）
// 一切进入 innerHTML 的动态插值必须过转义：
//   - HTML 文本/属性上下文 → w2Escape(...)
//   - 行内 JS 字符串字面量   → jsStr(...)（或直接改事件委托 + dataset，首选）
// 禁止把后端字段（供应商名/品名/单位/邮箱 operator/履历 details 等）
// 裸拼进 innerHTML：这些值可被 OCR 输出、文件名、注册邮箱等外部输入污染。
// 下拉选项等交互一律事件委托 + data-* 传值，不写内联 onXxx="fn(this,'插值')"。

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

// 旋转烤入状态：烘焙进行中标志与非批量单张烤后预览 URL（用于 reset 回收）
let _isBakingRotation = false;
let _bakedPreviewUrl = null;
let _hasRotatedFile = false;
let _hasRotatedAfterParse = false;
// C2：烤后预览 URL 回收统一入口（原先 7 处重复 revoke 块）
function revokeBakedPreviewUrl() {
    if (_bakedPreviewUrl) {
        try { URL.revokeObjectURL(_bakedPreviewUrl); } catch (e) {}
        _bakedPreviewUrl = null;
    }
}

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
// 「斤」按统一口径 0.5 kg（等同市斤）；港式司马斤请选用显式「司马斤」。
let availableUnits = [
    "kg", "g", "司马斤", "市斤", "斤", "两", "磅",
    "箱", "包", "件", "罐", "隻", "只", "瓶", "盒", "桶", "扎", "把", "袋"
];




 document.addEventListener('DOMContentLoaded', () => {
     initDemoRoleSwitch();       // demo：无密码 RBAC 角色下拉
     probeAuthAndEnter(false);   // P1-12: 启动鉴权探测（401 → 登录面板；AUTH_ENABLED=0 直通）
     initTabs();
     populateReceiptStatusFilter();  // 归档「单据状态」筛选下拉由 RECEIPT_STATUSES 生成
     initUpload();
    restoreBatchFromManifest(); // 刷新浏览器不丢失解析进度（从 manifest 恢复单据及解析状态）
    initFocalZoomAndCrop();
    initVendorHintSelect();     // 上传前可选供应商：恢复该设备上次选择
    updateGlobalDatalistUnits();
    loadInventoryData();
    loadSuppliersData();
    loadReceiptsHistory();
    loadDepartmentsAll();        // Wave 2（D44）：部门下拉/管理/报表共用数据源
    loadFinancePanel();         // Wave 1：首屏即拉付款域，供导航红点/顶部横幅提醒
    initDeptAdminDetails();
    initDishTab();              // Task 4: 餐品与每日消耗管理初始化
    // F-P1-3 多币种：币种切换联动金额符号渲染
    ['inpCurrency', 'arcCurrency'].forEach(id => {
        const sel = document.getElementById(id);
        if (sel) sel.addEventListener('change', renderCurrencySymbol);
    });
    renderCurrencySymbol();

    // Category 03, 04, 08: 工作台队列、分栏拖拽与全局快捷键
    if (typeof initSplitViewDivider === 'function') initSplitViewDivider();
    if (typeof initGlobalKeyboardShortcuts === 'function') initGlobalKeyboardShortcuts();
    if (typeof restoreWorkbenchQueueFilter === 'function') restoreWorkbenchQueueFilter();
    if (typeof loadWorkbenchQueue === 'function') loadWorkbenchQueue();

});

