"""
全量测试 #03：修复与评分 —— coder 行为 + 评分公式

覆盖：B01/B03 coder 越界防止 + score 公式验证

用法：python tests/bugfix/full_suite_03_fix_score.py
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
    "干净代码（不应过度修复）": """
def add(a, b):
    return a + b

def subtract(a, b):
    return a - b

def multiply(a, b):
    return a * b
""",
    "性能问题": """
def find_duplicates(items):
    result = []
    for i in range(len(items)):
        for j in range(len(items)):
            if i != j and items[i] == items[j] and items[i] not in result:
                result.append(items[i])
    return result
""",
    "评分公式-多问题": """
def calc_sm(a,b,c):
    x=a+b+c
    y=x/3
    return y

def GetData(Query):
    import json
    d=json.loads(Query)
    return d["result"]
""",
    "评分公式-低分": """
def f(x):
    return eval(x)
""",
    "评分公式-干净": """
def greet(name):
    return f"Hello, {name}!"

def add(a, b):
    return a + b
""",
}


async def run_sample(app, code, label):
    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = code
    cfg = {"configurable": {"thread_id": f"full-suite-03-{label}"}}

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

    return state.get("final_report"), state.get("coder_result"), state.get("critic_summary")


async def main():
    print("=== 代码审查 Agent 全量测试 #03：修复与评分 ===")
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
        print(code.strip())
        print(f"  ⏵ 审查中...")
        t0 = time.time()
        report, coder, critic = await run_sample(app, code, label)
        elapsed = time.time() - t0
        results[label] = (report, coder, critic, elapsed)
        if report:
            print(f"  ⏹ 完成 ({elapsed:.1f}s)")
            print(f"     status={report.status}  score: {report.score_before}→{report.score_after}")
            print(f"     sandbox_passed={report.sandbox_passed}  retry={report.retry_count}")
            if coder:
                changes = len(coder.changes) if coder.changes else 0
                skipped = len(coder.skipped_items) if coder.skipped_items else 0
                print(f"     changes={changes}  skipped={skipped}")
        else:
            print(f"  ⏹ 完成 ({elapsed:.1f}s) — 报告未生成")
        print()

    total_elapsed = time.time() - total_start

    # --- score formula checks ---
    print("=== [B03] 评分公式检测 ===")
    for label, (report, coder, critic, _) in results.items():
        if not report or not coder:
            continue
        changes_count = len(coder.changes) if coder.changes else 0
        sb = report.score_before
        sa = report.score_after
        delta = sa - sb
        print(f"  {label}:")
        print(f"    score {sb}→{sa} (Δ{delta:+d})")
        print(f"    changes={changes_count}  sandbox_passed={report.sandbox_passed}  retry={report.retry_count}")
        # verify formula
        if report.sandbox_passed and changes_count > 0:
            expected_max_delta = min(changes_count * 2, (100 - sb) // 2)
            if delta >= 0 and delta <= expected_max_delta:
                print(f"    ✅ 加分合理 (max={expected_max_delta}, actual={delta})")
            else:
                print(f"    ❌ 加分异常 (allowed 0~{expected_max_delta}, actual={delta})")
        elif not report.sandbox_passed:
            if delta <= 0:
                print(f"    ✅ 失败已扣分")
            else:
                print(f"    ❌ 失败未扣分 (delta={delta})")

    print()
    print("=== 最终审查报告 ===")
    print(f"  样本数: {len(results)}")
    print(f"  总耗时: {total_elapsed:.1f}s")
    for label, (_, _, _, elapsed) in results.items():
        print(f"    {label:30s} {elapsed:.1f}s")

    success_count = sum(1 for r, _, _, _ in results.values() if r and r.status in ("success", "partial"))
    failed_count = sum(1 for r, _, _, _ in results.values() if r and r.status == "failed")
    print(f"  通过: {success_count}")
    print(f"  失败: {failed_count}")
    print(f"  状态: {'success' if failed_count == 0 else 'partial'}")

    sys.exit(0 if failed_count == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
