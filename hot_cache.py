# -*- coding: utf-8 -*-
"""
超级记忆系统 —— 热缓存 hot_cache.py
LRU 缓存最近访问的节点详情，减少高频检索时的 SQLite 压力。
"""
from collections import OrderedDict


class HotCache:
    def __init__(self, capacity=512):
        self.capacity = capacity
        self._data = OrderedDict()

    def get(self, key):
        if key in self._data:
            self._data.move_to_end(key)
            return self._data[key]
        return None

    def put(self, key, value):
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = value
        while len(self._data) > self.capacity:
            self._data.popitem(last=False)

    def invalidate(self, key):
        self._data.pop(key, None)

    def clear(self):
        self._data.clear()

    def __len__(self):
        return len(self._data)
