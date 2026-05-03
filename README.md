# 超级记忆系统 MVP (Super Memory MVP)

一个轻量级、零依赖的**结构化记忆系统** —— 基于属性图存储 + 语义向量检索 + 遗忘曲线，模拟人类记忆的存储、关联与遗忘机制。

## 项目结构

```
超级记忆系统MVP版/
├── core.py          # 核心引擎：图存储 + ChromaDB 向量检索
├── server.py        # REST API 服务（stdlib http.server，零外部依赖）
├── requirements.txt # 可选依赖（仅向量搜索需要 chromadb）
├── README.md        # 本文件
└── web/
    ├── index.html   # 主界面：语义搜索 + 记忆卡片 + 批量导入
    └── graph.html   # 线性因果链可视化（D3.js）
```

## 核心特性

### 1. 属性图存储
- 以节点（事件/概念/任务/决策等）和边（因果关系/触发/阻塞等）构建知识图谱
- 完全基于 SQLite，零外部依赖

### 2. 语义向量检索
- 基于 ChromaDB + text2vec-base-chinese 实现中文语义搜索
- 自动为每个节点生成向量索引

### 3. 遗忘曲线
- 基于指数衰减的权重公式，模拟人类遗忘规律
- 按节点类型差异化衰减速度（决策类慢、事件类标准）
- 因果因子：关联边越多越难遗忘
- 定期 compact 机制自动剪枝低权重节点

### 4. 线性因果链
- 沿 `CAUSED_BY` 关系双向追溯（原因 + 后果）
- 支持指定深度追溯，生成可读的因果路径

### 5. 相似节点合并
- 语义相似度检测 + 自动合并
- 保留高权重节点，继承所有关联边

### 6. Web 管理界面
- 记忆卡片浏览与搜索
- 批量导入文本
- 线性因果链可视化（D3.js 力导向图）

## 快速开始

```bash
# 1. 安装可选依赖（如需要语义搜索）
pip install -r requirements.txt

# 2. 直接运行 REST API 服务
python server.py

# 3. 在浏览器打开
# http://localhost:8765
```

首次运行会自动创建 `super_memory.db`，系统即可使用。

> **注意**：本项目是一个**框架级开源项目结构**，提供核心功能代码。部分依赖（如 chromadb、text2vec 模型）需自行安装配置。详见部署文档。

## API 文档

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | Web 管理界面 |
| GET | `/graph.html` | 因果链可视化 |
| GET | `/api/v1/memory/stats` | 系统统计 |
| GET | `/api/v1/memory/graph?node_id=&depth=` | 线性因果链 |
| GET | `/api/v1/memory/node/{id}` | 节点详情 |
| POST | `/api/v1/memory/ingest` | 写入记忆 |
| POST | `/api/v1/memory/search` | 语义检索 |
| POST | `/api/v1/memory/compact` | 手动压缩 |

## 遗忘公式

```
W(t) = e^(-0.00022 × t)

  t = 距创建时间的小时数
  约 1 年不访问 → weight 降至 0.15，可被剪枝
```

每次访问权重 +0.05（上限 1.0），20 次封顶。访问可重置遗忘进程。

## 边类型定义（开发概念系统）

| 类型 | 语义 | 说明 |
|------|------|------|
| `CAUSED_BY` | A → B：A 的原因是 B | 线性因果链核心。A `caused_by` B 表示 A 的成因是 B |
| `LEADS_TO` | A → B：A 导致 B | 主动性因果关系。A 的结果/影响是 B |
| `RELATES_TO` | A — B：A 与 B 相关 | 弱关联，无方向性。用于构建关联网络 |
| `DEPENDS_ON` | A → B：A 依赖于 B | 依赖关系。A 的实现/存在依赖于 B |
| `SIMILAR_TO` | A — B：A 与 B 相似 | 语义相似但不完全相同。合并候选 |

## 许可证

MIT
