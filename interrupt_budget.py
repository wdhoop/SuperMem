# -*- coding: utf-8 -*-
"""
超级记忆系统 —— 打断预算 interrupt_budget.py
限制单轮维护任务的处理量（预算），防止 cron 长任务阻塞服务。
"""
import time


class Budget:
    def __init__(self, max_items=200, max_seconds=30):
        self.max_items = max_items
        self.max_seconds = max_seconds
        self._start = None
        self._used = 0

    def __enter__(self):
        self._start = time.time()
        self._used = 0
        return self

    def __exit__(self, *a):
        self._start = None

    @property
    def exhausted(self):
        if self._start is None:
            return False
        self._used += 1
        return self._used > self.max_items or (time.time() - self._start) > self.max_seconds

    def spend(self):
        self._used += 1
        return self.exhausted
