"""
B04 验证脚本 #05：端到端 —— 失败后人工给反馈 → coder_agent 继续修复

在 human_review 断点检测是否来自失败路径，若是则给具体修复指令，
追踪后续是否进入 coder_agent → sandbox_executor 重试链。

用法：python tests/bugfix/b04/test_b04_05_e2e_human_feedback.py
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

# 易错样本 #3：字符串格式化 + 动态属性访问
# % 格式化在 Python 中已不推荐，但代码中混合使用 % 和 f-string
# coder 可能在替换 % 格式化时引入语法错误
# getattr 动态访问可能被误判为安全问题
SAMPLE_CODE = """
API_TOKEN = "tok_deadbeef1234567890"

class ServiceManager:
    def __init__(self):
        self.services = {}

    def call(self, service_name, method, **params):
        svc = self.services.get(service_name)
        if svc is None:
            return None
        func = getattr(svc, method, None)
        if func is None:
            return None
        result = func(**params)
        return result

    def register(self, name, instance):
        self.services[name] = instance
        print("registered %%s" %% name)
"""


async def run_until_pause(app, config, initial_state):
    current_state = dict(initial_state) if initial_state else {}
    async for event in app.astream_events(initial_state, config, version="v2"):
        kind = event["event"]
        name = event.get("name", "")
        if kind == "on_chain_start" and name:
            print(f"  ⏵ [{name}]")
        elif kind == "on_chain_end" and name:
            output = event["data"].get("output", {})
            if isinstance(output, dict):
                for k, v in output.items():
                    if k in AgentState.__annotations__:
                        current_state[k] = v
    return current_state


async def main():
    print(f"=== B04 验证 #05：人工给反馈 → coder_agent 重试 ===")
    print(f"  LLM 模型: {LLM_MODEL}")
    print(f"  最大重试: {MAX_RETRY}")
    print()

    app = build_graph()
    print("图编译完成")
    print()

    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = SAMPLE_CODE
    config = {"configurable": {"thread_id": "b04-test-005"}}

    print("=== 待审查代码 ===")
    print(SAMPLE_CODE)
    print("正在执行审查流程...")

    state = await run_until_pause(app, config, initial_state)

    # 追踪每轮暂停时的状态和决策
    pause_log = []
    max_rounds = 15
    for round_num in range(max_rounds):
        snapshot = app.get_state(config)
        if not snapshot.next:
            break

        pause_nodes = snapshot.next
        current_state = snapshot.values
        sandbox = current_state.get("sandbox_result", {})
        retry = current_state.get("retry_count", 0)
        sandbox_ok = sandbox.passed if sandbox else False

        pause_info = {
            "round": round_num + 1,
            "paused_at": pause_nodes,
            "retry_count": retry,
            "sandbox_passed": sandbox_ok,
        }
        print(f"\n  第{round_num+1}轮暂停: {pause_nodes}")
        print(f"    retry_count={retry}, sandbox_passed={sandbox_ok}")

        # 如果沙箱失败且重试已达上限 → 这是 B04 的关键场景
        if not sandbox_ok and retry >= MAX_RETRY:
            print(f"    🔴 重试耗尽+沙箱失败，给人工反馈...")
            app.update_state(config, {
                "human_feedback": "用 subprocess.run 替换 os.popen，确保 capture_output=True"
            })
            pause_info["action"] = "gave_feedback"
        elif not sandbox_ok and retry > 0:
            print(f"    🟡 沙箱失败但还有重试额度，空白确认让其自动重试...")
            app.update_state(config, {"human_feedback": ""})
            pause_info["action"] = "auto_retry"
        else:
            print(f"    🟢 空白确认...")
            app.update_state(config, {"human_feedback": ""})
            pause_info["action"] = "approved"

        state = await run_until_pause(app, config, None)
        pause_log.append(pause_info)

    # --- 检测 ---
    print()
    print("=== B04 检测 ===")

    report = state.get("final_report")
    if not report:
        print("  ❌ 未生成最终报告")
        sys.exit(1)

    print(f"  最终 status:  {report.status}")
    print(f"  最终 retry:   {report.retry_count}")
    print(f"  sandbox_passed: {report.sandbox_passed}")

    # 检测：失败路径上是否有人工介入机会
    failure_pauses = [p for p in pause_log if not p["sandbox_passed"]]
    if failure_pauses:
        print(f"\n  沙箱失败暂停 {len(failure_pauses)} 次:")
        for p in failure_pauses:
            print(f"    第{p['round']}轮: retry={p['retry_count']}, action={p['action']}")

        # 关键检测：重试耗尽后的暂停点必须是 human_review
        exhausted = [p for p in failure_pauses if p["retry_count"] >= MAX_RETRY]
        for p in exhausted:
            if "human_review" in str(p["paused_at"]):
                print(f"    ✅ 重试耗尽后暂停在 human_review（B04 修复生效）")
            else:
                print(f"    ❌ 重试耗尽后暂停在 {p['paused_at']}，应为 human_review")
    else:
        print(f"\n  ⏭ 无沙箱失败，未触发失败路径")

    # 展示暂停日志摘要
    print(f"\n  完整暂停日志:")
    for p in pause_log:
        print(f"    R{p['round']}: {p['paused_at']} → {p['action']}")

    print()
    print(f"=== B04 验证 #05 完成 ===")


if __name__ == "__main__":
    asyncio.run(main())
