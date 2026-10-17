# -*- coding: utf-8 -*-
"""
超级记忆系统 —— 看门狗 watchdog.py
职责：
  1. 周期性检查 127.0.0.1:18766 /health；
  2. 连续失败达到阈值 → 按端口 18766 精确清理残留进程 → 重新拉起 main.py；
  3. 常驻后台（配合 Windows 计划任务实现开机自启）。
用法：pythonw scripts/watchdog.py
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import config  # noqa: E402

# [FIX] pythonw 下无控制台，sys.stdout/stderr 为 None，print 会崩溃。
# 重定向到日志文件，保证守护进程在 pythonw 下稳定运行。
_LOGFILE = os.path.join(config.STORAGE_DIR, "logs", "watchdog.log")
os.makedirs(os.path.dirname(_LOGFILE), exist_ok=True)
if sys.stdout is None or not hasattr(sys.stdout, "write"):
    sys.stdout = open(_LOGFILE, "a", encoding="utf-8", buffering=1)
if sys.stderr is None or not hasattr(sys.stderr, "write"):
    sys.stderr = sys.stdout

PORT = config.PORT
TOKEN = config.API_TOKEN
MAIN_PY = os.path.join(BASE_DIR, "main.py")
PYTHONW = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
if not os.path.exists(PYTHONW):
    PYTHONW = sys.executable  # 兜底

CHECK_INTERVAL = 30        # 秒
FAIL_LIMIT = 3             # 连续失败次数阈值
STARTUP_WAIT = 8           # 拉起后等待模型/服务就绪的秒数


def health_ok():
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}/health",
            headers={"X-API-Token": TOKEN})
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def kill_by_port(port):
    """按端口精确清理监听进程（只动本系统独占端口，绝不误杀其他进程）。"""
    try:
        res = subprocess.run(["netstat", "-ano"], capture_output=True, timeout=15,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return
    if res is None or res.stdout is None:  # [FIX] pythonw 环境下可能返回 None
        return
    # [FIX] Windows 下 netstat 输出为 GBK，UTF-8 环境直接 text=True 会解码崩溃
    out = res.stdout.decode("gbk", errors="replace")
    for line in out.splitlines():
        if f":{port}" in line and "LISTENING" in line:
            pid = line.strip().split()[-1]
            if pid.isdigit():
                subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def ensure_service():
    """清理残留并重新拉起服务。返回是否成功。"""
    kill_by_port(PORT)
    time.sleep(2)
    try:
        subprocess.Popen([PYTHONW, MAIN_PY], cwd=BASE_DIR,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        # [FIX] fallback 也必须无窗口：用 python.exe 带控制台拉起会闪黑框
        subprocess.Popen([sys.executable, MAIN_PY], cwd=BASE_DIR,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    # 等待服务就绪
    for _ in range(int(STARTUP_WAIT / 2) + 6):
        time.sleep(2)
        if health_ok():
            return True
    return False


def main():
    print(f"[watchdog] 启动：监控 127.0.0.1:{PORT}，每 {CHECK_INTERVAL}s 检查，阈值 {FAIL_LIMIT}")
    fails = 0
    while True:
        if health_ok():
            if fails:
                print("[watchdog] 服务恢复")
            fails = 0
        else:
            fails += 1
            print(f"[watchdog] 检查失败 {fails}/{FAIL_LIMIT}")
            if fails >= FAIL_LIMIT:
                print("[watchdog] 正在拉起服务…")
                ok = ensure_service()
                print(f"[watchdog] 拉起结果：{'成功' if ok else '失败，下一轮重试'}")
                fails = 0 if ok else fails
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("[watchdog] 退出")
