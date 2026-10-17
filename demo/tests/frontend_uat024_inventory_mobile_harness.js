#!/usr/bin/env node
/**
 * UAT-024：静态检查库存行模板与手机 CSS 契约（无浏览器）。
 * 用法：node demo/tests/frontend_uat024_inventory_mobile_harness.js
 */
'use strict';
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..', '..');
const results = [];
let failed = 0;
function check(name, ok, detail) {
    results.push({ name, ok, detail: detail || '' });
    if (!ok) failed += 1;
    console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ' — ' + detail : ''}`);
}

const invJs = fs.readFileSync(path.join(ROOT, 'demo/static/js/inventory_board.js'), 'utf8');
const style = fs.readFileSync(path.join(ROOT, 'demo/static/css/style.css'), 'utf8');
const mobile = fs.readFileSync(path.join(ROOT, 'demo/static/css/review-mobile.css'), 'utf8');
const index = fs.readFileSync(path.join(ROOT, 'demo/templates/index.html'), 'utf8');

check('row stamps data-sku-id', invJs.includes('tr.dataset.skuId'));
check('name cell data-label 品名', invJs.includes('data-label="品名"'));
check('actions cell data-label 操作', invJs.includes('data-label="操作"'));
check('inv-actions carries data-sku-id', /data-sku-id="\$\{Number\(sku\.id\)\}"/.test(invJs));
check('more menu binds same sku.id', invJs.includes("toggleInventoryMoreMenu(this, ${Number(sku.id)})"));
check('name has data-full-name', invJs.includes('data-full-name='));

check('style: inv-name-text break-word', /\.inv-name-text\s*\{[^}]*word-break:\s*break-word/s.test(style));
check('style: no break-all property on inv-name-text', (() => {
    // Only flag real CSS declarations, ignore comments that mention break-all.
    const re = /\.inv-name-text[^{]*\{[^}]*\}/g;
    let m, bad = false;
    while ((m = re.exec(style)) !== null) {
        const body = m[0].replace(/\/\*[^*]*\*+(?:[^/*][^*]*\*+)*\//g, '');
        if (/word-break\s*:\s*break-all/.test(body)) bad = true;
    }
    return !bad;
})());

check('mobile: UAT-024 block', mobile.includes('UAT-024'));
check('mobile: min-width 0 override', mobile.includes('min-width: 0 !important'));
check('mobile: card grid', mobile.includes('display: grid') && mobile.includes('#inventoryTable'));
check('mobile: 品名+操作 full width selectors',
    mobile.includes('td.inv-name-cell') && mobile.includes('td.inv-actions-cell'));
check('index: inventory-sku-table class', index.includes('inventory-sku-table'));
check('desktop min-width 640 retained', style.includes('min-width: 640px'));

console.log(`\n${results.length - failed} PASS / ${failed} FAIL`);
process.exit(failed ? 1 : 0);
