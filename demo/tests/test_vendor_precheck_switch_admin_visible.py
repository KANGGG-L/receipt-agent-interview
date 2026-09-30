# -*- coding: utf-8 -*-
"""首轮预判开关在管理台「可被发现 + 一键开关」的判别式用例（P2b-Y3 要求②）。

why 需要它：预判开关（settings 键 `vendor_precheck_enabled`）缺省 **false** 是刻意的
（打开会真发一次模型调用、增成本，不得为了数字好看默认打开）。但「缺省关闭」只有配上
「管理台能发现它、能一键开」才是可运营的：此前管理台的系统配置表单是**硬编码字段表**
（`ADMIN_SETTINGS_FIELDS` + 各函数里特判的 `preprocess_enabled`），`vendor_precheck_enabled`
不在其中 —— 后端 `PUT /api/admin/settings` 收得了这个键，界面上却看不见、也点不到。

本文件按「界面类交付必须被判据钉住」的三件事各断言一条：
  1. **控件存在**：index.html 里有 `setVendorPrecheckEnabled` 复选框（且 id 唯一）；
  2. **真被调用**：main.js 的 load / collect / reset 三处都处理了该键
     （读了不保存、或保存了不读，都会让「一键开关」实际失效）；
  3. **后端消费方存在**：键已登记进 `SETTINGS_DEFAULTS`（否则 PUT 会被"未知配置键"挡下）
     且 `vendor_precheck.is_enabled()` 真的在 `predict_vendor` 里被调用。
另加一条防恒真的自校准：换一个不存在的 id 去 grep，命中数必须为 0（证明上面的 grep 判据
真的能区分「有」与「无」，而不是无论查什么都返回命中）。
"""

import os
import re
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="precheck_switch_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_y3_switch.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")

_REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(_REPO, "demo"))

HTML = os.path.join(_REPO, "demo", "templates", "index.html")
MAIN_JS = os.path.join(_REPO, "demo", "static", "js", "main.js")
PRECHECK = os.path.join(_REPO, "demo", "app", "services", "vendor_precheck.py")

FIELD_ID = "setVendorPrecheckEnabled"
KEY = "vendor_precheck_enabled"


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_checkbox_exists_in_admin_form():
    html = _read(HTML)
    assert html.count('id="%s"' % FIELD_ID) == 1, "管理台表单里必须有且只有一个预判开关控件"
    # 自校准：不存在的 id 命中数必须是 0（否则上面的断言可能恒真）
    assert html.count('id="setVendorPrecheckNotExist"') == 0


def test_main_js_wires_load_collect_reset():
    js = _read(MAIN_JS)
    assert "getElementById('%s')" % FIELD_ID in js, "缺少 load 侧的读取"
    assert re.search(r"payload\.%s\s*=\s*!!vp\.checked" % KEY, js), "缺少保存侧的下发"
    assert re.search(r"payload\.%s\s*=\s*!!_adminSettingsDefaults\.%s" % (KEY, KEY), js), \
        "「恢复缺省值」没有把该键带回缺省，按钮文案与实际不符"


def test_backend_key_registered_and_consumed():
    from app.services import settings_service, vendor_precheck

    assert KEY in settings_service.SETTINGS_DEFAULTS, "未登记的键会被 PUT 以「未知配置键」挡下"
    assert settings_service.SETTINGS_DEFAULTS[KEY] is False, \
        "预判必须缺省关闭（打开即真发模型调用，不得为了数字默认开启）"

    src = _read(PRECHECK)
    # 消费方：开关判定必须在 predict_vendor 内部，否则界面开关改完不生效
    body = src.split("def predict_vendor", 1)
    assert len(body) == 2 and "is_enabled()" in body[1], \
        "predict_vendor 未消费开关 -> 管理台一键开关不会真的生效"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
