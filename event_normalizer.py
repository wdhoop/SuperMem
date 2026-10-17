# -*- coding: utf-8 -*-
"""
超级记忆系统 —— 事件归一 event_normalizer.py
时间/类型/文本的规范化，统一入库口径。
"""
import re
from datetime import datetime

import config


def normalize_timestamp(ts):
    """归一为 YYYY-MM-DD HH:MM；非法输入回退为当前时间。"""
    if not ts:
        return datetime.now().strftime("%Y-%m-%d %H:%M")
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{1,2}):(\d{2})", str(ts))
    if m:
        y, mo, d, h, mi = m.groups()
        return f"{y}-{mo}-{d} {int(h):02d}:{mi}"
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def normalize_node_type(t):
    """模糊映射到标准 NODE_TYPES；无法映射时返回 event 并记录。"""
    t = (t or "").strip().lower()
    exact = {"event": "event", "insight": "insight", "心得": "insight", "认知": "insight",
             "reference": "reference", "参考": "reference", "material": "material",
             "素材": "material", "decision": "decision", "决定": "decision", "决策": "decision",
             "technique": "technique", "技法": "technique", "rule": "rule", "规则": "rule",
             "task": "task", "任务": "task", "skill": "skill", "技能": "skill",
             "relation": "relation", "关系": "relation", "project": "project", "项目": "project",
             "concept": "concept", "概念": "concept", "person": "person", "人物": "person",
             "issue": "issue", "问题": "issue", "plot": "plot", "情节": "plot",
             "item": "item", "物件": "item", "scene": "scene", "场景": "scene",
             "work": "work", "作品": "work", "question": "question", "疑问": "question",
             "system": "system", "系统": "system", "book": "book", "书目": "book"}
    if t in exact:
        return exact[t]
    if t in config.NODE_TYPES:
        return t
    return "event"


def normalize_edge_type(t):
    t = (t or "").strip().lower()
    if t in config.EDGE_TYPES:
        return t
    # 中文/历史遗留边名归一
    alias = {"部分": "part_of", "包含": "part_of", "派生": "derives_insight",
             "应用": "applies", "改进": "improves", "进化": "evolves_from",
             "同主题": "shares_theme_with", "相关": "related", "合并": "merged_into",
             "取代": "replaced_by", "使用规则": "uses_rule", "包含技法": "contains_technique",
             "反驳": "refutes", "使能": "enable", "导致": "enable", "enable": "enable",
             "derived_from": "derives_insight", "relates_to": "related"}
    if t in alias:
        return alias[t]
    return "related"   # 未知类型归入 related（治理中逐步归一）


def normalize_text(t):
    return re.sub(r"\s+", " ", (t or "").strip())
