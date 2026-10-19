# -*- coding: utf-8 -*-
"""
超级记忆系统 —— 混合搜索融合 search_fusion.py
[UPD 2026-10-12] 落地"三路召回 + RRF 融合 + 时间加成"：
  ① 并行三路召回：
     - vector 向量路（语义搜索，找意思近的）
     - fts 关键词路（字面命中 content/tags，找字面命中的）
     - causal 因果路（从命中节点沿因果边扩散，带出因/果）
  ② RRF 融合：三路各自排名，score += 1/(60+rank)，哪路排得靠前贡献越大
  ③ 时间加成：越新的节点分越高（1 - 0.01/天，最低 0.2 保底）
统一为单管线，所有检索入口（/search、/nodes?q=、/rewind）共用。
[UPD 2026-10-19] 联想检索（assoc，评测回写）：可选增强——强召回时用无思考模式
  LLM 生成联想句扩展查询，与原查询合并召回（HyDE / query-expansion 同类）。
  LoCoMo-Plus 评测实证：隐藏抽象关联（因果/目标/价值观）下检索供给翻倍（25%→56%）。
  默认关闭（config.ASSOC_ENABLED=False），未配置/生成失败时静默回退原检索，行为不变。
"""
import json
import math
import re
import urllib.request
import urllib.error
from datetime import datetime

import config
from core import MemorySystem

# 联想句生成 prompt（评测四角度精简版；无思考模式模型，输出 JSON 数组）
_ASSOC_PROMPT = (
    "You are helping an AI memory system retrieve relevant past memories. "
    "A person (A) is now saying: \"{q}\". "
    "Imagine what A might have mentioned in PAST conversations (weeks or months ago) "
    "that relates to this statement. Do NOT stay on the literal surface; dig one layer deeper. "
    "Cover these angles: 1) a past cause/trigger event; 2) a persistent physical or emotional state; "
    "3) a long-term goal/plan; 4) a core value/principle. "
    "Write {n} first-person (\"I ...\") hypothetical past sentences, specific and concrete. "
    "Output strictly as a JSON array of {n} strings, nothing before or after."
)


def _assoc_enabled():
    return bool(config.ASSOC_ENABLED and config.ASSOC_ENDPOINT and config.ASSOC_KEY)


def _assoc_call(q):
    """调用无思考模式 LLM 生成联想句；失败/超时/空返回 []（调用方静默回退）。"""
    q = (q or "").strip()
    if not q:
        return []
    if len(q) > config.ASSOC_MAX_QUERY_LEN:
        q = q[:config.ASSOC_MAX_QUERY_LEN]
    payload = {
        "model": config.ASSOC_MODEL,
        "messages": [{"role": "user", "content": _ASSOC_PROMPT.format(q=q, n=config.ASSOC_N)}],
        "max_tokens": 600,
        "temperature": 0.9,
        "stream": False,
    }
    req = urllib.request.Request(
        config.ASSOC_ENDPOINT.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + config.ASSOC_KEY},
    )
    try:
        with urllib.request.urlopen(req, timeout=config.ASSOC_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
        content = (data["choices"][0]["message"].get("content") or "").strip()
        if not content:
            return []
        i, j = content.find("["), content.rfind("]")
        if i >= 0 and j > i:
            arr = json.loads(content[i:j + 1])
            if isinstance(arr, list):
                out = [str(x).strip() for x in arr if str(x).strip()][:config.ASSOC_N]
                return out
        strs = re.findall(r'"((?:[^"\\]|\\.)*)"', content)
        return [s.strip() for s in strs if s.strip()][:config.ASSOC_N]
    except Exception:
        return []


def gen_assoc_queries(q):
    """对外：生成联想查询句（供测试/接入方调用）。未配置或失败返回 []。"""
    if not _assoc_enabled():
        return []
    return _assoc_call(q)


def _recency_score(node):
    """时效性：按事件时间距今的月数做指数衰减（30 天半衰）"""
    try:
        ts = datetime.strptime(node.timestamp, "%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        ts = datetime.now()
    hours = max(0.0, (datetime.now() - ts).total_seconds() / 3600.0)
    return math.exp(-hours / (24.0 * 30.0))


def _tag_match(node, q):
    """标签匹配：查询词与 tags 的重合度"""
    ql = q.strip().lower()
    tags = node.tags or []
    if not ql or not tags:
        return 0.0
    hit = sum(1 for t in tags if t.lower() in ql or ql in t.lower())
    return min(hit / float(len(tags)), 1.0)


def _normalize(x, cap):
    try:
        return min(float(x) / float(cap), 1.0)
    except (ValueError, ZeroDivisionError, TypeError):
        return 0.0


def _tokenize(q):
    """把查询词拆成小写 token（中英文通用），并返回整句小写。"""
    ql = q.strip().lower()
    if not ql:
        return [], ql
    toks = [t for t in re.split(r"[\s,，。；;:：!！?？()（）\[\]{}<>《》\"'、/\\|_\-]+", ql) if t]
    return toks, ql


def _fts_recall(ms, q, project=None, top_k=config.RECALL_TOP_K):
    """关键词路：字面命中 content/tags（SQL LIKE 等价的内存实现，中英文通用）。"""
    toks, ql = _tokenize(q)
    if not toks:
        return []
    nodes = ms.graph.list_nodes(project=project, limit=100000)
    hits = []
    for node in nodes:
        if node.status == "obsolete":
            continue
        cl = (node.content or "").lower()
        tl = " ".join(node.tags or []).lower()
        if ql in cl or ql in tl:
            hits.append((node, 1.0))
        else:
            n = sum(1 for t in toks if t in cl or t in tl)
            if n >= 1:
                hits.append((node, 0.6 + 0.4 * (n / len(toks))))
    hits.sort(key=lambda x: x[1], reverse=True)
    return hits[:top_k]


def _time_bonus(node):
    """时间加成：按写入时间（created_at）新鲜度衰减，1 - 0.01/天，最低 0.2 保底。"""
    s = getattr(node, "created_at", None) or node.timestamp
    dt = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            dt = datetime.strptime(s, fmt)
            break
        except (ValueError, TypeError):
            continue
    if dt is None:
        dt = datetime.now()
    days = max(0.0, (datetime.now() - dt).total_seconds() / 86400.0)
    return max(0.2, 1.0 - days * 0.01)


def recall_candidates(ms, q, project=None, top_k=config.RECALL_TOP_K, assoc_queries=None):
    """并行三路召回 + RRF 融合 + 时间加成，返回候选（已按融合分排序）。
    assoc_queries: 联想句列表（可选）——每条联想句额外参与 vector/fts 召回并合并 RRF，
    扩大跨时间/跨粒度的召回面（评测回写，默认 None 时行为与旧版完全一致）。"""
    cands = {}

    def ensure(nid, node):
        if nid not in cands:
            cands[nid] = {"node": node, "vector_score": 0.0, "fts_score": 0.0,
                          "causal": 0, "assoc": 0, "rrf": 0.0, "time_bonus": 1.0,
                          "recall_score": 0.0}
        return cands[nid]

    # ① vector 向量路
    where = {"project": project} if project else None
    vec_hits = ms.vector.query(q, top_k=config.RECALL_TOP_K, where=where)
    vec_rank = 0
    for hit in vec_hits:
        node = ms.graph.get_node(hit["id"])
        if not node or node.status == "obsolete":
            continue
        vec_rank += 1
        rec = ensure(hit["id"], node)
        rec["vector_score"] = hit["score"]
        rec["rrf"] += 1.0 / (60.0 + vec_rank)

    # ② fts 关键词路（字面命中）
    fts_hits = _fts_recall(ms, q, project=project, top_k=config.RECALL_TOP_K)
    fts_rank = 0
    for node, fs in fts_hits:
        fts_rank += 1
        rec = ensure(node.id, node)
        rec["fts_score"] = fs
        rec["rrf"] += 1.0 / (60.0 + fts_rank)

    # ④ assoc 联想路（可选）：每条联想句走 vector+fts，命中并入 RRF（带 assoc 标记）
    assoc_rank = 0
    for aq in (assoc_queries or []):
        aq = (aq or "").strip()
        if not aq:
            continue
        for hit in ms.vector.query(aq, top_k=config.RECALL_TOP_K, where=where):
            node = ms.graph.get_node(hit["id"])
            if not node or node.status == "obsolete":
                continue
            assoc_rank += 1
            rec = ensure(hit["id"], node)
            rec["assoc"] += 1
            rec["rrf"] += 1.0 / (60.0 + assoc_rank)
        for node, fs in _fts_recall(ms, aq, project=project, top_k=config.RECALL_TOP_K):
            if node.status == "obsolete":
                continue
            assoc_rank += 1
            rec = ensure(node.id, node)
            rec["assoc"] += 1
            rec["rrf"] += 1.0 / (60.0 + assoc_rank)

    # ③ causal 因果路：从 vector/fts 各自 top 10 种子沿因果边扩散 1 层
    seen_seed = set()
    causal_seeds = []
    for hit in vec_hits[:10]:
        if hit["id"] not in seen_seed:
            seen_seed.add(hit["id"])
            causal_seeds.append(hit["id"])
    for node, _fs in fts_hits[:10]:
        if node.id not in seen_seed:
            seen_seed.add(node.id)
            causal_seeds.append(node.id)
    causal_rank = 0
    for nid in causal_seeds:
        for nbr in (ms.causal.get_causes(nid, depth=1, max_nodes=6) +
                    ms.causal.get_effects(nid, depth=1, max_nodes=6)):
            nn = ms.graph.get_node(nbr["id"])
            if not nn or nn.status == "obsolete":
                continue
            causal_rank += 1
            rec = ensure(nn.id, nn)
            rec["causal"] += 1
            rec["rrf"] += 1.0 / (60.0 + causal_rank)

    # 时间加成 + 最终融合分（RRF 分 * 新鲜度加权，最多上浮 40%）
    out = []
    for nid, rec in cands.items():
        rec["time_bonus"] = _time_bonus(rec["node"])
        rec["recall_score"] = rec["rrf"] * (0.6 + 0.4 * rec["time_bonus"])
        out.append(rec)
    out.sort(key=lambda c: c["recall_score"], reverse=True)
    return out[:top_k]


def rerank(ms, cands, top_n=config.SEARCH_LIMIT_DEFAULT):
    """精排：直接按 RRF 融合分（含时间加成）取 Top N。"""
    cands.sort(key=lambda c: c["recall_score"], reverse=True)
    return cands[:top_n]


def search(ms, q, limit=config.SEARCH_LIMIT_DEFAULT, project=None, with_causal=True):
    """完整检索：三路召回 → RRF 融合 → 时间加成 → 因果链（causes/effects 各取 3 层）。"""
    cands = recall_candidates(ms, q, project=project)
    top = rerank(ms, cands, top_n=limit)
    results = []
    for c in top:
        node = c["node"]
        ms.graph.touch(node.id)  # 检索即访问，计入 causal_factor
        item = {"node": node.to_dict(), "score": round(c["recall_score"], 4),
                "weight_zone": ("Core" if node.weight >= config.CORE_MIN else
                                "Working" if node.weight >= config.WORKING_MIN else
                                "Cold" if node.weight >= config.COLD_MIN else "Archive")}
        if with_causal:
            item["causes"] = ms.causal.get_causes(node.id)
            item["effects"] = ms.causal.get_effects(node.id)
        results.append(item)
    return {"query": q, "limit": limit, "engine": "recall+rrf(V6,3路)", "results": results}


def search_strong(ms, q, limit=config.SEARCH_LIMIT_DEFAULT, project=None, force_raw=False, expand=False):
    """强召回检索（2026-10-17 原文层）：先走三路召回+RRF；
    当图 top1 向量距离过远（语义不够近）、融合分极端低、无结果、或显式 force_raw 时，
    下探原文层（verbatim 向量）逐字召回，返回原文块 + 关联节点，带出处。
    日常检索（search/nodes_query）不触碰原文层。
    expand=True（2026-10-19 评测回写）：额外启用联想检索——生成联想句扩展召回面，
    未配置 config.ASSOC_* 或生成失败时静默回退，与 expand=False 结果一致。"""
    assoc_queries = []
    if expand and _assoc_enabled():
        assoc_queries = _assoc_call(q)

    cands = recall_candidates(ms, q, project=project, assoc_queries=assoc_queries)
    top = rerank(ms, cands, top_n=limit)
    results = []
    for c in top:
        node = c["node"]
        ms.graph.touch(node.id)
        item = {"node": node.to_dict(), "score": round(c["recall_score"], 4),
                "weight_zone": ("Core" if node.weight >= config.CORE_MIN else
                                "Working" if node.weight >= config.WORKING_MIN else
                                "Cold" if node.weight >= config.COLD_MIN else "Archive")}
        item["causes"] = ms.causal.get_causes(node.id)
        item["effects"] = ms.causal.get_effects(node.id)
        results.append(item)

    best_score = results[0]["score"] if results else 0.0
    # 图向量路 top1 相似度（取全部候选中 vector_score 最高者，而非 RRF top1——
    # RRF top1 可能纯靠因果/FTS 凑出 vec=0，不能代表向量匹配置信度）
    vec_max = max((c.get("vector_score", 0.0) for c in cands), default=0.0)
    # 原文向量路 top1 相似度（预先探测，比较型触发依据）
    verb_top = 0.0
    _vq = ms.vector.query_verbatim(q, top_k=1)
    if _vq:
        verb_top = float(_vq[0].get("score", 0.0))
    # 低置信判定（任一成立→下探原文）：
    #   ① 图无结果  ② RRF 融合分极端低  ③ 原文 top1 相似度 > 图向量 top1 相似度
    #   （③ 是主信号：原文比图更能答这个查询，就逐字召回；比较型判断自适应分布，无绝对阈值）
    low_conf = (not results) or best_score < config.STRONG_RECALL_FLOOR or verb_top > vec_max
    raw = []
    mode = "graph"
    if force_raw or low_conf:
        raw = ms.vector.query_verbatim(q, top_k=config.VERBATIM_STRONG_TOP_K)
        for r in raw:
            blk = ms.graph.get_verbatim(r["id"])
            if blk:
                r["text"] = blk["text"]
                r["ts"] = blk["ts"]
                r["source"] = blk["source"]
                r["node_id"] = blk["node_id"]
                r["session"] = blk["session"]
                r["node"] = ms.graph.get_node(blk["node_id"]).to_dict() if blk["node_id"] and ms.graph.get_node(blk["node_id"]) else None
            else:
                r["text"] = r.get("text", "")
        if raw:
            mode = "verbatim" if not results else "hybrid"
        elif not results:
            mode = "none"
    if assoc_queries:
        mode = mode + "+assoc"
    return {"query": q, "mode": mode, "engine": "recall+rrf→verbatim(强召回,assoc可选)",
            "results": results, "raw": raw, "assoc_queries": assoc_queries}


def nodes_query(ms, q, limit=config.SEARCH_LIMIT_DEFAULT, project=None):
    """/nodes?q= 走同一管线（三路召回+RRF），不带因果链（保持轻量）。"""
    cands = recall_candidates(ms, q, project=project)
    top = rerank(ms, cands, top_n=limit)
    return [{"node": c["node"].to_dict(), "score": round(c["recall_score"], 4)} for c in top]


def forgotten_nodes(ms, threshold=config.ARCHIVE_THRESHOLD, half_life_days=None):
    """/forgotten：按阈值列出已遗忘节点；half_life 可覆盖衰减口径（估算）。"""
    nodes = ms.graph.list_nodes(limit=100000)
    out = []
    for n in nodes:
        if n.status == "obsolete":
            continue
        w = n.weight
        if half_life_days:
            try:
                ts = datetime.strptime(n.timestamp, "%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                ts = datetime.now()
            days = max(0.0, (datetime.now() - ts).total_seconds() / 86400.0)
            w = w * (0.5 ** (days / float(half_life_days)))
        if w < threshold:
            out.append({"id": n.id, "content": n.content[:80], "weight": round(w, 3),
                        "node_type": n.node_type, "project": n.project})
    out.sort(key=lambda x: x["weight"])
    return out
