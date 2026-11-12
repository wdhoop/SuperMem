# -*- coding: utf-8 -*-
"""重算 episodes（应用 EPISODE_MAX_NODES 上限），并做一致性自检。

用法：
  python scripts/rebuild_episodes.py --mode dryrun     # 只统计现状
  python scripts/rebuild_episodes.py --mode rebuild    # 清空后全量重算
"""
import argparse
import io
import json
import os
import sys
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class _MS:
    def __init__(self, graph):
        self.graph = graph


def stats(conn, label):
    rows = conn.execute("SELECT id, start_timestamp, end_timestamp, node_ids FROM episodes").fetchall()
    sizes = []
    dup_start = {}
    bad_ref = 0
    for r in rows:
        try:
            nl = json.loads(r["node_ids"] or "[]")
        except Exception:
            nl = []
        sizes.append(len(nl))
        dup_start[r["start_timestamp"]] = dup_start.get(r["start_timestamp"], 0) + 1
        for nid in nl:
            if conn.execute("SELECT 1 FROM nodes WHERE id=?", (nid,)).fetchone() is None:
                bad_ref += 1
                break
    sizes.sort()
    print("[%s] episodes=%d  节点数 min=%s p50=%s p95=%s max=%s  重复start=%d  含悬空节点引用=%d" % (
        label, len(rows),
        sizes[0] if sizes else 0,
        sizes[len(sizes) // 2] if sizes else 0,
        sizes[int(len(sizes) * 0.95)] if sizes else 0,
        sizes[-1] if sizes else 0,
        sum(1 for v in dup_start.values() if v > 1), bad_ref))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["dryrun", "rebuild", "incremental"], default="dryrun")
    ap.add_argument("--db", default=None)
    a = ap.parse_args()

    import config
    import time_window
    from core import GraphStore

    db = a.db or config.DB_PATH
    print("DB:", db, " EPISODE_MAX_NODES =", config.EPISODE_MAX_NODES)
    g = GraphStore(db)
    ms = _MS(g)

    stats(g.conn, "前")

    if a.mode == "dryrun":
        g.conn.close()
        return

    if a.mode == "rebuild":
        g.conn.execute("DELETE FROM episodes")
        g.conn.commit()
    t0 = datetime.now()
    r = time_window.build_episodes(ms, return_stats=True)
    print("重算结果:", r, " 耗时 %.1fs" % (datetime.now() - t0).total_seconds())
    stats(g.conn, "后")
    g.conn.close()


if __name__ == "__main__":
    main()
    print("完成时间:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
