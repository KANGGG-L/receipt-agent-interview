"""
Receipt Vision MCP Server (ai_registry/mcp/servers/receipt_vision_mcp.py)
单据图像预处理、切片提取与 CLAHE 弱光增强服务。
"""

from typing import Dict, Any, List

class ReceiptVisionMCPServer:
    def __init__(self):
        self.server_name = "receipt-vision-preprocess"
        self.version = "1.0.0"

    def list_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "enhance_dark_receipt",
                "description": "对后厨弱光/阴影拍摄的单据执行自适应直方图均衡化 (CLAHE) 增强",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "image_path": {"type": "string"}
                    },
                    "required": ["image_path"]
                }
            }
        ]

MCPServer = ReceiptVisionMCPServer
