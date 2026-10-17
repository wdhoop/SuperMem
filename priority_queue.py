# -*- coding: utf-8 -*-
"""
超级记忆系统 —— 优先级队列 priority_queue.py
按 (weight + 时效) 排序的待处理队列，供提取/维护任务按优先级消费。
"""
import heapq
from datetime import datetime


class PriorityQueue:
    def __init__(self):
        self._heap = []
        self._seen = set()

    def push(self, item, priority):
        """priority 越大越先处理"""
        if item in self._seen:
            return
        self._seen.add(item)
        heapq.heappush(self._heap, (-priority, item))

    def pop(self):
        if not self._heap:
            return None
        neg, item = heapq.heappop(self._heap)
        return item

    def drain(self, max_items=100):
        out = []
        while self._heap and len(out) < max_items:
            out.append(self.pop())
        return out

    def __len__(self):
        return len(self._heap)


def priority_of(node):
    """节点优先级：weight 为主，访问次数为辅"""
    recency = 0.0
    try:
        ts = datetime.strptime(node.timestamp, "%Y-%m-%d %H:%M")
        recency = max(0.0, 1.0 - (datetime.now() - ts).total_seconds() / (86400 * 30))
    except (ValueError, TypeError):
        pass
    return node.weight * 0.7 + recency * 0.3
