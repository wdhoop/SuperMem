# -*- coding: utf-8 -*-
"""
超级记忆系统 —— 核心层 core.py
按《超级记忆系统-架构说明书》(2026-09-30) 重建。
含：数据类(Node/Edge/Episode) / GraphStore(图读写/去重/衰减/时间旅行) /
    VectorSearch(BGE 语义检索/对账) / CausalEngine(因果链 BFS) / 状态机 / 遗忘曲线。
[FIX] 相对原架构的修复均以 "FIX:" 注释标注。
"""
import json
import math
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta

import config

# ============================= 数据类 =============================

class Node:
    def __init__(self, id=None, content="", node_type="event", status="active",
                 timestamp=None, weight=config.W0, project="default", tags=None,
                 source="", confidence=1.0, created_at=None, last_accessed=None,
                 access_count=0, version_of=None, replaced_by=None, marked_until=None,
                 importance=None, grade=None):
        self.id = id or f"{node_type}_{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"
        self.content = content
        self.node_type = node_type
        self.status = status
        # [FIX 2026-11-13 P2-18] 时间格式统一到秒：原默认 "%Y-%m-%d %H:%M" 与 created_at
        # 的 "%Y-%m-%d %H:%M:%S" 不一致，再叠加 ISO 带 T 和 epoch，库里混了 4 种写法，
        # timeline 区间比较和 episode 聚合都在赌格式。
        self.timestamp = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.weight = weight
        self.project = project
        self.tags = tags or []
        self.source = source
        self.confidence = confidence
        self.created_at = created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.last_accessed = last_accessed
        self.access_count = access_count
        self.version_of = version_of
        self.replaced_by = replaced_by
        self.marked_until = marked_until
        # [UPD 2026-10-20 价值维] importance 价值分级：goal_related / operational / reference
        self.importance = importance or ("reference" if node_type != "goal" else "goal_related")
        # [UPD 2026-11-12 经验转正] grade：hint（线索，不可作依据）/ experience（合格经验，可作依据）
        self.grade = grade or config.GRADE_DEFAULT

    def to_dict(self):
        return {k: getattr(self, k) for k in
                ("id", "content", "node_type", "status", "timestamp", "weight", "project",
                 "tags", "source", "confidence", "created_at", "last_accessed",
                 "access_count", "version_of", "replaced_by", "marked_until", "importance",
                 "grade")}

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: v for k, v in d.items() if k in cls.__init__.__code__.co_varnames})


# ============ 经验转正判定（2026-11-12）============
def _parse_dt(s):
    """尽力解析时间串，失败返回 None。"""
    if not s:
        return None
    s = str(s).strip().replace("T", " ")
    if len(s) >= 19:
        s = s[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def compute_grade(node, now=None):
    """这条记忆能不能当答案的依据。
    experience = 合格经验：类型够硬 + 置信度够 + 被反复用过 + 没过期 / 或状态已确认
    hint       = 线索：只能说明"库里有这么一条"，不可直接当结论引用
    """
    if not getattr(config, "GRADE_ENABLED", False):
        return "experience"
    st = (getattr(node, "status", "") or "").lower()
    if st in config.GRADE_RETIRE_STATUSES:
        return "hint"
    if st in config.GRADE_CERTAIN_STATUSES:
        return "experience"
    if (getattr(node, "node_type", "") or "") not in config.GRADE_EVIDENCE_TYPES:
        return "hint"
    # [UPD 2026-11-12] insight/fact/experiment/milestone 这类短笔记容易被闲聊污染：
    # 实测转正后最短的一批是"你好吗？我很好。""今天天气不错。""缓存满了"（confidence 照样 1.0）。
    # 这类不能当依据，按内容长度挡回 hint。
    if (getattr(node, "node_type", "") or "") in config.GRADE_LEN_CHECK_TYPES:
        txt = (getattr(node, "content", "") or "").strip()
        if len(txt) < config.GRADE_MIN_CONTENT_LEN:
            return "hint"
    try:
        conf = float(getattr(node, "confidence", 0) or 0)
    except (TypeError, ValueError):
        conf = 0.0
    if conf < config.GRADE_EVIDENCE_MIN_CONF:
        return "hint"
    try:
        acc = int(getattr(node, "access_count", 0) or 0)
    except (TypeError, ValueError):
        acc = 0
    if acc < config.GRADE_EVIDENCE_MIN_ACCESS:
        return "hint"
    ref = _parse_dt(getattr(node, "last_accessed", None) or getattr(node, "created_at", None))
    if ref is not None:
        age = ((now or datetime.now()) - ref).days
        if age > config.GRADE_EVIDENCE_MAX_AGE_DAYS:
            return "hint"
    return "experience"


def skill_candidate(node):
    """反复命中到一定次数的合格经验 → skill 候选（只打标，不自动生成经验卡）。"""
    if compute_grade(node) != "experience":
        return False
    try:
        return int(getattr(node, "access_count", 0) or 0) >= int(getattr(config, "GRADE_SKILL_ACCESS", 10**9))
    except (TypeError, ValueError):
        return False


class Edge:
    def __init__(self, id=None, from_id="", to_id="", edge_type="enable", confidence=1.0,
                 source="manual", description="", strength=1.0):
        self.id = id or f"e_{uuid.uuid4().hex[:12]}"
        self.from_id = from_id
        self.to_id = to_id
        self.edge_type = edge_type
        self.confidence = confidence
        self.source = source
        self.description = description
        self.strength = strength

    def to_dict(self):
        return {"id": self.id, "from_id": self.from_id, "to_id": self.to_id,
                "edge_type": self.edge_type, "confidence": self.confidence,
                "source": self.source, "description": self.description, "strength": self.strength}

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: v for k, v in d.items() if k in cls.__init__.__code__.co_varnames})


class Episode:
    def __init__(self, id=None, title="", summary="", scenario="", start_timestamp="",
                 end_timestamp="", node_ids=None, edge_count=0, created_at=None):
        self.id = id or f"ep_{uuid.uuid4().hex[:12]}"
        self.title = title
        self.summary = summary
        self.scenario = scenario
        self.start_timestamp = start_timestamp
        self.end_timestamp = end_timestamp
        self.node_ids = node_ids or []
        self.edge_count = edge_count
        self.created_at = created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self):
        return {"id": self.id, "title": self.title, "summary": self.summary, "scenario": self.scenario,
                "start_timestamp": self.start_timestamp, "end_timestamp": self.end_timestamp,
                "node_ids": self.node_ids, "edge_count": self.edge_count, "created_at": self.created_at}


# ============================= 遗忘曲线 =============================

def _level_of(node):
    if node.status == "permanent" or (node.marked_until and node.marked_until > datetime.now().strftime("%Y-%m-%d %H:%M")):
        return "S"
    return config.TYPE_LEVEL_MAP.get(node.node_type, config.TYPE_LEVEL_DEFAULT)


_TS_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
               "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f")


def _parse_ts(value):
    """统一解析历史遗留的多格式时间戳（缺秒 / ISO / 带时区）。
    [FIX 2026-11-07] 旧实现只认 "%Y-%m-%d %H:%M:%S"，而库内 76.5% 的时间戳是
    "2026-09-30 17:23"（缺秒）这类格式，解析失败后被当成"刚刚创建"（age=0），
    衰减项恒为 1，这批记忆永不衰减。解析失败一律返回 None，不再伪造 now。"""
    if not value:
        return None
    s = str(value).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt
    except ValueError:
        pass
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _age_days(node, now_ts=None):
    now = _parse_ts(now_ts) or datetime.now()
    created = _parse_ts(getattr(node, "created_at", None)) or _parse_ts(getattr(node, "timestamp", None))
    if created is None:
        return 0.0
    return max(0.0, (now - created).total_seconds() / 86400.0)


def _last_active_days(node, now_ts=None):
    """距最近一次激活（写入或访问）的天数。

    [FIX 2026-11-13 P0-1b] 遗忘曲线的 t 原来是「创建至今」，于是任何 C 级事件满 100 天
    必然 e^(-0.03*100)=0.05 触底。实测全库 2149 条里有 1049 条（49%）会被一次性判死，
    而这批节点 access_count 6~108、last_accessed 就在当天——等于把"天天在用的记忆"
    按创建日期沉底。语义上衰减应看「多久没被想起」而不是「多久前写下的」，所以 t 取
    last_accessed / created_at / timestamp 三者的最晚值：被检索过的记忆重新计时。
    """
    now = _parse_ts(now_ts) or datetime.now()
    latest = None
    for attr in ("last_accessed", "created_at", "timestamp"):
        dt = _parse_ts(getattr(node, attr, None))
        if dt is not None and (latest is None or dt > latest):
            latest = dt
    if latest is None:
        return 0.0
    return max(0.0, (now - latest).total_seconds() / 86400.0)


def _access_age_days(node, now_ts=None):
    """距上次访问天数；无访问记录时返回 0（此时 access_count=0，热度项本就为 0，结果一致）。"""
    if not node.last_accessed:
        return 0.0
    now = _parse_ts(now_ts) or datetime.now()
    la = _parse_ts(node.last_accessed)
    if la is None:
        return 0.0
    return max(0.0, (now - la).total_seconds() / 86400.0)


def compute_weight(node, edge_count=0, now_ts=None):
    """遗忘曲线：W(t) = min(1, max(floor, W0 * e^(-λt) * causal_factor * status_mod * newness))
    [FIX 2026-11-07] 两处结构性修正：
      1) 访问热度不再乘进权重。旧实现 heat=access_count*e^(-λ·天)，51 次访问就能把
         causal_factor 顶到上限 2.0 —— 等于"用得越多越权威"，错误记忆靠被反复检索
         自我强化、永不衰减。访问量只保留在检索排序的 access 分量。
      2) causal 上限由 2.0 降到 1.3，保证 0.7 * 1.3 = 0.91 < 1.0，权重不再全部顶格，
         新旧条目之间重新有区分度；新写入的认知另有 7 天新近加成。"""
    level = _level_of(node)
    lam = config.LAMBDA_BY_TYPE[level]
    floor = config.FLOOR_BY_TYPE[level]
    causal_factor = min(1 + config.CAUSAL_FACTOR_RATE * math.log1p(max(edge_count, 0)),
                        config.CAUSAL_FACTOR_MAX)
    status_mod = config.STATUS_MOD.get(node.status, 1.0)
    age = _last_active_days(node, now_ts)
    newness = config.NEWNESS_BOOST if age <= config.NEWNESS_BOOST_DAYS else 1.0
    w = config.W0 * math.exp(-lam * age) * causal_factor * status_mod * newness
    return min(1.0, max(floor, w))


def weight_zone(w):
    if w >= config.CORE_MIN:
        return "Core"
    if w >= config.WORKING_MIN:
        return "Working"
    if w >= config.COLD_MIN:
        return "Cold"
    return "Archive"


# ============================= 状态机 =============================

# [FIX] 显式合法流转表，防止非法跳转
TRANSITIONS = {
    "active":     {"confirmed", "hypothesis", "permanent", "outdated", "obsolete", "disputed", "merged"},
    "confirmed":  {"permanent", "hypothesis", "outdated", "obsolete", "disputed", "merged"},
    "hypothesis": {"confirmed", "outdated", "obsolete", "disputed", "merged"},
    "permanent":  {"outdated", "obsolete"},
    "outdated":   {"obsolete", "merged"},
    "disputed":   {"confirmed", "obsolete", "merged"},
    "merged":     {"obsolete"},
    "obsolete":   set(),
}


def transition_allowed(old, new):
    return new in TRANSITIONS.get(old, set())


# ============================= 图存储 GraphStore =============================

def _load_tags(raw):
    """[FIX 2026-11-13 P1-11] tags 反序列化防双重转义。

    早期写入把已经序列化过的字符串又 json.dumps 了一次，落库成 '"[\\"supermem\\"]"'
    这样的字符串；读出来后再按字符拆，于是 tag_frequency 里出现 '"' 462 次、',' 148 次、
    空格 142 次这种"标签"。这里做幂等归一：最多解两层，非 list/str 一律丢弃。"""
    v = raw
    if isinstance(v, list):
        return [x for x in v if isinstance(x, str) and x.strip()]
    for _ in range(2):
        if not isinstance(v, str):
            break
        s = v.strip()
        if not s:
            return []
        try:
            v = json.loads(s)
        except Exception:
            return [s]
    if isinstance(v, list):
        return [x for x in v if isinstance(x, str) and x.strip()]
    if isinstance(v, str):
        return [v.strip()] if v.strip() else []
    return []


class GraphStore:
    def __init__(self, db_path=None):
        self.db_path = db_path or config.DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=OFF")
        self.init_db()

    # ---------- schema ----------
    def init_db(self):
        with self._lock, self.conn:
            self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS nodes(
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                node_type TEXT NOT NULL DEFAULT 'event',
                status TEXT NOT NULL DEFAULT 'active',
                timestamp TEXT NOT NULL,
                weight REAL NOT NULL DEFAULT 0.5,
                project TEXT NOT NULL DEFAULT 'default',
                tags TEXT NOT NULL DEFAULT '[]',
                source TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 1.0,
                created_at TEXT NOT NULL,
                last_accessed TEXT,
                access_count INTEGER NOT NULL DEFAULT 0,
                version_of TEXT,
                replaced_by TEXT,
                marked_until TEXT,
                importance TEXT NOT NULL DEFAULT 'reference',
                grade TEXT NOT NULL DEFAULT 'hint'
            );
            CREATE TABLE IF NOT EXISTS edges(
                id TEXT PRIMARY KEY,
                from_id TEXT NOT NULL,
                to_id TEXT NOT NULL,
                edge_type TEXT NOT NULL DEFAULT 'enable',
                confidence REAL NOT NULL DEFAULT 1.0,
                source TEXT NOT NULL DEFAULT 'manual',
                description TEXT NOT NULL DEFAULT '',
                strength REAL NOT NULL DEFAULT 1.0
            );
            CREATE TABLE IF NOT EXISTS episodes(
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                scenario TEXT NOT NULL DEFAULT '',
                start_timestamp TEXT,
                end_timestamp TEXT,
                node_ids TEXT NOT NULL DEFAULT '[]',
                edge_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS metadata(
                k TEXT PRIMARY KEY,
                v TEXT
            );
            CREATE TABLE IF NOT EXISTS verbatim(
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                ts TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                node_id TEXT,
                session TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_nodes_status ON nodes(status);
            CREATE INDEX IF NOT EXISTS idx_nodes_timestamp ON nodes(timestamp);
            CREATE INDEX IF NOT EXISTS idx_nodes_project ON nodes(project);
            CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_id);
            CREATE INDEX IF NOT EXISTS idx_edges_to ON edges(to_id);
            CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(edge_type);
            CREATE INDEX IF NOT EXISTS idx_episodes_title ON episodes(title);
            CREATE INDEX IF NOT EXISTS idx_episodes_scenario ON episodes(scenario);
            CREATE INDEX IF NOT EXISTS idx_verbatim_node ON verbatim(node_id);
            CREATE INDEX IF NOT EXISTS idx_verbatim_ts ON verbatim(ts);
            """)
            # [UPD 2026-10-20 价值维] 老库兼容：nodes 表缺 importance 列时补列（幂等）
            try:
                cols = [r["name"] for r in self.conn.execute("PRAGMA table_info(nodes)").fetchall()]
                if "importance" not in cols:
                    self.conn.execute("ALTER TABLE nodes ADD COLUMN importance TEXT NOT NULL DEFAULT 'reference'")
                    self.conn.commit()
                # [UPD 2026-11-12 经验转正] 老库补 grade 列（幂等）
                if "grade" not in cols:
                    self.conn.execute("ALTER TABLE nodes ADD COLUMN grade TEXT NOT NULL DEFAULT 'hint'")
                    self.conn.commit()
            except Exception:
                pass
            # [UPD 2026-11-12 实体钩子层] 写入时建好的锚点路由表：词 → 节点
            try:
                self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS anchors(
                    term TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    scene TEXT NOT NULL DEFAULT '',
                    project TEXT NOT NULL DEFAULT 'default',
                    hits INTEGER NOT NULL DEFAULT 0,
                    weight REAL NOT NULL DEFAULT 1.0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(term, node_id)
                );
                CREATE INDEX IF NOT EXISTS idx_anchors_term ON anchors(term);
                CREATE INDEX IF NOT EXISTS idx_anchors_node ON anchors(node_id);
                """)
                self.conn.commit()
            except Exception:
                pass
            # 初始化运维指标
            meta = self.conn.execute("SELECT k FROM metadata WHERE k='node_count_snapshot'").fetchone()
            if not meta:
                self.conn.execute("INSERT OR IGNORE INTO metadata(k,v) VALUES('node_count_snapshot','0')")
                self.conn.execute("INSERT OR IGNORE INTO metadata(k,v) VALUES('edge_count_snapshot','0')")
                self.conn.execute("INSERT OR IGNORE INTO metadata(k,v) VALUES('last_selfcheck_at','')")

    # ---------- metadata ----------
    def set_meta(self, k, v):
        with self._lock, self.conn:
            self.conn.execute("INSERT OR REPLACE INTO metadata(k,v) VALUES(?,?)", (k, str(v)))

    def get_meta(self, k, default=None):
        row = self.conn.execute("SELECT v FROM metadata WHERE k=?", (k,)).fetchone()
        return row["v"] if row else default

    # ---------- nodes ----------
    def add_node(self, node, dedup=True):
        """新增节点。dedup=True 时做内容前 60 字符 + 完全重复双查（INSERT OR IGNORE）。"""
        # [FIX 2026-11-13 P1-11] 写入端 tags 幂等归一：传进来是字符串（可能已序列化过）
        # 就按列表解析，避免落库时二次转义出新一批 '"[...]"' 脏数据。
        if not isinstance(node.tags, (list, tuple)):
            node.tags = _load_tags(node.tags)
        node.tags = [str(t).strip() for t in node.tags if str(t).strip()]
        with self._lock:
            if dedup:
                dup = self.conn.execute(
                    "SELECT id FROM nodes WHERE content=? OR (substr(content,1,60)=substr(?,1,60) AND length(content)>0)",
                    (node.content, node.content)).fetchone()
                if dup:
                    return dup["id"], False
            node.grade = compute_grade(node)  # [2026-11-12] 写入即判定：新记忆默认只是线索
            self.conn.execute("""INSERT OR IGNORE INTO nodes
                (id,content,node_type,status,timestamp,weight,project,tags,source,confidence,
                 created_at,last_accessed,access_count,version_of,replaced_by,marked_until,importance,grade)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (node.id, node.content, node.node_type, node.status, node.timestamp, node.weight,
                 node.project, json.dumps(node.tags, ensure_ascii=False), node.source, node.confidence,
                 node.created_at, node.last_accessed, node.access_count,
                 node.version_of, node.replaced_by, node.marked_until,
                 getattr(node, "importance", None) or "reference",
                 getattr(node, "grade", None) or config.GRADE_DEFAULT))
            if self.conn.total_changes or True:
                self.conn.commit()
            return node.id, True

    def get_node(self, nid):
        row = self.conn.execute("SELECT * FROM nodes WHERE id=?", (nid,)).fetchone()
        return self._row_to_node(row) if row else None

    # ---------- verbatim 原文层（2026-10-17 新增）----------
    def add_verbatim(self, text, ts=None, source="", node_id=None, session="", vid=None):
        """落一块逐字原文。文本超长自动按 VERBATIM_MAX_LEN 切块。返回 [id, ...]。"""
        import uuid
        ts = ts or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        chunks = [text[i:i + config.VERBATIM_MAX_LEN]
                  for i in range(0, len(text), config.VERBATIM_MAX_LEN)] or [""]
        ids = []
        with self._lock:
            for i, chunk in enumerate(chunks):
                # [FIX 2026-10-17] vid 作为块前缀：多块时带 _i 后缀，避免同 vid 导致 INSERT OR IGNORE 只落首块
                cid = (vid or uuid.uuid4().hex[:12]) if len(chunks) == 1 else \
                      (vid or uuid.uuid4().hex[:12]) + f"_{i}"
                self.conn.execute("""INSERT OR IGNORE INTO verbatim
                    (id,text,ts,source,node_id,session,created_at)
                    VALUES (?,?,?,?,?,?,?)""",
                    (cid, chunk, ts, source, node_id, session, now))
                self.conn.commit()
                ids.append(cid)
        return ids

    def get_verbatim(self, vid):
        row = self.conn.execute("SELECT * FROM verbatim WHERE id=?", (vid,)).fetchone()
        if not row:
            return None
        return {"id": row["id"], "text": row["text"], "ts": row["ts"], "source": row["source"],
                "node_id": row["node_id"], "session": row["session"], "created_at": row["created_at"]}

    def list_verbatim_by_node(self, node_id, limit=20):
        rows = self.conn.execute(
            "SELECT * FROM verbatim WHERE node_id=? ORDER BY ts DESC LIMIT ?",
            (node_id, limit)).fetchall()
        return [{"id": r["id"], "text": r["text"], "ts": r["ts"], "source": r["source"],
                 "node_id": r["node_id"], "session": r["session"]} for r in rows]

    def delete_verbatim(self, vid):
        with self._lock:
            self.conn.execute("DELETE FROM verbatim WHERE id=?", (vid,))
            self.conn.commit()

    def count_verbatim(self):
        return self.conn.execute("SELECT COUNT(*) AS c FROM verbatim").fetchone()["c"]

    def _row_to_node(self, row):
        node = Node(id=row["id"], content=row["content"], node_type=row["node_type"],
                    status=row["status"], timestamp=row["timestamp"], weight=row["weight"],
                    project=row["project"], tags=_load_tags(row["tags"]),
                    source=row["source"], confidence=row["confidence"], created_at=row["created_at"],
                    last_accessed=row["last_accessed"], access_count=row["access_count"],
                    version_of=row["version_of"], replaced_by=row["replaced_by"],
                    marked_until=row["marked_until"],
                    importance=row["importance"] if "importance" in row.keys() else None,
                    grade=row["grade"] if "grade" in row.keys() else None)
        return node

    def list_nodes(self, project=None, limit=100, status=None):
        sql = "SELECT * FROM nodes WHERE 1=1"
        args = []
        if project:
            sql += " AND project=?"
            args.append(project)
        if status:
            sql += " AND status=?"
            args.append(status)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        args.append(limit)
        rows = self.conn.execute(sql, args).fetchall()
        return [self._row_to_node(r) for r in rows]

    def update_node(self, node, fields=None):
        """局部更新节点字段；fields 为要更新的字段名集合，缺省为全部。"""
        allowed = {"content", "node_type", "status", "timestamp", "weight", "project", "tags",
                   "source", "confidence", "last_accessed", "access_count", "version_of",
                   "replaced_by", "marked_until", "grade"}
        with self._lock:
            if fields is None:
                fields = allowed
            else:
                fields = set(fields)
            # [FIX 2026-11-13] status 变更必须同步重算 grade：set_status / mark_permanent /
            # mark_outdated / merge_into / archive_nodes 都只写 status，grade 停在旧值。
            # 全库一致性审计（2276 条）实测 6 条不一致：merged 仍是 experience（应 hint）、
            # permanent 仍是 hint（应 experience）。这里统一兜住，避免每个调用点各自补。
            if "status" in fields and "grade" not in fields:
                node.grade = compute_grade(node)
                fields.add("grade")
            sets, args = [], []
            for f in (fields & allowed):
                val = getattr(node, f)
                if f == "tags":
                    # [FIX 2026-11-13 P1-11] 写入前统一归一：外部传进来的 tags 可能是
                    # 已被二次编码的字符串，直接 json.dumps 会再套一层引号（越写越脏）。
                    if not isinstance(val, (list, tuple)):
                        val = _load_tags(val)
                    val = json.dumps([str(t).strip() for t in val if str(t).strip()],
                                     ensure_ascii=False)
                sets.append(f"{f}=?")
                args.append(val)
            if not sets:
                return
            args.append(node.id)
            self.conn.execute(f"UPDATE nodes SET {', '.join(sets)} WHERE id=?", args)
            self.conn.commit()

    def touch(self, nid):
        """访问计数 +1（用于遗忘曲线 causal_factor 与检索）。

        [FIX 2026-11-13 P1-5] 访问后重算 grade：原实现 compute_grade 只在 add_node
        里算过一次，touch 不重算 → 一条线索被反复命中、access_count 早就够了，
        grade 仍停在 hint（实测库内有 20 条 decision/insight/goal 错级）。
        重算放在锁外（get_node/update_node 自带锁），不影响写入串行性。"""
        with self._lock:
            self.conn.execute(
                "UPDATE nodes SET access_count=access_count+1, last_accessed=? WHERE id=?",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), nid))
            self.conn.commit()
        node = self.get_node(nid)
        if node is not None:
            try:
                g = compute_grade(node)
            except Exception:
                return
            if g != (node.grade or ""):
                node.grade = g
                self.update_node(node, {"grade"})

    def set_status(self, nid, new_status):
        node = self.get_node(nid)
        if node and transition_allowed(node.status, new_status):
            node.status = new_status
            self.update_node(node, {"status"})
            return True
        return False

    # ---------- edges ----------
    def add_edge(self, edge, dedup=True):
        """[FIX 2026-11-13 P2-20] 去重 SELECT 移入锁内：原实现先在锁外查重、再在锁内 INSERT，
        两个并发写入同一 (from,to,type) 时都能通过查重，落成重复边（实测 4 组）。"""
        if edge.from_id == edge.to_id:
            return None, False  # [FIX] 自环检测
        with self._lock:
            if dedup:
                dup = self.conn.execute(
                    "SELECT id FROM edges WHERE from_id=? AND to_id=? AND edge_type=?",
                    (edge.from_id, edge.to_id, edge.edge_type)).fetchone()
                if dup:
                    return dup["id"], False
            self.conn.execute("""INSERT OR IGNORE INTO edges
                (id,from_id,to_id,edge_type,confidence,source,description,strength)
                VALUES (?,?,?,?,?,?,?,?)""",
                (edge.id, edge.from_id, edge.to_id, edge.edge_type, edge.confidence,
                 edge.source, edge.description, edge.strength))
            self.conn.commit()
            return edge.id, True

    def get_edges(self, nid):
        rows = self.conn.execute(
            "SELECT * FROM edges WHERE from_id=? OR to_id=? ORDER BY strength DESC", (nid, nid)).fetchall()
        return [Edge(id=r["id"], from_id=r["from_id"], to_id=r["to_id"], edge_type=r["edge_type"],
                     confidence=r["confidence"], source=r["source"], description=r["description"],
                     strength=r["strength"]) for r in rows]

    def in_edges(self, nid):
        rows = self.conn.execute("SELECT * FROM edges WHERE to_id=?", (nid,)).fetchall()
        return [Edge(id=r["id"], from_id=r["from_id"], to_id=r["to_id"], edge_type=r["edge_type"],
                     confidence=r["confidence"], source=r["source"], description=r["description"],
                     strength=r["strength"]) for r in rows]

    def out_edges(self, nid):
        rows = self.conn.execute("SELECT * FROM edges WHERE from_id=?", (nid,)).fetchall()
        return [Edge(id=r["id"], from_id=r["from_id"], to_id=r["to_id"], edge_type=r["edge_type"],
                     confidence=r["confidence"], source=r["source"], description=r["description"],
                     strength=r["strength"]) for r in rows]

    def edge_count_of(self, nid):
        row = self.conn.execute(
            "SELECT COUNT(*) c FROM edges WHERE from_id=? OR to_id=?", (nid, nid)).fetchone()
        return row["c"] if row else 0

    def delete_edge(self, eid):
        with self._lock:
            self.conn.execute("DELETE FROM edges WHERE id=?", (eid,))
            self.conn.commit()

    # ---------- 去重 / 相似检测 ----------
    def find_duplicates_by_prefix(self, prefix_len=60):
        """按内容前 N 字符分组找候选重复（认错巡检用）"""
        rows = self.conn.execute(
            "SELECT id, content, status, substr(content,1,?) AS pre, COUNT(*) OVER (PARTITION BY substr(content,1,?)) c "
            "FROM nodes WHERE length(content)>10", (prefix_len, prefix_len)).fetchall()
        return [dict(r) for r in rows if r["c"] > 1]

    # ---------- 遗忘曲线批量衰减 ----------
    def apply_decay(self, now_ts=None):
        """每日衰减：重算全部节点 weight；weight<0.2 的非 S 级节点归档（导出 + 移出向量索引由 VectorSearch 负责）"""
        rows = self.conn.execute("SELECT * FROM nodes").fetchall()
        archived = []
        for r in rows:
            node = self._row_to_node(r)
            # [FIX 2026-11-13] 已退出检索的节点（obsolete / merged）不再参与本轮归档统计。
            # 原实现把它们一起算进来，导致每次 /decay 都重复报"归档 N 条"，而 N 条早已不在
            # 索引里，调用方看到的 archived_count 和实际变化对不上。
            if node.status in ("obsolete", "merged"):
                continue
            ec = self.edge_count_of(node.id)
            w = compute_weight(node, ec, now_ts)
            if w != node.weight:
                node.weight = w
                self.update_node(node, {"weight"})
            if w < config.ARCHIVE_THRESHOLD and _level_of(node) != "S":
                archived.append(node.id)
        self.set_meta("last_decay_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        return archived

    def archive_nodes(self, node_ids):
        """归档：节点原记录导出到 archive/ + 真正退出活跃检索。

        [FIX 2026-11-13 P0-1] 原实现只在 JSONL 里写一行就返回，既不标 status、也不删向量，
        调用方无从得知哪些真归档了——整条"衰减→归档→删向量"链路是死代码。
        现在：① 导出原记录（可追溯、可回滚）；② status → obsolete（语义就是"沉睡退出检索"，
        不新增状态，避免与 STATUSES / 检索过滤 / verify_index 的排除集脱节）；
        ③ 返回真正归档的 id，由调用方删向量（VectorSearch 不在 GraphStore 手里）。"""
        exported = []
        if not node_ids:
            return exported
        os.makedirs(config.ARCHIVE_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d")
        out = os.path.join(config.ARCHIVE_DIR, f"archive_{stamp}.jsonl")
        with open(out, "a", encoding="utf-8") as f:
            for nid in node_ids:
                node = self.get_node(nid)
                if node is None:
                    continue
                f.write(json.dumps(node.to_dict(), ensure_ascii=False) + "\n")
                # merged 是"并入他节点"的终态，保留原状态才能追 version 链；
                # 只有仍在库内活跃的才算这次变 obsolete。
                if node.status not in ("obsolete", "merged"):
                    node.status = "obsolete"
                    self.update_node(node, {"status"})
                exported.append(nid)
        return exported

    # ---------- 时间旅行 ----------
    def nodes_as_of(self, ts, project=None, limit=500):
        """rewind：截至 ts 时刻已存在的节点（按 created_at 过滤，不按当前 status——状态是事后标记）"""
        sql = "SELECT * FROM nodes WHERE created_at <= ?"
        args = [ts]
        if project:
            sql += " AND project=?"
            args.append(project)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        args.append(limit)
        rows = self.conn.execute(sql, args).fetchall()
        return [self._row_to_node(r) for r in rows]

    # ---------- 统计 / 治理 ----------
    def stats(self):
        n = self.conn.execute("SELECT COUNT(*) c FROM nodes").fetchone()["c"]
        e = self.conn.execute("SELECT COUNT(*) c FROM edges").fetchone()["c"]
        ep = self.conn.execute("SELECT COUNT(*) c FROM episodes").fetchone()["c"]
        by_status = {r["status"]: r["c"] for r in self.conn.execute(
            "SELECT status, COUNT(*) c FROM nodes GROUP BY status").fetchall()}
        return {"nodes": n, "edges": e, "episodes": ep, "by_status": by_status}

    def indexable_node_count(self):
        """[FIX 2026-11-13 P1-6] 可索引节点数（与 rebuild_vector_index 同一口径）。

        向量对账原来用 nodes - vector_indexed，但可索引集合本身要排除
        obsolete / merged，于是 /rebuild 之后缺口永远归不了零（实测 3→7）。"""
        return self.conn.execute(
            "SELECT COUNT(*) c FROM nodes WHERE status NOT IN ('obsolete','merged')"
        ).fetchone()["c"]

    def dangling_edges(self):
        """悬空边（端点不存在）"""
        rows = self.conn.execute("""
            SELECT e.id FROM edges e
            LEFT JOIN nodes a ON a.id=e.from_id
            LEFT JOIN nodes b ON b.id=e.to_id
            WHERE a.id IS NULL OR b.id IS NULL""").fetchall()
        return [r["id"] for r in rows]

    def self_loops(self):
        rows = self.conn.execute("SELECT id FROM edges WHERE from_id=to_id").fetchall()
        return [r["id"] for r in rows]

    def duplicate_edges(self):
        """[FIX 2026-11-13 P2-17] 重复边（同 from/to/type 多条）。

        原来没有这个方法，selfcheck 也就从不检查重复边——实测库里有 4 组重复边
        一直没人治理，而 add_edge 的锁内去重只对新写入生效。"""
        rows = self.conn.execute("""
            SELECT from_id, to_id, edge_type, COUNT(*) AS c
            FROM edges GROUP BY from_id, to_id, edge_type HAVING c > 1""").fetchall()
        return [{"from_id": r["from_id"], "to_id": r["to_id"],
                 "edge_type": r["edge_type"], "count": r["c"]} for r in rows]

    def dedup_edges(self):
        """删除重复边：同 (from_id, to_id, edge_type) 只保留 id 最小的一条。返回删除条数。"""
        removed = 0
        dup = self.duplicate_edges()
        if not dup:
            return 0
        with self._lock:
            for d in dup:
                ids = [r["id"] for r in self.conn.execute(
                    "SELECT id FROM edges WHERE from_id=? AND to_id=? AND edge_type=? ORDER BY id",
                    (d["from_id"], d["to_id"], d["edge_type"])).fetchall()]
                for eid in ids[1:]:
                    self.conn.execute("DELETE FROM edges WHERE id=?", (eid,))
                    removed += 1
            self.conn.commit()
        return removed

    def isolated_nodes(self, min_edges=0):
        """孤岛节点（边数 <= min_edges）"""
        rows = self.conn.execute("""
            SELECT n.id FROM nodes n
            LEFT JOIN edges e ON e.from_id=n.id OR e.to_id=n.id
            WHERE n.status NOT IN ('obsolete','merged')
            GROUP BY n.id HAVING COUNT(e.id) <= ?""", (min_edges,)).fetchall()
        return [r["id"] for r in rows]

    def empty_content_nodes(self):
        rows = self.conn.execute("SELECT id FROM nodes WHERE length(trim(content))=0").fetchall()
        return [r["id"] for r in rows]

    def save_snapshot_meta(self):
        s = self.stats()
        self.set_meta("node_count_snapshot", s["nodes"])
        self.set_meta("edge_count_snapshot", s["edges"])


# ============================= 向量搜索 VectorSearch =============================

class VectorSearch:
    """BGE 中文向量检索（ChromaDB，cosine，512 维）。
    模型惰性加载；[FIX] 提供 verify_index() 对账，修复 SQLite/ChromaDB 双写一致性。"""

    _tokenizer = None
    _model = None
    _load_lock = threading.Lock()

    def __init__(self, chroma_dir=None):
        self.chroma_dir = chroma_dir or config.CHROMA_DIR
        os.makedirs(self.chroma_dir, exist_ok=True)
        self._client = None
        self._col = None
        self._vcol = None  # verbatim 原文集合（2026-10-17 新增，独立于 nodes）

    # ---------- 模型 ----------
    @classmethod
    def ensure_model(cls):
        """确保 BGE 模型就位：优先本地目录；缺失则从 HF 下载到本地目录
        （支持 HF_ENDPOINT 镜像，如 https://hf-mirror.com）。"""
        if cls._model is not None:
            return
        with cls._load_lock:
            if cls._model is not None:
                return
            from transformers import AutoModel, AutoTokenizer
            local = config.EMBED_MODEL_DIR
            if not (os.path.isdir(local) and any(os.listdir(local))):
                from huggingface_hub import snapshot_download
                os.makedirs(local, exist_ok=True)
                snapshot_download(config.EMBED_MODEL_NAME, local_dir=local)
            cls._tokenizer = AutoTokenizer.from_pretrained(local)
            cls._model = AutoModel.from_pretrained(local)

    @classmethod
    def embed_texts(cls, texts):
        """mean pooling + L2 归一化，返回 np.ndarray (n, 512)。"""
        import numpy as np
        import torch
        cls.ensure_model()
        encoded = cls._tokenizer(list(texts), padding=True, truncation=True,
                                 max_length=config.EMBED_MAX_SEQ, return_tensors="pt")
        with torch.no_grad():
            out = cls._model(**encoded)
        mask = encoded["attention_mask"].unsqueeze(-1).float()
        emb = (out.last_hidden_state * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1e-9)
        emb = torch.nn.functional.normalize(emb, p=2, dim=1)
        return emb.numpy()

    # ---------- ChromaDB ----------
    def _collection(self):
        if self._col is None:
            import chromadb
            self._client = chromadb.PersistentClient(path=self.chroma_dir)
            self._col = self._client.get_or_create_collection(
                name=config.COLLECTION, metadata={"hnsw:space": "cosine"})
        return self._col

    def upsert(self, node_id, text, metadata=None, embedding=None):
        """写向量；[FIX] 写入失败不静默——抛错由调用方进重试队列。"""
        if embedding is None:
            embedding = self.embed_texts([text])[0].tolist()
        meta = metadata or {}
        meta["node_id"] = node_id
        self._collection().upsert(ids=[node_id], embeddings=[embedding],
                                  documents=[text], metadatas=[meta])

    def delete(self, node_id):
        try:
            self._collection().delete(ids=[node_id])
        except Exception:
            pass

    def query(self, query_text, top_k=10, where=None):
        emb = self.embed_texts([query_text])[0].tolist()
        kw = {"query_embeddings": [emb], "n_results": top_k}
        if where:
            kw["where"] = where
        res = self._collection().query(**kw)
        out = []
        for i, nid in enumerate(res["ids"][0]):
            out.append({"id": nid, "distance": float(res["distances"][0][i]),
                        "score": 1.0 - float(res["distances"][0][i])})
        return out

    def count(self):
        try:
            return self._collection().count()
        except Exception:
            return 0

    # ---------- [FIX] 对账：修复双写一致性 ----------
    def verify_index(self, graph):
        """对比 SQLite 活跃节点与 ChromaDB 向量：返回缺向量 / 多向量两类孤儿。"""
        # [FIX 2026-11-13 P1-6] 排除集与 rebuild_vector_index.py 对齐：obsolete + merged。
        # 原来只排除 obsolete，merged 节点的残留向量被当成"该有"的，缺口永远算不平。
        db_ids = {n.id for n in graph.list_nodes(limit=100000)
                  if n.status not in ("obsolete", "merged")}
        col_ids = set(self._collection().get()["ids"]) if self.count() else set()
        missing = db_ids - col_ids   # 有节点无向量 → 需补写
        extra = col_ids - db_ids     # 有向量无节点 → 需清理
        return {"missing": missing, "extra": extra}

    def repair_index(self, graph, dry_run=False):
        """对账修复：补写缺失向量、清理多余向量。返回修复统计。"""
        rep = self.verify_index(graph)
        fixed = {"missing": 0, "extra": 0}
        if not dry_run:
            for nid in rep["missing"]:
                node = graph.get_node(nid)
                if node:
                    self.upsert(nid, node.content, metadata={
                        "node_type": node.node_type, "project": node.project,
                        "timestamp": node.timestamp, "status": node.status})
                    fixed["missing"] += 1
            for nid in rep["extra"]:
                self.delete(nid)
                fixed["extra"] += 1
        return {"before": {"missing": len(rep["missing"]), "extra": len(rep["extra"])},
                "fixed": fixed, "dry_run": dry_run}

    # ---------- verbatim 原文向量（2026-10-17 新增，独立集合）----------
    def _verbatim_col(self):
        if self._vcol is None:
            import chromadb
            if self._client is None:
                self._client = chromadb.PersistentClient(path=self.chroma_dir)
            self._vcol = self._client.get_or_create_collection(
                name=config.VERBATIM_COLLECTION, metadata={"hnsw:space": "cosine"})
        return self._vcol

    def upsert_verbatim(self, vid, text, metadata=None, embedding=None):
        """写原文向量（独立集合，日常检索不触碰）。"""
        if embedding is None:
            embedding = self.embed_texts([text])[0].tolist()
        meta = metadata or {}
        meta["vid"] = vid
        self._verbatim_col().upsert(ids=[vid], embeddings=[embedding],
                                    documents=[text], metadatas=[meta])

    def query_verbatim(self, query_text, top_k=config.VERBATIM_STRONG_TOP_K):
        """强召回：原文向量语义查询。返回 [{id, text, score, metadata}]。
        [FIX 2026-10-17] HNSW 近似检索在大规模同源集合下会漏掉真实命中
        （实测 2000+ 条同质原文时 top1 被聚簇噪声顶替，真实块相似度更高却不被召回）。
        修复：拉大原始检索面（fetch=500）→ 客户端按 similarity 降序精确重排 → 取 top_k。"""
        try:
            if self._verbatim_col().count() == 0:
                return []
        except Exception:
            return []
        emb = self.embed_texts([query_text])[0].tolist()
        fetch_n = max(top_k, config.VERBATIM_QUERY_FETCH)
        out = self._verbatim_col().query(query_embeddings=[emb], n_results=min(fetch_n, 1000))
        res = []
        ids = out.get("ids", [[]])[0] or []
        docs = out.get("documents", [[]])[0] or []
        dists = out.get("distances", [[]])[0] or []
        metas = out.get("metadatas", [[]])[0] or []
        for i, rid in enumerate(ids):
            res.append({"id": rid,
                        "text": docs[i] if i < len(docs) else "",
                        # 与 query() 的 score 语义统一：1-distance = cosine similarity（越大越近）
                        "score": round(1.0 - float(dists[i]), 4) if i < len(dists) else 0.0,
                        "metadata": metas[i] if i < len(metas) else {}})
        res.sort(key=lambda x: x["score"], reverse=True)   # 客户端精确排序，修复近似漏召
        return res[:top_k]

    def delete_verbatim(self, vid):
        try:
            self._verbatim_col().delete(ids=[vid])
        except Exception:
            pass

    def count_verbatim(self):
        try:
            return self._verbatim_col().count()
        except Exception:
            return 0


# ============================= 因果引擎 CausalEngine =============================

class CausalEngine:
    """因果链遍历。[FIX] 只沿因果语义边（enable/derives_insight/refutes/improves），
    不再把 part_of/evolves_from/shares_theme_with 等非因果边混入因果路径。"""

    def __init__(self, graph):
        self.graph = graph

    def _causal_edges(self, nid):
        return [e for e in self.graph.get_edges(nid) if e.edge_type in config.CAUSAL_EDGE_TYPES]

    def get_causes(self, nid, depth=config.CAUSAL_DEPTH, max_nodes=30):
        """沿反向边（指向该节点）BFS 找原因链"""
        seen, out = {nid}, []
        frontier = [e.from_id for e in self._causal_edges(nid) if e.to_id == nid]
        for _ in range(depth):
            nxt = []
            for fid in frontier:
                if fid in seen or len(out) >= max_nodes:
                    continue
                seen.add(fid)
                node = self.graph.get_node(fid)
                if node:
                    out.append(node.to_dict())
                nxt.extend(e.from_id for e in self._causal_edges(fid) if e.to_id == fid)
            frontier = nxt
        return out

    def get_effects(self, nid, depth=config.CAUSAL_DEPTH, max_nodes=30):
        """沿正向边（从该节点发出）BFS 找结果链"""
        seen, out = {nid}, []
        frontier = [e.to_id for e in self._causal_edges(nid) if e.from_id == nid]
        for _ in range(depth):
            nxt = []
            for tid in frontier:
                if tid in seen or len(out) >= max_nodes:
                    continue
                seen.add(tid)
                node = self.graph.get_node(tid)
                if node:
                    out.append(node.to_dict())
                nxt.extend(e.to_id for e in self._causal_edges(tid) if e.from_id == tid)
            frontier = nxt
        return out


# ============================= 便捷组装 =============================

class MemorySystem:
    """把 GraphStore / VectorSearch / CausalEngine 组装成对外服务对象"""

    def __init__(self):
        self.graph = GraphStore()
        self.vector = VectorSearch()
        self.causal = CausalEngine(self.graph)

    def add_node_with_vector(self, node, dedup=True):
        """[FIX] 双写一致性：SQLite 成功 → 向量 upsert，失败进重试队列（内存队列 + 对账兜底）"""
        nid, created = self.graph.add_node(node, dedup=dedup)
        if created:
            try:
                self.vector.upsert(nid, node.content, metadata={
                    "node_type": node.node_type, "project": node.project,
                    "timestamp": node.timestamp, "status": node.status})
            except Exception:
                # 进重试队列，由 selfcheck/下次写时补偿
                self._pending_vector_writes = getattr(self, "_pending_vector_writes", [])
                self._pending_vector_writes.append(nid)
            # [UPD 2026-11-12 实体钩子层] 写入即建路由：词 → 节点
            try:
                import anchors
                anchors.index_node(self, node)
            except Exception:
                pass
        return nid, created

    def add_verbatim(self, text, ts=None, source="", node_id=None, session="", vid=None, with_vector=True):
        """写原文块：落 SQLite + 原文向量（独立集合，日常检索不触碰）。返回 [vid, ...]。
        2026-10-17 新增：原文层强召回的数据入口。"""
        ids = self.graph.add_verbatim(text, ts=ts, source=source, node_id=node_id,
                                      session=session, vid=vid)
        if with_vector:
            for cid in ids:
                blk = self.graph.get_verbatim(cid)
                if blk:
                    try:
                        self.vector.upsert_verbatim(cid, blk["text"], metadata={
                            "node_id": node_id or "", "session": session or "",
                            "ts": blk["ts"], "source": source})
                    except Exception:
                        pass  # 向量失败不阻断原文落库（自检对账可补）
        return ids

    def get_verbatim(self, vid):
        blk = self.graph.get_verbatim(vid)
        if blk:
            if blk["node_id"]:
                node = self.graph.get_node(blk["node_id"])
                blk["node"] = node.to_dict() if node else None
            blk["linked_nodes"] = self.graph.list_verbatim_by_node(blk["node_id"], limit=10) if blk["node_id"] else []
        return blk

    def flush_pending_writes(self):
        """重试未写成功的向量；[FIX] 双写补偿"""
        pending = getattr(self, "_pending_vector_writes", [])
        ok = []
        for nid in pending:
            node = self.graph.get_node(nid)
            if node:
                try:
                    self.vector.upsert(nid, node.content)
                    ok.append(nid)
                except Exception:
                    pass
        self._pending_vector_writes = [p for p in pending if p not in ok]
        return ok
