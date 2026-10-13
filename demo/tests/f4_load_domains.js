// F4 S4-11 共享域加载器：main.js 按域拆分后，所有 node vm 沙箱 harness 必须按
// index.html 的加载链追加载入域文件（classic script 顶层声明进入同一全局作用域，
// 与浏览器契约一致）。顺序＝index.html 中 main.js 之后的域文件顺序。
//
// 用法（沙箱建好后）：
//   const { loadF4Domains } = require('./f4_load_domains.js');
//   const notes = loadF4Domains((src, name) => vm.runInNewContext(src, sandbox, { filename: name }), {
//       overrides: { 'supplier_archive.js': process.argv[4] },  // 反证突变体可按文件名覆盖
//       skip: ['supplier_archive.js'],                          // 已单独加载的域可跳过
//       warn: m => sandboxWarns.push(m),                        // 缺省 console.warn
//   });
const fs = require('fs');
const path = require('path');

const F4_DOMAIN_FILES = [
    // F5·S4-11（2026-10-13）：main.js 收口拆分，以下 8 个域文件按 index.html
    // 顺序紧跟 main.js 之后（common.js 内含 apiFetch 装配块，须先于各域加载）。
    'common.js',
    'upload.js',
    'review_core.js',
    'workbench_queue.js',
    'review_modal.js',
    'version_conflict.js',
    'photo_sider.js',
    'finance_ops.js',
    // F3/F4 既有域文件
    'supplier_archive.js',
    'dish_consumption.js',
    'cost_report.js',
    'departments.js',
    'inventory_board.js',
    'archive_list.js',
    'kpi_trend.js',
    'admin_observation.js',
    'dish_bom.js',
    'parsing_logs.js',
    'engine_admin.js',
];

function loadF4Domains(run, opts) {
    opts = opts || {};
    const overrides = opts.overrides || {};
    const skip = opts.skip || [];
    const warn = opts.warn || (m => console.warn(m));
    const notes = [];
    for (const name of F4_DOMAIN_FILES) {
        if (skip.indexOf(name) !== -1) continue;
        const p = overrides[name] || path.join(__dirname, '..', 'static', 'js', name);
        let src;
        try { src = fs.readFileSync(p, 'utf8'); }
        catch (e) { notes.push(name + ' read note: ' + e.message); continue; }
        try { run(src, name); }
        catch (e) { notes.push(name + ' load note: ' + e.message); }
    }
    return notes;
}

module.exports = { F4_DOMAIN_FILES, loadF4Domains };
