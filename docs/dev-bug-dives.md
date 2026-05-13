# 典型 Bug 深度分析

> 从 `dev-issues.md` 中精选。每篇记录一个非显而易见的 bug：错误现象 → 错误假设 → 根因 → 修复 → 经验。
> 适合面试展示、技术复盘。

## 目录

- [问题 #1：Send 第二个参数导致分支 state 缺少字段 —— `KeyError: 'original_code'`](#问题-1send-第二个参数导致分支-state-缺少字段--keyerror-original_code)
- [问题 #2：HITL `interrupt_before` 中断不抛异常 —— 流程静默走完但报告未生成](#问题-2hitl-interrupt_before-中断不抛异常--流程静默走完但报告未生成)
- [问题 #3：LLM 返回 `"issues": null` 导致 `AttributeError`](#问题-3llm-返回-issues-null-导致-attributeerror)
- [问题 #4：LLM 返回枚举非法值导致 `ValidationError` —— 系统性加固所有枚举字段](#问题-4llm-返回枚举非法值导致-validationerror--系统性加固所有枚举字段)

---

## 问题 #1：Send 第二个参数导致分支 state 缺少字段 —— `KeyError: 'original_code'`

**日期**：2026-05-11

**错误信息**：
```
KeyError: 'original_code'
During task with name 'security_reviewer' and id '0506ac5d-981d-50fc-c205-cfed1cf1bc59'
```

**触发位置**：`src/graph/nodes.py` 第 40 行，`security_reviewer` 节点内：

```python
HumanMessage(content=f"原始代码：{state['original_code']}"),
```

**错误代码**（`src/graph/builder.py` Send 分发函数）：

```python
def fanout_to_reviewers(state: AgentState) -> list[Send]:
    return [
        Send("security_reviewer", {"code_analysis": state["code_analysis"]}),
        Send("performance_reviewer", {"code_analysis": state["code_analysis"]}),
        Send("style_reviewer", {"code_analysis": state["code_analysis"]}),
    ]
```

**我们当时的错误理解**：

当时认为 Send 第二个参数是"覆盖/叠加到主 state 副本上"——即分支 state = 主 state（完整 13 个字段） + Send 覆盖的字段。按这个理解，只传 `code_analysis` 就够了，因为 `original_code` 会从主 state 继承过来。

这个理解是错的。

**排查过程**：

1. `code_parser` 节点执行成功，说明入口节点的 state 是完整的（`INITIAL_STATE` + 手动 set 的 `original_code`）
2. 错误发生在 `security_reviewer`，它是 Send 分发的目标
3. 检查 `fanout_to_reviewers` 函数，发现三个 `Send` 都只传了 `code_analysis`，没有传 `original_code`
4. 打印错误是 `KeyError: 'original_code'`，说明分支 state 里根本没有这个 key
5. 得出结论：Send 分支不会自动继承主 state 的其他字段

**正确理解**：

**Send 的第二个参数就是目标分支的全部 state 输入。** 主 state 的其他字段不会自动带过来。目标节点需要什么字段，Send 必须全部显式传入。

```
错误模型：分支 state = 主 state + Send 覆盖
正确模型：分支 state = Send 第二个参数（仅此而已）
```

| 假设 | Send("xx", {"code_analysis": ...}) | 分支能读到 original_code? |
|------|------|:---:|
| 错误理解 | 主state + code_analysis 覆盖 | ✅ 能（从主 state 继承） |
| 实际行为 | 分支 state **只有** code_analysis | ❌ 不能（没传就没有） |

**为什么会有错误理解**：

直觉上 LangGraph 的 state 是所有节点共享的，以为 Send 只是在共享 state 上临时修改一下传给分支。实际上 Send 是为每个分支创建独立的 state 副本，副本的初始内容由 Send 第二个参数决定，不继承主 state。

**修复后的代码**：

```python
def fanout_to_reviewers(state: AgentState) -> list[Send]:
    return [
        Send("security_reviewer", {
            "code_analysis": state["code_analysis"],
            "original_code": state["original_code"],   # 必须显式传
        }),
        Send("performance_reviewer", {
            "code_analysis": state["code_analysis"],
            "original_code": state["original_code"],
        }),
        Send("style_reviewer", {
            "code_analysis": state["code_analysis"],
            "original_code": state["original_code"],
        }),
    ]
```

**`return` 的行为不受影响**：

节点 `return` 的字典仍然会合并回主 state。这个理解始终正确。

**经验教训**：

1. Send 第二个参数不是"覆盖"，是目标分支的**全部 state 输入**。一个字段都不少。
2. 设计阶段对 API 的理解必须经过实际运行验证，不能单靠直觉和推理。
3. 如果错误发生在 Send 目标节点中且是 KeyError，优先怀疑 Send 传参不完整。
4. `Send("xx", {})` 传空字典，目标分支拿到的就是空 state，读任何字段都会 KeyError。空字典不是"用主 state"的意思。
5. 语法笔记中保留错误记录 + 纠正记录，对比学习比覆盖更有价值。

---

## 问题 #2：HITL `interrupt_before` 中断不抛异常 —— 流程静默走完但报告未生成

**日期**：2026-05-11

**错误现象**：
运行 `python scripts/run.py`，控制台输出：

```
正在执行审查流程...

=== 最终审查报告 ===
报告未生成，请检查上游流程
```

没有报错，没有异常，但 `final_report` 为 `None`，`status` 为 `"running"`。

**当时的错误代码**（`scripts/run.py`）：

```python
# 错误：以为 interrupt_before 会抛异常
try:
    result = app.invoke(initial_state, config)
except Exception:
    # HITL 中断，注入审批意见后恢复
    print(">>> 暂停在 human_review 节点...")
    app.update_state(config, {"human_feedback": ""})
    result = app.invoke(None, config)
```

**排查过程**：

1. 加了调试日志打印 `result` 中所有 key 的值，发现 `code_analysis`、`review_results`、`critic_summary`、`coder_result`、`sandbox_result` 全部正常输出（说明 `code_parser` → 审查员 → `critic_agent` → `coder_agent` → `sandbox_executor` 全线跑通）
2. 但 `final_report` 是 `None`，`status` 是 `"running"`
3. `except` 里的打印没有出现，说明 `invoke` 没有抛异常
4. 即 `interrupt_before` 在 `human_review` 前暂停了，但**不抛异常**，`invoke` 静默返回当前 state
5. 代码没意识到已经中断，直接跑去读 `final_report`，此时 `output_node` 还没执行，当然是 `None`

**根因**：

**LangGraph 1.1.x 的 `interrupt_before` 中断不抛异常。** `app.invoke()` 在断点处静默返回当前 state（就像正常完成一样），不发出任何信号告诉你"我还没跑完"。`except Exception` 抓了个寂寞。

这与我们最初的假设相反。之前我们想当然地认为"中断 = 抛异常"，所以设计了 `try/except` 来捕获并处理。实际上 LangGraph 的中断机制是让 `invoke` 正常返回，然后通过 `app.get_state(config).next` 让调用方主动检查是否真的完成。

**正确做法**：

```python
# 1. 正常执行，不管是否中断都返回当前 state
result = app.invoke(initial_state, config)

# 2. 检查是否真的完成了（next 非空 = 还有节点待执行 = 中断了）
state_snapshot = app.get_state(config)
if state_snapshot.next:
    # 中断了，注入 human_feedback 后恢复
    print(">>> 暂停在 human_review 节点...")
    app.update_state(config, {"human_feedback": ""})
    result = app.invoke(None, config)

# 3. 现在 result 里 final_report 一定有值
```

**`app.get_state(config).next` 的含义**：

| `next` 值 | 含义 |
|-----------|------|
| `()` 空元组 | 工作流已完全结束，没有待执行节点 |
| `('human_review',)` | 中断在 `human_review` 前，该节点待执行 |
| 其他非空值 | 中断在其他位置 |

**详细解析**：

**第一层：`interrupt_before` 做了什么**

编译图时传了 `interrupt_before=["human_review"]`，LangGraph 在执行到 `human_review` 节点之前主动暂停。它不是崩溃、不是报错，而是把当前 state 写入 checkpointer（MemorySaver），然后让 `invoke()` 正常返回。所以 `invoke()` 不抛异常——对它来说"暂停"和"跑完"都是正常结束。

**第二层：`get_state(config).next` 怎么区分"暂停"和"跑完"**

`app.get_state(config)` 通过 `thread_id` 去 checkpointer 里查这个流程的快照（StateSnapshot），快照里有一个字段叫 `next`，记录的是还有哪些节点排队等着执行：

| `next` 值 | 含义 |
|-----------|------|
| `()` 空元组 | 所有节点都执行完了，没有排队 |
| `('human_review',)` | 有节点在排队 → 说明被 `interrupt_before` 拦住了 |

所以 `if state_snapshot.next` 等价于问："还有人在排队吗？"——有，就是中断了；没有，就是真跑完了。

**一句话总结**：`invoke()` 不告诉你"我暂停了"，它只把 state 写盘就下班。你得自己查 checkpointer 里的排队名单（`.next`），名单非空就说明流程被挂起了，需要注入 `human_feedback` 再 `invoke(None)` 继续跑。

**经验教训**：

1. **不要假设异常 = 中断。** LangGraph 的 `interrupt_before` 是静默暂停，`invoke` 正常返回当前 state。中断检测必须用 `app.get_state(config).next`。
2. **`interrupt_before` 和异常是完全不同的机制。** 前者是 LangGraph 设计的中断点，后者是代码执行错误。我们用 `except Exception` 去接中断点，根本对不上号。
3. 调试流程卡住时，优先 dump `result` 的完整 state，看哪些字段有值、哪些是 None。关键线索藏在 state 里。
4. `checkpointer=MemorySaver()` 让 `get_state(config)` 能通过 `thread_id` 找回中断的 state，没有 checkpointer 中断状态无法持久化。

---

## 问题 #3：LLM 返回 `"issues": null` 导致 `AttributeError: 'NoneType' object has no attribute 'issues'`

**日期**：2026-05-11

**错误信息**：
```
AttributeError: 'NoneType' object has no attribute 'issues'
During task with name 'critic_agent' and id 'b8100fee-6657-6995-6342-12a6664da40a'
```

**触发代码**（`src/graph/nodes.py` 第 69 行，`critic_agent` 内部）：

```python
for r in state['review_results']:
    for issue in r.issues:   # ← r.issues 是 None，遍历崩溃
        issues_text.append(...)
```

**根因分析**：

`ReviewResult` 的 `issues` 字段定义：

```python
class ReviewResult(BaseModel):
    issues: list[Issue] = Field(default_factory=list)
```

`default_factory=list` 的作用是：**当创建 ReviewResult 时未传 `issues` 字段，自动赋 `[]`。** 但这里的问题不是"不传"，而是 LLM 在 JSON 里写了 `"issues": null`。

```
Pydantic 的行为：
  不传 "issues"     → default_factory 生效 → issues = []      ✅
  传 "issues": null  → Pydantic 赋 None   → issues = None     ❌
  传 "issues": [...]  → Pydantic 赋列表    → issues = [...]    ✅
```

`null` 在 JSON 中是一个明确的值（等价于 Python 的 `None`），Pydantic 收到 `None` 后**跳过 default_factory**，直接赋值 `None`。`critic_agent` 遍历 `None` 就爆了 `AttributeError`。

**为什么 LLM 会返回 `null`**：

审查员可能认为代码没问题（no issues found），于是 JSON 输出：
```json
{"dimension": "performance", "issues": null}
```

LLM 的逻辑是"没有问题，所以省略列表"。但 Pydantic 把 `null` 当成合法值收下了。

**修复方案**：在 `ReviewResult` 模型层加 `field_validator`，任何输入（包括 `null`）强制转为空列表。

**修复代码**（`src/models.py`）：

```python
from pydantic import BaseModel, Field, field_validator

class ReviewResult(BaseModel):
    dimension: ReviewDimension
    issues: list[Issue] = Field(default_factory=list)

    @field_validator("issues", mode="before")
    @classmethod
    def default_issues_to_empty(cls, v: list | None) -> list:
        """LLM 返回 null 时自动转为空列表，防止下游遍历 None 爆 AttributeError"""
        return v if v is not None else []
```

- `mode="before"` — 在类型校验**之前**运行，拿到的是 LLM 返回的原始值（可能是 `None`）
- `v if v is not None else []` — 原始值有内容就直接用，是 `None` 就返回 `[]`
- `@classmethod` — `field_validator` 要求被装饰函数是类方法

**为什么改模型层而不是消费节点**：

| 方案 | 改动位置 | 影响范围 | 评价 |
|------|---------|---------|------|
| `critic_agent` 加 `if r.issues` 保护 | `nodes.py` | 仅 `critic_agent` | 治标，其他节点未来读 `issues` 也可能踩坑 |
| `field_validator` 在模型层拦截 | `models.py` | 所有下游节点 | 治本，数据进系统时已净化 |

**经验教训**：

1. **`default_factory=list` 不防 `null`。** 它的作用域是"字段缺失"，不是"字段为 null"。LLM 显式输出 `null` 会绕过 default_factory。
2. **不信任 LLM 的输出格式。** 即使 Pydantic 模型有默认值和类型声明，LLM 仍然可能返回不符合预期的值（`null` 代替空列表、数字写成字符串等）。关键字段加 `field_validator` 做防御。
3. **数据清洗放在模型层，不在业务节点。** 模型是数据入口，脏数据从这里拦下后所有下游节点都受益。
4. `field_validator` 的 `mode="before"` vs `mode="after"` 区别：`before` 在类型校验前运行，适合处理原始值转换；`after` 在校验后运行，适合对已确认类型的值做进一步约束。

---

## 问题 #4：LLM 返回枚举非法值（`"安全"`、`"资源管理"`）导致 `ValidationError` —— 系统性加固所有枚举字段

**日期**：2026-05-13

**错误信息**：
```
pydantic_core._pydantic_core.ValidationError: 2 validation errors for ReviewResult
issues.2.category
  Input should be '注入', '敏感信息', ... or '其他' [type=enum, input_value='安全', input_type=str]
issues.3.category
  Input should be '注入', '敏感信息', ... or '其他' [type=enum, input_value='资源管理', input_type=str]
During task with name 'style_reviewer'
```

**错误现象**：`style_reviewer`（风格审查员）节点中，LLM 返回的 Issue 中 `category` 写了 `"安全"` 和 `"资源管理"`。`"安全"` 是 ReviewDimension 的值（审查维度），`"资源管理"` 是我们枚举里根本没定义的词。Pydantic 校验时直接抛 `ValidationError`，整个流程炸停。

**从单一报错到系统性排查**：

这次报错引发了我们的警惕：**LLM 返回的不只在 `category` 这个字段会越界，所有枚举字段都可能被 LLM 自由发挥。**

梳理全系统 LLM 通过 `with_structured_output` 输出的所有枚举字段 —— 共 4 个模型、7 个枚举字段有风险：

| # | 模型 | 字段 | 枚举 | LLM 输出节点 |
|---|------|------|------|-------------|
| 1 | `Issue` | `severity` | `Severity`（4 种） | 三个审查员 |
| 2 | `Issue` | `category` | `IssueCategory`（17 种） | 三个审查员 |
| 3 | `ReviewResult` | `dimension` | `ReviewDimension`（3 种） | 三个审查员 |
| 4 | `ActionItem` | `severity` | `Severity`（4 种） | `critic_agent` |
| 5 | `ActionItem` | `category` | `IssueCategory`（17 种） | `critic_agent` |
| 6 | `ActionItem` | `dimension` | `ReviewDimension`（3 种） | `critic_agent` |
| 7 | `ReflectionResult` | `failure_type` | `FailureType`（4 种） | `reflect_node` |

**分类讨论：7 个字段分两类处理**

经过逐个分析，这 7 个字段的性质不同，不能一刀切全加 `field_validator + fallback`。

**第一类（5 个字段）**：可以用 fallback

LLM 对这些字段有合理的判断权，但可能写错。加 `field_validator`，非法值自动落到一个合理的默认值。

| 字段 | 枚举 | fallback | 理由 |
|------|------|----------|------|
| `Issue.severity` | `Severity` | `MEDIUM` | 猜不准取中间值 |
| `Issue.category` | `IssueCategory` | `OTHER`（"其他"） | 枚举自带 OTHER，"其他"语义通 |
| `ActionItem.severity` | `Severity` | `MEDIUM` | 同上 |
| `ActionItem.category` | `IssueCategory` | `OTHER`（"其他"） | 同上 |
| `ReflectionResult.failure_type` | `FailureType` | `LOGIC_ERROR` | 最常见的沙箱失败类型 |

**第二类（2 个字段）**：`dimension` 不应该让 LLM 填

`dimension` 表示"这个结果是哪个审查员产生的"。这个信息在当前节点是**确定已知的**——`security_reviewer` 的 dimension 一定是 `SECURITY`，不需要 LLM 来判断。

而且 `dimension` 枚举只有三个值（`SECURITY` / `PERFORMANCE` / `STYLE`），没有"其他"。如果 LLM 写错了，没有任何合法 fallback 可用。

更关键的是 `ActionItem.dimension`：它出现在 `critic_agent`（汇总节点）的输出中。critic 的工作是**去重合并**——如果 security 和 style 同时指出第 5 行有问题，合并后的 ActionItem 到底算 security 还是 style？在去重合并逻辑下，`dimension` 这个概念本身就模糊了。

进一步追踪发现，`coder_agent` 在展开 `action_plan` 时读的是 `priority`、`lineno`、`severity`、`category`、`fix_instruction`，**从未读取 `dimension`**。也就是说这个字段虽然定义了，但整个流程中没有下游节点消费它。critic 在生成 `fix_instruction` 时，关于维度/来源的信息已经融入了修复指令的措辞中。

**结论**：

| 字段 | 方案 | 原因 |
|------|------|------|
| `ReviewResult.dimension` | **节点硬覆盖**：审查员内直接 `result.dimension = ReviewDimension.SECURITY`（各自赋值） | 节点知自身身份，无需 LLM 猜，可信 |
| `ActionItem.dimension` | **直接删除** | 去重后语义模糊 + 无下游消费 + 无合法 fallback |

**最终改动清单**：

一、`models.py` — 5 个 `field_validator`：

```python
# Issue 模型
@field_validator("severity", mode="before")
@classmethod
def unknown_severity_fallback(cls, v):
    try:
        return Severity(v) if isinstance(v, str) else v
    except ValueError:
        return Severity.MEDIUM

@field_validator("category", mode="before")
@classmethod
def unknown_category_fallback(cls, v):
    try:
        return IssueCategory(v) if isinstance(v, str) else v
    except ValueError:
        return IssueCategory.OTHER

# ActionItem 模型 —— 同样两个 validator（代码结构一致）

# ReflectionResult 模型
@field_validator("failure_type", mode="before")
@classmethod
def unknown_failure_type_fallback(cls, v):
    try:
        return FailureType(v) if isinstance(v, str) else v
    except ValueError:
        return FailureType.LOGIC_ERROR
```

二、`models.py` — 删除 `ActionItem.dimension` 字段

三、`nodes.py` — 三个审查员各加一行硬赋值：

```python
# security_reviewer 内
result.dimension = ReviewDimension.SECURITY

# performance_reviewer 内
result.dimension = ReviewDimension.PERFORMANCE

# style_reviewer 内
result.dimension = ReviewDimension.STYLE
```

`ReviewResult.dimension` 已由节点硬赋值覆盖，不被 LLM 写入，不存在越界风险，**不需要 validator**。

**经验教训**：

1. **LLM 对枚举值的输出不可靠。** 即使 prompt 中暗示了枚举的取值范围，LLM 仍可能自由发挥（如本例中把 dimension 的值 `"安全"` 写到 category 里）。
2. **出现一个枚举报错时，系统性排查所有枚举字段。** 本次从 `Issue.category` 一个点出发，发现了 7 处潜在风险。只修报错那一处是治标，全量排查才是治本。
3. **不是所有枚举字段都适合加 fallback。** 像 `dimension` 这种"确定性已知"的信息，应该从源头硬赋值，不让 LLM 参与。加 fallback 是防守，让 LLM 不要填才是进攻。
4. **枚举越界和 `null`（问题 #3）同属"LLM 输出不可信"这一类问题。** 同一种防御方法（`field_validator(mode="before")`）可以解决，区别在于 null→空值的处理用 `or` 逻辑，枚举越界用 `try/except ValueError`。
5. **字段的价值要结合架构全局评估，不能只看注释。** `ActionItem.dimension` 注释说"coder 可据此调整修复侧重点"，但实际代码从未消费它，且 critic 去重后该字段语义天然模糊。这种"看起来有用、实际没用"的字段删掉比修更干净。
