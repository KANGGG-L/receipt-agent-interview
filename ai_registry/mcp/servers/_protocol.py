"""MCP 服务共用的最小协议层：tools/list、tools/call、按行读写 JSON 的 stdio 入口。

只实现这三个服务需要的部分：参数按 inputSchema 校验（缺必填项、类型不对直接拒绝），
工具函数抛出的任何异常都转成 isError 结果，不会让服务进程退出。
"""

import json
import sys
from typing import Any, Dict, List

_TYPE_MAP = {"string": str, "array": list, "object": dict, "integer": int, "number": (int, float), "boolean": bool}


class ToolError(Exception):
    """工具执行失败（参数合法，但业务上不允许或做不了）。"""


def validate_arguments(schema: Dict[str, Any], arguments: Any) -> None:
    if not isinstance(arguments, dict):
        raise ToolError("arguments 必须是对象")
    for name in schema.get("required", []):
        if arguments.get(name) in (None, ""):
            raise ToolError(f"缺少必填参数: {name}")
    for name, spec in (schema.get("properties") or {}).items():
        if name in arguments and arguments[name] is not None:
            expected = _TYPE_MAP.get(spec.get("type"))
            value = arguments[name]
            if expected and (not isinstance(value, expected) or (spec.get("type") != "boolean" and isinstance(value, bool))):
                raise ToolError(f"参数 {name} 类型应为 {spec.get('type')}")
            if spec.get("type") == "array":
                item_type = _TYPE_MAP.get((spec.get("items") or {}).get("type"))
                if item_type and any(not isinstance(v, item_type) for v in value):
                    raise ToolError(f"参数 {name} 的元素类型应为 {(spec.get('items') or {}).get('type')}")


class MCPServerBase:
    server_name = ""
    version = "1.0.0"

    def list_tools(self) -> List[Dict[str, Any]]:  # pragma: no cover - 子类实现
        raise NotImplementedError

    def _handlers(self) -> Dict[str, Any]:  # pragma: no cover - 子类实现
        raise NotImplementedError

    def call_tool(self, name: str, arguments: Any) -> Dict[str, Any]:
        tools = {t["name"]: t for t in self.list_tools()}
        if name not in tools:
            return self._error(f"未知工具: {name}")
        try:
            validate_arguments(tools[name]["parameters"], arguments)
            result = self._handlers()[name](**arguments)
        except ToolError as e:
            return self._error(str(e))
        except Exception as e:  # noqa: BLE001 服务不能因为一次调用失败而退出
            return self._error(f"工具执行失败: {type(e).__name__}: {e}")
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}],
                "isError": False}

    @staticmethod
    def _error(message: str) -> Dict[str, Any]:
        return {"content": [{"type": "text", "text": message}], "isError": True}

    def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        rid = request.get("id") if isinstance(request, dict) else None
        method = request.get("method") if isinstance(request, dict) else None
        params = (request.get("params") if isinstance(request, dict) else None) or {}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": rid, "result": {"tools": self.list_tools()}}
        if method == "tools/call":
            return {"jsonrpc": "2.0", "id": rid,
                    "result": self.call_tool(params.get("name"), params.get("arguments") or {})}
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"不支持的方法: {method}"}}

    def serve_stdio(self, stdin=None, stdout=None) -> None:
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                response = self.handle_request(json.loads(line))
            except json.JSONDecodeError:
                response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "不是合法 JSON"}}
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()
