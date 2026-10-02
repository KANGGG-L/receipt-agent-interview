import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.models import EngineConfig
from app.api_admin import (
    EngineConfigBody,
    EngineConfigPayload,
    _normalize_and_sync_engine_updates,
    _quick_test_engine,
)

# 阶段4（00-approved-plan §3.4/§7）：api_format 与 engine 枚举从 admin 契约层移除。
# 本文件原测「api_format 回环（模型层同步 + body + index.html/main.js 接线）」；
# 现改为断言「admin 契约不再接受/暴露 api_format 与 engine 枚举」。
_REMOVED_API_FORMAT_FIELDS = (
    "api_format",
    "grey_api_format",
    "openai_rec_api_format",
    "grey_openai_rec_api_format",
    "openai_aud_api_format",
    "openai_parse_api_format",
    "grey_openai_aud_api_format",
    "grey_openai_parse_api_format",
)
_REMOVED_ENGINE_FIELDS = (
    "recognition_engine",
    "audit_engine",
    "parse_llm_engine",
    "grey_recognition_engine",
    "grey_audit_engine",
    "grey_parse_llm_engine",
)
_REMOVED_API_FORMAT_IDS = (
    "adminOpenaiRecApiFormat",
    "adminOpenaiAudApiFormat",
    "adminParseOpenaiApiFormat",
    "adminGreyOpenaiRecApiFormat",
    "adminGreyOpenaiAudApiFormat",
    "adminGreyParseOpenaiApiFormat",
)


class TestApiFormatLoopback(unittest.TestCase):
    def test_engine_config_defaults_and_sync(self):
        """models.EngineConfig 仍保留 api_format（供 DB/回滚快照兼容）且默认 chat_completion。"""
        cfg = EngineConfig()
        self.assertEqual(cfg.openai_rec_api_format, "chat_completion")
        self.assertEqual(cfg.api_format, "chat_completion")
        self.assertEqual(cfg.grey_openai_rec_api_format, "chat_completion")
        self.assertEqual(cfg.grey_api_format, "chat_completion")

        # Set via openai_rec_api_format
        cfg_custom = EngineConfig(openai_rec_api_format="anthropic", grey_openai_rec_api_format="response")
        self.assertEqual(cfg_custom.openai_rec_api_format, "anthropic")
        self.assertEqual(cfg_custom.api_format, "anthropic")
        self.assertEqual(cfg_custom.grey_openai_rec_api_format, "response")
        self.assertEqual(cfg_custom.grey_api_format, "response")

        # Set via legacy api_format
        cfg_legacy = EngineConfig(api_format="response", grey_api_format="anthropic")
        self.assertEqual(cfg_legacy.openai_rec_api_format, "response")
        self.assertEqual(cfg_legacy.api_format, "response")
        self.assertEqual(cfg_legacy.grey_openai_rec_api_format, "anthropic")
        self.assertEqual(cfg_legacy.grey_api_format, "anthropic")

    def test_engine_config_body_rejects_removed_fields(self):
        """admin 契约层：EngineConfigBody 不再声明 api_format / engine 字段，extra=forbid 下必须 422。"""
        self.assertIs(EngineConfigPayload, EngineConfigBody)
        for field in _REMOVED_API_FORMAT_FIELDS + _REMOVED_ENGINE_FIELDS:
            self.assertNotIn(field, EngineConfigBody.model_fields,
                             f"{field} 应从 EngineConfigBody 移除")
            with self.assertRaises(ValidationError):
                EngineConfigPayload(**{field: "anthropic"})
            with self.assertRaises(ValidationError):
                EngineConfigPayload(**{field: "openai"})

    def test_normalize_forces_openai_and_drops_api_format_sync(self):
        """归一逻辑：不再做 api_format 双向同步，且 engine 枚举恒置 openai（不依赖入参）。"""
        from app.models import EngineKind

        cfg = EngineConfig()
        updates = _normalize_and_sync_engine_updates(
            {"openai_rec_model": "some-model"}, cfg=cfg)
        # 不再注入任何 api_format 兼容键
        for field in _REMOVED_API_FORMAT_FIELDS:
            self.assertNotIn(field, updates, f"归一不得注入已下线的 {field}")
        # engine 枚举内部恒置 openai
        for field in _REMOVED_ENGINE_FIELDS:
            self.assertEqual(updates[field], EngineKind.OPENAI)

        # 即便入参为空，engine 枚举也被内部强制为 openai
        empty = _normalize_and_sync_engine_updates({}, cfg=cfg)
        for field in _REMOVED_ENGINE_FIELDS:
            self.assertEqual(empty[field], EngineKind.OPENAI)

    def test_quick_test_engine_branches(self):
        """_quick_test_engine 保留 api_format 形参默认值（不再被 admin body 驱动，但函数分支不变）。"""
        # 1. chat_completion calls /models
        with patch("requests.get") as mock_get, patch("requests.post") as mock_post:
            mock_get.return_value.status_code = 200
            err = _quick_test_engine(
                "openai", "gpt-4o", "https://api.openai.com/v1", "sk-test", "test_rec",
                api_format="chat_completion"
            )
            self.assertIsNone(err)
            self.assertTrue(mock_get.call_args[0][0].endswith("/models"))

        # 2. response calls /models then /responses
        with patch("requests.get") as mock_get, patch("requests.post") as mock_post:
            mock_get.return_value.status_code = 404
            mock_post.return_value.status_code = 200
            err = _quick_test_engine(
                "openai", "gpt-4o", "https://api.openai.com/v1", "sk-test", "test_rec",
                api_format="response"
            )
            self.assertIsNone(err)

        # 3. anthropic calls /messages
        with patch("requests.get") as mock_get, patch("requests.post") as mock_post:
            mock_get.return_value.status_code = 404
            mock_post.return_value.status_code = 200
            err = _quick_test_engine(
                "openai", "claude-3-5-sonnet", "https://api.anthropic.com/v1", "sk-test", "test_rec",
                api_format="anthropic"
            )
            self.assertIsNone(err)
            self.assertTrue(any("/messages" in str(call) for call in mock_post.call_args_list))

    def test_index_html_dom_elements(self):
        """index.html 不再暴露 api_format 选择器（阶段4）。"""
        with open("demo/templates/index.html", "r", encoding="utf-8") as f:
            html = f.read()

        for removed_id in _REMOVED_API_FORMAT_IDS:
            self.assertNotIn(f'id="{removed_id}"', html,
                             f"api_format 选择器 {removed_id} 应从 index.html 移除")
        self.assertNotIn('name="cfg-engine-api-format"', html)
        self.assertNotIn('name="cfg-grey-engine-api-format"', html)

    def test_main_js_data_binding(self):
        """main.js 不再采集/回显/绑定 api_format（阶段4）。"""
        with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
            js = f.read()

        for removed_id in _REMOVED_API_FORMAT_IDS:
            self.assertNotIn(removed_id, js,
                             f"main.js 仍需清理 api_format 控件引用 {removed_id}")
        for removed_binding in _REMOVED_API_FORMAT_FIELDS:
            self.assertNotIn(removed_binding, js,
                             f"main.js 仍需清理 api_format 字段 {removed_binding}")


if __name__ == "__main__":
    unittest.main()
