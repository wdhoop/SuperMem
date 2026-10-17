# -*- coding: utf-8 -*-
"""
LoCoMo QA 评测 27B limit=24 版：同库同配置，检索量从 8+8 扩到 24+24。
验证供给量曲线（证据命中 +12~16pp）→ F1 的真实传导。

前置：
  1) 运行 ingest_locomo.py 建好隔离库
  2) 本机 Ollama 已拉取 qwen3.8-27b-fast 并监听 http://127.0.0.1:11434
结果输出：benchmark/results/locomo_results_27b_l24.jsonl（断点续跑）
"""
import os, sys, json, time, requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # SuperMem/
sys.path.insert(0, ROOT)
import config
config.EMBED_MODEL_NAME = "BAAI/bge-small-en-v1.5"
config.EMBED_MODEL_DIR = os.path.join(config.MODELS_DIR, "bge-small-en-v1.5")
config.VERBATIM_STRONG_TOP_K = 24

from core import GraphStore, VectorSearch, CausalEngine, MemorySystem
from search_fusion import search_strong

class IsolatedMS(MemorySystem):
    def __init__(self, db_path, chroma_dir):
        self.graph = GraphStore(db_path=db_path)
        self.vector = VectorSearch(chroma_dir=chroma_dir)
        self.causal = CausalEngine(self.graph)

BENCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # benchmark/
DATA = os.path.join(BENCH, "data", "locomo10.json")
DBROOT = os.path.join(BENCH, "db")
OUT = os.path.join(BENCH, "results", "locomo_results_27b_l24.jsonl")
OLLAMA = os.environ.get("OLLAMA_ENDPOINT", "http://127.0.0.1:11434/api/chat")
MODEL = "qwen3.8-27b-fast"
LIMIT = 24

SYSTEM_PROMPT = (
    "You are answering questions about a long conversation between two people. "
    "Below are memory snippets retrieved from that conversation. The correct answer "
    "ALWAYS exists somewhere in the conversation, and the snippets usually point to it. "
    "Reason step by step using the snippets (they may be scattered across different times). "
    "Give the most likely concise answer: a date, name, number, or short phrase. "
    "Only reply 'unknown' if the snippets give you absolutely nothing to work with."
)

def llm_chat(system, user, timeout=240):
    payload = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "stream": False,
        "options": {"num_predict": 128, "temperature": 0, "top_p": 0.9},
        "think": False,
    }
    for attempt in range(3):
        try:
            r = requests.post(OLLAMA, json=payload, timeout=timeout)
            r.raise_for_status()
            msg = r.json().get("message", {}).get("content", "")
            if "<think>" in msg and "</think>" in msg:
                msg = msg.split("</think>", 1)[1].strip()
            return msg.strip()
        except Exception as e:
            if attempt == 2:
                return f"__LLM_ERR__{e}"
            time.sleep(5 * (attempt + 1))
    return ""

def build_context(res):
    parts = []
    for i, it in enumerate(res.get("results", [])[:LIMIT]):
        c = (it.get("node") or {}).get("content", "")
        ts = (it.get("node") or {}).get("timestamp", "")
        if c:
            parts.append(f"[memory {i+1}] (t={ts}) {c}")
    for i, r in enumerate(res.get("raw", [])[:LIMIT]):
        t = r.get("text", "")
        ts = r.get("ts", "")
        if t:
            parts.append(f"[verbatim {i+1}] (t={ts}) {t}")
    return "\n".join(parts)

def main():
    with open(DATA, encoding="utf-8") as f:
        data = json.load(f)
    done = set()
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    done.add((rec["sid"], rec["idx"]))
                except Exception:
                    pass
    print(f"resume: {len(done)} done", flush=True)
    CAT_NAMES = {1: "single-hop", 2: "temporal", 3: "commonsense", 4: "multi-hop", 5: "adversarial"}
    t0 = time.time()
    total = 0
    with open(OUT, "a", encoding="utf-8") as f:
        for sample in data:
            sid = sample["sample_id"]
            dbdir = os.path.join(DBROOT, sid)
            ms = IsolatedMS(os.path.join(dbdir, "super_memory.db"), os.path.join(dbdir, "chroma_db"))
            for idx, qa in enumerate(sample["qa"]):
                if (sid, idx) in done:
                    continue
                q = qa["question"]
                gt = qa.get("answer") or qa.get("adversarial_answer") or ""
                cat = qa["category"]
                try:
                    res = search_strong(ms, q, limit=LIMIT, project=sid)
                except Exception:
                    res = {"mode": "err", "results": [], "raw": []}
                ctx = build_context(res)
                user = f"Question: {q}\n\nMemory snippets:\n{ctx}\n\nAnswer:"
                pred = llm_chat(SYSTEM_PROMPT, user)
                rec = {
                    "sid": sid, "idx": idx, "category": CAT_NAMES.get(cat, str(cat)), "cat_id": cat,
                    "question": q, "gt": gt, "pred": pred,
                    "mode": res.get("mode", "?"),
                    "n_nodes": len(res.get("results", [])),
                    "n_raw": len(res.get("raw", [])),
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                done.add((sid, idx))
                total += 1
                if total % 20 == 0:
                    print(f"  +{total} elapsed={time.time()-t0:.0f}s last_mode={rec['mode']}", flush=True)
    print(f"DONE new={total} total_done={len(done)} elapsed={time.time()-t0:.0f}s", flush=True)

if __name__ == "__main__":
    main()
