"""
WhatsApp Notifier Plugin (ai_registry/plugins/notification/whatsapp_notifier.py)
面向香港餐饮老板最常用通讯软件的食材涨价与待审批预警推送插件。
"""

from typing import Dict, Any

class WhatsAppNotifierPlugin:
    def __init__(self):
        self.plugin_name = "whatsapp_hk_notifier"
        self.version = "1.0.0"

    def send_price_surge_alert(self, phone: str, item_name: str, surge_percent: float, old_price: float, new_price: float) -> bool:
        # 组装香港餐饮本地化通知文案
        message = (
            f"[WARN] 【老板请注意·食材涨价预警】\n"
            f"食材：{item_name}\n"
            f"原均价：HK$ {old_price:.2f}\n"
            f"本次进货价：HK$ {new_price:.2f} (+{surge_percent:.1f}%)\n"
            f"建议：已生成供应商议价话术，请在工作台查看。"
        )
        # 调用 WhatsApp Cloud API
        return True

Plugin = WhatsAppNotifierPlugin
