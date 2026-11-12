# -*- coding: utf-8 -*-
"""实体钩子层（anchor hook）—— 2026-11-12

设计要点（对应"下意识"机制）：
1. 写入时建表：实体/关键词 → 节点 的对应关系在写入那一刻就落到 anchors 表里，
   不是检索时现算的。
2. 准入闸门：只被单个节点用到的词是"私有词"，不进路由表；被 >=ANCHOR_MIN_DF 个
   节点共用才升级为钩子；出现在 >ANCHOR_MAX_DF 个节点上的词太泛（"记忆""系统"），
   检索时按 IDF 直接丢弃。
3. 运行时零开销：检索只做"切 n-gram + 查表"，不 embed、不调模型、不遍历图。

表结构（core.py init_db 建的）：
    anchors(term, node_id, scene, project, hits, weight, updated_at)
    PRIMARY KEY(term, node_id)
"""
import re
import threading
import time
from datetime import datetime

# [2026-11-13] 命中热度写回开关。默认开；压测时可置 False 以分离「写锁成本」与「查询成本」。
_HITS_WRITE = True
# 热度不逐次落盘：先在内存攒，够量或够久再一次性写。检索路径因此不承担写锁成本。
_HITS_BUF = {}
_HITS_BUF_LOCK = threading.Lock()
_HITS_FLUSH_EVERY = 200      # 攒够这么多不同词就写
_HITS_FLUSH_CALLS = 30       # 或累计这么多次检索就写
_HITS_FLUSH_SECS = 600       # 或距上次写超过这么久（有积压时）就写
_HITS_CALLS = 0
_HITS_LAST_FLUSH = time.time()

import config

# 中文连续串 / 英文与型号（RTX 4070 Ti SUPER、bge-small-zh-v1.5）
_TOKEN = re.compile(r"[\u4e00-\u9fff]{2,12}|[A-Za-z][A-Za-z0-9_\-\.]{1,24}")
_QUOTED = re.compile(r"[《【「『\"“]([^》】」』\"”]{2,20})[》】」』\"”]")
_STOP = {str(s).casefold() for s in config.ANCHOR_STOPWORDS}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ok(term):
    if not term:
        return False
    t = str(term).strip()
    # 中文按字数卡（12 字以上基本是整句），拉丁型号词可长到 24（bge-small-zh-v1.5 就 18 个字符）
    limit = 24 if _has_latin(t) else config.ANCHOR_MAX_TERM_LEN
    if len(t) < config.ANCHOR_MIN_TERM_LEN or len(t) > limit:
        return False
    if t.casefold() in _STOP:
        return False
    if t.isdigit():
        return False
    return True


def _scene_of(node):
    """场景标签：优先取 tags 里的 scene:xxx，退化为项目分区。"""
    for t in (getattr(node, "tags", None) or []):
        if isinstance(t, str) and t.startswith("scene:"):
            return t[6:].strip()
    return getattr(node, "project", "") or "default"


def _has_latin(t):
    """含 ASCII 字母或数字：型号/项目名/英文术语（Hive、125B、bge-small-zh）。"""
    return any(c.isascii() and c.isalnum() for c in str(t or ""))


def _df_limit(term):
    """不同类型的词给不同的 df 上限：拉丁词天然高频，中文高频基本都是口水话。"""
    return config.ANCHOR_MAX_DF_LATIN if _has_latin(term) else config.ANCHOR_MAX_DF_CN


def extract_terms(node):
    """从一个节点抽候选钩子词。候选不等于钩子——还要过"多节点共用"这道闸门。

    [UPD 2026-11-12 策略修正] 中文词只从 tags / 引号书名号里取，不从正文自由切 n-gram：
    实测闲聊语料上自由切词必然产出口水话（"救命""也是""服了"），把路由表淹掉。
    正文只贡献拉丁/数字 token（型号、项目名、英文术语），这类词几乎不会误伤。
    """
    terms = []

    def add(t):
        t = str(t or "").strip()
        if _ok(t) and t not in terms:
            terms.append(t)

    content = (getattr(node, "content", "") or "").strip()
    # 实体节点的内容本身就是专名（"SuperMem"、"RTX 4070 Ti SUPER"）
    if (getattr(node, "node_type", "") or "") in config.ANCHOR_ENTITY_TYPES and content:
        add(content[:24 if _has_latin(content) else config.ANCHOR_MAX_TERM_LEN])
    # 标签：写入方本来就在打的词，质量最高（中文锚点主要来源）
    for t in (getattr(node, "tags", None) or []):
        add(t)
    # 书名号 / 引号里的引用名
    for m in _QUOTED.finditer(content):
        add(m.group(1))
    # 正文里的拉丁字母/型号 token
    for m in _TOKEN.finditer(content):
        g = m.group(0)
        if _has_latin(g):
            add(g)
    return terms[:config.ANCHOR_MAX_TERMS_PER_NODE]


def index_node(ms, node):
    """单节点增量入表（写入路径调用）。返回写入的词数。"""
    if not getattr(config, "ANCHOR_ENABLED", False) or node is None:
        return 0
    terms = extract_terms(node)
    if not terms:
        return 0
    scene = _scene_of(node)
    rows = [(t, node.id, scene, getattr(node, "project", "default") or "default",
             0, _term_weight(node, t), _now()) for t in terms]
    conn = ms.graph.conn
    try:
        with ms.graph._lock:
            conn.executemany(
                "INSERT OR REPLACE INTO anchors(term,node_id,scene,project,hits,weight,updated_at)"
                " VALUES(?,?,?,?,?,?,?)", rows)
            conn.commit()
        _enforce_ceil(ms)
    except Exception:
        return 0
    return len(rows)


def _term_weight(node, term):
    """词与该节点的"绑定强度"——锚定表里最容易出错的就是这里：
    同一个词可能只是一个节点顺手打的 tag，语义上并不相关（实测："SuperMem"
    被 59 个节点打标签，其中多数只是提了一嘴，检索时会被误当成主题命中）。

    1.0 = 只出现在 tags（弱绑定）
    3.0 = 出现在内容开头（标题位）或实体节点的正文就是这个词（强绑定）
    +0.5 = 正文反复出现（>=2 次）
    """
    content = str(getattr(node, "content", "") or "")
    cl, tl = content.casefold(), str(term or "").casefold()
    w = 1.0
    pos = cl.find(tl) if tl else -1
    if 0 <= pos < 20:
        # 词的起始位置落在开头（标题位）——注意按起始位置判，不能要求整词落在前 20 字内，
        # 否则 "AlphaHook-2026" 这种跨 20 字边界的长型号词会被误判成弱绑定
        w = 3.0
    elif ((getattr(node, "node_type", "") or "") in config.ANCHOR_ENTITY_TYPES
          and content.strip().casefold() == tl):
        w = 3.0
    if tl and cl.count(tl) >= 2:
        w += 0.5
    return min(3.5, w)


def build_index(ms, prune=True):
    """全量重建钩子表（用于存量库一次性建表）。返回统计。"""
    conn = ms.graph.conn
    with ms.graph._lock:
        conn.execute("DELETE FROM anchors")
        conn.commit()
    nodes = ms.graph.list_nodes(limit=10 ** 7)
    rows = []
    for n in nodes:
        if n.status == "obsolete":
            continue
        scene = _scene_of(n)
        for t in extract_terms(n):
            rows.append((t, n.id, scene, n.project or "default", 0, _term_weight(n, t), _now()))
    with ms.graph._lock:
        for i in range(0, len(rows), 500):
            conn.executemany(
                "INSERT OR REPLACE INTO anchors(term,node_id,scene,project,hits,weight,updated_at)"
                " VALUES(?,?,?,?,?,?,?)", rows[i:i + 500])
        conn.commit()

    kept_terms = 0
    if prune:
        kept_terms = _prune(conn)
    total_terms = conn.execute("SELECT COUNT(DISTINCT term) FROM anchors").fetchone()[0]
    total_rows = conn.execute("SELECT COUNT(*) FROM anchors").fetchone()[0]
    return {"nodes": len(nodes), "candidate_rows": len(rows), "ohook_terms": kept_terms,
            "terms": total_terms, "rows": total_rows}


def _prune(conn):
    """准入闸门：删掉只被 1 个节点用到的私有词，以及过泛的词（按词类型分级上限）。"""
    conn.execute("DELETE FROM anchors WHERE term IN "
                 "(SELECT term FROM anchors GROUP BY term HAVING COUNT(*) < ?)",
                 (config.ANCHOR_MIN_DF,))
    dfs = conn.execute("SELECT term, COUNT(*) AS c FROM anchors GROUP BY term").fetchall()
    too_wide = [r["term"] for r in dfs if int(r["c"]) > _df_limit(r["term"])]
    for i in range(0, len(too_wide), 200):
        batch = too_wide[i:i + 200]
        conn.execute("DELETE FROM anchors WHERE term IN (%s)" % _placeholders(len(batch)), batch)
    conn.commit()
    return conn.execute("SELECT COUNT(DISTINCT term) FROM anchors").fetchone()[0]


def _enforce_ceil(ms):
    """表膨胀保护：超上限时按「共享度 → 绑定强度 → 使用热度 → 更新新度」淘汰尾部。

    [2026-11-13] 原排序只有 hits/updated_at，而 hits 长期恒为 0，等价于纯按时间淘汰，
    迁移时建的高价值实体锚点（Strata/Hive 等）会最先被新记忆的私有词挤掉。
    现按四级淘汰，逐层兜住：
      1. df 低的先删 —— 只被 1 个节点用到的私有词是垃圾，这本来就是准入闸门（MIN_DF）的本意
      2. weight 低的先删 —— tags 蹭到（1.0）让位于标题位/实体名（3.0）
      3. hits 低的是没在检索里被用过的
      4. 最后才轮到最老的

    [2026-11-13] 回收不再删到刚好卡上限，而是一次删到上限以下留出缓冲（默认 95%），
    否则每写一条新记忆就要重新跑一次排序回收。缓冲量由 ANCHOR_CEIL_SLACK_RATIO 控制。
    """
    conn = ms.graph.conn
    total = conn.execute("SELECT COUNT(*) FROM anchors").fetchone()[0]
    if total <= config.ANCHOR_MAX_TABLE:
        return
    ratio = getattr(config, "ANCHOR_CEIL_SLACK_RATIO", 0.05)
    target = int(config.ANCHOR_MAX_TABLE * (1.0 - ratio))
    target = max(1, min(target, config.ANCHOR_MAX_TABLE))
    excess = total - target
    if excess <= 0:
        return
    with ms.graph._lock:
        conn.execute(
            "DELETE FROM anchors WHERE rowid IN ("
            " SELECT a.rowid FROM anchors a"
            " JOIN (SELECT term, COUNT(*) AS dfc FROM anchors GROUP BY term) t"
            "   ON a.term = t.term"
            " ORDER BY t.dfc ASC, a.weight ASC, a.hits ASC, a.updated_at ASC"
            " LIMIT ?)",
            (excess,))
        conn.commit()


def _query_grams(q):
    """query 切成 2..ANCHOR_MAX_TERM_LEN 的 n-gram（纯字符串操作，成本可忽略）。"""
    s = re.sub(r"\s+", " ", (q or "").strip())
    if not s:
        return []
    grams = []
    n = len(s)
    for k in range(config.ANCHOR_MIN_TERM_LEN, min(config.ANCHOR_MAX_TERM_LEN, n) + 1):
        for i in range(0, n - k + 1):
            g = s[i:i + k].strip()
            if not g:
                continue
            if len(g) > 2 and " " in g[1:-1]:
                continue
            if _ok(g):
                grams.append(g)
    return list(dict.fromkeys(grams))


def _placeholders(n):
    return ",".join(["?"] * n)


def hook_recall(ms, query, project=None, top_k=None):
    """检索期查表：返回 [(node_id, hits, idf_weight), ...]，按命中强度降序。

    两步查询：先按 df 过滤掉私有词和过泛词（这就是"准入闸门"的检索侧复用），
    再按命中节点聚合。
    """
    if not getattr(config, "ANCHOR_ENABLED", False):
        return []
    grams = _query_grams(query)
    if not grams:
        return []
    top_k = top_k or config.ANCHOR_TOP_K
    conn = ms.graph.conn
    ph = _placeholders(len(grams))
    try:
        df_rows = conn.execute(
            f"SELECT term, COUNT(*) AS df FROM anchors WHERE term IN ({ph}) GROUP BY term",
            grams).fetchall()
        good = {r["term"]: r["df"] for r in df_rows
                if config.ANCHOR_MIN_DF <= r["df"] <= _df_limit(r["term"])}
        if not good:
            return []
        good_ph = _placeholders(len(good))
        proj_clause = ""
        args = list(good.keys())
        if project:
            proj_clause = " AND project=?"
            args.append(project)
        rows = conn.execute(
            f"SELECT node_id, COUNT(*) AS hits, SUM(weight) AS w FROM anchors "
            f"WHERE term IN ({good_ph}){proj_clause} GROUP BY node_id "
            f"ORDER BY w DESC LIMIT ?",
            args + [top_k]).fetchall()
    except Exception:
        return []

    total = float(len(good))
    out = []
    for r in rows:
        hits = int(r["hits"] or 0)
        if hits < config.ANCHOR_MIN_HITS_KEEP:
            continue
        strength = float(r["w"] or 0)
        # 覆盖率按"绑定强度"归一：强绑定（标题位、专名节点）满格 3.0/词
        cover = min(1.0, strength / (total * 3.0)) if total else 0.0
        out.append((r["node_id"], hits, round(cover, 4), round(strength, 3)))
    # [2026-11-13] 命中累加热度：hits 此前恒为 0，导致 _enforce_ceil 只能按时间淘汰。
    # 现在攒在内存里批量写，检索路径不承担写锁 + commit 成本（实测逐次写在 8 并发下多花 16ms）。
    if out and _HITS_WRITE:
        _buffer_hits(ms, good.keys())
    return out


def _buffer_hits(ms, terms):
    """累积命中计数，够量 / 够次 / 够久再一次性落盘。失败回灌，不影响检索。"""
    global _HITS_CALLS
    need_flush = False
    with _HITS_BUF_LOCK:
        for t in terms:
            _HITS_BUF[t] = _HITS_BUF.get(t, 0) + 1
        _HITS_CALLS += 1
        if (len(_HITS_BUF) >= _HITS_FLUSH_EVERY
                or _HITS_CALLS >= _HITS_FLUSH_CALLS
                or time.time() - _HITS_LAST_FLUSH > _HITS_FLUSH_SECS):
            need_flush = True
    if need_flush:
        flush_hits(ms)


def flush_hits(ms):
    """把内存里的命中计数写回表：一个事务搞定，避免逐词 commit。"""
    global _HITS_BUF, _HITS_CALLS, _HITS_LAST_FLUSH
    with _HITS_BUF_LOCK:
        buf = dict(_HITS_BUF)
        _HITS_BUF.clear()
        _HITS_CALLS = 0
        _HITS_LAST_FLUSH = time.time()
    if not buf:
        return
    try:
        with ms.graph._lock:
            conn = ms.graph.conn
            conn.executemany("UPDATE anchors SET hits=hits+? WHERE term=?",
                             [(c, t) for t, c in buf.items()])
            conn.commit()
    except Exception:
        with _HITS_BUF_LOCK:
            for t, c in buf.items():
                _HITS_BUF[t] = _HITS_BUF.get(t, 0) + c


def stats(ms):
    """钩子表体检：词数、行数、覆盖节点数、Top 词。"""
    conn = ms.graph.conn
    terms = conn.execute("SELECT COUNT(DISTINCT term) FROM anchors").fetchone()[0]
    rows = conn.execute("SELECT COUNT(*) FROM anchors").fetchone()[0]
    nodes = conn.execute("SELECT COUNT(DISTINCT node_id) FROM anchors").fetchone()[0]
    top = conn.execute(
        "SELECT term, COUNT(*) c FROM anchors GROUP BY term ORDER BY c DESC LIMIT 15").fetchall()
    return {"terms": terms, "rows": rows, "covered_nodes": nodes,
            "top_terms": [(r["term"], r["c"]) for r in top]}
