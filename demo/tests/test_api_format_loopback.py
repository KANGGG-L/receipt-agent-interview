import unittest
from unittest.mock import patch, MagicMock
from app.models import EngineConfig
from app.api_admin import (
    EngineConfigBody,
    EngineConfigPayload,
    _normalize_and_sync_engine_updates,
    _quick_test_engine,
)
import app.db as db


class TestApiFormatLoopback(unittest.TestCase):
    def test_engine_config_defaults_and_sync(self):
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

    def test_engine_config_payload_alias_and_parsing(self):
        self.assertIs(EngineConfigPayload, EngineConfigBody)
        body = EngineConfigPayload(
            openai_rec_api_format="anthropic",
            grey_openai_rec_api_format="response",
        )
        self.assertEqual(body.openai_rec_api_format, "anthropic")
        self.assertEqual(body.grey_openai_rec_api_format, "response")

    def test_normalize_and_sync_engine_updates(self):
        cfg = EngineConfig()
        updates = _normalize_and_sync_engine_updates(
            {"openai_rec_api_format": "anthropic", "grey_api_format": "response"},
            cfg=cfg,
        )
        self.assertEqual(updates["openai_rec_api_format"], "anthropic")
        self.assertEqual(updates["api_format"], "anthropic")
        self.assertEqual(updates["grey_openai_rec_api_format"], "response")
        self.assertEqual(updates["grey_api_format"], "response")

    def test_quick_test_engine_branches(self):
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
        with open("demo/templates/index.html", "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn('id="adminOpenaiRecApiFormat"', html)
        self.assertIn('id="adminGreyOpenaiRecApiFormat"', html)
        self.assertIn('value="chat_completion"', html)
        self.assertIn('value="response"', html)
        self.assertIn('value="anthropic"', html)

    def test_main_js_data_binding(self):
        with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
            js = f.read()

        # Loading logic
        self.assertIn("adminOpenaiRecApiFormat", js)
        self.assertIn("adminGreyOpenaiRecApiFormat", js)
        self.assertIn("cfg.openai_rec_api_format || cfg.api_format || 'chat_completion'", js)
        self.assertIn("cfg.grey_openai_rec_api_format || cfg.grey_api_format || 'chat_completion'", js)

        # Saving logic
        self.assertIn("openai_rec_api_format: getVal('adminOpenaiRecApiFormat') || 'chat_completion'", js)
        self.assertIn("grey_openai_rec_api_format: getVal('adminGreyOpenaiRecApiFormat') || 'chat_completion'", js)
