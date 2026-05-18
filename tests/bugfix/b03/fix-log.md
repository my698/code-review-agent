# B03: 评分公式 — 修复日志

## 问题描述

`output_node` 的评分逻辑存在两个 bug：

**Bug 1 — score_after 通胀**：
```python
score_after = min(score_before + len(changes) * 3, 100)
```
每个 change 盲目加 3 分，不区分 severity。修复 20 个 LOW 风格问题可以从 42 分涨到 100 分。
改动越多分越高 —— 逻辑倒挂（改动多说明原始代码问题多，不应是加分理由）。

**Bug 2 — 失败无扣分**：
```python
else:
    score_after = score_before
```
sandbox 失败时 score_after 不变。修坏了的代码和没修代码得分一样，不合理。

---

## 最初修复方案

### score_after 公式重新设计

当前：`score_before + len(changes) * 3`

修复方向：
- 降低每处修复的加分（`*3` → `*2`），避免多次修复的线性累加导致通胀
- 加上限防止极端跳跃——低分代码修几处不应接近满分
- 失败场景加扣分（`-10`）

### 方案

```
score_before = critic.score_before（critic 评的原始分）

if sandbox_passed and changes:
    提升上限 = (100 - score_before) // 2（最多提升到剩余空间的一半）
    实际提升 = min(len(changes) * 2, 提升上限)
    score_after = min(score_before + 实际提升, 100)

elif sandbox_passed and not changes:
    score_after = score_before（无改动，分不变）

else（sandbox 失败）:
    扣分 = 10
    score_after = max(score_before - 扣分, 0)
```

关键改动：
- `*3` → `*2`
- 新增提升上限 `(100 - score_before) // 2`，从根本上防止极端跳跃
- 失败扣 10 分

---

## 测试概况

| 轮次 | 测试脚本 | 样本类型 | 目的 |
|------|---------|---------|------|
| R1 | `test_b03_01_score_formula.py` | SQL 注入 | 检测单处修复评分变化 |
| R2 | `test_b03_02_multi_issue.py` | SQL注入+O(n²)+命名乱+格式烂 | 多问题修复时通胀检测 |
| R3 | `test_b03_03_failure_penalty.py` | 微妙代码（LLM易修坏） | 失败扣分逻辑检测 |
| R4 | `test_b03_04_low_score.py` | 严重漏洞（注入+硬编码+eval+裸except） | 低分极端跳跃边界检测 |
| R5 | `test_b03_05_clean.py` | 干净代码 | 无问题时代码评分稳定性 |

---

## 修复过程中连带发现的问题

### 连带 #1：`fixed_count` 字段冗余

**来源**：R2-R5 测试中发现 `coder.fixed_count` 与 `len(coder.changes)` 经常不一致。LLM 填充 `fixed_count` 时偶尔漏填（=0）或填错。

**分析**：`fixed_count` 是 LLM 填的整数字段，其值与 `changes` 列表的实际长度存在不一致风险。两个值表达了同一信息，保留两者只有同步错误的代价。

**修复**：`src/models.py` — 从 `CoderResult` 删除 `fixed_count: int = 0` 字段。所有使用处改为 `len(coder.changes)`。

涉及文件：
- `src/models.py` — 删除 `fixed_count` 字段
- `tests/bugfix/b03/` — 全部 5 个测试脚本，`fixed_count` / `coder.fixed_count` 替换为 `len(changes)` 或 `changes_count`

### 连带 #2：`CoderResult.changes` / `skipped_items` 缺少 null 保护

**来源**：B01 修复过程中发现 `ReviewResult.issues` 遇到 LLM 返回 `null` 时 Pydantic `default_factory=list` 不触发，导致 `None` 值透传。同理，`CoderResult.changes` 和 `skipped_items` 也需要相同防护。

**修复**：`src/models.py` — `CoderResult` 新增 field_validator：
```python
@field_validator("changes", "skipped_items", mode="before")
@classmethod
def default_list_to_empty(cls, v):
    return v if v is not None else []
```

### 连带 #3：`Issue.suggestion` / `description` / `code_snippet` / `lineno` 缺少 null 保护

**来源**：测试 03 运行中 performance_reviewer 输出 Issue 缺少 `suggestion` 字段，导致 `ValidationError` 崩溃。

**修复**：`src/models.py` — `Issue` 新增 4 个 field_validator：
```python
@field_validator("suggestion", "description", "code_snippet", mode="before")
@classmethod
def missing_string_fallback(cls, v):
    return v if v is not None else ""

@field_validator("lineno", mode="before")
@classmethod
def missing_lineno_fallback(cls, v):
    return v if v is not None else 0
```

### 连带 #4：HITL 两阶段执行导致测试脚本状态丢失

**来源**：`interrupt_before=["human_review"]` 将流程拆为两个阶段。第二阶段 `stream_until_pause(None, config)` 从空 state 开始，只捕获第二阶段事件。`coder_result` 和 `critic_summary`（第一阶段产物）丢失，导致测试中 `changes_count=0` 但 `score_delta != 0`。

**修复**：全部 5 个测试脚本在两阶段之间手动合并状态：
```python
for k in ["coder_result", "critic_summary", "review_results"]:
    if k not in state2 and k in state:
        state2[k] = state[k]
```

---

## 最终修复方案

### 一、`src/graph/nodes.py` — output_node 评分公式（行 326-336）

```python
score_before = critic.score_before if critic else 100
if sandbox_passed:
    if changes:
        # [B03] 每处修复 +2，提升上限为剩余空间的一半（不过度膨胀）
        improvement = min(len(changes) * 2, (100 - score_before) // 2)
        score_after = min(score_before + improvement, 100)
    else:
        score_after = score_before
else:
    # [B03] 沙箱失败扣 10 分
    score_after = max(score_before - 10, 0)
```

### 二、配套修复汇总

| 文件 | 变更 | 来源 |
|------|------|------|
| `src/graph/nodes.py` — output_node | 评分公式：`*3`→`*2` + 提升上限 + 失败 `-10` | Bug 1, 2 |
| `src/models.py` — `CoderResult` | 删除 `fixed_count` 字段 | 连带 #1 |
| `src/models.py` — `CoderResult` | `changes` / `skipped_items` 新增 null-guard validator | 连带 #2 |
| `src/models.py` — `Issue` | `suggestion`/`description`/`code_snippet`/`lineno` 新增 null-guard validator | 连带 #3 |
| `tests/bugfix/b03/test_b03_01_score_formula.py` | 适配新公式 + 状态合并修复 | 连带 #1, #4 |
| `tests/bugfix/b03/test_b03_02_multi_issue.py` | 适配新公式 + 状态合并修复 | 连带 #1, #4 |
| `tests/bugfix/b03/test_b03_03_failure_penalty.py` | 适配新公式 + 状态合并修复 | 连带 #1, #4 |
| `tests/bugfix/b03/test_b03_04_low_score.py` | 适配新公式 + 状态合并修复 | 连带 #1, #4 |
| `tests/bugfix/b03/test_b03_05_clean.py` | 适配新公式 + 状态合并修复 | 连带 #1, #4 |

---

## 遗留问题 & 未来修改方案

### `score_before` 字段的价值存疑

当前 `score_before` 来自 `critic.score_before`，即 LLM 对原始代码的评分。经过多轮测试观察到：

- 多漏洞代码（SQL 注入 + 硬编码 + eval + 裸 except，测试 #4）：LLM 给 45 分
- 干净代码（测试 #5）：LLM 给 25 分

LLM 打分不可靠，用它做评分基线等于在一个随机数上做运算。而且 `score_before` 提供的两个用途都可以不用它实现：

1. **修复前后对比**：用纯 delta 方案等效——初始分 = 0，修复后 `+N` 表示改善，`-10` 表示失败
2. **失败扣分基准**：公式 `max(score_before - 10, 0)` 依赖 `score_before`，但 delta 方案直接用 `-10` 即可

**简化方案（待后续实施）**：
- `CriticSummary` 去掉 `score_before`（LLM 不再打分，只做去重排序）
- `FinalReport` 只保留单一 `score` 字段（从 0 开始的纯 delta）
- `output_node` 公式：
  - 沙箱通过 + 有改动：`score = min(len(changes) * 2, 10)`（封顶 +10）
  - 沙箱通过 + 无改动：`score = 0`
  - 沙箱失败：`score = -10`
- 信息量不变（用户看到 +6 就知道改进了 6 分的程度），但不再受 LLM 打分波动污染
