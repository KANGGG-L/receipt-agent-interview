// Independent UAT harness (CJS + vm). Does not reuse QA fixtures.
const fs = require('fs');
const vm = require('vm');
const jsSrc = fs.readFileSync(process.argv[2], 'utf8');

function el(attrs={}, children=[]) {
  const o = {
    attributes: {...attrs},
    children: [...children],
    textContent: '',
    className: attrs.class || '',
    id: attrs.id || '',
    title: attrs.title || '',
    getAttribute(k){ return this.attributes[k] != null ? String(this.attributes[k]) : null; },
    setAttribute(k,v){ this.attributes[k]=String(v); if(k==='title') this.title=String(v); },
    closest(sel){
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
  for (const c of children) c._parent = o;
  return o;
}

const labelEl = el({class:'csp-s-74'});
labelEl.textContent = '待处理（待办全量）';
const pendingNum = el({id:'todoPendingNum', class:'metric-value'});
pendingNum.textContent = '0';
const card = el({
  class:'metric-card csp-s-73',
  'data-todo-metric':'pending',
  'data-click-hint':'点击筛选待处理单据',
  title:'点击筛选待处理单据',
}, [labelEl, pendingNum]);
pendingNum._parent = card;
labelEl._parent = card;

const byId = {
  todoPendingNum: pendingNum,
  todoFailedNum: el({id:'todoFailedNum'}),
  todoOverdueNum: el({id:'todoOverdueNum'}),
};

const sandbox = {
  console,
  document: { getElementById(id){ return byId[id] || null; } },
  STATUS_GROUP_MAP: { pending: ['uploaded','parsing','parsed','parsed_with_warnings','edited'] },
  allArchiveReceipts: [
    { status: 'uploaded', supplier: 'Vendor_A' },
    { status: 'edited', supplier: 'Vendor_B' },
    { status: 'approved', supplier: 'Vendor_A' },
  ],
  setText(id, v){ const e = byId[id]; if (e) e.textContent = String(v); },
};
const m = jsSrc.match(/function updateTodoBar\(\) \{[\s\S]*?\n\}/);
if (!m) { console.error('UPDATE_TODOBAR_NOT_FOUND'); process.exit(2); }
vm.createContext(sandbox);
vm.runInContext(m[0] + '\nupdateTodoBar();', sandbox);

const title = card.title || '';
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
const pass = hasClick && hasStats && shimGone && labelOk && title.includes('。') && pendingNum.textContent === '2';
console.log(pass ? 'NODE_HARNESS_PASS=true' : 'NODE_HARNESS_PASS=false');
process.exit(pass ? 0 : 1);
