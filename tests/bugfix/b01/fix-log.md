# B01: coder 越界重构 — 修复日志

## 问题描述

coder_agent 在执行 fix_instruction 时私自添加额外改动：改名、改签名、加功能、乱加 import、加注释。8/8 测试全部出现，最严重时把干净代码修到完全失败（测试 #5）。

###### 尝试 #1 — 2026-05-14

### 入手点分析

越界重构的根源在两条链路：

1. **critic_agent（上游）** — fix_instruction 写得宽泛，"可执行"没有细度约束，给了 coder 自由发挥的空间
2. **coder_agent（下游）** — SystemMessage 缺少硬边界，对"不要重构"没有具体的禁令清单

修改文件：`src/graph/nodes.py`

- `critic_agent`（第 90-96 行）：SystemMessage 第 4 条
- `coder_agent`（第 130-137 行）：SystemMessage 整体

### 具体方案

#### 一、critic_agent SystemMessage — 收紧 fix_instruction 粒度

**改什么**：`nodes.py` 第 95 行，SystemMessage 第 4 条。

**为什么要改**：当前第 4 条只写了一句话"每条生成可执行的 fix_instruction"。critic 不知道"可执行"意味着什么粒度。从 8 轮测试来看，critic 生成的 fix_instruction 往往是自然语言描述，比如"修复 SQL 注入漏洞，使用参数化查询"。这种模糊指令到了 coder 手里，coder 为了"执行"就开始自由发挥——既然你没说怎么修，那我就按我的理解来。

**怎么改**：把"可执行"拆成 4 条硬性格式要求：必须有行号 + FROM→TO + 禁用模糊词 + 超 3 行建议跳过。这样 critic 输出的每条指令都是手术刀而非画笔，coder 拿到后不需要自己判断"怎么修"。

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
            "   - 只描述操作，不写"建议""考虑""可改为"等模糊词\n"
            "   - 如果修复需要改动 3 行以上，在 description 中标注 [建议跳过]"
        )),
```

#### 二、coder_agent SystemMessage — 加硬边界

**改什么**：`nodes.py` 第 130-137 行，SystemMessage 整体替换。

**为什么要改**：旧 prompt 的 5 条规则里只有第 1 条"不要重构其他部分"涉及边界，但"重构"这个词太抽象——LLM 不知道改名算不算重构、加类型注解算不算重构。8 轮测试的共性问题是：coder 认为自己在"优化"而非"越界"。需要把抽象禁令翻译成 LLM 能逐条对照执行的具体条目。

**设计思路**：
- "禁止改名"直接对应用户最痛的改名问题（测试 #1, #2, #6）
- "禁止加功能"封死加权限校验/脱敏/Enum 的路（测试 #1, #3）
- "禁止加 import"杜绝 `import logging` 写分支里的坏习惯（测试 #2）
- "跳过规则"让 coder 对 minor 问题有"不动"的许可——旧 prompt 让它每条都修，它不敢不修就硬上
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

**修改后**

```python
        SystemMessage(content=(
            "你是 Python 代码修复专家。\n\n"
            "核心原则：最小改动 —— 只修改 fix_instruction 指定的问题行，其余代码一字不改。\n\n"
            "硬约束（违反即修复无效）：\n"
            "1. 禁止改名 —— 函数名、类名、变量名、参数名一律不动\n"
            "2. 禁止改签名 —— 不增删参数、不改返回类型、不加类型注解\n"
            "3. 禁止加功能 —— fix_instruction 没说到的逻辑一律不添加\n"
            "4. 禁止加 import —— 不新增任何 import 语句\n"
            "5. 禁止加注释 —— 不写 # FIXED / # TODO / 解释性注释\n"
            "6. 禁止改作用域 —— 不得把局部变量提升为全局、或把全局降为局部\n\n"
            "判断规则：\n"
            "- fix_instruction 标注 [建议跳过] 的项 → 跳过，原样保留\n"
            "- LOW / MEDIUM 级别问题需要新增代码逻辑 → 跳过\n"
            "- 改动超过 3 行 → 跳过，不修\n"
            "- 参考优先级：human_feedback > reflection_notes > fix_instruction\n"
            "- 修改后代码必须是可直接运行的合法 Python 代码"
        )),

### 预期效果

- coder 不再改名、加功能、乱加 import
- 对 LOW/MEDIUM 问题不再过度修复
- 改动超 3 行的问题直接跳过

### 实际测试结果（第一轮）

运行 `test_b01_coder_overfix.py`，使用 SQL 注入样本：

| 检测项 | 结果 |
|--------|:----:|
| 重命名函数 | ✅ 通过 |
| 新增函数 | ✅ 通过 |
| 参数变更 | ✅ 通过 |
| 新增 import | ✅ 通过 |
| 行数膨胀 | ✅ 通过 |
| SQL 注入已修复 | ✅ 通过 |

5 项越界检测全部通过，禁令生效。与修复前（8/8 测试全部出现越界）对比，效果明显。

### 新问题发现

修复后代码出现作用域变更，原始代码中连接在函数内按需创建，修复后变为模块级全局变量：

```python
# 修复后：连接被提到模块级别
connection = sqlite3.connect("users.db")  # 导入时即执行

def get_user(user_input):
    cursor = connection.cursor()
    ...
```

这不在 5 条禁令覆盖范围内——不是改名、不是加功能、不是加 import、不是改签名。但行为语义确实变了：原来在调用函数时报错变成 import 时即报错；多次调用会共享同一个连接。

### 追加修复（第一轮内）

coder_agent SystemMessage 硬约束新增第 6 条：

> 6. 禁止改作用域 —— 不得把局部变量提升为全局、或把全局降为局部

代码位置：`nodes.py` 第 143 行。需用同样样本再次运行验证脚本确认第 6 条生效。

### 实际测试结果（第二轮，验证第 6 条禁令）

运行 `test_b01_coder_overfix.py`，同一样本：

| 检测项 | 结果 |
|--------|:----:|
| 重命名函数 | ✅ 通过 |
| 新增函数 | ✅ 通过 |
| 参数变更 | ✅ 通过 |
| 新增 import | ✅ 通过 |
| 行数膨胀 | ✅ 通过 |
| SQL 注入已修复 | ✅ 通过 |

第 6 条禁令生效 —— `conn` 保留在函数内部，未被提升为全局。新增 `timeout=5.0` 参数微调不属于禁令范围。6 项全量禁令全部通过。

### 下一步

换用不同样本（性能问题为主、代码更长），多维度验证禁令在异类型代码上的稳定性。

### 实际测试结果（第三轮，多函数 + 性能样本）

运行 `test_b01_02_perf_duplicate.py`，O(n²) + 冗余字典查找样本，两个函数、代码更长：

| 检测项 | 结果 |
|--------|:----:|
| 重命名函数 | ✅ 通过 |
| 新增函数 | ✅ 通过 |
| 参数变更 | ✅ 通过 |
| 新增 import | ✅ 通过 |
| 行数膨胀 | ✅ 通过 |
| 作用域变更 | ✅ 通过 |

6 项全量禁令全部通过，修复效果：

- `find_duplicates`：O(n²) 双层循环 → O(n) set 去重，函数名和参数不变
- `get_scores`：去掉冗余 `if name in students` 检查，保留 docstring

与第一轮 SQL 注入样本对比，不同代码长度、不同问题类型下禁令均稳定生效。B01 初步可关闭。


---

## 尝试 #2 — 2026-05-15：新增 [需人工] 机制

### 入手点分析

测试 #04（混合问题样本：硬编码密钥 + 裸 except + 资源泄露）暴露了一个禁令无法覆盖的新问题。

修复后代码：
```python
import os            # 🔴 违反禁令第4条
import logging       # 🔴 违反禁令第4条

def fetch_user_data(user_id):
    if not user_id.isdigit():        # 🔴 违反禁令第3条（新增校验）
        raise ValueError("...")
    api_key = os.getenv("API_KEY")   # 把硬编码密钥改为环境变量
    ...
    except requests.RequestException as e:
        logging.exception(...)
```

越界检测抓到 `import os` 和 `import logging`。

但这不是之前那种"coder 手痒多改"的问题——**禁令和修复需求发生了冲突**：

- 安全审查员报告 `api_key = "sk-abc..."` 为 CRITICAL 硬编码密钥
- critic 生成 fix_instruction：改为 `os.getenv("API_KEY")`
- coder 面临两难：要修 CRITICAL 漏洞就必须 `os.getenv()`，要 `os.getenv()` 就必须 `import os`
- 禁令第 4 条禁止一切 import，但 coder 选择了"违禁令也要修漏洞"

更深层的问题：在孤立合成代码中，`os.getenv("API_KEY")` 修完代码**反而不可运行**——没有 `.env` 文件，没有 `load_dotenv()`，`os.getenv()` 返回 `None`，`Authorization: f"Bearer None"` 静默失败。从硬编码的确定 bug 变成了运行时不确定性。sandbox 只验语法，检测不到这个问题（B05 假通过）。

**结论：这类"改单个文件修不了"的问题，根本不应该自动修。应该跳过并给人工建议。**

### 改动范围

4 个文件，按依赖关系从底向上：

### 一、`src/models.py`

#### CoderResult — 新增 `skipped_items` 字段

```python
class CoderResult(BaseModel):
    fixed_code: str
    changes: list[ChangeItem] = Field(default_factory=list)
    fixed_count: int = 0
    notes: str = ""
    # [B01-#04] 因 [需人工] 跳过的条目
    skipped_items: list[str] = Field(default_factory=list)
```

#### FinalReport — 新增 `skipped_items` + status 新增 `partial`

```python
class FinalReport(BaseModel):
    ...
    status: str = "running"                                 # running / success / partial / failed
    skipped_items: list[str] = Field(default_factory=list)  # [B01-#04]
```

### 二、`src/graph/nodes.py` — critic_agent

在 SystemMessage 新增第 5 条规则：

```python
"5. [需人工] 标注规则（满足任一条件即标注）：\n"
"   - 修复需要新建文件（.env / config.py / Makefile 等）→ 必须标注 [需人工]\n"
"   - 修复需要安装新依赖包（pip install xxx）→ 必须标注 [需人工]\n"
"   - 修复需要改动当前文件以外的代码 → 必须标注 [需人工]\n"
"   - 修复依赖项目基础设施（环境变量注入、密钥管理系统、数据库连接池等）→ 必须标注 [需人工]\n"
"   - [需人工] 条目的 fix_instruction 不写 FROM→TO，改为描述：问题是什么 + 人工需要建立什么基础设施 + 建立后代码可以怎么改\n"
"   - 示例 fix_instruction：'第33行 api_key 为硬编码密钥。需人工建立 .env 文件存放 API_KEY，并在项目入口加 load_dotenv()。完成后将 api_key = \"sk-xxx\" 改为 api_key = os.getenv(\"API_KEY\")'"
```

### 三、`src/graph/nodes.py` — coder_agent

SystemMessage 判断规则首条：

```python
"- fix_instruction 标注 [需人工] → 必须跳过该条，不修，将该条内容原样写入 skipped_items 列表\n"
```

HumanMessage 追加提示：

```python
+ "\n\n注意：含 [需人工] 标记的条目，跳过修改，将其内容原样放入 skipped_items 列表。"
```

### 四、`src/graph/nodes.py` — output_node

状态判定从二元变为三元：

```python
# [B01-#04] 状态判定
if not sandbox_passed:
    status = "failed"
elif skipped:
    status = "partial"      # 新增：沙箱通过但有人工建议
else:
    status = "success"
```

`FinalReport` 组装时透传 `skipped_items`。

### 五、`tests/bugfix/b01/test_b01_04_mixed.py`

测试脚本同步更新：
- 展示 `report.status` 和 `skipped_items` 列表
- `partial` 状态视为可接受（不报失败退出）

### 实际测试结果

运行 `test_b01_04_mixed.py`：

```
=== B01 越界检测 ===
  流程状态: partial
  需人工介入 (1 条):
    - [1] 行4 | critical/敏感信息 — 指令：第4行 api_key 为硬编码密钥。
      需人工建立 .env 文件存放 API_KEY，并在项目入口加 load_dotenv()。
      完成后将 api_key = "sk-xxx" 改为 api_key = os.getenv("API_KEY")

  ✅ 重命名函数: 通过
  ✅ 新增函数: 通过
  ✅ 参数变更: 通过
  ✅ 新增import: 通过      ← 不再出现 import os / import logging
  ✅ 行数膨胀: 通过
  ✅ 作用域变更: 通过

=== 修复后代码 ===
import requests

def fetch_user_data(user_id):
    api_key = "sk-abc123def456ghi789"    ← 原样保留
    ...
    except:                               ← 裸 except 也原样保留
        ...

def read_config(path):
    f = open(path, "r")                   ← 手动 open/close 原样保留
    ...
```

关键变化：
- 硬编码密钥被正确识别为 `[需人工]`，coder 正确跳过
- 不再为修密钥而 `import os`，不再连带 `import logging`
- 代码一字未改，零越界
- 最终报告含人工建议，用户能看到

### [需人工] vs [建议跳过] 的区别

| 标记 | 含义 | coder 行为 |
|------|------|-----------|
| `[建议跳过]` | 修复可行但改动大（>3行），风险高 | 跳过，不记录 |
| `[需人工]` | 修复超出单文件能力，需要基础设施 | 跳过，**写入 skipped_items**，最终报告展示 |

### 注意

裸 `except:` 和 `open/close` 资源泄露此次也未修复。需要后续确认 critic 是将它们合并到了 `[需人工]` 条目中，还是给了独立的 fix_instruction。如果是前者，应该分开处理——可自动修的问题不应和需人工的问题绑在一起。


---

## 尝试 #3 — 2026-05-15：ActionItem.fix_instruction 缺失兜底

### 入手点分析

测试 #05（干净代码样本）在 critic_agent 节点崩溃：

```
pydantic_core._pydantic_core.ValidationError: 1 validation error for CriticSummary
action_plan.6.fix_instruction
  Field required [type=missing, ...]
```

堆栈显示 critic_agent 返回的 `CriticSummary` 中，第 6 个 `ActionItem` 缺少 `fix_instruction` 字段。LLM 偶发性漏字段，而 Pydantic 的 `fix_instruction: str`（无默认值）要求该字段必须存在。

### 改动范围

#### `src/models.py` — ActionItem

新增 field_validator，与其他两个 validator 并列：

```python
class ActionItem(BaseModel):
    ...
    fix_instruction: str

    @field_validator("fix_instruction", mode="before")
    @classmethod
    def missing_fix_instruction_fallback(cls, v):
        return v if v is not None else ""
```

### 机制说明

- `mode="before"` — 在 Pydantic 校验类型前先执行，不管 LLM 返回了什么原始值
- `v if v is not None else ""` — LLM 输出了就原样返回；漏字段（None）就返回空字符串，后续 `str` 校验也能过

### 与 severity / category validator 的对比

| 字段 | 兜底策略 | 原因 |
|------|---------|------|
| `severity` | 非法枚举值 → `MEDIUM` | LLM 编了一个不在枚举里的值 |
| `category` | 非法枚举值 → `OTHER` | 同上 |
| `fix_instruction` | `None` → `""` | **LLM 压根没输出这个 key**，字段缺失 |

前两者是"值不合法"，后者是"字段缺失"，防御手段不同。

### 实际测试结果

重新运行 `test_b01_05_clean.py` 不再崩溃。
