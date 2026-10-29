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


def recall_candidates(ms, q, project=None, top_k=config.RECALL_TOP_K, assoc_queries=None,
                      recency=0, scene=None, goal=0, state=None, tilt=False):
    """并行三路召回 + RRF 融合 + 时间加成，返回候选（已按融合分排序）。
    assoc_queries: 联想句列表（可选）——每条联想句额外参与 vector/fts 召回并合并 RRF，
    扩大跨时间/跨粒度的召回面（评测回写，默认 None 时行为与旧版完全一致）。
    recency: [UPD 2026-10-20 时间维] 0=默认（时间仅小幅加成）；1=时间加权模式——
    时间加成占比从 40% 提到 60%，且窗口内（RECENCY_WINDOW_DAYS 天）记忆额外加 15% 分，
    适合"最近/近况/最近做了什么"类查询。
    scene: [UPD 2026-10-20 空间维] 场景实体名（可选）——沿 involves 边带出该场景下的
    记忆节点并入 RRF（场景过滤检索），适合"在 XX 场景/项目/设备下发生了什么"类查询。
    goal: [UPD 2026-10-20 价值维] 目标导向重排（可选）——库内 goal 节点沿
    contributes_to 边扩散加权（服务目标的记忆前置）、沿 blocks 边降权（阻碍目标的
    记忆后置），适合"对当前目标最有用的是什么"类查询。
    state: [UPD 2026-10-20 状态维] 状态名（可选）——沿 transitions_to 入边带出
    "导致该状态"的事件/节点并入 RRF，适合"什么导致了 XX 状态"类查询。
    tilt: [UPD 2026-10-29 注意力倾斜，借鉴 digital-life-core v0.9.9] 可选——
    True 时启用注意力倾斜（只改排序不删内容）：
      A. 活跃目标自动感知（config.TILT_AUTO_GOAL）：调用方未显式传 goal 时，自动沿
         status=active 的 goal 节点的 contributes_to/blocks 边加权；
      B. importance 倾斜（config.TILT_IMPORTANCE_BOOST）：goal_related +15%、
         reference -10%、operational 不变（温和版危机/日常阶梯）。
    默认 False 时行为与旧版完全一致。"""
    cands = {}

    def ensure(nid, node):
        if nid not in cands:
            cands[nid] = {"node": node, "vector_score": 0.0, "fts_score": 0.0,
                          "causal": 0, "assoc": 0, "scene": 0, "goal": 0, "goal_block": 0,
                          "state": 0, "rrf": 0.0, "time_bonus": 1.0, "recall_score": 0.0}
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

    # ⑤ scene 场景路（[UPD 2026-10-20 空间维]，可选）：scene=实体名 →
    #    沿 involves 入边带出"该场景下"的记忆节点并入 RRF（带 scene 标记）
    scene_rank = 0
    if scene:
        scene_name = str(scene).strip()
        ent_nodes = [n for n in ms.graph.list_nodes(limit=100000)
                     if n.node_type in config.SCENE_ENTITY_TYPES
                     and n.status != "obsolete"
                     and (scene_name == (n.content or "").strip()
                          or (len(scene_name) >= 2 and scene_name in (n.content or "")))]
        for ent in ent_nodes[:5]:
            for e in ms.graph.in_edges(ent.id):
                if e.edge_type not in ("involves", "occurred_in"):
                    continue
                nn = ms.graph.get_node(e.from_id)
                if not nn or nn.status == "obsolete":
                    continue
                scene_rank += 1
                rec = ensure(nn.id, nn)
                rec["scene"] = rec.get("scene", 0) + 1
                rec["rrf"] += config.SCENE_SEARCH_WEIGHT * (1.0 / (60.0 + scene_rank))

    # ⑥ goal 目标路（[UPD 2026-10-20 价值维]，可选）：goal=1 → 库内 goal 节点沿
    #    contributes_to 入边加权（服务目标前置）、沿 blocks 入边降权（阻碍目标后置）
    # [UPD 2026-10-29 注意力倾斜] tilt 且未显式传 goal 时自动感知：只取 status=active
    #    的 goal 节点（"此刻该关注什么"），复用同一套加权逻辑。
    goal_rank = 0
    block_rank = 0
    auto_goal = bool(tilt and config.TILT_ENABLED and config.TILT_AUTO_GOAL and not goal)
    if (goal or auto_goal) and config.GOAL_SEARCH_ENABLED:
        goal_nodes = [n for n in ms.graph.list_nodes(limit=100000)
                      if n.node_type == "goal" and n.status != "obsolete"
                      and (n.status == "active" if auto_goal else True)]
        for g in goal_nodes[:10]:
            for e in ms.graph.in_edges(g.id):
                nn = ms.graph.get_node(e.from_id)
                if not nn or nn.status == "obsolete":
                    continue
                if e.edge_type == "contributes_to":
                    goal_rank += 1
                    rec = ensure(nn.id, nn)
                    rec["goal"] = rec.get("goal", 0) + 1
                    rec["rrf"] += config.GOAL_SEARCH_WEIGHT * (1.0 / (60.0 + goal_rank))
                elif e.edge_type == "blocks":
                    block_rank += 1
                    rec = ensure(nn.id, nn)
                    rec["goal_block"] = rec.get("goal_block", 0) + 1
                    rec["rrf"] -= config.GOAL_BLOCK_PENALTY * (1.0 / (60.0 + block_rank))

    # ⑦ state 状态路（[UPD 2026-10-20 状态维]，可选）：state=状态名 →
    #    沿 transitions_to 入边带出"导致该状态"的事件/节点并入 RRF
    state_rank = 0
    if state:
        state_name = str(state).strip()
        state_nodes = [n for n in ms.graph.list_nodes(limit=100000)
                       if n.node_type == "state" and n.status != "obsolete"
                       and (state_name == (n.content or "").strip()
                            or (len(state_name) >= 2 and state_name in (n.content or "")))]
        for sn in state_nodes[:5]:
            for e in ms.graph.in_edges(sn.id):
                if e.edge_type != "transitions_to":
                    continue
                nn = ms.graph.get_node(e.from_id)
                if not nn or nn.status == "obsolete":
                    continue
                state_rank += 1
                rec = ensure(nn.id, nn)
                rec["state"] = rec.get("state", 0) + 1
                rec["rrf"] += config.STATE_SEARCH_WEIGHT * (1.0 / (60.0 + state_rank))

    # 时间加成 + 最终融合分（RRF 分 * 新鲜度加权，最多上浮 40%）
    # [UPD 2026-10-20 时间维] recency=1 时：时间占比加大（0.4+0.6*time_bonus），
    #   且 RECENCY_WINDOW_DAYS 窗口内记忆再上浮 15%（近况类查询优先召回最新）
    # [UPD 2026-10-29 注意力倾斜] tilt 时叠加 importance 倾斜（只改排序不删内容）：
    #   goal_related +15% / reference -10% / operational 不变，乘在融合分上。
    recency = 1 if recency else 0
    window_days = max(1, config.RECENCY_WINDOW_DAYS)
    tilt_on = bool(tilt and config.TILT_ENABLED)
    out = []
    for nid, rec in cands.items():
        node = rec["node"]
        rec["time_bonus"] = _time_bonus(node)
        base = rec["rrf"]
        if recency:
            in_window = 0.0
            try:
                ts = datetime.strptime(node.timestamp, "%Y-%m-%d %H:%M")
                days_ago = max(0.0, (datetime.now() - ts).total_seconds() / 86400.0)
                if days_ago <= window_days:
                    in_window = 0.15
            except (ValueError, TypeError):
                pass
            rec["recall_score"] = base * (0.4 + 0.6 * rec["time_bonus"]) + in_window
        else:
            rec["recall_score"] = base * (0.6 + 0.4 * rec["time_bonus"])
        if tilt_on:
            imp = getattr(node, "importance", None) or "reference"
            boost = config.TILT_IMPORTANCE_BOOST.get(imp, 1.0)
            rec["tilt_boost"] = boost
            rec["recall_score"] = rec["recall_score"] * boost
        out.append(rec)
    out.sort(key=lambda c: c["recall_score"], reverse=True)
    return out[:top_k]


def rerank(ms, cands, top_n=config.SEARCH_LIMIT_DEFAULT):
    """精排：直接按 RRF 融合分（含时间加成）取 Top N。"""
    cands.sort(key=lambda c: c["recall_score"], reverse=True)
    return cands[:top_n]


def search(ms, q, limit=config.SEARCH_LIMIT_DEFAULT, project=None, with_causal=True,
           recency=0, scene=None, goal=0, state=None, tilt=False):
    """完整检索：三路召回 → RRF 融合 → 时间加成 → 因果链（causes/effects 各取 3 层）。
    recency=1：时间加权模式；scene=：场景过滤检索；goal=1：目标导向重排；
    state=：状态检索；tilt=True：注意力倾斜（活跃目标自动感知 + importance 倾斜，
    只改排序不删内容，见 recall_candidates）。"""
    cands = recall_candidates(ms, q, project=project, recency=recency, scene=scene,
                              goal=goal, state=state, tilt=tilt)
    top = rerank(ms, cands, top_n=limit)
    results = []
    for c in top:
        node = c["node"]
        ms.graph.touch(node.id)  # 检索即访问，计入 causal_factor
        item = {"node": node.to_dict(), "score": round(c["recall_score"], 4),
                "weight_zone": ("Core" if node.weight >= config.CORE_MIN else
                                "Working" if node.weight >= config.WORKING_MIN else
                                "Cold" if node.weight >= config.COLD_MIN else "Archive")}
        if "tilt_boost" in c:
            item["tilt_boost"] = c["tilt_boost"]
        if with_causal:
            item["causes"] = ms.causal.get_causes(node.id)
            item["effects"] = ms.causal.get_effects(node.id)
        results.append(item)
    return {"query": q, "limit": limit, "engine": "recall+rrf(V6,3路)",
            "tilted": bool(tilt and config.TILT_ENABLED), "results": results}


def search_strong(ms, q, limit=config.SEARCH_LIMIT_DEFAULT, project=None, force_raw=False,
                  expand=False, recency=0, scene=None, goal=0, state=None, tilt=False):
    """强召回检索（2026-10-17 原文层）：先走三路召回+RRF；
    当图 top1 向量距离过远（语义不够近）、融合分极端低、无结果、或显式 force_raw 时，
    下探原文层（verbatim 向量）逐字召回，返回原文块 + 关联节点，带出处。
    日常检索（search/nodes_query）不触碰原文层。
    expand=True（2026-10-19 评测回写）：额外启用联想检索——生成联想句扩展召回面，
    未配置 config.ASSOC_* 或生成失败时静默回退，与 expand=False 结果一致。
    recency=1 / scene= / goal=1 / state=（2026-10-20 时间维/空间维/价值维/状态维）：
    时间加权 / 场景过滤 / 目标导向重排 / 状态检索。
    tilt=True（2026-10-29 注意力倾斜）：活跃目标自动感知 + importance 倾斜，只改排序。"""
    assoc_queries = []
    if expand and _assoc_enabled():
        assoc_queries = _assoc_call(q)

    cands = recall_candidates(ms, q, project=project, assoc_queries=assoc_queries,
                              recency=recency, scene=scene, goal=goal, state=state, tilt=tilt)
    top = rerank(ms, cands, top_n=limit)
    results = []
    for c in top:
        node = c["node"]
        ms.graph.touch(node.id)
        item = {"node": node.to_dict(), "score": round(c["recall_score"], 4),
                "weight_zone": ("Core" if node.weight >= config.CORE_MIN else
                                "Working" if node.weight >= config.WORKING_MIN else
                                "Cold" if node.weight >= config.COLD_MIN else "Archive")}
        if "tilt_boost" in c:
            item["tilt_boost"] = c["tilt_boost"]
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
            "results": results, "raw": raw, "assoc_queries": assoc_queries,
            "tilted": bool(tilt and config.TILT_ENABLED)}


def nodes_query(ms, q, limit=config.SEARCH_LIMIT_DEFAULT, project=None, recency=0, scene=None, goal=0, state=None, tilt=False):
    """/nodes?q= 走同一管线（三路召回+RRF），不带因果链（保持轻量）。
    tilt=True：注意力倾斜（活跃目标自动感知 + importance 倾斜，只改排序）。"""
    cands = recall_candidates(ms, q, project=project, recency=recency, scene=scene,
                              goal=goal, state=state, tilt=tilt)
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
