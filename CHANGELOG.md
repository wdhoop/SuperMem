# CHANGELOG

## v1.0.1（2026-10-19）

### 新增：联想检索（associative retrieval）

- `search_fusion.py`：`search_strong(..., expand=True)` 新增可选联想检索——用"无思考模式"LLM 生成联想句扩展查询，与原查询合并召回（HyDE / query-expansion 同类）。
- `config.py`：新增 `ASSOC_ENABLED` / `ASSOC_ENDPOINT` / `ASSOC_KEY` / `ASSOC_MODEL` / `ASSOC_TIMEOUT` / `ASSOC_N` / `ASSOC_MAX_QUERY_LEN` 配置块。
- 新增 `gen_assoc_queries(q)` 对外函数；`recall_candidates` 支持联想句参与召回（带 `assoc` 标记）。
- **默认关闭、未配置/生成失败静默回退**：不启用时与旧版检索结果逐项一致（已验证 `ids_same_order=True`、`scores_same=True`、`mode` 一致）。
- 验证脚本：`scripts/verify_assoc_backport.py`（默认/回退/启用三态）。

### 新增：LoCoMo-Plus 全量评测（随包发布）

- 西安交大认知记忆基准，401 题全量：纯系统检索 25.19% → 联想桥 55.86%（官方全量供给参考 61.6 的 91%）。
- 评测口径：只给检索片段、不给完整对话；回答模型 DeepSeek V4-flash，联想生成器 deepseek-chat（无思考模式）。
- 补充实验公开：图遍历对照 26.43%（+1.2 点，止损）、goal/value 专项、value 强化 2.0（失败机制已定位）。
- 脚本 `benchmark/scripts/locomo_plus/`（ingest → 联想生成 → 评测 → judge，全断点续写）+ 结果 `benchmark/results/locomo_plus/`（JSONL）全公开可复现。
- 报告：`docs/LoCoMo-Plus评测报告.md`。

### 反哺与止损

- 评测最大成果（联想检索）回写生产；止损结论公开：建边/图遍历多跳/value 类强化联想不再投入。

## v1.0.0（2026-10-18）

正式版 v1.0：三层记忆架构（图层 SQLite + 向量层 ChromaDB/BGE + 原文层 verbatim）+ 融合检索 `search_strong` + 生命周期管理 + REST API / MCP / CLI 桥接，随包发布 LoCoMo 官方基准全量评测（1986 题，三模型对照）。
