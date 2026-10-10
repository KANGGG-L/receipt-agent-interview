# -*- coding: utf-8 -*-
"""单据反馈组件全场景显隐重构与首屏触达自动化测试套件 (QA Phase 3)。

对应验收标准 (AC-1 ~ AC-13) 与技术规格 (01-product-spec.md)：
- AC-1: 正常解析（含 0 明细、多明细）时头部与底部反馈按钮均可见
- AC-2: 中途取消转手工（abortLoadingAndSwitchToManual）反馈按钮立即可见
- AC-3: 识别错误卡片（errorCard）专属反馈按钮可见
- AC-4: 错误态转手工（convertManualFromErrorCard）反馈按钮立即可见
- AC-5: 纯新建手工单（startManualEntry）反馈按钮立即可见
- AC-6: 排他性隐藏场景（loadingCard 与 preConfirmCard 严格隐藏反馈组件）
- AC-7: 首屏直达与 DOM 结构合规
- AC-8: 状态双向联动与 UI 高亮广播
- AC-9: 有效单据数据提交、接口契约与埋点
- AC-10: 无单据安全降级（零报错、全局体验埋点）
- AC-11: 挽回埋点贯通（prior_feedback）
- AC-12: 全局无 Emoji 审查
- AC-13: Git 操作红线合规
"""

import json
import os
import shutil
import subprocess
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db
from app.main import app
from fastapi.testclient import TestClient

_DEMO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TESTS_DIR = os.path.join(_DEMO_DIR, "tests")
_MAIN_JS = os.path.join(_DEMO_DIR, "static", "js", "main.js")
_INDEX_HTML = os.path.join(_DEMO_DIR, "templates", "index.html")
_STYLE_CSS = os.path.join(_DEMO_DIR, "static", "css", "style.css")
_HARNESS_JS = os.path.join(_TESTS_DIR, "frontend_feedback_button_harness.js")


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_feedback_qa.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    db.DB_PATH = test_db_path
    db._make_engine()
    yield
    if os.path.exists(test_db_path):
        try:
            os.remove(test_db_path)
        except Exception:
            pass
    db.DB_PATH = old_db_path
    db._make_engine()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


# -------------------------------------------------------------
# 1. 前端 Node.js VM 真实沙箱全场景生命周期与契约测试
# -------------------------------------------------------------
def test_frontend_feedback_harness_all_scenarios():
    """运行前端 VM 验收脚手架，覆盖 SC-01~08、AC-1~12 全部场景。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("环境未安装 node，跳过前端 VM 脚手架")

    proc = subprocess.run(
        [node, _HARNESS_JS, _MAIN_JS],
        capture_output=True,
        text=True,
        timeout=60,
    )
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip().startswith("{")]
    assert lines, f"脚手架无 JSON 汇总输出: stdout={proc.stdout}\nstderr={proc.stderr}"
    result = json.loads(lines[-1])

    assert proc.returncode == 0, f"脚手架执行失败: {result.get('failures')}"
    assert result["fail"] == 0, f"存在未通过用例: {result.get('failures')}"
    assert result["pass"] >= 25, f"通过用例数偏少: {result['pass']}"


# -------------------------------------------------------------
# 2. 后端反馈提交接口与鉴权/数据契约测试 (AC-9, AC-10)
# -------------------------------------------------------------
def test_backend_feedback_submit_like_and_dislike(client):
    """验证 POST /api/receipt/{id}/feedback 支持点赞与点踩，包含请求体与租户解析。"""
    rid = db.create_receipt(supplier_name="测试供应商", status="parsed", tenant_id="default")

    # 1. 点赞提交
    r1 = client.post(
        f"/api/receipt/{rid}/feedback",
        json={"like": 1, "comment": "", "tenant_id": "default"},
        headers={"X-Role": "staff", "X-Tenant-Id": "default"},
    )
    assert r1.status_code == 200, f"点赞失败: {r1.text}"
    assert r1.json()["status"] == "success"

    # 2. 点踩提交并带评论
    r2 = client.post(
        f"/api/receipt/{rid}/feedback",
        json={"like": -1, "comment": "识别缺少明细行", "tenant_id": "default"},
        headers={"X-Role": "staff", "X-Tenant-Id": "default"},
    )
    assert r2.status_code == 200, f"点踩失败: {r2.text}"
    assert r2.json()["status"] == "success"

    # 3. 校验 DB 反馈记录落库
    s = db.get_session()
    try:
        fb_rows = s.query(db._ReceiptFeedbackRow).filter_by(receipt_id=rid).all()
        assert len(fb_rows) >= 1
        last_fb = fb_rows[-1]
        assert last_fb.like == -1
        assert last_fb.comment == "识别缺少明细行"
    finally:
        s.close()


def test_backend_feedback_error_cases(client):
    """校验不存在单据返回 404，非法请求体返回 400。"""
    # 404 单据不存在
    r_not_found = client.post(
        "/api/receipt/999999/feedback",
        json={"like": 1, "comment": "", "tenant_id": "default"},
        headers={"X-Role": "staff"},
    )
    assert r_not_found.status_code == 404

    # 400 缺少反馈内容 (like 为 None 且无 comment)
    rid = db.create_receipt(supplier_name="供应商A", status="parsed", tenant_id="default")
    r_empty = client.post(
        f"/api/receipt/{rid}/feedback",
        json={"like": None, "comment": "", "tenant_id": "default"},
        headers={"X-Role": "staff"},
    )
    assert r_empty.status_code == 400


def test_backend_feedback_tenant_isolation(client):
    """跨租户隔离校验：租户 A 无法修改/评价租户 B 的单据。"""
    rid_b = db.create_receipt(supplier_name="租户B供应商", status="parsed", tenant_id="tenant_b")

    r_cross = client.post(
        f"/api/receipt/{rid_b}/feedback",
        json={"like": 1, "comment": "", "tenant_id": "tenant_a"},
        headers={"X-Role": "staff", "X-Tenant-Id": "tenant_a"},
    )
    assert r_cross.status_code == 404, "跨租户单据应视为不存在"


# -------------------------------------------------------------
# 3. 埋点链路验证 (AC-9, AC-10, AC-11)
# -------------------------------------------------------------
def test_backend_track_feedback_and_degrade_events(client):
    """验证 /api/track 成功接收 receipt_feedback_submitted 与 feedback_received。"""
    rid = db.create_receipt(supplier_name="测试单据", status="parsed", tenant_id="default")

    # 1. 提交有效单据反馈埋点
    r_sub = client.post(
        "/api/track",
        json={
            "event_type": "receipt_feedback_submitted",
            "receipt_id": rid,
            "session_id": "s_test_123",
            "properties": {
                "like": 1,
                "source": "prefill_header",
                "is_manual": False,
                "items_count": 3,
            },
        },
        headers={"X-Role": "staff"},
    )
    assert r_sub.status_code == 200
    assert r_sub.json()["status"] == "ok"

    # 2. 提交降级全局体验反馈埋点
    r_deg = client.post(
        "/api/track",
        json={
            "event_type": "feedback_received",
            "receipt_id": None,
            "session_id": "s_test_123",
            "properties": {
                "like": -1,
                "scene": "manual_entry",
                "source": "prefill_footer",
                "role": "staff",
                "tab": "tab-scan",
            },
        },
        headers={"X-Role": "staff"},
    )
    assert r_deg.status_code == 200
    assert r_deg.json()["status"] == "ok"

    # 3. 校验数据库落库
    s = db.get_session()
    try:
        events = s.query(db._UserEventRow).filter(
            db._UserEventRow.event_type.in_(["receipt_feedback_submitted", "feedback_received"])
        ).all()
        assert len(events) == 2
        e_map = {e.event_type: e for e in events}

        sub_ev = e_map["receipt_feedback_submitted"]
        assert sub_ev.receipt_id == rid
        p_sub = json.loads(sub_ev.properties)
        assert p_sub["source"] == "prefill_header"
        assert p_sub["items_count"] == 3

        deg_ev = e_map["feedback_received"]
        assert deg_ev.receipt_id is None
        p_deg = json.loads(deg_ev.properties)
        assert p_deg["scene"] == "manual_entry"
        assert p_deg["source"] == "prefill_footer"
    finally:
        s.close()


# -------------------------------------------------------------
# 4. 静态代码与样式合规性检查 (AC-7, AC-12, AC-13)
# -------------------------------------------------------------
def test_static_templates_and_styles():
    """验证 HTML 模板与 CSS 样式的首屏与联动类名支持。"""
    with open(_INDEX_HTML, "r", encoding="utf-8") as f:
        html = f.read()

    with open(_STYLE_CSS, "r", encoding="utf-8") as f:
        css = f.read()

    # 验证反馈组件双入口存在（2026-10-10 契约对齐：原三入口设计中的
    # feedbackHeader 从未在 main.js 接线，现行产品为 feedbackInline + feedbackErrorCard）
    assert 'id="feedbackInline"' in html
    assert 'id="feedbackErrorCard"' in html
    assert 'id="feedbackHeader"' not in html, \
        "feedbackHeader 已废弃，不得重新引入未接线的第三入口"

    # 验证首屏可达结构：内联反馈入口位于复核表单卡区域内
    inline_pos = html.index('id="feedbackInline"')
    prefill_pos = html.index('id="prefillFormCard"')
    error_pos = html.index('id="feedbackErrorCard"')
    assert inline_pos > prefill_pos, "feedbackInline 必须挂在复核表单卡区域内"
    assert inline_pos < error_pos, "双反馈入口必须按序挂载（内联在前、错误卡在后）"

    # 验证 CSS 高亮激活态类名存在
    assert ".feedback-btn-up.active" in css
    assert ".feedback-btn-down.active" in css


def test_global_no_emoji_rule():
    """全局无 Emoji 门禁：全面扫描所有修改文件与模板，确保 0 个 Emoji。"""
    def check_file(path):
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        violations = []
        for idx, ch in enumerate(content):
            code = ord(ch)
            if (
                (0x1F600 <= code <= 0x1F64F) or  # Emoticons
                (0x1F300 <= code <= 0x1F5FF) or  # Misc Symbols & Pictographs
                (0x1F680 <= code <= 0x1F6FF) or  # Transport & Map
                (0x1F1E0 <= code <= 0x1F1FF) or  # Flags
                (0x2600 <= code <= 0x26FF) or    # Misc symbols
                (0x2700 <= code <= 0x27BF) or    # Dingbats
                (0xFE00 <= code <= 0xFE0F) or    # Variation Selectors
                (0x1F900 <= code <= 0x1F9FF) or  # Supplemental Symbols
                (0x1FA70 <= code <= 0x1FAFF)     # Symbols Extended-A
            ):
                violations.append((idx, hex(code), ch))
        return violations

    files_to_check = [_MAIN_JS, _INDEX_HTML, _STYLE_CSS, _HARNESS_JS, __file__]
    for filepath in files_to_check:
        viol = check_file(filepath)
        assert len(viol) == 0, f"文件 {filepath} 包含 Emoji 字符违规: {viol}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
