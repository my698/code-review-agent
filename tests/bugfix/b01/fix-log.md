# B01: coder 越界重构 — 修复日志

## 问题描述

coder_agent 在执行 fix_instruction 时私自添加额外改动：改名、改签名、加功能、乱加 import、加注释。8/8 测试全部出现，最严重时把干净代码修到完全失败（测试 #5）。

---

## 最初修复方案（2026-05-14）

### 入手点分析

越界重构的根源在两条链路：

1. **critic_agent（上游）** — fix_instruction 写得宽泛，"可执行"没有细度约束，给了 coder 自由发挥的空间
2. **coder_agent（下游）** — SystemMessage 缺少硬边界，对"不要重构"没有具体的禁令清单

修改文件：`src/graph/nodes.py`

### 修改一、critic_agent SystemMessage — 收紧 fix_instruction 粒度

**为什么要改**：当前第 4 条只写了一句话"每条生成可执行的 fix_instruction"。critic 不知道"可执行"意味着什么粒度。critic 生成的 fix_instruction 往往是自然语言描述，比如"修复 SQL 注入漏洞，使用参数化查询"。这种模糊指令到了 coder 手里，coder 为了"执行"就开始自由发挥。

**怎么改**：把"可执行"拆成 4 条硬性格式要求：必须有行号 + FROM→TO + 禁用模糊词 + 超 3 行标注跳过。

**修改前**

```python
        SystemMessage(content =(
            "你是代码审查主管。请对以下问题清单：\n"
            "1. 去重：多条指向同一行号+同类问题的合并为一条\n"
            "2. 排序：按严重度(CRITICAL > HIGH > MEDIUM > LOW)优先，同级按行号\n"
            "3. 评分：根据问题数量和严重度打分(0-100)\n"
            "4. 每条生成可执行的 fix_instruction"
        )),
```

**修改后**

```python
        SystemMessage(content =(
            "你是代码审查主管。请对以下问题清单：\n"
            "1. 去重：多条指向同一行号+同类问题的合并为一条\n"
            "2. 排序：按严重度(CRITICAL > HIGH > MEDIUM > LOW)优先，同级按行号\n"
            "3. 评分：根据问题数量和严重度打分(0-100)\n"
            "4. fix_instruction 格式要求：\n"
            "   - 必须包含：目标行号 + 具体改动（FROM → TO）\n"
            "   - 示例：第 10 行：将 cursor.execute(sql) 改为 cursor.execute(sql, (user_input,))\n"
            "   - 只描述操作，不写\"建议\"\"考虑\"\"可改为\"等模糊词\n"
            "   - 如果修复需要改动 3 行以上，在 description 中标注 [建议跳过]"
        )),
```

### 修改二、coder_agent SystemMessage — 加 5 条硬边界禁令

**为什么要改**：旧 prompt 的 5 条规则里只有第 1 条"不要重构其他部分"涉及边界，但"重构"这个词太抽象——LLM 不知道改名算不算重构、加类型注解算不算重构。需要把抽象禁令翻译成 LLM 能逐条对照执行的具体条目。

**设计思路**：
- "禁止改名"直接对应用户最痛的改名问题（测试 #1, #2, #6）
- "禁止加功能"封死加权限校验/脱敏/Enum 的路（测试 #1, #3）
- "禁止加 import"杜绝 `import logging` 写分支里的坏习惯（测试 #2）
- 优先级链 `human_feedback > reflection_notes > fix_instruction` 保留重试流程的纠偏能力

**修改前**

```python
        SystemMessage(content=(
            "你是 Python 代码修复专家。请按以下规则修改代码：\n"
            "1. 严格按照 fix_instruction 逐一修改，不要重构其他部分\n"
            "2. 保持原代码的缩进风格和整体结构\n"
            "3. 不要在修复代码周围添加注释标记（如 # FIXED）\n"
            "4. 修改后代码必须是可直接运行的合法 Python 代码\n"
            "5. 如果有 reflection_notes 或 human_feedback，优先参考其意见\n"
        )),
```

**修改后（初版，5 条禁令）**

```python
        SystemMessage(content=(
            "你是 Python 代码修复专家。\n\n"
            "核心原则：最小改动 —— 只修改 fix_instruction 指定的问题行，其余代码一字不改。\n\n"
            "硬约束（违反即修复无效）：\n"
            "1. 禁止改名 —— 函数名、类名、变量名、参数名一律不动\n"
            "2. 禁止改签名 —— 不增删参数、不改返回类型、不加类型注解\n"
            "3. 禁止加功能 —— fix_instruction 没说到的逻辑一律不添加\n"
            "4. 禁止加 import —— 不新增任何 import 语句\n"
            "5. 禁止加注释 —— 不写 # FIXED / # TODO / 解释性注释\n\n"
            "判断规则：\n"
            "- fix_instruction 标注 [建议跳过] 的项 → 跳过，原样保留\n"
            "- LOW / MEDIUM 级别问题需要新增代码逻辑 → 跳过\n"
            "- 改动超过 3 行 → 跳过，不修\n"
            "- 参考优先级：human_feedback > reflection_notes > fix_instruction\n"
            "- 修改后代码必须是可直接运行的合法 Python 代码"
        )),
```

### 预期效果

- coder 不再改名、加功能、乱加 import
- 对 LOW/MEDIUM 问题不再过度修复
- 改动超 3 行的问题直接跳过

---

## 测试概况

在修复过程中共进行 5 轮测试，覆盖 3 类样本：

| 轮次 | 测试脚本 | 样本类型 | 目的 |
|------|---------|---------|------|
| R1 | `test_b01_coder_overfix.py` | SQL 注入 | 验证初步方案（5 条禁令）是否生效 |
| R2 | 同上 | SQL 注入 | 验证追加的第 6 条禁令（作用域） |
| R3 | `test_b01_02_perf_duplicate.py` | O(n²) 性能 + 冗余查找 | 跨样本验证禁令稳定性 |
| R4 | `test_b01_04_mixed.py` | 硬编码密钥 + 裸 except + 资源泄露 | 验证修复需求与禁令共存场景 |
| R5 | `test_b01_05_clean.py` | 干净代码 | 验证无问题代码的流程稳定性 |

---

## 已修复项汇总

6 项越界检测在全部 5 轮测试中的通过情况：

| 检测项 | R1 | R2 | R3 | R4 | R5 |
|--------|:--:|:--:|:--:|:--:|:--:|
| 重命名函数 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 新增函数 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 参数变更 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 新增 import | ✅ | ✅ | ✅ | ✅ | ✅ |
| 行数膨胀 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 作用域变更 | — | ✅ | ✅ | ✅ | ✅ |

> R1 时作用域变更尚未加入检测项；R4 状态为 `partial`（含 `[需人工]` 建议），越界检测本身全部通过。




---

## 新暴露的问题

### 新问题 #1：作用域变更（5 条禁令覆盖不全）

**来源**：R1 测试暴露。初步方案的 5 条禁令生效后，检测脚本发现修复后的代码出现了新的越界形式——作用域变更。

**现象**：

```python
# 修复后：连接被提到模块级别
connection = sqlite3.connect("users.db")  # 导入时即执行

def get_user(user_input):
    cursor = connection.cursor()
    ...
```

**分析**：把局部变量提升为全局，不在 5 条禁令范围内——不是改名、不是加功能、不是加 import、不是改签名、不是加注释。但行为语义确实变了：原来在调用函数时报错，变成 import 时即报错；多次调用会共享同一个连接。

**修复**：coder_agent SystemMessage 硬约束新增第 6 条：

> 6. 禁止改作用域 —— 不得把局部变量提升为全局、或把全局降为局部

**验证**：R2 重新运行同一样本，`conn` 保留在函数内部，第 6 条禁令生效。后续 R3-R5 均通过。

---

### 新问题 #2：禁令与修复需求冲突 → [需人工] 机制

**来源**：R4 测试暴露。测试样本包含硬编码密钥（CRITICAL），修复需要 `import os` + `os.getenv()`，但禁令第 4 条禁止一切 import，禁令和修复需求发生冲突。

**现象**：

```python
import os            # 🔴 违反禁令第4条
import logging       # 🔴 违反禁令第4条

def fetch_user_data(user_id):
    if not user_id.isdigit():        # 🔴 违反禁令第3条（新增校验）
        raise ValueError("...")
    api_key = os.getenv("API_KEY")   # 把硬编码密钥改为环境变量
    ...
```

**分析**：

直接原因——coder 面临两难：要修 CRITICAL 漏洞就必须 `os.getenv()`，要 `os.getenv()` 就必须 `import os`；禁令禁止一切 import，但 coder 选择了"违禁令也要修漏洞"。

深层问题——在孤立合成代码中，`os.getenv("API_KEY")` 修完代码反而不可运行：没有 `.env` 文件，没有 `load_dotenv()`，`os.getenv()` 返回 `None`，`Authorization: f"Bearer None"` 静默失败。sandbox 只验语法，检测不到这个问题（B05 假通过）。

**结论：这类"改单个文件修不了"的问题，根本不应该自动修。应该跳过并给人工建议。**

**修复**：引入 `[需人工]` 机制，改动 4 个文件。

#### 一、`src/models.py` — 新增 `skipped_items` 字段 + status `partial`

```python
# CoderResult — 新增字段
skipped_items: list[str] = Field(default_factory=list)

# FinalReport — 新增字段 + status 取值扩展
status: str = "running"                                 # running / success / partial / failed
skipped_items: list[str] = Field(default_factory=list)
```

#### 二、`src/graph/nodes.py` — critic_agent

SystemMessage 新增第 5 条规则：

```python
"5. [需人工] 标注规则（满足任一条件即标注）：\n"
"   - 修复需要新建文件（.env / config.py / Makefile 等）→ 必须标注 [需人工]\n"
"   - 修复需要安装新依赖包（pip install xxx）→ 必须标注 [需人工]\n"
"   - 修复需要改动当前文件以外的代码 → 必须标注 [需人工]\n"
"   - 修复依赖项目基础设施（环境变量注入、密钥管理系统、数据库连接池等）→ 必须标注 [需人工]\n"
"   - [需人工] 条目的 fix_instruction 不写 FROM→TO，改为描述：问题是什么 + 人工需要建立什么基础设施 + 建立后代码可以怎么改\n"
"   - 示例 fix_instruction：'第33行 api_key 为硬编码密钥。需人工建立 .env 文件存放 API_KEY，并在项目入口加 load_dotenv()。完成后将 api_key = \"sk-xxx\" 改为 api_key = os.getenv(\"API_KEY\")'"
```

#### 三、`src/graph/nodes.py` — coder_agent

SystemMessage 判断规则首条：

```python
"- fix_instruction 标注 [需人工] → 必须跳过该条，不修，将该条内容原样写入 skipped_items 列表\n"
```

HumanMessage 追加提示：

```python
+ "\n\n注意：含 [需人工] 标记的条目，跳过修改，将其内容原样放入 skipped_items 列表。"
```

#### 四、`src/graph/nodes.py` — output_node

状态判定从二元变为三元：

```python
if not sandbox_passed:
    status = "failed"
elif skipped:
    status = "partial"      # 新增：沙箱通过但有人工建议
else:
    status = "success"
```

`FinalReport` 组装时透传 `skipped_items`。

**验证**：R4 测试输出 — 硬编码密钥被标记 `[需人工]`，coder 正确跳过，代码一字未改，零越界，status=`partial`。不再出现 `import os` 和 `import logging`。

**遗留问题**：裸 `except:` 和 `open/close` 资源泄露此次也未修复。需后续确认 critic 是将它们合并到了 `[需人工]` 条目中，还是给了独立的 fix_instruction。如果是前者，可自动修的问题不应和需人工的问题绑在一起。

---

### 新问题 #3：ActionItem.fix_instruction 缺失导致 ValidationError 崩溃

**来源**：R5 测试暴露。此问题在验证 `[需人工]` 机制（新问题 #2）的测试过程中连带发现。

**现象**：`test_b01_05_clean.py` 在 critic_agent 节点崩溃：

```
pydantic_core._pydantic_core.ValidationError: 1 validation error for CriticSummary
action_plan.6.fix_instruction
  Field required [type=missing, ...]
```

**分析**：LLM 偶发性漏字段，第 6 个 `ActionItem` 缺少 `fix_instruction`。Pydantic 的 `fix_instruction: str`（无默认值）要求该字段必须存在，直接抛 ValidationError。

**修复**：`src/models.py` — ActionItem 新增 field_validator

```python
class ActionItem(BaseModel):
    ...
    fix_instruction: str

    @field_validator("fix_instruction", mode="before")
    @classmethod
    def missing_fix_instruction_fallback(cls, v):
        return v if v is not None else ""
```

与已有 validator 的对比：

| 字段 | 兜底策略 | 原因 |
|------|---------|------|
| `severity` | 非法枚举值 → `MEDIUM` | LLM 编了一个不在枚举里的值 |
| `category` | 非法枚举值 → `OTHER` | 同上 |
| `fix_instruction` | `None` → `""` | LLM 压根没输出这个 key，字段缺失 |

前两者是"值不合法"，后者是"字段缺失"，防御手段不同。

**验证**：重新运行 R5 不再崩溃。

---

### 新问题 #4：[建议跳过] 语义矛盾 + minor 建议静默丢弃

**来源**：代码审查发现。此问题并非测试暴露，而是回顾 fix-log 和 prompt 设计时发现的语义缺陷。

**分析**：

1. critic 和 coder 对 `[建议跳过]` 的理解矛盾——critic 说"建议"，给了 LLM 选择空间；coder 规则却写"→ 跳过"（硬性跳过）
2. `[建议跳过]` 的 minor 优化建议被静默丢弃——只跳过不记录，用户完全看不到

**修复**（4 处）：

| 位置 | 改前 | 改后 |
|------|------|------|
| critic_agent 规则 4 | `[建议跳过]` | `[跳过]` |
| coder_agent 判断规则 | `[需人工]` 和 `[建议跳过]` 分两条 | 合并为 `[需人工] 或 [跳过] → 写入 skipped_items` |
| coder HumanMessage | 只提 `[需人工]` | 加 `或 [跳过]` |
| output_node 注释 | 只写"需人工" | 反映两类跳过项 |

最终标记语义：

| 标记 | 触发条件 | coder 行为 | 用户看到 |
|------|---------|-----------|---------|
| `[需人工]` | 需要新建文件/安装依赖/改其他文件/依赖基础设施 | 跳过，写入 skipped_items | 需要手动建立基础设施 |
| `[跳过]` | 改动 >3 行 / minor 问题 | 跳过，写入 skipped_items | 优化建议供参考 |

---

## 新问题 #5 — 2026-05-15：critic 分类体系重构（原则判定替代清单枚举）

### 问题发现

R4 测试（test_b01_04_mixed.py）重新运行后，skipped_items 从之前只有 1 条膨胀到 10 条。原因是 `[跳过]` 标签改为也写入 skipped_items 之后，之前被静默丢弃的 trivial 建议全部浮出水面：docstring、类型注解、session 复用、路径权限、等价写法建议等。

同时发现修复后代码仍有越界：

```python
import os                    # 🔴 禁令4：禁止加 import
    """Fetch user data from external API."""  # 🔴 禁令5：禁止加注释
    if not str(user_id).isdigit():           # 🔴 禁令3：禁止加功能
        raise ValueError("Invalid user ID")
```

越界检测只抓到 `import os`，docstring 和输入校验漏网——检测脚本缺少对应检查项。

### 入手点分析

**表面原因**：critic 把 reviewer 输出的所有 LOW/MEDIUM 噪音全部转成了 action_item（docstring、类型注解、等价写法等），coder 看到有明确 fix_instruction 的条目照常执行。`[需人工]` 那条的 api_key 没改，说明 coder 的标签识别是生效的——问题出在上游 critic 没有做足够的筛选。

**深层原因**：当前跳过体系存在两个设计缺陷：

1. **跳过定义混乱** — critic 的 `[跳过]` 标签（仅触发于改动 > 3 行）和 coder 的静默跳过规则（`LOW/MEDIUM + 新增逻辑 → 跳过`）是两个独立体系，共享"跳过"这个名字但行为完全不同。LLM 容易混淆，也给调试带来困惑。

2. **没有丢弃机制** — 整个链路里 critic 是唯一可以做筛选的节点，但 prompt 里从未告诉它"有些问题不需要生成 action_item"。reviewer 输出什么，critic 就原样转发。docstring、类型注解这些纯风格建议，应该直接在 critic 层丢弃。

### 方案讨论

用户最初提出"把所有可能的丢弃/跳过类型枚举写进 prompt"，经过评估否定了这个方向：

- LLM 不擅长逐条对清单，清单越长，注意力越稀释，漏判率越高
- 清单永远列不完，遇到清单外的情况 LLM 无法判断
- 之前 "fix_instruction 缺失" 的 ValidationError 就是 prompt 过长 LLM 漏字段的后果

最终采用**原则判定替代清单枚举**：

- critic 只回答一个原则性问题：**"这个 issue 是否影响代码的正确性或安全性？"**
- 不影响（docstring、类型注解、命名风格、等价写法、代码组织等）→ 直接丢弃
- 影响 → 按 `[需人工]`/`[跳过]`/修复三分类

### 改动范围

#### 一、critic_agent SystemMessage — 四分类判定

旧规则 4+5 合并为一个判定流程：

```
4. 对去重后的每条问题做判定：

   第一步：该问题是否影响代码的正确性或安全性？
   如果否 → 丢弃，不生成 action_item。
   （纯风格、命名偏好、docstring/类型注解/注释缺失、等价写法建议、
   代码组织建议等，只要不影响正确性和安全性，一律丢弃）

   如果是 → 按以下三类处理：

   [需人工] — 修复依赖当前文件之外的条件（满足任一即标注）：
   · 需要新建文件（.env / config.py 等）
   · 需要安装新依赖包
   · 需要改动当前文件以外的代码
   · 依赖项目基础设施（环境变量、密钥管理、数据库等）
   fix_instruction 描述：问题 + 所需基础设施 + 建立后怎么改

   [跳过] — 问题真实，但自动修复风险高于收益（满足任一即标注）：
   · 修复涉及 3 行以上代码变更
   · 修复会改变函数签名/类接口
   · 修复涉及核心算法/状态机/并发逻辑
   fix_instruction 描述问题 + 建议修复方向

   修复 — 不属于上述两类：
   · fix_instruction 必须包含行号 + FROM → TO
   · 禁用"建议""考虑""可改为"等模糊词
```

关键设计：丢弃/`[跳过]` 的边界看问题是否真实。docstring 缺失不是 bug，丢弃。变量名确实会误导但改名面太广，打 `[跳过]`。

#### 二、coder_agent SystemMessage — 删除隐藏规则

删掉了两条 coder 侧独立判断：
- `LOW / MEDIUM 级别问题需要新增代码逻辑 → 跳过` ❌
- `改动超过 3 行 → 跳过，不修` ❌

新规则只有两条：
```
- fix_instruction 含 [需人工] 或 [跳过] → 跳过该条，写入 skipped_items
- fix_instruction 无标签 → 逐一修复
```

**设计意图**：critic 是唯一决策点，coder 不再做任何自主判断，只看标签执行。

### 实际测试结果

重新运行 `test_b01_04_mixed.py`，6 项越界检测全部通过，skipped_items 从上一轮的 10 条降到 3 条，`import os` 不再出现。测试通过。

### 对比分析：上次越界 (`import os`) 本次为何未出现

上次 R4 输出中 `import os` 越界，本次没有。根本原因不是 coder 更听话了，而是 **critic 的分类改善了**：
- 两个需要 `os` 的条目（硬编码密钥、路径校验）都被正确打上 `[需人工]` 标签
- coder 看到标签直接跳过，根本不执行这些条目
- coder 实际修复的三条（异常、资源、字符串）全都不需要 `import os`

所以 `import os` 没有"出现的机会"。这是 critic 筛选 + coder 标签执行机制的结构性效果，而非 prompt 禁令的单方面约束。

-----

## 新问题 #6 — 2026-05-16：禁令定位不清 → "无标签逐一修复"架空 6 条禁令

### 问题发现

#5 完成后重新跑全部 5 个测试，03 和 05 仍未通过：

- **03**：`GetData` 仍被重命名为 `get_data`（重命名函数 ❌ + 新增函数 ❌）
- **05**：干净代码被加 `isinstance` 类型校验 + `raise TypeError`，行数膨胀 1.7x（行数膨胀 ❌）

### 入手点分析

两次失败都是同一模式：critic 没有对风格/类型注解类 issue 打标签，生成无标签 fix_instruction → coder 看到"无标签 → 逐一修复" → 照做。

深层原因不仅是 critic 误判， coder 禁令也存在问题：**禁令体系存在定位混乱**：

1. coder 的 6 条禁令设计初衷是防 coder "自发多做"——修 SQL 注入时顺手改名、顺手加 import
2. 但后续又要求："无标签 → 逐一修复" 是一个**无条件执行指令**，LLM 在执行指令压力下会把禁令当背景噪音
3. 结果：当 critic 误判（漏标）一条需要改名的 fix_instruction 时，禁令 #1（禁止改名）被"逐一修复"覆盖，形同虚设

也就是说，**禁令和修复指令之间存在天然的优先级倒挂**——具体修复指令永远压倒抽象禁令。03 和 05 的失败不是因为 coder 故意违禁，而是在"逐一修复"的指令压力下，禁令被当成了可忽略的背景。

### 方案讨论

两个方向：

**方案一：禁令单纯防手痒，不防 critic 误判。**

这等于接受"critic 误判的 X% 概率导致越界"。但 critic 是 LLM，不是确定性逻辑，其误判不可归零（见新问题 #5 末尾的可靠性讨论）。方案一等于放弃防御。

**方案二：硬禁令做二道防线，其余由强力兜底覆盖。**

关键洞察：禁令的误杀代价不同。改名、改签名、改作用域 → **零误杀**，改了就一定破坏外部合约。而加功能/加 import/加注释 → 语义已被"最小改动" + "绝对不能做要求之外的改动"全覆盖，单独列举只会重复甚至矛盾。

所以只保留硬禁令（3 条）作为二道防线，防线之外的防手痒由核心原则和强力兜底语气覆盖。

采用方案二。

### 改动范围

只改动 `src/graph/nodes.py` — coder_agent：

**一、禁令拆为硬/软两层**

```
硬禁令（绝对禁止，fix_instruction 要求也不行）：
  1. 禁止改名 —— 函数名、类名、变量名、参数名一律不动
  2. 禁止改签名 —— 不增删参数、不改返回类型
  3. 禁止改作用域 —— 不得把局部变量提升为全局、或把全局降为局部

**二、判断规则中硬禁令作为显式拦截点**

```
- fix_instruction 含 [需人工] 或 [跳过] → 跳过，写入 skipped_items
- fix_instruction 无标签 → 先过硬禁令检查：
  · 违反硬禁令（需改名/改签名/改作用域）→ 跳过该条，静默丢弃
  · 未违反硬禁令 → 严格按 fix_instruction 逐一修复（硬禁令拦截对用户无操作价值，不写入 skipped_items）
- 参考优先级：human_feedback > reflection_notes > fix_instruction
```

**三、强力兜底**

```
你绝对不能做任何 fix_instruction 要求之外的改动。一个字都不要多改。
```

### 防御体系最终结构

| 层 | 组件 | 拦截内容 | 可靠度 |
|----|------|---------|:--:|
| 第一道 | critic 四分类判定 | 纯风格/噪音 → 丢弃；需人工/高风 | 原则性，依赖 LLM |
| 第二道 | coder 硬禁令 | 改名/改签名/改作用域（含 critic | 结构性，行为分叉 |
| 第三道 | coder 强力兜底 | 自发加功能/加 import/加注释 | 语气性，LLM 遵从 |
| 兜底 | Pydantic field_va | LLM 输出格式异常 | 100%（代码逻辑） |

### 实际测试结果

5 个测试全部通过，03 的 `GetData` 不再被改名，05 的行数膨胀消失。

-----

## 最终修复方案

### 一、critic_agent 最终 SystemMessage（`nodes.py`）

```python
        SystemMessage(content =(
            "你是代码审查主管。请对以下问题清单：\n"
            "1. 去重：多条指向同一行号+同类问题的合并为一条\n"
            "2. 排序：按严重度(CRITICAL > HIGH > MEDIUM > LOW)优先，同级按行号\n"
            "3. 评分：根据问题数量和严重度打分(0-100)\n"
            "4. 对去重后的每条问题做判定：\n"
            "\n"
            "   第一步：该问题是否影响代码的正确性？\n"
            "   如果否 → 丢弃，不生成 action_item。\n"
            "   （纯风格、命名偏好、docstring/类型注解/注释缺失、等价写法建议、\n"
            "   代码组织建议等，只要不影响正确性，一律丢弃）\n"
            "\n"
            "   如果是 → 按以下三类处理：\n"
            "\n"
            "   [需人工] — 修复依赖当前文件之外的条件（满足任一即标注）：\n"
            "   · 需要新建文件（.env / config.py 等）\n"
            "   · 需要安装新依赖包\n"
            "   · 需要改动当前文件以外的代码\n"
            "   · 依赖项目基础设施（环境变量、密钥管理、数据库等）\n"
            "   fix_instruction 描述：问题 + 所需基础设施 + 建立后怎么改\n"
            "\n"
            "   [跳过] — 问题真实，但自动修复风险高于收益（满足任一即标注）：\n"
            "   · 修复涉及 3 行以上代码变更\n"
            "   · 修复会改变函数签名/类接口\n"
            "   · 修复涉及核心算法/状态机/并发逻辑\n"
            "   fix_instruction 描述问题 + 建议修复方向\n"
            "\n"
            "   修复 — 不属于上述两类：\n"
            "   · fix_instruction 必须包含行号 + FROM → TO\n"
            "   · 禁用\"建议\"\"考虑\"\"可改为\"等模糊词"
        )),
```

### 二、coder_agent 最终 SystemMessage（`nodes.py`）

```python
        # 硬禁令（#1-#3）：拦截一切来源，包括 critic 误判，零误杀
        # 兜底："核心原则"+"绝对不能"覆盖防手痒，不再单独列软禁令
        SystemMessage(content=(
            "你是 Python 代码修复专家。\n\n"
            "核心原则：最小改动 —— 只修改 fix_instruction 指定的问题行，其余代码一字不改。\n\n"
            # --- 硬禁令：绝对禁止，fix_instruction 要求也不行 ---
            "硬禁令（以下行为绝对禁止，包括 fix_instruction 要求的情况）：\n"
            "1. 禁止改名 —— 函数名、类名、变量名、参数名一律不动\n"
            "2. 禁止改签名 —— 不增删参数、不改返回类型\n"
            "3. 禁止改作用域 —— 不得把局部变量提升为全局、或把全局降为局部\n\n"
            # --- 执行规则：标签判定，硬禁令违规静默丢弃 ---
            "判断规则：\n"
            "- fix_instruction 含 [需人工] 或 [跳过] → 跳过，写入 skipped_items\n"
            "- fix_instruction 无标签 → 先过硬禁令检查：\n"
            "  · 违反硬禁令（需改名/改签名/改作用域）→ 跳过该条，静默丢弃\n"
            "  · 未违反硬禁令 → 严格按 fix_instruction 逐一修复\n"
            "- 参考优先级：human_feedback > reflection_notes > fix_instruction\n"
            "- 修改后代码必须是可直接运行的合法 Python 代码\n\n"
            # --- 强力兜底：防止任何自发多做 ---
            "你绝对不能做任何 fix_instruction 要求之外的改动。一个字都不要多改。"
        )),
```

HumanMessage 追加提示：

```python
+ "\n\n注意：含 [需人工] 或 [跳过] 标记的条目，跳过修改，将其内容原样放入 skipped_items 列表。"
```

### 三、配套修复

| 文件 | 变更 | 来源 |
|------|------|------|
| `src/models.py` — `CoderResult` | 新增 `skipped_items: list[str]` | 新问题 #2 |
| `src/models.py` — `FinalReport` | 新增 `skipped_items` 字段 + status 新增 `partial` | 新问题 #2 |
| `src/models.py` — `ActionItem` | `fix_instruction` 新增 field_validator（None → ""） | 新问题 #3 |
| `src/graph/nodes.py` — critic_agent | 四分类判定（丢弃 / [需人工] / [跳过] / 修复） | 新问题 #5 |
| `src/graph/nodes.py` — coder_agent | 删除隐藏跳过规则，只保留标签判断 | 新问题 #5 |
| `src/graph/nodes.py` — coder_agent | 禁令精简为硬禁令（3 条，二道防线）+ 强力兜底，删除软禁令（语义与核心原则重叠） | 新问题 #6 |
| `src/graph/nodes.py` — output_node | 三元状态判定（success / partial / failed），透传 skipped_items | 新问题 #2, #4 |
| `tests/bugfix/b01/test_b01_04_mixed.py` | 适配 `partial` 状态和 `skipped_items` 展示 | 新问题 #2 |

### 四、标记语义

| 判定 | 触发条件 | coder 行为 | 用户看到 |
|------|---------|-----------|---------|
| 丢弃 | 不影响正确性（docstring、命名风格、类型注解等） | 不生成 action_item | 无 |
| `[需人工]` | 需要新建文件/安装依赖/改其他文件/依赖基础设施 | 跳过，写入 skipped_items | 需要手动建立基础设施 |
| `[跳过]` | 改动 >3 行 / 改签名 / 涉及核心算法/状态机/并发 | 跳过，写入 skipped_items | 问题已知，建议修复方向 |
| 修复 | 影响正确性，且不属于上述两类 | 按 fix_instruction 修复 | 修复完成 |


---



