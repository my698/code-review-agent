"""
全量测试 #02：安全审查 —— 注入/凭据/反序列化/命令执行

覆盖：B01/B02 安全相关样本，验证 security_reviewer + coder 行为

用法：python tests/bugfix/full_suite_02_security.py
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
    "SQL 注入": """
import sqlite3

def get_user(db_path, username):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE name = '%s'" % username)
    return cursor.fetchall()
""",
    "硬编码凭据": """
API_KEY = "sk-abc123def456"

def call_api(endpoint):
    import requests
    return requests.get(endpoint, headers={"Authorization": "Bearer " + API_KEY})
""",
    "命令注入": """
import os

def ping_host(hostname):
    os.system("ping -c 1 " + hostname)
""",
    "反序列化": """
import pickle

def load_user_data(filename):
    data = open(filename, "rb").read()
    return pickle.loads(data)
""",
    "凭据+命令混合": """
DB_PASSWORD = "admin123"

def restart_service(name):
    import os
    if name in ["web", "worker"]:
        os.popen("sudo systemctl restart %s" % name)
    return True
""",
}


async def run_sample(app, config, code, label):
    """运行单个样本，返回最终报告"""
    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = code
    cfg = {"configurable": {"thread_id": f"full-suite-02-{label}"}}

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

    # resume HITL pauses
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

    return state.get("final_report"), state.get("sandbox_result")


async def main():
    print("=== 代码审查 Agent 全量测试 #02：安全审查 ===")
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
        report, sandbox = await run_sample(app, {}, code, label)
        elapsed = time.time() - t0
        results[label] = (report, sandbox, elapsed)
        if report:
            print(f"  ⏹ 完成 ({elapsed:.1f}s)")
            print(f"     status={report.status}  score: {report.score_before}→{report.score_after}")
            print(f"     sandbox_passed={report.sandbox_passed}  retry={report.retry_count}")
            print(f"     action_items={len(report.action_items)}  skipped={len(report.skipped_items)}")
        else:
            print(f"  ⏹ 完成 ({elapsed:.1f}s) — 报告未生成")
        print()

    total_elapsed = time.time() - total_start

    # --- final report ---
    print("=== 节点耗时统计 ===")
    for label, (report, _, elapsed) in results.items():
        print(f"  {label:20s} {elapsed:.1f}s")
    print(f"  {'总计':20s} {total_elapsed:.1f}s")

    print()
    print("=== 最终审查报告 ===")
    success_count = sum(1 for r, _, _ in results.values() if r and r.status in ("success", "partial"))
    failed_count = sum(1 for r, _, _ in results.values() if r and r.status == "failed")
    print(f"  样本数: {len(results)}")
    print(f"  通过:   {success_count}")
    print(f"  失败:   {failed_count}")
    print(f"  状态:   {'success' if failed_count == 0 else 'partial' if success_count > 0 else 'failed'}")

    # score summary
    scores_before = [r.score_before for r, _, _ in results.values() if r]
    scores_after = [r.score_after for r, _, _ in results.values() if r]
    if scores_before and scores_after:
        print(f"  评分区间: {min(scores_before)}-{max(scores_before)} → {min(scores_after)}-{max(scores_after)}")

    sys.exit(0 if failed_count == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
