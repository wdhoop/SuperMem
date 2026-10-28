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
# [UPD 2026-10-28 剪枝优化] 访问热度随时间衰减（半衰期 ~35 天）：
#   热度 heat = access_count * exp(-ACCESS_HEAT_LAMBDA * 距上次访问天数)。
#   修复"访问次数只增不衰"问题：很久以前频繁用、现在不再用的记忆，
#   其热度加成会随冷落逐渐消退（与遗忘曲线同向，属剪枝类优化，不做加法）。
ACCESS_HEAT_LAMBDA = 0.02
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

# ---- 时间维（2026-10-20 新增）----
# 检索时间加权：默认关闭（行为与旧版完全一致）；recency=1 时放大时间加成占比，
# 适合"最近发生了什么 / 近况"类查询。
RECENCY_DEFAULT = 0
RECENCY_WINDOW_DAYS = 30                        # recency 模式下新鲜度参考窗口（天）
# 时序边自动抽取开关（写入侧，默认开启；保守规则见 linker.auto_temporal_link）
TEMPORAL_LINK_ENABLED = True
# 时序连接词（触发 after/before 自动建边的保守识别词）
TEMPORAL_AFTER_KEYWORDS = ["后来", "之后", "随后", "最终", "终于", "于是", "此后",
                           "afterwards", "after that", "later", "eventually", "finally", "in the end"]
TEMPORAL_BEFORE_KEYWORDS = ["之前", "此前", "起初", "最初", "先做了",
                            "before that", "earlier", "initially", "at first", "previously"]

# ---- 空间/场景维（2026-10-20 新增）----
# 场景实体识别与情境建边：新节点 content 提到库内实体节点（person/system/item/scene/
#   project/work/book/concept）时，建 involves 边（事件→实体）。规则先行，实体名子串匹配。
SCENE_LINK_ENABLED = True
SCENE_ENTITY_TYPES = {"person", "system", "item", "scene", "project", "work", "book", "concept"}
# 检索场景过滤（scene 参数）默认关闭——显式传 scene= 才生效
SCENE_SEARCH_WEIGHT = 0.35                   # 场景命中邻居在 RRF 中的额外权重系数

# ---- 价值/目标维（2026-10-20 新增）----
# importance 价值分级：goal_related（直接服务当前目标，检索最高优先）/
#   operational（支撑日常操作，常规优先）/ reference（背景资料，低优先）。
# goal=1 时目标导向重排：库内 goal 节点沿 contributes_to/blocks 边扩散邻居加权，
#   blocks 邻居降权（阻碍目标的记忆在目标语境下后置）。
IMPORTANCE_LEVELS = ("goal_related", "operational", "reference")
GOAL_LINK_ENABLED = True                       # 写入时自动检测目标/愿望/里程碑短语 → goal 节点 + contributes_to 边
GOAL_SEARCH_ENABLED = True
GOAL_SEARCH_WEIGHT = 0.40                    # contributes_to 邻居加权系数
GOAL_BLOCK_PENALTY = 0.35                    # blocks 邻居降权系数

# ---- 状态维（2026-10-20 新增）----
# 状态快照：state 节点记录 agent/世界的当前态势认知（"任务 X 进行中"、"设备离线"等）。
# auto_state_link 规则：事件 content 含显式状态转移词（变为/切换为/改成/进入）时，
#   提取目标状态短语，命中库内 state 节点则建 transitions_to 边，未命中自动创建 state 节点。
#   仅处理显式转移（保守）；"状态为/是"等声明型不自动处理（避免误判），手动建 state 节点即可。
STATE_LINK_ENABLED = True
STATE_TRANSITION_KEYWORDS = ["状态变为", "状态变成", "状态切换到", "状态切换为",
                             "切换为", "状态改为", "状态改成", "改为", "进入",
                             "switched to", "transitioned to", "changed to", "turned into"]
# 状态检索（state 参数）：state=状态名 → 沿 transitions_to 带出"导致该状态"的事件/节点
STATE_SEARCH_WEIGHT = 0.35
# 自动创建 state 节点的 importance 默认值（状态快照对决策有用 → operational）
STATE_NODE_IMPORTANCE = "operational"

# ---- 状态机（说明书 §五）----
STATUSES = ["active", "confirmed", "permanent", "hypothesis",
            "outdated", "obsolete", "disputed", "merged"]
# 认错巡检关键词
DECISION_REVERSE_KEYWORDS = ["放弃", "不再", "改为", "换成", "撤销", "否决", "取消", "暂停", "回滚"]

# ---- 矛盾维（2026-10-28 剪枝优化新增）----
# 认知更新自动退休：新节点 content 命中以下关键词（观点/方案被推翻、放弃、更换），
#   且与库内旧 decision/rule 节点语义高度相似时，自动把旧节点流转为 outdated
#   （replaced_by 指向新节点 + superseded_by 边）。属剪枝类：旧认知下沉，不堆叠。
# 关键词 = DECISION_REVERSE_KEYWORDS（rule_extractor/selfcheck 在用）
#          + event_normalizer 已归一的"更新为/升级为/演进为/新方案取代"
#          + 常见认知修正词。
SUPERSEDE_LINK_ENABLED = True
SUPERSEDE_KEYWORDS = (["放弃", "不再", "改为", "换成", "撤销", "否决", "取消", "暂停", "回滚",
                       "更新为", "升级为", "演进为", "新方案取代", "推翻", "作废", "更正",
                       "不采用", "换用", "改用", "弃用", "撤回", "终止"] +
                      ["give up", "no longer", "switch to", "replace", "supersede", "abandon",
                       "revert", "rollback", "stop using", "change to"])
SUPERSEDE_TOP_K = 3                # 每次最多退休几个旧节点（保守）
SUPERSEDE_MIN_SCORE = 0.50         # 语义命中阈值（比 related 0.45 更严，防误伤）
SUPERSEDE_TARGET_TYPES = {"decision", "rule", "technique", "project"}  # 只退休高价值旧认知

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
# [UPD 2026-10-20 价值维] 新增 goal（目标节点，记忆与用户目标显式挂钩）
# [UPD 2026-10-20 状态维] 新增 state（状态快照节点，agent/世界的当前态势认知）
NODE_TYPES = ["event", "insight", "reference", "material", "decision", "technique", "rule", "task",
              "skill", "relation", "project", "concept", "person", "issue", "plot", "item", "scene",
              "work", "question", "system", "book", "goal", "state"]

# 标准边类型（说明书 §2.2）
# [UPD 2026-10-20 时间维] 新增时序边：before / after（显式先后）、superseded_by（认知更新，
#   与 replaced_by 区分：后者是纠错/版本链，前者是"观点/方案被新认知取代"）
# [UPD 2026-10-20 空间维] 新增情境边：involves（事件涉及场景实体）、occurred_in（发生在某场景）
# [UPD 2026-10-20 价值维] 新增价值边：contributes_to（记忆服务于某目标）、blocks（记忆阻碍某目标）
# [UPD 2026-10-20 状态维] 新增状态边：transitions_to（事件导致状态从 A 变为 B / 进入某状态）
EDGE_TYPES = ["part_of", "derives_insight", "applies", "improves", "evolves_from",
              "shares_theme_with", "related", "merged_into", "replaced_by", "uses_rule",
              "contains_technique", "refutes",
              "before", "after", "superseded_by",
              "involves", "occurred_in",
              "contributes_to", "blocks",
              "transitions_to"]
