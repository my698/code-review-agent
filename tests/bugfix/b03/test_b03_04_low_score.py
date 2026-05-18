"""
B03 验证脚本 #04：低分样本极端跳跃 — 评分边界检测

构造严重问题代码（多漏洞），critic 打分应很低（<30）。
验证 score_before 是否如实反映代码质量低，且 score_after 不因多处修复就跳至接近满分。

用法：python tests/bugfix/b03/test_b03_04_low_score.py
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

# 严重问题样本：SQL 注入 + 硬编码密码 + eval + 裸 except + 无格式
# 应得极低分（<30），但修复后不应跳至接近满分
SAMPLE_CODE = '''
import sqlite3
import os
import subprocess

PASSWORD="admin123"
API_KEY="sk-abc123xyz"

def login(user,pwd):
    conn=sqlite3.connect("app.db")
    cur=conn.cursor()
    cur.execute("SELECT * FROM users WHERE user='"+user+"' AND pwd='"+pwd+"'")
    return cur.fetchone()

def run_cmd(cmd):
    os.system(cmd)

def calc(x):
    return eval(x)

def read_file(path):
    f=open(path)
    data=f.read()
    f.close()
    return data
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
    print(f"=== B03 验证 #04：低分样本极端跳跃检测 ===")
    print(f"  LLM 模型: {LLM_MODEL}")
    print()

    print("正在构建工作流图...")
    app = build_graph()
    print("图编译完成")
    print()

    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = SAMPLE_CODE
    config = {"configurable": {"thread_id": "b03-test-004"}}

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
    skipped = coder.skipped_items if coder else []

    print(f"  score_before:  {score_before}")
    print(f"  score_after:   {score_after}")
    print(f"  score delta:   {score_after - score_before:+d}")
    print(f"  coder 修复:    {changes_count} 处")
    print(f"  skipped:       {len(skipped)} 条")
    print(f"  status:        {status}")

    # 检测 1: 低分样本应得低分
    print()
    print("--- 检测 1: 低分样本的 score_before ---")
    if score_before <= 20:
        print(f"  ✅ score_before={score_before}，正确反映代码极差")
    elif score_before <= 40:
        print(f"  🟡 score_before={score_before}，偏低但不够低（多漏洞代码应 <30）")
    else:
        print(f"  ❌ score_before={score_before}，多漏洞代码不应得高分")

    # 检测 2: 关键 —— 低分不应跳到接近满分
    print()
    print("--- 检测 2: 极端跳跃（B03 核心检测） ---")
    score_delta = score_after - score_before
    jump_ratio = score_after / max(score_before, 1)

    print(f"  跳跃幅度: +{score_delta} (x{jump_ratio:.1f})")
    if score_delta > 40:
        print(f"  ❌ 极端通胀：{score_before}→{score_after} (+{score_delta})")
        print(f"     预期合理区间：{score_before}→{min(score_before + changes_count * 2, score_before + 25)}")
    elif score_before < 30 and score_after > 65:
        print(f"  ❌ 评分跳跃过大：低分{score_before}→高分{score_after}")
    elif score_before < 30 and score_after > 50:
        print(f"  🟡 评分跳跃偏大：{score_before}→{score_after}")
    else:
        print(f"  ✅ 评分变化在合理范围")

    # 检测 3: 与 critic 原始评分对比
    print()
    print("--- 检测 3: critic 评分透传 ---")
    if critic and hasattr(critic, 'score_before'):
        print(f"  critic 评分: {critic.score_before}")
        print(f"  total_issues: {critic.total_issues}")
        print(f"  严重度分布: {critic.by_severity}")
        if critic.score_before != score_before:
            print(f"  ❌ 不一致：critic={critic.score_before} vs report={score_before}")
        else:
            print(f"  ✅ 一致")

    # 检测 4: 单修复贡献
    print()
    print("--- 检测 4: 单修复贡献（修复后） ---")
    if changes_count > 0 and score_delta > 0:
        per_fix = score_delta / changes_count
        print(f"  {changes_count} 处修复，每处 +{per_fix:.1f} 分")
        if per_fix > 3:
            print(f"  ❌ 当前公式 +3/chg 导致每处贡献过高")
        else:
            print(f"  ✅ 每处贡献可接受")
    else:
        print("  ⏭ 无修复或无分数变化")

    # 检测 5: skipped_items 对评分的影响
    print()
    print("--- 检测 5: 跳过项展示 ---")
    if skipped:
        print(f"  {len(skipped)} 条需人工介入或跳过：")
        for s in skipped[:5]:
            print(f"    - {s[:120]}")
    else:
        print("  无跳过项")

    print()
    print("=== B03 验证 #04 完成 ===")
