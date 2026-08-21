"""
DB Read-Only Query MCP Server (ai_registry/mcp/servers/db_readonly_mcp.py)
基于 Model Context Protocol (MCP) 暴露的进销存数据库安全只读端点。
"""

from typing import Dict, Any, List

class DBReadOnlyMCPServer:
    def __init__(self):
        self.server_name = "db-readonly-query"
        self.version = "1.0.0"

    def list_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "query_inventory_stats",
                "description": "执行只读聚合查询，获取指定门店在特定周期的食材采购量、库存及均价",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tenant_id": {"type": "string"},
                        "sql_query": {"type": "string", "description": "只读 SELECT 聚合语句"}
                    },
                    "required": ["tenant_id", "sql_query"]
                }
            }
        ]

MCPServer = DBReadOnlyMCPServer
