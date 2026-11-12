# -*- coding: utf-8 -*-
"""清理生产库里混入的评测语料节点（2024 年 xiaoyao 的 _20261011_ 批次）。

判定标准（必须同时满足，任一不满足就保留）：
  project='xiaoyao'  AND  timestamp 年份=2024  AND  id LIKE '%20261011%'
2026 年的同批次节点是真实记忆（含 xiaoyao_native_memory / manual / doubao 等多来源、
内容为真实项目记录），不在清理范围。

动作（--apply 时）：导出完整节点行到 JSON（可回滚）→ 删相关边 → 删节点
→ 删引用了这些节点的 episode（随后由 /episodes/build 重算）→ 删 chroma 向量。

用法：
  python scripts/cleanup_bench_nodes.py --mode dryrun
  python scripts/cleanup_bench_nodes.py --mode apply
"""
import argparse
import io
import json
import os
import sqlite3
import sys
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

WHERE = "project='xiaoyao' AND substr(timestamp,1,4)='2024' AND id LIKE '%20261011%'"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["dryrun", "apply"], default="dryrun")
    ap.add_argument("--db", default=os.path.join(ROOT, "storage", "super_memory.db"))
    ap.add_argument("--chroma", default=os.path.join(ROOT, "storage", "chroma_db"))
    ap.add_argument("--collection", default=None)
    a = ap.parse_args()

    conn = sqlite3.connect(a.db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    ids = [r[0] for r in cur.execute("SELECT id FROM nodes WHERE " + WHERE).fetchall()]
    print("命中节点: %d 条" % len(ids))
    if not ids:
        print("没有命中，退出")
        return
    idset = set(ids)

    # 边
    edges = cur.execute("""SELECT id, from_id, to_id, edge_type FROM edges
        WHERE from_id IN (SELECT id FROM nodes WHERE %s)
           OR to_id IN (SELECT id FROM nodes WHERE %s)""" % (WHERE, WHERE)).fetchall()
    print("相关边: %d 条" % len(edges))
    cross = [e for e in edges if (e["from_id"] in idset) != (e["to_id"] in idset)]
    print("  其中跨批次边（一端是真实记忆）: %d 条" % len(cross))

    # 内容抽样（供人工确认）
    print("\n抽查 6 条（确认是构造语料）:")
    for r in cur.execute("SELECT id, timestamp, node_type, source, substr(content,1,60) c FROM nodes WHERE %s ORDER BY timestamp LIMIT 6" % WHERE):
        print("  %s %s [%s/%s] %s" % (r["timestamp"], r["id"], r["node_type"], r["source"], r["c"].replace("\n", " ")))

    # episode 引用
    ep_hit = []
    for r in cur.execute("SELECT id, node_ids FROM episodes").fetchall():
        try:
            nl = json.loads(r["node_ids"] or "[]")
        except Exception:
            continue
        if any(x in idset for x in nl):
            ep_hit.append(r["id"])
    print("\n引用了这些节点的 episode: %d 条" % len(ep_hit))

    if a.mode == "dryrun":
        print("\n[dryrun] 未做任何修改。确认无误后用 --mode apply 执行。")
        conn.close()
        return

    # ---------- apply ----------
    bakdir = os.path.join(ROOT, "storage", "backup", "pre_episodefix_20261113")
    os.makedirs(bakdir, exist_ok=True)
    dump = [dict(r) for r in cur.execute("SELECT * FROM nodes WHERE " + WHERE).fetchall()]
    fpath = os.path.join(bakdir, "deleted_2024_bench_nodes.json")
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False, indent=1)
    print("\n已导出完整节点行 -> %s (%d 条)" % (fpath, len(dump)))

    cur.executemany("DELETE FROM edges WHERE id=?", [(e["id"],) for e in edges])
    print("删除边: %d" % len(edges))

    cur.executemany("DELETE FROM nodes WHERE id=?", [(i,) for i in ids])
    print("删除节点: %d" % len(ids))

    if ep_hit:
        cur.executemany("DELETE FROM episodes WHERE id=?", [(e,) for e in ep_hit])
        print("删除 episode: %d" % len(ep_hit))

    conn.commit()

    left = cur.execute("SELECT COUNT(*) FROM nodes WHERE " + WHERE).fetchone()[0]
    dangling = cur.execute("""SELECT COUNT(*) FROM edges WHERE
        from_id NOT IN (SELECT id FROM nodes) OR to_id NOT IN (SELECT id FROM nodes)""").fetchone()[0]
    total = cur.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    print("剩余命中节点: %d  总节点: %d  悬空边: %d" % (left, total, dangling))
    conn.close()

    # ---------- chroma 向量 ----------
    try:
        import chromadb
        sys.path.insert(0, ROOT)
        import config
        name = a.collection or config.COLLECTION
        cli = chromadb.PersistentClient(path=a.chroma)
        col = cli.get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})
        before = col.count()
        for i in range(0, len(ids), 100):
            col.delete(ids=ids[i:i + 100])
        print("向量删除: 集合 %s 前 %d → 后 %d" % (name, before, col.count()))
    except Exception as e:
        print("向量删除失败（节点已删，可用 /rebuild 重建索引收尾）: %s" % e)


if __name__ == "__main__":
    main()
    print("\n完成时间:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
