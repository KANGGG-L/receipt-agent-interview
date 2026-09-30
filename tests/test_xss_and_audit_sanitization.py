# -*- coding: utf-8 -*-
"""XSS immunity and audit log sensitive credential masking test suite.

Verifies:
1. GET /api/admin/engine-config masks all API key fields including rollback_snapshot.
2. PUT /api/admin/engine-config/promote and rollback mask all API key fields.
3. GET /api/admin/system-audit sanitizes all secret keys in old, new, and message strings.
4. demo/static/js/main.js escapeHtml properly escapes quotes, brackets, ampersands, and handles null/undefined.
"""

import json
import os
import re
import subprocess
import sys
import pytest
from starlette.testclient import TestClient

DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "demo"))
if DEMO_DIR not in sys.path:
    sys.path.insert(0, DEMO_DIR)

from app import db
from app.models import EngineConfig
from app.main import app


def test_engine_config_masks_api_keys():
    """Verify GET /api/admin/engine-config masks all api keys including rollback snapshot."""
    client = TestClient(app, headers={"X-Role": "admin"})
    orig_cfg = db.get_engine_config()
    try:
        cfg = db.get_engine_config()
        cfg.openai_rec_api_key = "sk-recsecretkey12345678"
        cfg.openai_aud_api_key = "sk-audsecretkey87654321"
        cfg.openai_parse_api_key = "sk-parsesecretkey11223344"
        cfg.grey_openai_rec_api_key = "sk-greyrecsecret99887766"
        cfg.grey_openai_aud_api_key = "sk-greyaudsecret55443322"
        cfg.grey_openai_parse_api_key = "sk-greyparsesecret66778899"
        cfg.rollback_snapshot = {
            "prev": {
                "recognition_engine": "openai",
                "openai_rec_api_key": "sk-prevsecretkey12345678",
                "openai_aud_api_key": "sk-prevaudsecret87654321",
                "openai_parse_api_key": "sk-prevparsesecret11223344",
            },
            "meta": {
                "action": "promote",
                "operator": "admin@example.com",
            }
        }
        db.set_engine_config(cfg)

        resp = client.get("/api/admin/engine-config")
        assert resp.status_code == 200
        raw_text = resp.text
        # Ensure no raw secret key is leaked in response
        assert "sk-recsecretkey12345678" not in raw_text
        assert "sk-audsecretkey87654321" not in raw_text
        assert "sk-parsesecretkey11223344" not in raw_text
        assert "sk-greyrecsecret99887766" not in raw_text
        assert "sk-greyaudsecret55443322" not in raw_text
        assert "sk-greyparsesecret66778899" not in raw_text
        assert "sk-prevsecretkey12345678" not in raw_text
        assert "sk-prevaudsecret87654321" not in raw_text
        assert "sk-prevparsesecret11223344" not in raw_text

        data = resp.json()["data"]
        assert data["openai_rec_api_key"] == "sk-rec****5678"
        assert data["openai_aud_api_key"] == "sk-aud****4321"
        assert data["openai_parse_api_key"] == "sk-par****3344"
        assert data["grey_openai_rec_api_key"] == "sk-gre****7766"
        assert data["grey_openai_aud_api_key"] == "sk-gre****3322"
        assert data["grey_openai_parse_api_key"] == "sk-gre****8899"

        # Check rollback_snapshot inside response data
        snap_prev = data["rollback_snapshot"]["prev"]
        assert snap_prev["openai_rec_api_key"] == "sk-pre****5678"
        assert snap_prev["openai_aud_api_key"] == "sk-pre****4321"
        # Verify GET /api/admin/grey-test also masks all keys in data.current
        resp_grey = client.get("/api/admin/grey-test")
        assert resp_grey.status_code == 200
        assert "sk-recsecretkey12345678" not in resp_grey.text
        assert resp_grey.json()["data"]["current"]["openai_rec_api_key"] == "sk-rec****5678"

        # Verify PUT /api/admin/engine-config masks keys in response
        put_resp = client.put("/api/admin/engine-config", json={
            "openai_rec_api_key": "sk-newputkey12345678",
            "openai_aud_api_key": "sk-newputaudkey87654321",
        })
        assert put_resp.status_code == 200
        assert "sk-newputkey12345678" not in put_resp.text
        assert "sk-newputaudkey87654321" not in put_resp.text
        assert put_resp.json()["data"]["openai_rec_api_key"] == "sk-new****5678"
        assert put_resp.json()["data"]["openai_aud_api_key"] == "sk-new****4321"
    finally:
        db.set_engine_config(orig_cfg)


def test_promote_and_rollback_mask_api_keys():
    """Verify promote and rollback endpoints mask all api keys in response data."""
    client = TestClient(app, headers={"X-Role": "admin"})
    orig_cfg = db.get_engine_config()
    try:
        cfg = db.get_engine_config()
        cfg.grey_enabled = True
        cfg.openai_rec_api_key = "sk-currentreckey12345678"
        cfg.openai_aud_api_key = "sk-currentaudkey87654321"
        cfg.openai_parse_api_key = "sk-currentparsekey112233"
        cfg.grey_openai_rec_api_key = "sk-greypromotekey87654321"
        cfg.grey_openai_aud_api_key = "sk-greyaudpromotekey1234"
        cfg.grey_openai_parse_api_key = "sk-greyparsepromotekey56"
        db.set_engine_config(cfg)

        # 1. Promote grey config
        resp_promote = client.put("/api/admin/engine-config/promote")
        assert resp_promote.status_code == 200
        promote_text = resp_promote.text
        assert "sk-greypromotekey87654321" not in promote_text
        assert "sk-greyaudpromotekey1234" not in promote_text
        assert "sk-greyparsepromotekey56" not in promote_text
        assert "sk-currentreckey12345678" not in promote_text

        promote_data = resp_promote.json()["data"]
        # Promoted keys should now be in regular slots, masked
        assert promote_data["openai_rec_api_key"] == "sk-gre****4321"
        assert promote_data["openai_aud_api_key"] == "sk-gre****1234"
        assert promote_data["openai_parse_api_key"] == "sk-gre****ey56"
        # Rollback snapshot prev keys should also be masked
        assert promote_data["rollback_snapshot"]["prev"]["openai_rec_api_key"] == "sk-cur****5678"

        # 2. Rollback engine config
        resp_rollback = client.put("/api/admin/engine-config/rollback")
        assert resp_rollback.status_code == 200
        rollback_text = resp_rollback.text
        assert "sk-currentreckey12345678" not in rollback_text
        assert "sk-currentaudkey87654321" not in rollback_text

        rollback_data = resp_rollback.json()["data"]
        assert rollback_data["openai_rec_api_key"] == "sk-cur****5678"
        assert rollback_data["openai_aud_api_key"] == "sk-cur****4321"
    finally:
        db.set_engine_config(orig_cfg)


def test_system_audit_masks_secrets():
    """Verify GET /api/admin/system-audit sanitizes secrets in audit log entries."""
    client = TestClient(app, headers={"X-Role": "admin"})
    secret_old = "sk-auditoldkey12345678"
    secret_new = "sk-auditnewkey87654321"
    secret_embedded = "sk-embeddedleak99887766"

    db.append_system_audit_log(
        who="admin@test.com",
        action="update_api_key",
        field="openai_rec_api_key",
        old=secret_old,
        new=secret_new
    )
    db.append_system_audit_log(
        who="admin@test.com",
        action="manual_test",
        field="custom_note",
        old=f"Found key {secret_embedded} in output",
        new="neutralized"
    )

    resp = client.get("/api/admin/system-audit")
    assert resp.status_code == 200
    raw_text = resp.text

    assert secret_old not in raw_text
    assert secret_new not in raw_text
    assert secret_embedded not in raw_text

    logs = resp.json()["data"]
    # Find matching log entries
    key_entry = next((l for l in logs if l.get("field") == "openai_rec_api_key"), None)
    assert key_entry is not None
    assert key_entry["old"] == "sk-aud****5678"
    assert key_entry["new"] == "sk-aud****4321"

    embedded_entry = next((l for l in logs if l.get("field") == "custom_note"), None)
    assert embedded_entry is not None
    assert "sk-emb****7766" in embedded_entry["old"]
    assert secret_embedded not in embedded_entry["old"]


def test_frontend_escape_html_present_and_safe():
    """Verify demo/static/js/main.js escapeHtml escapes single and double quotes, tags, ampersands."""
    js_path = os.path.abspath(os.path.join(DEMO_DIR, "static", "js", "main.js"))
    assert os.path.isfile(js_path)
    with open(js_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Verify implementation contains essential escape rules
    pattern = r"function escapeHtml\s*\([^)]*\)\s*\{([\s\S]*?)\}"
    match = re.search(pattern, content)
    assert match is not None, "escapeHtml function not found in main.js"

    fn_body = match.group(0)
    # Check that null/undefined handling and single quote replacement are present
    assert "s === null || s === undefined" in fn_body or "s == null" in fn_body
    assert "&#39;" in fn_body or "'&#39;'" in fn_body

    # Execute with Node.js to verify actual runtime behavior
    eval_script = f"""
    {fn_body}
    const tests = [
        [null, ''],
        [undefined, ''],
        ["<script>alert('xss')</script>", "&lt;script&gt;alert(&#39;xss&#39;)&lt;/script&gt;"],
        ['" onload="alert(1)"', '&quot; onload=&quot;alert(1)&quot;'],
        ["' onclick='alert(1)'", '&#39; onclick=&#39;alert(1)&#39;'],
        ['Foo & Bar <Baz>', 'Foo &amp; Bar &lt;Baz&gt;']
    ];
    for (const [input, expected] of tests) {{
        const actual = escapeHtml(input);
        if (actual !== expected) {{
            console.error(`Mismatch for input ${{JSON.stringify(input)}}: got ${{JSON.stringify(actual)}}, expected ${{JSON.stringify(expected)}}`);
            process.exit(1);
        }}
    }}
    console.log('OK');
    """
    res = subprocess.run(["node", "-e", eval_script], capture_output=True, text=True)
    assert res.returncode == 0, f"Node.js eval failed: {res.stderr}"
    assert "OK" in res.stdout


def test_export_center_esc_neutralizes_ocr_derived_markup():
    """Verify demo/static/js/export_center.js 的本地 esc() 逐值转义（Node 真实执行）。

    why: export_center.js 此前是本仓库唯一把后端字段（报表标题、供应商名、部门名、表头、
    预览单元格等 OCR 派生数据）裸拼进 innerHTML / document.write 的文件。修复引入的本地
    esc() 与 evalset.js 的 esc() 同实现（临时 div 的 textContent → innerHTML）。

    Node 里没有浏览器 DOM，故按 HTML 规范补一个最小替身：文本节点序列化只转义
    & < > 与不换行空格（引号在文本上下文不转义）。替身只服务的正是被断言的语义 ——
    「`<` / `>` 必须被吃掉」，因此恶意载荷不可能在 innerHTML / document.write 里被解析成标签。
    """
    js_path = os.path.abspath(os.path.join(DEMO_DIR, "static", "js", "export_center.js"))
    assert os.path.isfile(js_path)
    with open(js_path, "r", encoding="utf-8") as f:
        content = f.read()

    match = re.search(r"function esc\s*\([^)]*\)\s*\{([\s\S]*?)\n\}", content)
    assert match is not None, "esc function not found in export_center.js"
    fn_body = match.group(0)
    # 实现守卫：必须是「textContent → innerHTML」同源实现，不得自行拼正则（规则会漂移）
    assert "createElement" in fn_body and "textContent" in fn_body and "innerHTML" in fn_body

    malicious_img = "<img src=x onerror=alert('v')>"
    malicious_script = "<script>alert('xss_vendor')</script>"
    malicious_svg = "<svg onload=alert('item')>"
    malicious_iframe = "<iframe src='javascript:alert(1)'></iframe>"
    benign = "德利行 Tak Lee Hong"

    test_cases_json = json.dumps([
        [None, ""],
        # 文本上下文里只转义 & < >，引号原样保留（与浏览器 textContent → innerHTML 一致）
        [malicious_img, "&lt;img src=x onerror=alert('v')&gt;"],
        [malicious_script, "&lt;script&gt;alert('xss_vendor')&lt;/script&gt;"],
        [malicious_svg, "&lt;svg onload=alert('item')&gt;"],
        [malicious_iframe, "&lt;iframe src='javascript:alert(1)'&gt;&lt;/iframe&gt;"],
        ["Foo & Bar <Baz>", "Foo &amp; Bar &lt;Baz&gt;"],
        [benign, benign],
    ])

    eval_script = (
        "// 浏览器 DOM 替身：只实现 esc() 用到的 textContent → innerHTML 序列化语义\n"
        "const document = {\n"
        "    createElement() {\n"
        "        let text = '';\n"
        "        return {\n"
        "            set textContent(v) { text = v == null ? '' : String(v); },\n"
        "            get textContent() { return text; },\n"
        "            get innerHTML() {\n"
        "                return text.replace(/&/g, '&amp;').replace(/</g, '&lt;')\n"
        "                           .replace(/>/g, '&gt;').replace(/\\u00a0/g, '&nbsp;');\n"
        "            }\n"
        "        };\n"
        "    }\n"
        "};\n"
        + fn_body + "\n"
        "const testCases = " + test_cases_json + ";\n"
        "for (const [input, expected] of testCases) {\n"
        "    const escaped = esc(input);\n"
        "    if (escaped !== expected) {\n"
        "        console.error('Mismatch for ' + JSON.stringify(input) + ': got ' +"
        " JSON.stringify(escaped) + ', expected ' + JSON.stringify(expected));\n"
        "        process.exit(1);\n"
        "    }\n"
        "    if (escaped.includes('<') || escaped.includes('>')) {\n"
        "        console.error('Unescaped HTML delimiter in ' + JSON.stringify(escaped));\n"
        "        process.exit(1);\n"
        "    }\n"
        "}\n"
        "if (esc(undefined) !== '' || esc(0) !== '0') {\n"
        "    console.error('nullish / falsy 处理错误');\n"
        "    process.exit(1);\n"
        "}\n"
        "console.log('EXPORT_CENTER_ESC_VERIFIED');\n"
    )

    res = subprocess.run(["node", "-e", eval_script], capture_output=True, text=True)
    assert res.returncode == 0, f"Node.js eval failed: {res.stderr}"
    assert "EXPORT_CENTER_ESC_VERIFIED" in res.stdout
