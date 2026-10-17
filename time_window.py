# -*- coding: utf-8 -*-
"""
超级记忆系统 —— 时间窗口 time_window.py
timeline（episodes + 事件块 + 节点） / rewind（截至 ts 的因果链回溯）。
[FIX] episodes 已激活：build_episodes() 按时间窗口把节点+边聚合成情景块，
     修复原架构"已建表未启用、timeline 半残"的问题。
"""
import json
import os
from datetime import datetime

import config
from core import Episode, MemorySystem


def _parse_dt(s, default=None):
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return default or datetime.now()


def build_episodes(ms, window_hours=6, max_gap_minutes=240):
    """把连续时间窗口内的节点聚合成 Episode（画面式摘要）。
    窗口划分：按 timestamp 排序，相邻节点间隔 <= max_gap_minutes 归为同窗口。"""
    nodes = ms.graph.list_nodes(limit=100000)
    nodes = [n for n in nodes if n.status not in ("obsolete", "merged")]
    nodes.sort(key=lambda n: n.timestamp)
    windows = []
    cur = []
    prev = None
    for n in nodes:
        t = _parse_dt(n.timestamp)
        if prev and (t - prev).total_seconds() / 60 > max_gap_minutes and cur:
            windows.append(cur)
            cur = []
        cur.append(n)
        prev = t
    if cur:
        windows.append(cur)

    created = 0
    for w in windows:
        if len(w) < 2:
            continue
        start = w[0].timestamp
        end = w[-1].timestamp
        # 幂等：同窗口已存在则跳过
        dup = ms.graph.conn.execute(
            "SELECT id FROM episodes WHERE start_timestamp=? AND end_timestamp=?",
            (start, end)).fetchone()
        if dup:
            continue
        types = {}
        for n in w:
            types[n.node_type] = types.get(n.node_type, 0) + 1
        main_type = max(types, key=types.get)
        title = (w[0].content[:30] + ("…" if len(w[0].content) > 30 else ""))
        summary = "；".join(n.content[:40] for n in w[:5])
        node_ids = [n.id for n in w]
        edge_count = sum(ms.graph.edge_count_of(n.id) for n in w)
        ep = Episode(title=f"[{main_type}] {title}", summary=summary, scenario=main_type,
                     start_timestamp=start, end_timestamp=end, node_ids=node_ids,
                     edge_count=edge_count)
        ms.graph.conn.execute("""INSERT OR IGNORE INTO episodes
            (id,title,summary,scenario,start_timestamp,end_timestamp,node_ids,edge_count,created_at)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (ep.id, ep.title, ep.summary, ep.scenario, ep.start_timestamp, ep.end_timestamp,
             json.dumps(node_ids, ensure_ascii=False), edge_count, ep.created_at))
        ms.graph.conn.commit()
        created += 1
    return created


def get_episodes(ms, limit=50, scenario=None):
    sql = "SELECT * FROM episodes WHERE 1=1"
    args = []
    if scenario:
        sql += " AND scenario=?"
        args.append(scenario)
    sql += " ORDER BY start_timestamp DESC LIMIT ?"
    args.append(limit)
    rows = ms.graph.conn.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["node_ids"] = json.loads(d.get("node_ids") or "[]")
        out.append(d)
    return out


def timeline(ms, start_ts, end_ts):
    """时间线：范围内 episodes + 事件块 + 节点"""
    start, end = _parse_dt(start_ts), _parse_dt(end_ts)
    nodes = [n for n in ms.graph.list_nodes(limit=100000)
             if start <= _parse_dt(n.timestamp) <= end and n.status not in ("obsolete", "merged")]
    nodes.sort(key=lambda n: n.timestamp)
    eps = [e for e in get_episodes(ms, limit=500)
           if start <= _parse_dt(e["start_timestamp"]) <= end]
    return {"start": start_ts, "end": end_ts,
            "episodes": eps,
            "nodes": [n.to_dict() for n in nodes]}


def rewind(ms, ts, query, limit=config.SEARCH_LIMIT_DEFAULT):
    """时间旅行检索：截至 ts 时刻已存在的节点内做语义检索 + 因果链（回溯式）。"""
    as_of = ms.graph.nodes_as_of(ts)
    ids = {n.id for n in as_of}
    # 在 as-of 节点子集上做向量检索
    hits = ms.vector.query(query, top_k=config.RECALL_TOP_K)
    results = []
    for h in hits:
        nid = h["id"]
        if nid not in ids:
            continue
        node = ms.graph.get_node(nid)
        if not node:
            continue
        results.append({"node": node.to_dict(), "vector_score": round(h["score"], 4),
                        "causes": ms.causal.get_causes(nid), "effects": ms.causal.get_effects(nid)})
        if len(results) >= limit:
            break
    return {"as_of": ts, "query": query, "results": results}
