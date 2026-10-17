# -*- coding: utf-8 -*-
"""
超级记忆系统 —— 规则式记忆提取器 rule_extractor.py
纯规则识别（推断性质在 confidence 中体现，宁缺毋滥）。
识别：decision / event / insight / rule / task / question。
决策反转：含"放弃/不再/改为/换成/撤销/否决/取消/暂停/回滚"的决策句 → 标记 reversed=True（由认错巡检处理）。
"""
import re

import config

DECISION_MARKERS = ["我决定", "决定", "以后就", "今后", "计划", "打算", "确定", "拍板"]
INSIGHT_MARKERS = ["发现", "总结", "心得", "意识到", "认识到", "原来", "其实", "悟到"]
RULE_MARKERS = ["规则", "原则", "必须", "禁止", "一律", "每次都要", "规定"]
TASK_MARKERS = ["待办", "要做", "需要完成", "任务", "下一步", "todo", "计划做"]
QUESTION_MARKERS = ["？", "?", "疑问", "不确定", "想知道", "为什么"]
EVENT_MARKERS = ["今天", "昨天", "发生了", "完成", "上线", "发布", "会议", "遇到", "做了"]

_PRIORITY = ["decision", "rule", "task", "insight", "question", "event"]


def classify(sentence):
    """返回 (node_type, confidence)。多标记命中取优先级最高者。"""
    hits = {}
    for marker, t in [(m, "decision") for m in DECISION_MARKERS] + \
                     [(m, "insight") for m in INSIGHT_MARKERS] + \
                     [(m, "rule") for m in RULE_MARKERS] + \
                     [(m, "task") for m in TASK_MARKERS] + \
                     [(m, "question") for m in QUESTION_MARKERS] + \
                     [(m, "event") for m in EVENT_MARKERS]:
        if marker in sentence:
            hits.setdefault(t, 0)
            hits[t] += 1
    if not hits:
        return None, 0.0
    best = max(hits.items(), key=lambda kv: (_PRIORITY.index(kv[0]), kv[1]))[0]
    conf = min(0.5 + 0.1 * hits[best], 0.9)
    return best, conf


def is_decision_reversal(sentence):
    return any(k in sentence for k in config.DECISION_REVERSE_KEYWORDS)


def rule_extract(text, project="default", source=""):
    """按句拆分 → 规则匹配 → 候选节点列表。
    返回 [{"kind":"node","content":...,"node_type":...,"confidence":...,
           "tags":[...],"edges":[{"to":...,"type":...,"desc":...}]}, ...]
    全部为规则推断，写入时以 confidence 与 source 标记。"""
    sentences = [s.strip() for s in re.split(r"[。！？!?\n；;]+", text) if s.strip()]
    out = []
    for s in sentences:
        if len(s) < 4:
            continue
        ntype, conf = classify(s)
        if not ntype:
            continue
        tags = []
        if ntype == "decision" and is_decision_reversal(s):
            tags.append("reversal")   # 决策反转候选，供认错巡检
        out.append({"kind": "node", "content": s[:500], "node_type": ntype,
                    "confidence": round(conf, 2), "tags": tags,
                    "edges": [], "project": project, "source": source})
    # 同段内 事件→心得 的派生边（推断）：事件派生心得，方向为 event → insight
    events = [n for n in out if n["node_type"] == "event"]
    insights = [n for n in out if n["node_type"] == "insight"]
    if events and insights:
        events[0]["edges"].append({"to": f"node_ref_{insights[0]['content'][:20]}",
                                   "type": "derives_insight",
                                   "desc": "同段事件派生心得（规则推断）"})
    return out
