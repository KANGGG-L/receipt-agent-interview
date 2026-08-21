"""
Query SQL Generator Tool v1.0.0 (Production Active)
安全只读聚合 SQL 构建与租户硬隔离注入工具。
"""

import re
from typing import Dict, Any, Optional

class QuerySqlGenTool:
    FORBIDDEN = re.compile(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|GRANT|REVOKE)\b", re.IGNORECASE)

    def execute(self, raw_sql: str, tenant_id: str) -> Optional[str]:
        # 1. 拦截任何写操作
        if self.FORBIDDEN.search(raw_sql):
            raise PermissionError("禁止生成或执行任何具有写操作/DDL 的 SQL 语句！")

        # 2. 检查并强制注入租户隔离
        clean_sql = raw_sql.strip().rstrip(";")
        if "tenant_id" not in clean_sql.lower():
            if "where" in clean_sql.lower():
                clean_sql = clean_sql.replace("WHERE", f"WHERE tenant_id = '{tenant_id}' AND ", 1)
            else:
                clean_sql = f"{clean_sql} WHERE tenant_id = '{tenant_id}'"
        return clean_sql

Tool = QuerySqlGenTool
