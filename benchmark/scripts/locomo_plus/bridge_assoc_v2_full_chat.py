import os
# -*- coding: utf-8 -*-
"""
全量 401 深度联想生成（断点续写）
===================================
- 读 locomo_plus.json 全部 401 条 → 每条 8 条深度联想（四角度）
- 断点续写：已存在的 sid 跳过；每完成 1 条即落盘
- 输出 bridge_assoc_v2_full_chat.jsonl
"""
import json, os, os, time, re, urllib.request, urllib.error

KEY = os.environ.get("DEEPSEEK_API_KEY", "")
BASE = "https://api.deepseek.com/v1"
MODEL = "deepseek-chat"
PLUS = os.environ.get("LOCOMO_PLUS_JSON", os.path.join(PLUS_DATA, "locomo_plus.json"))
OUT = os.path.join(EVAL_ROOT, "bridge_assoc_v2_full_chat.jsonl")

PROMPT = (
    "You are helping an AI memory system retrieve relevant past memories. "
    "A person (A) is now saying something in a conversation. "
    "Based ONLY on what A says now, imagine what A might have mentioned in PAST conversations "
    "(weeks or months ago) that relates to the current statement.\n"
    "IMPORTANT: do NOT stay on the literal surface of the current statement. Dig one layer deeper "
    "to what could explain it. Think across these four angles, and write 2 hypothetical "
    "first-person past sentences for EACH angle (8 total):\n"
    "1) Past cause / trigger event: an earlier incident or condition that changed A's behavior.\n"
    "2) Persistent physical or emotional state: something A lives with or feels over time.\n"
    "3) Long-term goal / plan: something A is working toward that shapes current choices.\n"
    "4) Core value / principle: a belief A holds and acts on consistently.\n\n"
    "Each sentence must be first-person (\"I ...\"), specific and concrete, "
    "exactly like something A would have actually said before. "
    "Output strictly as a JSON array of 8 strings, nothing before or after.\n\n"
    "Current statement from A:\n{trigger}"
)


def call(prompt, timeout=180):
    payload = {"model": MODEL, "messages": [{"role": "user", "content": prompt}],
               "max_tokens": 1200, "temperature": 0.9, "stream": False}
    req = urllib.request.Request(BASE + "/chat/completions", data=json.dumps(payload).encode("utf-8"),
                                 method="POST", headers={"Content-Type": "application/json",
                                                         "Authorization": "Bearer " + KEY})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
                return (data["choices"][0]["message"].get("content") or "").strip()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(12 * (attempt + 1))
                continue
            return ""
        except Exception:
            time.sleep(10 * (attempt + 1))
    return ""


def parse_assoc(raw):
    raw = (raw or "").strip()
    try:
        i, j = raw.find("["), raw.rfind("]")
        if i >= 0 and j > i:
            arr = json.loads(raw[i:j + 1])
            if isinstance(arr, list):
                return [str(x).strip() for x in arr if str(x).strip()][:8]
    except Exception:
        pass
    strs = re.findall(r'"((?:[^"\\]|\\.)*)"', raw)
    return [s.strip() for s in strs if s.strip()][:8]


def main():
    plus_list = json.load(open(PLUS, encoding="utf-8"))
    done_sids = set()
    if os.path.exists(OUT):
        for l in open(OUT, encoding="utf-8"):
            try:
                done_sids.add(json.loads(l)["sid"])
            except Exception:
                pass

    total = len(plus_list)
    done = 0
    t0 = time.time()
    with open(OUT, "a", encoding="utf-8") as f:
        for idx, item in enumerate(plus_list):
            sid = "plus_%04d" % idx
            if sid in done_sids:
                done += 1
                continue
            trig = item.get("trigger_query") or item.get("trigger") or ""
            rel = item.get("relation_type", "?")
            # 重试直到 8 条
            assoc = []
            raw = ""
            for rnd in range(5):
                raw = call(PROMPT.format(trigger=trig))
                assoc = parse_assoc(raw)
                if len(assoc) >= 8:
                    break
                time.sleep(4)
            rec = {"sid": sid, "idx": idx, "relation_type": rel, "trigger": trig,
                   "assoc": assoc[:8], "raw": raw[:200]}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            done += 1
            if done % 20 == 0 or len(assoc) < 8:
                print("[%d/%d] %s rel=%s assoc=%d elapsed=%.0fs" % (
                    done, total, sid, rel, len(assoc), time.time() - t0), flush=True)
    n8 = sum(1 for l in open(OUT, encoding="utf-8") if len(json.loads(l)["assoc"]) >= 8)
    print("DONE total=%d n8=%d -> %s" % (done, n8, OUT), flush=True)


if __name__ == "__main__":
    main()

