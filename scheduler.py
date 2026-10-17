# -*- coding: utf-8 -*-
"""
超级记忆系统 —— 定时调度 scheduler.py
Windows 兼容的 cron 调度（纯 Python，后台线程每分钟 tick）。
任务来自 config.CRON_JOBS；按用户要求【不注册】"周备份到 Hive"，
仅启用本地备份（SQLite + chroma_db → backup/，保留最近 N 份）。
"""
import os
import shutil
import threading
from datetime import datetime

import config


# ---------- cron 表达式（5 字段：分 时 日 月 周；周 0=周日） ----------
def _parse_field(field, lo, hi):
    vals = set()
    for part in str(field).split(","):
        part = part.strip()
        if part == "*":
            vals.update(range(lo, hi + 1))
        elif "/" in part:
            base, step = part.split("/")
            step = int(step)
            if base == "*":
                vals.update(range(lo, hi + 1, step))
            else:
                vals.update(range(int(base), hi + 1, step))
        elif "-" in part:
            a, b = part.split("-")
            vals.update(range(int(a), int(b) + 1))
        else:
            vals.add(int(part))
    return vals


class CronExpr:
    def __init__(self, expr):
        f = expr.split()
        if len(f) != 5:
            raise ValueError(f"bad cron expr: {expr}")
        self.min = _parse_field(f[0], 0, 59)
        self.hour = _parse_field(f[1], 0, 23)
        self.day = _parse_field(f[2], 1, 31)
        self.mon = _parse_field(f[3], 1, 12)
        self.wday = _parse_field(f[4], 0, 6)

    def matches(self, now):
        cron_wday = (now.weekday() + 1) % 7  # python 周一=0 → cron 周日=0
        return (now.minute in self.min and now.hour in self.hour
                and now.day in self.day and now.month in self.mon
                and cron_wday in self.wday)


# ---------- 本地备份（[FIX] 启用；不做 Hive 备份） ----------
def backup_now(ms):
    """双备：SQLite + chroma_db → backup/<时间戳>/，保留 BACKUP_KEEP 份"""
    try:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(config.BACKUP_DIR, stamp)
        os.makedirs(dest, exist_ok=True)
        ms.graph.conn.execute("PRAGMA wal_checkpoint(FULL)")
        shutil.copy2(config.DB_PATH, os.path.join(dest, "super_memory.db"))
        if os.path.exists(config.DB_PATH + "-wal"):
            shutil.copy2(config.DB_PATH + "-wal", os.path.join(dest, "super_memory.db-wal"))
        if os.path.isdir(config.CHROMA_DIR):
            shutil.copytree(config.CHROMA_DIR, os.path.join(dest, "chroma_db"))
        backups = sorted([d for d in os.listdir(config.BACKUP_DIR)
                          if os.path.isdir(os.path.join(config.BACKUP_DIR, d))])
        for old in backups[:-config.BACKUP_KEEP]:
            shutil.rmtree(os.path.join(config.BACKUP_DIR, old), ignore_errors=True)
        return dest
    except Exception as e:
        print(f"[backup] 失败: {e}")
        return None


# ---------- 调度器 ----------
class Scheduler:
    def __init__(self, ms):
        self.ms = ms
        self.jobs = [{"name": j["name"], "expr": CronExpr(j["expr"]), "fn": j["fn"]}
                     for j in config.CRON_JOBS]
        self._last_run = {}
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(30)

    def tick(self):
        now = datetime.now()
        for job in self.jobs:
            if job["expr"].matches(now):
                key = f"{job['name']}@{now.strftime('%Y%m%d%H%M')}"
                if key not in self._last_run:
                    self._last_run[key] = True
                    threading.Thread(target=self._run, args=(job,), daemon=True).start()

    def _run(self, job):
        print(f"[scheduler] {job['name']} @ {datetime.now():%Y-%m-%d %H:%M}")
        try:
            getattr(self, job["fn"])()
        except Exception as e:
            print(f"[scheduler] {job['name']} 失败: {e}")

    # ---- 任务实现 ----
    def daily_decay(self):
        archived = self.ms.graph.apply_decay()
        if archived:
            self.ms.graph.archive_nodes(archived)
            for nid in archived:
                self.ms.vector.delete(nid)
        self.ms.graph.save_snapshot_meta()
        print(f"[decay] 已衰减；归档 {len(archived)} 条")

    def extract_memories(self):
        from extract import extract_dialogue_entries
        n = extract_dialogue_entries(self.ms)
        if n:
            print(f"[extract] 新增 {n} 条记忆")

    def weekly_selfcheck(self):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))
        from selfcheck import run_selfcheck
        report = run_selfcheck(self.ms, dry_run=False)
        print(f"[selfcheck] {report['summary']}")

    def build_episodes(self):
        from time_window import build_episodes
        n = build_episodes(self.ms)
        if n:
            print(f"[episodes] 新增 {n} 个情景块")

    def local_backup(self):
        p = backup_now(self.ms)
        print(f"[backup] 完成 → {p}")
