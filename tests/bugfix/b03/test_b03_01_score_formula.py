"""
B03 验证脚本 #01：评分公式 — SQL 注入样本

用 SQL 注入样本跑完整流程，检查 score_before 和 score_after 的合理性。

检测项：
  1. score_before 在合理范围内（0-100）
  2. score_after 不出现不合理膨胀（不应从低分直接跳到 100）
  3. 提升幅度不过大（每次 change 不应 +3 以上）
  4. 如果 sandbox 失败，score_after 应低于 score_before

用法：python tests/bugfix/b03/test_b03_01_score_formula.py
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

# SQL 注入样本（与 B01 #01 同款，已知会被检出真漏洞）
SAMPLE_CODE = '''
import sqlite3

def get_user(user_input):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE name = '" + user_input + "'")
    return cursor.fetchall()
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
    print(f"=== B03 验证 #01：评分公式检测（SQL 注入样本） ===")
    print(f"  LLM 模型: {LLM_MODEL}")
    print()

    print("正在构建工作流图...")
    app = build_graph()
    print("图编译完成")
    print()

    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = SAMPLE_CODE
    config = {"configurable": {"thread_id": "b03-test-001"}}

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
    # B03 评分检测
    # ============================================================
    print()
    print("=== B03 评分公式检测 ===")

    report = result.get("final_report")
    if not report:
        print("报告未生成，无法检测")
        sys.exit(1)

    score_before = report.score_before
    score_after = report.score_after
    changes = report.action_items if hasattr(report, 'action_items') else []
    status = report.status
    retry_count = report.retry_count

    print(f"  score_before: {score_before}")
    print(f"  score_after:  {score_after}")
    print(f"  status:       {status}")
    print(f"  retry_count:  {retry_count}")
    print(f"  changes:      {len(changes)} 条 action_item")

    # 检测 1: score_before 在合理范围
    print()
    print("--- 检测 1: score_before 范围 ---")
    if 0 <= score_before <= 100:
        print(f"  ✅ score_before={score_before} 在 0-100 范围内")
    else:
        print(f"  ❌ score_before={score_before} 超出范围")

    # 检测 2: score_after 不膨胀
    print()
    print("--- 检测 2: score_after 膨胀检查 ---")
    score_delta = score_after - score_before
    print(f"  score 变化: {score_delta:+d}")
    if score_delta > 30:
        print(f"  ❌ score_after 膨胀过大（+{score_delta} > 30）")
    elif score_delta > 15:
        print(f"  🟡 score_after 提升偏大（+{score_delta}），关注是否合理")
    elif score_delta >= 0:
        print(f"  ✅ score_after 提升合理（+{score_delta}）")
    else:
        print(f"  ✅ score_after 下降（{score_delta}），可能 sandbox 失败")

    # 检测 3: 每个 change 贡献不超过 2 分
    print()
    print("--- 检测 3: 单 change 贡献检查 ---")
    if len(changes) > 0 and score_delta > 0:
        per_change = score_delta / len(changes)
        print(f"  每 change 贡献: {per_change:.1f} 分")
        if per_change > 3:
            print(f"  ❌ 单 change 加分过高（{per_change:.1f} > 3）")
        elif per_change > 2:
            print(f"  🟡 单 change 加分偏高（{per_change:.1f} > 2）")
        else:
            print(f"  ✅ 单 change 加分合理（{per_change:.1f} ≤ 2）")
    else:
        print("  ⏭ 无 change 或 score 未提升，跳过")

    # 检测 4: 失败时应扣分
    print()
    print("--- 检测 4: 失败扣分检查 ---")
    if status == "failed":
        if score_after < score_before:
            print(f"  ✅ 修复失败，score_after 已扣分（{score_delta:+d}）")
        else:
            print(f"  ❌ 修复失败但 score_after 未扣分（{score_delta:+d}）")
    else:
        print(f"  ⏭ 状态为 {status}，非失败场景，跳过")

    # 检测 5: critic 评分一致性
    print()
    print("--- 检测 5: critic 评分与最终报告一致 ---")
    critic = result.get("critic_summary")
    if critic and hasattr(critic, 'score_before'):
        critic_score = critic.score_before
        print(f"  critic.score_before: {critic_score}")
        print(f"  report.score_before: {score_before}")
        if critic_score == score_before:
            print("  ✅ 一致")
        else:
            print(f"  ❌ 不一致（相差 {abs(critic_score - score_before)}）")
    else:
        print("  ⚠️ 无 critic_summary")

    print()
    print("=== B03 验证 #01 完成 ===")
