# -*- coding: utf-8 -*-
"""
超级记忆系统 —— 模式分析 analyze_patterns.py
高频标签 / 遗忘趋势 / 因果密度，供体检报告与洞察。
"""
from collections import Counter
from datetime import datetime

import config   # [FIX 2026-11-13 P1-7] 原来漏 import，causal_density 一调就 NameError
from core import MemorySystem, weight_zone


def tag_frequency(ms, top=20):
    """高频标签。过滤纯标点/空白标签：历史上 tags 被二次 JSON 编码过（105 条），
    解码后残留 `"` `,` 这类碎片，否则统计表会出现 `('"', 462)` 的假热门。"""
    c = Counter()
    for n in ms.graph.list_nodes(limit=100000):
        for t in (n.tags or []):
            t = str(t).strip()
            if len(t) < 2:
                continue
            if not any(ch.isalnum() or "\u4e00" <= ch <= "\u9fff" for ch in t):
                continue
            c[t] += 1
    return c.most_common(top)


def causal_density(ms):
    """因果边占比：反映记忆图的"因果连通"程度"""
    edges = ms.graph.conn.execute("SELECT edge_type FROM edges").fetchall()
    total = len(edges)
    causal = sum(1 for r in edges if r["edge_type"] in config.CAUSAL_EDGE_TYPES)
    return {"total_edges": total, "causal_edges": causal,
            "ratio": round(causal / total, 3) if total else 0.0}


def forgetting_trend(ms):
    """遗忘趋势：按创建月份统计 Cold/Archive 占比"""
    by_month = {}
    for n in ms.graph.list_nodes(limit=100000):
        m = n.created_at[:7]
        by_month.setdefault(m, {"total": 0, "cold": 0, "archive": 0})
        by_month[m]["total"] += 1
        z = weight_zone(n.weight)
        if z in ("Cold", "Archive"):
            by_month[m]["cold" if z == "Cold" else "archive"] += 1
    return dict(sorted(by_month.items()))
