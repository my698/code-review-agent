"""
性能审查员优化测试 #02：真阳性检测 —— 确认型性能问题

5 个代码样本，每个包含至少一个可从代码直接确认的性能低效模式。

用法：python tests/optimization/performance_reviewer/opt_perf_02_detect.py
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
    "嵌套循环 O(n²)": """
def find_duplicates(items):
    result = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if items[i] == items[j] and items[i] not in result:
                result.append(items[i])
    return result
""",
    "循环内字符串 += 拼接": """
def build_report(records):
    text = ""
    for r in records:
        text += f"ID:{r['id']}, Name:{r['name']}\\n"
    return text
""",
    "循环内 N+1 查询": """
def get_users_with_orders(db):
    users = db.query("SELECT * FROM users")
    result = []
    for u in users:
        orders = db.query(f"SELECT * FROM orders WHERE user_id = {u['id']}")
        result.append({"user": u, "orders": orders})
    return result
""",
    "不必要的中介列表": """
def total_price(items):
    prices = [item.price for item in items]
    return sum(prices)
""",
    "列表查成员 O(n)": """
def get_unique_names(users, whitelist):
    result = []
    for u in users:
        if u.name in whitelist and u.name not in [r.name for r in result]:
            result.append(u)
    return result
""",
}


async def run_sample(app, code: str, label: str):
    """运行单个样本端到端，返回最终报告"""
    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = code
    cfg = {"configurable": {"thread_id": f"opt-perf-02-{label}"}}

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
    print("=== 性能审查员优化测试 #02：真阳性检测 ===")
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

        if report:
            # 提取性能审查员的 issues
            perf_issues = []
            for rr in review_results:
                dim_val = rr.dimension.value if hasattr(rr.dimension, 'value') else str(rr.dimension)
                if dim_val == 'performance':
                    perf_issues = rr.issues
                    break

            print(f"  ⏹ 完成 ({elapsed:.1f}s)")
            print(f"     性能审查员发现 {len(perf_issues)} 个问题:")
            for iss in perf_issues:
                print(f"       [{iss.severity.value}] L{iss.lineno} {iss.category.value}: {iss.description[:60]}")
                if iss.estimated_impact:
                    print(f"       预估影响: {iss.estimated_impact}")
            print(f"     status={report.status}  score: {report.score_before}→{report.score_after}")
        else:
            print(f"  ⏹ 完成 ({elapsed:.1f}s) — 报告未生成")
        print()

    total_elapsed = time.time() - total_start

    print("=== 节点耗时统计 ===")
    for label, (_, _, elapsed) in results.items():
        print(f"  {label:30s} {elapsed:.1f}s")
    print(f"  {'总计':30s} {total_elapsed:.1f}s")

    # 汇总
    print()
    print("=== 最终审查报告 ===")
    def _get_perf(rev_results):
        for rr in rev_results:
            dim_val = rr.dimension.value if hasattr(rr.dimension, 'value') else str(rr.dimension)
            if dim_val == 'performance':
                return rr
        return None

    all_perf_issues = 0
    for _, (_, review_results, _) in results.items():
        pr = _get_perf(review_results)
        if pr:
            all_perf_issues += len(pr.issues)

    print(f"  样本数: {len(results)}")
    print(f"  性能审查员共发现问题: {all_perf_issues}")
    detected = sum(1 for _, (_, rrs, _) in results.items()
                   if _get_perf(rrs) and len(_get_perf(rrs).issues) > 0)
    print(f"  检出样本数: {detected}/{len(results)}")
    print(f"  检出率: {detected}/{len(results)}")

    status = "success" if detected >= 3 else "partial" if detected >= 1 else "failed"
    print(f"  状态: {status}")
    sys.exit(0 if status == "success" else 1)


if __name__ == "__main__":
    asyncio.run(main())
