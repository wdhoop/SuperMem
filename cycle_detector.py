# -*- coding: utf-8 -*-
"""
超级记忆系统 —— 环检测 cycle_detector.py
[FIX] 防自环 / 防有向循环（图遍历合法性），避免因果链 BFS 死循环。
"""
from core import MemorySystem


def has_self_loop(from_id, to_id):
    return from_id == to_id


def would_create_cycle(ms, from_id, to_id):
    """加边 from_id→to_id 前，检查图中是否已存在 to_id→...→from_id 路径。
    若存在，则新边会构成有向环，拒绝。DFS 防重复。"""
    if from_id == to_id:
        return True
    stack, seen = [to_id], set()
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for e in ms.graph.out_edges(cur):
            if e.to_id == from_id:
                return True
            if e.to_id not in seen:
                stack.append(e.to_id)
    return False


def detect_all_cycles(ms):
    """全图环检测（体检用）：返回所有成环节点 id 集合。"""
    nodes = [n.id for n in ms.graph.list_nodes(limit=100000)]
    in_cycle = set()
    for start in nodes:
        stack, path = [(start, [start])], set()
        while stack:
            cur, trail = stack.pop()
            if cur in path:
                continue
            path.add(cur)
            for e in ms.graph.out_edges(cur):
                if e.to_id == start:
                    in_cycle.update(trail + [e.to_id])
                elif e.to_id not in path:
                    stack.append((e.to_id, trail + [e.to_id]))
    return in_cycle
