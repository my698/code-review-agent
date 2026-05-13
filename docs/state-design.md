# State 结构设计

## 1. State 全景

AgentState 是整个 LangGraph 流程中唯一的数据载体。每个节点从 State 读取输入，处理后将结果写回 State。

```
AgentState
├── 输入
│   └── original_code: str              ← 用户提交的原始代码
├── 解析结果
│   └── code_analysis: CodeAnalysis     ← 代码解析节点输出
├── 三路审查结果（并行累积）
│   └── review_results: list[ReviewResult]  ← Annotated 累积
├── 汇总结果
│   └── critic_summary: CriticSummary   ← Critic 节点输出
├── 修复结果
│   └── coder_result: CoderResult       ← Coder 节点输出
├── 沙箱验证
│   └── sandbox_result: SandboxResult   ← 沙箱节点输出
├── 反思控制
│   ├── retry_count: int                ← 当前重试次数
│   └── reflection_notes: str           ← 反思分析内容
├── 人工审核
│   └── human_feedback: str             ← 用户修改意见
└── 最终输出
    ├── final_report: FinalReport       ← 最终报告对象
    └── status: str                     ← running | success | failed
```

---

## 2. 字段详解

### 2.1 `original_code: str`
- **来源**：用户输入
- **写入者**：起始节点（用户触发时填入）
- **读取者**：code_parser、coder_agent、reflect_node、output_node
- **说明**：整个流程的起点，**只读不修改**。Coder 修复后会生成新的代码字段，原始代码保留用于对比。

### 2.2 `code_analysis: CodeAnalysis`
- **来源**：code_parser 节点
- **写入者**：code_parser
- **读取者**：security_reviewer、performance_reviewer、style_reviewer
- **说明**：代码解析后的结构化信息，作为三个审查 Agent 的共同输入。

### 2.3 `review_results: Annotated[list[ReviewResult], operator.add]`
- **来源**：security_reviewer、performance_reviewer、style_reviewer（并行）
- **写入者**：三个审查 Agent（并行写入）
- **读取者**：critic_agent、output_node
- **说明**：**关键设计**——使用 `Annotated[list, operator.add]` 类型，确保三个并行节点同时写入时不会互相覆盖。LangGraph 框架会自动将每次写入追加到列表末尾，而不是替换整个字段。

  如果不加 `operator.add`，后写完成的节点会把先写的结果覆盖掉。

### 2.4 `critic_summary: CriticSummary`
- **来源**：critic_agent 节点
- **写入者**：critic_agent
- **读取者**：coder_agent、output_node
- **说明**：去重、排序后的修复方案，是 Coder 执行修复的依据。

### 2.5 `coder_result: CoderResult`
- **来源**：coder_agent 节点
- **写入者**：coder_agent
- **读取者**：sandbox_executor、human_review、output_node
- **说明**：每次 Coder 节点执行都会**覆盖**此字段（不是累积——每次重试产生的是新版本修复代码）。

### 2.6 `sandbox_result: SandboxResult`
- **来源**：sandbox_executor 节点
- **写入者**：sandbox_executor
- **读取者**：条件边（根据 success 判断走 human_review 还是 reflect_node）、output_node
- **说明**：执行结果，`exit_code == 0` 表示通过。

### 2.7 `retry_count: int`
- **来源**：初始化为 0
- **写入者**：reflect_node（每次反思 +1）
- **读取者**：条件边（判断是否超过上限 3）、output_node
- **说明**：**覆盖写入**——每次只存当前计数值，不需要累积。

### 2.8 `reflection_notes: str`
- **来源**：reflect_node 节点
- **写入者**：reflect_node
- **读取者**：coder_agent（作为重新修复的参考思路）
- **说明**：反思节点分析失败原因后生成的新修复思路，Coder 下次修复时会参考此内容。

### 2.9 `human_feedback: str`
- **来源**：human_review 节点（用户输入）
- **写入者**：human_review
- **读取者**：coder_agent（用户意见带回重新修复）
- **说明**：用户在确认页输入的修改意见。空字符串表示用户直接点了"确认"。

### 2.10 `final_report: FinalReport`
- **来源**：output_node 节点
- **写入者**：output_node
- **读取者**：前端展示层
- **说明**：整个流程的最终产出物，包含所有审查和修复信息的完整报告。

### 2.11 `status: str`
- **来源**：各节点都可能更新
- **写入者**：起始节点（`"running"`）、output_node（`"success"` 或 `"failed"`）
- **读取者**：前端展示层
- **说明**：标识当前流程状态，前端根据此字段决定展示正常结果还是错误提示。

---

## 3. 字段读写矩阵

| 字段 | code_parser | security | perf | style | critic | coder | sandbox | reflect | human | output |
|------|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| original_code | R | - | - | - | - | R | - | R | - | R |
| code_analysis | W | R | R | R | - | - | - | - | - | - |
| review_results | - | W | W | W | R | - | - | - | - | R |
| critic_summary | - | - | - | - | W | R | - | - | - | R |
| coder_result | - | - | - | - | - | W | R | - | R | R |
| sandbox_result | - | - | - | - | - | - | W | - | - | R |
| retry_count | - | - | - | - | - | - | - | W | - | R |
| reflection_notes | - | - | - | - | - | R | - | W | - | - |
| human_feedback | - | - | - | - | - | R | - | - | W | - |
| final_report | - | - | - | - | - | - | - | - | - | W |
| status | init=W | - | - | - | - | - | - | - | - | W |

> R = 只读，W = 写入，- = 不碰

---

## 4. 并行写入的安全性设计

三个审查 Agent 并行执行时都会向 `review_results` 写入数据。普通字段如果三个节点同时写，后执行的会覆盖先执行的，导致丢数据。

**解决方案**：使用 LangGraph 的 `Annotated` + `operator.add`：

```python
from typing import Annotated
import operator

class AgentState(TypedDict):
    review_results: Annotated[list[ReviewResult], operator.add]
```

- `Annotated` 告诉 LangGraph：这个字段的写入有特殊规则
- `operator.add` 定义规则：新值追加到旧值后面（列表相加），而不是替换
- 最终结果：安全Agent写1个、性能Agent写1个、风格Agent写1个 → State里是 `[sec_result, perf_result, style_result]`，谁也不会丢

**三个审查 Agent 的返回格式统一为**：
```python
{"review_results": [review_result]}  # 单元素列表
```
这样 `operator.add` 会将其追加到已有列表中。

其余字段（如 `coder_result`）都是串行执行节点写入，不存在并发冲突，无需特殊处理。
