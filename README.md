# 超级记忆系统（SuperMem）

![GitHub release (latest by date)](https://img.shields.io/github/v/release/wdhoop/SuperMem)
![GitHub](https://img.shields.io/github/license/wdhoop/SuperMem)

## 项目介绍

SuperMem 是一个基于图数据库与向量检索技术构建的**类人脑语义记忆存储与推理平台**。它能够将散落的知识碎片、对话记录、工作文档等非结构化信息转化为相互关联的语义网络，模拟人类记忆的存储、检索、关联与遗忘过程。

## 核心特性

✅ **三层记忆模型** - 短期/中期/长期三层记忆架构，模拟人脑记忆过程

✅ **因果链推理** - 支持多跳关联发现，自动构建推理路径，从"知道什么"到"理解为什么"

✅ **记忆遗忘机制** - 类人脑"用进废退"，记忆强度随时间衰减，高频访问自动强化

✅ **Auto-compact 自动压缩** - 智能合并相似节点，剪枝弱关系，保持图谱精简高效

✅ **混合向量检索** - 语义理解 + 模糊检索，P99延迟 < 105ms

## 性能基准

**测试规模：21万+ 节点 / 79万+ 边**

| 测试项目 | 样本量 | 成功率 | P50 | P95 | P99 | QPS |
|---------|--------|--------|-----|-----|-----|-----|
| 语义检索 | 1,000 | 90.2% | 41ms | 65ms | 104ms | 24.3 |
| 节点读取 | 5,000 | 100% | 5ms | 28ms | 42ms | 120.8 |

## 技术栈

- **图数据库**：Neo4j
- **向量引擎**：FAISS / Milvus
- **语义模型**：text2vec / OpenAI Embeddings
- **API接口**：RESTful HTTP

## 快速开始

```bash
# 克隆项目
git clone https://github.com/wdhoop/SuperMem.git

# 进入目录
cd SuperMem

# 查看技术文档
cat 超级记忆系统_MVP技术文档.md

# 查看性能测试报告
cat 超级记忆系统_MVP性能测试报告.md
##版本说明
v1.0.0 MVP (2026-05-02) - 核心功能发布，支持图存储、语义检索、Auto-compact、因果链、遗忘机制
##许可证
MIT License - Copyright (c) 2026 Chi Qian
