#!/usr/bin/env node
/**
 * UAT-036：静态契约——库存/餐品/scan 指标失败态为 — + 错误 + 重试，含请求世代号。
 * 用法：node demo/tests/frontend_uat036_kpi_fail_harness.js
 */
'use strict';
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '../..');
let pass = 0;
let fail = 0;
function check(name, cond) {
  if (cond) { pass += 1; console.log('[PASS]', name); }
  else { fail += 1; console.log('[FAIL]', name); }
}

const inv = fs.readFileSync(path.join(ROOT, 'demo/static/js/inventory_board.js'), 'utf8');
const dish = fs.readFileSync(path.join(ROOT, 'demo/static/js/dish_bom.js'), 'utf8');
const dishC = fs.readFileSync(path.join(ROOT, 'demo/static/js/dish_consumption.js'), 'utf8');
const wb = fs.readFileSync(path.join(ROOT, 'demo/static/js/workbench_queue.js'), 'utf8');
const sa = fs.readFileSync(path.join(ROOT, 'demo/static/js/supplier_archive.js'), 'utf8');
const arc = fs.readFileSync(path.join(ROOT, 'demo/static/js/archive_list.js'), 'utf8');

check('inventoryLoadSeq present', /let inventoryLoadSeq\s*=\s*0/.test(inv));
check('markInventoryMetricsFailed sets em-dash', /markInventoryMetricsFailed/.test(inv) && /innerText\s*=\s*'—'/.test(inv));
check('inventory retry button data-uat036', /data-uat036=["']inventory-retry["']/.test(inv));
check('inventory checks res.ok', /!res\s*\|\|\s*!res\.ok/.test(inv));
check('inventory catch guards mySeq', /if\s*\(\s*mySeq\s*!==\s*inventoryLoadSeq\s*\)\s*return/.test(inv));
check('degradeInventoryLoad calls markInventoryMetricsFailed', /function degradeInventoryLoad[\s\S]*markInventoryMetricsFailed\(\)/.test(inv));
check('clearInventoryMetricsFailed on success', /clearInventoryMetricsFailed\(\)/.test(inv));

check('dishesLoadSeq present', /let dishesLoadSeq\s*=\s*0/.test(dish));
check('markDishesLoadFailed distinct from empty copy', /餐品列表加载失败/.test(dish) && /当前不是空库/.test(dish));
check('dish retry onclick loadDishesList', /data-uat036=["']dish-retry["']/.test(dish));
check('loadDishesList guards mySeq', /mySeq\s*!==\s*dishesLoadSeq/.test(dish));

check('markDishKpiFailed sets kpi to em-dash', /function markDishKpiFailed/.test(dishC) && /kpiDishCost[\s\S]*'—'/.test(dishC));
check('dish kpi retry present', /data-uat036=["']dish-kpi-retry["']/.test(dishC));
check('dailyConsumptionReqSeq still used', /dailyConsumptionReqSeq/.test(dishC));
check('daily_consumption checks res.ok', /daily_consumption_http_/.test(dishC));

check('workbenchQueueLoadSeq present', /let workbenchQueueLoadSeq\s*=\s*0/.test(wb));
check('markWorkbenchQueueMetricsFailed sets qCount to em-dash', /markWorkbenchQueueMetricsFailed/.test(wb) && /qCountTodo[\s\S]*'—'/.test(wb));
check('workbench retry data-uat036', /data-uat036['"],\s*['"]workbench-retry['"]/.test(wb) || /workbench-retry/.test(wb));
check('updateWorkbenchQueueCounters skips when failed', /if\s*\(\s*workbenchQueueKpiFailed\s*\)\s*return/.test(wb));
check('renderWorkbenchQueueTable skips when failed', /function renderWorkbenchQueueTable[\s\S]*workbenchQueueKpiFailed/.test(wb));

check('updateTodoBar guards archive fail em-dash', /archiveKpiRetry[\s\S]*todoPendingNum[\s\S]*'—'/.test(sa) || /String\(pendingProbe\.textContent\)\.trim\(\)\s*===\s*'—'/.test(sa));
check('markArchiveKpiFailed still sets em-dash', /function markArchiveKpiFailed[\s\S]*textContent\s*=\s*'—'/.test(arc));
check('archiveLoadSeq present', /let archiveLoadSeq\s*=\s*0/.test(arc));

// Negative: do not convert all legitimate zeros in success path to em-dash only
check('inventory success still assigns meta counts (not only dash)', /totalEl\.innerText\s*=\s*meta\.total_active/.test(inv));
check('dish KPI success still uses summary zeros via renderDailyKPI', /renderDailyKPI\(data\.summary\s*\|\|\s*\{\}\)/.test(dishC));

console.log('');
console.log(pass + ' PASS / ' + fail + ' FAIL');
process.exit(fail ? 1 : 0);
