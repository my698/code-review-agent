# B05 fix-log: sandbox 语义验证缺失

## 问题描述

**现象**：sandbox 只验证 `exit_code == 0`，不验证修复是否真正消除了问题。coder 可能做出"表面修复"——代码能跑但修复无效（如 `os.getenv("KEY")` 无默认值，sandbox 无此环境变量，返回 None，认证静默失败）。

**性质判断**：这不是 bug。sandbox 设计目标是"语法 + 运行时崩溃检测"，语义验证需要额外模块（LLM 验证节点 / 测试用例生成 / 静态分析），属于功能性增强，当前阶段不引入。

## 当前版本已具备的缓解机制

| 机制 | 位置 | 覆盖 |
|------|------|------|
| `[需人工]` 四分类 | `critic_agent` SystemMessage | 需要新建 .env/config/db 的修复被跳过，放入 `skipped_items`，透传到 human_review 让人处理 |
| B04 HITL 失败介入 | `builder.py` `retry_or_fail` | 重试耗尽后进入 human_review，人可发现假通过并驳回 |
| `-W error` 升级 warning | `sandbox_executor` | 把 Python warning 升级为异常，多抓一类"代码能跑但有问题"的情况 |

## 最初修复方案

（此问题无最初修复方案——一上来就判断为功能性增强，不是几行代码能修的 bug。）

## 最终修复方案

只做一行改动：sandbox 执行命令加 `-W error` 标志。

```python
# 旧
['python3', tmp_path]

# 新
['python3', '-W', 'error', tmp_path]
```

`-W error` 将所有 Python warning（SyntaxWarning、ResourceWarning 等）升级为异常，exit_code 从 0 变 1，sandbox 判失败。

## 未来的完整方案（后续阶段）

- LLM 语义验证节点：对比修复前后代码，判断修复是否真解决了 critic 指出的问题
- 测试用例生成：LLM 为代码生成测试用例，sandbox 运行用例验证行为
- 静态分析增强：扫描空壳修复模式（`os.getenv` 无默认值等）

以上均需引入新模块，当前阶段五不纳入。

## 测试脚本

### test_b05_01_warning_as_error.py
- 验证 `-W error` 将 SyntaxWarning 升级为异常
- 旧行为：exit_code=0，passed=True
- 新行为：exit_code=1，passed=False

## 实施记录

### 2026-05-18

- 修改 `sandbox_executor` 执行命令加 `-W error`
- 判断 B05 为功能性增强，不是 bug，当前版本不引入新模块
- 确认现有 [需人工] + B04 HITL 已做缓解
