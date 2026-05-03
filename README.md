# 🧠 超级记忆系统 MVP

**Super Memory** — 将散落的知识转化为因果关联的语义记忆系统。

个人知识管理的下一站：不只是存储，更是推理。用向量语义与线性因果链，帮你发现知识间的"为什么"。

---

## ✨ 核心设计

| 维度 | 方案 |
|------|------|
| 图存储 | SQLite 实现属性图，支持 4 种节点类型（Concept/Entity/Document/Event）、4 种关系（RELATED_TO/PART_OF/CAUSED_BY/MENTIONS） |
| 向量检索 | ChromaDB + text2vec 本地中文嵌入，支持自然语言语义搜索 |
| 因果推理 | **线性因果链**，A→B→C 多跳追溯与推导，不展开网状，保持可解释性 |
| 记忆压缩 | 相似节点自动合并（0.92 阈值）+ 弱边剪枝（<0.15），保持图谱干净 |
| 遗忘曲线 | 时间衰减（e⁻⁰·⁰⁰¹ᵗ）+ 访问频率加权 |
| API | 6 个 REST 接口，JSON 统一响应，跨域支持 |
| Web UI | 纯静态 HTML/CSS/JS，零构建，双击即可打开 |

## 📂 文件结构

```
超级记忆系统MVP版/
├── README.md
├── requirements.txt        # 依赖清单
├── server.py               # REST API 服务
├── core.py                 # 图存储 + 向量索引引擎
├── web/
│   ├── index.html          # 主界面：搜索 + 卡片 + 批量导入
│   └── graph.html          # 线性因果链可视化（D3.js）
└── temp/                   # 临时辅助脚本（发布时可删）
```

## 🚀 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动
python server.py

# 3. 浏览器打开
http://localhost:8765
```

> 首次启动会自动下载中文向量模型（~300MB），需联网。

## 📡 API 接口

### POST /api/v1/memory/ingest — 写入记忆
```json
{"content": "Python 是一种动态类型语言", "type": "Concept", "name": "Python"}
```

### POST /api/v1/memory/search — 语义搜索
```json
{"query": "动态语言", "top_k": 10}
```

### GET /api/v1/memory/node/{id} — 节点详情
### GET /api/v1/memory/graph?node_id={id}&depth=3 — 线性因果链
### GET /api/v1/memory/stats — 系统统计
### POST /api/v1/memory/compact — 手动压缩

## 🧩 线性因果链

核心设计原则：**路径不网状**。当查询一个节点时，沿 `CAUSED_BY` 边向前追溯原因、向后推导后果，形成一条清晰的线性路径：

```
[原因] → [事件] → [后果]
   ↓
[深层次原因] → [事件] → [深层后果]
```

图谱 UI 使用 D3.js 实现，x 轴线性排列，滚轮缩放，点击节点跳转。

## ⚙️ 配置

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `PORT` | 8765 | 服务端口 |
| `SUPER_MEMORY_DB` | `super_memory.db` | 数据库路径 |

## 📜 开源协议

MIT License — 自由使用、修改、分发。

---

*从碎片到关联，从关联到因果。*
