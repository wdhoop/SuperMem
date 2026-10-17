# -*- coding: utf-8 -*-
"""
超级记忆系统 —— REST API api.py
stdlib http.server 零依赖实现。
[FIX] 安全：默认仅绑定 127.0.0.1 + 所有端点要求 X-API-Token 认证（原架构无认证）。
"""
import json
import os
import sys
import threading
import urllib.parse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
from core import Edge, MemorySystem, Node

# 运维脚本可被 API 触发
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))

import extract  # noqa: E402
import metrics as metrics_mod  # noqa: E402
import search_fusion  # noqa: E402
import time_window  # noqa: E402


class _Handler(BaseHTTPRequestHandler):
    server_version = "SuperMem/1.0"
    ms = None  # 由 start_server 注入

    def log_message(self, fmt, *args):
        pass  # 关闭默认访问日志（避免刷屏）；错误由响应体返回

    # ---------- 工具 ----------
    def _auth(self):
        return self.headers.get("X-API-Token", "") == config.API_TOKEN

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            n = 0
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def _params(self):
        u = urllib.parse.urlparse(self.path)
        return u.path, urllib.parse.parse_qs(u.query)

    # ---------- GET ----------
    def do_GET(self):
        if not self._auth():
            return self._send(401, {"error": "unauthorized"})
        path, qs = self._params()
        try:
            ms = self.ms
            if path == "/health":
                s = ms.graph.stats()
                vc = ms.vector.count()
                return self._send(200, {"status": "ok", "nodes": s["nodes"], "edges": s["edges"],
                                        "episodes": s["episodes"], "by_status": s["by_status"],
                                        "vector_indexed": vc, "vector_gap": max(0, s["nodes"] - vc)})
            if path.startswith("/nodes/"):
                nid = urllib.parse.unquote(path[len("/nodes/"):])
                node = ms.graph.get_node(nid)
                if not node:
                    return self._send(404, {"error": "not found"})
                return self._send(200, {"node": node.to_dict(),
                                        "in_edges": [e.to_dict() for e in ms.graph.in_edges(nid)],
                                        "out_edges": [e.to_dict() for e in ms.graph.out_edges(nid)],
                                        "causes": ms.causal.get_causes(nid),
                                        "effects": ms.causal.get_effects(nid)})
            if path == "/nodes":
                q = (qs.get("q") or [""])[0]
                limit = int((qs.get("limit") or [config.SEARCH_LIMIT_DEFAULT])[0])
                project = (qs.get("project") or [None])[0]
                if q:
                    res = search_fusion.nodes_query(ms, q, limit=limit, project=project)
                else:
                    res = [n.to_dict() for n in ms.graph.list_nodes(project=project, limit=limit)]
                return self._send(200, {"results": res})
            if path.startswith("/search/strong/"):
                q = urllib.parse.unquote(path[len("/search/strong/"):])
                limit = int((qs.get("limit") or [config.SEARCH_LIMIT_DEFAULT])[0])
                project = (qs.get("project") or [None])[0]
                force_raw = (qs.get("force") or ["0"])[0] in ("1", "true", "yes")
                return self._send(200, search_fusion.search_strong(ms, q, limit=limit,
                                                                   project=project, force_raw=force_raw))
            if path.startswith("/search/"):
                q = urllib.parse.unquote(path[len("/search/"):])
                limit = int((qs.get("limit") or [config.SEARCH_LIMIT_DEFAULT])[0])
                project = (qs.get("project") or [None])[0]
                return self._send(200, search_fusion.search(ms, q, limit=limit, project=project))
            if path.startswith("/rewind/"):
                parts = path[len("/rewind/"):].split("/", 1)
                ts = urllib.parse.unquote(parts[0])
                q = urllib.parse.unquote(parts[1]) if len(parts) > 1 else ""
                return self._send(200, time_window.rewind(ms, ts, q))
            if path.startswith("/timeline/"):
                parts = path[len("/timeline/"):].split("/")
                start = urllib.parse.unquote(parts[0])
                end = urllib.parse.unquote(parts[1]) if len(parts) > 1 else start
                return self._send(200, time_window.timeline(ms, start, end))
            if path == "/episodes":
                return self._send(200, {"episodes": time_window.get_episodes(ms)})
            if path.startswith("/episodes/"):
                eid = path[len("/episodes/"):]
                ep = next((e for e in time_window.get_episodes(ms, limit=500) if e["id"] == eid), None)
                return self._send(200, ep if ep else {"error": "not found"})
            if path == "/forgotten":
                threshold = float((qs.get("threshold") or [config.ARCHIVE_THRESHOLD])[0])
                hl = (qs.get("half_life") or [None])[0]
                hl = float(hl) if hl else None
                return self._send(200, {"forgotten": search_fusion.forgotten_nodes(ms, threshold, hl)})
            if path == "/metrics":
                return self._send(200, metrics_mod.full_report(ms))
            if path == "/sessions":
                from cross_session import list_sessions
                return self._send(200, {"sessions": list_sessions(ms)})
            if path.startswith("/verbatim/"):
                vid = urllib.parse.unquote(path[len("/verbatim/"):])
                blk = ms.get_verbatim(vid)
                if not blk:
                    return self._send(404, {"error": "not found"})
                return self._send(200, {"verbatim": blk})
            if path == "/verbatim":
                nid = (qs.get("node_id") or [None])[0]
                return self._send(200, {"verbatim": ms.graph.list_verbatim_by_node(nid) if nid else []})
            return self._send(404, {"error": "no such endpoint"})
        except Exception as e:
            return self._send(500, {"error": str(e)})

    # ---------- POST ----------
    def do_POST(self):
        if not self._auth():
            return self._send(401, {"error": "unauthorized"})
        path, _ = self._params()
        body = self._read_body()
        try:
            ms = self.ms
            if path == "/nodes":
                node = Node(content=body.get("content", ""), node_type=body.get("node_type", "event"),
                            project=body.get("project", "default"), tags=body.get("tags", []),
                            source=body.get("source", "api"), confidence=body.get("confidence", 1.0),
                            timestamp=body.get("timestamp"))
                if not node.content:
                    return self._send(400, {"error": "content required"})
                nid, created = ms.add_node_with_vector(node)
                if created:
                    try:
                        from linker import auto_link
                        auto_link(ms, nid, node.content, project=node.project)
                    except Exception:
                        pass
                # [2026-10-17] 日常写入自动落原文层：content 哈希幂等 vid，node_id 双向指针
                try:
                    raw_vid = "v_" + hashlib.sha1(node.content.encode("utf-8")).hexdigest()[:12]
                    ms.add_verbatim(node.content, source=node.source or "api", node_id=nid,
                                    session=node.project or "default", vid=raw_vid)
                except Exception:
                    pass
                return self._send(200, {"id": nid, "created": created, "raw_vid": raw_vid})
            if path == "/extract":
                created_n = 0
                raw_vids = []
                if body.get("text"):
                    created_n += extract.extract_from_text(body["text"], source=body.get("source", "api"),
                                                           project=body.get("project", "default"), ms=ms)
                    # [2026-10-17] 原始话术自动落原文层（逐字不压缩，哈希幂等）
                    try:
                        txt = body["text"]
                        if txt.strip():
                            raw_vid = "v_" + hashlib.sha1(txt.encode("utf-8")).hexdigest()[:12]
                            ms.add_verbatim(txt, source=body.get("source", "api"),
                                            session=body.get("project", "default"), vid=raw_vid)
                            raw_vids.append(raw_vid)
                    except Exception:
                        pass
                for item in body.get("nodes", []):
                    node = Node(content=item.get("content", ""), node_type=item.get("node_type", "event"),
                                project=item.get("project", "default"), tags=item.get("tags", []),
                                source=item.get("source", "api"), confidence=item.get("confidence", 0.8))
                    if node.content:
                        nid, created = ms.add_node_with_vector(node)
                        if created:
                            try:
                                from linker import auto_link
                                auto_link(ms, nid, node.content, project=node.project)
                            except Exception:
                                pass
                        created_n += 1 if created else 0
                for e in body.get("edges", []):
                    ms.graph.add_edge(Edge(from_id=e.get("from", ""), to_id=e.get("to", ""),
                                           edge_type=e.get("type", "related"),
                                           source=e.get("source", "api"),
                                           description=e.get("desc", "")))
                return self._send(200, {"created": created_n, "raw_vids": raw_vids})
            if path == "/verbatim":
                # 原文层写入（2026-10-17）：text 必填，逐字不压缩；
                # node_id 关联图节点（双向指针），ts/source/session 为出处元数据
                text = body.get("text", "")
                if not text:
                    return self._send(400, {"error": "text required"})
                ids = ms.add_verbatim(
                    text=text, ts=body.get("ts"), source=body.get("source", "api"),
                    node_id=body.get("node_id"), session=body.get("session", ""),
                    vid=body.get("id"))
                return self._send(200, {"ids": ids, "count": len(ids)})
            if path == "/sync/apply":
                # 双向同步专用：按 id 幂等 upsert（节点/边），保留本地访问计数
                nodes = body.get("nodes", [])
                edges = body.get("edges", [])
                applied_n = updated_n = edge_n = 0
                for item in nodes:
                    nid = (item.get("id") or "").strip()
                    if not nid or not item.get("content"):
                        continue
                    node = Node(id=nid, content=item.get("content", ""),
                                node_type=item.get("node_type", "event"),
                                status=item.get("status", "active"),
                                timestamp=item.get("timestamp"),
                                weight=item.get("weight", config.W0),
                                project=item.get("project", "default"),
                                tags=item.get("tags", []),
                                source=item.get("source", "sync"),
                                confidence=item.get("confidence", 1.0),
                                created_at=item.get("created_at") or item.get("timestamp"))
                    existing = ms.graph.get_node(nid)
                    if existing:
                        node.last_accessed = existing.last_accessed
                        node.access_count = existing.access_count
                        ms.graph.update_node(node, fields={"content", "node_type", "status", "timestamp",
                                                           "weight", "project", "tags", "source",
                                                           "confidence", "version_of", "replaced_by",
                                                           "marked_until"})
                        try:
                            ms.vector.upsert(nid, node.content, metadata={
                                "node_type": node.node_type, "project": node.project,
                                "timestamp": node.timestamp, "status": node.status})
                        except Exception:
                            pass
                        updated_n += 1
                    else:
                        _, created = ms.add_node_with_vector(node, dedup=False)
                        applied_n += 1
                for e in edges:
                    from_id = (e.get("from") or e.get("from_id") or "").strip()
                    to_id = (e.get("to") or e.get("to_id") or "").strip()
                    if not from_id or not to_id:
                        continue
                    edge = Edge(id=e.get("id"), from_id=from_id, to_id=to_id,
                                edge_type=e.get("type") or e.get("edge_type", "related"),
                                confidence=e.get("confidence", 1.0),
                                source=e.get("source", "sync"),
                                description=e.get("desc") or e.get("description", ""),
                                strength=e.get("strength", 1.0))
                    ms.graph.add_edge(edge, dedup=True)
                    edge_n += 1
                return self._send(200, {"applied_nodes": applied_n, "updated_nodes": updated_n,
                                        "edges": edge_n})
            if path == "/mark":
                nid = body.get("id")
                action = body.get("action")
                from node_lifecycle import (confirm, mark_disputed, mark_hypothesis,
                                            mark_obsolete, mark_outdated, mark_permanent)
                fn = {"confirm": confirm, "hypothesis": mark_hypothesis, "permanent": mark_permanent,
                      "outdated": lambda m, n: mark_outdated(m, n, body.get("replaced_by")),
                      "obsolete": mark_obsolete, "disputed": mark_disputed}.get(action)
                if not fn or not nid:
                    return self._send(400, {"error": "action(id) required"})
                ok = fn(ms, nid)
                node = ms.graph.get_node(nid)
                return self._send(200, {"ok": ok, "status": node.status if node else None})
            if path == "/decay":
                archived = ms.graph.apply_decay()
                if archived:
                    ms.graph.archive_nodes(archived)
                    for nid in archived:
                        ms.vector.delete(nid)
                return self._send(200, {"decayed": True, "archived_count": len(archived)})
            if path == "/episodes/build":
                n = time_window.build_episodes(ms)
                return self._send(200, {"episodes_created": n})
            if path == "/selfcheck":
                from selfcheck import run_selfcheck
                report = run_selfcheck(ms, dry_run=body.get("dry_run", False))
                return self._send(200, report)
            if path == "/rebuild":
                from rebuild_vector_index import rebuild
                threading.Thread(target=rebuild, args=(ms, bool(body.get("dry_run", False))),
                                 daemon=True).start()
                return self._send(202, {"rebuild": "started"})
            if path == "/backup":
                from scheduler import backup_now
                p = backup_now(ms)
                return self._send(200, {"backup": p or "failed"})
            return self._send(404, {"error": "no such endpoint"})
        except Exception as e:
            return self._send(500, {"error": str(e)})


def start_server(ms, host=None, port=None):
    host = host or config.HOST
    port = port or config.PORT
    _Handler.ms = ms
    httpd = ThreadingHTTPServer((host, port), _Handler)
    return httpd
