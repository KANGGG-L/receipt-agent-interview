# -*- coding: utf-8 -*-
"""UAT-002: 归档待处理全量与今日待审批统计口径不同且标签解释专项测试。

核心验收与安全边界（严禁让两个数字相等，不改审批状态机）：
1. 合成 uploaded 1、edited 1、approved 1 三张单据时，归档待办数 (2) 与今日待审批数 (1) 必须不相等；
2. 批准 edited 后，相应 edited 待审批计数减 1，已入账 (approved) 单据不得出现在待审批口径中；
3. 前端 UI 组件（supplier_archive.js / workbench_queue.js）明确展示统计口径：
   - 归档待办条悬停说明准确描述 5 态集合（uploaded, parsing, parsed, parsed_with_warnings, edited）；
   - 今日待审批队列悬停说明准确描述仅 1 态集合（edited / 待老板审批）；
4. edited 状态全仓术语收敛统一为「待老板审批」，不得在界面残留「待审核」；
5. 租户边界与数据隔离严密，对 Tenant B 无意外改动。
"""

import json
import os
import subprocess
import sys
import pytest
from fastapi.testclient import TestClient

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "demo", "tests"))
sys.path.insert(0, os.path.join(REPO_ROOT, "demo"))

from app import db
from _b2_helpers import hdr, new_tenant, make_receipt, item


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def _run_node_harness(js_code: str):
    """在 Node 环境中加载必要的全局对象与待测静态 JS 脚本并执行断言。"""
    repo_root_json = json.dumps(REPO_ROOT)
    test_script_json = json.dumps("(function() {\n" + js_code + "\n})()")
    harness_code = f"""
    const fs = require('fs');
    const path = require('path');
    const vm = require('vm');

    const REPO_ROOT = {repo_root_json};
    const termsSrc = fs.readFileSync(path.join(REPO_ROOT, 'demo', 'static', 'js', 'terms.js'), 'utf8');
    const reviewCoreSrc = fs.readFileSync(path.join(REPO_ROOT, 'demo', 'static', 'js', 'review_core.js'), 'utf8');
    const archiveSrc = fs.readFileSync(path.join(REPO_ROOT, 'demo', 'static', 'js', 'supplier_archive.js'), 'utf8');
    const queueSrc = fs.readFileSync(path.join(REPO_ROOT, 'demo', 'static', 'js', 'workbench_queue.js'), 'utf8');
    const adminObsSrc = fs.readFileSync(path.join(REPO_ROOT, 'demo', 'static', 'js', 'admin_observation.js'), 'utf8');

    // 构造模拟 DOM 容器
    const elements = {{}};
    function mockElement(id, tagName = 'div') {{
        const el = {{
            id: id,
            tagName: tagName.toUpperCase(),
            textContent: '',
            innerHTML: '',
            title: '',
            attributes: {{}},
            classList: {{
                classes: new Set(),
                add(c) {{ this.classes.add(c); }},
                remove(c) {{ this.classes.delete(c); }},
                toggle(c, force) {{
                    if (force !== undefined) {{
                        if (force) this.classes.add(c); else this.classes.delete(c);
                    }} else {{
                        if (this.classes.has(c)) this.classes.delete(c); else this.classes.add(c);
                    }}
                }},
                has(c) {{ return this.classes.has(c); }}
            }},
            children: [],
            parentElement: null,
            setAttribute(k, v) {{ this.attributes[k] = String(v); }},
            getAttribute(k) {{ return this.attributes[k] !== undefined ? this.attributes[k] : null; }},
            querySelector(sel) {{
                if (sel === '.csp-s-74' || sel === '.metric-label') return this.children[0] || null;
                return null;
            }},
            querySelectorAll(sel) {{
                return [];
            }},
            closest(sel) {{
                if (sel === '.metric-card' || sel === '.card') return this.parentElement || this;
                return null;
            }}
        }};
        return el;
    }}

    const cardPending = mockElement('metricCardPending');
    cardPending.setAttribute('data-todo-metric', 'pending');
    cardPending.setAttribute('data-click-hint', '点击筛选待处理单据');
    cardPending.title = '点击筛选待处理单据';
    const labelPending = mockElement('labelPending');
    labelPending.textContent = '待处理（待办全量）';
    const numPending = mockElement('todoPendingNum');
    cardPending.children.push(labelPending, numPending);
    labelPending.parentElement = cardPending;
    numPending.parentElement = cardPending;

    const numFailed = mockElement('todoFailedNum');
    const numOverdue = mockElement('todoOverdueNum');

    const qCountTodo = mockElement('qCountTodo');
    const qCountReview = mockElement('qCountReview');
    const qCountApprove = mockElement('qCountApprove');
    const qCountIssues = mockElement('qCountIssues');
    const qTotalBadge = mockElement('workbenchQueueTotalBadge');
    const queueTabApprove = mockElement('queueTabApprove', 'button');

    elements['metricCardPending'] = cardPending;
    elements['todoPendingNum'] = numPending;
    elements['todoFailedNum'] = numFailed;
    elements['todoOverdueNum'] = numOverdue;
    elements['qCountTodo'] = qCountTodo;
    elements['qCountReview'] = qCountReview;
    elements['qCountApprove'] = qCountApprove;
    elements['qCountIssues'] = qCountIssues;
    elements['workbenchQueueTotalBadge'] = qTotalBadge;
    elements['queueTabApprove'] = queueTabApprove;

    const documentMock = {{
        getElementById(id) {{
            return elements[id] || null;
        }},
        querySelectorAll(sel) {{
            return [];
        }},
        createElement(tag) {{
            return mockElement('dyn_' + Math.random(), tag);
        }},
        addEventListener() {{}},
        removeEventListener() {{}}
    }};

    const sandbox = {{
        console: console,
        window: {{
            addEventListener() {{}},
            removeEventListener() {{}}
        }},
        document: documentMock,
        allArchiveReceipts: [],
        cachedWorkbenchQueueData: [],
        currentWorkbenchQueueFilter: 'todo',
        currentRole: 'owner',
        w2Escape: (s) => String(s || ''),
        setText: (id, val) => {{
            const el = documentMock.getElementById(id);
            if (el) el.textContent = String(val);
        }},
        i18nLabel: (k, def) => def
    }};
    sandbox.window = sandbox;
    sandbox.global = sandbox;

    vm.createContext(sandbox);
    vm.runInContext(termsSrc, sandbox, {{ filename: 'terms.js' }});
    vm.runInContext(reviewCoreSrc, sandbox, {{ filename: 'review_core.js' }});
    vm.runInContext(archiveSrc, sandbox, {{ filename: 'supplier_archive.js' }});
    vm.runInContext(queueSrc, sandbox, {{ filename: 'workbench_queue.js' }});

    // 运行测试断言（在 sandbox 上下文内执行，确保能访问全部作用域变量与函数）
    const testScript = {test_script_json};
    const result = vm.runInContext(testScript, sandbox, {{ filename: 'test_uat002_assert.js' }});

    process.stdout.write(JSON.stringify(result));
    """;
    proc = subprocess.run(["node", "-e", harness_code], capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


# =========================================================================
# 准则 1 & 3：合成 uploaded 1、edited 1、approved 1，两计数器数字必须不相等且各自准确
# =========================================================================
def test_01_synthetic_three_receipts_archive_and_today_counters_differ(client):
    """AC 1: 合成 uploaded 1, edited 1, approved 1，归档待办数 (2) 与今日待审批数 (1) 必须不相等且各自精确匹配。"""
    tn = new_tenant()

    # 1. 创建 uploaded 单据
    rid_uploaded = db.create_receipt(supplier_name="Vendor_A", status="uploaded", tenant_id=tn)
    assert rid_uploaded, "无法创建 uploaded 单据"

    # 2. 创建 edited 单据（店员保存人工核对修改，状态变为 edited）
    rid_edited, _ = make_receipt(
        client, tn,
        items=[item("原料白菜", 10, "斤", 2.0)],
        total=20.0,
        supplier="Vendor_A",
        approve=False,
    )
    assert rid_edited, "无法创建 edited 单据"
    row_edited = db.get_receipt_row(rid_edited)
    assert row_edited.status == "edited", f"预期 edited 状态，实际为 {row_edited.status}"

    # 3. 创建 approved 单据（已审批入账入库）
    rid_approved, _ = make_receipt(
        client, tn,
        items=[item("生姜", 5, "斤", 4.0)],
        total=20.0,
        supplier="Vendor_A",
        approve=True,
    )
    assert rid_approved, "无法创建 approved 单据"
    row_approved = db.get_receipt_row(rid_approved)
    assert row_approved.status == "approved", f"预期 approved 状态，实际为 {row_approved.status}"

    # 4. 从 API 读取该租户全量单据
    resp = client.get("/api/receipts", headers=hdr(tn, "owner"))
    assert resp.status_code == 200
    items_list = resp.json().get("data") or []
    assert len(items_list) == 3, f"预期 3 张单据，实际获取到 {len(items_list)}"

    # 口径集合精确推导：
    # 归档待办 (STATUS_GROUP_MAP.pending) 集合：['uploaded', 'parsing', 'parsed', 'parsed_with_warnings', 'edited']
    pending_group = {"uploaded", "parsing", "parsed", "parsed_with_warnings", "edited"}
    archive_pending = [r for r in items_list if r.get("status") in pending_group]

    # 今日待审批 (RECEIPT_PENDING_REVIEW_STATES vs edited) 集合：仅状态为 edited
    today_approve = [r for r in items_list if r.get("status") == "edited"]

    # 负向铁律：两数字必须不相等！
    assert len(archive_pending) == 2, f"归档待办预期 2 (uploaded+edited)，实测 {len(archive_pending)}"
    assert len(today_approve) == 1, f"今日待审批预期 1 (edited)，实测 {len(today_approve)}"
    assert len(archive_pending) != len(today_approve), (
        f"硬约束违规：归档待办数 ({len(archive_pending)}) 与今日待审批数 ({len(today_approve)}) 不得被强行做成相等！"
    )

    # 集合差集证明：二者差集恰为 uploaded 单据
    diff = set(r["id"] for r in archive_pending) - set(r["id"] for r in today_approve)
    assert diff == {rid_uploaded}, f"差集预期包含且仅包含 uploaded 单据 #{rid_uploaded}，实测为 {diff}"


# =========================================================================
# 准则 2 & 4：批准 edited 单据后，仅相应待审批计数减 1，已入账单据不计入待审批
# =========================================================================
def test_02_approval_transition_decrements_approval_count_only_approved_excluded(client):
    """AC 2: 批准 edited 单据后，待审批计数减 1，已入账 (approved) 不计入任何待审批口径。"""
    tn = new_tenant()

    # 1. 准备 uploaded 1, edited 1, approved 1
    rid_up = db.create_receipt(supplier_name="Vendor_A", status="uploaded", tenant_id=tn)
    rid_ed, _ = make_receipt(client, tn, items=[item("菜心", 8, "斤", 3.0)], total=24.0, supplier="Vendor_A", approve=False)
    rid_ap, _ = make_receipt(client, tn, items=[item("冬瓜", 10, "斤", 1.5)], total=15.0, supplier="Vendor_A", approve=True)

    # 审批前核验
    r_before = client.get("/api/receipts", headers=hdr(tn, "owner")).json().get("data") or []
    ed_before = [r for r in r_before if r.get("status") == "edited"]
    assert len(ed_before) == 1

    # 2. 审批 edited 单据
    row_ed = db.get_receipt_row(rid_ed)
    res_app = client.post(f"/api/receipt/{rid_ed}/approve", headers=hdr(tn, "owner"), json={"version": row_ed.version})
    assert res_app.status_code == 200 and res_app.json().get("status") == "success", f"审批失败: {res_app.text}"

    # 3. 审批后核验
    r_after = client.get("/api/receipts", headers=hdr(tn, "owner")).json().get("data") or []
    ed_after = [r for r in r_after if r.get("status") == "edited"]
    ap_after = [r for r in r_after if r.get("status") == "approved"]

    # 待审批数减 1，归零
    assert len(ed_after) == 0, "审批后待审批单据数必须减至 0"
    # 已入库单据数从 1 增加到 2
    assert len(ap_after) == 2, f"已入库单据数预期为 2，实测 {len(ap_after)}"

    # 负向安全：已 approved 的单据绝不包含在待审批口径中
    for item_approved in ap_after:
        assert item_approved.get("status") != "edited"


# =========================================================================
# 准则 1：前端 UI 组件口径可见性（悬停提示、说明文案对应真实状态集合）
# =========================================================================
def test_03_scope_labels_and_hover_explanations_in_ui_components():
    """AC 3: 归档待办条与工作台待我审批标签及悬停说明必须明确注明各自统计的状态集合。"""
    js_test = """
    // 注入合成单据数据到上下文
    allArchiveReceipts = [
        { id: 101, status: 'uploaded' },
        { id: 102, status: 'edited' },
        { id: 103, status: 'approved' }
    ];
    cachedWorkbenchQueueData = [
        { id: 101, status: 'uploaded' },
        { id: 102, status: 'edited' },
        { id: 103, status: 'approved' }
    ];

    // 执行归档待办条更新与工作台计数更新
    updateTodoBar();
    updateWorkbenchQueueCounters();

    const cardPending = document.getElementById('metricCardPending');
    const labelPending = cardPending.querySelector('.csp-s-74');
    const numPending = document.getElementById('todoPendingNum');

    const tabApprove = document.getElementById('queueTabApprove');
    const qCountApprove = document.getElementById('qCountApprove');

    return {
        todoPendingCount: numPending.textContent,
        todoPendingCardTitle: cardPending.title || '',
        todoPendingLabelText: labelPending ? labelPending.textContent.trim() : '',
        qCountApprove: qCountApprove.textContent,
        queueTabApproveTitle: tabApprove ? tabApprove.title : ''
    };
    """
    code, stdout, stderr = _run_node_harness(js_test)
    assert code == 0, f"Node 执行失败: stderr={stderr}"
    res = json.loads(stdout)

    # 归档待办数必须为 2
    assert str(res["todoPendingCount"]) == "2"
    # 归档待办卡片标题/悬停必须同时保留点击提示与 5 态口径说明（UAT-002 回归修复）
    title_archive = res["todoPendingCardTitle"]
    assert "点击筛选" in title_archive, (
        f"归档待办卡片 title 必须保留点击筛选提示，实际为: '{title_archive}'"
    )
    assert "已上传" in title_archive and "待老板审批" in title_archive, (
        f"归档待办卡片 title 必须说明包含的状态集合，实际为: '{title_archive}'"
    )
    assert res.get("todoPendingLabelText") == "待处理（待办全量）", (
        f"归档待办标签须以 HTML 源头为准，不得依赖 JS shim，实际为: '{res.get('todoPendingLabelText')}'"
    )

    # 待我审批数必须为 1
    assert str(res["qCountApprove"]) == "1"
    # 今日待我审批标签标题/悬停必须注明仅统计 edited 状态
    title_approve = res["queueTabApproveTitle"]
    assert "edited" in title_approve or "待老板审批" in title_approve, (
        f"待我审批 tab title 必须说明仅统计 edited 状态，实际为: '{title_approve}'"
    )


# =========================================================================
# 准则 2：全仓 edited 状态术语收敛统一为「待老板审批」，消除「待审核」漂移
# =========================================================================
def test_04_terminology_consistency_for_edited_status():
    """AC 4: 静态源码核验：terms.js, review_core.js, workbench_queue.js, admin_observation.js 对 edited 状态统一使用「待老板审批」。"""
    with open(os.path.join(REPO_ROOT, "demo", "static", "js", "terms.js"), "r", encoding="utf-8") as f:
        terms_content = f.read()
    assert "edited: { label: '待老板审批'" in terms_content, "terms.js edited label 必须为 '待老板审批'"

    with open(os.path.join(REPO_ROOT, "demo", "static", "js", "review_core.js"), "r", encoding="utf-8") as f:
        review_content = f.read()
    assert "edited:" in review_content
    assert "_termStatus('edited', 'label')" in review_content

    # 核验 admin_observation.js：不得硬编码渲染为「待审核」
    with open(os.path.join(REPO_ROOT, "demo", "static", "js", "admin_observation.js"), "r", encoding="utf-8") as f:
        admin_content = f.read()
    # 确认 admin_observation.js 中 it.status === 'edited' 不得使用「待审核」
    assert "statusBadge = '<span class=\"badge badge-warning\" style=\"font-size:0.72rem; padding:2px 6px;\">待审核</span>'" not in admin_content, (
        "admin_observation.js 仍硬编码使用「待审核」作为 edited 状态名，必须收敛为「待老板审批」！"
    )

    with open(os.path.join(REPO_ROOT, "demo", "templates", "index.html"), "r", encoding="utf-8") as f:
        index_content = f.read()
    # 待办条 pending 卡：源头标签与 title 不得残留「待审核」
    assert 'data-todo-metric="pending"' in index_content
    assert ">待处理（待办全量）<" in index_content
    assert 'title="点击筛选待处理单据"' in index_content
    # 队列按钮「待我审批」为角色第一人称同义（并列「待我复核」），非 edited 状态漂移
    assert ">待我审批 (" in index_content or ">待我审批 (<" in index_content or '待我审批 (' in index_content

    with open(os.path.join(REPO_ROOT, "demo", "static", "js", "supplier_archive.js"), "r", encoding="utf-8") as f:
        archive_content = f.read()
    assert "textContent.trim() === '待审核'" not in archive_content, (
        "supplier_archive.js 不得保留焊死在「待审核」精确字符串上的 DOM shim"
    )


# =========================================================================
# 准则 5：租户隔离安全边界（Tenant B 数据互不可见，且前后零污染）
# =========================================================================
def test_05_tenant_isolation_boundary(client):
    """AC 5: 租户隔离测试，合成 Tenant A 单据前后，Tenant B 单据基线完全隔离且计数不受影响。"""
    t_a = new_tenant()
    t_b = new_tenant()

    # Tenant B 创建 2 张单据
    db.create_receipt(supplier_name="Vendor_B", status="uploaded", tenant_id=t_b)
    make_receipt(client, t_b, items=[item("葱", 2, "斤", 5.0)], total=10.0, supplier="Vendor_B", approve=False)

    b_before = client.get("/api/receipts", headers=hdr(t_b, "owner")).json().get("data") or []
    assert len(b_before) == 2, f"Tenant B 初始单据数应为 2，实测 {len(b_before)}"

    # Tenant A 创建单据并进行流转
    db.create_receipt(supplier_name="Vendor_A", status="uploaded", tenant_id=t_a)
    rid_a_ed, _ = make_receipt(client, t_a, items=[item("蒜", 3, "斤", 4.0)], total=12.0, supplier="Vendor_A", approve=False)
    row_a = db.get_receipt_row(rid_a_ed)
    client.post(f"/api/receipt/{rid_a_ed}/approve", headers=hdr(t_a, "owner"), json={"version": row_a.version})

    # Tenant B 重新核验
    b_after = client.get("/api/receipts", headers=hdr(t_b, "owner")).json().get("data") or []
    assert len(b_after) == 2, f"Tenant A 的改动导致 Tenant B 数据泄露或变化，实测 {len(b_after)}"

    # 跨租户越权读取阻断验证
    r_cross = client.get(f"/api/receipt/{rid_a_ed}", headers=hdr(t_b, "owner"))
    assert r_cross.status_code in [403, 404], f"跨租户越权读取必须返回 403 或 404，实测 {r_cross.status_code}"
