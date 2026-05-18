"""
B02 验证脚本 #05：干净代码样本 — 安全审查误报检测

代码本身可运行、风格合规、有 docstring、有异常处理，无任何安全漏洞。
验证 security_reviewer 是否对干净代码"发明"安全问题。

检测项：
  1. security_reviewer 不应报告任何安全问题
  2. 尤其不应编造注入/敏感信息/加密缺陷等
  3. 不应出现推测性措辞

用法：python tests/bugfix/b02/test_b02_05_clean.py
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

# 干净代码样本（与 B01 #05 同款）：
# - calculate_average: 有 docstring、边界检查、无安全风险
# - safe_divide: 有异常处理、正常除法，无安全风险
SAMPLE_CODE = '''
def calculate_average(numbers):
    """Return the arithmetic mean of a list of numbers."""
    if not numbers:
        return 0.0
    return sum(numbers) / len(numbers)


def safe_divide(a, b):
    """Divide a by b, returning None when b is zero."""
    try:
        return a / b
    except ZeroDivisionError:
        return None
'''


SECURITY_CATEGORIES = {
    "注入", "敏感信息", "加密缺陷", "权限控制",
    "认证", "序列化", "SSRF", "路径遍历",
}

SECURITY_SEVERITIES = {"CRITICAL", "HIGH"}


def extract_security_issues(review_results: list) -> list[dict]:
    for r in review_results:
        if hasattr(r, 'dimension') and 'SECURITY' in str(r.dimension):
            issues = []
            for issue in r.issues:
                issues.append({
                    "severity": str(issue.severity) if hasattr(issue, 'severity') else "?",
                    "category": str(issue.category) if hasattr(issue, 'category') else "?",
                    "description": issue.description[:150] if hasattr(issue, 'description') else "",
                    "lineno": issue.lineno if hasattr(issue, 'lineno') else 0,
                })
            return issues
    return []


def extract_critic_security_items(action_items: list) -> list[dict]:
    security_items = []
    for item in action_items:
        category = str(item.category) if hasattr(item, 'category') else ""
        severity = str(item.severity) if hasattr(item, 'severity') else ""
        if category in SECURITY_CATEGORIES or severity in SECURITY_SEVERITIES:
            security_items.append({
                "severity": severity,
                "category": category,
                "description": item.description[:150] if hasattr(item, 'description') else "",
            })
    return security_items


def check_speculative_language(issues: list[dict]) -> list[str]:
    speculative = []
    keywords = ["可能", "潜在", "建议加强", "建议增加", "应考虑", "推荐使用"]
    for issue in issues:
        desc = issue.get("description", "")
        for kw in keywords:
            if kw in desc:
                speculative.append(f"  [{issue.get('severity', '?')}] {desc[:100]}... (关键词: '{kw}')")
                break
    return speculative


async def run_with_timing(app, config, initial_state):
    node_times: dict[str, float] = {}
    total_cost: dict[str, float] = {}

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
    print(f"=== B02 验证 #05：安全审查误报检测（干净代码样本） ===")
    print(f"  LLM 模型: {LLM_MODEL}")
    print(f"  最大重试: {MAX_RETRY}")
    print()

    print("正在构建工作流图...")
    app = build_graph()
    print("图编译完成")
    print()

    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = SAMPLE_CODE
    config = {"configurable": {"thread_id": "b02-test-005"}}

    print("=== 待审查代码 ===")
    print(SAMPLE_CODE)

    result, cost = asyncio.run(run_with_timing(app, config, initial_state))

    print()
    print("=== 节点耗时统计 ===")
    for node_name in [
        "code_parser", "security_reviewer", "performance_reviewer",
        "style_reviewer", "critic_agent", "coder_agent",
        "sandbox_executor", "reflect_node", "human_review", "output_node",
    ]:
        if node_name in cost:
            print(f"  {node_name:25s} {cost[node_name]:.1f}s")

    # ============================================================
    # B02 专项检测
    # ============================================================
    print()
    print("=== B02 安全误报检测 ===")

    review_results = result.get("review_results", [])
    if not review_results:
        print("⚠️ review_results 为空，跳过检测")
        sys.exit(0)

    # 检测 1: security_reviewer 干净代码不应报告任何安全问题
    print()
    print("--- 检测 1: 干净代码安全误报 ---")
    sec_issues = extract_security_issues(review_results)

    if not sec_issues:
        print("  ✅ security_reviewer 未报告安全问题（正确）")
    else:
        print(f"  ❌ security_reviewer 对干净代码报告了 {len(sec_issues)} 条安全问题：")
        for issue in sec_issues:
            print(f"     - [{issue['severity']}] {issue['category']}: {issue['description']}")
            print(f"       行号: {issue['lineno']}")

    # 检测 2: 不应有安全类 category
    print()
    print("--- 检测 2: 安全类 category 误判 ---")
    sec_cat_issues = [
        i for i in sec_issues
        if any(cat in i.get("category", "") for cat in SECURITY_CATEGORIES)
    ]
    if sec_cat_issues:
        print(f"  ❌ {len(sec_cat_issues)} 条被标为安全类 category：")
        for issue in sec_cat_issues:
            print(f"     - [{issue['category']}] {issue['description']}")
    else:
        print("  ✅ 无安全类 category 误判")

    # 检测 3: critic 安全维度残留
    print()
    print("--- 检测 3: critic 安全维度残留 ---")
    report = result.get("final_report")
    if report and hasattr(report, 'action_items') and report.action_items:
        security_action_items = extract_critic_security_items(report.action_items)
        if security_action_items:
            print(f"  ❌ critic 保留 {len(security_action_items)} 条安全 action_item：")
            for item in security_action_items:
                print(f"     - [{item['severity']}] {item['category']}: {item['description']}")
        else:
            print("  ✅ critic 汇总后无安全维度 action_item")
    else:
        print("  ⚠️ 无 action_items 可供检测")

    # 检测 4: 推测性措辞
    print()
    print("--- 检测 4: 推测性措辞 ---")
    speculative = check_speculative_language(sec_issues)
    if speculative:
        print(f"  ❌ {len(speculative)} 处推测性措辞：")
        for s in speculative:
            print(f"     {s}")
    else:
        print("  ✅ 无推测性措辞")

    print()
    print("=== B02 验证 #05 完成 ===")
