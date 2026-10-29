# CHANGELOG

## v1.1.1（2026-10-29）

### 新增：注意力倾斜（attention tilt，借鉴 digital-life-core v0.9.9）

把数字生命内核的注意力阶梯（危机×3 / 日常×0.1）翻译为记忆检索的温和版：

- 检索结果**全量召回后按当前状态向量重排，只改顺序、不删内容**——"记忆一条都没丢，只是此刻该先看到什么"。
- 两层信号：
  - **活跃目标自动感知**：库内 `status=active` 的 goal 节点沿 contributes_to / blocks 边扩散加权，等价"此刻系统在忙什么，相关记忆优先"；
  - **importance 倾斜**：goal_related ×1.15、operational ×1.0、reference ×0.9，乘在融合分上。
- 默认开启（`TILT_ENABLED=True`，`TILT_GOAL_BOOST` / `TILT_REF_PENALTY` 可调）；无活跃 goal 或全 reference 库时零影响，评测对照一键 `TILT_ENABLED=False`。
- 检索响应新增 `tilted` / `tilt_boost` 字段，行为可观测。

改动：`config.py`（TILT_* 配置块）、`search_fusion.py`（recall_candidates / search / search_strong / nodes_query 全部支持 `tilt` 参数）、`api.py`（`/nodes`、`/search`、`/search_strong` 透传 `tilt` 参数）。

### 新增：写入回执验证（write receipt，借鉴 digital-life-core v0.9.19）

数字生命内核"7 场梦只存 1 场"的教训——**写完按原文读回，不假装成功**：

- `saved` 是唯一判定依据：写入后按主键直读节点，`content + project` 逐字比对才判已存。
- 读不回自动**再写一次**，二次不回才如实报未存住。
- `POST /nodes` 响应新增 `saved` / `verified` 字段；`POST /extract` 新增 `verified_n`（每条新建节点写后读回计数）。

改动：`api.py`（`_verify_saved` 静态方法 + /nodes、/extract 两条写入路径接入）。

## v1.1.0（2026-10-29）

### 剪枝优化：认知更新自动退休 + 访问热度衰减

按"平行替换 / 最小改动 / 剪枝"的优化哲学落地两条剪枝类优化，**不做加法、不加底座**：

- **矛盾自动退休 `auto_supersede_link`**：新节点内容命中认知更新词（放弃/不再/改为/换成/撤销/更新为/升级为/演进为等）且与库内旧 decision/rule/technique/project 节点语义相似（阈值 0.50）时，自动把旧节点流转为 outdated，并挂 `replaced_by` + `superseded_by` 边指向新节点。幂等；permanent 记忆不动。
- **访问热度衰减**：`compute_weight` 的 causal_factor 由裸 `access_count` 改为热度 `heat = access_count × e^(-ACCESS_HEAT_LAMBDA × 距上次访问天数)`（`ACCESS_HEAT_LAMBDA=0.02`，半衰期约 35 天）。最近仍在访问的记忆保留加成；很久以前频繁用、现在不再用的记忆，热度加成随遗忘曲线消退。无访问记录时行为与旧版完全一致。

改动：`config.py`（新增 `ACCESS_HEAT_LAMBDA` 与 `SUPERSEDE_*` 配置块）、`linker.py`（新增 `auto_supersede_link`）、`extract.py`（两条写入路径挂剪枝 hook）、`core.py`（新增 `_access_age_days`、`compute_weight` 改用 heat）。隔离测试 13/13 通过（`locomo_plus_eval/scripts/test_prune_features.py`）；**基准评测评分严格不变**（注入只写 event 节点、检索排序不调用 compute_weight、评分不碰 weight/status）。

### 随包补齐：四维自动联想（时间/场景/价值/状态）

- 时间维：时序连接词 → before/after 边 + recency 时间加权。
- 场景维：场景实体 → involves 边 + scene 定向检索。
- 价值维：目标/愿望短语 → goal 节点 + contributes_to/blocks 边 + importance 分级 + goal 重排。
- 状态维：状态转移词 → state 节点 + transitions_to 边 + state 定向检索。
- 该能力为生产已验证并评测过的成果（2026-10-20 上线），本次随包发布补全仓库代码一致性。

### 清理

- 移除与新版 core 不兼容的遗留 MVP 入口 `server.py`（发布入口以 `main.py` / `mcp_server.py` / `bridge/smem.py` 为准）。

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
