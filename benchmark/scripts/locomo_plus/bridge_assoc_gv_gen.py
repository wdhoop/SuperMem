# -*- coding: utf-8 -*-
"""
goal/value 强化联想：每样本 +4 条"具体对象化"联想句
=====================================================
- 仅对 goal / value 两类样本
- goal 强化：猜"具体的长期目标是什么"（旅行/买车/买房/求婚/学位/比赛/生意/健康…），
  要求具体到对象和动作
- value 强化：猜"一贯坚持的具体原则"（公平/诚实/独立/家庭优先/体验胜过拥有…），
  并给出过去如何执行的一个具体例子
- 输出 bridge_assoc_gv.jsonl（sid, extra[4]）
"""
import json, time, re, urllib.request, urllib.error

KEY = os.environ.get("DEEPSEEK_API_KEY", "")
BASE = "https://api.deepseek.com/v1"
MODEL = "deepseek-chat"
PLUS = os.environ.get("LOCOMO_PLUS_JSON", os.path.join(PLUS_DATA, "locomo_plus.json"))
OUT = os.path.join(EVAL_ROOT, "bridge_assoc_gv.jsonl")

GOAL_PROMPT = (
    "You are helping an AI memory system retrieve past memories. "
    "A person (A) says something now that is a small side-effect of a LONG-TERM GOAL they have been working toward for months. "
    "Your job: guess WHAT that specific long-term goal concretely is. "
    "Do NOT stay on the surface (e.g. don't just say 'they save money' — say what they are saving FOR: "
    "a trip, a car, a house, a ring, a degree, a competition, a business, a health goal, a big event...).\n"
    "Write 4 first-person hypothetical sentences A would have said in the past, each naming a SPECIFIC goal object/plan "
    "(include concrete things like a country, an item, a person's name if guessable, a deadline).\n\n"
    "Current statement from A:\n{trigger}\n\n"
    "Output strictly as a JSON array of 4 strings, nothing before or after."
)

VALUE_PROMPT = (
    "You are helping an AI memory system retrieve past memories. "
    "A person (A) says something now that reflects a CORE VALUE / PRINCIPLE they have held and acted on consistently for a long time "
    "(fairness, honesty, independence, family-first, experiences over possessions, standing up to injustice, etc.). "
    "Your job: guess WHICH specific principle it is, and give a concrete past example of A acting on it.\n"
    "Write 4 first-person hypothetical sentences A would have said in the past. "
    "Each must (1) name the principle explicitly AND (2) describe one specific past situation where A acted on it "
    "(a real event: a person, a place, a decision).\n\n"
    "Current statement from A:\n{trigger}\n\n"
    "Output strictly as a JSON array of 4 strings, nothing before or after."
)


def call(prompt, timeout=180):
    payload = {"model": MODEL, "messages": [{"role": "user", "content": prompt}],
               "max_tokens": 1200, "temperature": 0.9, "stream": False}
    req = urllib.request.Request(BASE + "/chat/completions", data=json.dumps(payload).encode("utf-8"),
                                 method="POST", headers={"Content-Type": "application/json",
                                                         "Authorization": "Bearer " + KEY})
    last_err = ""
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
                content = (data["choices"][0]["message"].get("content") or "").strip()
                if content:
                    return content
                last_err = "empty_content"
                time.sleep(8 * (attempt + 1))
        except urllib.error.HTTPError as e:
            last_err = "HTTP_%s" % e.code
            time.sleep(12 * (attempt + 1))
        except Exception as e:
            last_err = str(e)[:120]
            time.sleep(10 * (attempt + 1))
    return "__FAIL__" + last_err


def parse_assoc(raw):
    raw = (raw or "").strip()
    try:
        i, j = raw.find("["), raw.rfind("]")
        if i >= 0 and j > i:
            arr = json.loads(raw[i:j + 1])
            if isinstance(arr, list):
                return [str(x).strip() for x in arr if str(x).strip()][:4]
    except Exception:
        pass
    strs = re.findall(r'"((?:[^"\\]|\\.)*)"', raw)
    return [s.strip() for s in strs if s.strip()][:4]


def main():
    plus_list = json.load(open(PLUS, encoding="utf-8"))
    targets = [(i, x) for i, x in enumerate(plus_list) if x.get("relation_type") in ("goal", "value")]
    done_sids = set()
    import os
    if os.path.exists(OUT):
        for l in open(OUT, encoding="utf-8"):
            try:
                done_sids.add(json.loads(l)["sid"])
            except Exception:
                pass

    t0 = time.time()
    with open(OUT, "a", encoding="utf-8") as f:
        for idx, item in targets:
            sid = "plus_%04d" % idx
            if sid in done_sids:
                continue
            trig = item.get("trigger_query") or item.get("trigger") or ""
            rel = item.get("relation_type", "?")
            prompt = GOAL_PROMPT if rel == "goal" else VALUE_PROMPT
            extra = []
            raw = ""
            for rnd in range(3):
                raw = call(prompt.format(trigger=trig))
                extra = parse_assoc(raw)
                if len(extra) >= 4:
                    break
                time.sleep(2)
            rec = {"sid": sid, "idx": idx, "relation_type": rel, "trigger": trig,
                   "extra": extra[:4], "raw": raw[:200]}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            print("[%s] %s extra=%d elapsed=%.0fs" % (sid, rel, len(extra), time.time() - t0), flush=True)
    print("DONE -> " + OUT, flush=True)


if __name__ == "__main__":
    main()
