# -*- coding: utf-8 -*-
"""
超级记忆系统 —— 打断预算 interrupt_budget.py
限制单轮维护任务的处理量（预算），防止 cron 长任务阻塞服务。
"""
import time


class Budget:
    """单轮处理量预算：max_items 项 / max_seconds 秒，先到先算耗尽。

    [FIX 2026-11-13 P1-8] 原实现有两个语义错误：
      ① `exhausted` 是 property，却在里面 `self._used += 1`——读一次就等于消费一次，
         于是 max_items=3 实际只能处理 1 项（测试实测只消费 1 次）；
      ② `__exit__` 把 `_start` 置 None 后，`exhausted` 直接返回 False——
         退出 with 之后任何调用方都被告知"预算没耗尽"，cron 长任务因此不收敛。
    现在：exhausted 是纯查询；计数只由 spend() 递增；上下文外视为已耗尽。"""

    def __init__(self, max_items=200, max_seconds=30):
        self.max_items = max_items
        self.max_seconds = max_seconds
        self._start = None
        self._used = 0
        self._closed = False

    def __enter__(self):
        self._start = time.time()
        self._used = 0
        self._closed = False
        return self

    def __exit__(self, *a):
        self._start = None
        self._closed = True

    @property
    def used(self):
        return self._used

    @property
    def exhausted(self):
        if self._start is None:
            return self._closed   # 上下文外：本轮的账已经结了，一律视为耗尽
        return self._used >= self.max_items or (time.time() - self._start) > self.max_seconds

    def spend(self, n=1):
        """记一次消费，返回消费后是否已耗尽。"""
        if self._start is None:
            return True
        self._used += n
        return self.exhausted
