"""
CodeBuddy CLI Engine Plugin (ai_registry/plugins/vlm_engines/codebuddy_plugin.py)
用于本地免费调用 CodeBuddy CLI 驱动 MiniMax-M3-Pay 视觉模型。
"""

class CodeBuddyEnginePlugin:
    def __init__(self):
        self.plugin_name = "codebuddy_vlm_engine"
        self.version = "1.0.0"
        self.supported_models = ["minimax-m3-pay"]

    def is_available(self) -> bool:
        import shutil
        return shutil.which("codebuddy") is not None

Plugin = CodeBuddyEnginePlugin
