# CHANGELOG

## v1.0.2（2026-10-25）

### 新增：全上下文天花板对照（建议④落地）

为给"记忆系统相对理论上限的提升"一个可对外对话的可比锚点，补跑 LoCoMo **全量供给对照**：

- 同一模型（Qwen3.8-Flash-Next IQ3_S）、同一 judge（DeepSeek V4-Flash，官方 LLM-Judge 二档协议）、同一 1986 题；唯一变量是供给方式（检索 limit24 vs 整段对话直进上下文）。
- 结果（双口径交叉一致）：
  - Token F1：全上下文 0.4801 > 检索供给 0.4551（+2.5pp），逐题胜 702 vs 593；
  - accuracy：检索供给 0.6148 > 全上下文 0.6062（-0.9pp）；
  - 分类结构性差异：multi-hop/temporal/single-hop/commonsense 全上下文领先（+1~46pp），adversarial（对抗性误导）检索供给反超 38~53pp——**记忆系统相对全量供给的核心价值是"信息过滤/防误导"**。
- 新增脚本：`benchmark/scripts/eval_locomo_125b_fullctx.py`（KV 复用、断点续跑）、`judge_fullctx.py`、`score_fullctx.py`；结果 `benchmark/results/locomo_results_125b_fullctx.jsonl`、`judge_125b_l24_v2.jsonl`、`judge_125b_fullctx.jsonl`（各 1986 行，逐题可复核）。
- 报告：`docs/全上下文天花板对照报告.md`。
- 研发指向：temporal（+38.9pp 差距最大）与 multi-hop（+8.4pp）检索覆盖是下一步重点；adversarial 防御作为差异化方向显式化。

## v1.0.1（2026-10-19）

### 新增：联想检索（associative retrieval）

- `search_fusion.py`：`search_strong(..., expand=True)` 新增可选联想检索——用"无思考模式"LLM 生成联想句扩展查询，与原查询合并召回（HyDE / query-expansion 同类）。
- `config.py`：新增 `ASSOC_ENABLED` / `ASSOC_ENDPOINT` / `ASSOC_KEY` / `ASSOC_MODEL` / `ASSOC_TIMEOUT` / `ASSOC_N` / `ASSOC_MAX_QUERY_LEN` 配置块。
- 新增 `gen_assoc_queries(q)` 对外函数；`recall_candidates` 支持联想句参与召回（带 `assoc` 标记）。
- **默认关闭、未配置/生成失败静默回退**：不启用时与旧版检索结果逐项一致（已验证 `ids_same_order=True`、`scores_same=True`、`mode` 一致）。
- 验证脚本：`scripts/verify_assoc_backport.py`（默认/回退/启用三态）。

### 新增：LoCoMo-Plus 全量评测（随包发布）

- 西安交大认知记忆基准，401 题全量：纯系统检索 25.19% → 联想桥 55.86%，**反超官方全部方法最高分（Gemini 2.5 Pro 全量供给 45.72%）10.1 个点**（注：早前流传的"官方 61.6"经核实无官方出处，已弃用）。
- 评测口径：只给检索片段、不给完整对话；回答模型 DeepSeek V4-flash，联想生成器 deepseek-chat（无思考模式）。
- 补充实验公开：图遍历对照 26.43%（+1.2 点，止损）、goal/value 专项、value 强化 2.0（失败机制已定位）。
- 脚本 `benchmark/scripts/locomo_plus/`（ingest → 联想生成 → 评测 → judge，全断点续写）+ 结果 `benchmark/results/locomo_plus/`（JSONL）全公开可复现。
- 报告：`docs/LoCoMo-Plus评测报告.md`。

### 反哺与止损

- 评测最大成果（联想检索）回写生产；止损结论公开：建边/图遍历多跳/value 类强化联想不再投入。

## v1.0.0（2026-10-18）

正式版 v1.0：三层记忆架构（图层 SQLite + 向量层 ChromaDB/BGE + 原文层 verbatim）+ 融合检索 `search_strong` + 生命周期管理 + REST API / MCP / CLI 桥接，随包发布 LoCoMo 官方基准全量评测（1986 题，三模型对照）。
