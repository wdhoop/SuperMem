# -*- coding: utf-8 -*-
"""
LoCoMo LLM-Judge accuracy：用云端 DeepSeek V4-Flash 当评判模型（官方 LLM-Judge 协议），
对 125B l24 v2（基准）与 125B fullctx（天花板）的 pred/gt 判 0/1，输出 accuracy。
与 judge_locomo.py 完全同协议（语义等价判对、unknown 判错、0/1、reasoning_effort=none）。
"""
import os, json, time, requests
from concurrent.futures import ThreadPoolExecutor

EVAL = r"E:\AIGC_Hub\_refactor\locomo_eval"
CFG = json.load(open(os.path.join(EVAL, "ds_api.json"), encoding="utf-8"))
ENDPOINT = CFG["endpoint"]
API_KEY = CFG["api_key"]
MODEL = CFG["model"]

FILES = [
    ("locomo_results_125b_l24_v2.jsonl", "judge_125b_l24_v2.jsonl", "125B-l24-v2"),
    ("locomo_results_125b_fullctx.jsonl", "judge_125b_fullctx.jsonl", "125B-fullctx"),
]

SYSTEM = (
    "You are an evaluator for a long-conversation memory benchmark. Judge whether the MODEL ANSWER "
    "is CORRECT given the QUESTION and the GROUND TRUTH. Semantic equivalence counts as correct "
    "(e.g., different date formats pointing to the same time, synonyms, paraphrases). If the model "
    "answer is 'unknown' or does not answer the question, it is WRONG. "
    "Reply with exactly one token: 1 (correct) or 0 (wrong)."
)

def judge_one(q, gt, pred, timeout=60):
    user = f"QUESTION: {q}\nGROUND TRUTH: {gt}\nMODEL ANSWER: {pred}\nVERDICT (0/1):"
    payload = {
        "model": MODEL,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": user}],
        "max_tokens": 8,
        "temperature": 0,
        "reasoning_effort": "none",
        "stream": False,
    }
    hdrs = {"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"}
    for attempt in range(5):
        try:
            r = requests.post(ENDPOINT, json=payload, headers=hdrs, timeout=timeout)
            if r.status_code == 429:
                time.sleep(4 * (attempt + 1))
                continue
            r.raise_for_status()
            content = (r.json()["choices"][0]["message"].get("content") or "").strip()
            return "1" if content.startswith("1") else ("0" if content.startswith("0") else f"__BAD__{content[:20]}")
        except Exception as e:
            if attempt == 4:
                return f"__ERR__{type(e).__name__}"
            time.sleep(3 * (attempt + 1))
    return "__ERR__"


def run_file(src, dst, label):
    path_in = os.path.join(EVAL, src)
    path_out = os.path.join(EVAL, dst)
    if not os.path.exists(path_in):
        print(f"[{label}] 缺少输入 {src}，跳过", flush=True)
        return
    recs = [json.loads(l) for l in open(path_in, encoding="utf-8") if l.strip()]
    done = set()
    if os.path.exists(path_out):
        for l in open(path_out, encoding="utf-8"):
            try:
                d = json.loads(l)
                done.add((d["sid"], d["idx"]))
            except Exception:
                pass
    todo = [r for r in recs if (r["sid"], r["idx"]) not in done]
    print(f"[{label}] total={len(recs)} todo={len(todo)} resume={len(done)}", flush=True)
    t0 = time.time()
    n = 0
    with open(path_out, "a", encoding="utf-8") as f:
        def work(r):
            q, gt, pred = r["question"], r["gt"], r["pred"]
            return (r["sid"], r["idx"]), {"sid": r["sid"], "idx": r["idx"], "category": r["category"],
                                          "question": q, "gt": gt, "pred": pred,
                                          "verdict": judge_one(q, gt, pred)}
        with ThreadPoolExecutor(max_workers=8) as ex:
            for _, out in ex.map(work, todo):
                f.write(json.dumps(out, ensure_ascii=False) + "\n")
                f.flush()
                n += 1
                if n % 100 == 0:
                    print(f"  [{label}] +{n} elapsed={time.time()-t0:.0f}s", flush=True)
    rows = [json.loads(l) for l in open(path_out, encoding="utf-8")]
    from collections import defaultdict
    ok = [r for r in rows if r["verdict"] == "1"]
    bad = [r for r in rows if str(r["verdict"]).startswith("__")]
    acc = len(ok) / len(rows) if rows else 0
    unk = [r for r in rows if "unknown" in r["pred"].lower()]
    unk_acc = sum(1 for r in unk if r["verdict"] == "1") / len(unk) if unk else 0
    cat = defaultdict(lambda: [0, 0])
    for r in rows:
        cat[r["category"]][1] += 1
        if r["verdict"] == "1":
            cat[r["category"]][0] += 1
    print(f"[{label}] DONE n={len(rows)} accuracy={acc:.4f} unknown={len(unk)} unk_acc={unk_acc:.4f} err={len(bad)}", flush=True)
    for c, (okc, nc) in sorted(cat.items(), key=lambda x: -x[1][1]):
        print(f"  [{label}] {c:>14} n={nc:>4} acc={okc/nc:.4f}", flush=True)


if __name__ == "__main__":
    for src, dst, label in FILES:
        run_file(src, dst, label)
