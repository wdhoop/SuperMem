# SuperMem 超级记忆系统

**本地优先、图结构 + 向量 + 原文三层的长期记忆系统**，为 AI 智能体（LLM / 数字人 / 桌面宠物）提供可检索、可演化的持久记忆底座。

> **v1.2.4（2026-11-13）**：**26 项缺陷大修**——首轮全量测试查出 23 项，补齐漏检后共 26 项逐条修完。检索端四处核心改动：锚点加成改乘性、语义扩散改为"只补位"（不再污染主路排序）、中文按 2–4 字滑窗切词、关键词候选窗口 320→2000（原实现等于只保留最新 320 条命中行，老记忆被整批丢弃）。134 题自召回 @1 25.6% → **74.6%**、MRR 0.478 → **0.846**，未召回 11 → 1；库外问题拒答 **12/12**。同时修掉 grade 不同步、边类型被压平、gap 口径错、非法输入 500 且已写入等一批数据一致性问题，并提供幂等升级脚本 `scripts/migrate_fix_20261113.py`。详见 [CHANGELOG](CHANGELOG.md)、[修复清单](docs/大修修复清单.md)、[重测报告](docs/大修后重测报告.md)。

> **v1.1.3（2026-11-07）**：修复**遗忘曲线失效**——时间戳解析只认单一格式，导致真实库中 76.5% 的记忆时间戳解析失败、衰减永不生效；同时访问热度被乘进权重，被反复检索的旧结论长期顶格 1.00、新写入只有 0.70。现改为多格式时间解析 + 访问量只参与排序 + causal 上限 1.3 + 7 天新近加成。生产库全量重算：顶格节点 1036 → 37，错误旧条 1.00 → 0.35，当天新条目 0.70 → 1.00。详见 [CHANGELOG](CHANGELOG.md) 与 [修复报告](docs/权重衰减修复报告.md)。

> **v1.1.1（2026-10-29）**：新增**注意力倾斜**（借鉴 digital-life-core——活跃目标自动感知 + importance 倾斜，全量召回后只改排序不删内容）与**写入回执验证**（写完按原文读回，`saved` 是唯一判定依据，读不回自动重写一次）。详见 [CHANGELOG](CHANGELOG.md)。

> **v1.1.0（2026-10-29）**：新增**剪枝优化**——认知更新自动退休（旧 decision/rule 被新认知取代时自动转 outdated 并挂 superseded_by 边）+ 访问热度衰减（长期冷落的记忆热度加成随遗忘曲线消退，半衰期约 35 天）；同时随包补齐生产已评测的**四维自动联想**（时间/场景/价值/状态）。详见 [CHANGELOG](CHANGELOG.md)。

> **v1.0.1（2026-10-19）**：新增**联想检索**（associative retrieval，可选增强）——检索时用无思考模式 LLM 生成联想句扩展查询（HyDE 同类），LoCoMo-Plus 基准实测检索供给翻倍（25.19% → 55.86%）；随包发布 LoCoMo-Plus 全量评测（401 题，脚本+结果全公开）。详见 [CHANGELOG](CHANGELOG.md)。

SuperMem 把"记忆"当作一等公民：写入时自动提取实体/事件/规则、构建因果与语义边；检索时融合图检索 + 向量检索 + 原文层强召回；运行时由生命周期管理器自动完成节点状态流转、去重、遗忘与归档。核心逻辑**零第三方框架依赖**（仅向量索引依赖 ChromaDB），可嵌入任意 LLM 应用。

---

## 特性

- **三层记忆架构**
  - **图层**：SQLite 存储 nodes/edges，节点带类型（概念/实体/事件/文档/规则…）、权重分区（Core / Working / Cold）、时间戳，边带因果/时序/语义类型与强度。
  - **向量层**：ChromaDB + BGE embedding，cosine 相似度检索，支持本地模型目录。
  - **原文层（verbatim）**：写入内容逐字落库，带独立向量索引，供"强召回下探"——当图检索置信不足时自动下探原文，实测对 LoCoMo 难题组带来稳定 +25~28% 增益。
- **融合检索 `search_strong`**：图检索 + 向量检索 + 原文下探 + 时间窗过滤 + 因果链扩展，一次调用返回结构化上下文。
- **全量缺陷大修（v1.2.4）**：26 项逐条修复。检索端四处核心改动（锚点乘性加成 / 语义扩散只补位 / 中文 2–4 字滑窗切词 / 关键词候选窗口 320→2000），另有 grade 与 status 同步、边类型归一、gap 口径、输入校验、向量删除、episode 幂等键等一批一致性修复；134 题自召回 @1 25.6%→74.6%、MRR 0.478→0.846，库外问题拒答 12/12；附幂等升级脚本 `scripts/migrate_fix_20261113.py`。代价是 p95 延迟 263→415ms，已如实记录在 CHANGELOG。
- **经验转正与检索拒答（v1.1.4）**：节点级 `grade` 只分 `experience`（可当结论用）/ `hint`（只能是线索），靠类型、置信度、被命中次数、时效四道条件转正；检索结论再分四档 `answer` / `record`（一手记录，作答时标明"这是记录不是结论"）/ `hint` / `no_match`，并附原因码（`margin_flat` / `top1_not_evidence` / `no_lexical_or_anchor_support` 等）。解决"该答的说没有、不该答的硬答"。
- **实体锚定检索（v1.1.4，新增模块 `anchors.py`）**：写入时把实体词抽进锚点表（私有词 / 高 df 泛词 / 口语虚词三道闸门；中文词只从 `tags`、书名引号、实体节点内容取，正文只抽拉丁型号 token），检索时先查表、命中并入融合分（RRF 权重 0.55 + 绑定强度加成 0.03），补"字面不通、语义也漂"的那一路召回；命中回写 `hits`、四级排序淘汰、表满一次删到 95% 留缓冲。
- **关键词路 SQL 化（v1.1.4）**：`_fts_recall` 从"Python 全表遍历逐条比对"改为 SQL 层 `instr` 先筛命中行、只对命中集算分，结果逐项一致，生产库 4.6× / 万节点库 4.7×。
- **语义关联建边修复（v1.1.2）**：修复 related 边"漏建 + 不召回"双缺陷——写入时双向建边（新→老同时补老→新）+ k 2→8 阈值分层（强关联全建、弱关联限量）+ 检索沿 related 边扩散一跳（top 20 截断并入 RRF）+ 历史漏边一次性 backfill 脚本（幂等、可 `--dry-run` 预演）。
- **注意力倾斜（v1.1.1，默认开启）**：检索全量召回后按当前状态向量重排——活跃目标自动感知（active goal 沿边扩散加权）+ importance 倾斜（goal_related ×1.15 / reference ×0.9）；**只改排序不删内容**，无活跃 goal 时零影响，一键 `TILT_ENABLED=False` 对照旧基线。
- **写入回执验证（v1.1.1）**：写入后按原文读回，`saved` 是唯一判定依据；读不回自动重写一次，二次不回才如实报未存住（不假装成功）。
- **联想检索（v1.0.1，可选）**：`search_strong(..., expand=True)` 用无思考模式 LLM 生成联想句扩展查询（HyDE / query-expansion 同类），把"字面检索不到但语义相关"的记忆捞出来；默认关闭、未配置时静默回退，旧行为完全不变。
- **自动提取与建边**：规则提取器、事件归一化、跨会话聚合、相似节点自动建语义边、因果边自动推导。
- **四维自动联想（v1.1.0）**：写入时自动识别时间（before/after）、场景（involves）、价值（goal + contributes_to/blocks）、状态（state + transitions_to）四类关系建边；检索支持 recency / scene / goal / state 定向模式。
- **生命周期管理**：节点状态机（候选/活跃/确认/过时/废弃）、权重衰减与升温、遗忘阈值、存档与备份。
- **剪枝优化（v1.1.0 / 修正 v1.1.1）**：矛盾自动退休——新认知推翻旧决策/规则时自动把旧节点转 outdated 并挂 superseded_by 边；权重衰减——按 S/A/B/C 档位各自的遗忘曲线折旧，边数用对数压缩、访问次数只参与检索排序（v1.1.1 起不再乘进权重，避免"被反复检索的错误记忆永远顶格"）。
- **调度与中断预算**：定时回收、批量写入合并、中断预算（interrupt budget）避免高频打断，Hot Cache 提速热词检索。
- **REST API**：`http.server` 标准库实现，零 Web 框架依赖，Token 认证，默认只绑定 127.0.0.1。
- **MCP 服务**：可选启动 MCP Server，供 Claude / 其他 MCP 客户端直接读写记忆。
- **CLI 桥接 `bridge/smem.py`**：健康检查 / 写入 / 语义检索 / 时间旅行 / 状态流转，内置服务自愈（服务未启动时自动拉起）。

---

## 快速开始

### 1. 安装

```bash
# Python 3.10+
pip install -r requirements.txt
```

首次启动会自动下载/加载 embedding 模型（默认 `BAAI/bge-small-zh-v1.5`，可改 `config.py` 中的 `EMBED_MODEL_NAME` 与 `MODELS_DIR`）。

### 2. 配置访问令牌

```bash
# 启动前设置你自己的令牌（生产/公网使用必须配置）
set SUPERMEM_API_TOKEN=your-own-token        # Windows
export SUPERMEM_API_TOKEN=your-own-token     # Linux/macOS
```

未设置时使用 `config.py` 中的占位值 `change-me`（仅限本地调试）。

### 3. 启动服务

```bash
python main.py          # 启动 REST API（默认 http://127.0.0.1:18766）
# 可选：python mcp_server.py  # 启动 MCP Server
# 可选：python scripts/watchdog.py  # 看门狗：服务异常自动拉起
```

### 4. 使用 CLI 写入与检索

```bash
python bridge/smem.py health
python bridge/smem.py add "今天完成了桥接，决定让豆包助手直接读写本地记忆。" --type decision --source myapp
python bridge/smem.py search "桥接方案" --limit 5
python bridge/smem.py rewind "2026-09-30 12:00" "桥接方案"     # 时间旅行：某时刻视角下的记忆
python bridge/smem.py mark <node_id> confirmed                 # 状态流转
```

### 5. REST API 示例

```bash
# 写入（自动建边 + 自动落原文层）
curl -X POST http://127.0.0.1:18766/nodes \
  -H "Authorization: Bearer $SUPERMEM_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content":"项目定于下周一上线，风险点是数据库迁移。","project":"myapp","source":"myapp","tags":["上线","迁移"]}'

# 批量提取（自动识别规则/事件/心得，可带 nodes/edges）
curl -X POST http://127.0.0.1:18766/extract \
  -H "Authorization: Bearer $SUPERMEM_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"text":"今天完成了桥接，决定让豆包助手直接读写本地记忆。","source":"myapp","project":"myapp"}'

# 融合检索（图检索 + 向量 + 原文层强召回）
curl "http://127.0.0.1:18766/search/strong/%E6%95%B0%E6%8D%AE%E5%BA%93%E8%BF%81%E7%A7%BB%20%E9%A3%8E%E9%99%A9?limit=10"

# 健康检查（响应里的 version 即当前运行的版本，vector_gap 应为 0）
curl http://127.0.0.1:18766/health -H "X-API-Token: $SUPERMEM_API_TOKEN"
```

更多端点：`GET /search/<query>`（普通检索）、`GET /nodes?q=`（节点搜索/列表）、`GET /rewind/<ts>/<query>`（时间旅行）、`GET /timeline/<start>/<end>`（时间线）、`POST /mark`（状态流转）、`POST /decay`（权重衰减）、`POST /selfcheck`（健康自检）、`POST /rebuild`（重建向量索引）、`POST /backup`（备份）。

---

## 架构概览

```
写入链路：extract → rule_extractor / event_normalizer → core(Node) → add_node_with_vector
          → linker(auto_link 建边) → verbatim 原文落库 → memory_updater / cross_session

检索链路：search_strong(融合) = 图检索 + 向量检索 + 实体锚点
          →（可选 v1.0.1）联想检索 expand：无思考模式 LLM 生成联想句扩展召回
          → 置信不足时下探原文层（verbatim 强召回）
          → 时间窗过滤 + 因果链扩展 → 权重分区排序输出
          →（v1.1.4）判据分级：answer / record / hint / no_match，附原因码

生命周期：node_lifecycle / edge_lifecycle（状态机、衰减、遗忘）
          + priority_queue（回收队列）+ scheduler（定时任务）
          + interrupt_budget（中断预算）+ hot_cache（热词缓存）
          + metrics（统计）+ cycle_detector（循环检测）
```

- `core.py`：图存储（GraphStore）、向量检索（VectorSearch）、因果引擎（CausalEngine）、记忆系统（MemorySystem）、经验转正判定（`compute_grade`）
- `search_fusion.py`：融合检索主入口、拒答判据（`judge_verdict`）
- `anchors.py`：实体锚点表——写入抽词建表、检索查表并入融合分
- `api.py` / `main.py`：REST 服务与启动入口
- `scheduler.py`：后台定时任务（回收、备份、清理）
- `mcp_server.py`：MCP 协议服务（可选）

---

## 基准评测（LoCoMo）

在 **LoCoMo 官方基准**（10 段超长多会话对话 / 1986 道 QA / 五类推理）上完成全量评测：
本地隔离库（零接触生产库）构建，官方 F1 + LLM-Judge accuracy 双口径，**脚本与结果全部随仓库公开、可复现**。

| 作答模型 | limit24 Token F1 | LLM-Judge Accuracy |
|---|---|---|
| 9B（qwen3.5:9b, 本地） | 0.4083 | 0.5504 |
| 27B（qwen3.8-27b-fast, 本地） | 0.4313 | 0.5821 |
| 125B（Qwen3.8-Flash-Next, Strata 本地） | **0.4585** | **0.6163** |

关键发现：
- **检索供给是瓶颈，模型 × 供给是乘法关系**：limit 8→24 时证据命中 65.8%→82.2%，F1 三档同步 +6~8pp（9B +6.4 / 27B +6.9 / 125B +7.7），模型越大增益越大。
- **原文层强召回是有效增量**：触发原文下探（hybrid）的难题组，F1 稳定高出图检索组 +25~28%（三个 answerer 下均复现）。
- 分类别 125B：multi-hop 0.6175 / adversarial 0.4359 / single-hop 0.4048 / commonsense 0.3552 / temporal 0.1515。

完整方法论、口径声明与社区对比见 [`benchmark/README.md`](benchmark/README.md) 与 [`docs/LoCoMo评测报告.md`](docs/LoCoMo评测报告.md)。

---

## 基准评测（LoCoMo-Plus，v1.0.1 随包发布）

在 **LoCoMo-Plus 基准**（西安交大认知记忆基准，401 题：causal 101 / state 100 / goal 100 / value 100）上完成全量评测，回答模型为 DeepSeek V4-flash。评测口径：**只给检索片段、不给完整对话**（模拟真实记忆系统的回忆使用方式），比官方"全量供给"更严苛。

| 检索策略 | 成绩 | 含义 |
|---|---|---|
| 纯系统检索（无增强） | **25.19%** | 记忆系统自身（向量+图谱+原文层）真实底分 |
| 纯检索 + 图遍历扩展 | 26.43% | 系统静态检索策略上限（+1.2 点，几乎无增益） |
| **联想桥（检索策略升级）** | **55.86%** | 检索管线加 LLM query 生成（HyDE 同类），**反超官方全部方法最高分（Gemini 2.5 Pro 全量供给 45.72%）10.1 个点** |

- **30.7 个点的增益全部来自检索策略升级**（联想桥），不是静态图谱遍历能替代的（图遍历对照仅 +1.2 点）。
- 分类型：causal 77.2% / state 69% / goal 37% / value 40%；goal/value 是公认难点，短板与失败实验全部如实公开。
- 该评测的最大成果（联想检索）已回写生产成为 **v1.0.1 核心新能力**，配置 `ASSOC_ENABLED=True` + OpenAI 兼容端点即可启用。

完整报告：[`docs/LoCoMo-Plus评测报告.md`](docs/LoCoMo-Plus评测报告.md)；脚本与结果：`benchmark/scripts/locomo_plus/`、`benchmark/results/locomo_plus/`（可复现）。

---

## 升级到 v1.2.4（从 1.1.x）

1. 停服务，冷备数据库：`storage/super_memory.db` 连同 `-wal` / `-shm` 一起拷走（这三个文件构成完整回滚点）。
2. 覆盖代码，`storage/` 保持自己的不动。
3. 跑迁移（幂等，可重复执行）：

```bash
python scripts/migrate_fix_20261113.py --db storage/super_memory.db --dry-run   # 先看会改什么
python scripts/migrate_fix_20261113.py --db storage/super_memory.db --apply     # 实际执行
```

4. 起服务，`GET /health` 应返回 `"version": "1.2.4"` 且 `vector_gap == 0`（version 不一致说明覆盖的代码不对，gap 不为 0 说明向量索引没跟上），再跑一次 `python scripts/selfcheck.py`。

迁移会动到的数据：标签清洗、边类型归一、重复边删除、related 边 strength 回填、节点时间戳归一、grade 重算、episode 引用修复与同起点 episode 合并。**第二次执行应全部为 0**——若不为 0，说明脚本没覆盖干净，先别继续。

## 目录结构

```
SuperMem/
├── core.py / api.py / main.py / config.py   # 核心 + 服务 + 入口 + 配置
├── search_fusion.py / anchors.py             # 融合检索 + 实体锚点表
├── linker.py / extract.py
├── rule_extractor.py / event_normalizer.py / memory_updater.py
├── node_lifecycle.py / edge_lifecycle.py / cycle_detector.py
├── time_window.py / cross_session.py / priority_queue.py
├── interrupt_budget.py / hot_cache.py / metrics.py
├── scheduler.py / mcp_server.py / analyze_patterns.py
├── bridge/smem.py              # CLI 桥接
├── scripts/                    # selfcheck / rebuild_vector_index / watchdog / verify_assoc_backport / smoke_anchors / migrate_fix_20261113（v1.2.4 升级迁移）
├── web/                        # 简版可视化面板（搜索 + 图谱）
├── benchmark/                  # LoCoMo 评测 + LoCoMo-Plus 评测（脚本 + 结果 + README）
│   ├── scripts/locomo_plus/    #   LoCoMo-Plus：ingest → 联想生成 → 评测 → judge
│   └── results/locomo_plus/    #   LoCoMo-Plus：联想句 / 评测结果 / judge 结果（JSONL）
└── docs/                       # 架构说明 + LoCoMo 评测报告 + LoCoMo-Plus 评测报告 + 大修修复清单 / 大修后重测报告
```

---

## 许可证

[MIT](LICENSE) © 2026 Chi Qian

## 致谢

- 向量索引：[ChromaDB](https://github.com/chroma-core/chroma)
- Embedding：[BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5)（评测用 bge-small-en-v1.5）
- 基准：[Snap Research LoCoMo](https://github.com/snap-research/LoCoMo)
