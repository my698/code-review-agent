"""
风格审查员优化测试 #03：噪音过滤 —— 不应触发风格问题的代码

5 个代码样本，不包含客观风格违规，或仅为个人偏好级别。

用法：python tests/optimization/style_reviewer/opt_style_03_noise.py
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
    "规范的完整函数": """
def fetch_user(user_id: int) -> dict | None:
    \"\"\"从数据库获取用户信息，不存在则返回 None。\"\"\"
    result = db.query("SELECT * FROM users WHERE id = ?", (user_id,))
    return result[0] if result else None
""",
    "单行工具函数": """
def add(a: int, b: int) -> int:
    \"\"\"返回两数之和。\"\"\"
    return a + b
""",
    "标准异常处理": """
def read_config(path: str) -> dict:
    \"\"\"读取 JSON 配置文件。\"\"\"
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid config: {path}") from e
""",
    "合理的命名约定": """
class UserSession:
    \"\"\"管理用户会话状态。\"\"\"

    def __init__(self, user_id: int):
        self.user_id = user_id
        self.created_at = time.time()

    def is_expired(self, timeout: int = 3600) -> bool:
        return time.time() - self.created_at > timeout
""",
    "Pythonic 惯用写法": """
def filter_active(items: list[dict]) -> list[dict]:
    \"\"\"过滤出活跃状态的条目。\"\"\"
    return [item for item in items if item.get("status") == "active"]
""",
}


async def run_sample(app, code: str, label: str):
    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = code
    cfg = {"configurable": {"thread_id": f"opt-style-03-{label}"}}

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
    print("=== 风格审查员优化测试 #03：噪音过滤 ===")
    print(f"  LLM 模型: {LLM_MODEL}")
    print(f"  最大重试: {MAX_RETRY}")
    print()
    print("  预期：这些样本不应触发风格审查员报告 HIGH 问题")
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
            high_issues = [i for i in style_issues if i.severity.value == "high"]
            print(f"  ⏹ 完成 ({elapsed:.1f}s)")
            if not style_issues:
                print(f"     ✅ 风格审查员无问题报告")
            else:
                print(f"     风格审查员报告 {len(style_issues)} 个问题:")
                for iss in style_issues:
                    print(f"       [{iss.severity.value}] L{iss.lineno} {iss.category.value}: {iss.description[:60]}")
                if high_issues:
                    print(f"     ⚠️ 含 {len(high_issues)} 个 HIGH 问题（可能误报）")
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
    false_high = 0
    total_style_issues = 0
    for label, (_, review_results, _) in results.items():
        sr = _get_style(review_results)
        if sr:
            total_style_issues += len(sr.issues)
            false_high += sum(1 for i in sr.issues if i.severity.value == "high")

    print(f"  样本数: {len(results)}")
    print(f"  风格审查员共报告: {total_style_issues} 个问题")
    print(f"  其中 HIGH 误报: {false_high}")

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
