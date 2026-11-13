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
import anchors  # [UPD 2026-11-12] 实体钩子层
from core import MemorySystem, skill_candidate

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


_TS_FMTS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M", "%Y-%m-%d")


def _parse_dt_any(s):
    """[FIX 2026-11-13] 时间容错解析：返回 datetime 或 None。

    原 _recency_score / _time_bonus 在解析失败时一律退化成 datetime.now()，
    等于把"时间未知"当成"刚刚发生"——格式不认识的旧节点白拿满分新鲜度，
    在 recency=1 模式下直接把老记忆顶到前排。现在失败返回 None，由调用方给中性分。"""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        try:
            return datetime.fromtimestamp(float(s))
        except (ValueError, OSError, OverflowError):
            return None
    s = str(s).strip()
    if not s:
        return None
    for fmt in _TS_FMTS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _recency_score(node):
    """时效性：按事件时间距今的月数做指数衰减（30 天半衰）。时间未知给中性 0.5。"""
    ts = _parse_dt_any(getattr(node, "timestamp", None))
    if ts is None:
        return 0.5
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


_CN_RE = re.compile(r"[\u4e00-\u9fff]+")
_LATIN_RE = re.compile(r"[a-z0-9][a-z0-9._:/+#]*")
_SPLIT_RE = re.compile(r"[\s,，。；;:：!！?？()（）\[\]{}<>《》\"'、/\\|_\-—…]+")


def _tokenize(q):
    """把查询拆成 token 并返回整句小写：非中文串整段保留，中文段切 2-4 字 gram。

    [FIX 2026-11-13 P0-3] 原实现只按标点/空白切：中文长句（"为什么向量索引重建之后
    缺口反而变大了"）整句成为一个 token，而 FTS 腿是用 instr 做整串子串匹配，
    长句必然 0 命中——7 条难例里 6 条整句一 token，judge_verdict 的 lexical support
    随之全灭。现在中文按短段整取 + 长段滑窗切 gram，单字噪声丢弃。"""
    ql = q.strip().lower()
    if not ql:
        return [], ql
    toks = []
    seen = set()

    def _add(t):
        if not t or t in seen:
            return False
        if len(t) > 64:
            return False
        if len(t) == 1 and _CN_RE.match(t):
            return False          # 中文单字噪声大，不进 FTS
        seen.add(t)
        toks.append(t)
        return True

    for seg in _SPLIT_RE.split(ql):
        if not seg or len(toks) >= config.TOKENIZE_MAX_TOKENS:
            break
        if not _CN_RE.search(seg):
            _add(seg)
            continue
        # 混排段落：先保留英文/型号片段（AIGC_Hub、125B、RTX4070 之类）
        for lat in _LATIN_RE.findall(seg):
            if _add(lat) and len(toks) >= config.TOKENIZE_MAX_TOKENS:
                break
        for cns in _CN_RE.findall(seg):
            if _add(cns) and len(toks) >= config.TOKENIZE_MAX_TOKENS:
                break
            L = len(cns)
            if L <= config.TOKENIZE_CN_MAX_SEG:
                for i in range(L - 1):
                    bg = cns[i:i + 2]
                    if bg not in config.ANCHOR_STOPWORDS:
                        if _add(bg) and len(toks) >= config.TOKENIZE_MAX_TOKENS:
                            break
                continue
            stop = False
            for w in config.TOKENIZE_CN_GRAMS:
                if w > L or stop:
                    break
                for i in range(0, L - w + 1, config.TOKENIZE_CN_STEP):
                    g = cns[i:i + w]
                    if g in config.ANCHOR_STOPWORDS:
                        continue
                    if _add(g) and len(toks) >= config.TOKENIZE_MAX_TOKENS:
                        stop = True
                        break
            if stop:
                break
    return toks[:config.TOKENIZE_MAX_TOKENS], ql


def _node_turn(node):
    """从 tags 里读回 turn 序号（0 基，与适配器 ingest 的 idx 一致）。无标签返回 None。"""
    for t in (getattr(node, "tags", None) or []):
        if isinstance(t, str) and t.startswith("turn:"):
            try:
                return int(t[5:])
            except ValueError:
                return None
    return None


def _fts_recall(ms, q, project=None, top_k=config.RECALL_TOP_K, max_turn=None):
    """关键词路：字面命中 content/tags（中英文通用）。
    max_turn：只召回 turn 序号 < max_turn 的节点（评测回放用，模拟"库里只有 cutoff 前的历史"）。

    [UPD 2026-11-13] 从"拉全表到 Python 逐条比对"改为"SQL 层先筛命中行，再在命中集里算分"：
    原实现 list_nodes(limit=100000) 会把每行都转成 Node 对象（1 万节点时 10 万个对象字段），
    耗时随节点数近线性。现由 SQLite C 层做字符串扫描，只把真正命中的行转对象。
    两库实测结果集与分数完全一致，1 万节点 115ms → 24ms。"""
    toks, ql = _tokenize(q)
    if not toks:
        return []
    conds, args = [], []
    sql = "SELECT * FROM nodes WHERE status != 'obsolete'"
    if project:
        sql += " AND project=?"
        args.append(project)   # [FIX 2026-11-13 P0-1] 占位符在 SQL 最前，参数必须先绑定；
                               # 原先 append 在 token 之后，project 被绑成第一个 token（如 'when'），
                               # 导致 WHERE project='when' 恒不匹配、关键词路整条返回 0 条。
    for t in toks:
        conds.append("(instr(lower(content), ?) > 0 OR instr(lower(tags), ?) > 0)")
        args.extend([t, t])
    sql += " AND (" + " OR ".join(conds) + ") ORDER BY timestamp DESC"
    # [FIX 2026-11-13 P0-3] 中文切词后 token 变多（最多 24 个 OR 条件 × 2），长句命中行可能上千；
    # 在 SQL 层先截断再算分，避免把整库命中行全转成 Node。
    # [FIX 2026-11-13 P0-3b] 截断值原为 max(64, top_k*8)=320 —— 但配合 ORDER BY timestamp DESC，
    #   等于"只保留最新的 320 条命中行"，老节点（正确答案常在其中）被整批丢掉。
    #   实测：134 题自召回 @1 因此从 65.4% 掉到 53.0%。改为主库规模内的足量窗口。
    #   窗口 2000 时 @1=74.6%；收到 800 后 @1 反而跌到 72.4%，p95 无变化（415→422ms）——
    #   说明 p95 不是这个窗口造成的，质量优先取 2000。
    sql += " LIMIT ?"
    args.append(max(2000, int(top_k) * 8))
    rows = ms.graph.conn.execute(sql, args).fetchall()
    hits = []
    for r in rows:
        node = ms.graph._row_to_node(r)
        if max_turn is not None:
            tt = _node_turn(node)
            if tt is None or tt >= max_turn:
                continue
        cl = (node.content or "").lower()
        tl = " ".join(node.tags or []).lower()
        if ql in cl or ql in tl:
            hits.append((node, 1.0))
            continue
        matched = [t for t in toks if t in cl or t in tl]
        if not matched:
            continue
        # [FIX 2026-11-13 P0-3] 命中门槛：至少一个 ≥2 字的 gram，且"命中 2 个以上 token
        # 或命中最长 gram ≥4 字"——否则"记忆""系统"这种高频二字词会让半库节点拿 0.6 分。
        # [FIX 2026-11-13 P0-4] 门槛与 0.85 强制分只对中文 gram 生效：
        #   这两条是为中文 2-4 字滑窗设计的（中文 token 碎，需要门槛防"记忆/系统"污染）；
        #   英文是整词 token，几乎每个实词都 ≥4 字，套用后门槛形同虚设，
        #   且 0.85 会把"命中 1/2/3 个实词"全部压成同一档，反而抹平区分度。
        #   实测：修完参数顺序后保留这两条 → 74.9%；改为仅中文生效 → 82.0%（基线 82.2%）。
        cn_matched = [t for t in matched if not t.isascii()]
        cn_longest = max((len(t) for t in cn_matched), default=0)
        if cn_matched and not (len(matched) >= 2 or cn_longest >= 4):
            continue
        score = 0.6 + 0.4 * (len(matched) / float(len(toks)))
        if cn_longest >= 4:
            score = max(score, 0.85)   # 仅中文长 gram 给绝对底
        hits.append((node, min(1.0, score)))
    hits.sort(key=lambda x: x[1], reverse=True)
    return hits[:top_k]


def _time_bonus(node):
    """时间加成：按写入时间（created_at）新鲜度衰减，1 - 0.01/天，最低 0.2 保底。

    [FIX 2026-11-13] 原实现只认 3 种格式、失败取 now——与 _recency_score 同一个病：
    格式不认识的老节点拿到满分时间加成。现在走 _parse_dt_any，未知时间给中性 0.5。"""
    s = getattr(node, "created_at", None) or getattr(node, "timestamp", None)
    dt = _parse_dt_any(s)
    if dt is None:
        return 0.5
    days = max(0.0, (datetime.now() - dt).total_seconds() / 86400.0)
    return max(0.2, 1.0 - days * 0.01)


def recall_candidates(ms, q, project=None, top_k=config.RECALL_TOP_K, assoc_queries=None,
                      recency=0, scene=None, goal=0, state=None, tilt=False, max_turn=None):
    """并行三路召回 + RRF 融合 + 时间加成，返回候选（已按融合分排序）。
    max_turn：评测回放用——只让 turn 序号 < max_turn 的节点进候选池，等价于"库里当时
    只有 cutoff 之前的历史"。不传时行为与旧版完全一致。
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

    def allow(node):
        """max_turn 时只允许"带 turn 标签且序号 < max_turn"的节点进候选池。
        无 turn 标签的节点（scheduler 自动抽取的二手记忆）一律排除——增量灌库的
        真实状态里并不存在它们，混进来会污染回放对照。"""
        if max_turn is None:
            return True
        tt = _node_turn(node)
        return tt is not None and tt < max_turn

    def ensure(nid, node):
        if nid not in cands:
            cands[nid] = {"node": node, "vector_score": 0.0, "fts_score": 0.0,
                          "causal": 0, "related": 0, "assoc": 0, "scene": 0, "goal": 0,
                          "goal_block": 0, "state": 0, "anchor": 0, "rrf": 0.0, "time_bonus": 1.0,
                          "recall_score": 0.0}
        return cands[nid]

    # ① vector 向量路
    where = {"project": project} if project else None
    # max_turn 时多取几倍候选，剔除 cutoff 之后的轮次后再补足 RECALL_TOP_K
    vec_pool = config.RECALL_TOP_K * 4 if max_turn is not None else config.RECALL_TOP_K
    vec_hits = ms.vector.query(q, top_k=vec_pool, where=where)
    vec_rank = 0
    vec_in = []
    for hit in vec_hits:
        node = ms.graph.get_node(hit["id"])
        if not node or node.status == "obsolete" or not allow(node):
            continue
        if vec_rank >= config.RECALL_TOP_K:
            break
        vec_rank += 1
        vec_in.append(hit)
        rec = ensure(hit["id"], node)
        rec["vector_score"] = hit["score"]
        rec["rrf"] += 1.0 / (60.0 + vec_rank)

    # ② fts 关键词路（字面命中）
    fts_hits = _fts_recall(ms, q, project=project, top_k=config.RECALL_TOP_K, max_turn=max_turn)
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
    for hit in vec_in[:10]:
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
            if not nn or nn.status == "obsolete" or not allow(nn):
                continue
            causal_rank += 1
            rec = ensure(nn.id, nn)
            rec["causal"] += 1
            rec["rrf"] += 1.0 / (60.0 + causal_rank)

    # ③b related 语义扩散路（[UPD 2026-11-03] 新增）：从同一批种子沿 related 边
    #    双向扩散 1 跳并入 RRF——补 causal 路的盲区。related 边由 auto_link 自动建
    #    （语义关联），此前检索不沿其扩散，导致"搜显卡算力解锁带不出挖矿实测"
    #    这类同事件多口径记忆分离；开启后命中节点直接带出语义邻居。
    if config.RELATED_DIFFUSE_ENABLED:
        related_rank = 0
        min_strength = float(getattr(config, "RELATED_DIFFUSE_MIN_STRENGTH", 0.0))
        weight = float(getattr(config, "RELATED_DIFFUSE_WEIGHT", 1.0))
        for nid in causal_seeds:
            for e in ms.graph.out_edges(nid):
                if e.edge_type != "related":
                    continue
                # [FIX 2026-11-13 P0-2] 只扩散强度够的边：auto_link 的 0.45-0.55 弱边
                # 实测 20 条里 7 条完全无关，全额扩散等于往候选池灌噪声。
                if float(getattr(e, "strength", 1.0) or 0) < min_strength:
                    continue
                nn = ms.graph.get_node(e.to_id)
                if not nn or nn.status == "obsolete" or not allow(nn):
                    continue
                related_rank += 1
                rec = ensure(nn.id, nn)
                rec["related"] += 1
                # [FIX 2026-11-13 P0-2b] 同一候选只计一次扩散分量：原实现每命中一次就累加，
                # 热门邻居被 20 个种子（vec top10 + fts top10）反复命中后 RRF 叠到 0.02+，
                # 直接顶掉向量第 1 名——这正是"关掉扩散反而更准"的机制。
                if rec["related"] == 1:
                    # [FIX 2026-11-13 P0-2c] 扩散分单独记账，不并进主 RRF：
                    #   主路候选（vector/fts/anchor 命中）的分数完全不受扩散影响，
                    #   扩散分只用来把"主路一条没召回到"的邻居补进候选池（只补位）。
                    rec["related_rrf"] = rec.get("related_rrf", 0.0) + weight * (1.0 / (60.0 + related_rank))
                if related_rank >= config.RELATED_DIFFUSE_MAX:
                    break
            if related_rank >= config.RELATED_DIFFUSE_MAX:
                break
            for e in ms.graph.in_edges(nid):
                if e.edge_type != "related":
                    continue
                if float(getattr(e, "strength", 1.0) or 0) < min_strength:
                    continue
                nn = ms.graph.get_node(e.from_id)
                if not nn or nn.status == "obsolete" or not allow(nn):
                    continue
                related_rank += 1
                rec = ensure(nn.id, nn)
                rec["related"] += 1
                # [FIX 2026-11-13 P0-2b] 同一候选只计一次扩散分量：原实现每命中一次就累加，
                # 热门邻居被 20 个种子（vec top10 + fts top10）反复命中后 RRF 叠到 0.02+，
                # 直接顶掉向量第 1 名——这正是"关掉扩散反而更准"的机制。
                if rec["related"] == 1:
                    # [FIX 2026-11-13 P0-2c] 扩散分单独记账，不并进主 RRF：
                    #   主路候选（vector/fts/anchor 命中）的分数完全不受扩散影响，
                    #   扩散分只用来把"主路一条没召回到"的邻居补进候选池（只补位）。
                    rec["related_rrf"] = rec.get("related_rrf", 0.0) + weight * (1.0 / (60.0 + related_rank))
                if related_rank >= config.RELATED_DIFFUSE_MAX:
                    break
            if related_rank >= config.RELATED_DIFFUSE_MAX:
                break

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
                if not nn or nn.status == "obsolete" or not allow(nn):
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
                if not nn or nn.status == "obsolete" or not allow(nn):
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
                if not nn or nn.status == "obsolete" or not allow(nn):
                    continue
                state_rank += 1
                rec = ensure(nn.id, nn)
                rec["state"] = rec.get("state", 0) + 1
                rec["rrf"] += config.STATE_SEARCH_WEIGHT * (1.0 / (60.0 + state_rank))

    # ⑧ anchor 实体钩子路（[UPD 2026-11-12] 借鉴"下意识"机制）：query 里出现钩子词
    #    → 直接带出写入时绑定该词的节点。运行时只做字符串切分 + 查表，不 embed、
    #    不调模型、不遍历图；命中强度按"钩子覆盖率"折算，避免泛词淹没向量结果。
    anchor_rank = 0
    if getattr(config, "ANCHOR_ENABLED", False):
        try:
            hooks = anchors.hook_recall(ms, q, project=project)
        except Exception:
            hooks = []
        for nid, hits, cov, strength in hooks:
            nn = ms.graph.get_node(nid)
            if not nn or nn.status == "obsolete" or not allow(nn):
                continue
            anchor_rank += 1
            rec = ensure(nn.id, nn)
            rec["anchor"] = hits
            rec["anchor_strength"] = strength
            rec["rrf"] += config.ANCHOR_SEARCH_WEIGHT * cov * (1.0 / (60.0 + anchor_rank))

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
        if not base and rec.get("related_rrf"):
            # 主路一条没命中、只由 related 扩散带出来的候选：用扩散分兜底（只补位）
            base = rec["related_rrf"]
        if rec.get("anchor"):
            # [FIX 2026-11-13 P0-2] 钩子加成从"定值 0.03"改为"相对加成 + 小额绝对底"：
            #   乘性项只放大节点已有的排序地位（锚定 = 确认，不是凭空创造排序）；
            #   绝对底 0.004 只够把纯钩子命中拉进候选池，拉不到前排。
            #   旧实现 0.03 相对 RRF 基准 1/61≈0.0164 是 +83%，实测挤掉 10 条本该第一的答案。
            s = min(1.0, float(rec.get("anchor_strength", 0) or 0) / 4.0)
            base = base * (1.0 + config.ANCHOR_BOOST_REL * s) + config.ANCHOR_BOOST_ABS * s
        if recency:
            in_window = 0.0
            ts = _parse_dt_any(getattr(node, "timestamp", None))
            if ts is not None:
                days_ago = max(0.0, (datetime.now() - ts).total_seconds() / 86400.0)
                if days_ago <= window_days:
                    in_window = 0.15
            rec["recall_score"] = base * (0.4 + 0.6 * rec["time_bonus"]) + in_window
        else:
            rec["recall_score"] = base * (0.6 + 0.4 * rec["time_bonus"])
        if tilt_on:
            imp = getattr(node, "importance", None) or "reference"
            boost = config.TILT_IMPORTANCE_BOOST.get(imp, 1.0)
            rec["tilt_boost"] = boost
            rec["recall_score"] = rec["recall_score"] * boost
        out.append(rec)
    # [FIX 2026-11-13 P0-2c] related 扩散只做"补位"，不参与主排序：
    #   实测（134 题自召回）关掉扩散 @1=53.0%，开着 @1=34.3%——扩散把 22pt 的正确答案
    #   挤出了 top1。根因不是权重大小，而是"仅由扩散召回的候选"和主路候选同池竞争。
    #   改成分段排序（key 见 _final_order_key）：主路命中优先，仅扩散召回的排其后，
    #   仍能补足 @10 的召回，但抢不走 top1。
    out.sort(key=_final_order_key)
    return out[:top_k]


def _final_order_key(c):
    """最终排序 key：有任何主路命中的候选优先，仅由 related 扩散召回的候选排在其后。

    [FIX 2026-11-13 P0-2c] 扩散只补位（补 @10 的召回），不参与主排序抢 top1。
    """
    has_main = bool(c.get("vector_score") or c.get("fts_score") or c.get("anchor")
                    or c.get("causal") or c.get("assoc") or c.get("scene")
                    or c.get("goal") or c.get("goal_block") or c.get("state"))
    return (0 if has_main else 1, -c["recall_score"])


def rerank(ms, cands, top_n=config.SEARCH_LIMIT_DEFAULT):
    """精排：按 RRF 融合分（含时间加成）取 Top N，保持"主路优先 / 扩散补位"的分段。"""
    cands.sort(key=_final_order_key)
    return cands[:top_n]


def _evidence_kind(r):
    """[UPD 2026-11-12] 一条命中的证据成色：experience / record / hint。

    experience = 已转正的决策、规则、结论、坑——可以当依据说"就是这样"；
    record     = 事件/状态/待办这类一手记录——可以答，但必须标明"这是一条记录，
                 不是结论"（否则就是拿流水账冒充经验）；
    hint       = 其余未转正内容——只能当线索，不能说成答案。
    """
    if r.get("evidence_level") == "experience":
        return "experience"
    nt = ((r.get("node") or {}).get("node_type") or "")
    if nt in getattr(config, "GRADE_RECORD_TYPES", set()):
        return "record"
    return "hint"


def judge_verdict(results):
    """[UPD 2026-11-12] 检索端拒答判据：库里没有，就直说没有。

    返回 (verdict, confidence, reason)：
      answer   = 有明确最佳命中，且最佳命中是转正经验（可作依据）
      record   = 最佳命中是一手记录（事件/状态/待办），排序明确但只能当记录答
      hint     = 命中了但只能当线索（分数挤在一起 / 最佳命中是未转正内容）
      no_match = 库内没有相关内容

    判据依次收口：绝对地板 → 字面/锚定支撑 → 证据成色 → 相对间隔。
    """
    if not getattr(config, "VERDICT_ENABLED", False):
        return "answer", 1.0, "verdict_disabled"
    if not results:
        return "no_match", 0.0, "no_results"
    scores = [float(r.get("score", 0.0) or 0.0) for r in results]
    top1 = scores[0]
    top2 = scores[1] if len(scores) > 1 else 0.0
    margin = (top1 / top2) if top2 > 1e-9 else 99.0
    # [UPD 2026-11-12] 判据要求"最佳命中本身成色够"，不能拿 top3 里别处的经验
    # 给一条 hint 级流水账背书——否则 top1 是事件记录、却对外声称"这是答案"，自相矛盾。
    top1_kind = _evidence_kind(results[0])
    if top1 < config.VERDICT_ABS_FLOOR:
        return "no_match", round(min(1.0, top1 / config.VERDICT_ABS_FLOOR), 4), "below_floor"
    conf = round(min(1.0, top1 / (config.VERDICT_ABS_FLOOR * 5.0)), 4)
    # [UPD 2026-11-12] 纯向量猜测不算"库里有"：top5 里既无实体钩子命中、也无整串字面命中，
    # 说明只是语义空间里"看着近"，库里其实没有这条内容 → 直接拒答，不给线索级答案。
    # [FIX 2026-11-13 P0-5] 支撑必须由 top1 自己给出，不能让队列里别的条目背书。
    #   原写法 any(... for r in results[:5])：top5 里任意一条有字面/锚点就算"库里有"，
    #   纯向量猜出的 top1 被别人的命中兜住也能判 answer。实测"火星殖民计划第三阶段预算"
    #   （库内为零）→ answer/ok，top1 lexical=False、fts=0.0、anchor=1、score=0.0226。
    #   v1（只认字面/锚点）会误杀"记忆系统为什么用三层架构"（score=0.0290 但用词不重合）；
    #   故改为三条件任一，兼顾"字面不重合但确实命中"：
    #     a) 字面命中 ≥ VERDICT_LEXICAL_MIN
    #     b) 锚点词命中 ≥ VERDICT_ANCHOR_MIN 个
    #     c) 融合分 ≥ VERDICT_SELF_SCORE_MIN（0.025 = 地板的 1.25 倍）
    #   回放：库内 40 条真实节点 query 零误杀；库外 10 条中仅"火星殖民"一条被修正为 no_match。
    top1 = results[0]
    top1_self = (float(top1.get("fts_sim", 0) or 0) >= config.VERDICT_LEXICAL_MIN
                 or int(top1.get("anchor_hits", 0) or 0) >= getattr(config, "VERDICT_ANCHOR_MIN", 2)
                 or float(top1.get("score", 0) or 0) >= getattr(config, "VERDICT_SELF_SCORE_MIN", 0.025))
    if not top1_self:
        return "no_match", conf, "top1_no_self_support"
    if config.VERDICT_EVIDENCE_REQUIRED and top1_kind != "experience":
        # 记录档：top1 是一手记录，且排序明确 → 可以答，但标记为记录
        if top1_kind == "record" and margin >= config.VERDICT_MARGIN_MIN:
            return "record", conf, "record_only"
        if top1_kind == "record":
            return "hint", conf, "margin_flat"
        return "hint", conf, "top1_not_evidence"
    if margin < config.VERDICT_MARGIN_MIN:
        return "hint", conf, "margin_flat"
    return "answer", conf, "ok"


def search(ms, q, limit=config.SEARCH_LIMIT_DEFAULT, project=None, with_causal=True,
           recency=0, scene=None, goal=0, state=None, tilt=False, touch=True, max_turn=None):
    """完整检索：三路召回 → RRF 融合 → 时间加成 → 因果链（causes/effects 各取 3 层）。
    recency=1：时间加权模式；scene=：场景过滤检索；goal=1：目标导向重排；
    state=：状态检索；tilt=True：注意力倾斜（活跃目标自动感知 + importance 倾斜，
    只改排序不删内容，见 recall_candidates）。
    touch=False：只读检索（不累加 access_count），用于评测回放 / A-B / 事后复盘。
    max_turn=N：只检索 turn 序号 < N 的历史（评测回放专用，等价于增量灌库时的可见范围）。"""
    cands = recall_candidates(ms, q, project=project, recency=recency, scene=scene,
                              goal=goal, state=state, tilt=tilt, max_turn=max_turn)
    top = rerank(ms, cands, top_n=limit)
    results = []
    for c in top:
        node = c["node"]
        if touch:
            ms.graph.touch(node.id)  # 检索即访问，计入 causal_factor（只读模式下跳过）
        item = {"node": node.to_dict(), "score": round(c["recall_score"], 4),
                "weight_zone": ("Core" if node.weight >= config.CORE_MIN else
                                "Working" if node.weight >= config.WORKING_MIN else
                                "Cold" if node.weight >= config.COLD_MIN else "Archive"),
                # [UPD 2026-11-12] 这条能不能当依据：experience=可作依据 / record=一手记录 / hint=只能当线索
                "evidence_level": getattr(node, "grade", None) or config.GRADE_DEFAULT,
                "skill_candidate": skill_candidate(node),
                "anchor_hits": int(c.get("anchor", 0) or 0),
                # 字面/实体支撑：只有向量"看着像"不算证据（[UPD 2026-11-12] 拒答判据用）
                "lexical_hit": float(c.get("fts_score", 0) or 0) >= config.VERDICT_LEXICAL_MIN,
                "vector_sim": round(float(c.get("vector_score", 0) or 0), 4),
                "fts_sim": round(float(c.get("fts_score", 0) or 0), 4)}
        if "tilt_boost" in c:
            item["tilt_boost"] = c["tilt_boost"]
        if with_causal:
            item["causes"] = ms.causal.get_causes(node.id)
            item["effects"] = ms.causal.get_effects(node.id)
        item["evidence_kind"] = _evidence_kind(item)
        results.append(item)
    verdict, conf, reason = judge_verdict(results)
    return {"query": q, "limit": limit, "engine": "recall+rrf(V6,3路+anchor)",
            "tilted": bool(tilt and config.TILT_ENABLED),
            "verdict": verdict, "confidence": conf, "verdict_reason": reason,
            "results": results}


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
                                "Cold" if node.weight >= config.COLD_MIN else "Archive"),
                "evidence_level": getattr(node, "grade", None) or config.GRADE_DEFAULT,
                "skill_candidate": skill_candidate(node),
                "anchor_hits": int(c.get("anchor", 0) or 0),
                # 字面/实体支撑：只有向量"看着像"不算证据（[UPD 2026-11-12] 拒答判据用）
                "lexical_hit": float(c.get("fts_score", 0) or 0) >= config.VERDICT_LEXICAL_MIN,
                "vector_sim": round(float(c.get("vector_score", 0) or 0), 4),
                "fts_sim": round(float(c.get("fts_score", 0) or 0), 4)}
        if "tilt_boost" in c:
            item["tilt_boost"] = c["tilt_boost"]
        item["causes"] = ms.causal.get_causes(node.id)
        item["effects"] = ms.causal.get_effects(node.id)
        item["evidence_kind"] = _evidence_kind(item)
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
    verdict, conf, reason = judge_verdict(results)
    if verdict == "no_match" and raw:
        # 原文层能逐字捞到东西，就不是"库里没有"，只是还没成经验
        verdict, reason = "hint", "verbatim_only"
    return {"query": q, "mode": mode, "engine": "recall+rrf→verbatim(强召回,assoc可选)",
            "verdict": verdict, "confidence": conf, "verdict_reason": reason,
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
            ts = _parse_dt_any(getattr(n, "timestamp", None))
            days = 0.0 if ts is None else max(0.0, (datetime.now() - ts).total_seconds() / 86400.0)
            w = w * (0.5 ** (days / float(half_life_days)))
        if w < threshold:
            out.append({"id": n.id, "content": n.content[:80], "weight": round(w, 3),
                        "node_type": n.node_type, "project": n.project})
    out.sort(key=lambda x: x["weight"])
    return out
