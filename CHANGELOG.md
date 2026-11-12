# CHANGELOG

## v1.2.4（2026-11-13）

### 概述：一次由全量测试驱动的 26 项缺陷大修

首轮全量测试跑出 14 条失败、23 项缺陷；补齐漏检项后共 **26 项**，全部处理（25 项修复 + 1 项复核为非缺陷）。改动 17 个源文件，全部先在生产库的隔离副本上验证，再回写生产代码根。

### 一、检索质量（P0，本轮改善的主体）

1. **锚点加成从"固定 +0.03"改为乘性加成**。原实现对每个锚点命中节点一律加固定分，命中数量多的弱相关节点可以盖过强命中。改为按绑定强度乘性加成（权重 0.35 × strength），并对无锚点支撑的结果设上限约束（`ANCHOR_CEIL_SLACK_RATIO = 0.05`），避免锚点路单独把低分节点顶上去。
2. **语义扩散不再污染排序**。related 扩散分改为独立记账（`rec["related_rrf"]`），只有主路（图 / 向量 / 关键词）零召回时才用它兜底；主路已有分的结果不再被扩散分抬高。同时加三档约束：strength ≥ 0.55 才扩散、每次最多 8 条、权重 0.7。
3. **中文长句不再不切词**。原 `_tokenize` 只按空格切，整句中文变成单个 token，关键词路完全失效。改为中文 2–4 字滑窗 gram、英数按词切、过滤口语虚词，并加命中门槛（至少命中一个 ≥2 字 gram，且"命中 ≥2 个 token 或最长 gram ≥4 字"），避免"记忆""系统"这类高频二字词让半个库的节点都拿到 0.6 分。
4. **关键词候选窗口 320 → 2000**。`_fts_recall` 的 `ORDER BY timestamp DESC LIMIT max(64, top_k*8)` 实际等于"只保留最新的 320 条命中行"，老记忆（往往正是答案）被整批丢弃。这是本轮最大的单点改善来源：窗口提到 2000 后 @1 直接从 53.0% 跳到 74.6%。800 档对照实测掉 2.2 个点且 p95 无改善，故取 2000。

**质量实测**（134 题自召回、同一套集子）：

| 指标 | 修复前 | 修复后 |
|---|---|---|
| @1 | 25.6% | **74.6%** |
| @3 | 64.7% | **94.0%** |
| @10 | 91.7% | **99.3%** |
| MRR | 0.478 | **0.846** |
| 未召回 | 11/134 | **1/134** |

库外问题（12 条库内无答案的提问）：拒答 **12/12**，不编答案。

### 二、数据一致性与接口（P1）

- **边类型被压平成 related**：别名映射表补 trigger / enable / related 族，历史 127 条非标准类型边归一。
- **grade 重复计算**：写入与状态变更各算一次，且两次入参不同。合并为单次，以节点最终状态为准。
- **`/health` `/stats` 的 gap 口径错**：把 merged / obsolete 节点也算进"向量缺口"，导致 gap 长期非零。改为只统计可索引节点。
- **`causal_density` 空边集必崩**：分母为零未保护。
- **中断预算重复扣减**：`spend` 前先读数、加锁后再读数，等于读一次扣一次。改为锁内唯一递增。
- **`cycle_detector` 不区分边类型**：把时序环（before/after）误判为因果环。改为按边类型过滤后跑迭代 Tarjan。
- **非法输入返回 500 且已写入**：`content=123` 这类输入在 try 之外崩在 `raw_vid` 上，节点却已入库。改为前置校验返回 400，不落库。
- **标签双转义**：`["a"]` 被 `json.dumps` 两次，读出来是字符串 `"[\"a\"]"`。改为幂等解两层转义 + 写入侧归一。
- **`/mark` 状态流转不删向量**：节点转 obsolete/merged 后旧向量仍参与检索。改为同步删向量。
- **状态变更不同步 grade**（第 24 项）：`set_status` / `mark_permanent` / `mark_outdated` / `merge_into` / `archive_nodes` 五个入口都只 `update_node(status)`，grade 留在旧值——生产库实测 6 条不一致（merged 仍是 experience、permanent 仍是 hint）。修法是在 `core.update_node` 锁内、构造字段集之前统一重算，一处兜住五个入口。

### 三、其余修复（按模块）

- `core.py`：`archive_nodes` 未真正归档；`apply_decay` 未跳过 obsolete/merged；`add_edge` 去重与写入分在两个锁；`compute_weight` 用创建时间而非最后活跃时间；`verify_index` 把 merged 计入缺口；补 `duplicate_edges` / `dedup_edges` / `indexable_node_count`。
- `api.py`：`/episodes` 无 limit 与 count；`/timeline` 改为路径式；`/rebuild` 同步阻塞改异步 202；`/extract` 补参数校验；`/decay` 补归档链；`_read_body` 坏 JSON 返回 400。
- `time_window.py`：时间解析补五格式；`build_episodes` 幂等键从 `(start,end)` 改为 `start`（窗口后移不再复制一份 episode）；`get_episodes` 默认 200 条。
- `event_normalizer.py`：时间带秒、边类型别名补 trigger / related 族。
- `linker.py`：双向边补齐 strength。`extract.py`：补 `return_ids`。
- `interrupt_budget.py`：`__exit__` 置 closed、`used` 属性、`exhausted` 改纯查询。
- `analyze_patterns.py`：缺 `import config` 导致必崩；`tag_frequency` 补过滤。
- `mcp_server.py`：错误码规范为 -32601 / -32602、顶层 error 字段。
- `node_lifecycle.py`：`merge_into` 删被合并节点向量。
- `scripts/selfcheck.py`：`duplicate_edges` 从"只报"改为"实删"；7a 项补删向量。
- `bridge/smem.py`：补 `raw_search`、检索输出 `[判定]` 前置、CLI search 改人类可读。

### 四、升级迁移（升级必跑）

新增 `scripts/migrate_fix_20261113.py`，8 类数据修复，**幂等**，支持 `--dry-run` 预演：

```bash
python scripts/migrate_fix_20261113.py --db storage/super_memory.db --dry-run
python scripts/migrate_fix_20261113.py --db storage/super_memory.db --apply
```

生产库（2149 节点 / 26452 边）实测：标签清洗 110、边类型归一 127、重复边删除 62（4 组）、related strength 回填 25646、节点时间戳 1689、创建/访问时间 749、grade 重算 20、episode 引用修复 1、episode 重复合并 10。**第二次执行全部为 0**。

迁移后可感知变化：episodes 98→87（同起点重复聚合被合并）、related 边 strength 由全 1.0 变为均值 0.698（扩散过滤阈值 0.55 从此真正生效）。

### 五、测试

11 个套件 **178/178 全绿**（约 5 分钟）：模块 25 / HTTP 读 44 / HTTP 写 36 / HTTP 维护 7 / 维护扩展 9 / 缺陷冒烟 17 / 归档链 6 / 生命周期 6 / MCP 11 / CLI 桥接 10 / 重建锚点 7。

### 六、代价（如实记录）

- 交错延迟 p95 263ms → 415ms，均值 137ms → 150ms。
- 并发：4 并发 p50 382 → 461ms（9.9 → 7.6 qps）；8 并发 563 → 793ms（12.4 → 8.7 qps）。
- 当前归因为"关键词候选池 320→2000 导致 Python 层构造 Node 增多 + GIL 争用"，**尚未做单变量实验**，列为未闭环项。节点规模到 1 万时需要重设关键词召回策略（SQL 侧预打分 / 分批取 / 命中行数自适应）。

### 改动文件

`config.py` `core.py` `search_fusion.py` `api.py` `anchors.py` `time_window.py` `event_normalizer.py` `linker.py` `extract.py` `cycle_detector.py` `interrupt_budget.py` `analyze_patterns.py` `mcp_server.py` `node_lifecycle.py` `scripts/selfcheck.py` `bridge/smem.py`，新增 `scripts/migrate_fix_20261113.py`。

逐条明细见 [大修修复清单](docs/大修修复清单.md)，实验过程与数据见 [大修后重测报告](docs/大修后重测报告.md)。

## v1.1.4（2026-11-13）

### 检索端改造：该答的能答，不该答的说不知道

检索端长期有两个方向相反的毛病：该答的不答（知识明明在库里，判成"没有"），以及不该答的硬答（库里只有沾边记录，也给出结论）。另外节点涨到一万后，关键词这一路耗时近乎线性增长。本版一次收掉这四件事。

**① 经验转正（节点级 grade：`experience` / `hint`）**

只靠相似度挑"最像的那条"当答案依据，等于把事件流水账也当结论用。新增 `compute_grade()`，给每个节点落一个 `grade` 字段：

- `experience`：可作结论依据的经验——decision / rule / technique / project / goal / insight / fact / experiment / milestone 中，confidence ≥ 0.80、被命中 ≥ 2 次、120 天内仍有访问；
- `hint`：线索，只能说明"库里有这么一条"，不可直接当结论引用。

状态为 outdated / obsolete / disputed / merged 的强制降回 `hint`；`confirmed` / `permanent` 直接视为经验（当前生产库内 0 条，这条闸门暂时空转）。给 event 不发经验牌（库里 1100+ 条流水账，发了就等于让事件记录冒充结论），task / state / todo 同理。生产 2148 节点中判定为经验卡的约 514 条。

**② 检索拒答（结论级 verdict：`answer` / `record` / `hint` / `no_match`）**

新增 `judge_verdict()`，按"绝对地板 → 字面或锚点支撑 → 证据成色 → 相对间隔"依次判定：

- `answer`：有明确最佳命中，且 top1 是经验卡，top1/top2 分比 ≥ 1.10；
- `record`：top1 是一手记录（事件 / 状态 / 待办），排序明确但只能当记录答，响应标明"这是记录，不是结论"；
- `hint`：命中了但只能当线索（分数挤在一起，或 top1 未转正）；
- `no_match`：库内没有相关内容。

同时附原因码 `ok` / `record_only` / `margin_flat` / `top1_not_evidence` / `below_floor` / `no_lexical_or_anchor_support` / `no_results`。纯向量"看着近"不算有内容：top5 里既无锚点命中也无整串字面命中的，直接判 `no_match`。

`VERDICT_MARGIN_MIN` 1.25 → 1.10：13 条 query A/B 中只有"小妖 3D 形象"一条从 hint 变为 answer/ok（生产实测该条 top1/top2 为 0.0577/0.0552，属"多条经验真的接近"），其余 12 条不动，3 条无关 query 判定一致。

**③ 实体锚定检索（新增模块 `anchors.py`）**

写入时把节点里的实体词（人名 / 系统 / 项目 / 作品等）抽进一张锚点表，检索时先查表，命中即把对应节点并入融合分。补的是"字面不通、语义也漂"的那一路：问"小妖的形象"和库里的 3D 建模记录没有共同词，向量距离也远，但"小妖"作为实体锚点能直接连起来。

- 三路（图 / 向量 / 锚点）各自独立召回后 RRF 合并，`ANCHOR_SEARCH_WEIGHT=0.55`、命定值加成 `ANCHOR_BOOST=0.03`（按绑定强度给，不按命中词数给，否则只在 tags 蹭到实体词的节点会被顶到 top1）；
- 准入闸门：只被一个节点用到的词（私有词）不进表（`ANCHOR_MIN_DF=2`）；中文词 df 上限 50、拉丁词 200（泛词会污染路由）；口语虚词与内部元标签进停用表；
- **抽词来源有取舍**：中文词只从 `tags`、书名号/引号引用、实体节点内容里取，不从正文自由切 n-gram——实测在闲聊语料上自由切词必然产出口水话（"救命""也是""服了"）把路由表淹掉；正文只贡献拉丁字母与型号 token（Hive、125B、bge-small-zh），这类词几乎不会误伤；
- 表上限 20000 行。

**④ 淘汰策略修复：hits 从没被写过**

锚点表快满时按 `hits ASC, updated_at ASC` 淘汰，但检索命中从来不回写 `hits`，它恒为 0——淘汰于是退化成"纯按时间删最老的"，迁移时刚建好的高价值实体锚点被第一批删掉。生产实测相关记忆召回 58 → 37。

三步修：

1. **命中回写**：`hook_recall` 返回前对命中的词执行 `hits = hits + 1`；
2. **四级排序**：`ORDER BY df ASC → weight ASC → hits ASC → updated_at ASC`（只做第 1、2 步时仍从 58 掉到 37，加上 df 这一层才稳住）；
3. **写回缓冲**：逐条 `UPDATE ... COMMIT` 会让钩子层慢 4–9 倍，改为内存缓冲 + 三条触发条件（200 个词 / 30 次调用 / 600 秒）任一满足即一次 `executemany`。

表满回收一次多删留缓冲：不再删到刚好 20000，而是一次删到 19000（`ANCHOR_CEIL_SLACK_RATIO=0.05`），避免每写一条就触发一次回收。1300 条新记忆模拟：超限回收 **188 次 → 3 次**，总成本 **5076 ms → 134 ms**（约 38 倍），四个常用词命中数零衰减。

**⑤ 关键词路：从 Python 全表遍历改为 SQL 层先筛**

`_fts_recall` 原实现是 `list_nodes(limit=100000)` 把每一行实例化成 Node 对象、再在 Python 里逐条做子串匹配——不是索引查询，是纯 Python 全表扫，耗时随节点数线性增长。改为在 SQL 里 `instr(lower(content), ?) > 0 OR instr(lower(tags), ?) > 0` 粗筛命中行，只对命中行构造对象再算原分数；打分、排序、top_k、max_turn 全部保留：

| 库 | 改前 | 改后 | 加速 |
|---|---|---|---|
| 生产库（2147 节点） | 235 ms | 51 ms | 4.6× |
| 万节点库（10300 节点） | 1135 ms | 242 ms | 4.7× |

两库各 10 条 query 的结果 id 顺序与每项分数**逐项一致**；13 条 query 的判据快照（verdict / reason / top1 / 前 5 名）差异 **0 条**。

**万节点压测（隔离实例，7078 → 10000 节点）**

- 检索延迟（10 query × 锚点开/关 × 3 次中位）：锚点开 234 → 307 ms（+31%），关 245 → 312 ms（+27%）；
- 表满回收成本只跟表行数有关、与节点数无关：20050 行表 `_enforce_ceil` 34.6 ms 删 1050 行到 19000，高 df 实体词一个没丢；`_prune` 61.5 ms；
- 写入 300 条：p50 75 ms / p95 306 ms / p99 632 ms，最大值 6.6 s 仅为首条冷启动。

**一条被推翻的判断**：曾认为中文词 df 上限 50 在万节点偏紧、建议提到 120–150。真实词频核查否掉了它——生产库中文词 276 个、最高 df 是"小妖"38；万节点库中文词 3234 个、最高 df 是"用户"32；两库 df ≥ 50 的中文词都是 **0 个**。此前的"用户 df=435"是构造 20050 行测试表时反复复用同几个词造成的假象。上限保持 50。

改动：`anchors.py`（新增）、`core.py`（grade 判定 + 锚点表接入 + 命中回写）、`search_fusion.py`（判据 + 锚点路 + `_fts_recall` SQL 化）、`config.py`（`GRADE_*` / `VERDICT_*` / `ANCHOR_*` 三块参数）。完整过程、逐项数据与回退方式见 [docs/检索端改造报告.md](docs/检索端改造报告.md)。

**已知未做**：关键词路仍是全表扫（`instr` 用不到索引），10 万节点约 240 ms，真正解法是 FTS5 或自建倒排；`status=confirmed` 转正闸门当前空转（库内 0 条）；万节点 A/B 只在隔离实例做过，生产库未跑全量。

## v1.1.3（2026-11-07）

### 修复：遗忘曲线失效（权重不再被访问次数顶格、衰减真正生效）

一次检索命中已失效的旧结论，追查后发现衰减机制在真实数据上基本没生效，两处结构性缺陷：

- **时间戳解析失败导致永不衰减**：`_age_days` / `_access_age_days` 只认 `%Y-%m-%d %H:%M:%S`，遇到缺秒（`2026-09-30 17:23`）、ISO 带时区、纯日期格式时静默兜底为"现在"，age=0 使衰减项恒为 1。生产库实测 76.5%（1494/1953）节点命中此问题。新增 `_parse_ts()` 多格式兼容（ISO 优先 + 缺秒/日期/微秒回退），解析失败返回 None 而不再伪造当前时间。
- **访问热度乘进权重导致顶格**：旧 `causal_factor` 用 `edge_count × heat`，访问 51 次即可顶到上限 2.0，`0.7 × 2.0 = 1.4` 被截为 1.00——被反复检索的错误记忆长期满权、无法退休，而新写入条目出生只有 0.70。修为 `min(1 + 0.3·log1p(edges), 1.3)`，访问量只保留在检索排序的 access 分量。`CAUSAL_FACTOR_MAX` 2.0 → 1.3，另加 `NEWNESS_BOOST=1.2`（7 天内新写入）。

效果（生产库 1953 节点全量重算）：权重顶格节点 1036 → 37；错误旧条 1.00 → 0.35（Cold）；当天新写入条目 0.70 → 1.00（Core）；归档 0 条无误伤。

改动：`core.py`（`_parse_ts` + `_age_days` / `_access_age_days` / `compute_weight`）、`config.py`（上限与 newness 配置）。完整过程、前后数据与回退方式见 [docs/权重衰减修复报告.md](docs/权重衰减修复报告.md)。

**已知未修**：检索精排层仍无 recency（改动会连带影响既有评测基线，留待独立评估）；自动退休仍只认关键词（放宽到"同 tags 自动退休"存在误杀风险，暂不启用）。

## v1.1.2（2026-11-03）

### 修复：语义关联建边缺陷（related 边"漏建 + 有边不召回"）

生产库实证触发：两条算力口径记忆（解锁基准 `decision_20261031_d923a647` 与挖矿收益核算 `event_20261031_a25b13de`）分存两处、语义检索两轮都带不出对方——"该关联的没关联上，关联上的又不参与召回"。定位为双缺陷：

- **缺陷 A（写入侧）**：`auto_link` 只在入库那一刻跑一次、k=2 限量、永不回溯——先写节点找不到后写节点（时间差漏边），后写节点 top-2 被占满（漏边永不补）。
- **缺陷 B（召回侧）**：`search_fusion` 只沿因果语义边（enable / derives_insight / refutes / improves）扩散，占边量 97% 的 related 边完全不参与召回（有边不用）。

修复（防爆方案，全量落地）：

- **双向建边**：新→老 的同时补 老→新，复用同一次 vector.query，零额外成本，根治时间差漏边。
- **k 2→8 有限放宽 + 阈值分层**：score≥0.70 强关联全建；[0.45, 0.70) 弱关联每次最多 3 条，防图稠密稀释。
- **related 扩散召回**：检索命中种子后沿 related 边双向扩散一跳，top 20 截断并入 RRF（默认开启，`RELATED_DIFFUSE_ENABLED` / `RELATED_DIFFUSE_MAX` 可调）。
- **历史漏边一次性补建**：新增 `scripts/backfill_related_edges.py`（幂等、限量、支持 `--dry-run` 预演），不做周期全库重扫。

验证（全实证）：图规模 nodes=1907 / edges 9647→25947（backfill 9113 配对，双向 dedup 后净增 16300 条）；检索"显卡算力解锁"第 2 名直接带出挖矿核算节点（修复前两轮检索均带不出）；写入新节点 8 出 + 8 入双向对称、边数增长可控。

改动：`config.py`（语义关联链路配置块）、`linker.py`（auto_link 双向建边 + 分层限量）、`search_fusion.py`（related 扩散路）、`scripts/backfill_related_edges.py`（新增）。

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
