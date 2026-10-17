# -*- coding: utf-8 -*-
"""
超级记忆系统 —— 节点生命周期 node_lifecycle.py
确认 / 标记永久 / 假设 / 过时 / 废弃 / 合并 / 争议 的显式流转，全部经状态机合法表校验。
"""
import json
from datetime import datetime, timedelta

import config
from core import Edge, MemorySystem, Node, transition_allowed


def confirm(ms, nid):
    """确认：hypothesis/active → confirmed"""
    return ms.graph.set_status(nid, "confirmed")


def mark_hypothesis(ms, nid):
    return ms.graph.set_status(nid, "hypothesis")


def mark_permanent(ms, nid, years=10):
    """S 级永久：设 marked_until 未来日期（默认 10 年），状态 → permanent"""
    node = ms.graph.get_node(nid)
    if not node:
        return False
    if not transition_allowed(node.status, "permanent"):
        return False
    until = (datetime.now() + timedelta(days=365 * years)).strftime("%Y-%m-%d %H:%M")
    node.status = "permanent"
    node.marked_until = until
    ms.graph.update_node(node, {"status", "marked_until"})
    return True


def mark_outdated(ms, nid, replaced_by=None):
    """过时：被新版取代，设 replaced_by + replaced_by 边"""
    node = ms.graph.get_node(nid)
    if not node or not transition_allowed(node.status, "outdated"):
        return False
    node.status = "outdated"
    if replaced_by:
        node.replaced_by = replaced_by
        ms.graph.add_edge(Edge(from_id=nid, to_id=replaced_by, edge_type="replaced_by",
                               source="selfcheck", description="版本链：旧版被新版取代"))
    ms.graph.update_node(node, {"status", "replaced_by"})
    return True


def mark_obsolete(ms, nid):
    """废弃：沉睡退出检索（保留骨架）"""
    return ms.graph.set_status(nid, "obsolete")


def mark_disputed(ms, nid):
    return ms.graph.set_status(nid, "disputed")


def merge_into(ms, nid, target_id):
    """合并留痕：nid → merged，指向保留节点 target_id，建 merged_into 边"""
    node = ms.graph.get_node(nid)
    if not node or not transition_allowed(node.status, "merged"):
        return False
    node.status = "merged"
    ms.graph.add_edge(Edge(from_id=nid, to_id=target_id, edge_type="merged_into",
                           source="selfcheck", description="重复合并留痕"))
    ms.graph.update_node(node, {"status"})
    return True
