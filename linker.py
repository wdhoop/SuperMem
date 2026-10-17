# -*- coding: utf-8 -*-
"""
超级记忆系统 —— 自动语义关联 linker.py
消灭孤岛节点：新节点入库后，用向量检索同 project 的相似老节点，
score 超阈值建 related 边，让新记忆挂到已有知识树上。
幂等：core.add_edge(dedup=True) 按 from/to/type 去重，重复提取不会产生重复边。
失败静默（try/except 由调用方包裹，不阻断主流程）。
"""

from core import Edge


def auto_link(ms, node_id, content, project="default", k=2, min_score=0.45):
    """新节点入库后调用：与老节点按语义建 related 边。返回新边数。"""
    try:
        res = ms.vector.query(content, top_k=k + 8, where={"project": project})
    except Exception:
        return 0
    if not res:
        return 0
    existing_to = {e.to_id for e in ms.graph.get_edges(node_id)}
    linked = 0
    for hit in res:
        oid = hit.get("id", "")
        if not oid or oid == node_id or oid in existing_to:
            continue
        score = hit.get("score", 0.0)
        if score < min_score:
            continue
        old = ms.graph.get_node(oid)
        if old is None or old.status == "obsolete":
            continue
        ms.graph.add_edge(Edge(from_id=node_id, to_id=oid, edge_type="related",
                               source="auto_link", description="语义关联 %.2f" % score))
        existing_to.add(oid)
        linked += 1
        if linked >= k:
            break
    return linked
