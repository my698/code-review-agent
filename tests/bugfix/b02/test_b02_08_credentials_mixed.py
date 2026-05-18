"""
B02 验证脚本 #08：凭据+反序列化混合样本 — 真漏洞 + 伪模式

混合：硬编码凭据(真) + pickle 反序列化(真) + os.getenv(伪-不是硬编码) + json.load(伪-安全)
验证 security_reviewer 是否能区分真正的硬编码凭据 vs 环境变量读取，
以及不安全的 pickle 反序列化 vs 安全的 json 解析。

检测项：
  1. 真漏洞检出：API_KEY = "sk-abc" → 硬编码凭据
  2. 真漏洞检出：pickle.load(open(x, 'rb')) → 不安全反序列化
  3. 伪模式压制：os.getenv("PASSWORD") → 不是硬编码
  4. 伪模式压制：json.load(open(x)) → 安全，不是反序列化漏洞

用法：python tests/bugfix/b02/test_b02_08_credentials_mixed.py
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

# 混合样本：
# - API_KEY / DB_PASSWORD: 真硬编码凭据 → 应检出敏感信息
# - load_session: pickle.load → 应检出反序列化漏洞
# - get_db_password: os.getenv → 不是硬编码，不应报告
# - load_config: json.load → 安全反序列化，不应报告
# - SECRET_KEY: 也是真硬编码
SAMPLE_CODE = '''
import os
import json
import pickle

API_KEY = "sk-proj-abc123def456ghi789jkl"
DB_PASSWORD = "admin123!"
SECRET_KEY = "my-secret-key-for-jwt"


def load_session(filename):
    """Real unsafe deserialization - pickle.load with external input."""
    with open(filename, "rb") as f:
        return pickle.load(f)


def get_db_password():
    """Safe - reads from environment variable, not hardcoded."""
    return os.getenv("DB_PASSWORD", "")


def load_config(filename):
    """Safe - json.load is not a deserialization vulnerability."""
    with open(filename, "r") as f:
        return json.load(f)


def save_state(data, filename):
    """Real unsafe serialization - pickle.dump to user-controlled path."""
    with open(filename, "wb") as f:
        pickle.dump(data, f)
'''


SECURITY_CATEGORIES = {"注入", "敏感信息", "加密", "权限", "反序列化"}


def extract_security_issues(review_results: list) -> list[dict]:
    for r in review_results:
        if hasattr(r, 'dimension') and 'SECURITY' in str(r.dimension):
            return [
                {
                    "severity": str(i.severity),
                    "category": str(i.category),
                    "description": i.description[:200],
                    "lineno": i.lineno,
                }
                for i in r.issues
            ]
    return []


async def run_with_timing(app, config, initial_state):
    node_times = {}
    total_cost = {}

    async def stream_until_pause(state, cfg):
        current_state = dict(state) if state else {}
        async for event in app.astream_events(state, cfg, version="v2"):
            kind = event["event"]
            name = event.get("name", "")
            if kind == "on_chain_start" and name in [
                "code_parser", "security_reviewer", "performance_reviewer",
                "style_reviewer", "critic_agent", "coder_agent",
                "sandbox_executor", "reflect_node", "human_review", "output_node",
            ]:
                node_times[name] = time.time()
                print(f"  ⏵ [{name}] 开始...")
            elif kind == "on_chain_end" and name in node_times:
                elapsed = time.time() - node_times.pop(name)
                total_cost[name] = total_cost.get(name, 0) + elapsed
                print(f"  ⏹ [{name}] 完成 ({elapsed:.1f}s)")
                output = event["data"].get("output", {})
                if isinstance(output, dict):
                    for k, v in output.items():
                        if k in AgentState.__annotations__:
                            current_state[k] = v
        return current_state

    print("正在执行审查流程...")
    state = await stream_until_pause(initial_state, config)
    state_snapshot = app.get_state(config)
    if state_snapshot.next:
        print(">>> 暂停在 human_review 节点，自动批准...")
        app.update_state(config, {"human_feedback": ""})
        state = await stream_until_pause(None, config)
    return state, total_cost


if __name__ == "__main__":
    print(f"=== B02 验证 #08：凭据+反序列化混合样本 ===")
    print(f"  LLM 模型: {LLM_MODEL}")
    print()

    print("正在构建工作流图...")
    app = build_graph()
    print("图编译完成")
    print()

    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = SAMPLE_CODE
    config = {"configurable": {"thread_id": "b02-test-008"}}

    print("=== 待审查代码 ===")
    print(SAMPLE_CODE)

    result, cost = asyncio.run(run_with_timing(app, config, initial_state))

    print()
    print("=== 节点耗时统计 ===")
    for n in ["code_parser", "security_reviewer", "performance_reviewer",
              "style_reviewer", "critic_agent", "coder_agent",
              "sandbox_executor", "reflect_node", "human_review", "output_node"]:
        if n in cost:
            print(f"  {n:25s} {cost[n]:.1f}s")

    review_results = result.get("review_results", [])
    if not review_results:
        print("\n⚠️ review_results 为空，跳过检测")
        sys.exit(0)

    sec_issues = extract_security_issues(review_results)

    # ============================================================
    # 检测 1: 硬编码凭据检出
    # ============================================================
    print()
    print("=== 检测 1: 真漏洞检出（硬编码凭据） ===")
    cred_found = False
    for issue in sec_issues:
        desc = issue["description"].lower()
        cat = issue["category"]
        if ("敏感信息" in cat or "加密" in cat or
            "key" in desc or "密码" in desc or "凭据" in desc or "api" in desc or
            "secret" in desc or "硬编码" in desc):
            cred_found = True
            print(f"  ✅ 硬编码凭据检出: [{issue['severity']}] {cat} L{issue['lineno']}")
            print(f"     {issue['description'][:120]}")
            break

    if not cred_found:
        print("  ❌ 漏报：硬编码 API_KEY / DB_PASSWORD / SECRET_KEY 未被检出")

    # ============================================================
    # 检测 2: pickle 反序列化检出
    # ============================================================
    print()
    print("=== 检测 2: 真漏洞检出（pickle 不安全反序列化） ===")
    pickle_found = False
    for issue in sec_issues:
        desc = issue["description"].lower()
        cat = issue["category"]
        if ("反序列化" in cat or
            ("pickle" in desc and ("不安全" in desc or "漏洞" in desc or
             "代码执行" in desc or "反序列化" in desc))):
            pickle_found = True
            print(f"  ✅ pickle 反序列化检出: [{issue['severity']}] {cat} L{issue['lineno']}")
            print(f"     {issue['description'][:120]}")
            break

    if not pickle_found:
        # 放宽：检查是否至少提及了 pickle
        for issue in sec_issues:
            desc = issue["description"].lower()
            if "pickle" in desc:
                pickle_found = True
                print(f"  🟡 pickle 提及但可能分类/严重度不准: [{issue['severity']}] {issue['category']}")
                print(f"     {issue['description'][:120]}")
                break

    if not pickle_found:
        print("  ❌ 漏报：pickle.load 不安全反序列化未被检出")

    # ============================================================
    # 检测 3: 伪模式压制 — os.getenv 不应误报
    # ============================================================
    print()
    print("=== 检测 3: 伪模式压制（os.getenv 不应标为硬编码） ===")
    getenv_fp = [
        i for i in sec_issues
        if i["lineno"] >= 16 and i["lineno"] <= 18 and
        ("敏感信息" in i["category"] or "key" in i["description"].lower() or
         "密码" in i["description"] or "凭据" in i["description"])
    ]
    if getenv_fp:
        print(f"  ❌ 误报：os.getenv 被标为硬编码凭据 ({len(getenv_fp)} 条)")
        for issue in getenv_fp:
            print(f"     [{issue['severity']}] L{issue['lineno']}: {issue['description'][:100]}")
    else:
        print("  ✅ os.getenv 未被误报")

    # ============================================================
    # 检测 4: 伪模式压制 — json.load 不应误报反序列化
    # ============================================================
    print()
    print("=== 检测 4: 伪模式压制（json.load 安全反序列化） ===")
    json_fp = [
        i for i in sec_issues
        if i["lineno"] >= 22 and i["lineno"] <= 25 and
        ("反序列化" in i["category"] or
         ("json" in i["description"].lower() and "反序列化" in i["category"]))
    ]
    if json_fp:
        print(f"  ❌ 误报：json.load 被标为反序列化漏洞 ({len(json_fp)} 条)")
        for issue in json_fp:
            print(f"     [{issue['severity']}] L{issue['lineno']}: {issue['description'][:100]}")
    else:
        print("  ✅ json.load 未被误报")

    # ============================================================
    # 检测 5: 推测措辞
    # ============================================================
    print()
    print("=== 检测 5: 推测性措辞 ===")
    speculative_keywords = ["可能", "潜在", "建议加强", "建议增加", "应考虑"]
    found_spec = []
    for issue in sec_issues:
        for kw in speculative_keywords:
            if kw in issue["description"]:
                found_spec.append(f"  [{issue['severity']}] {kw}: {issue['description'][:100]}")
                break
    if found_spec:
        print(f"  ❌ {len(found_spec)} 处推测性措辞:")
        for s in found_spec:
            print(f"     {s}")
    else:
        print("  ✅ 无推测性措辞")

    # ============================================================
    print()
    print("=== 安全审查员原始输出 ===")
    for r in review_results:
        dim = str(r.dimension) if hasattr(r, 'dimension') else "?"
        if "SECURITY" in dim:
            print(f"  issues 数量: {len(r.issues)}")
            for i, issue in enumerate(r.issues):
                sev = str(issue.severity) if hasattr(issue, 'severity') else "?"
                cat = str(issue.category) if hasattr(issue, 'category') else "?"
                print(f"    [{i}] {sev} | {cat} | L{issue.lineno}: {issue.description[:150]}")
    print()
    print("=== B02 验证 #08 完成 ===")
