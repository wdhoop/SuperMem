# -*- coding: utf-8 -*-
"""
LoCoMo QA 评测 · 125B limit=24（Strata 引擎 + Qwen3.8-Flash-Next）
与 27B l24 完全相同的数据/检索/prompt 配置，仅把 answerer 换成 Strata 125B + limit 8→24。
目的：最终测试——供给量 24 下 125B 的终版成绩（与 9B/27B 同口径对比）。
结果：benchmark/results/locomo_results_125b_l24.jsonl

前置：
  1) 运行 ingest_locomo.py 建好隔离库
  2) 本机 Strata 引擎已启动并暴露 OpenAI 兼容端点（默认 http://127.0.0.1:8080/v1），
     模型名按本地实际部署档位填写（如 qwen3.8-flash-next-iq3_s）
  3) 设置环境变量 STRATA_API_KEY 为你的本地引擎访问凭证
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
OUT = os.path.join(BENCH, "results", "locomo_results_125b_l24.jsonl")
ENDPOINT = os.environ.get("STRATA_ENDPOINT", "http://127.0.0.1:8080/v1/chat/completions")
API_KEY = os.environ.get("STRATA_API_KEY", "")   # 本地 Strata 引擎凭证（必填）
MODEL = os.environ.get("STRATA_MODEL", "qwen3.8-flash-next-iq3_s")  # 按本地部署档位填写

SYSTEM_PROMPT = (
    "You are answering questions about a long conversation between two people. "
    "Below are memory snippets retrieved from that conversation. The correct answer "
    "ALWAYS exists somewhere in the conversation, and the snippets usually point to it. "
    "Reason step by step using the snippets (they may be scattered across different times). "
    "Give the most likely concise answer: a date, name, number, or short phrase. "
    "Only reply 'unknown' if the snippets give you absolutely nothing to work with."
)

def llm_chat(system, user, timeout=300):
    payload = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": 128,
        "temperature": 0,
        "stream": False,
    }
    hdrs = {"Content-Type": "application/json"}
    if API_KEY:
        hdrs["Authorization"] = f"Bearer {API_KEY}"
    for attempt in range(3):
        try:
            r = requests.post(ENDPOINT, json=payload, headers=hdrs, timeout=timeout)
            r.raise_for_status()
            return (r.json()["choices"][0]["message"].get("content") or "").strip()
        except Exception as e:
            if attempt == 2:
                return f"__LLM_ERR__{e}"
            time.sleep(5 * (attempt + 1))
    return ""

def build_context(res):
    parts = []
    for i, it in enumerate(res.get("results", [])[:24]):
        c = (it.get("node") or {}).get("content", "")
        ts = (it.get("node") or {}).get("timestamp", "")
        if c:
            parts.append(f"[memory {i+1}] (t={ts}) {c}")
    for i, r in enumerate(res.get("raw", [])[:24]):
        t = r.get("text", "")
        ts = r.get("ts", "")
        if t:
            parts.append(f"[verbatim {i+1}] (t={ts}) {t}")
    return "\n".join(parts)

def main():
    if not API_KEY:
        print("警告：未设置 STRATA_API_KEY 环境变量，将尝试无鉴权访问", flush=True)
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
                    res = search_strong(ms, q, limit=24, project=sid)
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
