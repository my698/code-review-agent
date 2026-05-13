# Bug 修复工作台

> 待修 bug 清单 + 修复记录。每个 bug 关联 `docs/dev-bug-dives.md` 的详细分析。

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

