# LoCoMo 基准评测包

本目录是 SuperMem 在 **LoCoMo**（Long Conversation Memory）基准上的完整评测包：
**脚本、数据说明、逐题结果全部公开，可完整复现**。

- 评测规模：10 段超长多会话对话 / **1986 道 QA** / 五类推理
- 隔离原则：每段对话一个独立库（SQLite + Chroma），**零接触生产记忆库**
- 双口径：官方 **Token F1** + **LLM-Judge accuracy**
- 结果文件：`results/*.jsonl`（1986 行/文件，均含 sid/idx/question/gt/pred/分类）

---

## 一、数据来源

LoCoMo 数据集来自 **Snap Research LoCoMo**（官方仓库：[github.com/snap-research/LoCoMo](https://github.com/snap-research/LoCoMo)）。

评测使用官方 10 段多会话对话子集，结构：

```json
[
  {
    "sample_id": "conv_26",
    "conversation": {
      "session_1_date_time": "...",
      "session_1": [ {"speaker": "...", "text": "...", "dia_id": "..."}, ... ],
      "session_2": [...]
    },
    "qa": [
      {"question": "...", "answer": "...", "adversarial_answer": "...", "category": 1..5, "evidence": ["D1:3"]},
      ...
    ]
  }, ...
]
```

**注意**：adversarial 类（category 5，446 题）答案在 `adversarial_answer` 字段（官方口径），脚本已兼容。
请把下载的 `locomo10.json` 放到 `benchmark/data/` 下。

---

## 二、复现步骤

### 0. 前置

```bash
pip install -r requirements.txt   # 仓库根目录
# 本地下载 bge-small-en-v1.5 放入 storage/models/（或按 config.py 配置）
```

### 1. 建隔离库（10 个）

```bash
python benchmark/scripts/ingest_locomo.py
# 输出 benchmark/db/<sample_id>/super_memory.db + chroma_db
```

### 2. 跑 QA（三档 answerer，任选）

```bash
# 9B（Ollama，需已拉取 qwen3.5:9b）
python benchmark/scripts/eval_locomo_9b_l24.py

# 27B（Ollama，需已拉取 qwen3.8-27b-fast）
python benchmark/scripts/eval_locomo_27b_l24.py

# 125B（本地 Strata 引擎，需已启动 OpenAI 兼容端点）
set STRATA_API_KEY=your-key          # Windows
export STRATA_API_KEY=your-key       # Linux/macOS
set STRATA_MODEL=qwen3.8-flash-next-iq3_s   # 按本地部署档位
python benchmark/scripts/eval_locomo_125b_l24.py
```

每个脚本输出 `results/locomo_results_<档位>_l24.jsonl`，**断点续跑**（中断后重跑自动跳过已完成题目）。

### 3. 打分（官方 F1）

```bash
# 单文件全量 F1 统计请用 score_f1.py 与任意结果文件对比（--a 为主文件）
python benchmark/scripts/score_f1.py --a results/locomo_results_125b_l24.jsonl --b results/locomo_results_9b_l24.jsonl
# 输出：总体 F1、unknown 率、逐题胜平负、分类别明细
```

### 4. LLM-Judge accuracy（可选，需 DeepSeek 或其他 OpenAI 兼容 API）

```bash
set DEEPSEEK_API_KEY=sk-xxx
export DEEPSEEK_MODEL=deepseek-v4-flash   # 默认
python benchmark/scripts/judge_accuracy.py
# 输出 results/judge_<档位>_l24.jsonl + accuracy 汇总（总体/分类别/unknown 子集）
```

Judge 协议（同官方）：语义等价判对；`unknown` 一律判错；只输出 0/1；temperature=0。

---

## 三、结果文件

`results/` 下已随包发布 6 个全量结果（各 1986 行）：

| 文件 | 内容 |
|---|---|
| `locomo_results_9b_l24.jsonl` | 9B limit24 评测逐题结果 |
| `locomo_results_27b_l24.jsonl` | 27B limit24 评测逐题结果 |
| `locomo_results_125b_l24.jsonl` | 125B limit24 评测逐题结果 |
| `judge_9b_l24.jsonl` | 9B 逐题 judge 判定（0/1） |
| `judge_27b_l24.jsonl` | 27B 逐题 judge 判定 |
| `judge_125b_l24.jsonl` | 125B 逐题 judge 判定 |

成绩汇总与社区对比见 [`../docs/LoCoMo评测报告.md`](../docs/LoCoMo评测报告.md)。

---

## 四、口径声明（重要）

1. **F1 与 accuracy 不可直接对比**：社区主流报 LLM-Judge accuracy（GPT-4 级 judge、语义等价判对）；F1 是词面 token 重叠，数值天然偏低。
2. **本评测是纯检索片段 + 本地模型作答**，无 oracle、无近全量上下文；社区高分（92%+）普遍使用 GPT-4 级 answerer 或 oracle 检索，条件不同。
3. **题数口径**：官方 10 段全量 1986 题；部分社区自报用 1540 题子集。
4. LoCoMo **无官方统一排行榜**，社区成绩均为自报，且存在造假争议——本包以"脚本 + 逐题结果全公开"保证可复现。

---

## 五、LoCoMo-Plus 评测（v1.0.1 随包新增）

**LoCoMo-Plus** 是西安交大认知记忆基准（401 题：causal 101 / state 100 / goal 100 / value 100），聚焦隐藏抽象关联（跨时间因果、目标、价值观）的回忆能力。

### 复现步骤

```bash
# 0. 准备数据：官方 locomo_plus.json（含每样本多轮对话 + 前提问题）放入
#    benchmark/locomo_plus_data/locomo_plus.json（或用 LOCOMO_PLUS_JSON 环境变量指定）

# 1. 建隔离库（401 个独立库，生产版完整架构复刻）
set LOCOMO_PLUS_DATA=benchmark/locomo_plus_data
python benchmark/scripts/locomo_plus/ingest_locomo_plus_par.py

# 2. 生成联想句（无思考模式，需 DeepSeek 或其他 OpenAI 兼容 API）
set DEEPSEEK_API_KEY=sk-xxx
python benchmark/scripts/locomo_plus/bridge_assoc_v2_full_chat.py

# 3. 跑评测（给前提 + 基于记忆回答；answerer 默认 DeepSeek V4-flash）
python benchmark/scripts/locomo_plus/bridge_eval_v4_full_chat.py

# 4. judge 判定 + 补判（parse_fail 清零）
python benchmark/scripts/locomo_plus/judge_bridge_v4_full_chat.py
python benchmark/scripts/locomo_plus/judge_fix_parsed_chat.py
```

### 结果文件（`results/locomo_plus/`，已随包发布）

| 文件 | 内容 |
|---|---|
| `bridge_assoc_v2_full_chat.jsonl` | 401×8 联想句（统一口径定稿） |
| `bridge_judged_v4_full_chat.jsonl` | **定稿 judge 结果：224/401 = 55.86%** |
| `bridge_judged_base_full.jsonl` | 纯系统基线 judge：101/401 = 25.19% |
| `bridge_judged_graphx.jsonl` | 图遍历对照 judge：106/401 = 26.43% |
| `bridge_judged_gv.jsonl` / `bridge_judged_value16.jsonl` | goal/value 专项实验 judge |

### 口径声明

1. **只给检索片段、不给完整对话**（模拟真实记忆系统回忆使用方式），比官方"全量供给"更严苛。
2. 官方 61.6 是"模型看完整对话推理"上限；本评测 55.86% 是"记忆系统回忆供给"能力，达成度 91%。
3. 联想桥 = 检索管线 + LLM query 生成（HyDE 同类），30.7 点增益全部来自检索策略升级。
4. goal/value 短板与失败实验（value 强化 2.0 等）全部如实公开，见 [`../docs/LoCoMo-Plus评测报告.md`](../docs/LoCoMo-Plus评测报告.md)。
