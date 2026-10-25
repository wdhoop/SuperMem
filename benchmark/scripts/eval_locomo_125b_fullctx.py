# -*- coding: utf-8 -*-
"""
LoCoMo QA 评测 · 125B full-context（全量供给天花板基线，建议④）
与 125B l24 v2 完全相同的数据/模型/输出协议，唯一区别：供给从"检索 top-24 片段"换成"完整对话原文"。
目的：得到"把所有对话塞进上下文直接回答"的本地模型上限，与 limit24 检索供给对比，量化记忆系统的相对提升/差距。

实现：每段对话一个多轮 OpenAI 会话（对话作公共前缀 + 逐题追加 Question），复用引擎 KV 缓存，
避免 1986 次全量 prefill（每段大 prefill 一次，后续只处理增量）。

输出：locomo_results_125b_fullctx.jsonl（字段与 l24 v2 完全一致，mode="fullctx"）
"""
import os, json, time, requests

EVAL = r"E:\AIGC_Hub\_refactor\locomo_eval"
DATA = os.path.join(EVAL, "data", "locomo10.json")
OUT = os.path.join(EVAL, "locomo_results_125b_fullctx.jsonl")
ENDPOINT = "http://127.0.0.1:8080/v1/chat/completions"
GW_PING = "http://127.0.0.1:9000/api/llm/ping"   # 心跳：刷新 Strata 活跃时间，防空闲看门狗误杀
API_KEY = "sk_strata_iwnsdrfax8v6hko1l7g49z5t3c2mjbpe"
MODEL = "qwen3.8-flash-next-iq3_s"
LAST_HEARTBEAT = [0.0]

# 仅跑前 N 段（None=全量 10 段）。用于小样验证速度与正确性。
LIMIT_SAMPLES = None  # 调试时设 1 或 2

SYSTEM_PROMPT = (
    "You are answering questions about a long conversation between two people. "
    "Below is the COMPLETE conversation. The correct answer "
    "ALWAYS exists somewhere in the conversation. "
    "Reason step by step using the conversation (relevant information may be scattered across different times). "
    "Give the most likely concise answer: a date, name, number, or short phrase. "
    "Only reply 'unknown' if the conversation gives you absolutely nothing to work with."
)

def heartbeat():
    now = time.time()
    if now - LAST_HEARTBEAT[0] < 300:
        return
    LAST_HEARTBEAT[0] = now
    try:
        requests.get(GW_PING, timeout=5)
    except Exception:
        pass

def llm_chat(messages, timeout=600):
    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
        "max_tokens": 128,
        "temperature": 0,
    }
    hdrs = {"Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}"}
    for attempt in range(4):
        try:
            r = requests.post(ENDPOINT, json=payload, headers=hdrs, timeout=timeout)
            r.raise_for_status()
            j = r.json()
            msg = j["choices"][0]["message"]
            return (msg.get("content") or "").strip()
        except requests.exceptions.ConnectionError:
            if attempt == 3:
                return "__LLM_ERR__connection"
            time.sleep(2 * (attempt + 1))
        except Exception as e:
            if attempt == 3:
                return f"__LLM_ERR__{e}"
            time.sleep(3 * (attempt + 1))
    return ""

def build_full_conversation(conversation):
    """完整对话文本：DATE + CONVERSATION + Speaker said, "..."（与 unified_input 同风格）"""
    sessions = sorted(
        [k for k in conversation.keys() if k.startswith("session_") and not k.endswith("_date_time")],
        key=lambda x: int(x.split("_")[-1]),
    )
    context = ""
    for s in sessions:
        date = conversation.get(f"{s}_date_time", "")
        turns = conversation.get(s, [])
        context += f"DATE: {date}\nCONVERSATION:\n"
        for d in turns:
            speaker = d.get("speaker", "?")
            text = (d.get("text") or "").strip()
            if "blip_caption" in d:
                context += f'{speaker} said, "{text}" and shared {d["blip_caption"]}.\n'
            else:
                context += f'{speaker} said, "{text}"\n'
        context += "\n"
    return context

def main():
    with open(DATA, encoding="utf-8") as f:
        data = json.load(f)
    if LIMIT_SAMPLES:
        data = data[:LIMIT_SAMPLES]

    done = set()
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    if "__LLM_ERR__" not in str(rec.get("pred", "")):
                        done.add((rec["sid"], rec["idx"]))
                except Exception:
                    pass
    print(f"resume: {len(done)} done ({'错误条目将重跑' if os.path.exists(OUT) else '全新跑'})", flush=True)

    CAT_NAMES = {1: "single-hop", 2: "temporal", 3: "commonsense", 4: "multi-hop", 5: "adversarial"}
    t0 = time.time()
    total = 0
    with open(OUT, "a", encoding="utf-8") as f:
        for sample in data:
            sid = sample["sample_id"]
            conv = sample.get("conversation") or {}
            ctx = build_full_conversation(conv)
            # 该段对话的全部待跑题目
            qas = []
            for idx, qa in enumerate(sample["qa"]):
                if (sid, idx) in done:
                    continue
                q = qa["question"]
                gt = qa.get("answer") or qa.get("adversarial_answer") or ""
                cat = qa["category"]
                qas.append((idx, q, gt, cat))
            if not qas:
                print(f"[{sid}] all done", flush=True)
                continue

            print(f"[{sid}] 待跑 {len(qas)} 题，对话长度 {len(ctx)} 字符，开始多轮会话", flush=True)
            # 多轮会话：对话为公共前缀，逐题追加
            msgs = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": "Below is the complete conversation.\n\n" + ctx + "\n\nAnswer the following questions one by one, based on the conversation above."}]
            for idx, q, gt, cat in qas:
                msgs.append({"role": "user", "content": f"Question: {q}\n\nAnswer:"})
                pred = llm_chat(msgs)
                msgs.append({"role": "assistant", "content": pred})
                heartbeat()
                rec = {
                    "sid": sid, "idx": idx, "category": CAT_NAMES.get(cat, str(cat)), "cat_id": cat,
                    "question": q, "gt": gt, "pred": pred,
                    "mode": "fullctx",
                    "n_nodes": 0,
                    "n_raw": 0,
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                done.add((sid, idx))
                total += 1
                if total % 20 == 0:
                    print(f"  +{total} elapsed={time.time()-t0:.0f}s msgs_tokens_approx={sum(len(m['content']) for m in msgs)}", flush=True)
    print(f"DONE new={total} total_done={len(done)} elapsed={time.time()-t0:.0f}s", flush=True)

if __name__ == "__main__":
    main()
