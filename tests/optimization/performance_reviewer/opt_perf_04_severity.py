"""
性能审查员优化测试 #04：严重度校准 —— CRITICAL/HIGH/MEDIUM/LOW 分级验证

4 个样本，分别对应 4 个严重度级别。

用法：python tests/optimization/performance_reviewer/opt_perf_04_severity.py
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
    "CRITICAL: 嵌套循环无上界 + N+1": """
def process_all_users(db):
    users = db.query("SELECT * FROM users")
    for u in users:
        orders = db.query(f"SELECT * FROM orders WHERE user_id = {u['id']}")
        for o in orders:
            items = db.query(f"SELECT * FROM items WHERE order_id = {o['id']}")
            o['items'] = items
        u['orders'] = orders
    return users
""",
    "HIGH: 循环内重复 IO": """
def read_logs(filenames):
    results = []
    for fname in filenames:
        with open(fname) as f:
            data = f.read()
        parsed = parse_log(data)
        results.extend(parsed)
    return results
""",
    "MEDIUM: 低效数据结构": """
def get_valid_ids(all_ids, blocked_ids):
    valid = []
    for aid in all_ids:
        if aid in blocked_ids:
            continue
        valid.append(aid)
    return valid
""",
    "LOW: 微小优化点": """
def format_price(amount):
    return str(round(amount, 2))
""",
}


async def run_sample(app, code: str, label: str):
    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = code
    cfg = {"configurable": {"thread_id": f"opt-perf-04-{label}"}}

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


EXPECTED = {
    "CRITICAL: 嵌套循环无上界 + N+1": "critical",
    "HIGH: 循环内重复 IO": "high",
    "MEDIUM: 低效数据结构": "medium",
    "LOW: 微小优化点": "low",
}


async def main():
    print("=== 性能审查员优化测试 #04：严重度校准 ===")
    print(f"  LLM 模型: {LLM_MODEL}")
    print(f"  最大重试: {MAX_RETRY}")
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

        expected_sev = EXPECTED[label]
        if report:
            perf_issues = []
            for rr in review_results:
                if hasattr(rr, 'dimension') and (rr.dimension.value if hasattr(rr.dimension, 'value') else str(rr.dimension)) == 'performance':
                    perf_issues = rr.issues
                    break

            print(f"  ⏹ 完成 ({elapsed:.1f}s)")
            print(f"     预期严重度: {expected_sev}")
            if perf_issues:
                max_sev = max(
                    perf_issues,
                    key=lambda i: {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(i.severity.value, 0)
                )
                print(f"     实际最高严重度: {max_sev.severity.value}")
                for iss in perf_issues:
                    match = "✅" if iss.severity.value == expected_sev else " "
                    print(f"       {match} [{iss.severity.value}] L{iss.lineno} {iss.category.value}: {iss.description[:60]}")
            else:
                print(f"     实际: 无性能问题报告 ❌")
            print(f"     status={report.status}  score: {report.score_before}→{report.score_after}")
        else:
            print(f"  ⏹ 完成 ({elapsed:.1f}s) — 报告未生成")
        print()

    total_elapsed = time.time() - total_start

    print("=== 节点耗时统计 ===")
    for label, (_, _, elapsed) in results.items():
        print(f"  {label:40s} {elapsed:.1f}s")
    print(f"  {'总计':40s} {total_elapsed:.1f}s")

    print()
    print("=== 最终审查报告 ===")
    severities = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    match_count = 0
    total = 0
    for label, (_, review_results, _) in results.items():
        expected_sev = EXPECTED[label]
        for rr in review_results:
            if hasattr(rr, 'dimension') and (rr.dimension.value if hasattr(rr.dimension, 'value') else str(rr.dimension)) == 'performance':
                total += 1
                if rr.issues:
                    max_sev = max(rr.issues, key=lambda i: severities.get(i.severity.value, 0))
                    actual = max_sev.severity.value
                else:
                    actual = "none"
                # 容忍一级偏差（如预期 HIGH 实际 CRITICAL 也算合理）
                sev_diff = abs(severities.get(actual, 0) - severities.get(expected_sev, 0))
                if sev_diff <= 1:
                    match_count += 1
                    print(f"  ✅ {label}: expected={expected_sev} actual={actual}")
                else:
                    print(f"  ❌ {label}: expected={expected_sev} actual={actual} (偏差 > 1 级)")

    print(f"  严重度匹配: {match_count}/{total}")
    status = "success" if match_count >= total * 0.75 else "partial" if match_count >= total * 0.5 else "failed"
    print(f"  状态: {status}")
    sys.exit(0 if status == "success" else 1)


if __name__ == "__main__":
    asyncio.run(main())
