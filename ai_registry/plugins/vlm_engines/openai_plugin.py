"""
OpenAI Compatible Engine Plugin (ai_registry/plugins/vlm_engines/openai_plugin.py)
支持任意兼容 OpenAI 接口协议的多模态网关与本地 Ollama。
"""

class OpenAIEnginePlugin:
    def __init__(self):
        self.plugin_name = "openai_compatible_engine"
        self.version = "1.1.0"
        # 历史注记：原列表含 "minimax-m3-pay"（本机 CodeBuddy CLI 的视觉模型），
        # 该 CLI 引擎已于 2026-09-02 弃用并从代码中删除，故一并移除。
        self.supported_models = ["qwen3-vl-flash", "gpt-4o", "claude-3-5-sonnet"]

    def validate_config(self, base_url: str, api_key: str, model_name: str) -> bool:
        return bool(base_url and api_key and model_name)

Plugin = OpenAIEnginePlugin
