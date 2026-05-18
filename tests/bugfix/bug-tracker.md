# Bug 修复工作台

> 待修 bug 清单 + 修复记录。

---

## 已修复

| Bug | 修复日期 | 说明 |
|-----|:--:|------|
| B00 健壮性 | 2026-05-18 | 全链路 LLM 输出兜底：7 节点 null 守卫 + 22 个 field_validator |
| B01 coder 越界 | 2026-05-16 | 硬禁令二道防线 + critic 四分类判定 |
| B02 安全误报 | 2026-05-17 | security_reviewer prompt 重构为结构化确认标准 |
| B03 评分公式 | 2026-05-17 | score_after 通胀修正 + 失败扣分 + 提升上限 |
| B04 失败缺 HITL | 2026-05-18 | retry_or_fail 重试耗尽后路由到 human_review |
| B05 sandbox 假通过 | 2026-05-18 | -W error 升级 warning 为异常（定性为功能增强，非 bug） |

---

## 各 Bug 详情

### B00: LLM 结构化输出健壮性

- **状态**: ✅ 已修复
- **修复日志**: `tests/bugfix/b00/fix-log.md`
- **现象**: LLM 的 `with_structured_output()` 不能保证输出 100% 符合 Pydantic schema。枚举值非法、必填字段为 null、整个输出为 None 三种失败模式均可导致 `ValidationError` 全链路崩溃。
- **修复**: 双重防线——节点级 `if result is None` 守卫 (7 节点) + 模型级 `@field_validator` 兜底 (22 个 validator, 9 个模型)

### B01: coder 越界重构

- **状态**: ✅ 已修复
- **修复日志**: `tests/bugfix/b01/fix-log.md`
- **现象**: coder 在修复计划外擅自改函数名、加业务逻辑、改异常语义
- **修复**: coder prompt 硬禁令（禁止改名/改签名/改作用域）+ critic 四分类判定（丢弃/[需人工]/[跳过]/修复）

### B02: 安全审查员误报

- **状态**: ✅ 已修复
- **修复日志**: `tests/bugfix/b02/fix-log.md`
- **现象**: 输入校验缺失被标为 CRITICAL 注入，对无害代码上纲上线
- **修复**: security_reviewer prompt 从 1 行扩到 ~30 行，确认标准（危险操作 + 不可信数据源 同时满足）、危险操作清单（每类带排除项）、禁止推测措辞

### B03: 评分公式问题

- **状态**: ✅ 已修复
- **修复日志**: `tests/bugfix/b03/fix-log.md`
- **现象**: `score_after = score_before + len(changes) * 3` 通胀，失败时无扣分
- **修复**: `*3→*2`，提升上限 `(100-sb)//2`，失败 `-10`。CoderResult 删除 `fixed_count` 字段

### B04: 失败流程缺少 HITL

- **状态**: ✅ 已修复
- **修复日志**: `tests/bugfix/b04/fix-log.md`
- **现象**: sandbox 失败后 retry 耗尽直接 output_node，无人工介入
- **修复**: `retry_or_fail` 在 `retry_count >= MAX_RETRY` 时返回 `human_review`（原是 `output_node`）。方案讨论中放弃"首次失败即 HITL"，采纳"先自动 retry 再人工介入"

### B05: sandbox 语义验证缺失

- **状态**: ✅ 已处理（功能增强，非 bug）
- **修复日志**: `tests/bugfix/b05/fix-log.md`
- **现象**: sandbox 只验语法不验语义，空壳修复（如 `os.getenv` 无默认值）可能假通过
- **处理**: sandbox 命令加 `-W error`，warning 升级异常。完整语义验证需新模块，当前阶段不引入。已有 `[需人工]` + B04 HITL 缓解

---

## 8 轮基线测试概况

> 2026-05-13 完成，覆盖安全/性能/风格/混合/干净代码等场景。

| # | 测试场景 | 核心发现 |
|---|---------|---------|
| 1 | SQL 注入 | coder 越界（改名/加权限校验） |
| 2 | 性能问题 | coder 改签名/加 import；score_after 通胀 |
| 3 | 风格灾难 | 安全审查员误报；coder 引入 Enum 重构 |
| 4 | 混合问题 | with_structured_output 返回 None 触发系统性修复 |
| 5 | 干净代码 | coder 修坏干净代码；失败无 HITL；score 不扣分 |
| 6 | 资源泄露 | coder 改名；reflect 诊断不足 |
