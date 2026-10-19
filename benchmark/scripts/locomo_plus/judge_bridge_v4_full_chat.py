# -*- coding: utf-8 -*-
"""全量 401 judge：DeepSeek judge bridge_results_v4_full_chat.jsonl（断点续写）"""
import os, json, time, re, urllib.request, urllib.error

KEY = os.environ.get("DEEPSEEK_API_KEY", "")
BASE = "https://api.deepseek.com/v1"
MODEL = "deepseek-flash"
SRC = os.path.join(EVAL_ROOT, "bridge_results_v4_full_chat.jsonl")
OUT = os.path.join(EVAL_ROOT, "bridge_judged_v4_full_chat.jsonl")

JUDGE_SYSTEM = (
    "You are a Memory Awareness Judge.\n"
    "Your task: Judge whether the Model Prediction considers or is linked to the Evidence. "
    "If there is a clear connection, the answer is correct (score 1); if not, it is wrong (no score).\n\n"
    "Labels:\n"
    "- \"correct\": The prediction explicitly or implicitly reflects/uses the evidence (memory or constraint). Give 1 point.\n"
    "- \"wrong\": The prediction does not show such a link to the evidence. No point.\n\n"
    "Memory/Evidence:\n{evidence}\n\n"
    "Model Prediction:\n{pred}\n\n"
    'Return your judgment strictly in JSON format:\n'
    '{{"label": "correct"|"wrong", "reason": "<Does the prediction relate to the evidence?>"}}'
)


def call_judge(evidence, pred, timeout=120):
    user = JUDGE_SYSTEM.format(evidence=evidence or "", pred=pred or "")
    payload = {"model": MODEL,
               "messages": [{"role": "user", "content": user}],
               "max_tokens": 800, "temperature": 0, "stream": False}
    req = urllib.request.Request(BASE + "/chat/completions", data=json.dumps(payload).encode("utf-8"),
                                 method="POST", headers={"Content-Type": "application/json",
                                                         "Authorization": "Bearer " + KEY})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
                return (data["choices"][0]["message"].get("content") or "").strip()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(6 * (attempt + 1))
                continue
            return f"__ERR_HTTP_{e.code}__"
        except Exception as e:
            if attempt == 3:
                return f"__ERR__{e}"
            time.sleep(5 * (attempt + 1))
    return "__ERR__"


def parse_label(raw):
    raw = (raw or "").strip()
    try:
        m = re.search(r'"label"\s*:\s*"([^"]+)"', raw)
        if m:
            return m.group(1).strip().lower()
    except Exception:
        pass
    if "correct" in raw.lower():
        return "correct"
    if "wrong" in raw.lower():
        return "wrong"
    return "parse_fail"


def main():
    rows = [json.loads(l) for l in open(SRC, encoding="utf-8")]
    done_sids = set()
    if os.path.exists(OUT):
        for l in open(OUT, encoding="utf-8"):
            try:
                done_sids.add(json.loads(l)["sid"])
            except Exception:
                pass

    total = len(rows)
    done = 0
    t0 = time.time()
    with open(OUT, "a", encoding="utf-8") as f:
        for r in rows:
            sid = r["sid"]
            if sid in done_sids:
                done += 1
                continue
            raw = call_judge(r.get("evidence", ""), r.get("pred", ""))
            label = parse_label(raw)
            score = 1.0 if label == "correct" else 0.0
            rec = dict(r)
            rec["judge_label"] = label
            rec["judge_score"] = score
            rec["judge_raw"] = raw[:300]
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            done += 1
            if done % 20 == 0:
                print("[%d/%d] %s label=%s elapsed=%.0fs" % (
                    done, total, sid, label, time.time() - t0), flush=True)
            if done % 3 == 0:
                time.sleep(0.5)
    # 汇总
    all_rows = [json.loads(l) for l in open(OUT, encoding="utf-8")]
    n = len(all_rows)
    ok = sum(1 for x in all_rows if x["judge_label"] == "correct")
    fail = sum(1 for x in all_rows if x["judge_label"] == "parse_fail" or str(x["judge_label"]).startswith("__"))
    print("=" * 50)
    print("FULL Cognitive V4 score: %d/%d = %.2f%%" % (ok, n, 100.0 * ok / n))
    print("parse_fail/err: %d" % fail)
    by_rel = {}
    for x in all_rows:
        by_rel.setdefault(x["relation_type"], [0, 0])
        by_rel[x["relation_type"]][1] += 1
        if x["judge_label"] == "correct":
            by_rel[x["relation_type"]][0] += 1
    for k, (a, b) in sorted(by_rel.items()):
        print("  %s: %d/%d" % (k, a, b))
    print("-> " + OUT)


if __name__ == "__main__":
    main()

