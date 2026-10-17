# -*- coding: utf-8 -*-
"""
超级记忆系统 —— 边生命周期 edge_lifecycle.py
创建 / 强度调整 / 悬空边清理。治理原则：边类型只改 edge_type 字段，不删节点只调属性。
"""
from core import Edge, MemorySystem


def add_or_update_edge(ms, from_id, to_id, edge_type, source="manual",
                       description="", strength=1.0, confidence=1.0):
    """同向同类型边已存在 → 更新强度/描述（幂等）；否则新建。"""
    if from_id == to_id:
        return None, False
    existing = [e for e in ms.graph.get_edges(from_id) if e.to_id == to_id and e.edge_type == edge_type]
    if existing:
        e = existing[0]
        e.strength = strength
        e.description = description or e.description
        e.source = source
        # 直接 SQL 更新
        ms.graph.conn.execute(
            "UPDATE edges SET strength=?, description=?, source=? WHERE id=?",
            (e.strength, e.description, source, e.id))
        ms.graph.conn.commit()
        return e.id, False
    edge = Edge(from_id=from_id, to_id=to_id, edge_type=edge_type, source=source,
                description=description, strength=strength, confidence=confidence)
    return ms.graph.add_edge(edge)


def clean_dangling_edges(ms):
    """清理悬空边（端点节点已不存在）"""
    ids = ms.graph.dangling_edges()
    for eid in ids:
        ms.graph.delete_edge(eid)
    return len(ids)


def adjust_strength(ms, edge_id, delta):
    """强度微调（±）"""
    rows = ms.graph.conn.execute("SELECT * FROM edges WHERE id=?", (edge_id,)).fetchall()
    if not rows:
        return False
    s = rows[0]["strength"] + delta
    s = max(0.0, min(2.0, s))
    ms.graph.conn.execute("UPDATE edges SET strength=? WHERE id=?", (s, edge_id))
    ms.graph.conn.commit()
    return True
