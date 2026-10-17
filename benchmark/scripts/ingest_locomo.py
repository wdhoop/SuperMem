# -*- coding: utf-8 -*-
"""
LoCoMo ingest：把 10 段超长对话逐轮写入 10 个独立隔离评测库（零污染）。
每段库 = benchmark/db/<sample_id>/super_memory.db + chroma_db

写入策略：
  - 每轮对话 = 1 个 event 节点（content="Speaker: text"），source/project=sample_id，tags=[speaker, session]
  - 同一轮 text 逐字落原文层（verbatim），node_id 双向指针，session=session_key
  - 无文字但带图片的轮次：content 用 blip_caption（[img] 标注），不落原文层
  - 补建语义边（auto_link, k=2, min_score=0.40），与生产链路一致

embedding 使用 bge-small-en-v1.5（LoCoMo 是英文，覆盖 config 仅限本进程）。

前置：下载 LoCoMo 数据 locomo10.json 放到 benchmark/data/ 下
（数据集来自 Snap Research LoCoMo，见 benchmark/README.md 中的来源链接）。
"""
import os, sys, json, time, shutil

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # SuperMem/
sys.path.insert(0, ROOT)
import config

# [仅本进程] 切英文 embedding，LoCoMo 评测专用；生产库/服务不受影响
config.EMBED_MODEL_NAME = "BAAI/bge-small-en-v1.5"
config.EMBED_MODEL_DIR = os.path.join(config.MODELS_DIR, "bge-small-en-v1.5")

from core import GraphStore, VectorSearch, CausalEngine, MemorySystem, Node
from linker import auto_link

class IsolatedMS(MemorySystem):
    def __init__(self, db_path, chroma_dir):
        self.graph = GraphStore(db_path=db_path)
        self.vector = VectorSearch(chroma_dir=chroma_dir)
        self.causal = CausalEngine(self.graph)

BENCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # benchmark/
DATA = os.path.join(BENCH, "data", "locomo10.json")
DBROOT = os.path.join(BENCH, "db")

def main():
    with open(DATA, encoding="utf-8") as f:
        data = json.load(f)
    print(f"convs: {len(data)}", flush=True)
    os.makedirs(DBROOT, exist_ok=True)
    total_nodes = total_verbatim = 0
    t0 = time.time()
    for ci, sample in enumerate(data):
        sid = sample["sample_id"]
        conv = sample["conversation"]
        spk = set()
        dbdir = os.path.join(DBROOT, sid)
        if os.path.exists(dbdir):
            shutil.rmtree(dbdir)
        os.makedirs(dbdir)
        ms = IsolatedMS(os.path.join(dbdir, "super_memory.db"), os.path.join(dbdir, "chroma_db"))
        n_nodes = n_verb = 0
        # 按 key 顺序遍历 session（session_1_date_time / session_1 ...）
        for key in sorted(conv.keys(), key=lambda k: (0, int(k.split("_")[1])) if k.startswith("session_") and not k.endswith("date_time") else (1, 0)):
            if not (key.startswith("session_") and isinstance(conv[key], list)):
                continue
            sess = key  # e.g. session_1
            for turn in conv[key]:
                spk.add(turn.get("speaker", ""))
                text = (turn.get("text") or "").strip()
                cap = (turn.get("blip_caption") or "").strip()
                if not text and not cap:
                    continue
                content = f"{turn.get('speaker','')}: {text}" if text else f"{turn.get('speaker','')}: [img] {cap}"
                node = Node(content=content, node_type="event",
                                  source=sid, project=sid,
                                  tags=[turn.get("speaker", ""), sess],
                                  timestamp=turn.get("dia_id", sess))
                nid, created = ms.add_node_with_vector(node, dedup=True)
                if created:
                    n_nodes += 1
                    # 补建语义边（与生产链路一致）：新节点挂到相似老节点
                    auto_link(ms, nid, node.content, project=sid, k=2, min_score=0.40)
                if created and text:
                    vids = ms.add_verbatim(text, source=sid, node_id=nid, session=sess, with_vector=True)
                    n_verb += len(vids)
        ms.flush_pending_writes()
        total_nodes += n_nodes
        total_verbatim += n_verb
        print(f"[{ci+1}/{len(data)}] {sid} sessions_speakers={sorted(spk)} nodes={n_nodes} verbatim={n_verb} ({time.time()-t0:.0f}s)", flush=True)
    print(f"DONE total_nodes={total_nodes} total_verbatim={total_verbatim} elapsed={time.time()-t0:.0f}s", flush=True)

if __name__ == "__main__":
    main()
