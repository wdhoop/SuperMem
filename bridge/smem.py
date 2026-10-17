# -*- coding: utf-8 -*-
"""
超级记忆系统 —— 桥接客户端 bridge/smem.py
豆包助手（或其他智能体）读写本地 SuperMem 的统一通道。
用法：
    python smem.py health                      # 健康检查（含自愈尝试）
    python smem.py ensure                      # 确保服务可用（挂了自动拉起）
    python smem.py add "内容" [--type insight] [--tags a,b] [--project default] [--source doubao]
    python smem.py extract "文本"              # 规则提取入库（自动建因果边）
    python smem.py search "查询" [--limit 10]
    python smem.py rewind "2026-09-30 12:00" "查询"
    python smem.py mark <node_id> confirmed    # 状态流转
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import config  # noqa: E402

PORT = config.PORT
TOKEN = config.API_TOKEN
BASE_URL = f"http://127.0.0.1:{PORT}"


def api(method, path, body=None, timeout=30):
    url = BASE_URL + path
    req = urllib.request.Request(url, method=method,
                                 headers={"X-API-Token": TOKEN,
                                          "Content-Type": "application/json; charset=utf-8"})
    if body is not None:
        req.data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def health():
    try:
        return api("GET", "/health", timeout=8)
    except Exception:
        return None


def ensure(max_wait=40):
    """确保服务可用：不可用则经看门狗拉起，最多等待 max_wait 秒。返回 health dict 或 None。"""
    h = health()
    if h:
        return h
    watchdog = os.path.join(BASE_DIR, "scripts", "watchdog.py")
    # [FIX] 用 pythonw 拉起 watchdog：python.exe 版 watchdog 会用带控制台方式拉起 main.py 导致黑框闪烁
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    launcher = pythonw if os.path.exists(pythonw) else sys.executable
    try:
        subprocess.Popen([launcher, watchdog], cwd=BASE_DIR,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        pass
    waited = 0
    while waited < max_wait:
        time.sleep(5)
        waited += 5
        h = health()
        if h:
            return h
    return None


def add(content, node_type="insight", tags=None, project="default", source="doubao"):
    return api("POST", "/nodes", {"content": content, "node_type": node_type,
                                  "tags": tags or [], "project": project, "source": source})


def extract(text, source="doubao", project="default"):
    return api("POST", "/extract", {"text": text, "source": source, "project": project})


def search(q, limit=10, project=None):
    path = f"/search/{urllib.parse.quote(q)}?limit={limit}"
    if project:
        path += f"&project={urllib.parse.quote(project)}"
    return api("GET", path)


def rewind(ts, q, limit=10):
    return api("GET", f"/rewind/{urllib.parse.quote(ts)}/{urllib.parse.quote(q)}?limit={limit}")


def mark(nid, action):
    return api("POST", "/mark", {"id": nid, "action": action})


# ---------- 原文层 verbatim（2026-10-17 新增）----------
def raw_add(text, ts=None, source="doubao", node_id=None, session=""):
    """写一块逐字原文（可关联图节点，双向指针）。"""
    body = {"text": text, "source": source, "session": session}
    if ts:
        body["ts"] = ts
    if node_id:
        body["node_id"] = node_id
    return api("POST", "/verbatim", body)


def raw_search(q, limit=5, force=False):
    """强召回：三路召回置信不足时自动下探原文层；--force 强制走原文。"""
    path = f"/search/strong/{urllib.parse.quote(q)}?limit={limit}"
    if force:
        path += "&force=1"
    return api("GET", path)


def raw_get(vid):
    return api("GET", f"/verbatim/{urllib.parse.quote(vid)}")


def _fmt_search(res):
    lines = [f"[检索] {res['query']}（引擎 {res['engine']}）"]
    for i, it in enumerate(res.get("results", []), 1):
        n = it["node"]
        causes = " ".join(c["content"][:18] for c in it.get("causes", [])[:2])
        effects = " ".join(e["content"][:18] for e in it.get("effects", [])[:2])
        lines.append(f"  {i}. [{n['node_type']}/{n['status']}] {n['content']}")
        lines.append(f"     权重 {n['weight']:.2f} ({it.get('weight_zone')}) | 得分 {it['score']}"
                     + (f" | 因: {causes}" if causes else "") + (f" | 果: {effects}" if effects else ""))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(prog="smem")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health")
    sub.add_parser("ensure")
    p_add = sub.add_parser("add")
    p_add.add_argument("content")
    p_add.add_argument("--type", default="insight")
    p_add.add_argument("--tags", default="")
    p_add.add_argument("--project", default="default")
    p_add.add_argument("--source", default="doubao")
    p_ex = sub.add_parser("extract")
    p_ex.add_argument("text")
    p_ex.add_argument("--source", default="doubao")
    p_ex.add_argument("--project", default="default")
    p_sr = sub.add_parser("search")
    p_sr.add_argument("query")
    p_sr.add_argument("--limit", type=int, default=10)
    p_sr.add_argument("--project", default=None)
    p_rw = sub.add_parser("rewind")
    p_rw.add_argument("ts")
    p_rw.add_argument("query")
    p_mk = sub.add_parser("mark")
    p_mk.add_argument("node_id")
    p_mk.add_argument("action")
    p_ra = sub.add_parser("raw", help="原文层（verbatim）：add/search/get")
    p_ra.add_argument("op", choices=["add", "search", "get"])
    p_ra.add_argument("payload")
    p_ra.add_argument("--ts", default=None)
    p_ra.add_argument("--source", default="doubao")
    p_ra.add_argument("--node-id", default=None)
    p_ra.add_argument("--session", default="")
    p_ra.add_argument("--limit", type=int, default=5)
    p_ra.add_argument("--force", action="store_true", help="强召回时强制走原文层")

    args = ap.parse_args()
    if args.cmd == "health":
        h = health()
        if h:
            print(f"OK nodes={h['nodes']} edges={h['edges']} vector_indexed={h['vector_indexed']} gap={h['vector_gap']}")
        else:
            print("DOWN")
    elif args.cmd == "ensure":
        h = ensure()
        print(f"ensure -> {'OK' if h else 'FAILED'}")
    elif args.cmd == "add":
        h = ensure()
        if not h:
            print("ERROR: service unavailable")
            sys.exit(1)
        r = add(args.content, node_type=args.type, tags=[t for t in args.tags.split(",") if t],
                project=args.project, source=args.source)
        print(f"add -> id={r['id']} created={r['created']}")
    elif args.cmd == "extract":
        h = ensure()
        if not h:
            print("ERROR: service unavailable")
            sys.exit(1)
        r = extract(args.text, source=args.source, project=args.project)
        print(f"extract -> created={r['created']}")
    elif args.cmd == "search":
        h = ensure()
        if not h:
            print("ERROR: service unavailable")
            sys.exit(1)
        print(_fmt_search(search(args.query, limit=args.limit, project=args.project)))
    elif args.cmd == "rewind":
        h = ensure()
        if not h:
            print("ERROR: service unavailable")
            sys.exit(1)
        r = rewind(args.ts, args.query)
        print(json.dumps(r, ensure_ascii=False, indent=2)[:2000])
    elif args.cmd == "mark":
        r = mark(args.node_id, args.action)
        print(f"mark -> ok={r.get('ok')} status={r.get('status')}")
    elif args.cmd == "raw":
        h = ensure()
        if not h:
            print("ERROR: service unavailable")
            sys.exit(1)
        if args.op == "add":
            r = raw_add(args.payload, ts=args.ts, source=args.source,
                        node_id=args.node_id, session=args.session)
            print(f"raw add -> ids={r['ids']} count={r['count']}")
        elif args.op == "get":
            r = raw_get(args.payload)
            if "verbatim" in r:
                v = r["verbatim"]
                print(f"[原文块 {v['id']}] @{v['ts']} source={v['source']} node={v.get('node_id')} session={v.get('session')}")
                print(v["text"][:1200])
            else:
                print(json.dumps(r, ensure_ascii=False)[:500])
        elif args.op == "search":
            r = raw_search(args.payload, limit=args.limit, force=args.force)
            print(f"[强召回] {r['query']} | mode={r['mode']}（引擎 {r['engine']}）")
            if r.get("results"):
                for i, it in enumerate(r["results"], 1):
                    n = it["node"]
                    print(f"  G{i}. [{n['node_type']}/{n['status']}] {n['content'][:60]} | 得分 {it['score']}")
            if r.get("raw"):
                for i, it in enumerate(r["raw"], 1):
                    print(f"  R{i}. [score {it['score']}] node={it.get('node_id')} @{it.get('ts')} src={it.get('source')}")
                    print(f"      {it['text'][:150]}")
            else:
                print("  （无原文命中）")


if __name__ == "__main__":
    main()
