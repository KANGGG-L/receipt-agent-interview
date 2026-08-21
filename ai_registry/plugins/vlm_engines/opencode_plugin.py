"""
Opencode CLI Engine Plugin (ai_registry/plugins/vlm_engines/opencode_plugin.py)
用于本地免费调用 Opencode CLI 驱动 MiMo-V2.5 Free 多模态模型。
"""

class OpencodeEnginePlugin:
    def __init__(self):
        self.plugin_name = "opencode_vlm_engine"
        self.version = "1.0.0"
        self.supported_models = ["mimo-v2.5-free", "kimi-k1.5"]

    def is_available(self) -> bool:
        import shutil
        return shutil.which("opencode") is not None

Plugin = OpencodeEnginePlugin
