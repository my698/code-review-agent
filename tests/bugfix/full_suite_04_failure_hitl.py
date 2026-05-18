"""
全量测试 #04：异常与 HITL —— 失败路径 + 重试 + 人工介入

覆盖：B04/B05 sandbox 失败路由 + retry 机制 + HITL 断点

用法：python tests/bugfix/full_suite_04_failure_hitl.py
"""
import sys
import asyncio
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message=".*allowed_objects.*")
import logging
logging.getLogger("langgraph.checkpoint.serde.jsonplus").setLevel(logging.ERROR)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_DIR = str(PROJECT_ROOT / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from config import LLM_MODEL, MAX_RETRY
from graph.builder import build_graph
from graph.state import INITIAL_STATE, AgentState

SAMPLES = {
    "os.popen 易错修复": """
import os

DB_PASSWORD = "admin123"

def list_users(filter_role=None):
    cmd = "SELECT * FROM users"
    if filter_role:
        cmd += " WHERE role = '%s'" % filter_role
    return os.popen("mysql -e \\"%s\\"" % cmd).read()

def restart_service(name):
    if name in ["web", "worker", "scheduler"]:
        os.popen("sudo systemctl restart %s" % name)
    return True
""",
    "exec + 格式化混合": """
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
""",
    "warning 触发代码": """
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
""",
}


async def run_sample(app, code, label):
    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = code
    cfg = {"configurable": {"thread_id": f"full-suite-04-{label}"}}

    # track visited nodes
    visited = []
    state = {}

    async for event in app.astream_events(initial_state, cfg, version="v2"):
        kind = event["event"]
        name = event.get("name", "")
        if kind == "on_chain_start" and name:
            pass  # too noisy for E2E
        elif kind == "on_chain_end" and name:
            visited.append(name)
            output = event["data"].get("output", {})
            if isinstance(output, dict):
                for k, v in output.items():
                    if k in AgentState.__annotations__:
                        state[k] = v

    # resume all HITL pauses
    pause_count = 0
    for _ in range(15):
        snap = app.get_state(cfg)
        if not snap.next:
            break
        pause_count += 1
        app.update_state(cfg, {"human_feedback": ""})
        async for event in app.astream_events(None, cfg, version="v2"):
            kind = event["event"]
            name = event.get("name", "")
            if kind == "on_chain_end" and name:
                visited.append(name)
                output = event["data"].get("output", {})
                if isinstance(output, dict):
                    for k, v in output.items():
                        if k in AgentState.__annotations__:
                            state[k] = v

    return state.get("final_report"), state.get("sandbox_result"), visited, pause_count


async def main():
    print("=== 代码审查 Agent 全量测试 #04：异常与 HITL ===")
    print(f"  LLM 模型: {LLM_MODEL}")
    print(f"  最大重试: {MAX_RETRY}")
    print()

    print("正在构建工作流图...")
    app = build_graph()
    print("图编译完成，10 个节点 + 条件边已就位")
    print()

    results = {}
    total_start = time.time()

    for label, code in SAMPLES.items():
        print(f"--- {label} ---")
        print(f"  ⏵ 审查中...")
        t0 = time.time()
        report, sandbox, visited, pauses = await run_sample(app, code, label)
        elapsed = time.time() - t0
        results[label] = (report, sandbox, visited, pauses, elapsed)

        if report:
            print(f"  ⏹ 完成 ({elapsed:.1f}s)")
            print(f"     status={report.status}  score: {report.score_before}→{report.score_after}")
            print(f"     sandbox_passed={report.sandbox_passed}  retry={report.retry_count}")
            print(f"     HITL 暂停: {pauses} 次")

            # B04 check: did failure path reach human_review?
            has_reflect = "reflect_node" in visited
            has_human = "human_review" in visited
            if has_reflect:
                if has_human:
                    print(f"     ✅ 失败路径经过 human_review（B04 修复生效）")
                else:
                    print(f"     ⚠ reflect 出现但 human_review 未出现（可能重试未耗尽）")
            elif not has_reflect and report.sandbox_passed:
                print(f"     ✅ 沙箱通过，未触发失败路径")
        else:
            print(f"  ⏹ 完成 ({elapsed:.1f}s) — 报告未生成")
        print()

    total_elapsed = time.time() - total_start

    print("=== 最终审查报告 ===")
    print(f"  样本数: {len(results)}")
    print(f"  总耗时: {total_elapsed:.1f}s")

    success_count = sum(1 for r, _, _, _, _ in results.values() if r and r.status in ("success", "partial"))
    failed_count = sum(1 for r, _, _, _, _ in results.values() if r and r.status == "failed")
    print(f"  通过: {success_count}")
    print(f"  失败: {failed_count}")

    # B04/B05 specific
    for label, (report, sandbox, visited, pauses, elapsed) in results.items():
        has_reflect = "reflect_node" in visited
        has_human_after_reflect = False
        if has_reflect:
            reflect_idx = max(i for i, n in enumerate(visited) if n == "reflect_node")
            human_idx = max((i for i, n in enumerate(visited) if n == "human_review"), default=-1)
            has_human_after_reflect = human_idx > reflect_idx
        print(f"  {label}: reflect={has_reflect}  human_after_reflect={has_human_after_reflect}  pauses={pauses}")

    print(f"  状态: {'success' if failed_count == 0 else 'partial'}")
    sys.exit(0 if failed_count == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
