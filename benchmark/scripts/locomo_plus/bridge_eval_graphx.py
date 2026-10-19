# -*- coding: utf-8 -*-
"""
纯系统对照：基线 + 图遍历扩展（无 LLM 联想）
=============================================
- 与 A 基线同管道：trigger 直检 limit=24
- 附加：对命中节点做 1 跳图遍历（edges related/双向），取邻居节点内容合并供给
- answerer：V4（与主成绩一致，保持利用端可比）
- 输出 bridge_results_graphx.jsonl（断点续写）
"""
import os, sys, json, time, sqlite3, urllib.request, urllib.error

MS_ROOT = os.environ.get("SUPERMEM_ROOT", os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")))
EVAL_ROOT = os.environ.get("EVAL_ROOT", os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "results", "locomo_plus")))
PLUS_DATA = os.environ.get("LOCOMO_PLUS_DATA", os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "locomo_plus_data")))
DBROOT = os.path.join(EVAL_ROOT, "db_plus")
OUT = os.path.join(EVAL_ROOT, "bridge_results_graphx.jsonl")

sys.path.insert(0, MS_ROOT)
sys.path.insert(0, PLUS_DATA)
import config
config.EMBED_MODEL_NAME = "BAAI/bge-small-en-v1.5"
config.EMBED_MODEL_DIR = os.path.join(config.MODELS_DIR, "bge-small-en-v1.5")

from core import GraphStore, VectorSearch, CausalEngine, MemorySystem
from search_fusion import search_strong
from build_conv import parse_ab_dialogue, map_speaker

class IsolatedMS(MemorySystem):
    def __init__(self, db_path, chroma_dir):
        self.graph = GraphStore(db_path=db_path)
        self.vector = VectorSearch(chroma_dir=chroma_dir)
        self.causal = CausalEngine(self.graph)

DS_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DS_BASE = "https://api.deepseek.com/v1"
DS_MODEL = "deepseek-flash"

SYSTEM_PROMPT = (
    "You are B, continuing a natural conversation with A. "
    "Below are memory snippets retrieved from A's past conversation with you. "
    "A may be referring back to something mentioned earlier. "
    "Respond as B would, naturally and concisely (1-3 sentences). "
    "If a snippet is clearly relevant to what A says, acknowledge or build on that memory "
    "(reference its specific details, not just generic empathy). "
    "If nothing is relevant, respond naturally as a friend would."
)


def ds_chat(system, user, timeout=180):
    payload = {"model": DS_MODEL,
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
               "max_tokens": 256, "temperature": 0, "stream": False}
    req = urllib.request.Request(DS_BASE + "/chat/completions", data=json.dumps(payload).encode("utf-8"),
                                 method="POST", headers={"Content-Type": "application/json",
                                                         "Authorization": "Bearer " + DS_KEY})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
                return (data["choices"][0]["message"].get("content") or "").strip()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(10 * (attempt + 1))
                continue
            return f"__ERR_HTTP_{e.code}__"
        except Exception as e:
            if attempt == 3:
                return f"__ERR__{e}"
            time.sleep(8 * (attempt + 1))
    return "__ERR__"


def build_context(frags):
    parts = []
    for i, c in enumerate(frags[:40]):
        if c:
            parts.append("[memory %d] %s" % (i + 1, c[:300]))
    return "\n".join(parts)


def cue_to_evidence(cue_dialogue, locomo_item):
    conv = locomo_item.get("conversation") or {}
    sa = conv.get("speaker_a", "A")
    sb = conv.get("speaker_b", "B")
    try:
        turns = map_speaker(parse_ab_dialogue(cue_dialogue or ""), sa, sb)
        return "\n".join(f'{t["speaker"]}: {t["text"].strip()}' for t in turns if t.get("text"))
    except Exception:
        return cue_dialogue or ""


def graph_neighbors(db_path, node_ids, max_per_node=4, max_total=24):
    """1 跳图遍历：命中节点的双向边邻居内容（纯图谱能力）"""
    out = []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        for nid in node_ids:
            rows = conn.execute(
                "SELECT n.content FROM edges e JOIN nodes n ON "
                "(n.id = e.from_id OR n.id = e.to_id) WHERE (e.from_id=? OR e.to_id=?) AND n.id != ? LIMIT ?",
                (nid, nid, nid, max_per_node)).fetchall()
            for r in rows:
                out.append(r["content"])
            if len(out) >= max_total:
                break
        conn.close()
    except Exception:
        pass
    return out


def main():
    plus_list = json.load(open(os.path.join(PLUS_DATA, "locomo_plus.json"), encoding="utf-8"))
    locomo_list = json.load(open(os.path.join(PLUS_DATA, "locomo10.json"), encoding="utf-8"))

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
        for idx, plus in enumerate(plus_list):
            sid = "plus_%04d" % idx
            if sid in done_sids:
                done += 1
                continue
            trig = plus.get("trigger_query") or plus.get("trigger") or ""
            rel = plus.get("relation_type", "?")
            locomo_item = locomo_list[idx % len(locomo_list)]
            dbdir = os.path.join(DBROOT, sid)
            if not os.path.isdir(dbdir):
                rec = {"sid": sid, "idx": idx, "relation_type": rel, "trigger": trig,
                       "pred": "__NO_DB__", "n_frags": 0, "n_graph": 0}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                done += 1
                continue
            ms = IsolatedMS(os.path.join(dbdir, "super_memory.db"), os.path.join(dbdir, "chroma_db"))
            try:
                res = search_strong(ms, trig, limit=24, project=sid)
            except Exception:
                res = {"results": [], "raw": []}
            frags = []
            hit_ids = []
            for it in res.get("results", []):
                c = (it.get("node") or {}).get("content", "")
                nid = (it.get("node") or {}).get("id", "")
                if c:
                    frags.append(c)
                if nid:
                    hit_ids.append(nid)
            for it in res.get("raw", []):
                t = it.get("text", "")
                if t:
                    frags.append(t)

            n_graph = 0
            if hit_ids:
                gnei = graph_neighbors(os.path.join(dbdir, "super_memory.db"), hit_ids)
                for t in gnei:
                    if t and t not in frags:
                        frags.append(t)
                        n_graph += 1

            ctx = build_context(frags)
            user = f"Memory snippets:\n{ctx}\n\nA: {trig}\n\nB:"
            pred = ds_chat(SYSTEM_PROMPT, user)
            rec = {
                "sid": sid, "idx": idx, "relation_type": rel, "trigger": trig,
                "cue_dialogue": plus.get("cue_dialogue", ""),
                "evidence": cue_to_evidence(plus.get("cue_dialogue", ""), locomo_item),
                "pred": pred, "n_frags": len(frags), "n_graph": n_graph, "mode": res.get("mode", "?"),
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            done += 1
            if done % 20 == 0:
                print("[%d/%d] %s rel=%s n_frags=%d n_graph=%d elapsed=%.0fs" % (
                    done, total, sid, rel, len(frags), n_graph, time.time() - t0), flush=True)
    print("DONE total=%d -> %s" % (done, OUT), flush=True)


if __name__ == "__main__":
    main()
