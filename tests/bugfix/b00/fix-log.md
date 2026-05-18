# B00 fix-log: LLM 结构化输出全链路健壮性加固

## 问题描述

LLM 的 `with_structured_output()` 不能保证输出 100% 符合 Pydantic schema。三种典型失败模式：

1. **整个输出为 None** — LLM 返回空或 LangChain 解析失败
2. **枚举值非法** — severity 写"非常严重"而不是 "critical"
3. **必填字段为 null** — LLM 漏填 name/lineno/description 等字段

不做兜底 → `ValidationError` → 全链路崩溃。

## 修复方案

两层防线：

1. **节点层 null 守卫** — `if result is None: return {}`
2. **模型层 field_validator** — 非法枚举 → 安全默认值，null → 空值

### 第一条防线：节点级 null 守卫（7 节点）

每个 LLM 调用节点在 `with_structured_output().invoke()` 之后立即检查：

```python
result = structured_llm.invoke([...])
if result is None:
    return {}  # 或 return safe_default
```

| 节点 | 处理 |
|------|------|
| `code_parser` | `return {"code_analysis": CodeAnalysis()}` |
| `security_reviewer` | `return {"review_results": []}` |
| `performance_reviewer` | `return {"review_results": []}` |
| `style_reviewer` | `return {"review_results": []}` |
| `critic_agent` | `return {}` |
| `coder_agent` | `return {}` |
| `reflect_node` | `return {"reflection_notes": "LLM 返回为空...", "retry_count": ...}` |

消费端守卫（`coder_agent` / `sandbox_executor` / `reflect_node` / `output_node`）同步检查上游字段：

```python
coder = state.get('coder_result')
if coder is None:
    return {}
```

### 第二条防线：field_validator（22 个 validator，9 个模型）

#### 枚举值兜底

| 字段 | 风险 | 兜底值 |
|------|------|--------|
| `Issue.severity` | "非常严重"、乱码 | `MEDIUM` |
| `Issue.category` | "资源管理"、非法值 | `OTHER` |
| `ActionItem.severity` | 同 Issue | `MEDIUM` |
| `ActionItem.category` | 同 Issue | `OTHER` |
| `ReflectionResult.failure_type` | LLM 编的类型 | `LOGIC_ERROR` |

#### null → 默认值兜底

| 模型 | 字段 | 兜底值 |
|------|------|--------|
| `FunctionInfo` | `name` | `""` |
| | `lineno` | `0` |
| `ClassInfo` | `name` | `""` |
| | `lineno` | `0` |
| `Issue` | `suggestion`, `description`, `code_snippet` | `""` |
| | `lineno` | `0` |
| `ReviewResult` | `issues` | `[]` |
| `ActionItem` | `priority`, `lineno` | `0` |
| | `description` | `""` |
| | `fix_instruction` | `""` |
| `CriticSummary` | `score_before` | `0` |
| | `total_issues` | `0` |
| `ChangeItem` | `lineno` | `0` |
| | `original`, `fixed`, `reason` | `""` |
| `CoderResult` | `fixed_code` | `""` |
| | `changes`, `skipped_items` | `[]` |
| `ReflectionResult` | `root_cause`, `new_strategy` | `""` |

#### 其他防护

| 位置 | 机制 | 说明 |
|------|------|------|
| `ReviewResult.dimension` | 节点 hard assign | `result.dimension = ReviewDimension.SECURITY`，完全忽略 LLM 输出 |

## 覆盖范围

| 模型 | LLM 来源 | validator 数 | 覆盖状态 |
|------|----------|:----------:|:------:|
| `CodeAnalysis` | code_parser | 0 | 所有字段有默认值，无需 validator |
| `FunctionInfo` | code_parser | 2 | ✅ |
| `ClassInfo` | code_parser | 2 | ✅ |
| `Issue` | 三审查员 | 4 | ✅ |
| `ReviewResult` | 三审查员 | 1 | ✅ |
| `ActionItem` | critic_agent | 4 | ✅ |
| `CriticSummary` | critic_agent | 2 | ✅ |
| `ChangeItem` | coder_agent | 2 | ✅ |
| `CoderResult` | coder_agent | 3 | ✅ |
| `ReflectionResult` | reflect_node | 2 | ✅ |
| `SandboxResult` | 非 LLM | 0 | 我方代码生成，无需 validator |
| `FinalReport` | 非 LLM | 0 | 我方代码生成，无需 validator |

## 实施记录

### B00 汇总（2026-05-18）

新增 12 个 field_validator，覆盖 7 个模型中此前遗漏的 Required 字段：

- `FunctionInfo`: name, lineno
- `ClassInfo`: name, lineno
- `ActionItem`: priority, description, lineno
- `CriticSummary`: score_before, total_issues
- `ChangeItem`: lineno, original, fixed, reason
- `CoderResult`: fixed_code
- `ReflectionResult`: root_cause, new_strategy

连同此前 B01-B05 已加的 10 个 validator + 7 个节点级 null 守卫，全部 LLM 产出字段已无遗漏。
