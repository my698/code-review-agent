"""
B03 验证脚本 #05：干净代码基线 — 评分不变检测

干净代码应得高分（>=80），且修复后分数不应大幅变化（没有需要修的问题）。

用法：python tests/bugfix/b03/test_b03_05_clean.py
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

# 干净代码样本（与 B01 #05 同款）
SAMPLE_CODE = '''
def calculate_average(numbers):
    """Return the arithmetic mean of a list of numbers."""
    if not numbers:
        return 0.0
    return sum(numbers) / len(numbers)


def safe_divide(a, b):
    """Divide a by b, returning None when b is zero."""
    try:
        return a / b
    except ZeroDivisionError:
        return None
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
    print(f"=== B03 验证 #05：干净代码基线评分 ===")
    print(f"  LLM 模型: {LLM_MODEL}")
    print()

    print("正在构建工作流图...")
    app = build_graph()
    print("图编译完成")
    print()

    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = SAMPLE_CODE
    config = {"configurable": {"thread_id": "b03-test-005"}}

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

    coder = result.get("coder_result")
    critic = result.get("critic_summary")
    score_before = report.score_before
    score_after = report.score_after
    status = report.status
    changes_count = len(coder.changes) if coder else 0
    changes = coder.changes if coder else []

    print(f"  score_before:  {score_before}")
    print(f"  score_after:   {score_after}")
    print(f"  score delta:   {score_after - score_before:+d}")
    print(f"  coder 修复:    {changes_count} 处")
    print(f"  changes:       {len(changes)} 条")
    print(f"  status:        {status}")

    # 检测 1: 干净代码应得高分
    print()
    print("--- 检测 1: 干净代码高分基线 ---")
    if score_before >= 80:
        print(f"  ✅ score_before={score_before}，干净代码得高分")
    elif score_before >= 60:
        print(f"  🟡 score_before={score_before}，中等偏低")
    else:
        print(f"  ❌ score_before={score_before}，干净代码不应得低分")

    # 检测 2: 分数不应因无改动而变化
    print()
    print("--- 检测 2: 无改动时分数稳定 ---")
    if changes_count == 0:
        if score_after == score_before:
            print(f"  ✅ 无改动，分数不变（{score_before}）")
        else:
            print(f"  ❌ 无改动但分数变化：{score_before}→{score_after}")
    else:
        print(f"  ⚠️ coder 修复了 {changes_count} 处，分数变化 +{score_after - score_before}")
        if score_after - score_before > 5:
            print(f"  ❌ 干净代码不应有大幅分数提升（改动可能是越界修复）")
        else:
            print(f"  ✅ 分数变化微小")

    # 检测 3: 不应出现失败
    print()
    print("--- 检测 3: 状态检测 ---")
    if status == "success":
        print(f"  ✅ 状态=success，干净代码流程正常")
    elif status == "partial":
        print(f"  🟡 状态=partial，有跳过项（检查是否不必要的建议）")
    elif status == "failed":
        print(f"  ❌ 状态=failed，干净代码不应导致失败")
    else:
        print(f"  ⏭ 状态={status}")

    # 检测 4: critic 评分一致性
    print()
    print("--- 检测 4: critic 评分一致性 ---")
    if critic and hasattr(critic, 'score_before'):
        c_score = critic.score_before
        print(f"  critic: {c_score}  |  report: {score_before}")
        if c_score == score_before:
            print("  ✅ 一致")
        else:
            print(f"  ❌ 差 {abs(c_score - score_before)}")
    else:
        print("  ⚠️ 无 critic_summary")

    print()
    print("=== B03 验证 #05 完成 ===")
