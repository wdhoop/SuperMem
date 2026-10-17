# -*- coding: utf-8 -*-
"""
超级记忆系统 —— 向量索引全量重建 scripts/rebuild_vector_index.py
分批重建；排除 archived/obsolete/merged 节点；--dry-run 预演；可作异步后台任务。
用法：
    python scripts/rebuild_vector_index.py [--dry-run]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from core import MemorySystem  # noqa: E402


def rebuild(ms, dry_run=False):
    nodes = ms.graph.list_nodes(limit=100000)
    active = [n for n in nodes if n.status not in ("obsolete", "merged")]
    print(f"[rebuild] 共 {len(nodes)} 节点，将重建 {len(active)} 条（排除 {len(nodes) - len(active)} 条沉睡/合并）")
    if dry_run:
        print("[rebuild] 预演结束（未改动）")
        return {"dry_run": True, "total": len(nodes), "to_rebuild": len(active)}

    # 重置 collection（幂等）
    col = ms.vector._collection()  # 触发 chroma client 惰性初始化
    client = ms.vector._client
    try:
        client.delete_collection(config.COLLECTION)
    except Exception:
        pass
    ms.vector._col = client.get_or_create_collection(
        name=config.COLLECTION, metadata={"hnsw:space": "cosine"})

    # 分批 upsert
    BATCH = config.EMBED_BATCH
    done = 0
    for i in range(0, len(active), BATCH):
        chunk = active[i:i + BATCH]
        texts = [n.content for n in chunk]
        embs = ms.vector.embed_texts(texts)
        ids = [n.id for n in chunk]
        metas = [{"node_type": n.node_type, "project": n.project,
                  "timestamp": n.timestamp, "status": n.status} for n in chunk]
        ms.vector._col.upsert(ids=ids, embeddings=embs.tolist(),
                              documents=texts, metadatas=metas)
        done += len(chunk)
        print(f"[rebuild] {done}/{len(active)}")
    print(f"[rebuild] 完成：{done} 条向量")
    ms.graph.set_meta("last_rebuild_at", __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return {"dry_run": False, "rebuilt": done}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="预演，不实际改动")
    args = ap.parse_args()
    ms = MemorySystem()
    rebuild(ms, dry_run=args.dry_run)
