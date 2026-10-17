# -*- coding: utf-8 -*-
"""
LoCoMo LLM-Judge accuracy：用 DeepSeek（或其他 OpenAI 兼容 API）当评判模型，
按官方 LLM-Judge 协议对评测结果的 pred/gt 判 0/1，输出 accuracy（总体+分类别+unknown 子集）。

官方协议要点：语义等价判对；'unknown' 一律判错；只输出 0/1。

前置（环境变量）：
  DEEPSEEK_API_KEY    必填
  DEEPSEEK_MODEL      默认 deepseek-v4-flash
  DEEPSEEK_ENDPOINT   默认 https://api.deepseek.com/v1/chat/completions

输入：benchmark/results/locomo_results_{125b,27b,9b}_l24.jsonl
输出：benchmark/results/judge_{name}.jsonl（断点续跑）
"""
import os, json, time, requests
from concurrent.futures import ThreadPoolExecutor

BENCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # benchmark/
RESULTS = os.path.join(BENCH, "results")

ENDPOINT = os.environ.get("DEEPSEEK_ENDPOINT", "https://api.deepseek.com/v1/chat/completions")
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")

FILES = [
    ("locomo_results_125b_l24.jsonl", "judge_125b_l24.jsonl", "125B"),
    ("locomo_results_27b_l24.jsonl",  "judge_27b_l24.jsonl",  "27B"),
    ("locomo_results_9b_l24.jsonl",   "judge_9b_l24.jsonl",   "9B"),
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
    path_in = os.path.join(RESULTS, src)
    path_out = os.path.join(RESULTS, dst)
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
                if n % 50 == 0:
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
    if not API_KEY:
        print("错误：未设置 DEEPSEEK_API_KEY 环境变量", flush=True)
        raise SystemExit(1)
    for src, dst, label in FILES:
        if os.path.exists(os.path.join(RESULTS, src)):
            run_file(src, dst, label)
        else:
            print(f"[{label}] 缺少输入 {src}，跳过", flush=True)
