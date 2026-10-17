# -*- coding: utf-8 -*-
"""
超级记忆系统 —— 跨会话检索 cross_session.py
按会话/来源标记聚合检索入口：同一项目多会话记忆的合并视图。
"""
import config
from core import MemorySystem


def search_across_sessions(ms, q, project="default", limit=config.SEARCH_LIMIT_DEFAULT):
    """跨会话检索：project 维度聚合（会话按 source 区分，默认全量）。"""
    from search_fusion import search
    return search(ms, q, limit=limit, project=project)


def list_sessions(ms, project=None):
    """按 source 分组列出会话来源与节点数"""
    sql = "SELECT source, COUNT(*) c, MAX(created_at) last FROM nodes"
    args = []
    if project:
        sql += " WHERE project=?"
        args.append(project)
    sql += " GROUP BY source ORDER BY c DESC"
    rows = ms.graph.conn.execute(sql, args).fetchall()
    return [{"source": r["source"], "count": r["c"], "last": r["last"]} for r in rows]
