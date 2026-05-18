# B04 fix-log: 沙箱失败后缺少人工介入

## 问题描述

**现象**：sandbox 执行失败后，系统自动走 reflect → coder 重试循环，或重试耗尽后直接 fail，全程无人工介入机会。

**根因**：`builder.py` 的 `retry_or_fail` 路由函数（行 35-39）直接返回 `coder_agent` 或 `output_node`，跳过了 `human_review` 断点。

**影响**：
- 用户无法在失败时提供修复方向，coder 可能反复犯同样的错误
- 重试耗尽后直接输出失败报告，用户连"接受当前结果"的选择都没有
- HITL 设计初衷是每个关键决策点都让人参与，失败路径违背了这个原则

## 最初修复方案

**思路**：每次 sandbox 失败后立刻暂停人工介入。reflect_node 分析完 → human_review 断点 → 人决定。

```python
# 旧
def retry_or_fail(state):
    if state["retry_count"] >= MAX_RETRY:
        return "output_node"
    return "coder_agent"

# 新
def retry_or_fail(state):
    """反思后的路由：进入人工确认，让用户决定重试还是接受失败"""
    return "human_review"
```

**理由**（来自 Claude）：
- LLM 的 reflect 分析质量不可靠，盲重试可能越修越歪，浪费 API 调用
- 人看一眼反思分析 5 秒就能判断方向，比等 3 轮盲重试（2-3 分钟）效率高
- 重试耗尽后才让人介入等于把"最后一次机会"也浪费了

## 方案讨论

### 用户反对意见（4 点）

1. **高估人的评估能力** — 找 bug 方面大模型判断往往比人准确。加上当前 `human_feedback > reflection_notes > fix_instruction` 的优先级链，人给错方向 LLM 会被带偏。
2. **二者结合更好** — 完全无人工不行（LLM 会陷入幻觉），完全放弃自动 retry 也不行。最佳是 LLM 先自行纠错，不行了再人工介入。
3. **后续计划引入 retry 记忆机制** — 届时自动 retry 价值更大，"首次失败即 HITL"会放弃这个演进空间。
4. **API 成本顾虑** — 如果担心 retry 太多，MAX_RETRY 减一次即可，没必要完全取消自动 retry。

### 结论

用户方案更优，采纳。核心逻辑：**LLM 擅长的模式匹配（reflect 分析错误类型）+ 人擅长的方向判断（"别继续了，方向不对"）各做各擅长的事，不是互相覆盖。**

## 最终修复方案

只改 `builder.py` `retry_or_fail` 一个返回值：重试耗尽后 `output_node` → `human_review`。

```python
def retry_or_fail(state):
    """反思后的路由：未达上限→重新修复，已达上限→人工介入"""
    if state["retry_count"] >= MAX_RETRY:
        return "human_review"   # 原来是 "output_node"
    return "coder_agent"
```

**路由逻辑**：
- `retry_count < MAX_RETRY` → `coder_agent`（继续自动重试）
- `retry_count >= MAX_RETRY` → `human_review`（人工介入）
  - 人给反馈 → 回到 `coder_agent`（带人工指导继续修）
  - 人空白确认 → `output_node`（接受失败，输出 status=failed）

**MAX_RETRY 不变**（仍为 3），`should_continue_or_output` 完整复用，`interrupt_before=["human_review"]` 无需改动。

## 测试脚本

| # | 类型 | 覆盖 |
|---|------|------|
| 01 | 直接测 `retry_or_fail` | 无 LLM，验证各 retry_count 返回值 |
| 02 | 直接测 `should_continue_or_output` | 无 LLM，验证双分支路由 |
| 03 | 端到端 | os.popen 样本，追踪节点序列，验证失败路径 |
| 04 | 端到端 | exec 样本，空白确认 → output_node + status=failed |
| 05 | 端到端 | %% 格式化样本，人工反馈 → coder_agent 继续修 |

## 实施记录

### 2026-05-18 第一轮

- 修改 `builder.py:37`：`"output_node"` → `"human_review"`
- 创建 5 个测试脚本
- 01/02 直接路由测试通过（<1s，无 LLM）
- 03 端到端：触发 1 次 sandbox 失败 + retry，二轮 coder 修好，未达重试上限（status=partial）
- 04 端到端：sandbox 一轮通过（status=partial），失败路径未触发
- 05 端到端：sandbox 一轮通过（status=partial），失败路径未触发
- 三个样本代码均被 coder 正确修复，侧面印证硬禁令 + 四分类 critic 的有效性
