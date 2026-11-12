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
API_TOKEN = os.environ.get("SUPERMEM_API_TOKEN", "supermem-local-2026")

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
CAUSAL_FACTOR_MAX = 1.3   # [FIX 2026-11-07] 原 2.0：0.7*2.0=1.4，任何有边的老条目都会顶格 1.0，新旧无法区分
NEWNESS_BOOST = 1.2       # 新写入认知的 7 天新近加成，让新结论先压过旧的
NEWNESS_BOOST_DAYS = 7
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
# [FIX 2026-11-13 P0-1] 权重地板与归档阈值（ARCHIVE_THRESHOLD=0.2）原本数学互斥：
#   C 级 floor=0.20 恰好等于阈值，B=0.30/A=0.35 高于阈值 → 全库 0 个节点能掉到 0.2 以下，
#   归档链路成了死代码（实测 2218 节点中 1048 个 weight 精确停在 0.2000）。
#   现把低价值类型的地板拉开距离：C 0.20→0.05、B 0.30→0.15；A（decision/project）
#   与 S（permanent）保持高地板，高价值认知不参与自动归档。
FLOOR_BY_TYPE = {"S": 0.8, "A": 0.35, "B": 0.15, "C": 0.05}
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
# [FIX 2026-11-13 P0-3] 中文长句切词：原 _tokenize 只按标点/空白切，中文长句整句成一个 token，
#   FTS 腿的 instr 整串匹配必然 0 命中（7 条难例 6 条整句一 token），连带 judge_verdict 的
#   lexical support 全失效。现对中文段按 2-4 字滑窗切 gram，整段 token 仍保留（命中给满分）。
TOKENIZE_CN_MAX_SEG = 4        # 中文段 ≤ 该长度直接整段作 token
TOKENIZE_CN_GRAMS = (4, 3, 2)  # 长段滑窗宽度（从长到短，长 gram 优先）
TOKENIZE_CN_STEP = 2           # 滑窗步长
TOKENIZE_MAX_TOKENS = 24       # 单次查询最多多少个 token（防 SQL OR 条件爆炸）
SEARCH_TOKEN_MIN_LEN = 2       # 中文 token 最小长度（单字噪声太大，不进 FTS）
EPISODE_LIMIT_DEFAULT = 200    # [FIX 2026-11-13 P2-13] /episodes 默认返回条数（原 50 条截断）

# ---- 因果链（[FIX] 只沿因果语义边遍历，避免 part_of/evolves_from 稀释因果）----
CAUSAL_EDGE_TYPES = {"enable", "trigger", "derives_insight", "refutes", "improves"}
CAUSAL_DEPTH = 3

# ---- 语义关联链路（2026-11-03 修复：auto_link 双向化 + related 检索扩散）----
# 背景：auto_link 原 k=2 限量导致同事件多口径记忆（如"解锁广播"与"挖矿实测"）因
#   top-2 截断/写入时间差漏边；检索侧 causal 路又只沿因果语义边扩散，related 边
#   "有边不用"——表现为"搜显卡算力解锁带不出挖矿实测"。
# 修复：① 写入侧 k 2→8 有限放宽 + 双向建边（新→老 与 老→新，复用同一次 query，
#   成本≈0，根治时间差漏边）；② 阈值分层（强关联全建、弱关联限量，防图稠密稀释）；
#   ③ 检索侧新增 related 扩散路（默认开），related 邻居并入 RRF。
AUTO_LINK_K = 8                    # 每节点 related 边上限（原 2，有限放宽防稠密）
AUTO_LINK_MIN_SCORE = 0.45         # related 边最低语义分（原值不变）
AUTO_LINK_STRONG_SCORE = 0.70      # 强关联阈值：>=0.70 的候选全建（不额外限量）
AUTO_LINK_MID_MAX = 3              # 弱关联[0.45,0.70)每节点最多建几条（分层防稀释）
AUTO_LINK_QUERY_K = 20             # 向量检索候选面（含强+弱候选）
RELATED_DIFFUSE_ENABLED = True     # 检索侧 related 边扩散开关
# [FIX 2026-11-13 P0-2] 扩散路原本与 vector 第 1 名同权（related_rank 从 1 起算 1/(60+rank)，
#   每查询平均灌 12.8 个候选，实测 20 条 related 边里 7 条完全无关），把正确答案挤出 top1。
#   现：① 扩散只做"补位"，主路召回过的候选分数完全不受扩散影响（实测扩散原为净拖累：
#     关掉扩散 @1=53.0%，开着 34.3%，差 22pt 的 top1 被扩散抢走）；
#      ② 上限 20→8；③ 只扩散强度够的边（弱边不入 RRF）。
RELATED_DIFFUSE_MAX = 8            # 扩散邻居并入候选池的上限（防候选爆炸）
RELATED_DIFFUSE_WEIGHT = 0.7       # 扩散分权重系数（只在主路零命中时兜底，单次分量需够大）
                                   # [FIX 2026-11-13 P0-2b] 每个候选只计一次扩散（不再按命中次数累加）。
RELATED_DIFFUSE_MIN_STRENGTH = 0.55  # 边的语义强度下限（auto_link 写入时落 strength=score）

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
SCENE_SEARCH_WEIGHT = 0.18                   # 场景命中邻居在 RRF 中的额外权重系数
                                             # [FIX 2026-11-13] 0.35→0.18：单路边邻居分不应超过 RRF 第 1 名的 20%

# ---- 价值/目标维（2026-10-20 新增）----
# importance 价值分级：goal_related（直接服务当前目标，检索最高优先）/
#   operational（支撑日常操作，常规优先）/ reference（背景资料，低优先）。
# goal=1 时目标导向重排：库内 goal 节点沿 contributes_to/blocks 边扩散邻居加权，
#   blocks 邻居降权（阻碍目标的记忆在目标语境下后置）。
IMPORTANCE_LEVELS = ("goal_related", "operational", "reference")
GOAL_LINK_ENABLED = True                       # 写入时自动检测目标/愿望/里程碑短语 → goal 节点 + contributes_to 边
GOAL_SEARCH_ENABLED = True
GOAL_SEARCH_WEIGHT = 0.20                    # contributes_to 邻居加权系数
                                             # [FIX 2026-11-13] 0.40→0.20（同上：边路不得压过正路）
GOAL_BLOCK_PENALTY = 0.20                    # blocks 邻居降权系数

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
STATE_SEARCH_WEIGHT = 0.18          # [FIX 2026-11-13] 0.35→0.18（边路不得压过正路）
# 自动创建 state 节点的 importance 默认值（状态快照对决策有用 → operational）
STATE_NODE_IMPORTANCE = "operational"

# ---- 注意力倾斜 attention tilt（2026-10-29 借鉴 digital-life-core v0.9.9）----
# 借鉴点：检索结果全量召回后按"当前状态向量"重排——只改顺序不删内容
#   （digital-life-core 的"前额叶切除"教训：危机×3 / 日常×0.1，中间线性插值）。
# SuperMem 的"当前状态向量"翻译为两层信号：
#   A. 活跃目标自动感知：库内存在 status=active 的 goal 节点时，自动沿
#      contributes_to/blocks 边加权（复用价值维 goal 路逻辑，无需调用方显式传 goal=1）——
#      语义等价"此刻该关注什么"；
#   B. importance 倾斜：goal_related 记忆 +15%（服务当前目标）、reference 记忆 -10%
#      （背景资料后置）、operational 不变——温和版的危机/日常阶梯。
# 默认开启：行为变化仅出现在"库内确有活跃 goal 或节点带 importance 字段"时，
#   无 goal 库/全 reference 库零影响；如需对照旧基线（LoCoMo 等评测），一键置 False。
TILT_ENABLED = True                              # 总开关
TILT_AUTO_GOAL = True                            # 活跃目标自动感知（未显式传 goal 时生效）
TILT_IMPORTANCE_BOOST = {"goal_related": 1.15, "operational": 1.0, "reference": 0.9}

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
              "transitions_to",
              # [FIX 2026-11-13 P1-4] 补 trigger / enable：此前白名单缺这两类，
              #   normalize_edge_type 先查白名单未命中就落 related，导致 /extract 与
              #   /sync/apply 提交的因果边全部被压平成 related（全库 97.6% related 的根源）。
              "trigger", "enable"]


# =====================================================================
# [UPD 2026-11-12] 三条改造（lab 实例全量验证通过，已合并生产）
# ① 经验转正标记  ② 检索拒答  ③ 实体钩子层（anchor hook）
# =====================================================================

# ---- ① 经验转正标记：离散的"能否作为答案依据"开关 ----
GRADE_ENABLED = True
GRADE_EVIDENCE_TYPES = {"decision", "rule", "technique", "project", "goal",
                        "insight", "fact", "experiment", "milestone"}
# [UPD 2026-11-12] 补进 insight/fact/experiment/milestone：实测库里 insight 174 条里
# 119 条已满足 conf>=0.8 且 access>=2，内容全是"结论：xxx""坑：xxx""根因：xxx"，
# 是最典型的经验卡，之前被类型闸门整类挡在门外（experience 卡在 336）。
# 仍然不给 event 发牌（1157 条流水账，给了就等于把事件记录冒充结论）；
# task/state/todo 也不给（待办和状态天生是线索。
GRADE_EVIDENCE_MIN_CONF = 0.80        # confidence 门槛
GRADE_EVIDENCE_MIN_ACCESS = 2         # 被检索命中次数门槛
GRADE_EVIDENCE_MAX_AGE_DAYS = 120     # 超过该天数未再被访问 → 退回线索
GRADE_CERTAIN_STATUSES = {"confirmed", "permanent"}     # 直接视为合格经验
GRADE_RETIRE_STATUSES = {"outdated", "obsolete", "disputed", "merged"}  # 强制降回线索
GRADE_SKILL_ACCESS = 6                # 同一经验反复命中次数 ≥ 此值 → 打 skill_candidate 标记
GRADE_DEFAULT = "hint"                # 缺省等级：未转正的只能当线索
GRADE_RECORD_TYPES = {"event", "state", "todo", "task"}
GRADE_LEN_CHECK_TYPES = {"insight", "fact", "experiment", "milestone"}
GRADE_MIN_CONTENT_LEN = 12
# [UPD 2026-11-12] 只有上面四类做长度检查：它们是"短笔记"形态，混着大量闲聊
# （实测最短的是"你好吗？我很好。""今天天气不错。""缓存满了"，confidence 全 1.0）。
# decision/rule 之类本身有内容规范，不额外卡长度。
# [UPD 2026-11-12] "记录档"：事件/状态/待办这类节点不转正（不能冒充结论），
# 但它们确实是一手记录，命中且排序明确时可以答，只是要标明"这是记录，不是结论"。
# 这样既不给流水账发经验牌，也不会让 answer 变成死档。

# ---- ② 检索拒答：库里没有就直说没有 ----
VERDICT_ENABLED = True
VERDICT_ABS_FLOOR = 0.02              # RRF 融合分绝对地板（低于此视为无有效命中）
VERDICT_MARGIN_MIN = 1.10             # top1 / top2 分比低于此 → 结果聚集、无明确最佳
                                      # [2026-11-13] 1.25→1.10：answer 硬条件是 top1 必须为经验节点，
                                      # margin 只防"两条经验打平"；1.10 实测多放出 1 条真经验，
                                      # 3 条无关 query 判定一条未变
VERDICT_EVIDENCE_REQUIRED = True
VERDICT_LEXICAL_MIN = 0.6             # 字面命中强度达到这个值才算"有支撑"（整串1.0 / 多token命中0.6+）      # answer 级结论必须至少有一条 evidence 级节点

# ---- ③ 实体钩子层（anchor hook）：写入时建表，检索时查表 ----
ANCHOR_ENABLED = True
ANCHOR_SEARCH_WEIGHT = 0.55           # 钩子命中在 RRF 中的权重（锚定效应高于普通一路召回）
ANCHOR_BOOST = 0.03                   # [已弃用 2026-11-13 P0-2] 定值加成改为下面两个参数；
                                      # 保留常量仅为兼容旧引用
# [FIX 2026-11-13 P0-2] 定值 0.03 相对 RRF 基准 1/61=0.0164 是 +83% 的固定加成——
#   一条只靠钩子命中的节点（rrf=0）能直接 +0.03 顶到第 1 名之外的所有正常结果前面，
#   实测挤掉 10 条本该第一的正确答案（相似度 0.736/0.607/0.58）。
#   现改为"相对加成 + 小额绝对底"：乘性只在节点已有排序地位时放大（锚定=确认不是创造），
#   绝对底 0.004 约等于 RRF 第 40 名的水平，只够把纯钩子命中拉进候选池，拉不到前排。
ANCHOR_BOOST_REL = 0.35               # 乘性加成上限：base = rrf*(1+0.35*strength)
ANCHOR_BOOST_ABS = 0.004              # 绝对底：纯钩子命中最多 +0.004
ANCHOR_MIN_TERM_LEN = 2               # 中文最小字数
ANCHOR_MAX_TERM_LEN = 12              # 最长词条（防整句入表）
ANCHOR_MAX_TERMS_PER_NODE = 12        # 单节点最多抽几个钩子
ANCHOR_TOP_K = 20                     # 单次最多并入多少条钩子命中
ANCHOR_MAX_TABLE = 20000              # 钩子表条目上限
ANCHOR_CEIL_SLACK_RATIO = 0.05        # [2026-11-13] 超限时一次多删留缓冲（删到 95%），
                                      # 避免每写一条就触发一次回收
ANCHOR_MIN_HITS_KEEP = 1              # 命中次数低于此值的钩子可被回收
ANCHOR_MAX_DF_LATIN = 200             # 拉丁字母/型号词的 df 上限（实体词天然高频，如 Hive、Strata）
ANCHOR_MAX_DF_CN = 50                 # 中文词的 df 上限（中文高频几乎都是口语虚词，必须卡死）
ANCHOR_MIN_DF = 2                     # 只被一个节点用到的词是私有词，不进路由表（准入闸门）
ANCHOR_ENTITY_TYPES = {"person", "system", "item", "scene", "project", "work",
                       "book", "concept"}  # 实体节点直接整条内容作为锚点词
ANCHOR_STOPWORDS = {"这个", "那个", "我们", "你们", "他们", "什么", "怎么", "为什么",
                    "可以", "已经", "一下", "一个", "现在", "然后", "但是", "因为",
                    "所以", "如果", "就是", "还是", "没有", "不是", "这样", "那样",
                    # 口语虚词/无检索价值的常用串（实测库内高频，会污染路由）
                    "也是", "那就好", "服了", "真的", "好家伙", "不过", "话说回来",
                    "那可不", "在呢", "嗯嗯", "哈哈哈", "呜呜", "救命", "反正", "其实",
                    "感觉", "觉得", "知道", "时候", "事情", "东西", "意思", "可能",
                    "应该", "需要", "一直", "有点", "特别", "比较", "非常", "确实",
                    "是不是", "有没有", "对吧", "咋办", "啥的", "之类", "什么的",
                    "这个时候", "那个时候", "我知道了", "明白了", "没关系", "没什么",
                    "怎么了", "咋了", "行吧", "好吧", "算了", "随便", "慢慢", "先说",
                    "听着", "说白了", "总之", "而且", "或者", "虽然", "除非", "既然",
                    "于是", "接着", "同时", "另外", "此外", "这样的话", "怎么样",
                    "是不是啊", "对啊", "是啊", "嗯哼", "哎哟", "我去", "我的天",
                    # 内部元标签与无实体信息的泛词（实测库里高频，不构成"实体锚点"）
                    "auto_extract", "llm_extract", "event", "turn", "bench", "bench22",
                    "AI", "API", "GB", "MB", "KB", "tok", "py", "js", "json", "csv",
                    "md", "txt", "url", "http", "https", "true", "false", "none", "null",
                    "user", "assistant", "system", "default", "test", "tmp", "ok", "id"}
