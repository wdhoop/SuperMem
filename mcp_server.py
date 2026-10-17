# -*- coding: utf-8 -*-
"""
超级记忆系统 —— MCP 服务接口 mcp_server.py
轻量 MCP（stdio + JSON-RPC）：initialize / tools/list / tools/call。
供外部智能体（如豆包助手）通过 MCP 接入本记忆系统。
"""
import json
import sys

import search_fusion
import time_window
from core import MemorySystem, Node

TOOLS = [
    {"name": "health", "description": "记忆系统健康状态", "inputSchema": {"type": "object"}},
    {"name": "search_nodes", "description": "语义检索记忆（含因果链）",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"},
                     "limit": {"type": "integer"}}, "required": ["query"]}},
    {"name": "add_node", "description": "写入一条记忆节点",
     "inputSchema": {"type": "object", "properties": {"content": {"type": "string"},
                     "node_type": {"type": "string"}, "tags": {"type": "array"}}, "required": ["content"]}},
    {"name": "extract_text", "description": "从文本规则提取记忆（决策/事件/心得等）",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"},
                     "project": {"type": "string"}}, "required": ["text"]}},
    {"name": "rewind", "description": "时间旅行：截至某时刻我已知什么",
     "inputSchema": {"type": "object", "properties": {"ts": {"type": "string"},
                     "query": {"type": "string"}}, "required": ["ts", "query"]}},
    {"name": "timeline", "description": "时间线：时间段内的情景块与节点",
     "inputSchema": {"type": "object", "properties": {"start": {"type": "string"},
                     "end": {"type": "string"}}, "required": ["start", "end"]}},
    {"name": "forgotten", "description": "已遗忘（低权重）节点",
     "inputSchema": {"type": "object", "properties": {"threshold": {"type": "number"}}}},
]


class McpServer:
    def __init__(self, ms):
        self.ms = ms

    def _result(self, obj):
        return {"content": [{"type": "text", "text": json.dumps(obj, ensure_ascii=False)}]}

    def handle(self, msg):
        method = msg.get("method")
        if method == "initialize":
            return {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
                    "serverInfo": {"name": "supermem-mcp", "version": "1.0"}}
        if method == "tools/list":
            return {"tools": TOOLS}
        if method == "tools/call":
            return self._call(msg["params"])
        return {"error": {"code": -32601, "message": f"method not found: {method}"}}

    def _call(self, params):
        name = params.get("name")
        args = params.get("arguments", {})
        ms = self.ms
        try:
            if name == "health":
                s = ms.graph.stats()
                res = {"status": "ok", "nodes": s["nodes"], "edges": s["edges"],
                       "episodes": s["episodes"], "vector_indexed": ms.vector.count()}
            elif name == "search_nodes":
                res = search_fusion.search(ms, args.get("query", ""),
                                           limit=int(args.get("limit", 10)))
            elif name == "add_node":
                node = Node(content=args.get("content", ""),
                            node_type=args.get("node_type", "event"),
                            tags=args.get("tags", []), source="mcp")
                nid, created = ms.add_node_with_vector(node)
                res = {"id": nid, "created": created}
            elif name == "extract_text":
                import extract
                n = extract.extract_from_text(args.get("text", ""),
                                              source="mcp", project=args.get("project", "default"), ms=ms)
                res = {"created": n}
            elif name == "rewind":
                res = time_window.rewind(ms, args.get("ts", ""), args.get("query", ""))
            elif name == "timeline":
                res = time_window.timeline(ms, args.get("start", ""), args.get("end", ""))
            elif name == "forgotten":
                from search_fusion import forgotten_nodes
                res = {"forgotten": forgotten_nodes(ms, float(args.get("threshold", 0.2)))}
            else:
                return {"content": [{"type": "text", "text": "unknown tool"}], "isError": True}
            return self._result(res)
        except Exception as e:
            return {"content": [{"type": "text", "text": json.dumps({"error": str(e)}, ensure_ascii=False)}],
                    "isError": True}

    def serve(self):
        """stdio 逐行 JSON-RPC。"""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            resp = {"jsonrpc": "2.0", "id": msg.get("id"), "result": self.handle(msg)}
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    ms = MemorySystem()
    McpServer(ms).serve()
