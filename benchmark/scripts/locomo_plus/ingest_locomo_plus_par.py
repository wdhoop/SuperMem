# -*- coding: utf-8 -*-
"""
LoCoMo-Plus ingest 并行版（生产版完整架构，隔离评测库）
=========================================================
复用 ingest_locomo_plus.py 的写入逻辑，multiprocessing 满负荷并行建库。

- 16 worker 进程（每进程 torch 单线程，跑满 20 逻辑核）
- 每样本独立隔离库 db_plus/plus_XXXX/
- 跳过已存在的库（已验证的 plus_0000~0002 不动）
- 单样本失败记录 failed.txt，不中断整体
"""
import os
import sys
import json
import time
import shutil
import multiprocessing as mp

MS_ROOT = os.environ.get("SUPERMEM_ROOT", os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")))
EVAL_ROOT = os.environ.get("EVAL_ROOT", os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "results", "locomo_plus")))
PLUS_DATA = os.environ.get("LOCOMO_PLUS_DATA", os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "locomo_plus_data")))
DBROOT = os.path.join(EVAL_ROOT, "db_plus")
WORKERS = 16


def _init_worker(plus_list, locomo_list):
    """每个 worker 进程初始化：数据入全局 + 环境路径 + 单线程 torch（避免线程争抢）"""
    globals()["_PLUS"] = plus_list
    globals()["_LOCOMO"] = locomo_list
    sys.path.insert(0, MS_ROOT)
    sys.path.insert(0, PLUS_DATA)
    import config  # noqa
    config.EMBED_MODEL_NAME = "BAAI/bge-small-en-v1.5"
    config.EMBED_MODEL_DIR = os.path.join(config.MODELS_DIR, "bge-small-en-v1.5")
    try:
        import torch
        torch.set_num_threads(1)
    except Exception:
        pass


def build_one(idx):
    from core import GraphStore, VectorSearch, CausalEngine, MemorySystem, Node  # noqa
    from linker import auto_link  # noqa
    from build_conv import build_context  # noqa

    plus = _PLUS[idx]
    locomo_item = _LOCOMO[idx % len(_LOCOMO)]
    sid = f"plus_{idx:04d}"
    dbdir = os.path.join(DBROOT, sid)

    # 已建则跳过（保留验证库）
    if os.path.exists(os.path.join(dbdir, "super_memory.db")):
        return (idx, 0, 0, "skip", "")

    class IsolatedMS(MemorySystem):
        def __init__(self, db_path, chroma_dir):
            self.graph = GraphStore(db_path=db_path)
            self.vector = VectorSearch(chroma_dir=chroma_dir)
            self.causal = CausalEngine(self.graph)

    try:
        ctx = build_context(plus, locomo_item)
        dialogue = ctx["dialogue"]
        query_len = len(ctx["query_turns"])
        turns = dialogue[:len(dialogue) - query_len] if query_len else dialogue

        if os.path.exists(dbdir):
            shutil.rmtree(dbdir)
        os.makedirs(dbdir)

        ms = IsolatedMS(os.path.join(dbdir, "super_memory.db"),
                        os.path.join(dbdir, "chroma_db"))
        n_nodes = n_verb = 0
        for ti, turn in enumerate(turns):
            text = (turn.get("text") or "").strip()
            spk = (turn.get("speaker") or "").strip()
            if not text:
                continue
            content = f"{spk}: {text}"
            node = Node(content=content, node_type="event", source=sid, project=sid,
                        tags=[spk, "plus"], timestamp=str(ti))
            nid, created = ms.add_node_with_vector(node, dedup=True)
            if created:
                auto_link(ms, nid, node.content, project=sid, k=2, min_score=0.40)
                vids = ms.add_verbatim(text, source=sid, node_id=nid,
                                       session="plus", with_vector=True)
                n_verb += len(vids)
                n_nodes += 1
        ms.flush_pending_writes()
        return (idx, n_nodes, n_verb, "ok", "")
    except Exception as e:
        return (idx, 0, 0, "fail", repr(e))


def main():
    with open(os.path.join(PLUS_DATA, "locomo_plus.json"), encoding="utf-8") as f:
        plus_list = json.load(f)
    with open(os.path.join(PLUS_DATA, "locomo10.json"), encoding="utf-8") as f:
        locomo_list = json.load(f)

    os.makedirs(DBROOT, exist_ok=True)
    N = len(plus_list)
    print(f"total samples: {N}, workers: {WORKERS}, dbroot: {DBROOT}", flush=True)

    t0 = time.time()
    total_nodes = total_verb = 0
    done = skipped = failed = 0
    failed_list = []

    ctx = mp.get_context("spawn")  # spawn 更稳（Windows 上避免 fork 拷贝问题）
    with ctx.Pool(WORKERS, initializer=_init_worker, initargs=(plus_list, locomo_list)) as pool:
        for idx, n_nodes, n_verb, status, err in pool.imap_unordered(build_one, range(N), chunksize=1):
            if status == "ok":
                done += 1
                total_nodes += n_nodes
                total_verb += n_verb
            elif status == "skip":
                skipped += 1
            else:
                failed += 1
                failed_list.append((idx, err))
            if (done + skipped + failed) % 25 == 0:
                print(f"[{(done+skipped+failed)}/{N}] ok={done} skip={skipped} fail={failed} "
                      f"nodes={total_nodes} verb={total_verb} elapsed={time.time()-t0:.0f}s", flush=True)

    elapsed = time.time() - t0
    print(f"DONE ok={done} skip={skipped} fail={failed} nodes={total_nodes} "
          f"verbatim={total_verb} elapsed={elapsed:.0f}s", flush=True)
    if failed_list:
        with open(os.path.join(EVAL_ROOT, "failed.txt"), "w", encoding="utf-8") as f:
            for idx, err in failed_list:
                f.write(f"plus_{idx:04d}\t{err}\n")
        print(f"failed list -> {EVAL_ROOT}/failed.txt", flush=True)


if __name__ == "__main__":
    main()
