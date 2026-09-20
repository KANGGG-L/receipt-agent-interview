// N5：A/B 实验覆盖管理台配置的前端提示——共享函数行为 + 单张/批量调用路径断言。
// 沿用 tests/frontend_auth_headers_test.js 的做法：真实 main.js 载入 node vm 沙箱。
// 用法（cwd = demo/）：node tests/frontend_experiment_override_test.js [static/js/main.js]
const fs = require('fs');
const path = require('path');
// 默认路径基于脚本自身位置解析，cwd 任意均可（原先的 cwd 相对默认值会让「跑齐 harness」假失败）
const vm = require('vm');

const src = fs.readFileSync(process.argv[2] || path.join(__dirname, '..', 'static', 'js', 'main.js'), 'utf8');
const sandbox = { console, setTimeout, clearTimeout };
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.self = sandbox;
// 只提供最小 DOM 面；main.js 其余顶层初始化允许失败（与 auth headers 测试同款）
sandbox.document = {
    addEventListener() {}, getElementById() { return null; },
    querySelector() { return null; }, querySelectorAll() { return []; },
    createElement() { return { style: {}, classList: { add(){}, remove(){}, toggle(){} }, appendChild(){}, addEventListener(){} }; },
    documentElement: { style: { setProperty(){} } },
    body: null, readyState: 'loading',
};

try { vm.runInNewContext(src, sandbox, { filename: 'main.js' }); }
catch (e) { console.log('load note:', e.message); }

const toasts = [];
sandbox.showToast = (msg, type, duration) => { toasts.push({ msg, type, duration }); };
const notify = sandbox.notifyExperimentOverrideIfApplied;

let pass = 0, fail = 0;
function ok(name, cond, detail) {
    if (cond) { pass++; console.log('PASS ' + name); }
    else { fail++; console.log('FAIL ' + name + ' -- ' + detail); }
}
function idxOf(marker) { return src.indexOf(marker); }
function lineOf(marker) {
    const idx = idxOf(marker);
    return idx < 0 ? -1 : src.slice(0, idx).split('\n').length;
}

ok('前置: notifyExperimentOverrideIfApplied 已导出', typeof notify === 'function',
   'typeof=' + typeof notify);

// ---- A. 共享提示函数的行为（单张与批量共用同一份文案/机制） ----
toasts.length = 0;
const retTreatment = { experiment_override: { experiment_id: 7, grp: 'treatment', model: 'm', base_url: 'b' } };
const r1 = notify(retTreatment);
ok('A1 实验组: 返回 true', r1 === true, 'return=' + r1);
ok('A2 实验组: 文案含实验号与组别',
   toasts.length === 1 && toasts[0].msg === '本次识别由实验 #7 的实验组配置覆盖',
   JSON.stringify(toasts));
ok('A3 提示为 warning 级', toasts[0] && toasts[0].type === 'warning', JSON.stringify(toasts[0]));

toasts.length = 0;
const r2 = notify({ experiment_override: { experiment_id: 12, grp: 'control' } });
ok('A4 对照组: 组别文案正确',
   r2 === true && toasts.length === 1 && toasts[0].msg === '本次识别由实验 #12 的对照组配置覆盖',
   JSON.stringify(toasts));

toasts.length = 0;
const r3 = notify({ status: 'success', data: {} });
ok('A5 无覆盖: 返回 false 且不弹提示', r3 === false && toasts.length === 0, 'return=' + r3 + ' toasts=' + toasts.length);

toasts.length = 0;
const r4 = notify({ data: { experiment_override: { experiment_id: 3, grp: 'treatment' } } });
ok('A6 data 内层回退: 同样能识别覆盖事实', r4 === true && toasts.length === 1, 'return=' + r4);

toasts.length = 0;
const r5 = notify({ experiment_override: { grp: 'treatment' } });
ok('A7 缺 experiment_id: 不误报', r5 === false && toasts.length === 0, 'return=' + r5);

toasts.length = 0;
notify(retTreatment);
const EMOJI = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}]/u;
ok('A8 文案无 emoji', !EMOJI.test(toasts[0].msg), toasts[0].msg);

// ---- B. 调用路径：单张两处（已有）+ 批量收口 settle（本轮新增） ----
const applyIdx = idxOf('function applyRecognizedResult(');
const batchStart = idxOf('function uploadBatch(');
const batchEnd = idxOf('function triggerBatchAnalysis(');
const batchRegion = (batchStart >= 0 && batchEnd > batchStart) ? src.slice(batchStart, batchEnd) : '';
const singleInBatchStart = idxOf('function uploadSinglePhoto(');
const singleInBatchRegion = (singleInBatchStart >= 0 && batchStart > singleInBatchStart)
    ? src.slice(singleInBatchStart, batchStart) : '';

ok('B1 单张路径 applyRecognizedResult 已调用（既有）',
   applyIdx > 0 && src.slice(applyIdx, applyIdx + 4000).includes('notifyExperimentOverrideIfApplied(ret);'),
   'line=' + lineOf('function applyRecognizedResult('));
ok('B2 单张路径 settleUploadResult 已调用（既有）',
   lineOf('function settleUploadResult(') > 0
   && src.slice(idxOf('function settleUploadResult('), idxOf('function settleUploadResult(') + 4000)
        .includes('notifyExperimentOverrideIfApplied(ret);'),
   'line=' + lineOf('function settleUploadResult('));
ok('B3 批量路径 uploadBatch 声明了去重标记',
   batchRegion.includes('let experimentOverrideNotified = false;'),
   'line=' + lineOf('let experimentOverrideNotified = false;'));
ok('B4 批量路径 settle 内复用了同一函数（去重后调用）',
   batchRegion.includes('experimentOverrideNotified = notifyExperimentOverrideIfApplied(ret);')
   && batchRegion.includes('if (!experimentOverrideNotified)'),
   'line=' + lineOf('experimentOverrideNotified = notifyExperimentOverrideIfApplied(ret);'));
ok('B5 批量模式下的单张路径（uploadSinglePhoto）也补上了提示（原为盲区）',
   singleInBatchRegion.includes('notifyExperimentOverrideIfApplied(ret);'),
   'line=' + lineOf('function uploadSinglePhoto('));

const total = (src.match(/notifyExperimentOverrideIfApplied\(ret\);/g) || []).length;
ok('B6 调用点共 4 处（单张 2 + 批量 2）', total === 4, 'count=' + total);

console.log('\nSUMMARY: ' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
