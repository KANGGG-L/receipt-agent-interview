"""
Chroma Vendor Memory MCP Server (ai_registry/mcp/servers/chroma_vendor_memory_mcp.py)
基于 Model Context Protocol (MCP) 暴露的供应商记忆向量检索服务。
"""

from typing import Dict, Any, List

class ChromaVendorMemoryMCPServer:
    def __init__(self, host: str = "localhost", port: int = 8000):
        self.server_name = "chroma-vendor-memory"
        self.version = "1.0.0"

    def list_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "retrieve_vendor_memory",
                "description": "按供应商名称查询该商户的历史别名、习惯单位与版式先验知识",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tenant_id": {"type": "string", "description": "门店租户ID"},
                        "supplier_name": {"type": "string", "description": "单据上的供应商候选名称"}
                    },
                    "required": ["tenant_id", "supplier_name"]
                }
            },
            {
                "name": "save_vendor_memory",
                "description": "将人工确认的单据品名别名与单位习惯沉淀回写向量库",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tenant_id": {"type": "string"},
                        "supplier_name": {"type": "string"},
                        "facts": {"type": "array", "items": {"type": "string"}}
                    },
                    "required": ["tenant_id", "supplier_name", "facts"]
                }
            }
        ]

MCPServer = ChromaVendorMemoryMCPServer
