"""
超级记忆系统 — 核心引擎
GraphStore: SQLite 图存储
VectorStore: ChromaDB 向量检索
"""

import sqlite3
import uuid
import json
import os
import threading
from datetime import datetime, timedelta

DB_PATH = os.environ.get("SUPER_MEMORY_DB", os.path.join(os.path.dirname(__file__), "super_memory.db"))


# ─── 图存储 ────────────────────────────────────────────────────────────

class GraphStore:
    """SQLite 属性图存储"""

    def __init__(self, db_path=None):
        self.path = db_path or DB_PATH
        self._local = threading.local()
        self._init_db()

    def _get_conn(self):
        """返回线程本地连接。每个线程独立连接，避免 SQLite 线程安全问题。"""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
        return self._local.conn

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL DEFAULT 'Concept',
                name TEXT NOT NULL,
                content TEXT DEFAULT '',
                source TEXT DEFAULT '',
                confidence REAL DEFAULT 1.0,
                access_count INTEGER DEFAULT 0,
                weight REAL DEFAULT 1.0,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS edges (
                id TEXT PRIMARY KEY,
                from_id TEXT NOT NULL,
                to_id TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'RELATED_TO',
                weight REAL DEFAULT 1.0,
                context TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY(from_id) REFERENCES nodes(id),
                FOREIGN KEY(to_id) REFERENCES nodes(id)
            );
            CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_id);
            CREATE INDEX IF NOT EXISTS idx_edges_to ON edges(to_id);
            CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
        """)
        conn.commit()

    def save_node(self, node_id, node_type="Concept", name="", content="", source="",
                  confidence=1.0, weight=1.0):
        """创建或更新节点。如 id 已存在则更新 name/content 和 updated_at。"""
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        conn = self._get_conn()
        existing = conn.execute("SELECT id FROM nodes WHERE id=?", (node_id,)).fetchone()
        if existing:
            conn.execute("""
                UPDATE nodes SET name=?, content=?, updated_at=?, confidence=MAX(confidence,?),
                                 weight=MAX(weight,?)
                WHERE id=?
            """, (name, content, now, confidence, weight, node_id))
        else:
            conn.execute("""
                INSERT INTO nodes (id, type, name, content, source, confidence, weight, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (node_id, node_type, name, content, source, confidence, weight, now, now))
        conn.commit()
        return node_id

    def get_node(self, node_id):
        row = self._get_conn().execute(
            "SELECT id, type, name, content, source, confidence, access_count, weight, created_at, updated_at "
            "FROM nodes WHERE id=?", (node_id,)
        ).fetchone()
        if not row:
            return None
        return dict(row)

    def get_all_nodes(self, limit=1000, offset=0):
        rows = self._get_conn().execute(
            "SELECT id, type, name, content, source, confidence, access_count, weight, created_at, updated_at "
            "FROM nodes ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_node(self, node_id):
        conn = self._get_conn()
        conn.execute("DELETE FROM edges WHERE from_id=? OR to_id=?", (node_id, node_id))
        conn.execute("DELETE FROM nodes WHERE id=?", (node_id,))
        conn.commit()

    def save_edge(self, from_id, to_id, edge_type="RELATED_TO", weight=1.0, context=""):
        """创建边（有向关系）。

        CAUSED_BY: from_id → to_id 表示「from 的原因是 to」
          例如: 深度学习 → 机器学习，表示「深度学习的原因是机器学习」
          追溯方向：从 from_id 找到 to_id 即是找到原因
          推导方向：从 to_id 找到 from_id 即是找到后果
        RELATED_TO / PART_OF / MENTIONS: 通用关联

        如果同类型同方向的边已存在，则使用 MAX(weight) 更新权重。
        """
        conn = self._get_conn()
        existing = conn.execute(
            "SELECT id FROM edges WHERE from_id=? AND to_id=? AND type=?",
            (from_id, to_id, edge_type)
        ).fetchone()
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        if existing:
            conn.execute(
                "UPDATE edges SET weight=MAX(weight,?), context=?, created_at=? WHERE id=?",
                (weight, context, now, existing["id"])
            )
            return existing["id"]
        edge_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO edges (id, from_id, to_id, type, weight, context, created_at) VALUES (?,?,?,?,?,?,?)",
            (edge_id, from_id, to_id, edge_type, weight, context, now)
        )
        conn.commit()
        return edge_id

    def get_node_edges(self, node_id):
        """返回指定节点的所有出边和入边。"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, from_id, to_id, type, weight, context, created_at "
            "FROM edges WHERE from_id=? OR to_id=? ORDER BY weight DESC",
            (node_id, node_id)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_edges(self, limit=5000):
        rows = self._get_conn().execute(
            "SELECT id, from_id, to_id, type, weight, context, created_at FROM edges "
            "ORDER BY weight DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_edge(self, edge_id):
        self._get_conn().execute("DELETE FROM edges WHERE id=?", (edge_id,))
        self._get_conn().commit()

    def get_stats(self):
        conn = self._get_conn()
        node_count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        return {"nodes": node_count, "edges": edge_count}

    def record_access(self, node_id):
        """记录节点访问，增加 access_count 并重算 weight。

        遗忘公式：
          decay(t) = e^(-0.00022 * t)     # t = 距创建时间的小时数
                                            # 1 年（8760h）后 decay ≈ 0.15
          freq_bonus = min(1.0, count * 0.05)  # 每次访问 +0.05，上限 1.0
          new_weight = max(0.05, min(1.0, decay + freq_bonus))

        权重 < 0.15 → compact 可剪枝
        权重 < 0.25 → compact 输出提醒列表
        权重 < 0.05 → 彻底遗忘（保底）
        """
        conn = self._get_conn()
        node = self.get_node(node_id)
        if not node:
            return
        count = node["access_count"] + 1
        hours_elapsed = 0
        try:
            created = datetime.strptime(node["created_at"], "%Y-%m-%d %H:%M:%S")
            hours_elapsed = (datetime.utcnow() - created).total_seconds() / 3600
        except:
            pass
        decay = 2.71828 ** (-0.00022 * hours_elapsed)
        freq_bonus = min(1.0, count * 0.05)
        new_weight = min(1.0, max(0.05, decay + freq_bonus))
        conn.execute(
            "UPDATE nodes SET access_count=?, weight=?, updated_at=datetime('now') WHERE id=?",
            (count, round(new_weight, 4), node_id)
        )
        conn.commit()
        return new_weight


# ─── 向量检索引擎 ────────────────────────────────────────────────────────

class VectorStore:
    """ChromaDB 向量检索封装"""

    def __init__(self, persist_dir=None):
        import chromadb
        from chromadb.config import Settings
        persist_dir = persist_dir or os.path.join(os.path.dirname(__file__), "chroma_db")
        os.makedirs(persist_dir, exist_ok=True)
        self.client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False)
        )
        self.collection = self.client.get_or_create_collection(
            name="super_memory",
            metadata={"hnsw:space": "cosine"}
        )
        self._model = None  # 延迟加载
        self._lock = threading.Lock()

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer("shibing624/text2vec-base-chinese")
        return self._model

    def _embed(self, texts):
        model = self._get_model()
        return model.encode(texts, normalize_embeddings=True).tolist()

    def upsert(self, node_id, text):
        """插入或更新节点向量。"""
        vec = self._embed([text])[0]
        with self._lock:
            try:
                self.collection.update(ids=[node_id], embeddings=[vec], metadatas=[{"text": text}])
            except:
                self.collection.add(ids=[node_id], embeddings=[vec], metadatas=[{"text": text}])

    def upsert_batch(self, items):
        """批量 upsert: [(id, text), ...]"""
        if not items:
            return
        ids = [it[0] for it in items]
        texts = [it[1] for it in items]
        vecs = self._embed(texts)
        with self._lock:
            for i in range(0, len(ids), 100):
                batch = slice(i, i + 100)
                try:
                    self.collection.update(ids=ids[batch], embeddings=vecs[batch],
                                           metadatas=[{"text": t} for t in texts[batch]])
                except:
                    self.collection.add(ids=ids[batch], embeddings=vecs[batch],
                                        metadatas=[{"text": t} for t in texts[batch]])

    def search(self, query, top_k=10):
        """语义检索，返回 [(node_id, score), ...]。"""
        vec = self._embed([query])[0]
        with self._lock:
            results = self.collection.query(query_embeddings=[vec], n_results=top_k)
        ids = results["ids"][0]
        dists = results["distances"][0]
        # 余弦距离转相似度
        return [(i, round(1.0 - d, 4)) for i, d in zip(ids, dists)]

    def delete(self, node_id):
        with self._lock:
            try:
                self.collection.delete(ids=[node_id])
            except:
                pass

    def count(self):
        return self.collection.count()


# ─── 便捷创建 ────────────────────────────────────────────────────────────

def generate_id():
    return uuid.uuid4().hex[:12]
