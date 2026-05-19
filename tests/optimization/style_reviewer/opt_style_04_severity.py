"""
风格审查员优化测试 #04：严重度校准 —— HIGH/MEDIUM/LOW 分级验证

3 个样本，分别对应 3 个严重度级别。

用法：python tests/optimization/style_reviewer/opt_style_04_severity.py
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
    "HIGH: bare except 吞异常": """
def process_items(items):
    for item in items:
        try:
            handle(item)
        except:
            pass
""",
    "MEDIUM: PEP 8 命名违规": """
def GetDataFromAPI(UrlString, ApiKey):
    import requests
    response = requests.get(UrlString, headers={"Authorization": ApiKey})
    return response.json()
""",
    "LOW: 缺少文档字符串": """
def clamp(value, lo, hi):
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value
""",
}

EXPECTED = {
    "HIGH: bare except 吞异常": "high",
    "MEDIUM: PEP 8 命名违规": "medium",
    "LOW: 缺少文档字符串": "low",
}


async def run_sample(app, code: str, label: str):
    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = code
    cfg = {"configurable": {"thread_id": f"opt-style-04-{label}"}}

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


def _get_style(rev_results):
    for rr in rev_results:
        dim_val = rr.dimension.value if hasattr(rr.dimension, 'value') else str(rr.dimension)
        if dim_val == 'style':
            return rr
    return None


async def main():
    print("=== 风格审查员优化测试 #04：严重度校准 ===")
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
            style_rr = _get_style(review_results)
            style_issues = style_rr.issues if style_rr else []

            print(f"  ⏹ 完成 ({elapsed:.1f}s)")
            print(f"     预期严重度: {expected_sev}")
            if style_issues:
                max_sev = max(
                    style_issues,
                    key=lambda i: {"high": 3, "medium": 2, "low": 1}.get(i.severity.value, 0)
                )
                print(f"     实际最高严重度: {max_sev.severity.value}")
                for iss in style_issues:
                    pep8 = f" [{iss.pep8_ref}]" if iss.pep8_ref else ""
                    match = "✅" if iss.severity.value == expected_sev else " "
                    print(f"       {match} [{iss.severity.value}] L{iss.lineno} {iss.category.value}{pep8}: {iss.description[:60]}")
            else:
                print(f"     实际: 无风格问题报告 ❌")
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
    severities = {"high": 3, "medium": 2, "low": 1}
    match_count = 0
    total = 0
    for label, (_, review_results, _) in results.items():
        expected_sev = EXPECTED[label]
        style_rr = _get_style(review_results)
        if style_rr:
            total += 1
            if style_rr.issues:
                max_sev = max(style_rr.issues, key=lambda i: severities.get(i.severity.value, 0))
                actual = max_sev.severity.value
            else:
                actual = "none"
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
