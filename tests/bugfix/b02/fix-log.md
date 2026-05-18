# B02: 安全审查员误报 — 修复日志

## 问题描述

security_reviewer 对无安全风险的代码上纲上线：输入校验缺失被标为 CRITICAL 注入漏洞、无害代码被推测为"潜在风险"。导致整体评分被拉低，误导 coder 过度修复（本来只需改风格，结果被安全标签逼着修"漏洞"）。

**触发样本**（测试 #3 风格灾难）：

```python
def calc_sm(a,b,c):
    x=a+b+c
    y=x/3
    return y

def GetData(Query):
    import json
    d=json.loads(Query)
    return d["result"]
```

此代码无任何安全漏洞。`GetData` 接收 JSON 字符串并解析，`json.loads` 本身不会注入，返回 `d["result"]` 也不是 SQL/命令执行。但 security_reviewer 将其标为 CRITICAL 注入。

---

## 最初修复方案

### 根因

security_reviewer 的 SystemMessage 只有一句话：

> "你是一个资深安全审计专家，专查注入漏洞、敏感信息泄露、加密缺陷、权限问题。"

这个 prompt 存在两个问题：

1. **无抑制指令** — 只说"查"，没说"只报告确认存在的漏洞"
2. **无分级指引** — 没有区分"确认存在"和"潜在风险"，LLM 倾向宁可误报不可漏报

LLM 看到 `d["result"]` 这种"外部数据流入→使用"的模式，即使没有真正的注入点，也会因为"安全审计专家"的角色设定而上报。

### 修复方向

security_reviewer 的 SystemMessage 加两条核心约束：

1. **确认原则**：只报告确认存在的安全漏洞，不推测潜在风险
2. **注入判断标准**：必须有明确的 sink（SQL 执行/命令执行/模板渲染等），仅有数据流入不构成注入

---

## 测试概况

| 轮次 | 测试脚本 | 样本类型 | 目的 |
|------|---------|---------|------|
| R1 | `test_b02_01_style.py` | 风格灾难 | 纯风格代码是否误报 CRITICAL |
| R2 | `test_b02_02_file_ops.py` | 安全文件 IO | `open`+`json.load` 是否误判路径遍历/反序列化 |
| R3 | `test_b02_03_dataprocess.py` | 数据处理 | `json.loads`+f-string 是否误判注入 |
| R4 | `test_b02_04_mixed.py` | 真安全漏洞+非安全混合 | 正确识别硬编码密钥，不将裸 except 标安全 |
| R5 | `test_b02_05_clean.py` | 干净代码 | 不对无问题代码发明安全问题 |
| R6 | `test_b02_06_sql_mixed.py` | SQL 注入(真)+参数化查询(伪)+json 无sink(伪) | 区分真注入与安全模式 |
| R7 | `test_b02_07_command_mixed.py` | 命令注入+eval(真)+subprocess硬编码(伪)+安全路径(伪) | 区分真命令注入与安全调用 |
| R8 | `test_b02_08_credentials_mixed.py` | 硬编码凭据+pickle(真)+os.getenv(伪)+json.load(伪) | 区分真凭据泄露与安全读取 |

---

## 修复记录

### 修改一、security_reviewer SystemMessage — 确认原则 + 危险操作清单

**旧版（1 句话）**：
```
你是一个资深安全审计专家,专查注入漏洞、敏感信息泄露、加密缺陷、权限问题。
```

**新版（确认标准 + 6 类危险操作清单 + 排除项）**：
- 确认标准：必须同时满足攻击面（不可信数据）和危险操作，仅满足一条不报告
- 6 类危险操作：SQL 拼接执行、命令执行、路径拼接、硬编码凭据、不安全反序列化、不安全加密
- 每类附带排除项（参数化查询、硬编码 subprocess、os.path.join 固定前缀、json.loads、os.getenv）
- 严重度指引：CRITICAL=sq拼接/命令注入/硬编码生产凭据/pickle.load(用户输入)
- 约束：不确定 → 不报告，禁止推测措辞

### 修改二、Issue 模型 — 必填字段缺失兜底

`src/models.py`：给 `Issue.suggestion`、`description`、`code_snippet`、`lineno` 加 field_validator。
LLM 偶发遗漏字段导致 ValidationError（本次 performance_reviewer 触发），与 B01 ActionItem.fix_instruction 同类问题。

| 字段 | 兜底 |
|------|------|
| `suggestion` | `""` |
| `description` | `""` |
| `code_snippet` | `""` |
| `lineno` | `0` |

### 修改文件总览

| 文件 | 变更 |
|------|------|
| `src/graph/nodes.py` — security_reviewer | SystemMessage 从 1 句扩展为确认原则+6类清单+排除项 |
| `src/models.py` — Issue | 新增 4 个 field_validator 防 LLM 漏字段 |

---

## 检测项

| 检测项 | 说明 |
|--------|------|
| 安全 CRITICAL 误报 | 无安全漏洞的代码不应被标为 CRITICAL 或 HIGH（安全维度） |
| category 越界 | 风格问题不应标为安全类 category（如 `注入`、`敏感信息`） |
| 推测性报告 | 不应出现"可能""潜在""建议加强"等推测性措辞的安全问题 |

---

## 最终修复方案

### security_reviewer 最终 SystemMessage（`nodes.py` 行 38-69）

```python
SystemMessage(content = (
    "你是资深安全审计专家。只报告确认存在的安全漏洞，不推测潜在风险。\n\n"
    "确认标准 —— 必须同时满足：\n"
    "1. 代码中存在危险操作（具体函数/模式见下方）\n"
    "2. 该危险操作的输入来自不可信数据源（用户输入/外部请求/文件读取）\n"
    "仅满足一条 → 不报告。\n\n"
    "危险操作清单：\n\n"
    "· SQL 拼接执行 — cursor.execute(sql_string) / raw() / extra() 且 sql_string 含用户输入\n"
    "  排除：参数化查询 cursor.execute(sql, (user_input,))\n\n"
    "· 命令执行 — os.system() / subprocess.call() / eval() / exec() / compile()\n"
    "  排除：subprocess.run([\"ls\", \"-l\"]) 参数列表已硬编码的情况\n\n"
    "· 路径拼接 — open(user_input) / open(path + user_input) 无校验\n"
    "  排除：open(\"config.json\") / open(os.path.join(BASE, x)) 路径前缀固定的\n\n"
    "· 硬编码凭据 — 代码中出现 password=\"xxx\" / api_key=\"sk-xxx\" / secret=\"xxx\" 等固定字符串\n"
    "  这是唯一不需要攻击面的条目 —— 凭据本身即是漏洞\n\n"
    "· 不安全反序列化 — pickle.load() / yaml.load() / marshal.load()\n"
    "  排除：json.load/loads（安全，不构成反序列化漏洞）\n\n"
    "· 不安全加密 — MD5/SHA1 做密码哈希 / 硬编码加密盐或 IV\n\n"
    "严重度：\n"
    "  CRITICAL — sql拼接/命令注入/硬编码生产凭据/pickle.load(用户输入)\n"
    "  HIGH — 其他确认漏洞\n"
    "  MEDIUM — 确认存在但危害低（如无实际利用路径的路径拼接）\n\n"
    "无确认漏洞 → issues 返回空列表 []\n"
    "不确定 → 不报告\n"
    "禁止\"可能\"\"潜在\"\"建议加强\"等推测措辞"
)),
```

### 设计原则

- **清单适用安全审查**：安全漏洞种类有限（5-8 类），每类有明确 technical signature（函数名/代码模式），列清单不会无限膨胀
- 区别于 critic 的原则判定（critic 面对的问题种类无限，必须用原则而非清单）
- 每类配排除项，压制已知误报（参数化查询、json.loads、os.getenv、subprocess 硬编码参数等）

