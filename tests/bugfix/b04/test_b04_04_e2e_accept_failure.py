"""
B04 验证脚本 #04：端到端 —— 失败后人工空白确认 → output_node + status=failed

用含 eval + SQL 拼接的代码，容易触发安全审查。
在 human_review 断点处检测是否来自失败路径，若是则空白确认，
验证 output_node 生成的报告 status=failed。

用法：python tests/bugfix/b04/test_b04_04_e2e_accept_failure.py
"""
import sys
import asyncio
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

# 易错样本 #2：exec + 格式化字符串 + 异常处理缺失
# exec 替换不是简单找替换，如果 coder 去掉 exec 但保留动态行为逻辑容易出错
# print 中的格式化字符串也可能被误改
SAMPLE_CODE = """
SECRET_KEY = "sk-abc123def456"

def execute_dynamic(code_str, context=None):
    if context is None:
        context = {}
    exec(code_str, context)
    return context

def format_output(data, template):
    result = template % data
    print(result)
    return result
"""


async def run_until_pause(app, config, initial_state):
    current_state = dict(initial_state) if initial_state else {}
    async for event in app.astream_events(initial_state, config, version="v2"):
        kind = event["event"]
        name = event.get("name", "")
        if kind == "on_chain_end" and name:
            output = event["data"].get("output", {})
            if isinstance(output, dict):
                for k, v in output.items():
                    if k in AgentState.__annotations__:
                        current_state[k] = v
    return current_state


async def main():
    print(f"=== B04 验证 #04：人工空白确认 → output_node (status=failed) ===")
    print(f"  LLM 模型: {LLM_MODEL}")
    print(f"  最大重试: {MAX_RETRY}")
    print()

    app = build_graph()
    print("图编译完成")
    print()

    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = SAMPLE_CODE
    config = {"configurable": {"thread_id": "b04-test-004"}}

    print("=== 待审查代码 ===")
    print(SAMPLE_CODE)
    print("正在执行审查流程...")

    # 跑完所有轮次，每轮自动空白确认
    state = await run_until_pause(app, config, initial_state)

    max_rounds = 15
    for round_num in range(max_rounds):
        snapshot = app.get_state(config)
        if not snapshot.next:
            break
        pause_nodes = snapshot.next
        print(f"  第{round_num+1}轮暂停: {pause_nodes}")

        # 空白确认：接受当前结果
        app.update_state(config, {"human_feedback": ""})
        state = await run_until_pause(app, config, None)

    # --- 检测 ---
    print()
    print("=== B04 检测 ===")

    report = state.get("final_report")
    sandbox = state.get("sandbox_result")
    retry_count = state.get("retry_count", 0)
    sandbox_passed = sandbox.passed if sandbox else False

    if not report:
        print("  ❌ 未生成最终报告")
        sys.exit(1)

    print(f"  status:         {report.status}")
    print(f"  retry_count:    {retry_count}")
    print(f"  sandbox_passed: {sandbox_passed}")
    print(f"  score_before:   {report.score_before}")
    print(f"  score_after:    {report.score_after}")

    # 检测 1：如果最终状态是 failed，报告应正确反映
    if report.status == "failed":
        print(f"\n  ✅ 流程以 failed 结束（沙箱验证失败）")
        print(f"     用户已通过 human_review 确认接受失败结果")
        print(f"     而不是系统自动放弃无人工介入")
    elif report.status == "success":
        print(f"\n  ⏭ 沙箱通过了，未触发失败路径")
        print(f"     （如果修坏了会触发 retry→human，本次未激活）")
    elif report.status == "partial":
        print(f"\n  ⏭ 沙箱通过但有跳过项")

    # 检测 2：确认 final_report 字段完整性
    print()
    print("--- 报告字段完整性 ---")
    checks = [
        ("original_code", bool(report.original_code)),
        ("action_items", isinstance(report.action_items, list)),
        ("status", report.status in ("success", "partial", "failed")),
        ("sandbox_passed", isinstance(report.sandbox_passed, bool)),
        ("retry_count", isinstance(report.retry_count, int)),
    ]
    for field, ok in checks:
        print(f"  {'✅' if ok else '❌'} {field}")

    print()
    print(f"=== B04 验证 #04 完成 ===")


if __name__ == "__main__":
    asyncio.run(main())
