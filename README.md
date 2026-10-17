# SuperMem 超级记忆系统

**本地优先、图结构 + 向量 + 原文三层的长期记忆系统**，为 AI 智能体（LLM / 数字人 / 桌面宠物）提供可检索、可演化的持久记忆底座。

SuperMem 把"记忆"当作一等公民：写入时自动提取实体/事件/规则、构建因果与语义边；检索时融合图检索 + 向量检索 + 原文层强召回；运行时由生命周期管理器自动完成节点状态流转、去重、遗忘与归档。核心逻辑**零第三方框架依赖**（仅向量索引依赖 ChromaDB），可嵌入任意 LLM 应用。

---

## 特性

- **三层记忆架构**
  - **图层**：SQLite 存储 nodes/edges，节点带类型（概念/实体/事件/文档/规则…）、权重分区（Core / Working / Cold）、时间戳，边带因果/时序/语义类型与强度。
  - **向量层**：ChromaDB + BGE embedding，cosine 相似度检索，支持本地模型目录。
  - **原文层（verbatim）**：写入内容逐字落库，带独立向量索引，供"强召回下探"——当图检索置信不足时自动下探原文，实测对 LoCoMo 难题组带来稳定 +25~28% 增益。
- **融合检索 `search_strong`**：图检索 + 向量检索 + 原文下探 + 时间窗过滤 + 因果链扩展，一次调用返回结构化上下文。
- **自动提取与建边**：规则提取器、事件归一化、跨会话聚合、相似节点自动建语义边、因果边自动推导。
- **生命周期管理**：节点状态机（候选/活跃/确认/过时/废弃）、权重衰减与升温、遗忘阈值、存档与备份。
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
├── scripts/                    # selfcheck / rebuild_vector_index / watchdog
├── web/                        # 简版可视化面板（搜索 + 图谱）
├── benchmark/                  # LoCoMo 评测（脚本 + 结果 + README）
└── docs/                       # 架构说明 + 评测报告
```

---

## 许可证

[MIT](LICENSE) © 2026 Chi Qian

## 致谢

- 向量索引：[ChromaDB](https://github.com/chroma-core/chroma)
- Embedding：[BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5)（评测用 bge-small-en-v1.5）
- 基准：[Snap Research LoCoMo](https://github.com/snap-research/LoCoMo)
