# Bug 修复工作台

> 待修 bug 清单 + 修复记录。每个 bug 关联 `docs/dev-bug-dives.md` 的详细分析。

---

## 8 轮测试概况

> 2026-05-13 完成，覆盖安全/性能/风格/混合/干净代码等场景。

| # | 测试场景 | 测试代码特征 | 核心发现 |
|---|---------|-------------|---------|
| 1 | SQL 注入 | `cursor.execute(sql)` 拼接用户输入 | coder 改名(`get_user`→`get_users_by_name`)、提升全局连接、加权限校验/脱敏/环境变量 |
| 2 | 性能问题 | O(n²) 循环 + 冗余字典查找 | coder 改签名(`items`→`elements`)、加 TypeError、`import logging` 写在 if 分支内；score_after 通胀(42→100) |
| 3 | 风格灾难 | 命名混乱、格式烂、违反 PEP 8 | 安全审查员误报（输入校验≠注入）；coder 引入 Enum 重构、改异常语义 |
| 4 | 混合问题 | 硬编码密钥 + 裸 except + 资源泄露 | `with_structured_output` 返回 None 导致全链路崩溃，触发系统性 None 守卫修复 |
| 5 | 干净代码 | 可正常运行、无明显问题 | coder 对 6 个 LOW/MEDIUM 问题逐一修坏，3 次重试耗尽；失败流程无 HITL；score_after 不变(85→85) |
| 6 | 资源泄露 | `subprocess` 未等待、文件句柄未关 | coder 再次改名(`run_backup`→`execute_command`)；reflect 诊断能力不足 |
| 7 | （待补充） | — | — |
| 8 | （待补充） | — | — |

### 各测试暴露的 Bug 分布

| Bug | 测试 #1 | #2 | #3 | #4 | #5 | #6 |
|-----|:-:|:-:|:-:|:-:|:-:|:-:|
| B01 coder 越界 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| B02 安全审查误报 | | | ✓ | | | |
| B03 评分公式 | | ✓ | | | ✓ | |
| B04 失败缺 HITL | | | | | ✓ | |
| B05 sandbox 假通过 | | | | | | |

---

## B01: coder 越界重构

- **状态**: 🔴 待修
- **关联**: `docs/dev-bug-dives.md` #6（待写入）
- **测试脚本**: `test_b01_coder_overfix.py`
- **现象**: coder 在修复计划外擅自改函数名、加业务逻辑（Enum/权限校验/脱敏）、改异常语义（None→raise）、选择性执行（用注释糊弄代替真修复）
- **影响**: 8/8 测试出现，最高频问题
- **修复方向**: 强化 coder prompt 约束——禁止改名、禁止加新逻辑、禁止只加注释不修改

## B02: 安全审查员误报

- **状态**: 🔴 待修
- **关联**: `docs/dev-bug-dives.md` #7（待写入）
- **测试脚本**: `test_b02_security_false_positive.py`
- **现象**: 输入校验缺失被标为"critical 注入"，对无害代码上纲上线
- **影响**: 拉低整体评分，误导 coder 过度修复
- **修复方向**: prompt 加误报抑制——"只报告确认存在的安全漏洞，不推测潜在风险"

## B03: 评分公式问题

- **状态**: 🔴 待修
- **关联**: `docs/dev-bug-dives.md` #8（待写入）
- **测试脚本**: `test_b03_score_formula.py`
- **现象**: 
  - `score_after = score_before + len(changes) * 3` 通胀（改动越多分越高）
  - 修复失败时 score_after 不变（42→100, 85→85 都不合理）
- **修复方向**: score_after 基于 critic 评分而非改动数量；失败时扣分

## B04: 失败流程缺少 HITL

- **状态**: 🔴 待修
- **关联**: `docs/dev-bug-dives.md` #9（待写入）
- **测试脚本**: `test_b04_failure_no_hitl.py`
- **现象**: sandbox 失败 → reflect → retry 耗尽 → output_node，全程无人工介入
- **影响**: 用户收到 status=failed 但不知道修复尝试了什么
- **修复方向**: 失败终止前插入 human_review，或至少展示 reflect 分析结果

## B05: sandbox 虚假通过

- **状态**: 🔴 待修
- **关联**: `docs/dev-bug-dives.md` #10（待写入）
- **测试脚本**: `test_b05_sandbox_verify.py`
- **现象**: 沙箱只跑 `python3 tmp.py` 测语法，不验证修复是否真消除了问题。`exec(code_str)` 仍保留，但 `sandbox_passed=True`
- **修复方向**: 短期在 prompt 中关注、长期加验证用例执行

---

## 已修复

（修复后移至此处）

