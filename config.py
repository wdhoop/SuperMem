# -*- coding: utf-8 -*-
"""
超级记忆系统 —— 统一配置
按《超级记忆系统-架构说明书》(2026-09-30) 重建。
重建时已修复评审发现的问题，修复项均在本文件用 [FIX] 标注。
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORAGE_DIR = os.path.join(BASE_DIR, "storage")

# ---- 存储路径 ----
DB_PATH = os.path.join(STORAGE_DIR, "super_memory.db")
CHROMA_DIR = os.path.join(STORAGE_DIR, "chroma_db")
ARCHIVE_DIR = os.path.join(STORAGE_DIR, "archive")
BACKUP_DIR = os.path.join(STORAGE_DIR, "backup")
MEMORY_DIR = os.path.join(STORAGE_DIR, "memory")       # 回写 memory/YYYY-MM-DD.md
MODELS_DIR = os.path.join(STORAGE_DIR, "models")       # 本地模型目录
SELFCHECK_LOG_DIR = os.path.join(STORAGE_DIR, "selfcheck_logs")

# ---- 服务（[FIX] 默认只绑定本机 + Token 认证）----
HOST = "127.0.0.1"
PORT = 18766  # 原架构 8765 被本机百度输入法占用；18765 被 pythonw 占用，均避开
API_TOKEN = os.environ.get("SUPERMEM_API_TOKEN", "change-me")

# ---- 向量索引 ----
EMBED_MODEL_NAME = "BAAI/bge-small-zh-v1.5"
EMBED_DIM = 512
EMBED_MAX_SEQ = 512
COLLECTION = "super_memory_nodes"
EMBED_MODEL_DIR = os.path.join(MODELS_DIR, "bge-small-zh-v1.5")  # [FIX] 模型本地化，离线可用
EMBED_BATCH = 32

# [FIX] 向量去重阈值可配置（原架构硬编码 0.85）
DEDUP_THRESHOLD = float(os.environ.get("SUPERMEM_DEDUP_THRESHOLD", "0.85"))

# ---- 遗忘曲线（按说明书 §六）----
W0 = 0.7
ARCHIVE_THRESHOLD = 0.2
CAUSAL_FACTOR_RATE = 0.3
CAUSAL_FACTOR_MAX = 2.0
# 重要性阶梯：S/A/B/C
LAMBDA_BY_TYPE = {
    "S": 0.0005,   # 半衰期 ~6.9 年，floor=0.8
    "A": 0.0030,   # decision / project
    "B": 0.008,    # task
    "C": 0.03,     # insight
}
FLOOR_BY_TYPE = {"S": 0.8, "A": 0.35, "B": 0.30, "C": 0.20}
# 默认级别映射（说明书中未列出的类型给默认值，标注为重建时补充）
TYPE_LEVEL_DEFAULT = "C"
TYPE_LEVEL_MAP = {"decision": "A", "project": "A", "task": "B", "insight": "C"}
STATUS_MOD = {"hypothesis": 1.1, "confirmed": 1.0, "outdated": 0.3, "obsolete": 0.1, "active": 1.0,
              "permanent": 1.0, "disputed": 0.6, "merged": 0.3}

# 权重分区（§六）
CORE_MIN = 0.8
WORKING_MIN = 0.5
COLD_MIN = 0.2

# ---- 检索（[FIX] 修复双轨制：统一为“召回 → 精排”单管线）----
# 召回权重（候选粗筛）
RECALL_WEIGHTS = {"vector": 0.35, "tag": 0.20, "recency": 0.20, "weight": 0.15, "access": 0.10}
# 精排权重（V5 三重排：向量 / 衰减权重 / 边数 / 访问）
RERANK_WEIGHTS = {"vector": 0.50, "weight": 0.25, "edge_count": 0.15, "access": 0.10}
# 类型加分（decision / rule / technique 高价值）
TYPE_BONUS = {"decision": 0.10, "rule": 0.10, "technique": 0.08, "insight": 0.05}
RECALL_TOP_K = 40
SEARCH_LIMIT_DEFAULT = 10

# ---- 因果链（[FIX] 只沿因果语义边遍历，避免 part_of/evolves_from 稀释因果）----
CAUSAL_EDGE_TYPES = {"enable", "derives_insight", "refutes", "improves"}
CAUSAL_DEPTH = 3

# ---- 原文层 verbatim（2026-10-17 新增）----
# 逐字原文层：日常检索不触碰，强召回（置信低 / 细节追问 / 溯源）才触发。
VERBATIM_COLLECTION = "super_memory_verbatim"   # 独立原文向量集合（与 nodes 集合分开）
STRONG_RECALL_FLOOR = 0.005                     # RRF 融合分极端低（<0.005）→ 自动下探原文（辅助信号）
# 主触发信号为比较型（search_fusion.search_strong 内实现）：
#   原文向量 top1 相似度 > 图向量 top1 相似度 → 下探原文（原文比图更懂这个查询）
# 实测依据（2026-10-17 压力测试）：绝对相似度阈值不可靠——相关对儿 0.668 与
# 无关泛化 0.576/0.625 混在同一区间；比较型判断自适应分布，已覆盖实体/字面类盲区。
VERBATIM_STRONG_TOP_K = 5                       # 原文强召回条数
VERBATIM_QUERY_FETCH = 500                      # 原文向量检索原始召回面（HNSW 近似漏召修复：拉大检索面后客户端精确重排）
VERBATIM_MAX_LEN = 4000                         # 单块原文最大字符数（超出自动截断落块）

# ---- 联想检索 assoc（2026-10-19 评测回写：LoCoMo-Plus 联想桥 / HyDE query 扩展）----
# 可选增强：强召回时用"无思考模式"LLM 生成联想句扩展查询（评测实证：在隐藏抽象关联
# 基准上检索供给能力翻倍，causal/goal/value 全覆盖 +23~43 点）。这是检索管线加
# LLM query 生成的策略升级（HyDE 同类），不是对记忆图谱本身的改动。
# 不配置（ASSOC_ENABLED=False 或端点为空）则完全关闭，检索行为与旧版完全一致。
ASSOC_ENABLED = False                          # 总开关（默认关，不改变现有行为）
ASSOC_ENDPOINT = ""                            # OpenAI 兼容端点，如 https://api.deepseek.com/v1
ASSOC_KEY = ""                                 # API key（留空则关闭）
ASSOC_MODEL = "deepseek-chat"                  # 无思考模式档（评测实证：比推理档快 30 倍且质量更好）
ASSOC_TIMEOUT = 20                             # 单次联想生成超时（秒）
ASSOC_N = 4                                    # 联想句条数（评测默认 8，生产取 4 平衡延迟与增益）
ASSOC_MAX_QUERY_LEN = 400                      # trigger 超长截断（字符）

# ---- 状态机（说明书 §五）----
STATUSES = ["active", "confirmed", "permanent", "hypothesis",
            "outdated", "obsolete", "disputed", "merged"]
# 认错巡检关键词
DECISION_REVERSE_KEYWORDS = ["放弃", "不再", "改为", "换成", "撤销", "否决", "取消", "暂停", "回滚"]

# ---- 维护任务（[FIX] 启用本地备份；按用户要求不启用“周备份到 Hive”）----
# cron 表达式: 分 时 日 月 周
CRON_JOBS = [
    {"name": "每日记忆衰减",      "expr": "0 2 * * *",      "fn": "daily_decay"},
    {"name": "本地对话记忆提取",  "expr": "0 0,2,4,6,8,12,18,20,22 * * *", "fn": "extract_memories"},
    {"name": "每周记忆体检",      "expr": "0 3 * * 0",       "fn": "weekly_selfcheck"},
    {"name": "episode 聚合",      "expr": "0 4 * * *",       "fn": "build_episodes"},
    {"name": "系统日常维护备份",  "expr": "30 3 * * *",      "fn": "local_backup"},
]
# 备份保留份数
BACKUP_KEEP = 14

# ---- 提取 ----
EXTRACT_LLM_ENDPOINT = os.environ.get("SUPERMEM_LLM_ENDPOINT", "")  # 外部 LLM 增强（可选，默认关）
EXTRACT_LLM_LENGTH = 200  # 内容超过该长度才触发 LLM 增强

# 节点类型（说明书 §2.1）
NODE_TYPES = ["event", "insight", "reference", "material", "decision", "technique", "rule", "task",
              "skill", "relation", "project", "concept", "person", "issue", "plot", "item", "scene",
              "work", "question", "system", "book"]

# 标准边类型（说明书 §2.2）
EDGE_TYPES = ["part_of", "derives_insight", "applies", "improves", "evolves_from",
              "shares_theme_with", "related", "merged_into", "replaced_by", "uses_rule",
              "contains_technique", "refutes"]

