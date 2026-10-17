# -*- coding: utf-8 -*-
"""
超级记忆系统 —— 指标统计 metrics.py
节点/边/向量覆盖/衰减分布/状态分布/访问热点的量化视图。
"""
from collections import Counter

import config
from core import MemorySystem, weight_zone


def full_report(ms):
    s = ms.graph.stats()
    vec_count = ms.vector.count()
    # 衰减分布
    zones = Counter()
    for n in ms.graph.list_nodes(limit=100000):
        zones[weight_zone(n.weight)] += 1
    # 类型分布
    types = Counter()
    for n in ms.graph.list_nodes(limit=100000):
        types[n.node_type] += 1
    # 访问热点 TOP
    hot = sorted(ms.graph.list_nodes(limit=100000),
                 key=lambda n: n.access_count, reverse=True)[:10]
    return {
        "stats": s,
        "vector_indexed": vec_count,
        "vector_gap": max(0, s["nodes"] - vec_count),
        "weight_zones": dict(zones),
        "node_types": dict(types.most_common()),
        "hot_nodes": [{"id": n.id, "content": n.content[:40], "access_count": n.access_count,
                       "weight": round(n.weight, 3)} for n in hot],
    }
