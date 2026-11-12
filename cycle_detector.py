# -*- coding: utf-8 -*-
"""
超级记忆系统 —— 环检测 cycle_detector.py
[FIX] 防自环 / 防有向循环（图遍历合法性），避免因果链 BFS 死循环。
"""
from core import MemorySystem

# [FIX 2026-11-13 P1-9] 只有"结构性/因果性"边才参与环判定。
# related 是 auto_link 建的双向边，A↔B 天然构成 2 节点环；before/after 是时序链；
# shares_theme_with / involves / occurred_in 是弱关联。原实现把所有边一视同仁，
# 于是 2216 个节点里有 1991 个被判"在环上"（89.8%）——指标彻底失效。
CYCLE_EDGE_TYPES = {
    "derives_insight", "enable", "trigger", "refutes", "improves",
    "part_of", "merged_into", "replaced_by", "superseded_by",
    "evolves_from", "contributes_to", "blocks", "uses_rule",
    "contains_technique", "applies", "transitions_to",
}


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
    """全图环检测（体检用）：返回真正处在环上的节点 id 集合。

    [FIX 2026-11-13 P1-9] 原实现有两个硬伤：
      ① 不区分边类型——related 双向边让 A↔B 全被算成环，成环率 89.8%（假指标）；
      ② DFS 的所有分支共享同一个 `path` 集合：一旦某节点被一条分支访问过，
         另一条分支再遇到它就直接 continue，漏掉真实环，还让 trail 无限增长。
    现在改为"只沿结构性边做 Tarjan 强连通分量"，迭代实现（不爆递归栈），
    分量大小 >1 或自环即判为环。"""
    nodes = [n.id for n in ms.graph.list_nodes(limit=100000)]
    idset = set(nodes)
    adj = {}
    for n in nodes:
        outs = []
        for e in ms.graph.out_edges(n):
            if e.edge_type not in CYCLE_EDGE_TYPES:
                continue
            if e.to_id not in idset:
                continue
            outs.append(e.to_id)
        adj[n] = outs

    index, low, onstack, stk = {}, {}, {}, []
    result = set()
    counter = 0
    for root in nodes:
        if root in index:
            continue
        call = [(root, 0)]
        while call:
            v, pi = call[-1]
            if pi == 0:
                index[v] = low[v] = counter
                counter += 1
                stk.append(v)
                onstack[v] = True
            children = adj.get(v, [])
            pushed = False
            for i in range(pi, len(children)):
                w = children[i]
                if w not in index:
                    call[-1] = (v, i + 1)
                    call.append((w, 0))
                    pushed = True
                    break
                if onstack.get(w):
                    low[v] = min(low[v], index[w])
            if pushed:
                continue
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stk.pop()
                    onstack[w] = False
                    comp.append(w)
                    if w == v:
                        break
                if len(comp) > 1 or (len(comp) == 1 and v in adj.get(v, [])):
                    result.update(comp)
            call.pop()
            if call:
                pv = call[-1][0]
                low[pv] = min(low[pv], low[v])
    return result
