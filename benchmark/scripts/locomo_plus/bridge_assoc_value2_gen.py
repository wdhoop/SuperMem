import os
# -*- coding: utf-8 -*-
"""
value 专项强化 2.0：每 value 样本 +4 条"原则冲突/来源"联想
============================================================
- 仅 value 100 样本
- 方向：不重复"原则+例子"（gv 已有），聚焦：
  1) 原则冲突/牺牲时刻（A 曾为坚持原则付出代价/拒绝诱惑）
  2) 原则来源（童年/家庭/经历塑造该信念的故事）
  3) 原则的边界（A 曾明确说过什么情况不妥协/什么情况破例）
  4) 对他人输出原则的时刻（A 曾教/劝/纠正别人）
- 输出 bridge_assoc_value2.jsonl（sid, extra2[4]）
"""
import json, os, time, re, urllib.request, urllib.error, os

KEY = os.environ.get("DEEPSEEK_API_KEY", "")
BASE = "https://api.deepseek.com/v1"
MODEL = "deepseek-chat"
PLUS = os.environ.get("LOCOMO_PLUS_JSON", os.path.join(PLUS_DATA, "locomo_plus.json"))
OUT = os.path.join(EVAL_ROOT, "bridge_assoc_value2.jsonl")

VALUE2_PROMPT = (
    "You are helping an AI memory system retrieve past memories. "
    "A person (A) says something now that reflects a CORE VALUE they have held and acted on for a long time. "
    "The hidden memory is usually a PAST MOMENT where this value was tested or formed, not just stated.\n"
    "Write 4 first-person hypothetical sentences A would have said in the past, covering these angles:\n"
    "1) A moment A sacrificed or paid a price to stick to this value (refused an easy path, lost something, said no).\n"
    "2) The origin of this value: a childhood/family/life story that shaped it (a person who taught it, an event).\n"
    "3) The boundary of this value: something A explicitly said they would NEVER compromise on, or an exception they allow.\n"
    "4) A moment A taught/advised/corrected someone else about this value.\n"
    "Each sentence must be first-person, specific and concrete (a person, a place, a decision, a consequence).\n\n"
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
    targets = [(i, x) for i, x in enumerate(plus_list) if x.get("relation_type") == "value"]
    done_sids = set()
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
            extra2 = []
            raw = ""
            for rnd in range(3):
                raw = call(VALUE2_PROMPT.format(trigger=trig))
                extra2 = parse_assoc(raw)
                if len(extra2) >= 4:
                    break
                time.sleep(2)
            rec = {"sid": sid, "idx": idx, "relation_type": "value", "trigger": trig,
                   "extra2": extra2[:4], "raw": raw[:200]}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            print("[%s] value extra2=%d elapsed=%.0fs" % (sid, len(extra2), time.time() - t0), flush=True)
    print("DONE -> " + OUT, flush=True)


if __name__ == "__main__":
    main()
