# -*- coding: utf-8 -*-
"""
超级记忆系统 —— 记忆提取 extract.py
三条来源：规则提取（cron/文本） / LLM 增强（>200 字符，可选配置，默认关闭） / 手动/接口写入。
双向提取流程（会话结束）：提取节点/边 → 更新向量索引 → 回写当日 memory/YYYY-MM-DD.md。
"""
import json
import os
import urllib.request
from datetime import datetime

import config
from core import Edge, MemorySystem, Node
from rule_extractor import rule_extract


def extract_from_text(text, source="manual", project="default", ms=None, dedup=True, write_daily=True):
    """从一段文本规则提取节点/边候选并入库。返回新增节点数。"""
    ms = ms or MemorySystem()
    candidates = rule_extract(text, project=project, source=source)
    added = 0
    for cand in candidates:
        if cand["kind"] != "node":
            continue
        node = Node(content=cand["content"], node_type=cand["node_type"], project=project,
                    source=source, tags=cand.get("tags", []),
                    confidence=cand.get("confidence", 0.6))
        nid, created = ms.add_node_with_vector(node, dedup=dedup)
        if created:
            added += 1
            for e in cand.get("edges", []):
                # 边指向同批候选中的节点：此处用内容前缀回查真实 id
                target = _find_node_by_prefix(ms, e["to"].replace("node_ref_", ""), project)
                if target:
                    ms.graph.add_edge(Edge(from_id=nid, to_id=target, edge_type=e["type"],
                                           source=source, description=e.get("desc", "")))
            # 自动语义关联老节点（消灭孤岛）：新记忆挂到已有知识树上
            try:
                from linker import auto_link
                auto_link(ms, nid, cand["content"], project=project)
            except Exception:
                pass
    # LLM 增强（可选配置；默认关闭）
    if len(text) > config.EXTRACT_LLM_LENGTH and config.EXTRACT_LLM_ENDPOINT:
        _llm_enhance(text, project, source, ms)
    if write_daily:
        _write_daily_memory(text, source, project)
    return added


def _find_node_by_prefix(ms, prefix, project):
    if not prefix:
        return None
    nodes = ms.graph.list_nodes(project=project, limit=500)
    for n in nodes:
        if n.content.startswith(prefix) or prefix in n.content[:60]:
            return n.id
    return None


def _llm_enhance(text, project, source, ms):
    """外部 LLM 增强：POST {text, project} → {nodes:[{content,node_type,tags}]}。
    配置 SUPERMEM_LLM_ENDPOINT 后启用；失败仅记日志，不影响主流程。"""
    try:
        req = urllib.request.Request(
            config.EXTRACT_LLM_ENDPOINT,
            data=json.dumps({"text": text, "project": project}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for item in data.get("nodes", []):
            node = Node(content=item["content"][:500], node_type=item.get("node_type", "insight"),
                        project=project, source=f"llm:{source}",
                        tags=item.get("tags", []), confidence=0.9)
            ms.add_node_with_vector(node)
    except Exception as e:
        print(f"[extract] LLM enhance failed: {e}")


def _write_daily_memory(text, source, project):
    """双向提取回写：当日 memory/YYYY-MM-DD.md"""
    os.makedirs(config.MEMORY_DIR, exist_ok=True)
    day = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(config.MEMORY_DIR, f"{day}.md")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"- [{datetime.now().strftime('%H:%M')}] ({source}/{project}) {text.strip()}\n")


def extract_dialogue_entries(ms=None):
    """cron 提取：扫描 memory/*.md 对话记录，抽取入库（INSERT OR IGNORE 去重）。返回新增数。"""
    ms = ms or MemorySystem()
    os.makedirs(config.MEMORY_DIR, exist_ok=True)
    added = 0
    for fn in sorted(os.listdir(config.MEMORY_DIR)):
        if not fn.endswith(".md"):
            continue
        path = os.path.join(config.MEMORY_DIR, fn)
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or not line.startswith("- ["):
                    continue
                content = line.split("]", 1)[-1].strip()
                if len(content) >= 4:
                    added += extract_from_text(content, source="dialogue", project="default",
                                               ms=ms, write_daily=False)
    return added
