"""
Ollama Local Model Plugin (ai_registry/plugins/vlm_engines/ollama_plugin.py)
用于连接本地 Ollama 部署的开源多模态大模型 (如 LLaVA / Qwen2-VL)。
"""

class OllamaEnginePlugin:
    def __init__(self, host: str = "http://localhost:11434"):
        self.plugin_name = "ollama_local_engine"
        self.version = "1.0.0"
        self.host = host

    def get_supported_models(self) -> list:
        return ["qwen2.5-vl", "llava:13b", "minicpm-v"]

Plugin = OllamaEnginePlugin
