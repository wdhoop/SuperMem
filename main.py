# -*- coding: utf-8 -*-
"""
超级记忆系统 —— 启动入口 main.py
初始化 → 模型就位 → 调度器 → REST API。
用法：python main.py
"""
import os
import sys

import config
from core import MemorySystem, VectorSearch

# [FIX] pythonw 无控制台时保护 stdout/stderr，日志落到文件（守护进程场景）
_LOGFILE = os.path.join(config.STORAGE_DIR, "logs", "main.log")
os.makedirs(os.path.dirname(_LOGFILE), exist_ok=True)
if sys.stdout is None or not hasattr(sys.stdout, "write"):
    sys.stdout = open(_LOGFILE, "a", encoding="utf-8", buffering=1)
if sys.stderr is None or not hasattr(sys.stderr, "write"):
    sys.stderr = sys.stdout


def ensure_model():
    """确保 BGE 模型就位。缺失时从 HuggingFace 下载（支持 HF_ENDPOINT 镜像）。"""
    try:
        VectorSearch.ensure_model()
        loc = config.EMBED_MODEL_DIR if __import__("os").path.isdir(config.EMBED_MODEL_DIR) \
            else config.EMBED_MODEL_NAME
        print(f"[model] BGE 向量模型就绪：{loc}")
        return True
    except Exception as e:
        print(f"[model] 警告：模型暂不可用（向量检索降级）：{e}")
        print("        提示：可设置 HF_ENDPOINT=https://hf-mirror.com 后重启加速下载")
        return False


def main():
    ms = MemorySystem()
    s = ms.graph.stats()
    print(f"[init] 存储：{config.DB_PATH}")
    print(f"[init] 现状：nodes={s['nodes']} edges={s['edges']} episodes={s['episodes']}")

    ensure_model()

    from scheduler import Scheduler
    sched = Scheduler(ms)
    sched.start()
    print("[scheduler] 已启动：每日衰减 02:00 / 对话提取 每2小时 / 周体检 周日03:00 "
          "/ episode聚合 04:00 / 本地备份 03:30（不含 Hive 备份）")

    from api import start_server
    httpd = start_server(ms)
    print(f"[api] 服务地址：http://{config.HOST}:{config.PORT}")
    print(f"[api] 认证 Token：{config.API_TOKEN}")
    print("      调用示例：")
    print(f"        curl -H 'X-API-Token: {config.API_TOKEN}' http://127.0.0.1:{config.PORT}/health")
    print(f"        curl -H 'X-API-Token: {config.API_TOKEN}' -X POST http://127.0.0.1:{config.PORT}/extract "
          "-d '{{\"text\":\"今天决定把记忆系统部署到本地\"}}'")
    print("      Ctrl+C 停止")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[exit] 正在退出…")
        sched._stop.set()
        httpd.server_close()
        print("[exit] 已完成")


if __name__ == "__main__":
    main()
