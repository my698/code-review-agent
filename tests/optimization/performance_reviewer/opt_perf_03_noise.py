"""
性能审查员优化测试 #03：噪音过滤 —— 不应触发性能问题的代码

5 个代码样本，均不包含可从代码直接确认的性能低效模式。

用法：python tests/optimization/performance_reviewer/opt_perf_03_noise.py
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

SAMPLES = {
    "固定小数据循环": """
def load_configs():
    configs = {}
    for name in ["db", "cache", "api", "log", "email"]:
        configs[name] = read_config(name)
    return configs
""",
    "一次性初始化加载": """
import json

def init_app(config_path):
    with open(config_path) as f:
        config = json.load(f)
    return config
""",
    "O(n) 单次线性遍历": """
def count_active(users):
    total = 0
    for u in users:
        if u.status == "active":
            total += 1
    return total
""",
    "合理使用 join 拼接": """
def format_names(users):
    return ", ".join(u.name for u in users)
""",
    "set 优化后的 O(1) 查找": """
def get_active(users, active_ids):
    active_set = set(active_ids)
    return [u for u in users if u.id in active_set]
""",
}


async def run_sample(app, code: str, label: str):
    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = code
    cfg = {"configurable": {"thread_id": f"opt-perf-03-{label}"}}

    state = {}
    async for event in app.astream_events(initial_state, cfg, version="v2"):
        kind = event["event"]
        name = event.get("name", "")
        if kind == "on_chain_end" and name:
            output = event["data"].get("output", {})
            if isinstance(output, dict):
                for k, v in output.items():
                    if k in AgentState.__annotations__:
                        state[k] = v

    for _ in range(15):
        snap = app.get_state(cfg)
        if not snap.next:
            break
        app.update_state(cfg, {"human_feedback": ""})
        async for event in app.astream_events(None, cfg, version="v2"):
            kind = event["event"]
            name = event.get("name", "")
            if kind == "on_chain_end" and name:
                output = event["data"].get("output", {})
                if isinstance(output, dict):
                    for k, v in output.items():
                        if k in AgentState.__annotations__:
                            state[k] = v

    return state.get("final_report"), state.get("review_results", [])


async def main():
    print("=== 性能审查员优化测试 #03：噪音过滤 ===")
    print(f"  LLM 模型: {LLM_MODEL}")
    print(f"  最大重试: {MAX_RETRY}")
    print()
    print("  预期：这些样本不应触发性能审查员报告问题（空列表或仅 LOW）")
    print()

    print("正在构建工作流图...")
    app = build_graph()
    print("图编译完成")
    print()

    results = {}
    total_start = time.time()

    for label, code in SAMPLES.items():
        print(f"--- {label} ---")
        print(code.strip())
        print(f"  ⏵ 审查中...")
        t0 = time.time()
        report, review_results = await run_sample(app, code, label)
        elapsed = time.time() - t0
        results[label] = (report, review_results, elapsed)

        if report:
            perf_issues = []
            for rr in review_results:
                dim_val = rr.dimension.value if hasattr(rr.dimension, 'value') else str(rr.dimension)
                if dim_val == 'performance':
                    perf_issues = rr.issues
                    break

            high_issues = [i for i in perf_issues if i.severity.value in ("critical", "high")]
            print(f"  ⏹ 完成 ({elapsed:.1f}s)")
            if not perf_issues:
                print(f"     ✅ 性能审查员无问题报告")
            else:
                print(f"     性能审查员报告 {len(perf_issues)} 个问题:")
                for iss in perf_issues:
                    print(f"       [{iss.severity.value}] L{iss.lineno} {iss.category.value}: {iss.description[:60]}")
                if high_issues:
                    print(f"     ⚠️ 含 {len(high_issues)} 个 CRITICAL/HIGH 问题（可能误报）")
            print(f"     status={report.status}  score: {report.score_before}→{report.score_after}")
        else:
            print(f"  ⏹ 完成 ({elapsed:.1f}s) — 报告未生成")
        print()

    total_elapsed = time.time() - total_start

    print("=== 节点耗时统计 ===")
    for label, (_, _, elapsed) in results.items():
        print(f"  {label:30s} {elapsed:.1f}s")
    print(f"  {'总计':30s} {total_elapsed:.1f}s")

    print()
    print("=== 最终审查报告 ===")
    # 统计：不应有 CRITICAL/HIGH 误报
    false_high = 0
    total_perf_issues = 0
    for label, (_, review_results, _) in results.items():
        for rr in review_results:
            dim_val = rr.dimension.value if hasattr(rr.dimension, 'value') else str(rr.dimension)
            if dim_val == 'performance':
                total_perf_issues += len(rr.issues)
                false_high += sum(1 for i in rr.issues if i.severity.value in ("critical", "high"))

    print(f"  样本数: {len(results)}")
    print(f"  性能审查员共报告: {total_perf_issues} 个问题")
    print(f"  其中 CRITICAL/HIGH 误报: {false_high}")

    # 误报 ≤ 1 个视为通过（容错）
    if false_high == 0:
        status = "success"
    elif false_high <= 2:
        status = "partial"
    else:
        status = "failed"
    print(f"  状态: {status}")
    sys.exit(0 if status == "success" else 1)


if __name__ == "__main__":
    asyncio.run(main())
