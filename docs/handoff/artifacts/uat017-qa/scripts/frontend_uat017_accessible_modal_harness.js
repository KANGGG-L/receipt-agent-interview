// UAT-017 QA harness: openAccessibleModal / Esc / focus restore / Tab trap
// Loads real review_modal.js + Esc/Tab listener excerpt + openReconModal wiring check.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = process.argv[2] || path.join(__dirname, '..', '..', '..', '..');
const reviewPath = path.join(ROOT, 'demo/static/js/review_modal.js');
const financePath = path.join(ROOT, 'demo/static/js/finance_ops.js');
const adminPath = path.join(ROOT, 'demo/static/js/admin_observation.js');

let pass = 0, fail = 0;
const failures = [];
function ok(name, cond, detail) {
  if (cond) { pass++; console.log('[OK]', name); }
  else { fail++; failures.push(name + ' -- ' + (detail || '')); console.log('[FAIL]', name, '--', detail || ''); }
}

function mkEl(props) {
  const attrs = Object.assign({}, (props && props.attrs) || {});
  const classes = new Set(String((props && props.className) || '').split(/\s+/).filter(Boolean));
  const el = {
    tagName: (props && props.tagName) || 'DIV',
    id: (props && props.id) || '',
    className: [...classes].join(' '),
    style: Object.assign({ display: '' }, (props && props.style) || {}),
    disabled: !!(props && props.disabled),
    type: (props && props.type) || '',
    value: (props && props.value) || '',
    innerText: (props && props.innerText) || '',
    textContent: (props && props.textContent) || '',
    children: (props && props.children) || [],
    parent: null,
    offsetWidth: props && props.offsetWidth != null ? props.offsetWidth : 40,
    offsetHeight: props && props.offsetHeight != null ? props.offsetHeight : 20,
    _focused: false,
    focus() { el._focused = true; document.activeElement = el; },
    blur() { el._focused = false; },
    getClientRects() { return (el.offsetWidth || el.offsetHeight) ? [{ width: el.offsetWidth, height: el.offsetHeight }] : []; },
    classList: {
      add(c) { classes.add(c); el.className = [...classes].join(' '); },
      remove(c) { classes.delete(c); el.className = [...classes].join(' '); },
      contains(c) { return classes.has(c); },
    },
    setAttribute(k, v) { attrs[k] = String(v); },
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(attrs, k) ? attrs[k] : null; },
    querySelector(sel) {
      const all = collect(el);
      if (sel.startsWith('#')) return all.find(x => x.id === sel.slice(1)) || null;
      return null;
    },
    querySelectorAll(sel) {
      const all = collect(el);
      // simplified: support the getModalFocusableElements selector by tag/type
      return all.filter(x => {
        const t = (x.tagName || '').toLowerCase();
        if (t === 'button' && !x.disabled) return true;
        if (t === 'select' && !x.disabled) return true;
        if (t === 'input' && !x.disabled && x.type !== 'hidden') return true;
        if (t === 'textarea' && !x.disabled) return true;
        if (x.getAttribute && x.getAttribute('tabindex') && x.getAttribute('tabindex') !== '-1') return true;
        return false;
      });
    },
    contains(node) {
      if (node === el) return true;
      return collect(el).includes(node);
    },
    appendChild(c) { c.parent = el; el.children.push(c); return c; },
  };
  Object.keys(attrs).forEach(k => { /* already set */ });
  if (props && props.attrs) Object.assign(attrs, props.attrs);
  return el;
}
function collect(root) {
  const out = [];
  (function walk(n) {
    (n.children || []).forEach(c => { out.push(c); walk(c); });
  })(root);
  return out;
}

const triggerBtn = mkEl({ tagName: 'BUTTON', id: 'btnOpenRecon', textContent: '发起对账' });
const period = mkEl({ tagName: 'SELECT', id: 'reconPeriodType', offsetWidth: 100, offsetHeight: 30 });
const start = mkEl({ tagName: 'INPUT', id: 'reconStart', type: 'date', offsetWidth: 100, offsetHeight: 30 });
const end = mkEl({ tagName: 'INPUT', id: 'reconEnd', type: 'date', offsetWidth: 100, offsetHeight: 30 });
const cancel = mkEl({ tagName: 'BUTTON', id: 'btnCancelReconCreate', offsetWidth: 80, offsetHeight: 30 });
const submit = mkEl({ tagName: 'BUTTON', id: 'btnSubmitReconCreate', offsetWidth: 80, offsetHeight: 30 });
const title = mkEl({ tagName: 'H3', id: 'reconCreateTitle', textContent: '发起对账', offsetWidth: 0, offsetHeight: 0 });
const hint = mkEl({ tagName: 'DIV', id: 'reconSupplierHint', offsetWidth: 0, offsetHeight: 0 });
const closeX = mkEl({ tagName: 'BUTTON', className: 'modal-close-btn', attrs: { 'aria-label': '关闭' }, offsetWidth: 24, offsetHeight: 24 });

const modal = mkEl({
  tagName: 'DIV',
  id: 'reconCreateModal',
  className: 'modal-backdrop hide',
  attrs: {
    role: 'dialog',
    'aria-modal': 'true',
    'aria-labelledby': 'reconCreateTitle',
    'aria-describedby': 'reconSupplierHint',
  },
});
const card = mkEl({ tagName: 'DIV', className: 'modal-card' });
const header = mkEl({ tagName: 'DIV', className: 'modal-header' });
const body = mkEl({ tagName: 'DIV', className: 'modal-body' });
header.appendChild(title); header.appendChild(closeX);
body.appendChild(hint); body.appendChild(period); body.appendChild(start); body.appendChild(end);
body.appendChild(cancel); body.appendChild(submit);
card.appendChild(header); card.appendChild(body);
modal.appendChild(card);

const byId = {
  reconCreateModal: modal,
  reconPeriodType: period,
  reconStart: start,
  reconEnd: end,
  btnCancelReconCreate: cancel,
  btnSubmitReconCreate: submit,
  reconCreateTitle: title,
  reconSupplierHint: hint,
  btnOpenRecon: triggerBtn,
};

const keyListeners = [];
const document = {
  activeElement: triggerBtn,
  body: mkEl({ tagName: 'BODY' }),
  getElementById(id) { return byId[id] || null; },
  querySelector(sel) {
    if (sel === '.modal-backdrop:not(.hide)') return modal.classList.contains('hide') ? null : modal;
    if (sel.startsWith('#')) return byId[sel.slice(1)] || null;
    return null;
  },
  querySelectorAll(sel) {
    if (sel === '.modal-backdrop:not(.hide)') return modal.classList.contains('hide') ? [] : [modal];
    return [];
  },
  addEventListener(type, fn) { if (type === 'keydown') keyListeners.push(fn); },
  createElement(tag) { return mkEl({ tagName: String(tag).toUpperCase() }); },
};

const sandbox = {
  console, setTimeout, clearTimeout, Promise,
  Object, Array, String, Number, Boolean, JSON, Math, Date, Error, TypeError, RegExp, Map, Set,
  document,
  window: {},
  showToast() {},
  apiFetch() { return Promise.resolve({ json: async () => ({ status: 'error' }) }); },
  TOAST_DURATION: { long: 3000 },
  toastFailDetail(x) { return String(x || ''); },
  finState: { supplierId: 1, suppliers: [{ id: 1, name: 'QA Supplier' }] },
};
sandbox.window = sandbox;
sandbox.global = sandbox;
sandbox.globalThis = sandbox;

// Load review_modal.js (defines open/closeAccessibleModal)
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(reviewPath, 'utf8'), sandbox, { filename: 'review_modal.js' });
vm.runInContext('window.__getModalStack = function(){ return _activeAccessibleModals; };', sandbox);
function stackLen(){ return vm.runInContext('(_activeAccessibleModals||[]).length', sandbox); }
function stackTrigger(){ return vm.runInContext('_activeAccessibleModals[0] && _activeAccessibleModals[0].trigger', sandbox); }


// Extract only Esc/Tab listener from admin_observation.js
const adminSrc = fs.readFileSync(adminPath, 'utf8');
const listenerStart = adminSrc.indexOf('// 全局键盘监听：无障碍模态弹窗 Esc');
ok('admin_observation has Esc/Tab listener block', listenerStart >= 0);
if (listenerStart >= 0) {
  // take from comment through end of that IIFE-ish listener (approx to next blank+function or EOF nearby)
  let chunk = adminSrc.slice(listenerStart);
  // stop at next top-level function after the listener if present
  const endMatch = chunk.match(/\nfunction |\n\/\/ ===|\nwindow\./);
  // keep first ~120 lines
  chunk = chunk.split('\n').slice(0, 90).join('\n');
  vm.runInContext(chunk, sandbox, { filename: 'admin_esc_tab.js' });
}
ok('keydown listener registered', keyListeners.length >= 1, 'count=' + keyListeners.length);

// Load openReconModal + closeModalById from finance_ops (minimal extract)
const finSrc = fs.readFileSync(financePath, 'utf8');
const closeFn = finSrc.match(/function closeModalById\(id\) \{[\s\S]*?\n\}/);
const openById = finSrc.match(/function openModalById\(id\) \{[\s\S]*?\n\}/);
const openRecon = finSrc.match(/function openReconModal\(\) \{[\s\S]*?\n\}/);
ok('extracted closeModalById', !!closeFn);
ok('extracted openReconModal', !!openRecon);
vm.runInContext(
  (closeFn ? closeFn[0] : '') + '\n' +
  (openById ? openById[0] : '') + '\n' +
  (openRecon ? openRecon[0] : '') + '\n' +
  'window.closeModalById = closeModalById;\n' +
  'window.openModalById = openModalById;\n' +
  'window.openReconModal = openReconModal;\n',
  sandbox,
  { filename: 'finance_extract.js' }
);

// --- AC checks ---
triggerBtn.focus();
ok('trigger focused before open', document.activeElement === triggerBtn);

sandbox.openReconModal();
ok('modal visible (hide removed)', !modal.classList.contains('hide'));
ok('role=dialog after open', modal.getAttribute('role') === 'dialog');
ok('aria-modal=true after open', modal.getAttribute('aria-modal') === 'true');
ok('aria-labelledby=reconCreateTitle', modal.getAttribute('aria-labelledby') === 'reconCreateTitle');
ok('aria-describedby=reconSupplierHint', modal.getAttribute('aria-describedby') === 'reconSupplierHint');
ok('preferred focus on #reconPeriodType', document.activeElement === period, 'active=' + (document.activeElement && document.activeElement.id));
ok('_activeAccessibleModals stacked', stackLen() === 1, 'len='+stackLen());
ok('stack trigger is open button', stackTrigger() === triggerBtn);

// Tab trap: from last focusable, Tab should wrap to first
const focusables = sandbox.getModalFocusableElements(modal);
ok('focusables include period/cancel/submit', focusables.length >= 3, 'n=' + focusables.length);
const first = focusables[0], last = focusables[focusables.length - 1];
last.focus();
keyListeners.forEach(fn => fn({ key: 'Tab', shiftKey: false, preventDefault() { this._pd = true; }, stopPropagation() {}, _pd: false }));
ok('Tab from last wraps to first', document.activeElement === first, 'active=' + (document.activeElement && document.activeElement.id));

first.focus();
keyListeners.forEach(fn => fn({ key: 'Tab', shiftKey: true, preventDefault() {}, stopPropagation() {} }));
ok('Shift+Tab from first wraps to last', document.activeElement === last, 'active=' + (document.activeElement && document.activeElement.id));

// Esc closes and restores trigger
period.focus();
keyListeners.forEach(fn => fn({ key: 'Escape', preventDefault() {}, stopPropagation() {} }));
ok('Esc hides modal', modal.classList.contains('hide'));
ok('Esc clears stack', stackLen() === 0, 'len='+stackLen());
ok('Esc restores trigger focus', document.activeElement === triggerBtn, 'active=' + (document.activeElement && document.activeElement.id));

// cancel path via closeModalById also restores
triggerBtn.focus();
sandbox.openReconModal();
ok('re-open works', !modal.classList.contains('hide') && stackLen() === 1, 'len='+stackLen());
sandbox.closeModalById('reconCreateModal');
ok('closeModalById hides + restores trigger', modal.classList.contains('hide') && document.activeElement === triggerBtn);

// negative: payment modal id not rewritten into stack title
ok('payment modal not in byId as rewritten', !String(modal.getAttribute('aria-labelledby') || '').includes('payment'));

console.log('\nSUMMARY PASS=' + pass + ' FAIL=' + fail);
if (failures.length) failures.forEach(f => console.log('  -', f));
process.exit(fail ? 1 : 0);
