"""
B02 验证脚本 #02：文件操作样本 — 安全审查误报检测

安全的文件读写在 security_reviewer 眼里可能被误判为"路径遍历"或"不安全反序列化"。
这个样本只有常规文件 IO + JSON 解析，无任何安全漏洞。

检测项：
  1. security_reviewer 不应将安全文件操作标为 CRITICAL/HIGH
  2. 不应出现"路径遍历""反序列化"等安全类 category

用法：python tests/bugfix/b02/test_b02_02_file_ops.py
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

# 文件操作样本：安全地读 JSON 配置文件
# 特征：open+json.load 是标准用法，filename 来自参数而非用户直接输入
# 安全审查员可能误判为：路径遍历（open 参数未校验）、不安全反序列化（json.load）
SAMPLE_CODE = '''
def read_config(filename):
    """Read configuration from a JSON file."""
    import json
    with open(filename, 'r') as f:
        config = json.load(f)
    return config


def save_result(filepath, data):
    """Save processed result to a file."""
    with open(filepath, 'w') as f:
        f.write(str(data))
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
    print(f"=== B02 验证 #02：安全审查误报检测（文件操作样本） ===")
    print(f"  LLM 模型: {LLM_MODEL}")
    print(f"  最大重试: {MAX_RETRY}")
    print()

    print("正在构建工作流图...")
    app = build_graph()
    print("图编译完成")
    print()

    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = SAMPLE_CODE
    config = {"configurable": {"thread_id": "b02-test-002"}}

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

    # 检测 1: security_reviewer 高危误报
    print()
    print("--- 检测 1: security_reviewer 高危误报 ---")
    sec_issues = extract_security_issues(review_results)

    if not sec_issues:
        print("  ✅ security_reviewer 未报告任何安全问题（正确）")
    else:
        high_issues = [
            i for i in sec_issues
            if i["severity"] in ("Severity.CRITICAL", "Severity.HIGH")
        ]
        if high_issues:
            print(f"  ❌ 安全审查员误报 {len(high_issues)} 条高危问题：")
            for issue in high_issues:
                print(f"     - [{issue['severity']}] {issue['category']}: {issue['description']}")
        else:
            print(f"  🟡 security_reviewer 报告了 {len(sec_issues)} 条问题，但无高危：")
            for issue in sec_issues:
                print(f"     - [{issue['severity']}] {issue['category']}: {issue['description']}")

    # 检测 2: 路径遍历 / 反序列化误判
    print()
    print("--- 检测 2: 路径遍历/反序列化 误判 ---")
    path_traversal = [
        i for i in sec_issues
        if "路径遍历" in i["category"] or "序列化" in i["category"]
    ]
    if path_traversal:
        print(f"  ❌ security_reviewer 误判 {len(path_traversal)} 条路径遍历/反序列化：")
        for issue in path_traversal:
            print(f"     - [{issue['category']}] {issue['description']}")
    else:
        print("  ✅ 无路径遍历/反序列化误判")

    # 检测 3: critic 汇总后安全维度残留
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
    print("=== 安全审查员原始输出 ===")
    for r in review_results:
        dim = str(r.dimension) if hasattr(r, 'dimension') else "?"
        if "SECURITY" in dim:
            print(f"  dimension: {dim}")
            print(f"  issues 数量: {len(r.issues)}")
            for i, issue in enumerate(r.issues):
                sev = str(issue.severity) if hasattr(issue, 'severity') else "?"
                cat = str(issue.category) if hasattr(issue, 'category') else "?"
                desc = issue.description if hasattr(issue, 'description') else ""
                print(f"    [{i}] {sev} | {cat} | L{issue.lineno}: {desc}")

    print()
    print("=== B02 验证 #02 完成 ===")
