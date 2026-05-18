"""
B04 验证脚本 #03：端到端 —— 追踪失败路径是否到达 human_review

用含 os.popen + 硬编码凭据的代码。os.popen 的修复容易出错：
coder 可能替换为 subprocess.run 但 API 不同（返回值不是文件对象，.read() 会炸）。

用法：python tests/bugfix/b04/test_b04_03_e2e_failure_path.py
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

# 易错样本 #1：os.popen 替换为 subprocess 容易搞错 API
# os.popen("cmd").read() → subprocess.run(["cmd"], capture_output=True).stdout
# 如果 coder 写成 subprocess.run("cmd").read() 或漏了 capture_output 就会炸
# getenv 的第二个参数是可选的，有 bug 的可能
SAMPLE_CODE = """
import os

DB_PASSWORD = "admin123"

def getenv(key):
    return os.environ[key]

def list_users(filter_role=None):
    cmd = "SELECT * FROM users"
    if filter_role:
        cmd += " WHERE role = '%s'" % filter_role
    return os.popen("mysql -e \\"%s\\"" % cmd).read()

def restart_service(name):
    if name in ["web", "worker", "scheduler"]:
        os.popen("sudo systemctl restart %s" % name)
    return True
"""


REAL_NODES = [
    "code_parser", "security_reviewer", "performance_reviewer",
    "style_reviewer", "critic_agent", "coder_agent",
    "sandbox_executor", "reflect_node", "human_review", "output_node",
]


async def run_with_tracking(app, config, initial_state):
    visited = []
    current_state = dict(initial_state) if initial_state else {}
    node_start = {}

    async for event in app.astream_events(initial_state, config, version="v2"):
        kind = event["event"]
        name = event.get("name", "")

        if kind == "on_chain_start" and name in REAL_NODES:
            node_start[name] = time.time()

        elif kind == "on_chain_end" and name in REAL_NODES:
            visited.append(name)
            elapsed = time.time() - node_start.pop(name, 0)
            output = event["data"].get("output", {})
            if isinstance(output, dict):
                for k, v in output.items():
                    if k in AgentState.__annotations__:
                        current_state[k] = v
            print(f"  ⏹ [{name}] 完成 ({elapsed:.1f}s)")

    return visited, current_state


def resume_all_pauses(app, config, max_rounds=15):
    """自动批准所有暂停点，收集完整节点序列"""
    all_visited = []
    all_state = {}

    for round_num in range(max_rounds):
        snapshot = app.get_state(config)
        if not snapshot.next:
            break
        state_vals = snapshot.values
        retry = state_vals.get("retry_count", 0)
        sandbox = state_vals.get("sandbox_result", {})
        sandbox_ok = sandbox.passed if sandbox else False

        if retry > 0:
            print(f"  🔄 第 {retry} 次重试后暂停在 human_review (sandbox_passed={sandbox_ok})，自动批准...")
        else:
            print(f"  ⏸ 暂停在 human_review (sandbox_passed={sandbox_ok})，自动批准...")
        app.update_state(config, {"human_feedback": ""})
        visited, state = asyncio.run(run_with_tracking(app, config, None))
        all_visited.extend(visited)
        all_state = state

    return all_visited, all_state


if __name__ == "__main__":
    print(f"=== B04 验证 #03：端到端失败路径追踪 ===")
    print(f"  LLM 模型: {LLM_MODEL}")
    print(f"  最大重试: {MAX_RETRY}")
    print()

    print("正在构建工作流图...")
    app = build_graph()
    print("图编译完成")
    print()

    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = SAMPLE_CODE
    config = {"configurable": {"thread_id": "b04-test-003"}}

    print("=== 待审查代码 ===")
    print(SAMPLE_CODE)

    # 第一段：跑到第一个断点
    visited1, state1 = asyncio.run(run_with_tracking(app, config, initial_state))

    # 后续：自动批准所有暂停
    visited2, final_state = resume_all_pauses(app, config)
    all_visited = visited1 + visited2

    # --- 分析 ---
    print()
    print("=== B04 检测 ===")

    report = final_state.get("final_report", {})
    sandbox = final_state.get("sandbox_result", {})
    retry_count = final_state.get("retry_count", 0)

    if report:
        print(f"  status:        {report.status}")
        print(f"  score_before:  {report.score_before}")
        print(f"  score_after:   {report.score_after}")
        print(f"  retry_count:   {report.retry_count}")
        print(f"  sandbox_passed: {sandbox.passed if sandbox else 'N/A'}")

    # 节点序列
    unique = list(dict.fromkeys(all_visited))
    print(f"\n  完整节点序列: {' → '.join(unique)}")

    # 检测 reflect → human_review 路径
    has_reflect = "reflect_node" in all_visited
    reflect_count = all_visited.count("reflect_node")
    human_count = all_visited.count("human_review")

    print(f"\n  reflect_node 出现: {reflect_count} 次")
    print(f"  human_review 出现: {human_count} 次")

    if has_reflect:
        # 找所有 reflect 之后的节点序列
        reflect_positions = [i for i, n in enumerate(all_visited) if n == "reflect_node"]
        for idx, pos in enumerate(reflect_positions):
            after = all_visited[pos:pos+5]
            print(f"  reflect #{idx+1} 之后: {' → '.join(after)}")
            if "human_review" in after:
                print(f"    ✅ 到达了 human_review")
            elif "coder_agent" in after:
                print(f"    ⏭ 未到上限，进入 coder_agent 重试")
            elif "output_node" in after and "human_review" not in after:
                print(f"    ❌ B04 Bug：直接到 output_node，无人工介入")

    if retry_count >= MAX_RETRY and report and report.status == "failed":
        # 重试耗尽，检查是否经过了 human_review
        last_reflect_idx = max([i for i, n in enumerate(all_visited) if n == "reflect_node"], default=-1)
        last_human_idx = max([i for i, n in enumerate(all_visited) if n == "human_review"], default=-1)
        if last_human_idx > last_reflect_idx:
            print(f"\n  ✅ 重试耗尽后经过了 human_review（人在最终失败前有介入机会）")
        else:
            print(f"\n  ❌ 重试耗尽后未经过 human_review，直接输出失败报告")

    print()
    print(f"=== B04 验证 #03 完成 ===")
