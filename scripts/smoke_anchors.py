# -*- coding: utf-8 -*-
"""锚点链路自检：临时库跑一遍建表 → 命中 → hits 回写 → 表满回收，不加载向量模型。

用法：python scripts/smoke_anchors.py
退出码 0 表示全部通过。
"""
import os
import sys
import tempfile
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import GraphStore, Node  # noqa: E402
import anchors  # noqa: E402
import config  # noqa: E402

SAMPLES = [
    ("小妖 3D 形象重做完成，用 Blender 重建了骨骼，尾巴长度按设定改短了。", "event", ["小妖", "3D建模"]),
    ("小妖的视频生成参数定稿，用 seedance 出片，时长 15 秒。", "decision", ["小妖", "视频"]),
    ("Strata 是本地推理集群，上面跑 125B 模型，显存占用需要盯着。", "fact", ["Strata"]),
    ("Strata 的调度器改成闲时卸载循环。", "decision", ["Strata"]),
    ("SuperMem 的锚点表上限保持 20000 行，超限一次删到 19000 留缓冲。", "decision", ["SuperMem"]),
]

QUERY = "小妖的形象怎么做的"
EXPECT_HITS = 2          # 两个节点带 小妖 标签


def main():
    tmp_dir = tempfile.mkdtemp(prefix="smem_smoke_")
    db = os.path.join(tmp_dir, "test.db")
    g = GraphStore(db_path=db)
    ms = SimpleNamespace(graph=g)

    for text, typ, tags in SAMPLES:
        g.add_node(Node(content=text, node_type=typ, confidence=0.9, tags=tags), dedup=False)

    for n in g.list_nodes(limit=50):
        anchors.index_node(ms, n)

    st = anchors.stats(ms)
    print(f"锚点表：{st['terms']} 个词 / {st['rows']} 行，覆盖 {st['covered_nodes']} 个节点")

    # 一、准入闸门：私有词（只被一个节点使用）不该成为路由词；
    #     增量写入不 prune，这类词要等全量 build_index(prune=True) 才被清掉
    private = g.conn.execute("SELECT COUNT(*) FROM anchors WHERE term='Blender'").fetchone()[0]
    shared = g.conn.execute("SELECT COUNT(*) FROM anchors WHERE term='小妖'").fetchone()[0]
    print(f"私有词 Blender df={private}（增量写入不 prune，留待全量 build_index 清理）｜共用词 小妖 df={shared}")

    # 二、检索命中
    hits = anchors.hook_recall(ms, QUERY, top_k=5)
    got = len(hits)
    print(f"查「{QUERY}」命中 {got} 个节点")
    ok_hit = got >= EXPECT_HITS

    # 三、hits 回写（缓冲未清空前不应为 0）
    anchors.flush_hits(ms)
    total_hits = g.conn.execute("SELECT SUM(hits) FROM anchors WHERE term='小妖'").fetchone()[0] or 0
    print(f"小妖 累计 hits={total_hits}")
    ok_hits = total_hits > 0

    # 四、表满回收一次多删留缓冲
    old_ceil = config.ANCHOR_MAX_TABLE
    config.ANCHOR_MAX_TABLE = 4
    anchors._enforce_ceil(ms)
    left = g.conn.execute("SELECT COUNT(*) FROM anchors").fetchone()[0]
    print(f"上限压到 4 行后剩 {left} 行")
    ok_ceil = left <= 4
    config.ANCHOR_MAX_TABLE = old_ceil

    g.conn.close()
    os.remove(db)
    os.rmdir(tmp_dir)

    ok = all((ok_hit, ok_hits, ok_ceil))
    print("结果：" + ("通过" if ok else "失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
