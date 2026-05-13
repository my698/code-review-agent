# 开发问题记录

---

## 问题 #1：Python 包导入路径配置

**日期**：2026-05-04

**问题描述**：
项目中 `src/config.py` 需要被其他模块导入，但 `src/` 不在 Python 默认搜索路径中，导致 `ModuleNotFoundError`。核心矛盾：代码放在 `src/` 目录下，但 Python 不知道去那里找。

**尝试过的方案**：

1. **直接在项目根目录用 `from src.config import ...`**
   - 结果：失败
   - 原因：在项目根目录时碰巧当前工作目录在 `sys.path` 里所以能找到，换到 `/tmp` 等目录立刻报错。不可移植，治标不治本。

2. **在 `config.py` 内部自动把 `src/` 加入 `sys.path`**
   - 结果：失败
   - 原因：先有鸡还是先有蛋——`config.py` 还没被导入时里面的代码不会执行，`sys.path` 没被修改，所以连 `import config` 本身都失败。

3. **`pip install -e .`（第一版 `pyproject.toml`，build-backend 配置错误）**
   - 结果：安装失败
   - 原因：`build-backend` 写成了 `setuptools.backends._legacy:_Backend`，Python 3.12 + 新版 pip 不支持该旧版后端。

4. **`pip install -e .`（第二版 `pyproject.toml`，修复了 build-backend）+ `from src.config import ...`**
   - 结果：安装成功，但 `/tmp` 下导入仍失败
   - 原因：`.pth` 文件把 `src/` 目录本身加入了 `sys.path`，导入时应该直接写模块名 `from config import ...`，而非带 `src.` 前缀。导入写法错误。

5. **创建 `scripts/run.py` 入口脚本，手动 `sys.path.insert`**
   - 结果：成功（在项目根目录下运行 `python scripts/run.py` 可正常导入）
   - 性质：辅助开发手段，不是 Python 包的标准安装方式，但有效。

**最终解决方案**（双轨制，互不冲突）：

- **方案 A（标准做法）**：`pip install -e .` 可编辑安装
  - `pyproject.toml` 使用 `build-backend = "setuptools.build_meta"`
  - `[tool.setuptools.package-dir]` 设置 `"" = "src"`
  - `src/` 及其所有子目录（`agents/`、`graph/`、`tools/`）都需要 `__init__.py`（空文件即可）
  - 安装后 `.pth` 文件自动把 `src/` 加入 `sys.path`
  - 导入写法统一为 `from config import ...`（不带 `src.` 前缀）

- **方案 B（开发辅助）**：`scripts/run.py` 入口脚本
  - 脚本开头手动 `sys.path.insert(0, SRC_DIR)`
  - 导入写法同样是 `from config import ...`
  - 适用于快速开发调试

- **两套机制互不冲突，导入写法完全统一。**

**经验教训**：
1. `pip install -e .` 是 Python 项目的标准可编辑安装方式，配置一次，终生受益。
2. `.pth` 文件的作用是把指定目录加入 `sys.path`，安装后导入时不需要带父目录前缀。
3. 所有子包目录都需要 `__init__.py`（即使是空文件），否则 setuptools 不会把它们打包进去。
4. `pyproject.toml` 中 `build-backend` 要用主流写法 `setuptools.build_meta`，避免兼容性问题。
5. 遇到导入问题时，先查 `sys.path` 内容（`python -c "import sys; print(sys.path)"`）和 `.pth` 文件内容，再定位原因。

---

## 问题 #2：流式输出方案选择——`stream_mode="updates"` vs `astream_events`

**日期**：2026-05-05

**问题描述**：
设计文档 `graph-design.md` 最初使用 `graph.stream(stream_mode="updates")` 做流式输出。
该模式只能拿到节点**完成后的输出**，前端无法区分"节点开始执行""LLM 正在生成 token""节点执行完成"等中间状态，
颗粒度太粗。

**方案对比**：

| | `stream_mode="updates"` | `astream_events` |
|---|---|---|
| 颗粒度 | 仅节点完成后一个事件 | 节点开始/token生成/节点结束等多个事件 |
| 前端展示 | "正在审查..." → 突然完成 | "安全审查中..." → token逐字输出 → "审查完成" |
| LLM token 实时流 | 不支持 | 支持（`on_chat_model_stream`） |
| 实现复杂度 | 低 | 中（需按 `kind` 过滤事件） |
| 节点代码改动 | 无 | 无（只改图执行层） |
| 是否原生支持 | 是 | 是（LangGraph 自带） |

**最终决策**：采用 `astream_events(version="v2")`

**依据**：
1. 需求文档明确要求"实时看到三个 Agent 并行审查进度（流式输出）"，`astream_events` 才能真正实现
2. 节点函数内部不用任何改动，只在图调用层切换
3. LangGraph 原生支持，不引入额外依赖
4. `version="v2"` 是 LangGraph 1.1.x 的标准事件模式

**经验教训**：
1. 流式输出选型应在设计阶段而非实现阶段做，避免后期返工
2. `stream_mode="updates"` 适合简单场景（只需知道节点何时完成），`astream_events` 适合需要细粒度进度展示的场景
3. `astream_events` 产生的事件量远多于 `stream_mode="updates"`，消费端必须按 `kind` 严格过滤，不能全量推给前端
4. 三个审查节点并行执行时通过 `name` 字段区分来源，前端可分别展示各节点的实时状态

---

## 问题 #3：`pip install -e .` 成功但 `import langgraph` 仍报 ModuleNotFoundError

**日期**：2026-05-11

**问题描述**：
执行 `pip install -e .` 成功，但运行 `python -c "from graph.builder import build_graph; build_graph()"` 时报错 `ModuleNotFoundError: No module named 'langgraph'`。同级路径的 `graph` 包能找到，但第三方依赖找不到。

**排查过程**：

1. **初步怀疑路径问题**：项目中导入使用裸模块名（`from graph.state import ...`），怀疑 `src/` 没加入 `sys.path`。回头查 `docs/dev-issues.md` 问题 #1 已有解决方案——`pip install -e .` + `pyproject.toml` 中 `package-dir = { "" = "src" }`。
2. **排除路径问题**：`graph` 包能导入成功（`from graph.builder import build_graph` 未报路径错误），说明 `src/` 已在 `sys.path` 中，路径没问题。
3. **定位根因**：查看 `pyproject.toml`，发现 `[project]` 下**缺少 `dependencies` 列表**。`pip install -e .` 只注册了包路径（`.pth` 文件），但不会安装任何第三方依赖。

**根因**：

`pyproject.toml` 中 `[project]` 节点只声明了 `name`、`version`、`requires-python`，没有 `dependencies` 字段。`pip install -e .` 按声明做可编辑安装，缺少的依赖不装。

**解决方案**：

在 `pyproject.toml` 的 `[project]` 下补上 `dependencies` 列表：

```toml
[project]
dependencies = [
    "langgraph>=1.1,<2.0",
    "langchain>=1.2,<2.0",
    "langchain-deepseek>=1.0,<2.0",
    "pydantic>=2.0",
]
```

然后重新执行 `pip install -e .`，pip 自动安装全部依赖，问题解决。

**经验教训**：
1. `pip install -e .` 只安装 `pyproject.toml` 中声明的依赖，未声明的不装。`.pth` 路径入口和依赖安装是两件独立的事。
2. 遇到 `ModuleNotFoundError` 时先区分：是**项目模块找不到**（路径问题）还是**第三方包找不到**（依赖声明问题），两类根因完全不同。
3. 新项目首次配置时容易遗漏 `dependencies`，建议 `pip install -e .` 后立即 `pip list | grep langgraph` 验证核心依赖已装。

---

## 问题 #4：`python -c` 验证导入与 `python scripts/run.py` 的差异

**日期**：2026-05-11

**问题描述**：
在未执行 `pip install -e .` 的情况下，用 `python -c "from graph.builder import ..."` 验证模块导入，报 `ModuleNotFoundError`。但运行 `python scripts/run.py` 却能正常导入。

**原因**：

`python -c` 直接执行代码，不经过 `scripts/run.py` 中的 `sys.path.insert(0, SRC_DIR)`，因此 `src/` 不在 `sys.path` 中。而 `python scripts/run.py` 会先执行脚本顶部的路径拼接代码，`src/` 被正确加入 `sys.path`。

**结论**：

两种验证方式各有前提：

| 验证方式 | 前提条件 | 适用场景 |
|----------|---------|---------|
| `python -c "from graph.builder import ..."` | 需先 `pip install -e .`（`.pth` 自动加路径） | 快速验证包是否可导入 |
| `python scripts/run.py` | 不需要 `pip install -e .`（脚本自己加路径） | 完整运行入口 |

**经验教训**：
1. 用 `python -c` 做冒烟测试前，确认 `pip install -e .` 已执行且依赖完整。
2. 给用户建议验证命令时，先确认用户当前环境状态（是否已 `pip install -e .`），再给对应命令。
3. `scripts/run.py` 是最可靠的验证入口，它有独立的路径处理逻辑，不依赖 `.pth`。

---

## 问题 #5：Send 第二个参数导致分支 state 缺少字段 —— `KeyError: 'original_code'`

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

## 问题 #6：HITL `interrupt_before` 中断不抛异常 —— 流程静默走完但报告未生成

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

#详细解析
 ---                                                                   
  第一层：interrupt_before 做了什么
                                                                        
  编译图时传了 interrupt_before=["human_review"]，LangGraph 在执行到
  human_review 节点之前主动暂停。它不是崩溃、不是报错，而是把当前 state 
  写入 checkpointer（MemorySaver），然后让 invoke() 正常返回。所以
  invoke() 不抛异常——对它来说"暂停"和"跑完"都是正常结束。               
                  
  ---                                                                   
  第二层：get_state(config).next 怎么区分"暂停"和"跑完"
                                                                        
  app.get_state(config) 通过 thread_id 去 checkpointer
  里查这个流程的快照（StateSnapshot），快照里有一个字段叫               
  next，记录的是还有哪些节点排队等着执行：
                                                                        
  ┌───────────────────┬──────────────────────────────────────────────┐  
  │      next 值      │                     含义                     │
  ├───────────────────┼──────────────────────────────────────────────┤  
  │ () 空元组         │ 所有节点都执行完了，没有排队                 │
  ├───────────────────┼──────────────────────────────────────────────┤  
  │ ('human_review',) │ 有节点在排队 → 说明被 interrupt_before       │  
  │                   │ 拦住了                                       │  
  └───────────────────┴──────────────────────────────────────────────┘  
                                                                        
  所以 if state_snapshot.next                                           
  等价于问："还有人在排队吗？"——有，就是中断了；没有，就是真跑完了。
                                                                        
  ---             
  一句话总结：invoke() 不告诉你"我暂停了"，它只把 state
  写盘就下班。你得自己查 checkpointer                                   
  里的排队名单（.next），名单非空就说明流程被挂起了，需要注入
  human_feedback 再 invoke(None) 继续跑。

**经验教训**：

1. **不要假设异常 = 中断。** LangGraph 的 `interrupt_before` 是静默暂停，`invoke` 正常返回当前 state。中断检测必须用 `app.get_state(config).next`。
2. **`interrupt_before` 和异常是完全不同的机制。** 前者是 LangGraph 设计的中断点，后者是代码执行错误。我们用 `except Exception` 去接中断点，根本对不上号。
3. 调试流程卡住时，优先 dump `result` 的完整 state，看哪些字段有值、哪些是 None。关键线索藏在 state 里。
4. `checkpointer=MemorySaver()` 让 `get_state(config)` 能通过 `thread_id` 找回中断的 state，没有 checkpointer 中断状态无法持久化。

---

## 问题 #7：LLM 返回 `"issues": null` 导致 `AttributeError: 'NoneType' object has no attribute 'issues'`

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