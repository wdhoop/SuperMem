# -*- coding: utf-8 -*-
"""补判全量 judge 中的 parse_fail/err 样本（重跑 judge 直到解析出 label）"""
import os, json, time, re, urllib.request, urllib.error

KEY = os.environ.get("DEEPSEEK_API_KEY", "")
BASE = "https://api.deepseek.com/v1"
MODEL = "deepseek-flash"
SRC = os.path.join(EVAL_ROOT, "bridge_judged_v4_full_chat.jsonl")

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
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
                return (data["choices"][0]["message"].get("content") or "").strip()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(8 * (attempt + 1))
                continue
            return f"__ERR_HTTP_{e.code}__"
        except Exception as e:
            if attempt == 4:
                return f"__ERR__{e}"
            time.sleep(6 * (attempt + 1))
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


rows = [json.loads(l) for l in open(SRC, encoding="utf-8")]
bad = [r for r in rows if r["judge_label"] == "parse_fail" or str(r["judge_label"]).startswith("__")]
print("parse_fail/err count: %d" % len(bad), flush=True)
for b in bad:
    print("  %s label=%s" % (b["sid"], b["judge_label"]), flush=True)

fixed = 0
for b in bad:
    for rnd in range(4):
        raw = call_judge(b.get("evidence", ""), b.get("pred", ""))
        label = parse_label(raw)
        if label in ("correct", "wrong"):
            b["judge_label"] = label
            b["judge_score"] = 1.0 if label == "correct" else 0.0
            b["judge_raw"] = raw[:300]
            fixed += 1
            print("  FIX %s -> %s" % (b["sid"], label), flush=True)
            break
        time.sleep(3)

with open(SRC, "w", encoding="utf-8") as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

all_rows = [json.loads(l) for l in open(SRC, encoding="utf-8")]
n = len(all_rows)
ok = sum(1 for x in all_rows if x["judge_label"] == "correct")
remain_fail = sum(1 for x in all_rows if x["judge_label"] == "parse_fail" or str(x["judge_label"]).startswith("__"))
print("=" * 50)
print("FINAL score: %d/%d = %.2f%% (fixed=%d, remain_fail=%d)" % (ok, n, 100.0 * ok / n, fixed, remain_fail))
by_rel = {}
for x in all_rows:
    by_rel.setdefault(x["relation_type"], [0, 0])
    by_rel[x["relation_type"]][1] += 1
    if x["judge_label"] == "correct":
        by_rel[x["relation_type"]][0] += 1
for k, (a, b) in sorted(by_rel.items()):
    print("  %s: %d/%d" % (k, a, b))

