# -*- coding: utf-8 -*-
"""
超级记忆系统 —— 事件归一 event_normalizer.py
时间/类型/文本的规范化，统一入库口径。
"""
import re
from datetime import datetime

import config


_NOW_FMT = "%Y-%m-%d %H:%M:%S"


def normalize_timestamp(ts):
    """归一为 YYYY-MM-DD HH:MM:SS。

    [FIX 2026-11-13 P2-18] 原实现输出 "%Y-%m-%d %H:%M"（无秒），与 Node.created_at
    的带秒格式、以及库里已有的 ISO 带 T / epoch 数字混成 4 种写法，跨格式比较全靠
    各处自己写容错。现在统一带秒，并接受 epoch（int/float/纯数字串）、ISO（含 T、
    含时区）、纯日期；真的无法解析才回退 now。"""
    if ts is None or ts == "":
        return datetime.now().strftime(_NOW_FMT)
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(float(ts)).strftime(_NOW_FMT)
        except (ValueError, OSError, OverflowError):
            return datetime.now().strftime(_NOW_FMT)
    s = str(ts).strip()
    if re.match(r"^\d{10}(\.\d+)?$", s):
        try:
            return datetime.fromtimestamp(float(s)).strftime(_NOW_FMT)
        except (ValueError, OSError, OverflowError):
            return datetime.now().strftime(_NOW_FMT)
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?", s)
    if m:
        y, mo, d, h, mi, sec = m.groups()
        return f"{y}-{mo}-{d} {int(h):02d}:{mi}:{int(sec or 0):02d}"
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return f"{s} 00:00:00"
    return datetime.now().strftime(_NOW_FMT)


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
             "system": "system", "系统": "system", "book": "book", "书目": "book",
             "goal": "goal", "目标": "goal", "愿望": "goal",
             "state": "state", "状态": "state", "态势": "state"}
    if t in exact:
        return exact[t]
    if t in config.NODE_TYPES:
        return t
    return "event"


def normalize_edge_type(t):
    """边类型归一：先精确匹配 config.EDGE_TYPES，再查中英文别名表，未知归 related。

    [FIX 2026-11-13 P1-4] 原实现把 trigger 压平成 related：config.EDGE_TYPES 里既无
    trigger 也无 enable，别名表里也没有 trigger 的任何写法，于是"触发/原因/causes"
    提交上来全落成 related——因果链断裂，CausalEngine 拿不到边。config 已补
    trigger/enable，这里补对应中英文别名。"""
    t = (t or "").strip().lower()
    if t in config.EDGE_TYPES:
        return t
    # 中文/历史遗留边名归一
    alias = {"部分": "part_of", "包含": "part_of", "派生": "derives_insight",
             "应用": "applies", "改进": "improves", "进化": "evolves_from",
             "同主题": "shares_theme_with", "相关": "related", "合并": "merged_into",
             "取代": "replaced_by", "使用规则": "uses_rule", "包含技法": "contains_technique",
             "反驳": "refutes", "使能": "enable", "导致": "enable", "enable": "enable",
             "derived_from": "derives_insight", "relates_to": "related", "relate": "related",
             "关联": "related", "有关系": "related",
             # [FIX 2026-11-13 P1-4] 因果触发类（原来全被压平成 related）
             "触发": "trigger", "引发": "trigger", "起因": "trigger", "原因": "trigger",
             "triggered_by": "trigger", "causes": "trigger", "cause": "trigger",
             "trigger": "trigger", "leads_to": "trigger",
             # [UPD 2026-10-20 时间维] 时序连接词归一
             "之前": "before", "此前": "before", "起初": "before", "最初": "before",
             "之后": "after", "后来": "after", "随后": "after", "此后": "after",
             "最终": "after", "更新为": "superseded_by", "升级为": "superseded_by",
             "演进为": "superseded_by", "新方案取代": "superseded_by",
             # [UPD 2026-10-20 空间维] 情境边归一
             "涉及": "involves", "提到": "involves", "用到": "involves", "发生于": "occurred_in",
             "发生在": "occurred_in", "发生场景": "occurred_in",
             # [UPD 2026-10-20 价值维] 价值边归一
             "有助于": "contributes_to", "促进": "contributes_to", "服务于": "contributes_to",
             "支撑": "contributes_to", "阻碍": "blocks", "妨碍": "blocks", "冲突": "blocks",
             # [UPD 2026-10-20 状态维] 状态边归一
             "状态变为": "transitions_to", "状态变成": "transitions_to",
             "状态切换到": "transitions_to", "切换为": "transitions_to",
             "状态改为": "transitions_to", "状态改成": "transitions_to", "进入": "transitions_to"}
    if t in alias:
        return alias[t]
    return "related"   # 未知类型归入 related（治理中逐步归一）


def normalize_text(t):
    return re.sub(r"\s+", " ", (t or "").strip())
