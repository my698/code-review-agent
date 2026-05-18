"""
B03 验证脚本 #03：失败扣分缺失 — 评分无惩罚检测

用可正常运行的简单代码，检查当修复失败时 score_after 是否仍然 == score_before。
当前公式：失败分支 score_after = score_before，不扣分。

注意：sandbox 失败是概率事件，此脚本重点检测"如果失败了，公式是否扣分"。
如果本次运行未触发失败，则展示当前状态供参考。

用法：python tests/bugfix/b03/test_b03_failure_penalty.py
"""
import sys
import asyncio
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message=".*allowed_objects.*")
import logging
logging.getLogger("langgraph.checkpoint.serde.jsonplus").setLevel(logging.ERROR)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SRC_DIR = str(PROJECT_ROOT / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from config import LLM_MODEL, MAX_RETRY
from graph.builder import build_graph
from graph.state import INITIAL_STATE, AgentState

# 微妙代码样本：逻辑正确但有隐藏复杂度，LLM 修复容易引入 bug
# divide_all 处理空列表返回空，但 LLM 可能"优化"为列表推导式时搞错
# parse_numbers 用 try/except 做类型转换，是 Python 常见模式但 LLM 可能改坏
SAMPLE_CODE = '''
def divide_all(numbers, divisor):
    """Divide all numbers by divisor, skip if divisor is zero."""
    if divisor == 0:
        return []
    result = []
    for num in numbers:
        if num is not None:
            result.append(num / divisor)
    return result


def parse_numbers(items):
    """Convert items to numbers, filtering out non-numeric values."""
    numbers = []
    for item in items:
        try:
            numbers.append(float(item))
        except (ValueError, TypeError):
            pass
    return numbers


def merge_dicts(dicts):
    """Merge a list of dicts, later keys overwrite earlier ones."""
    result = {}
    for d in dicts:
        for key, value in d.items():
            result[key] = value
    return result
'''


async def run_with_timing(app, config, initial_state):
    node_times = {}
    total_cost = {}

    async def stream_until_pause(state, cfg):
        current_state = dict(state) if state else {}
        async for event in app.astream_events(state, cfg, version="v2"):
            kind = event["event"]
            name = event.get("name", "")
            if kind == "on_chain_start" and name in [
                "code_parser", "security_reviewer", "performance_reviewer",
                "style_reviewer", "critic_agent", "coder_agent",
                "sandbox_executor", "reflect_node", "human_review", "output_node",
            ]:
                node_times[name] = time.time()
                print(f"  ⏵ [{name}] 开始...")
            elif kind == "on_chain_end" and name in node_times:
                elapsed = time.time() - node_times.pop(name)
                total_cost[name] = total_cost.get(name, 0) + elapsed
                print(f"  ⏹ [{name}] 完成 ({elapsed:.1f}s)")
                output = event["data"].get("output", {})
                if isinstance(output, dict):
                    for k, v in output.items():
                        if k in AgentState.__annotations__:
                            current_state[k] = v
        return current_state

    print("正在执行审查流程...")
    state = await stream_until_pause(initial_state, config)
    state_snapshot = app.get_state(config)
    if state_snapshot.next:
        print(">>> 暂停在 human_review 节点，自动批准...")
        app.update_state(config, {"human_feedback": ""})
        state2 = await stream_until_pause(None, config)
        for k in ["coder_result", "critic_summary", "review_results"]:
            if k not in state2 and k in state:
                state2[k] = state[k]
        state = state2
    return state, total_cost


if __name__ == "__main__":
    print(f"=== B03 验证 #03：失败扣分缺失检测 ===")
    print(f"  LLM 模型: {LLM_MODEL}")
    print(f"  最大重试: {MAX_RETRY}")
    print()

    print("正在构建工作流图...")
    app = build_graph()
    print("图编译完成")
    print()

    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = SAMPLE_CODE
    config = {"configurable": {"thread_id": "b03-test-003"}}

    print("=== 待审查代码 ===")
    print(SAMPLE_CODE)

    result, cost = asyncio.run(run_with_timing(app, config, initial_state))

    print()
    print("=== 节点耗时统计 ===")
    for n in ["code_parser", "security_reviewer", "performance_reviewer",
              "style_reviewer", "critic_agent", "coder_agent",
              "sandbox_executor", "reflect_node", "human_review", "output_node"]:
        if n in cost:
            print(f"  {n:25s} {cost[n]:.1f}s")

    # ============================================================
    print()
    print("=== B03 评分公式检测 ===")

    report = result.get("final_report")
    if not report:
        print("报告未生成，无法检测")
        sys.exit(1)

    sandbox = result.get("sandbox_result")
    coder = result.get("coder_result")
    score_before = report.score_before
    score_after = report.score_after
    status = report.status
    retry_count = report.retry_count
    sandbox_passed = sandbox.passed if sandbox else False

    print(f"  score_before:  {score_before}")
    print(f"  score_after:   {score_after}")
    print(f"  score delta:   {score_after - score_before:+d}")
    print(f"  status:        {status}")
    print(f"  retry_count:   {retry_count}")
    print(f"  sandbox_passed: {sandbox_passed}")

    # 检测 1: 如果失败了，score_after 应 < score_before
    print()
    print("--- 检测 1: 失败扣分逻辑 ---")
    if status == "failed":
        print(f"  🟡 流程最终失败（重试 {retry_count} 次后）")
        if score_after < score_before:
            print(f"  ✅ 失败已扣分：{score_before} → {score_after} ({score_after - score_before:+d})")
        else:
            print(f"  ❌ B03 Bug 暴露：失败但分数不变（{score_before} → {score_after}）")
            print(f"     当前公式：失败分支 score_after = score_before = {score_before}")
            print(f"     合理公式：失败分支 score_after = {max(score_before - 10, 0)}")
    elif status == "success" or status == "partial":
        print(f"  ⏭ 状态={status}，未触发失败场景")
        print(f"     若失败时 score_after = score_before（不扣分），B03 Bug 2 存在")
    else:
        print(f"  ⏭ 状态={status}")

    # 检测 2: 重试次数与扣分的关系
    print()
    print("--- 检测 2: 重试累加扣分 ---")
    if retry_count > 0:
        print(f"  重试 {retry_count} 次，每次失败应累积扣分")
        print(f"  score 变化: {score_after - score_before:+d}")
        if score_after >= score_before:
            print(f"  ❌ 多次重试后分数仍不降，公式无惩罚机制")
    else:
        print(f"  ⏭ 无重试（retry_count=0）")

    # 检测 3: 当前公式模拟
    print()
    print("--- 检测 3: 公式对比 ---")
    changes_count = len(coder.changes) if coder and coder.changes else 0
    print(f"  coder changes: {changes_count} 条")
    print(f"  当前公式:")
    if sandbox_passed and changes_count > 0:
        print(f"    score_after = min({score_before} + {changes_count} * 3, 100) = {min(score_before + changes_count * 3, 100)}")
    else:
        print(f"    score_after = score_before = {score_before}  ← 无条件保持")
    print(f"  实际 score_after: {score_after}")

    # 检测 4: score_before 合理性
    print()
    print("--- 检测 4: score_before 合理性 ---")
    critic = result.get("critic_summary")
    if critic and hasattr(critic, 'score_before'):
        print(f"  critic 原始评分: {critic.score_before}")
        print(f"  total_issues: {critic.total_issues}")
        print(f"  by_severity: {critic.by_severity}")

    print()
    print("=== B03 验证 #03 完成 ===")
