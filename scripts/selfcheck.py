# -*- coding: utf-8 -*-
"""
超级记忆系统 —— 周体检/自查 scripts/selfcheck.py
流程：总览 → 悬空边删 / 自环清理 → 孤岛统计 → 重复合并 → 空内容标注 →
     因果连通性 → 认错巡检（版本堆积/沉睡/决策反转/状态机健康度）→ 向量对账修复。
治理原则：不删节点只调属性；宁少标不可错标；每步可回滚（先备份由 scheduler 负责）。
[FIX] 向量对账修复双写一致性（missing 补写 / extra 清理）。
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from core import Edge, MemorySystem  # noqa: E402
from edge_lifecycle import clean_dangling_edges  # noqa: E402
from node_lifecycle import mark_obsolete, mark_outdated  # noqa: E402


def run_selfcheck(ms, dry_run=False):
    report = {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "dry_run": dry_run}
    log = []

    # 1. 总览
    s = ms.graph.stats()
    report["stats"] = s
    log.append(f"总览 nodes={s['nodes']} edges={s['edges']} episodes={s['episodes']}")

    # 2. 悬空边 / 自环
    dangling = ms.graph.dangling_edges()
    loops = ms.graph.self_loops()
    if not dry_run:
        removed = clean_dangling_edges(ms)
        for eid in loops:
            ms.graph.delete_edge(eid)
    else:
        removed = len(dangling) + len(loops)
    report["dangling_edges"] = len(dangling)
    report["self_loops"] = len(loops)
    report["removed_edges"] = removed if not dry_run else None
    log.append(f"悬空边 {len(dangling)} / 自环 {len(loops)} / 清理 {removed}")

    # 3. 孤岛
    isolated = ms.graph.isolated_nodes(min_edges=0)
    report["isolated_nodes"] = len(isolated)
    log.append(f"孤岛节点（无边）{len(isolated)}")

    # 4. 重复检测 → 合并留痕（版本堆积）
    dups = ms.graph.find_duplicates_by_prefix(60)
    merged = 0
    groups = {}
    for d in dups:
        groups.setdefault(d["pre"], []).append(d["id"])
    for pre, ids in groups.items():
        if len(ids) < 2:
            continue
        # 保留：content 最长 / created_at 最新
        keep = max(ids, key=lambda i: (len(ms.graph.get_node(i).content or ""),
                                       ms.graph.get_node(i).created_at))
        for i in ids:
            if i == keep:
                continue
            node = ms.graph.get_node(i)
            if node and node.status not in ("outdated", "merged", "obsolete"):
                if not dry_run:
                    from node_lifecycle import merge_into
                    merge_into(ms, i, keep)
                merged += 1
    report["duplicate_groups"] = len(groups)
    report["merged_duplicates"] = merged
    log.append(f"重复组 {len(groups)} / 合并 {merged}")

    # 5. 空内容
    empty = ms.graph.empty_content_nodes()
    report["empty_content_nodes"] = len(empty)
    if empty:
        log.append(f"空内容节点 {len(empty)}（待人工确认，不自动删）")

    # 6. 因果连通性
    causal_nodes = 0
    for n in ms.graph.list_nodes(limit=100000):
        if any(e.edge_type in config.CAUSAL_EDGE_TYPES for e in ms.graph.get_edges(n.id)):
            causal_nodes += 1
    report["causal_connected_nodes"] = causal_nodes
    log.append(f"因果连通节点 {causal_nodes}")

    # 7. 认错巡检
    reversal = 0
    dormant = 0
    all_nodes = ms.graph.list_nodes(limit=100000)
    # 7a. 沉睡节点：confirmed + 从未访问 + weight<0.3 → obsolete
    for n in all_nodes:
        if n.status == "confirmed" and n.access_count == 0 and n.weight < 0.3:
            if not dry_run:
                mark_obsolete(ms, n.id)
            dormant += 1
    # 7b. 决策反转：含反转关键词的 decision → 找同主题旧决策标 outdated + evolves_from
    decisions = [n for n in all_nodes if n.node_type == "decision"]
    for n in decisions:
        if not any(k in n.content for k in config.DECISION_REVERSE_KEYWORDS):
            continue
        topic = n.content
        for k in config.DECISION_REVERSE_KEYWORDS:
            topic = topic.replace(k, "")
        topic = topic.strip("，。、 ")
        if len(topic) < 4:
            continue
        for old in decisions:
            if (old.id != n.id and old.created_at < n.created_at and old.status in
                    ("active", "confirmed", "permanent") and topic and topic in old.content):
                if not dry_run:
                    mark_outdated(ms, old.id, replaced_by=n.id)
                    ms.graph.add_edge(Edge(from_id=old.id, to_id=n.id, edge_type="evolves_from",
                                           source="selfcheck", description="决策反转留痕"))
                reversal += 1
    # 7c. 状态机健康度
    flow_states = {st: 0 for st in ("hypothesis", "outdated", "obsolete", "disputed", "merged")}
    for n in all_nodes:
        if n.status in flow_states:
            flow_states[n.status] += 1
    report["dormant_obsoleted"] = dormant
    report["decision_reversals"] = reversal
    report["state_flow"] = flow_states
    log.append(f"认错巡检：沉睡→obsolete {dormant} / 决策反转 {reversal} / 流转状态 {flow_states}")
    if all(v == 0 for v in flow_states.values()) and len(all_nodes) > 10:
        log.append("提示：状态机从未流转（无 hypothesis/outdated/obsolete/disputed/merged），建议人工审视")

    # 8. 向量对账（[FIX] 双写一致性修复）
    repair = ms.vector.repair_index(ms.graph, dry_run=dry_run)
    report["vector_reconciliation"] = repair
    log.append(f"向量对账：缺失 {repair['before']['missing']} / 多余 {repair['before']['extra']} "
               f"/ 修复 {repair['fixed']}")

    # 落日志 + 元数据
    ms.graph.save_snapshot_meta()
    if not dry_run:
        ms.graph.set_meta("last_selfcheck_at", report["time"])
        os.makedirs(config.SELFCHECK_LOG_DIR, exist_ok=True)
        with open(os.path.join(config.SELFCHECK_LOG_DIR,
                               f"selfcheck_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"),
                  "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    report["log"] = log
    report["summary"] = (f"体检完成：清理边 {removed}，合并重复 {merged}，沉睡归档 {dormant}，"
                         f"决策反转 {reversal}，向量修复 {repair['fixed']}")
    return report


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="预演")
    args = ap.parse_args()
    ms = MemorySystem()
    r = run_selfcheck(ms, dry_run=args.dry_run)
    print(json.dumps(r, ensure_ascii=False, indent=2))
