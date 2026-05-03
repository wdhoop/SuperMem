"""
超级记忆系统 MVP — REST API 服务
"""

import json
import os
import sys
import threading
import re
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime

# 将项目根目录加入 path
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import GraphStore, VectorStore, generate_id

PORT = int(os.environ.get("PORT", "8765"))

store = GraphStore()
vec = VectorStore()
_compact_lock = threading.Lock()


# ─── 工具函数 ───────────────────────────────────────────────────────────

def json_response(handler, data, status=200):
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))


def read_body(handler):
    length = int(handler.headers.get("Content-Length", "0"))
    return json.loads(handler.rfile.read(length).decode("utf-8")) if length else {}


def static_path(path):
    return os.path.join(ROOT, path.lstrip("/"))


def guess_mime(path):
    ext = os.path.splitext(path)[1].lower()
    return {
        ".html": "text/html; charset=utf-8",
        ".js": "application/javascript",
        ".css": "text/css",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".svg": "image/svg+xml",
    }.get(ext, "application/octet-stream")


def serve_static(handler, filepath):
    if not os.path.isfile(filepath) or os.path.getsize(filepath) == 0:
        json_response(handler, {"error": "Not Found"}, 404)
        return
    mime = guess_mime(filepath)
    with open(filepath, "rb") as f:
        data = f.read()
    handler.send_response(200)
    handler.send_header("Content-Type", mime)
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(data)


# ─── API 处理 ────────────────────────────────────────────────────────────

def handle_ingest(body):
    """POST /api/v1/memory/ingest — 写入新记忆"""
    content = body.get("content", "").strip()
    if not content:
        return {"code": 400, "message": "content 不能为空"}
    node_type = body.get("type", "Concept")
    name = body.get("name", content[:60])
    source = body.get("source", "")
    node_id = generate_id()
    store.save_node(node_id, node_type=node_type, name=name, content=content, source=source)
    vec.upsert(node_id, content)
    return {"code": 0, "message": "ok", "data": {"id": node_id, "type": node_type, "name": name}}


def handle_search(body):
    """POST /api/v1/memory/search — 语义检索"""
    query = body.get("query", "").strip()
    top_k = min(int(body.get("top_k", 10)), 50)
    if not query:
        return {"code": 400, "message": "query 不能为空"}

    # 向量检索
    vec_results = vec.search(query, top_k=top_k * 2)
    results = []
    for nid, score in vec_results:
        node = store.get_node(nid)
        if node and score >= 0.3:
            node["similarity"] = score
            results.append(node)

    # 按相似度排序，取 top_k
    results.sort(key=lambda x: x["similarity"], reverse=True)
    results = results[:top_k]

    return {"code": 0, "message": "ok", "data": results}


def handle_node_detail(node_id):
    """GET /api/v1/memory/node/{id} — 节点详情"""
    node = store.get_node(node_id)
    if not node:
        return {"code": 404, "message": "节点不存在"}
    edges = store.get_node_edges(node_id)
    store.record_access(node_id)
    return {"code": 0, "message": "ok", "data": {"node": node, "edges": edges}}


def handle_graph(params):
    """GET /api/v1/memory/graph — 线性因果链

    设计原则：路径不网状。
    以指定节点为中心，沿 CAUSED_BY 边做两方向追溯：
      向前追溯（原因链）: 节点 → 原因 → 更深层原因
        边定义：CAUSED_BY(from=A, to=B) 表示「A 的原因是 B」
        查询：找 from_id→to_id 方向，to_id == 当前节点
      向后推导（后果链）: 节点 → 后果 → 更深层后果
        查询：找 from_id→to_id 方向，from_id == 当前节点
    两个方向的节点合并为一条线性路径返回。
    """
    node_id = params.get("node_id", "")
    depth = min(int(params.get("depth", 3)), 10)

    if not node_id:
        # 无指定节点返回最近 10 个节点的因果链
        nodes = store.get_all_nodes(limit=10)
        edges = store.get_all_edges(limit=20)
        return {"code": 0, "message": "ok", "data": {"nodes": nodes, "edges": edges}}

    node = store.get_node(node_id)
    if not node:
        return {"code": 404, "message": "节点不存在"}
    store.record_access(node_id)

    # 线性因果链：沿 CAUSED_BY 向前追溯（原因链），向后推理（后果链）
    visited = {node_id}
    chain_nodes = [node]

    # 向前追溯（原因链）：A → B（A caused_by B → A 的原因是谁）
    def trace_backward(nid, remaining):
        if remaining <= 0:
            return
        edges = store.get_node_edges(nid)
        for e in edges:
            if e["type"] == "CAUSED_BY" and e["to_id"] == nid and e["from_id"] not in visited:
                visited.add(e["from_id"])
                n = store.get_node(e["from_id"])
                if n:
                    chain_nodes.append(n)
                    trace_backward(e["from_id"], remaining - 1)

    # 向后推理（后果链）：A → B（A leads_to B → A 的后果是谁）
    def trace_forward(nid, remaining):
        if remaining <= 0:
            return
        edges = store.get_node_edges(nid)
        for e in edges:
            if e["type"] == "CAUSED_BY" and e["from_id"] == nid and e["to_id"] not in visited:
                visited.add(e["to_id"])
                n = store.get_node(e["to_id"])
                if n:
                    chain_nodes.append(n)
                    trace_forward(e["to_id"], remaining - 1)

    trace_backward(node_id, depth)
    trace_forward(node_id, depth)

    # 收集链内边
    chain_ids = {n["id"] for n in chain_nodes}
    chain_edges = []
    added_edge_ids = set()
    for n in chain_nodes:
        for e in store.get_node_edges(n["id"]):
            if e["from_id"] in chain_ids and e["to_id"] in chain_ids and e["id"] not in added_edge_ids:
                chain_edges.append(e)
                added_edge_ids.add(e["id"])

    return {"code": 0, "message": "ok", "data": {"nodes": chain_nodes, "edges": chain_edges}}


def handle_stats():
    """GET /api/v1/memory/stats — 系统统计"""
    st = store.get_stats()
    vec_count = vec.count()
    return {"code": 0, "message": "ok", "data": {
        "nodes": st["nodes"],
        "edges": st["edges"],
        "vector_index": vec_count
    }}


def handle_compact(body):
    """POST /api/v1/memory/compact — 手动触发压缩

    三个阶段：
      Phase 1 — 相似节点合并：对每个节点向量搜索 top-5 最相似节点，
                若相似度 >= threshold（默认 0.92）则合并：
                  - 保留 weight 较高的节点
                  - 被合并节点（weight 较低）的所有边迁移到保留节点
                  - 边权重取 MAX
                  - 删除被合并节点和对应向量
      Phase 2 — 弱边剪枝：删除 weight < min_edge_weight（默认 0.15）的边
                节点的遗忘保底为 0.05，剪枝阈值 0.15，提醒阈值 0.25。
                一年不访问的节点 weight 约降至 0.15，可被剪枝。
      Phase 3 — 向量索引重建：对所有剩余节点重新 upsert 向量
    """
    if not _compact_lock.acquire(blocking=False):
        return {"code": 1, "message": "正在压缩中，请稍后重试"}

    try:
        threshold = float(body.get("similarity_threshold", 0.92))
        min_edge_weight = float(body.get("min_edge_weight", 0.15))
        stats = {"merged": 0, "pruned_edges": 0}
        nodes = store.get_all_nodes(limit=5000)

        # Phase 0: 检查即将遗忘的节点（权重 < 0.25，偏离创建时间 > 7 天）
        warnings = []
        for n in nodes:
            if n["weight"] is not None and n["weight"] < 0.25:
                try:
                    created = datetime.strptime(n["created_at"], "%Y-%m-%d %H:%M:%S")
                    days = (datetime.utcnow() - created).days
                except:
                    days = 0
                if days > 7:
                    warnings.append({"id": n["id"], "name": n["name"], "weight": n["weight"], "days_unvisited": days})
        stats["warnings"] = warnings

        # Phase 1: 相似节点合并
        texts = [(n["id"], n["name"] + " " + n["content"]) for n in nodes]
        # 分批做向量搜索找相似
        merged = set()
        for i, (nid, txt) in enumerate(texts):
            if nid in merged:
                continue
            similar = vec.search(txt, top_k=5)
            for sid, score in similar:
                if sid != nid and sid not in merged and score >= threshold:
                    src = store.get_node(sid)
                    dst = store.get_node(nid)
                    if src and dst:
                        # 保留 weight 高的，迁移所有边
                        keeper = nid if dst["weight"] >= src["weight"] else sid
                        removed = sid if keeper == nid else nid
                        merged.add(removed)
                        edges = store.get_node_edges(removed)
                        for e in edges:
                            if e["from_id"] == removed:
                                store.save_edge(keeper, e["to_id"], e["type"], e["weight"], e["context"])
                            else:
                                store.save_edge(e["from_id"], keeper, e["type"], e["weight"], e["context"])
                        store.delete_node(removed)
                        vec.delete(removed)
                        stats["merged"] += 1
                    break  # 每节点只合并最相似的那一个

        # Phase 2: 弱边剪枝
        all_edges = store.get_all_edges(limit=10000)
        for e in all_edges:
            if e["weight"] < min_edge_weight:
                store.delete_edge(e["id"])
                stats["pruned_edges"] += 1

        # Phase 3: 重建向量索引
        remaining = store.get_all_nodes(limit=5000)
        vec.upsert_batch([(n["id"], n["name"] + " " + n["content"]) for n in remaining])

        return {"code": 0, "message": "ok", "data": stats}
    finally:
        _compact_lock.release()


# ─── 路由 ────────────────────────────────────────────────────────────────
# GET:
#   /                    → web/index.html（主界面）
#   /graph.html          → web/graph.html（因果链可视化）
#   /api/v1/memory/stats → 系统统计
#   /api/v1/memory/graph → 线性因果链（参数: node_id, depth）
#   /api/v1/memory/node/{id} → 节点详情
# POST:
#   /api/v1/memory/ingest  → 写入记忆
#   /api/v1/memory/search  → 语义检索
#   /api/v1/memory/compact → 手动压缩

class Handler(BaseHTTPRequestHandler):
    """HTTP 请求处理器。静态文件自动路由到 web/ 目录，API 路径分发到处理函数。"""

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        params = parse_qs(parsed.query)
        sp = {k: v[0] for k, v in params.items()}

        try:
            # 静态文件
            if path == "" or path == "/":
                return serve_static(self, static_path("web/index.html"))
            if path == "/graph.html":
                return serve_static(self, static_path("web/graph.html"))

            web_path = static_path("web" + path)
            if os.path.isfile(web_path):
                return serve_static(self, web_path)

            # API
            if path == "/api/v1/memory/stats":
                return json_response(self, handle_stats())

            if path == "/api/v1/memory/graph":
                return json_response(self, handle_graph(sp))

            # /api/v1/memory/node/{id}
            m = re.match(r"^/api/v1/memory/node/(.+)$", path)
            if m:
                return json_response(self, handle_node_detail(m.group(1)))

            json_response(self, {"code": 404, "message": "接口不存在"}, 404)
        except Exception as e:
            json_response(self, {"code": 500, "message": str(e)}, 500)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        try:
            body = read_body(self)

            if path == "/api/v1/memory/ingest":
                return json_response(self, handle_ingest(body))
            if path == "/api/v1/memory/search":
                return json_response(self, handle_search(body))
            if path == "/api/v1/memory/compact":
                return json_response(self, handle_compact(body))

            json_response(self, {"code": 404, "message": "接口不存在"}, 404)
        except json.JSONDecodeError:
            json_response(self, {"code": 400, "message": "请求体不是有效的 JSON"}, 400)
        except Exception as e:
            json_response(self, {"code": 500, "message": str(e)}, 500)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        pass  # 静默日志

def main():
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"\n  [SM] 超级记忆系统 MVP 版 v1.0")
    print(f"  ─────────────────────────────")
    print(f"  运行在: http://localhost:{PORT}")
    print(f"  API:    http://localhost:{PORT}/api/v1/memory/stats")
    print(f"  UI:     http://localhost:{PORT}")
    print(f"  图谱:   http://localhost:{PORT}/graph.html")
    print(f"\n  按 Ctrl+C 停止\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  服务已停止")
        server.server_close()


if __name__ == "__main__":
    main()
