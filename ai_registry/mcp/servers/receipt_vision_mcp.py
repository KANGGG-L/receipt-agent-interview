"""
Receipt Vision MCP Server (ai_registry/mcp/servers/receipt_vision_mcp.py)
单据图像预处理、切片提取与 CLAHE 弱光增强服务。

只允许处理上传目录里的图片（取文件名后在上传目录内解析，拒绝路径穿越与绝对路径），
输出写到系统临时目录，不覆盖原图。
"""

import os
import tempfile
from typing import Dict, Any, List

try:
    from ._protocol import MCPServerBase, ToolError
except ImportError:
    from _protocol import MCPServerBase, ToolError


class ReceiptVisionMCPServer(MCPServerBase):
    def __init__(self, upload_dir: str = ""):
        self.server_name = "receipt-vision-preprocess"
        self.version = "1.0.0"
        self.upload_dir = upload_dir

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

    def _handlers(self):
        return {"enhance_dark_receipt": self.enhance_dark_receipt}

    def _resolve(self, image_path: str) -> str:
        base = self.upload_dir
        if not base:
            from app.api_receipts import UPLOAD_DIR
            base = str(UPLOAD_DIR)
        name = os.path.basename(str(image_path).strip())
        full = os.path.realpath(os.path.join(base, name))
        if not name or os.path.dirname(full) != os.path.realpath(base) or not os.path.isfile(full):
            raise ToolError("只能处理上传目录里已存在的图片")
        return full

    def enhance_dark_receipt(self, image_path: str) -> Dict[str, Any]:
        import cv2
        from app.services import preprocess
        src = self._resolve(image_path)
        image = preprocess._imread(src)
        if image is None:
            raise ToolError("图片读不出来（文件损坏或格式不支持）")
        out = preprocess.enhance(image)
        fd, out_path = tempfile.mkstemp(prefix="mcp_enhanced_", suffix=".jpg")
        os.close(fd)
        if not cv2.imwrite(out_path, out):
            os.remove(out_path)
            raise ToolError("增强后的图片写不出来")
        return {"output_path": out_path}


MCPServer = ReceiptVisionMCPServer

if __name__ == "__main__":
    MCPServer().serve_stdio()
