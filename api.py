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
import event_normalizer  # noqa: E402  [2026-10-20 时间维] 边类型归一
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

    @staticmethod
    def _verify_saved(ms, nid, node, attempt=1):
        """[2026-10-29 借鉴 digital-life-core v0.9.19] 写入回执验证：
        写完按原文读回（SQLite 主键直读，不走缓存），saved 是唯一判定依据；
        读不回（missing）就再写一次，二次还不回才如实报未存住（attempt>=2 时
        返回 False）。dedup 命中（created=False）时节点本来已存在，读回即真。"""
        got = ms.graph.get_node(nid)
        if got is not None and got.content == node.content and got.project == node.project:
            return True, nid
        if attempt >= 2:
            return False, nid
        nid2, _ = ms.add_node_with_vector(node)
        return _Handler._verify_saved(ms, nid2, node, attempt + 1)

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        """[FIX 2026-11-13 P1-10] 严格解析请求体：坏 JSON / 非对象一律抛 ValueError，
        由 do_POST 转 400。原实现吞掉异常返回 {}，于是 `[]` 这种合法 JSON 到了
        `node_data.get(...)` 才崩成 500，报错信息还完全对不上。"""
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            n = 0
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            obj = json.loads(raw.decode("utf-8"))
        except Exception:
            raise ValueError("invalid JSON body")
        if not isinstance(obj, dict):
            raise ValueError("body must be a JSON object")
        return obj

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
                # [FIX 2026-11-13 P1-6] gap 口径改为"可索引节点 - 已索引"（可索引集合排除
                # obsolete/merged，与 rebuild_vector_index 一致）；原来用 nodes - vc，
                # 这两个被排除的状态永远算作缺口，/rebuild 之后反而从 3 涨到 7 归不了零。
                indexable = ms.graph.indexable_node_count()
                return self._send(200, {"status": "ok", "nodes": s["nodes"], "edges": s["edges"],
                                        "episodes": s["episodes"], "by_status": s["by_status"],
                                        "indexable_nodes": indexable,
                                        "vector_indexed": vc,
                                        "vector_gap": max(0, indexable - vc)})
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
                recency = int((qs.get("recency") or [config.RECENCY_DEFAULT])[0])  # [时间维]
                scene = (qs.get("scene") or [None])[0]                             # [空间维]
                goal = int((qs.get("goal") or [0])[0])                             # [价值维]
                state = (qs.get("state") or [None])[0]                             # [状态维]
                tilt = (qs.get("tilt") or [str(int(config.TILT_ENABLED))])[0] in ("1", "true", "yes")  # [注意力倾斜]
                if q:
                    res = search_fusion.nodes_query(ms, q, limit=limit, project=project,
                                                    recency=recency, scene=scene, goal=goal,
                                                    state=state, tilt=tilt)
                else:
                    res = [n.to_dict() for n in ms.graph.list_nodes(project=project, limit=limit)]
                return self._send(200, {"results": res})
            if path.startswith("/search/strong/"):
                q = urllib.parse.unquote(path[len("/search/strong/"):])
                limit = int((qs.get("limit") or [config.SEARCH_LIMIT_DEFAULT])[0])
                project = (qs.get("project") or [None])[0]
                force_raw = (qs.get("force") or ["0"])[0] in ("1", "true", "yes")
                recency = int((qs.get("recency") or [config.RECENCY_DEFAULT])[0])  # [时间维]
                scene = (qs.get("scene") or [None])[0]                             # [空间维]
                goal = int((qs.get("goal") or [0])[0])                             # [价值维]
                state = (qs.get("state") or [None])[0]                             # [状态维]
                tilt = (qs.get("tilt") or [str(int(config.TILT_ENABLED))])[0] in ("1", "true", "yes")  # [注意力倾斜]
                return self._send(200, search_fusion.search_strong(ms, q, limit=limit,
                                                                   project=project, force_raw=force_raw,
                                                                   recency=recency, scene=scene,
                                                                   goal=goal, state=state, tilt=tilt))
            if path.startswith("/search/"):
                q = urllib.parse.unquote(path[len("/search/"):])
                limit = int((qs.get("limit") or [config.SEARCH_LIMIT_DEFAULT])[0])
                project = (qs.get("project") or [None])[0]
                recency = int((qs.get("recency") or [config.RECENCY_DEFAULT])[0])  # [时间维]
                scene = (qs.get("scene") or [None])[0]                             # [空间维]
                goal = int((qs.get("goal") or [0])[0])                             # [价值维]
                state = (qs.get("state") or [None])[0]                             # [状态维]
                tilt = (qs.get("tilt") or [str(int(config.TILT_ENABLED))])[0] in ("1", "true", "yes")  # [注意力倾斜]
                return self._send(200, search_fusion.search(ms, q, limit=limit, project=project,
                                                            recency=recency, scene=scene,
                                                            goal=goal, state=state, tilt=tilt))
            if path.startswith("/rewind/"):
                parts = path[len("/rewind/"):].split("/", 1)
                ts = urllib.parse.unquote(parts[0])
                q = urllib.parse.unquote(parts[1]) if len(parts) > 1 else ""
                return self._send(200, time_window.rewind(ms, ts, q))
            if path.startswith("/timeline/"):
                parts = path[len("/timeline/"):].split("/")
                start = urllib.parse.unquote(parts[0])
                end = urllib.parse.unquote(parts[1]) if len(parts) > 1 else start
                project = (qs.get("project") or [None])[0]      # [时间维]
                q = (qs.get("q") or [None])[0]                  # [时间维]
                return self._send(200, time_window.timeline(ms, start, end,
                                                            project=project, q=q))
            if path == "/episodes":
                # [FIX 2026-11-13 P2-13] 默认 limit 50 截断（库内 98 条时只返回前 50），
                # 且没有 limit 参数；现在支持 limit（默认 config.EPISODE_LIMIT_DEFAULT=200），
                # 并回传 count 便于调用方判断是否被截断。
                limit = int((qs.get("limit") or [config.EPISODE_LIMIT_DEFAULT])[0])
                eps = time_window.get_episodes(ms, limit=limit)
                return self._send(200, {"episodes": eps, "count": len(eps)})
            if path.startswith("/episodes/"):
                eid = path[len("/episodes/"):]
                ep = next((e for e in time_window.get_episodes(ms, limit=500) if e["id"] == eid), None)
                # [FIX 2026-11-13 P2-14] 不存在时返回 200 + {"error": ...} 会让客户端
                # 误判成功（HTTP 状态才是契约）；改为 404。
                if not ep:
                    return self._send(404, {"error": "not found", "id": eid})
                return self._send(200, ep)
            if path == "/forgotten":
                threshold = float((qs.get("threshold") or [config.ARCHIVE_THRESHOLD])[0])
                hl = (qs.get("half_life") or [None])[0]
                hl = float(hl) if hl else None
                return self._send(200, {"forgotten": search_fusion.forgotten_nodes(ms, threshold, hl)})
            if path == "/edges":
                # [2026-10-29 新增] 边列表（只读）：贾维斯等智能体做记忆体检需看边关系
                limit = min(int((qs.get("limit") or [500])[0]), 5000)
                offset = int((qs.get("offset") or [0])[0])
                etype = (qs.get("type") or [None])[0]
                sql = "SELECT * FROM edges WHERE 1=1"
                args = []
                if etype:
                    sql += " AND edge_type=?"
                    args.append(etype)
                sql += " ORDER BY strength DESC LIMIT ? OFFSET ?"
                args += [limit, offset]
                rows = ms.graph.conn.execute(sql, args).fetchall()
                return self._send(200, {"edges": [dict(r) for r in rows], "count": len(rows),
                                        "limit": limit, "offset": offset})
            if path == "/stats":
                # [2026-10-29 新增] 库统计（只读）：节点/边/类型/项目分布
                s = ms.graph.stats()
                vc = ms.vector.count()
                indexable = ms.graph.indexable_node_count()
                by_type = {}
                for r in ms.graph.conn.execute(
                        "SELECT node_type, COUNT(*) AS c FROM nodes GROUP BY node_type"):
                    by_type[r["node_type"]] = r["c"]
                by_project = {}
                for r in ms.graph.conn.execute(
                        "SELECT project, COUNT(*) AS c FROM nodes GROUP BY project"):
                    by_project[r["project"]] = r["c"]
                return self._send(200, {"nodes": s["nodes"], "edges": s["edges"],
                                        "episodes": s["episodes"], "by_status": s["by_status"],
                                        "by_type": by_type, "by_project": by_project,
                                        "indexable_nodes": indexable,
                                        "vector_indexed": vc,
                                        # [FIX 2026-11-13 P1-6] 同 /health：按可索引集合算缺口
                                        "vector_gap": max(0, indexable - vc)})
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
        except ValueError as e:
            # [FIX 2026-11-13 P2-15] 查询参数类型错误（recency=abc、goal=abc）原来一路
            # 冒到 except Exception 变成 500 "invalid literal for int()"。现在统一 400。
            return self._send(400, {"error": "bad parameter: %s" % e})
        except Exception as e:
            return self._send(500, {"error": str(e)})

    # ---------- POST ----------
    def do_POST(self):
        if not self._auth():
            return self._send(401, {"error": "unauthorized"})
        path, _ = self._params()
        try:
            body = self._read_body()   # [FIX 2026-11-13 P1-10] 移进 try，坏请求体由下面转 400
            ms = self.ms
            if path == "/nodes":
                c = body.get("content")
                # [FIX 2026-11-13 P1-10] 类型/空值前置校验：content=123 原来直接进 Node，
                # 后续 .encode() 崩在 try 外的 raw_vid 上（500 却已经入库一条节点）。
                if not isinstance(c, str) or not c.strip():
                    return self._send(400, {"error": "content must be a non-empty string"})
                node = Node(content=c, node_type=body.get("node_type", "event"),
                            project=body.get("project", "default"), tags=body.get("tags", []),
                            source=body.get("source", "api"), confidence=body.get("confidence", 1.0),
                            timestamp=body.get("timestamp"),
                            importance=body.get("importance"))  # [价值维] goal_related/operational/reference
                nid, created = ms.add_node_with_vector(node)
                # [2026-10-29 借鉴 digital-life-core v0.9.19] 写入回执验证：
                # 写完按原文读回（SQLite 主键直读），saved 是唯一判定依据；
                # 读不回自动重写一次，二次不回才如实报未存住。
                verified, saved_nid = _Handler._verify_saved(ms, nid, node)
                if created:
                    try:
                        from linker import auto_link, auto_scene_link, auto_temporal_link, auto_state_link, auto_goal_link
                        auto_link(ms, nid, node.content, project=node.project)
                        auto_temporal_link(ms, nid, node.content, project=node.project)  # [时间维]
                        auto_scene_link(ms, nid, node.content, project=node.project)     # [空间维]
                        auto_state_link(ms, nid, node.content, project=node.project)     # [状态维]
                        auto_goal_link(ms, nid, node.content, project=node.project)      # [价值/目标维]
                    except Exception:
                        pass
                # [2026-10-17] 日常写入自动落原文层：content 哈希幂等 vid，node_id 双向指针
                raw_vid = None
                try:
                    raw_vid = "v_" + hashlib.sha1(node.content.encode("utf-8")).hexdigest()[:12]
                    ms.add_verbatim(node.content, source=node.source or "api", node_id=nid,
                                    session=node.project or "default", vid=raw_vid)
                except Exception:
                    pass
                return self._send(200, {"id": saved_nid, "created": created,
                                        "saved": verified, "verified": verified,
                                        "raw_vid": raw_vid})
            if path == "/extract":
                created_n = 0
                verified_n = 0
                raw_vids = []
                if body.get("text"):
                    # [FIX 2026-11-13 P1] 走 return_ids=True，逐条读回校验：
                    # 原来只拿到 int，verified_n 在这条分支上恒为 0，等于假报"0 条存住"。
                    n_txt, txt_ids = extract.extract_from_text(
                        body["text"], source=body.get("source", "api"),
                        project=body.get("project", "default"), ms=ms, return_ids=True)
                    created_n += n_txt
                    for tid in txt_ids:
                        if ms.graph.get_node(tid) is not None:
                            verified_n += 1
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
                            # [2026-10-29 写入回执验证] 每条新建节点写后读回，saved 是判定依据
                            verified, _sn = _Handler._verify_saved(ms, nid, node)
                            if verified:
                                verified_n += 1
                            try:
                                from linker import auto_link, auto_scene_link, auto_temporal_link, auto_state_link, auto_goal_link
                                auto_link(ms, nid, node.content, project=node.project)
                                auto_temporal_link(ms, nid, node.content, project=node.project)  # [时间维]
                                auto_scene_link(ms, nid, node.content, project=node.project)     # [空间维]
                                auto_state_link(ms, nid, node.content, project=node.project)     # [状态维]
                                auto_goal_link(ms, nid, node.content, project=node.project)      # [价值/目标维]
                            except Exception:
                                pass
                        created_n += 1 if created else 0
                for e in body.get("edges", []):
                    # [2026-10-20 时间维] 边类型经 event_normalizer 归一（中文别名→标准边类型）
                    etype = event_normalizer.normalize_edge_type(e.get("type", "related"))
                    ms.graph.add_edge(Edge(from_id=e.get("from", ""), to_id=e.get("to", ""),
                                           edge_type=etype,
                                           source=e.get("source", "api"),
                                           description=e.get("desc", "")))
                return self._send(200, {"created": created_n, "verified": verified_n,
                                        "raw_vids": raw_vids})
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
                    # [2026-10-20 时间维] 边类型归一（中文别名→标准边类型）
                    etype = event_normalizer.normalize_edge_type(e.get("type") or e.get("edge_type", "related"))
                    edge = Edge(id=e.get("id"), from_id=from_id, to_id=to_id,
                                edge_type=etype,
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
                # [FIX 2026-11-13 P1-12] 状态置为 obsolete / merged 后同步删向量：
                # /decay 会删向量，/mark 不会 → 同一语义两种结果，vector_gap 与检索一致性都乱。
                # 向量清理集中在 API 层做，node_lifecycle 保持纯状态职责。
                if ok and node and node.status in ("obsolete", "merged"):
                    try:
                        ms.vector.delete(nid)
                    except Exception:
                        pass
                return self._send(200, {"ok": ok, "status": node.status if node else None})
            if path == "/decay":
                archived = ms.graph.apply_decay()
                if archived:
                    ms.graph.archive_nodes(archived)
                    for nid in archived:
                        ms.vector.delete(nid)
                return self._send(200, {"decayed": True, "archived_count": len(archived)})
            if path == "/episodes/build":
                stats = time_window.build_episodes(ms, return_stats=True)
                # [FIX 2026-11-13] 同段窗口只刷新不新增，把 updated 一并回传，便于确认幂等
                return self._send(200, {"episodes_created": stats["created"],
                                        "episodes_updated": stats["updated"],
                                        "episodes_unchanged": stats["unchanged"]})
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
        except ValueError as e:
            # [FIX 2026-11-13 P1-10] 请求体非法（坏 JSON / 非对象 / content 类型错）→ 400，
            # 不再落到 500 让调用方分不清"参数错"还是"服务崩"。
            return self._send(400, {"error": str(e)})
        except Exception as e:
            return self._send(500, {"error": str(e)})


def start_server(ms, host=None, port=None):
    host = host or config.HOST
    port = port or config.PORT
    _Handler.ms = ms
    httpd = ThreadingHTTPServer((host, port), _Handler)
    return httpd
