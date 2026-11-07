#!/usr/bin/env python3
"""Minimal JSON-RPC MCP stdio server used by llm_mcp tests.

Speaks newline-delimited JSON-RPC 2.0 on stdin/stdout, covering initialize,
tools/list, and tools/call. No third-party MCP SDK required.
"""
import json
import sys

TOOLS = [
    {
        "name": "llm_mcp_test_echo",
        "description": "Echo a message back as JSON",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Text to echo",
                }
            },
            "required": ["message"],
        },
        "annotations": {
            "title": "Test Echo",
            "readOnlyHint": True,
            "idempotentHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
        },
    },
    {
        "name": "llm_mcp_test_fail",
        "description": "Always fails",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def write(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def handle(request):
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2025-03-26",
                "serverInfo": {"name": "llm-mcp-test-server", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            },
        }

    if method in ("notifications/initialized", "initialized"):
        return None

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name == "llm_mcp_test_fail":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": "intentional failure"}],
                },
            }
        if name == "llm_mcp_test_echo":
            payload = json.dumps({"echoed": arguments.get("message")})
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "isError": False,
                    "content": [{"type": "text", "text": payload}],
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Unknown tool {name}"},
        }

    if method in ("ping", "echo"):
        return {"jsonrpc": "2.0", "id": request_id, "result": {"ok": True}}

    if request_id is not None:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Unknown method {method}"},
        }
    return None


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle(request)
        if response is not None:
            write(response)


if __name__ == "__main__":
    main()
