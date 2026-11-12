# -*- coding: utf-8 -*-
"""
超级记忆系统 —— 时间窗口 time_window.py
timeline（episodes + 事件块 + 节点） / rewind（截至 ts 的因果链回溯）。
[FIX] episodes 已激活：build_episodes() 按时间窗口把节点+边聚合成情景块，
     修复原架构"已建表未启用、timeline 半残"的问题。
"""
import json
import os
import re
from datetime import datetime

import config
from core import Episode, MemorySystem

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TS_FMTS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
            "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d")


def _parse_dt(s, default=None, end_of_day=False):
    """[FIX 2026-11-13 P2-16] 时间解析容错。

    原实现只认 "%Y-%m-%d %H:%M"，失败就返回 datetime.now()：/timeline 传纯日期
    （2026-11-12）时 start/end 双双塌成 now，区间退化为 [now, now]，真实 143 条
    只剩 37 条。现在支持 5 种写法 + ISO 变体；失败返回 default（不再伪造 now）。
    end_of_day=True 时纯日期补到 23:59:59，让 end=2026-11-12 覆盖当天全天。"""
    if s is None:
        return default
    s = str(s).strip()
    if not s:
        return default
    for fmt in _TS_FMTS:
        try:
            dt = datetime.strptime(s, fmt)
        except ValueError:
            continue
        if fmt == "%Y-%m-%d" and end_of_day:
            dt = dt.replace(hour=23, minute=59, second=59)
        return dt
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return default


def build_episodes(ms, window_hours=6, max_gap_minutes=240, return_stats=False):
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
        if t is None:
            continue   # [FIX 2026-11-13] 时间无法解析的节点不参与窗口切分（原来塌成 now 会挤到末尾造出假窗口）
        if prev and (t - prev).total_seconds() / 60 > max_gap_minutes and cur:
            windows.append(cur)
            cur = []
        cur.append(n)
        prev = t
    if cur:
        windows.append(cur)

    created = 0
    updated = 0
    unchanged = 0
    for w in windows:
        if len(w) < 2:
            continue
        start = w[0].timestamp
        end = w[-1].timestamp
        types = {}
        for n in w:
            types[n.node_type] = types.get(n.node_type, 0) + 1
        main_type = max(types, key=types.get)
        # [UPD 2026-10-20 空间维] scenario 优先取窗口内高频场景实体名（involves 边指向），
        #   无则回退节点类型众数
        scene_counter = {}
        for n in w:
            for e in ms.graph.out_edges(n.id):
                if e.edge_type not in ("involves", "occurred_in"):
                    continue
                ent = ms.graph.get_node(e.to_id)
                if ent is not None:
                    name = (ent.content or "").strip()
                    if len(name) >= 2:
                        scene_counter[name] = scene_counter.get(name, 0) + 1
        scenario = main_type
        if scene_counter:
            scenario = max(scene_counter, key=scene_counter.get)
        title = (w[0].content[:30] + ("…" if len(w[0].content) > 30 else ""))
        summary = "；".join(n.content[:40] for n in w[:5])
        node_ids = [n.id for n in w]
        edge_count = sum(ms.graph.edge_count_of(n.id) for n in w)
        # [FIX 2026-11-13 P1] 幂等键从 (start,end) 改为 start。
        # 原实现在"新节点并入同一窗口"后 end 会后移，二元组永远不相等，于是每跑一次
        # /episodes/build 就把该段整批复制一遍（实测 97 → 241，一次 +144 条）。
        # 同一段起点命中时改为刷新既有 episode；内容完全一致则一个字节都不写。
        dup = ms.graph.conn.execute(
            "SELECT id, end_timestamp, node_ids FROM episodes WHERE start_timestamp=?",
            (start,)).fetchone()
        if dup:
            try:
                old_ids = json.loads(dup["node_ids"] or "[]")
            except Exception:
                old_ids = None
            if dup["end_timestamp"] == end and isinstance(old_ids, list) and old_ids == node_ids:
                unchanged += 1
                continue
            ms.graph.conn.execute(
                """UPDATE episodes SET end_timestamp=?, title=?, summary=?, scenario=?,
                   node_ids=?, edge_count=? WHERE id=?""",
                (end, f"[{main_type}] {title}", summary, main_type,
                 json.dumps(node_ids, ensure_ascii=False), edge_count, dup["id"]))
            updated += 1
            continue
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
    ms.graph.conn.commit()
    if return_stats:
        return {"created": created, "updated": updated, "unchanged": unchanged}
    return created


def get_episodes(ms, limit=config.EPISODE_LIMIT_DEFAULT, scenario=None):
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


def timeline(ms, start_ts, end_ts, project=None, q=None):
    """时间线：范围内 episodes + 事件块 + 节点。
    [UPD 2026-10-20 时间维] 支持按项目过滤（project=）与主题字面过滤（q=，
    匹配 content 子串，不引入向量开销）。"""
    start = _parse_dt(start_ts)
    end = _parse_dt(end_ts, end_of_day=True)   # [FIX 2026-11-13 P2-16] end 传纯日期时覆盖当天全天
    if start is None or end is None:
        raise ValueError("invalid timestamp: start=%r end=%r" % (start_ts, end_ts))
    nodes = []
    for n in ms.graph.list_nodes(limit=100000):
        if n.status in ("obsolete", "merged"):
            continue
        t = _parse_dt(n.timestamp)
        if t is None or not (start <= t <= end):
            continue
        nodes.append(n)
    if project:
        nodes = [n for n in nodes if n.project == project]
    if q:
        ql = q.strip().lower()
        nodes = [n for n in nodes if ql in (n.content or "").lower() or
                 ql in " ".join(n.tags or []).lower()]
    nodes.sort(key=lambda n: n.timestamp)
    eps = []
    for e in get_episodes(ms, limit=2000):
        es = _parse_dt(e["start_timestamp"])
        if es is not None and start <= es <= end:
            eps.append(e)
    if project:
        # episode 按所属节点项目粗过滤：node_ids 含该项目的节点才算
        def _ep_in_project(e):
            for i in e.get("node_ids", []):
                n = ms.graph.get_node(i)
                if n is not None and n.project == project:
                    return True
            return False
        eps = [e for e in eps if _ep_in_project(e)]
    return {"start": start_ts, "end": end_ts, "project": project, "q": q,
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
