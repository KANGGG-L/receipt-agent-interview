"""
DB Read-Only Query MCP Server (ai_registry/mcp/servers/db_readonly_mcp.py)
基于 Model Context Protocol (MCP) 暴露的进销存数据库安全只读端点。

安全约束（与 query_sql_gen 工具同一套守卫，不另写第二份）：只允许单条 SELECT，SQL 必须显式
带 {tenant_id} 占位符并由服务端替换为调用方租户；连接本身再开 query_only，写操作在数据库层面
也会失败。结果最多返回 MAX_ROWS 行。
"""

from typing import Dict, Any, List

try:
    from ._protocol import MCPServerBase, ToolError
except ImportError:  # 以脚本方式直接运行
    from _protocol import MCPServerBase, ToolError

MAX_ROWS = 200


class DBReadOnlyMCPServer(MCPServerBase):
    def __init__(self):
        self.server_name = "db-readonly-query"
        self.version = "1.0.0"

    def list_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "query_inventory_stats",
                "description": "执行只读聚合查询，获取指定门店在特定周期的食材采购量、库存及均价。SQL 必须是单条 SELECT 且包含 {tenant_id} 占位符",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tenant_id": {"type": "string"},
                        "sql_query": {"type": "string", "description": "只读 SELECT 聚合语句，必须含 {tenant_id}"}
                    },
                    "required": ["tenant_id", "sql_query"]
                }
            }
        ]

    def _handlers(self):
        return {"query_inventory_stats": self.query_inventory_stats}

    def query_inventory_stats(self, tenant_id: str, sql_query: str) -> Dict[str, Any]:
        from ai_registry.tools.query_sql_gen.v1_1_0 import QuerySqlGenTool
        try:
            safe_sql = QuerySqlGenTool().execute(sql_query, tenant_id)
        except PermissionError as e:
            raise ToolError(str(e))
        if not safe_sql.lstrip().lower().startswith(("select", "with")):
            raise ToolError("只允许 SELECT 查询")
        from sqlalchemy import text
        from app import db
        with db._engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA query_only = ON")
            try:
                result = conn.execute(text(safe_sql))
                rows = [dict(r._mapping) for r in result.fetchmany(MAX_ROWS + 1)]
            finally:
                conn.exec_driver_sql("PRAGMA query_only = OFF")
        truncated = len(rows) > MAX_ROWS
        return {"rows": rows[:MAX_ROWS], "truncated": truncated}


MCPServer = DBReadOnlyMCPServer

if __name__ == "__main__":
    MCPServer().serve_stdio()
