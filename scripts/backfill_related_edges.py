# -*- coding: utf-8 -*-
"""
超级记忆系统 —— 历史 related 边一次性补建 scripts/backfill_related_edges.py
[UPD 2026-11-03] auto_link 升级（k2→k8 + 双向建边 + 阈值分层）后，用新逻辑对全库
非 obsolete 节点重跑一次关联，补 k=2 时代因 top-2 截断/写入时间差漏掉的 related 边。
特性：
  - 幂等：core.add_edge(dedup=True) 按 (from,to,type) 去重，重复跑不会产生重复边；
  - 限量：每个节点 related 边 ≤ AUTO_LINK_K(=8)，弱关联限量 AUTO_LINK_MID_MAX(=3)，
    不会像"周期全库重扫"那样产生补边潮；
  - 双向：命中老节点时同时补"老→新"反向边（根治时间差漏边）。
用法：
    python scripts/backfill_related_edges.py [--dry-run]
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from core import MemorySystem  # noqa: E402
from linker import auto_link  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="预演：不写库，只统计候选")
    args = ap.parse_args()

    ms = MemorySystem()
    nodes = ms.graph.list_nodes(limit=100000)
    active = [n for n in nodes if n.status != "obsolete"]
    print(f"[backfill] 共 {len(nodes)} 节点，处理 {len(active)} 条（排除 {len(nodes) - len(active)} 条 obsolete）")

    if args.dry_run:
        print("[backfill] 预演结束（未改动）")
        return

    t0 = time.time()
    added = 0
    for n in active:
        try:
            added += auto_link(ms, n.id, n.content, project=n.project)
        except Exception as e:  # 单节点失败不阻断全库
            print(f"[backfill] 节点 {n.id} 跳过: {e}")
    dt = time.time() - t0
    print(f"[backfill] 完成: 处理 {len(active)} 节点, 新增 related 配对 {added} 个"
          f"（每个配对建双向两条边, 实际新增边约 {added * 2} 条, 反向边重复命中被 dedup 时实际略少）, 耗时 {dt:.1f}s")


if __name__ == "__main__":
    main()
