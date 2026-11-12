# -*- coding: utf-8 -*-
"""一次性数据迁移（2026-11-13 大修配套）：修历史脏数据，让代码侧修复真正生效。

处理的 7 类历史遗留：
  1. tags 双重转义（每个标签被 JSON 编码了两层，读出是 "\\"\\\\u5199..."）→ 还原成中文标签
  2. 边类型写法不一（relate / RELATES_TO / block 等）→ 归一到 config.EDGE_TYPES
  3. 重复边（同 from/to/type 多条）→ 只留一条
  4. related 边 strength 恒 1.0 → 从 description「语义关联 0.xx」回填真实分数
  5. timestamp 四种格式混存（无秒 / ISO 带 T / 纯日期 / epoch）→ 统一 YYYY-MM-DD HH:MM:SS
  6. grade 只在写入时算过一次，与实际权重/访问量脱节 → 按当前 compute_grade 重算
  7. 坏 episode（node_ids 里引用了不存在的节点）→ 剔除失效引用

用法（只对隔离副本执行，生产未授权）：
    python scripts/migrate_fix_20261113.py --db <db路径> --dry-run
    python scripts/migrate_fix_20261113.py --db <db路径> --apply
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from core import MemorySystem, compute_grade  # noqa: E402
from event_normalizer import normalize_timestamp  # noqa: E402

_U_RE = re.compile(r"\\+u([0-9a-fA-F]{4})")
_SCORE_RE = re.compile(r"语义关联\s*([0-9]*\.?[0-9]+)")

# 历史里出现过、但不在白名单的边类型 → 目标类型（只做确定同义的映射）
EDGE_ALIAS = {
    "relate": "related",
    "relates_to": "related",
    "block": "blocks",
}


def _unescape_u(s):
    for _ in range(3):
        new = _U_RE.sub(lambda m: chr(int(m.group(1), 16)), s)
        if new == s:
            break
        s = new
    return s


def clean_tag(t):
    """把被多层转义过的单个标签还原成干净字符串（可能返回 list：内嵌数组时）。"""
    s = str(t).strip()
    for _ in range(4):
        s2 = s
        if s2.startswith('\\"'):
            s2 = s2[2:]
        elif s2.startswith('"'):
            s2 = s2[1:]
        if s2.endswith('\\"'):
            s2 = s2[:-2]
        elif s2.endswith('"'):
            s2 = s2[:-1]
        s2 = s2.strip()
        if s2 == s:
            break
        s = s2
    s = _unescape_u(s).strip().strip("\\").strip()
    if s.startswith("["):
        try:
            inner = json.loads(s)
            if isinstance(inner, list):
                out = []
                for x in inner:
                    r = clean_tag(x)
                    if isinstance(r, list):
                        out.extend(r)
                    elif r:
                        out.append(r)
                return out
        except Exception:
            pass
    return s


def clean_tags(raw):
    """返回 (清洗后的 list, 是否发生变化)。"""
    try:
        arr = json.loads(raw) if raw else []
    except Exception:
        arr = [raw]
    if not isinstance(arr, list):
        arr = [arr]
    out = []
    for item in arr:
        r = clean_tag(item)
        if isinstance(r, list):
            for x in r:
                x = str(x).strip()
                if x and x not in out:
                    out.append(x)
        else:
            r = str(r).strip()
            if r and r not in out:
                out.append(r)
    out = [x for x in out if x not in ("[]", "null", "None")]
    return out, out != [str(x) for x in arr]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not (args.dry_run or args.apply):
        print("必须指定 --dry-run 或 --apply")
        return 2

    config.DB_PATH = args.db
    ms = MemorySystem()
    g = ms.graph
    d = not args.apply
    report = {}

    nodes = g.list_nodes(limit=100000)
    idset = {n.id for n in nodes}

    # 1. tags
    fixed_tags = 0
    for n in nodes:
        row = g.conn.execute("SELECT tags FROM nodes WHERE id=?", (n.id,)).fetchone()
        raw = row["tags"] if row else None
        new, changed = clean_tags(raw)
        if changed:
            fixed_tags += 1
            if not d:
                g.conn.execute("UPDATE nodes SET tags=? WHERE id=?",
                               (json.dumps(new, ensure_ascii=False), n.id))
    if not d:
        g.conn.commit()
    report["tags_fixed"] = fixed_tags

    # 2. 边类型归一
    edges = g.conn.execute("SELECT id, edge_type FROM edges").fetchall()
    etypes_fixed = 0
    for r in edges:
        t = (r["edge_type"] or "").strip()
        low = t.lower()
        target = None
        if t != low:
            target = low if low in config.EDGE_TYPES else EDGE_ALIAS.get(low)
        elif t not in config.EDGE_TYPES:
            target = EDGE_ALIAS.get(t)
        if target and target in config.EDGE_TYPES:
            etypes_fixed += 1
            if not d:
                g.conn.execute("UPDATE edges SET edge_type=? WHERE id=?", (target, r["id"]))
    if not d:
        g.conn.commit()
    report["edge_types_fixed"] = etypes_fixed

    # 3. 重复边
    dup_before = len(g.duplicate_edges())
    if d:
        report["duplicate_groups_before"] = dup_before
    else:
        report["duplicate_edges_removed"] = g.dedup_edges()
        report["duplicate_groups_after"] = len(g.duplicate_edges())

    # 4. related strength 回填
    backfilled = 0
    for r in g.conn.execute(
            "SELECT id, description, strength FROM edges WHERE edge_type='related'").fetchall():
        m = _SCORE_RE.search(r["description"] or "")
        if not m:
            continue
        score = float(m.group(1))
        if score <= 0 or score > 1:
            continue
        cur = float(r["strength"] or 1.0)
        if abs(cur - score) > 0.02:
            backfilled += 1
            if not d:
                g.conn.execute("UPDATE edges SET strength=? WHERE id=?", (round(score, 4), r["id"]))
    if not d:
        g.conn.commit()
    report["related_strength_backfilled"] = backfilled

    # 5. timestamp 归一
    ts_fixed = 0
    for n in nodes:
        raw = n.timestamp or ""
        s = str(raw)
        if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", s):
            continue
        new = normalize_timestamp(s)
        if new != s:
            ts_fixed += 1
            if not d:
                g.conn.execute("UPDATE nodes SET timestamp=? WHERE id=?", (new, n.id))
    if not d:
        g.conn.commit()
    report["timestamps_fixed"] = ts_fixed

    # created_at / last_accessed 同步统一（只处理明显非标准格式）
    # [FIX 2026-11-13] 空 last_accessed 表示"从未被检索命中"，语义上是缺值；
    # normalize_timestamp("") 会回退 now，等于把没访问过的记忆写成"刚访问"，
    # 反而让衰减永远不生效。空值原样保留，只归一非空且非标准格式的值。
    ca_fixed = 0
    for r in g.conn.execute("SELECT id, created_at, last_accessed FROM nodes").fetchall():
        ca = str(r["created_at"] or "")
        la = str(r["last_accessed"] or "")
        nca = ca if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", ca) else normalize_timestamp(ca)
        nla = la if (not la or re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", la)) else normalize_timestamp(la)
        if nca != ca or nla != la:
            ca_fixed += 1
            if not d:
                g.conn.execute("UPDATE nodes SET created_at=?, last_accessed=? WHERE id=?",
                               (nca, nla, r["id"]))
    if not d:
        g.conn.commit()
    report["node_time_fields_fixed"] = ca_fixed

    # 6. grade 重算
    grade_fixed = 0
    for n in nodes:
        try:
            newg = compute_grade(n)
        except Exception:
            continue
        if newg and newg != n.grade:
            grade_fixed += 1
            if not d:
                g.conn.execute("UPDATE nodes SET grade=? WHERE id=?", (newg, n.id))
    if not d:
        g.conn.commit()
    report["grade_fixed"] = grade_fixed

    # 7. 坏 episode 引用
    ep_fixed = 0
    ep_deleted = 0
    for r in g.conn.execute("SELECT id, node_ids FROM episodes").fetchall():
        try:
            arr = json.loads(r["node_ids"] or "[]")
        except Exception:
            arr = []
        if not isinstance(arr, list):
            continue
        bad = [x for x in arr if x not in idset]
        if not bad:
            continue
        good = [x for x in arr if x in idset]
        ep_fixed += 1
        if not d:
            if good:
                g.conn.execute("UPDATE episodes SET node_ids=? WHERE id=?",
                               (json.dumps(good, ensure_ascii=False), r["id"]))
            else:
                g.conn.execute("DELETE FROM episodes WHERE id=?", (r["id"],))
                ep_deleted += 1
    if not d:
        g.conn.commit()
    report["episodes_fixed"] = ep_fixed
    report["episodes_deleted"] = ep_deleted

    # 8. episode 同 start 重复（/episodes/build 旧幂等键是 (start,end)，窗口 end 后移就复制一份）
    dup_ep_removed = 0
    groups = g.conn.execute(
        "SELECT start_timestamp, COUNT(*) AS c FROM episodes GROUP BY start_timestamp "
        "HAVING COUNT(*) > 1").fetchall()
    for grp in groups:
        rows = g.conn.execute(
            "SELECT id, end_timestamp, node_ids FROM episodes WHERE start_timestamp=? "
            "ORDER BY created_at ASC, id ASC", (grp["start_timestamp"],)).fetchall()
        if len(rows) < 2:
            continue
        end_max = ""
        merged = []
        for r in rows:
            try:
                arr = json.loads(r["node_ids"] or "[]")
            except Exception:
                arr = []
            if isinstance(arr, list):
                for x in arr:
                    if x not in merged:
                        merged.append(x)
            if (r["end_timestamp"] or "") > end_max:
                end_max = r["end_timestamp"] or ""
        dup_ep_removed += len(rows) - 1
        if not d:
            g.conn.execute("UPDATE episodes SET end_timestamp=?, node_ids=? WHERE id=?",
                           (end_max, json.dumps(merged, ensure_ascii=False), rows[0]["id"]))
            for r in rows[1:]:
                g.conn.execute("DELETE FROM episodes WHERE id=?", (r["id"],))
    if not d:
        g.conn.commit()
    report["episodes_duplicate_removed"] = dup_ep_removed

    report["mode"] = "apply" if args.apply else "dry-run"
    report["db"] = args.db
    report["time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
