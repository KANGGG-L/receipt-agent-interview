# 纯 OpenAI 兼容引擎配置重构与过期选项移除实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重构「系统与引擎配置」模块，移除全部已过期的本地 CLI 下拉与冗余模型选择控件，将其统一为原生 OpenAI 兼容双列卡片与高级折叠抽屉，实现后端双向兼容与零回归交付。

**Architecture:**
- **后端层**: `api_admin.py` 中 `set_engine_config` 与 `test_engine_config` 全面接收纯 `openai_*` 参数并自动双向同步至存量字段，快速探测移除非 OpenAI 分支；
- **模板层**: `index.html` 移除 12 个废弃下拉框与删除按钮，重构成「常规识别与常规审核」双列卡片，及「解析 LLM」与「分组灰测」高级折叠抽屉；
- **前端脚本**: `main.js` 清理已下线 DOM 读取与旧模型增删逻辑，直接双向绑定 OpenAI 字段，增加抽屉展开/收起平滑交互。

**Tech Stack:** Python 3.9+, FastAPI, Pydantic, Vanilla HTML5/CSS3/JavaScript, Pytest, Node.js (AST/Runtime test evaluation).

## Global Constraints
- 严格遵守**零 Emoji 规范**；
- 保持存量数据、环境配置与单元测试 100% 向下兼容（后端自动从 `openai_*_model` 同步 `*_model`）；
- 保持已完成的 SSRF 阻断网关、Canary Token 握手、XML 数据沙箱与敏感 Key 掩码保护不变。

---

### Task 1: 后端契约强化与双向字段对齐

**Files:**
- Modify: `demo/app/api_admin.py`
- Modify: `demo/app/models.py`
- Test: `tests/test_openai_only_engine_config.py`

**Interfaces:**
- Consumes: `demo/app/models.py:EngineConfig`, `demo/app/services/security_guard.py:validate_safe_external_url`.
- Produces:
  - `set_engine_config(body, request)`: 支持纯 `openai_*` 入参，自动同步更新 `recognition_model = openai_rec_model`、`audit_model = openai_aud_model`、`parse_llm_model = openai_parse_model` 及灰测字段，并将引擎固定为 `openai`。
  - `test_engine_config(body, request)`: 纯走 OpenAI 兼容 `/models` 轻量探测，移除非 OpenAI 逻辑。

- [ ] **Step 1: Write the failing test for backend auto-sync and pure OpenAI contract**

```python
# tests/test_openai_only_engine_config.py
import pytest
import os
import sys
from starlette.testclient import TestClient

DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "demo"))
if DEMO_DIR not in sys.path:
    sys.path.insert(0, DEMO_DIR)

from app import db
from app.main import app

client = TestClient(app, headers={"X-Role": "admin"})

def test_put_engine_config_pure_openai_auto_syncs_legacy_fields():
    orig_cfg = db.get_engine_config()
    try:
        payload = {
            "openai_rec_base_url": "https://api.siliconflow.cn/v1",
            "openai_rec_api_key": "sk-sync-test-key12345678",
            "openai_rec_model": "Qwen/Qwen3-VL-32B-Instruct",
            "audit_enabled": True,
            "audit_mode": "text",
            "openai_aud_base_url": "https://api.siliconflow.cn/v1",
            "openai_aud_api_key": "sk-sync-aud-key87654321",
            "openai_aud_model": "zai-org/GLM-4.5V",
            "parse_llm_enabled": True,
            "openai_parse_base_url": "https://api.siliconflow.cn/v1",
            "openai_parse_api_key": "sk-sync-parse-key11223344",
            "openai_parse_model": "meituan-longcat/LongCat-2.0",
        }
        res = client.put("/api/admin/engine-config", json=payload)
        assert res.status_code == 200
        
        cfg = db.get_engine_config()
        # 验证自动同步回填至旧模型与旧引擎字段
        assert cfg.recognition_engine.value == "openai"
        assert cfg.recognition_model == "Qwen/Qwen3-VL-32B-Instruct"
        assert cfg.audit_engine.value == "openai"
        assert cfg.audit_model == "zai-org/GLM-4.5V"
        assert cfg.parse_llm_engine.value == "openai"
        assert cfg.parse_llm_model == "meituan-longcat/LongCat-2.0"
    finally:
        db.set_engine_config(orig_cfg)
```

- [ ] **Step 2: Run test to verify it fails or needs implementation**

Run: `pytest tests/test_openai_only_engine_config.py::test_put_engine_config_pure_openai_auto_syncs_legacy_fields -v`

- [ ] **Step 3: Implement auto-sync in `demo/app/api_admin.py`**

在 `demo/app/api_admin.py` 的 `set_engine_config` 与 `test_engine_config` 中：
- 若请求中提供 `openai_rec_model`，自动同步赋给 `updates["recognition_model"]` 与 `updates["recognition_engine"] = "openai"`；
- 若提供 `openai_aud_model`，自动同步赋给 `updates["audit_model"]` 与 `updates["audit_engine"] = "openai"`；
- 若提供 `openai_parse_model`，自动同步赋给 `updates["parse_llm_model"]` 与 `updates["parse_llm_engine"] = "openai"`；
- 灰测参数（`grey_openai_rec_model` 等）同理执行同步映射；
- 移除 `_quick_test_engine` 中的本地 CLI 分支，仅保留 OpenAI 兼容 `/models` 探测。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_openai_only_engine_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add demo/app/api_admin.py demo/app/models.py tests/test_openai_only_engine_config.py
git commit -m "feat: enforce pure OpenAI engine config and auto-sync legacy model fields"
```

---

### Task 2: 界面重组与废弃控件清理

**Files:**
- Modify: `demo/templates/index.html` (lines ~1487-1780)
- Modify: `demo/static/css/style.css`
- Test: `tests/test_openai_only_engine_config.py`

**Interfaces:**
- Consumes: DOM IDs for OpenAI fields (`adminOpenaiRecBaseUrl`, `adminOpenaiRecApiKey`, etc.).
- Produces:
  - Clean Dual-Column Card Layout for Primary Recognition and Audit.
  - Collapsible Drawers for Parse LLM (`#adminParseDrawer`) and Grey Testing (`#adminGreyDrawer`).
  - Total removal of 12 legacy dropdowns and delete buttons.

- [ ] **Step 1: Write DOM structure verification test**

```python
# tests/test_openai_only_engine_config.py
def test_template_removes_legacy_dropdowns_and_presents_dual_columns():
    html_path = os.path.abspath(os.path.join(DEMO_DIR, "templates", "index.html"))
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    # 断言已彻底移除旧选择控件
    legacy_ids = [
        'id="adminRecognitionEngine"',
        'id="adminRecognitionModel"',
        'id="adminParseEngine"',
        'id="adminParseModel"',
        'id="adminAuditEngine"',
        'id="adminAuditModel"',
        'id="adminGreyRecEngine"',
        'id="adminGreyRecModel"',
        'id="adminGreyAudEngine"',
        'id="adminGreyAudModel"',
        'id="adminGreyParseEngine"',
        'id="adminGreyParseModel"',
    ]
    for lid in legacy_ids:
        assert lid not in html, f"Legacy control {lid} should be removed from template"

    # 断言存在核心双列卡片与高级抽屉结构
    assert 'id="adminRecognitionCard"' in html
    assert 'id="adminAuditCard"' in html
    assert 'id="adminParseDrawer"' in html
    assert 'id="adminGreyDrawer"' in html
    assert 'id="adminOpenaiRecBaseUrl"' in html
    assert 'id="adminOpenaiAudBaseUrl"' in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_openai_only_engine_config.py::test_template_removes_legacy_dropdowns_and_presents_dual_columns -v`
Expected: FAIL

- [ ] **Step 3: Implement template and CSS refactor**

- 在 `demo/templates/index.html` 的 `<section id="tab-engine">` 内：
  - 彻底剔除 12 个旧下拉框；
  - 构造包含 `#adminRecognitionCard` 和 `#adminAuditCard` 的自适应双列网格；
  - 构造包含 `#adminParseDrawer` 与 `#adminGreyDrawer` 的可折叠高级抽屉（带有平滑展开动画及切换指示）；
  - 保留所有必须的 OpenAI 兼容参数输入框与预设网关选择。
- 在 `demo/static/css/style.css` 中添加抽屉折叠样式：
  ```css
  .engine-drawer {
      border: 1px solid var(--border-color, #e2e8f0);
      border-radius: 8px;
      margin-top: 12px;
      background: var(--card-bg, #ffffff);
  }
  .engine-drawer-header {
      padding: 10px 14px;
      cursor: pointer;
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-weight: 600;
      user-select: none;
  }
  .engine-drawer-content {
      padding: 12px 14px;
      border-top: 1px dashed var(--border-color, #e2e8f0);
  }
  ```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_openai_only_engine_config.py::test_template_removes_legacy_dropdowns_and_presents_dual_columns -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add demo/templates/index.html demo/static/css/style.css tests/test_openai_only_engine_config.py
git commit -m "feat: restructure engine config UI to dual columns and collapsible drawers"
```

---

### Task 3: 前端交互与配置读写精简

**Files:**
- Modify: `demo/static/js/main.js` (lines ~11810-12150)
- Test: `tests/test_openai_only_engine_config.py`

**Interfaces:**
- Consumes: OpenAI fields from `#tab-engine`.
- Produces:
  - `loadAdminEngineConfig()`: 读取并直接填充 OpenAI 字段，初始化抽屉展开状态。
  - `saveAdminEngineConfig()`: 直接从 DOM 读取参数，组装带双向对齐的保存体提交。
  - `toggleEngineDrawer(drawerId)`: 展开/收起高级抽屉。
  - 移除已废弃的旧模型事件监听与空引用风险。

- [ ] **Step 1: Write test for frontend script AST / Node.js runtime validation**

```python
# tests/test_openai_only_engine_config.py
import subprocess

def test_main_js_no_longer_queries_removed_engine_controls():
    js_path = os.path.abspath(os.path.join(DEMO_DIR, "static", "js", "main.js"))
    with open(js_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 验证 saveAdminEngineConfig 与 bindAdminEngineEventsOnce 不再依赖已删除的旧控件
    for removed_id in [
        "adminRecognitionEngine",
        "adminRecognitionModel",
        "adminParseEngine",
        "adminParseModel",
        "adminAuditEngine",
        "adminAuditModel",
    ]:
        assert f"document.getElementById('{removed_id}')" not in content, f"Found lingering query for removed element {removed_id}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_openai_only_engine_config.py::test_main_js_no_longer_queries_removed_engine_controls -v`
Expected: FAIL

- [ ] **Step 3: Refactor `demo/static/js/main.js`**

- 清理 `bindAdminEngineEventsOnce`、`loadAdminEngineConfig`、`saveAdminEngineConfig`：
  - 移除对废弃下拉框的 `addEventListener`；
  - 移除 `fillModelOptions` 与 `handleModelSelectChange`；
  - `loadAdminEngineConfig` 直接回显 `cfg.openai_rec_base_url`、`cfg.openai_rec_api_key`、`cfg.openai_rec_model` 等字段；
  - `saveAdminEngineConfig` 直接采集双列与抽屉内元素，向后端发送纯 OpenAI 参数及开关；
  - 补充 `toggleEngineDrawer(drawerId)` 函数支持抽屉折叠交互。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_openai_only_engine_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add demo/static/js/main.js tests/test_openai_only_engine_config.py
git commit -m "feat: simplify main.js engine config binding and remove dead CLI dropdown logic"
```

---

### Task 4: 端到端攻防联动与全量工程回归

**Files:**
- Modify: `tests/test_openai_only_engine_config.py`
- Test: All regression suites

- [ ] **Step 1: Write full E2E flow test for engine configuration lifecycle**

在 `tests/test_openai_only_engine_config.py` 中添加端到端生命周期测试：
- 读取配置 -> 修改配置 (纯 OpenAI 参数) -> 触发探测 (验证合规公网与 SSRF 阻断) -> 保存配置 -> 刷新读取 -> 验证回显掩码与旧字段双向对齐。

- [ ] **Step 2: Run the new E2E test**

Run: `pytest tests/test_openai_only_engine_config.py -v`
Expected: PASS

- [ ] **Step 3: Run full project regression suite**

Run:
```bash
pytest tests/test_ssrf_and_credential_guard.py \
       tests/test_canary_protocol_guard.py \
       tests/test_enhanced_prompt_guard.py \
       tests/test_xss_and_audit_sanitization.py \
       tests/test_untrusted_api_security_e2e.py \
       tests/test_siliconflow_default_provider.py \
       demo/tests/test_export_center_ac.py \
       tests/test_fix_p1_governance.py -v
```
Expected: All 68+ tests PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_openai_only_engine_config.py
git commit -m "test: complete E2E validation and regression for pure OpenAI engine configuration"
```
