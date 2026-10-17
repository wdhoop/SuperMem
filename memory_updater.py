# -*- coding: utf-8 -*-
"""
超级记忆系统 —— 记忆更新 memory_updater.py
面向会话/接口的增量更新：修正内容、替换版本、纠错入口。
[FIX] 提供显式"纠错"能力（记错了怎么改），补齐原架构缺失的反馈回路。
"""
from datetime import datetime

from core import MemorySystem, Node
from node_lifecycle import mark_outdated


def correct_content(ms, nid, new_content, reason=""):
    """纠错：修正节点正文，保留原记录为 outdated + 版本链（replaced_by）。
    返回新节点 id。"""
    node = ms.graph.get_node(nid)
    if not node:
        return None
    new_node = Node(content=new_content[:500], node_type=node.node_type, project=node.project,
                    source=node.source, tags=node.tags, confidence=1.0,
                    version_of=nid, marked_until=node.marked_until)
    new_id, created = ms.add_node_with_vector(new_node)
    if created:
        mark_outdated(ms, nid, replaced_by=new_id)
        from core import Edge
        ms.graph.add_edge(Edge(from_id=nid, to_id=new_id, edge_type="replaced_by",
                               source="user_correction", description=reason))
    return new_id


def replace_version(ms, nid, new_content):
    """版本更新：同主题新版本（evolves_from 边）"""
    return correct_content(ms, nid, new_content, reason="version update")


def touch_batch(ms, ids):
    for nid in ids:
        ms.graph.touch(nid)
    return len(ids)
