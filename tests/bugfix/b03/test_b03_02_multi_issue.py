"""
B03 验证脚本 #02：多问题混合样本 — 评分通胀检测

SQL注入+O(n²)循环+命名混乱+格式烂，四个维度全有实际可修复问题。
coder 修复后预计 >=3 处 change，用 +3/chg 公式会显著拉高 score_after。

检测项：
  1. score_before 反映代码真实质量（应偏低，30-60）
  2. score_after 不因多处修复而过度膨胀（单 change +3 会放大问题）
  3. 提升幅度不超过 30 分（从烂代码修几处不应接近满分）

用法：python tests/bugfix/b03/test_b03_02_multi_issue.py
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

# 多问题混合：SQL 注入(真安全) + O(n²)循环(真性能) + 命名混乱(真风格) + 格式烂(真风格)
# 预计 critic score_before 偏低（30-50），coder 会修复多处 → 容易暴露 +3/chg 通胀
SAMPLE_CODE = '''
import sqlite3

def GetUserData(userinput):
    conn=sqlite3.connect("users.db")
    cursor=conn.cursor()
    cursor.execute("SELECT * FROM users WHERE name='"+userinput+"'")
    results=cursor.fetchall()
    output=[]
    for i in range(len(results)):
        for j in range(len(results)):
            if i!=j and results[i]==results[j] and results[i] not in output:
                output.append(results[i])
    return output


def ProcItems(lst):
    res=[]
    for i in range(len(lst)):
        res.append(lst[i]*2)
    return res
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
    print(f"=== B03 验证 #02：评分通胀检测（多问题混合） ===")
    print(f"  LLM 模型: {LLM_MODEL}")
    print()

    print("正在构建工作流图...")
    app = build_graph()
    print("图编译完成")
    print()

    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = SAMPLE_CODE
    config = {"configurable": {"thread_id": "b03-test-002"}}

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
    changes = coder.changes if coder else []
    changes_count = len(changes)
    skipped = coder.skipped_items if coder else []
    status = report.status

    print(f"  score_before: {score_before}")
    print(f"  score_after:  {score_after}")
    print(f"  coder 实际修复: {changes_count} 处")
    print(f"  coder changes 记录: {len(changes)} 条")
    print(f"  skipped_items: {len(skipped)} 条")
    print(f"  status: {status}")

    score_delta = score_after - score_before

    # 检测 1: score_before 应偏低（烂代码）
    print()
    print("--- 检测 1: score_before 合理范围 ---")
    if score_before < 30:
        print(f"  ✅ score_before={score_before} 偏低，反映代码质量差")
    elif score_before < 60:
        print(f"  🟡 score_before={score_before} 中等，不算明显偏低")
    else:
        print(f"  ❌ score_before={score_before} 偏高，烂代码不应得高分")

    # 检测 2: 通胀检测 —— 当前公式 +3/chg，修复越多越膨胀
    print()
    print("--- 检测 2: 评分通胀（当前公式 +3/chg vs 合理 +2/chg） ---")
    expected_current = min(score_before + changes_count * 3, 100)
    expected_reasonable = min(score_before + changes_count * 2, 100 - score_before // 2 + score_before)
    print(f"  当前公式预估 (+3/chg): {expected_current}")
    print(f"  合理公式预估 (+2/chg): {expected_reasonable}")
    print(f"  实际 score_after:        {score_after}")

    if changes_count >= 4 and score_delta >= 12:
        print(f"  ❌ 通胀暴露：{changes_count} 处修复 → +{score_delta} 分（单处 +{score_delta/changes_count:.1f}）")
    elif changes_count >= 3 and score_delta >= 9:
        print(f"  🟡 可能通胀：{changes_count} 处修复 → +{score_delta} 分")
    elif changes_count > 0:
        print(f"  ✅ 提升合理：{changes_count} 处修复 → +{score_delta} 分（单处 +{score_delta/changes_count:.1f}）")
    else:
        print("  ⏭ 无修复，跳过")

    # 检测 3: 不应从低分直接跳到接近满分
    print()
    print("--- 检测 3: 极端跳跃检测 ---")
    if score_before < 40 and score_after > 80:
        print(f"  ❌ 极端跳跃：{score_before}→{score_after}（+{score_delta}），烂代码不应接近满分")
    elif score_before < 50 and score_after > 75:
        print(f"  🟡 较大跳跃：{score_before}→{score_after}（+{score_delta}），关注是否过度")
    else:
        print(f"  ✅ 无极端跳跃")

    # 检测 4: 单 change 贡献
    print()
    print("--- 检测 4: 单 change 平均贡献 ---")
    if changes_count > 0 and score_delta > 0:
        per_fix = score_delta / changes_count
        print(f"  每处修复: +{per_fix:.1f} 分")
        if per_fix > 3:
            print(f"  ❌ 异常：单处 +{per_fix:.1f} > 3")
        elif per_fix > 2:
            print(f"  🟡 偏高：单处 +{per_fix:.1f} > 2")
        else:
            print(f"  ✅ 合理")
    else:
        print("  ⏭ 跳过")

    # 检测 5: critic 评分透传
    print()
    print("--- 检测 5: critic → report 评分一致性 ---")
    if critic and hasattr(critic, 'score_before'):
        print(f"  critic.score_before: {critic.score_before}")
        print(f"  report.score_before: {score_before}")
        diff = abs(critic.score_before - score_before)
        if diff == 0:
            print("  ✅ 一致")
        else:
            print(f"  ❌ 不一致（差 {diff}）")
    else:
        print("  ⚠️ 无 critic_summary")

    print()
    print("=== B03 验证 #02 完成 ===")
