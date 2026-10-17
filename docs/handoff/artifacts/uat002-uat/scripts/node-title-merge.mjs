// Independent UAT harness — does not import pytest fixtures.
// Simulates updateTodoBar title merge + label stability after "page load".

import { readFileSync } from 'fs';
import { createRequire } from 'module';
const require = createRequire(import.meta.url);

const jsPath = process.argv[2];
const jsSrc = readFileSync(jsPath, 'utf8');

// Minimal DOM
function el(tag, attrs={}, children=[]) {
  const o = {
    tagName: tag.toUpperCase(),
    attributes: {...attrs},
    children: [...children],
    textContent: '',
    className: attrs.class || '',
    id: attrs.id || '',
    title: attrs.title || '',
    getAttribute(k){ return this.attributes[k] != null ? String(this.attributes[k]) : null; },
    setAttribute(k,v){ this.attributes[k]=String(v); if(k==='title') this.title=String(v); },
    closest(sel){
      // only support [data-todo-metric="pending"] and .metric-card on self
      if (sel === '[data-todo-metric="pending"]') {
        if (this.attributes['data-todo-metric'] === 'pending') return this;
        return this._parent ? this._parent.closest(sel) : null;
      }
      if (sel === '.metric-card') {
        if ((this.className||'').includes('metric-card')) return this;
        return this._parent ? this._parent.closest(sel) : null;
      }
      return null;
    },
  };
  for (const c of children) { c._parent = o; }
  return o;
}

const labelEl = el('div', {class:'csp-s-74'});
labelEl.textContent = '待处理（待办全量）';
const pendingNum = el('div', {id:'todoPendingNum', class:'metric-value'});
pendingNum.textContent = '0';
const card = el('div', {
  class:'metric-card csp-s-73',
  role:'button',
  'data-todo-metric':'pending',
  'data-click-hint':'点击筛选待处理单据',
  title:'点击筛选待处理单据',
}, [labelEl, pendingNum]);
pendingNum._parent = card;
labelEl._parent = card;

const byId = { todoPendingNum: pendingNum, todoFailedNum: el('div',{id:'todoFailedNum'}), todoOverdueNum: el('div',{id:'todoOverdueNum'}), queueTabApprove: el('button',{id:'queueTabApprove'}) };
byId.queueTabApprove.textContent = '待我审批 (0)';

globalThis.document = {
  getElementById(id){ return byId[id] || null; },
};

// STATUS_GROUP_MAP subset needed by updateTodoBar
globalThis.STATUS_GROUP_MAP = {
  pending: ['uploaded','parsing','parsed','parsed_with_warnings','edited'],
};
globalThis.allArchiveReceipts = [
  { status: 'uploaded', supplier: 'Vendor_A' },
  { status: 'edited', supplier: 'Vendor_B' },
  { status: 'approved', supplier: 'Vendor_A' },
];
function setText(id, v){ const e=document.getElementById(id); if(e) e.textContent=String(v); }
globalThis.setText = setText;

// Extract and eval updateTodoBar from served JS
const m = jsSrc.match(/function updateTodoBar\(\) \{[\s\S]*?\n\}/);
if (!m) { console.error('UPDATE_TODOBAR_NOT_FOUND'); process.exit(2); }
eval(m[0]);
updateTodoBar();

const title = card.title || card.getAttribute('title') || '';
const label = labelEl.textContent.trim();
const aria = card.getAttribute('aria-label') || '';
const hasClick = title.includes('点击筛选');
const hasStats = title.includes('统计口径') && title.includes('待老板审批') && title.includes('5种');
const shimGone = !jsSrc.includes("textContent.trim() === '待审核'");
const labelOk = label === '待处理（待办全量）';
const out = {
  todoPendingCount: pendingNum.textContent,
  title,
  label,
  aria,
  hasClickHint: hasClick,
  hasStats,
  shimGone,
  labelOk,
  titleHasBoth: hasClick && hasStats,
};
console.log(JSON.stringify(out, null, 2));
const pass = hasClick && hasStats && shimGone && labelOk && title.includes('。');
console.log(pass ? 'NODE_HARNESS_PASS=true' : 'NODE_HARNESS_PASS=false');
process.exit(pass ? 0 : 1);
