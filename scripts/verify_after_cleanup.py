# -*- coding: utf-8 -*-
"""清理与重算后的生产库验收核查（只读）。"""
import io, json, os, sqlite3, sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "storage", "super_memory.db")
conn = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
conn.row_factory = sqlite3.Row
c = conn.cursor()

print("=" * 60)
print("1. 语料残留")
print("  2024 年节点:", c.execute("SELECT COUNT(*) FROM nodes WHERE substr(timestamp,1,4)='2024'").fetchone()[0])
print("  命中批次 id LIKE %20261011% 且 2024:", c.execute(
    "SELECT COUNT(*) FROM nodes WHERE id LIKE '%20261011%' AND substr(timestamp,1,4)='2024'").fetchone()[0])
print("  全批次 20261011 节点:", c.execute("SELECT COUNT(*) FROM nodes WHERE id LIKE '%20261011%'").fetchone()[0])
for r in c.execute("""SELECT project, substr(timestamp,1,4) y, COUNT(*) n FROM nodes
                      WHERE id LIKE '%20261011%' GROUP BY project, y ORDER BY y, n DESC"""):
    print("    %-10s %s 年 %d 条" % (r["project"], r["y"], r["n"]))

print("=" * 60)
print("2. 总量")
for t in ("nodes", "edges", "episodes", "anchors", "verbatim"):
    try:
        print("  %-9s %d" % (t, c.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]))
    except Exception as e:
        print("  %-9s (表不可查: %s)" % (t, e))

print("=" * 60)
print("3. episode 规模分布")
sizes = [len(json.loads(r["node_ids"] or "[]")) for r in c.execute("SELECT node_ids FROM episodes")]
sizes.sort()
if sizes:
    n = len(sizes)
    def q(p):
        return sizes[min(n - 1, int(round((n - 1) * p)))]
    print("  块数=%d  min=%d p50=%d p95=%d max=%d  总和节点=%d" % (n, sizes[0], q(0.5), q(0.95), sizes[-1], sum(sizes)))
    over = [s for s in sizes if s > 40]
    print("  超过 40 的块:", len(over), over[:10])

starts = [r["start_timestamp"] for r in c.execute("SELECT start_timestamp FROM episodes")]
print("  重复 start:", len(starts) - len(set(starts)))

print("=" * 60)
print("4. 悬空检查")
ids = set(r[0] for r in c.execute("SELECT id FROM nodes"))
dangling = 0
for r in c.execute("SELECT node_ids FROM episodes"):
    for nid in json.loads(r["node_ids"] or "[]"):
        if nid not in ids:
            dangling += 1
print("  episode 内悬空节点引用:", dangling)

edge_dangling = c.execute("""SELECT COUNT(*) FROM edges e
   WHERE NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id=e.from_id)
      OR NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id=e.to_id)""").fetchone()[0]
print("  悬空边:", edge_dangling)

vb_dangling = c.execute("""SELECT COUNT(*) FROM verbatim v
   WHERE v.node_id IS NOT NULL AND v.node_id <> ''
     AND NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id=v.node_id)""").fetchone()[0]
print("  verbatim 指向已不存在节点的条数:", vb_dangling)

print("=" * 60)
print("5. 真实记忆抽样（确认未被误删）")
for kw in ("ESP32", "claw_extract", "Qwen3-TTS", "Strata", "庙小妖", "AIGC Hub"):
    hit = c.execute("SELECT COUNT(*) FROM nodes WHERE content LIKE ?", ("%" + kw + "%",)).fetchone()[0]
    print("  含「%s」节点: %d" % (kw, hit))
for r in c.execute("""SELECT id, timestamp, project, substr(content,1,40) t FROM nodes
                      WHERE id LIKE '%20261011%' AND substr(timestamp,1,4)='2026'
                      ORDER BY timestamp DESC LIMIT 3"""):
    print("  %s %s [%s] %s..." % (r["id"][:34], r["timestamp"], r["project"], r["t"]))
conn.close()
