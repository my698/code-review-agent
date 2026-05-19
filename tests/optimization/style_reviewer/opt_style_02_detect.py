"""
风格审查员优化测试 #02：真阳性检测 —— 客观风格违规

5 个代码样本，每个包含至少一个可从代码直接确认的客观风格违规。

用法：python tests/optimization/style_reviewer/opt_style_02_detect.py
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
    "bare except": """
def load_data(path):
    try:
        with open(path) as f:
            return f.read()
    except:
        return None
""",
    "命名违反 convention": """
class user_manager:
    def GetUserList(self, db_connection):
        result = db_connection.execute("SELECT * FROM users")
        return result
""",
    "缺少文档字符串和类型注解": """
def calculate(a, b, mode):
    if mode == 1:
        return a + b
    elif mode == 2:
        return a - b
    return 0
""",
    "注释与代码矛盾": """
def retry_request(url, max_tries=3):
    # try at most 5 times
    for i in range(max_tries):
        try:
            return fetch(url)
        except Exception:
            pass
    return None
""",
    "copy-paste 重复": """
def validate_email(value):
    if "@" not in value:
        return False
    if "." not in value:
        return False
    return True

def validate_domain(value):
    if "@" in value:
        return False
    if "." not in value:
        return False
    return True
""",
}


async def run_sample(app, code: str, label: str):
    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = code
    cfg = {"configurable": {"thread_id": f"opt-style-02-{label}"}}

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
    print("=== 风格审查员优化测试 #02：真阳性检测 ===")
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
            style_rr = _get_style(review_results)
            style_issues = style_rr.issues if style_rr else []
            print(f"  ⏹ 完成 ({elapsed:.1f}s)")
            print(f"     风格审查员发现 {len(style_issues)} 个问题:")
            for iss in style_issues:
                pep8 = f" [{iss.pep8_ref}]" if iss.pep8_ref else ""
                print(f"       [{iss.severity.value}] L{iss.lineno} {iss.category.value}{pep8}: {iss.description[:60]}")
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
    all_style_issues = 0
    for _, (_, review_results, _) in results.items():
        sr = _get_style(review_results)
        if sr:
            all_style_issues += len(sr.issues)

    print(f"  样本数: {len(results)}")
    print(f"  风格审查员共发现问题: {all_style_issues}")
    detected = sum(1 for _, (_, rrs, _) in results.items()
                   if _get_style(rrs) and len(_get_style(rrs).issues) > 0)
    print(f"  检出样本数: {detected}/{len(results)}")
    print(f"  检出率: {detected}/{len(results)}")

    status = "success" if detected >= 3 else "partial" if detected >= 1 else "failed"
    print(f"  状态: {status}")
    sys.exit(0 if status == "success" else 1)


if __name__ == "__main__":
    asyncio.run(main())
