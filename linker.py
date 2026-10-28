# -*- coding: utf-8 -*-
"""
超级记忆系统 —— 自动语义关联 linker.py
消灭孤岛节点：新节点入库后，用向量检索同 project 的相似老节点，
score 超阈值建 related 边，让新记忆挂到已有知识树上。
幂等：core.add_edge(dedup=True) 按 from/to/type 去重，重复提取不会产生重复边。
失败静默（try/except 由调用方包裹，不阻断主流程）。
[UPD 2026-10-20 时间维] 新增 auto_temporal_link：识别时序连接词，与同项目最近
  事件节点建 before/after 边（保守规则，仅同 project 且双方为 event/decision 节点）。
[UPD 2026-10-20 空间维] 新增 auto_scene_link：content 提到库内场景实体节点
  （person/system/item/scene/project/work/book/concept）→ 建 involves 边。
[UPD 2026-10-20 状态维] 新增 auto_state_link：content 含显式状态转移词 →
  提取目标状态短语，命中库内 state 节点则建 transitions_to 边，未命中自动创建。
"""

from core import Edge, Node


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


def auto_temporal_link(ms, node_id, content, project="default"):
    """时间维：新节点含"后来/之后/随后/最终/于是"→ 与同 project 时间上最近的先前
    event/decision 节点建 after 边；含"之前/起初/最初/先做了"→ 建 before 边。
    保守规则：只建一条、仅同 project、目标非 obsolete、目标 timestamp 早于当前。
    语义：after 表示"本事件发生在其后"，before 表示"本事件发生在其前"。
    返回新边数；任何异常静默返回 0，不阻断主流程。
    """
    try:
        import config
        if not config.TEMPORAL_LINK_ENABLED:
            return 0
        c = (content or "")
        hit_word = None
        edge_type = None
        for w in config.TEMPORAL_AFTER_KEYWORDS:
            if w in c:
                edge_type, hit_word = "after", w
                break
        if edge_type is None:
            for w in config.TEMPORAL_BEFORE_KEYWORDS:
                if w in c:
                    edge_type, hit_word = "before", w
                    break
        if edge_type is None:
            return 0
        # 同 project、非 obsolete 的先前事件节点（按时间倒序取最近一条）
        nodes = ms.graph.list_nodes(project=project, limit=100000)
        now_ts = getattr(ms.graph.get_node(node_id), "timestamp", "9999")
        cands = [n for n in nodes
                 if n.id != node_id and n.status != "obsolete"
                 and n.node_type in ("event", "decision")
                 and n.timestamp <= now_ts]
        if not cands:
            return 0
        cands.sort(key=lambda n: n.timestamp, reverse=True)
        prev = cands[0]
        # 幂等：同 from/to/type 已存在则跳过
        existing = {e.edge_type for e in ms.graph.get_edges(node_id) if e.to_id == prev.id}
        if edge_type in existing:
            return 0
        ms.graph.add_edge(Edge(from_id=node_id, to_id=prev.id, edge_type=edge_type,
                               source="auto_temporal",
                               description=f"时序[{hit_word}]：本事件在其{'后' if edge_type=='after' else '前'}发生"))
        return 1
    except Exception:
        return 0


def auto_scene_link(ms, node_id, content, project="default"):
    """空间维：content 提到库内场景实体节点（person/system/item/scene/project/
    work/book/concept）→ 建 involves 边（事件涉及该场景实体）。
    规则先行：实体名子串匹配（实体名长度 2-60），幂等（同 from/to/type 已存在跳过）。
    任何异常静默返回 0，不阻断主流程。"""
    try:
        import config
        if not config.SCENE_LINK_ENABLED:
            return 0
        c = (content or "").strip()
        if len(c) < 2:
            return 0
        nodes = ms.graph.list_nodes(limit=100000)
        # 幂等按 (to_id, edge_type) 检查，避免 auto_link 的 related 边误挡 involves 边
        existing_pairs = {(e.to_id, e.edge_type) for e in ms.graph.get_edges(node_id)}
        linked = 0
        for n in nodes:
            if n.id == node_id or n.status == "obsolete":
                continue
            if n.node_type not in config.SCENE_ENTITY_TYPES:
                continue
            if (n.id, "involves") in existing_pairs:
                continue
            name = (n.content or "").strip()
            if len(name) < 2 or len(name) > 60:
                continue
            if name in c:
                ms.graph.add_edge(Edge(from_id=node_id, to_id=n.id, edge_type="involves",
                                       source="auto_scene",
                                       description=f"涉及场景实体[{name}]"))
                existing_pairs.add((n.id, "involves"))
                linked += 1
        return linked
    except Exception:
        return 0


def auto_state_link(ms, node_id, content, project="default"):
    """状态维：事件 content 含显式状态转移词 → 提取目标状态短语，命中库内 state 节点
    则建 transitions_to 边，未命中自动创建 state 节点并建边。
    仅处理强转移词（状态变为/切换到/转为等），"状态为/是"声明型与泛化"进入/改为"
    不自动处理（避免误判）；任何异常静默返回 0，不阻断主流程。"""
    try:
        import config
        import re
        if not config.STATE_LINK_ENABLED:
            return 0
        c = (content or "").strip()
        if len(c) < 4:
            return 0
        pat = re.compile(
            r"(?:状态变为|状态变成|状态切换到|状态切换为|状态改为|状态改成|切换为|转为|切到|"
            r"switched to|transitioned to|changed to|turned into)"
            r"[：: ]{0,2}([^，。；!?！？：:\n]{2,60})")
        m = pat.search(c)
        if not m:
            return 0
        state_name = m.group(1).strip().rstrip("的了 and to")
        if len(state_name) < 2:
            return 0
        # 找库内已有 state 节点（content 精确匹配优先，其次包含）
        target = None
        for n in ms.graph.list_nodes(limit=100000):
            if n.node_type == "state" and n.status != "obsolete":
                nm = (n.content or "").strip()
                if nm == state_name:
                    target = n
                    break
                if target is None and len(state_name) >= 4 and state_name in nm:
                    target = n
        if target is None:
            _tid, _cr = ms.add_node_with_vector(Node(
                content=state_name, node_type="state", project=project,
                source="auto_state", importance=config.STATE_NODE_IMPORTANCE))
            target = ms.graph.get_node(_tid)
        if target is None:
            return 0
        existing_pairs = {(e.to_id, e.edge_type) for e in ms.graph.get_edges(node_id)}
        if (target.id, "transitions_to") in existing_pairs:
            return 0
        ms.graph.add_edge(Edge(from_id=node_id, to_id=target.id, edge_type="transitions_to",
                               source="auto_state", description=f"状态转移 → [{state_name}]"))
        return 1
    except Exception:
        return 0


def auto_goal_link(ms, node_id, content, project="default"):
    """价值/目标维：content 含显式目标/愿望/里程碑/原则短语 → 命中库内 goal 节点
    则建 contributes_to 边，未命中自动创建 goal 节点并建边。
    保守规则：仅处理显式目标表述（目标/愿望/打算/里程碑/红线等），幂等；
    任何异常静默返回 0，不阻断主流程。"""
    try:
        import config
        import re
        if not config.GOAL_LINK_ENABLED:
            return 0
        c = (content or "").strip()
        if len(c) < 6:
            return 0
        pats = [
            re.compile(r"(?:目标|愿望|打算|愿景)(?:是|为|：|:)?\s*([^。；!?！？：:]{2,40})"),
            re.compile(r"(?:要|想|希望)(?:实现|做|完成|打造|搭建|做出来)([^。；!?！？]{2,40})"),
            re.compile(r"(?:里程碑|下一步|接下来|计划)(?:是|为|：|:)\s*([^。；!?！？]{2,40})"),
            re.compile(r"(?:红线|底线|原则)(?:是|为|：|:)?\s*([^。；!?！？]{2,40})"),
        ]
        phrase = None
        for pat in pats:
            m = pat.search(c)
            if m and len(m.group(1).strip()) >= 2:
                phrase = m.group(1).strip().rstrip("的了和")
                break
        if not phrase:
            return 0
        # 找库内已有 goal 节点（content 精确匹配优先，其次包含）
        target = None
        for n in ms.graph.list_nodes(limit=100000):
            if n.node_type == "goal" and n.status != "obsolete":
                nm = (n.content or "").strip()
                if nm == phrase:
                    target = n
                    break
                if target is None and len(phrase) >= 4 and phrase in nm:
                    target = n
        if target is None:
            _tid, _cr = ms.add_node_with_vector(Node(
                content=phrase, node_type="goal", project=project,
                source="auto_goal", importance="goal_related"))
            target = ms.graph.get_node(_tid)
        if target is None:
            return 0
        existing_pairs = {(e.to_id, e.edge_type) for e in ms.graph.get_edges(node_id)}
        if (target.id, "contributes_to") in existing_pairs:
            return 0
        ms.graph.add_edge(Edge(from_id=node_id, to_id=target.id, edge_type="contributes_to",
                               source="auto_goal", description=f"服务于目标[{phrase}]"))
        return 1
    except Exception:
        return 0


def auto_supersede_link(ms, node_id, content, project="default"):
    """矛盾/剪枝维：content 含认知更新关键词（放弃/不再/改为/换成/推翻/更新为…），
    且与库内旧 decision/rule/technique/project 节点语义高度相似时，
    自动把旧节点流转为 outdated（replaced_by 指向新节点 + superseded_by 边）。
    属剪枝类优化：旧认知下沉不堆叠；保守（阈值更严、只退高价值类型、幂等）；
    任何异常静默返回 0，不阻断主流程。"""
    try:
        import config
        from core import transition_allowed
        if not config.SUPERSEDE_LINK_ENABLED:
            return 0
        c = (content or "").strip()
        if len(c) < 6:
            return 0
        if not any(k in c for k in config.SUPERSEDE_KEYWORDS):
            return 0
        res = ms.vector.query(c, top_k=config.SUPERSEDE_TOP_K + 8, where={"project": project})
        if not res:
            return 0
        retired = 0
        for hit in res:
            oid = hit.get("id", "")
            if not oid or oid == node_id:
                continue
            score = hit.get("score", 0.0)
            if score < config.SUPERSEDE_MIN_SCORE:
                continue
            old = ms.graph.get_node(oid)
            if old is None:
                continue
            # 只退休高价值旧认知；已退休/已废弃/已合并/永久的跳过
            if old.node_type not in config.SUPERSEDE_TARGET_TYPES:
                continue
            if old.status in ("outdated", "obsolete", "merged", "permanent"):
                continue
            if not transition_allowed(old.status, "outdated"):
                continue
            # 幂等：已有 superseded_by 关系的旧节点不再重复退休
            if any(e.edge_type == "superseded_by" for e in ms.graph.get_edges(oid)):
                continue
            old.status = "outdated"
            old.replaced_by = node_id
            ms.graph.update_node(old, {"status", "replaced_by"})
            ms.graph.add_edge(Edge(from_id=oid, to_id=node_id, edge_type="superseded_by",
                                   source="auto_supersede",
                                   description=f"认知更新取代（语义 {score:.2f}）：[{c[:24]}…]"))
            retired += 1
            if retired >= config.SUPERSEDE_TOP_K:
                break
        return retired
    except Exception:
        return 0
