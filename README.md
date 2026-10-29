# SuperMem 超级记忆系统

**本地优先、图结构 + 向量 + 原文三层的长期记忆系统**，为 AI 智能体（LLM / 数字人 / 桌面宠物）提供可检索、可演化的持久记忆底座。

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
- **注意力倾斜（v1.1.1，默认开启）**：检索全量召回后按当前状态向量重排——活跃目标自动感知（active goal 沿边扩散加权）+ importance 倾斜（goal_related ×1.15 / reference ×0.9）；**只改排序不删内容**，无活跃 goal 时零影响，一键 `TILT_ENABLED=False` 对照旧基线。
- **写入回执验证（v1.1.1）**：写入后按原文读回，`saved` 是唯一判定依据；读不回自动重写一次，二次不回才如实报未存住（不假装成功）。
- **联想检索（v1.0.1，可选）**：`search_strong(..., expand=True)` 用无思考模式 LLM 生成联想句扩展查询（HyDE / query-expansion 同类），把"字面检索不到但语义相关"的记忆捞出来；默认关闭、未配置时静默回退，旧行为完全不变。
- **自动提取与建边**：规则提取器、事件归一化、跨会话聚合、相似节点自动建语义边、因果边自动推导。
- **四维自动联想（v1.1.0）**：写入时自动识别时间（before/after）、场景（involves）、价值（goal + contributes_to/blocks）、状态（state + transitions_to）四类关系建边；检索支持 recency / scene / goal / state 定向模式。
- **生命周期管理**：节点状态机（候选/活跃/确认/过时/废弃）、权重衰减与升温、遗忘阈值、存档与备份。
- **剪枝优化（v1.1.0）**：矛盾自动退休——新认知推翻旧决策/规则时自动把旧节点转 outdated 并挂 superseded_by 边；访问热度衰减——很久不再使用的记忆热度加成随时间消退（半衰期约 35 天），旧认知自然下沉、不堆叠。
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

# 健康检查
curl http://127.0.0.1:18766/health -H "Authorization: Bearer $SUPERMEM_API_TOKEN"
```

更多端点：`GET /search/<query>`（普通检索）、`GET /nodes?q=`（节点搜索/列表）、`GET /rewind/<ts>/<query>`（时间旅行）、`GET /timeline/<start>/<end>`（时间线）、`POST /mark`（状态流转）、`POST /decay`（权重衰减）、`POST /selfcheck`（健康自检）、`POST /rebuild`（重建向量索引）、`POST /backup`（备份）。

---

## 架构概览

```
写入链路：extract → rule_extractor / event_normalizer → core(Node) → add_node_with_vector
          → linker(auto_link 建边) → verbatim 原文落库 → memory_updater / cross_session

检索链路：search_strong(融合) = 图检索 + 向量检索
          →（可选 v1.0.1）联想检索 expand：无思考模式 LLM 生成联想句扩展召回
          → 置信不足时下探原文层（verbatim 强召回）
          → 时间窗过滤 + 因果链扩展 → 权重分区排序输出

生命周期：node_lifecycle / edge_lifecycle（状态机、衰减、遗忘）
          + priority_queue（回收队列）+ scheduler（定时任务）
          + interrupt_budget（中断预算）+ hot_cache（热词缓存）
          + metrics（统计）+ cycle_detector（循环检测）
```

- `core.py`：图存储（GraphStore）、向量检索（VectorSearch）、因果引擎（CausalEngine）、记忆系统（MemorySystem）
- `search_fusion.py`：融合检索主入口
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

## 目录结构

```
SuperMem/
├── core.py / api.py / main.py / config.py   # 核心 + 服务 + 入口 + 配置
├── search_fusion.py / linker.py / extract.py
├── rule_extractor.py / event_normalizer.py / memory_updater.py
├── node_lifecycle.py / edge_lifecycle.py / cycle_detector.py
├── time_window.py / cross_session.py / priority_queue.py
├── interrupt_budget.py / hot_cache.py / metrics.py
├── scheduler.py / mcp_server.py / analyze_patterns.py
├── bridge/smem.py              # CLI 桥接
├── scripts/                    # selfcheck / rebuild_vector_index / watchdog / verify_assoc_backport
├── web/                        # 简版可视化面板（搜索 + 图谱）
├── benchmark/                  # LoCoMo 评测 + LoCoMo-Plus 评测（脚本 + 结果 + README）
│   ├── scripts/locomo_plus/    #   LoCoMo-Plus：ingest → 联想生成 → 评测 → judge
│   └── results/locomo_plus/    #   LoCoMo-Plus：联想句 / 评测结果 / judge 结果（JSONL）
└── docs/                       # 架构说明 + LoCoMo 评测报告 + LoCoMo-Plus 评测报告
```

---

## 许可证

[MIT](LICENSE) © 2026 Chi Qian

## 致谢

- 向量索引：[ChromaDB](https://github.com/chroma-core/chroma)
- Embedding：[BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5)（评测用 bge-small-en-v1.5）
- 基准：[Snap Research LoCoMo](https://github.com/snap-research/LoCoMo)
