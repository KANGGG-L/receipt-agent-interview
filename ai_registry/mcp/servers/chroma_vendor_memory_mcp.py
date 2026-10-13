"""
Chroma Vendor Memory MCP Server (ai_registry/mcp/servers/chroma_vendor_memory_mcp.py)
基于 Model Context Protocol (MCP) 暴露的供应商记忆向量检索服务。

读：直接走 rag.retrieve_context（租户硬门、相关性门都在里面）。
写：**不直接生效**——事实一律先进「待确认队列」由人工闸批准（与应用内反馈回写同一条治理路径），
并在入队前做注入特征检测。
"""

from typing import Dict, Any, List

try:
    from ._protocol import MCPServerBase, ToolError
except ImportError:
    from _protocol import MCPServerBase, ToolError


class ChromaVendorMemoryMCPServer(MCPServerBase):
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
                "description": "把单据品名别名与单位习惯提交到「待确认队列」，经人工确认后才会生效（不会直接写入记忆库）",
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

    def _handlers(self):
        return {"retrieve_vendor_memory": self.retrieve_vendor_memory,
                "save_vendor_memory": self.save_vendor_memory}

    def retrieve_vendor_memory(self, tenant_id: str, supplier_name: str) -> Dict[str, Any]:
        from app.services.rag import retrieve_context
        context = retrieve_context(supplier_name, tenant_id=tenant_id) or ""
        return {"supplier_name": supplier_name, "context": context}

    def save_vendor_memory(self, tenant_id: str, supplier_name: str, facts: List[str]) -> Dict[str, Any]:
        if not facts:
            raise ToolError("facts 不能为空")
        from ai_registry.tools.prompt_injection_guard.v1_1_0_normalize import PromptInjectionGuardTool
        from app import db
        guard = PromptInjectionGuardTool()
        queued, rejected = [], []
        for fact in facts:
            text = str(fact).strip()
            if not text:
                continue
            hit, snippet = guard.contains_injection_attack(text)
            if hit:
                rejected.append({"fact": text[:80], "reason": f"疑似提示词注入: {snippet}"})
                continue
            pending_id, is_new = db.enqueue_pending_memory(
                supplier_name, text, tenant_id=tenant_id, source_kind="mcp_client")
            queued.append({"pending_id": pending_id, "is_new": is_new})
        return {"queued": queued, "rejected": rejected, "note": "已进入待确认队列，人工确认后才生效"}


MCPServer = ChromaVendorMemoryMCPServer

if __name__ == "__main__":
    MCPServer().serve_stdio()
