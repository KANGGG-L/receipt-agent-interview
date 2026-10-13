// demo/static/js/departments.js
// 部门归属与花销（Wave 2 D44）：全局部门数据、下拉/一键填充、部门管理 UI、
// 部门管理详情折叠状态（initDeptAdminDetails）。
// F4·S4-11 从 main.js 按域抽离（两段），逐字节一致；加载顺序在 main.js 之后。
// =====================================================================
// Wave 2（D44）：部门归属与花销——全局部门数据 + 下拉/一键填充工具
// =====================================================================
let allDepartments = [];          // GET /api/departments 全量（含停用）；下拉/报表自行过滤 active

// 只列 active 部门（停用部门不进"选部门"下拉，见 04 章二）
function activeDepartments() {
    return (allDepartments || []).filter(d => Number(d.active) === 1);
}

function loadDepartmentsAll() {
    return apiFetch('/api/departments')
        .then(res => res.json())
        .then(ret => {
            if (ret && ret.status === 'success') allDepartments = ret.data || [];
        })
        .catch(err => { console.error('加载部门列表失败:', err); allDepartments = []; });
}

// 生成部门下拉 options：空选项（未分配）+ active 部门；若 selectedId 是已停用部门
// （历史成本补打场景），追加并选中它——保证编辑已停用部门单据不静默丢值。
function deptSelectOptionsHtml(selectedId) {
    const cur = (selectedId != null && String(selectedId).trim() !== '')
        ? Number(selectedId) : null;
    let html = '<option value="">未分配</option>';
    activeDepartments().forEach(d => {
        const sel = (cur !== null && Number(d.id) === cur) ? ' selected' : '';
        html += `<option value="${Number(d.id)}"${sel}>${w2Escape(d.name)}</option>`;
    });
    if (cur !== null && !activeDepartments().some(d => Number(d.id) === cur)) {
        const inactiveDept = (allDepartments || []).find(d => Number(d.id) === cur);
        if (inactiveDept) {
            html += `<option value="${Number(cur)}" selected>${w2Escape(inactiveDept.name)} 已停用</option>`;
        }
    }
    return html;
}

// 把部门 options 填充进指定 <select>（单据级下拉用），保留已停用部门的当前选择
function populateDeptSelect(selectEl, selectedId) {
    if (!selectEl) return;
    selectEl.innerHTML = deptSelectOptionsHtml(selectedId);
}

// 一键填充：单据级部门 → 所有"未手动改过"的明细行（04 章三：data-manual 行跳过）
function applyDeptToAll(tableBodyId, headerSelectId) {
    const headerSel = document.getElementById(headerSelectId);
    const deptId = headerSel ? (headerSel.value || '') : '';
    const deptName = (headerSel && headerSel.selectedOptions && headerSel.selectedOptions[0])
        ? headerSel.selectedOptions[0].text : '未分配';
    const tbody = document.getElementById(tableBodyId);
    if (!tbody) return;
    let applied = 0;
    tbody.querySelectorAll('tr').forEach(tr => {
        if (tr.dataset && tr.dataset.manual === '1') return;   // 已手动改过 → 不动
        const sel = tr.querySelector('.inp-dept');
        if (sel) { sel.value = deptId; applied++; }
    });
    showToast(applied > 0
        ? `已将部门「${deptName}」应用到 ${applied} 行明细`
        : '没有可应用的明细行', 'info');
}

// ---------------------------------------------------------------------
// Wave 2（D44）：部门管理 UI（契约①–④；owner 专属增/改/停用，staff 只读）
// ---------------------------------------------------------------------
function loadDepartmentAdmin() {
    const owner = isOwnerRole();
    const addBtn = document.getElementById('btnAddDepartment');
    if (addBtn) addBtn.classList.toggle('hide', !owner);

    return apiFetch('/api/departments')
        .then(res => res.json())
        .then(ret => {
            if (!ret || ret.status !== 'success') return;
            allDepartments = ret.data || [];   // 同步全局部门数据，供下拉/报表复用
            const body = document.getElementById('departmentAdminBody');
            if (!body) return;
            body.innerHTML = '';
            const depts = allDepartments;
            if (depts.length === 0) {
                body.innerHTML = '<tr><td colspan="3" style="text-align:center; color:var(--text-muted); padding:24px;">暂无部门，管理者可点击上方「新增部门」创建</td></tr>';
                return;
            }
            depts.forEach(d => {
                const id = Number(d.id);
                const active = Number(d.active) === 1;
                const statusBadge = active
                    ? '<span class="badge badge-success">启用中</span>'
                    : '<span class="badge badge-secondary">已停用</span>';
                let actions = '<span style="color:var(--text-muted); font-size:0.75rem;">只读</span>';
                if (owner) {
                    actions = `<button class="btn btn-secondary" style="padding:2px 8px; font-size:0.75rem;"
                            onclick="renameDepartment(${id})" title="重命名部门">改名</button> `;
                    if (active) {
                        actions += `<button class="btn btn-danger" style="padding:2px 8px; font-size:0.75rem;"
                            onclick="deactivateDepartment(${id})"
                            title="停用后不再出现在选部门下拉，历史花销仍留在报表">停用</button>`;
                    } else {
                        actions += '<span style="color:var(--text-muted); font-size:0.72rem;">已停用</span>';
                    }
                }
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td>${w2Escape(d.name)}${active ? '' : ' <span style="color:var(--text-muted); font-size:0.72rem;">已停用</span>'}</td>
                    <td class="col-center">${statusBadge}</td>
                    <td class="col-center" style="white-space:nowrap;">${actions}</td>`;
                body.appendChild(tr);
            });
        })
        .catch(err => { console.error('加载部门列表失败', err); showToast('加载部门列表失败' + toastFailDetail(err), 'error'); });
}

// 契约②：新增部门（owner）
async function addDepartment() {
    const modal = document.getElementById('departmentModal');
    const title = document.getElementById('departmentModalTitle');
    const input = document.getElementById('inpDepartmentName');
    const idInput = document.getElementById('modalDepartmentId');
    if (modal && input) {
        if (title) title.innerText = '新增部门';
        input.value = '';
        if (idInput) idInput.value = '';
        modal.classList.remove('hide');
        setTimeout(() => input.focus(), 50);
        return;
    }
    const name = await uiPrompt({ title: '新增部门', label: '部门名称', confirmText: '添加' });
    if (name === null) return;
    const n = String(name).trim();
    if (!n) { showToast('部门名称不能为空', 'warning'); return; }
    _saveDepartmentApi('', n);
}

// 契约③：重命名部门（owner）
async function renameDepartment(id) {
    const dept = (allDepartments || []).find(d => Number(d.id) === Number(id));
    const cur = dept ? dept.name : '';
    const modal = document.getElementById('departmentModal');
    const title = document.getElementById('departmentModalTitle');
    const input = document.getElementById('inpDepartmentName');
    const idInput = document.getElementById('modalDepartmentId');
    if (modal && input) {
        if (title) title.innerText = '重命名部门';
        input.value = cur;
        if (idInput) idInput.value = String(id);
        modal.classList.remove('hide');
        setTimeout(() => input.focus(), 50);
        return;
    }
    const name = await uiPrompt({ title: '重命名部门', label: '新的部门名称（原名：' + cur + '）', value: cur, confirmText: '保存' });
    if (name === null) return;
    const n = String(name).trim();
    if (!n) { showToast('部门名称不能为空', 'warning'); return; }
    _saveDepartmentApi(id, n);
}

function submitDepartmentCreate() {
    const input = document.getElementById('inpDepartmentName');
    const idInput = document.getElementById('modalDepartmentId');
    if (!input) return;
    const n = input.value.trim();
    if (!n) { showToast('部门名称不能为空', 'warning'); return; }
    const id = idInput ? idInput.value.trim() : '';
    _saveDepartmentApi(id, n);
}

function _saveDepartmentApi(id, n) {
    const isEdit = !!id;
    const url = isEdit ? ('/api/departments/' + Number(id)) : '/api/departments';
    const method = isEdit ? 'PATCH' : 'POST';

    apiFetch(url, {
        method: method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: n }),
    })
    .then(res => Promise.all([res.status, res.json().catch(() => null)]))
    .then(([httpStatus, ret]) => {
        if (!ret || ret.status !== 'success') {
            console.error('[department] 保存失败', httpStatus, ret);
            showToast((isEdit ? '重命名' : '新增') + '部门失败，请稍后重试', 'error');
            return;
        }
        closeModalById('departmentModal');
        showToast((isEdit ? '已重命名为「' : '已新增部门「') + n + '」', 'success');
        const details = document.getElementById('deptAdminDetails');
        if (details) details.open = true;
        loadDepartmentAdmin();
        loadDepartmentsAll();
    })
    .catch(err => {
        console.error('部门保存异常', err);
        showToast('保存部门失败' + toastFailDetail(err), 'error');
    });
}

// 契约④：停用部门（owner，软删除；不得停用最后一个启用部门——后端 400 兜底）
async function deactivateDepartment(id) {
    const dept = (allDepartments || []).find(d => Number(d.id) === Number(id));
    const label = dept ? dept.name : ('#' + Number(id));
    const okDept = await uiConfirm({
        title: '停用部门',
        message: '确定停用部门「' + label + '」？\n停用后它不再出现在「选部门」下拉中，但以前的明细成本仍算在这个部门，花销报表照常显示。',
        confirmText: '停用',
        danger: true,
    });
    if (!okDept) return;
    apiFetch('/api/departments/' + Number(id), { method: 'DELETE' })
    .then(res => Promise.all([res.status, res.json().catch(() => null)]))
    .then(([httpStatus, ret]) => {
        if (!ret || ret.status !== 'success') {
            console.error('[department] 停用失败', httpStatus, ret);
            showToast('停用部门失败，请稍后重试', 'error');
            return;
        }
        showToast('已停用部门「' + label + '」', 'success');
        loadDepartmentAdmin();
        loadDepartmentsAll();
    })
    .catch(err => { console.error('停用部门请求异常', err); showToast('停用部门失败' + toastFailDetail(err), 'error'); });
}
function initDeptAdminDetails() {
    const details = document.getElementById('deptAdminDetails');
    if (!details) return;
    if (localStorage.getItem('dept_admin_open') === '1') {
        details.open = true;
    }
    details.addEventListener('toggle', () => {
        localStorage.setItem('dept_admin_open', details.open ? '1' : '0');
    });
}
