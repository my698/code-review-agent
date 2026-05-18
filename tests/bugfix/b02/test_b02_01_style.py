"""
B02 验证脚本 #01：安全审查误报检测 — 风格灾难样本

用纯风格问题代码（命名混乱 + 格式烂 + PEP 8 违规）跑完整流程，
检测 security_reviewer 是否对无安全漏洞的代码上纲上线。

验证项：
  1. security_reviewer 的 issues 中无 CRITICAL / HIGH（安全维度不应出现高危）
  2. security_reviewer 的 issues 中无安全类 category（注入/敏感信息/加密等）
  3. critic 汇总后，安全维度的 action_item 不过度（不应将风格问题转为安全修复）

用法：python tests/bugfix/b02/test_b02_01_style.py
"""
import sys
import asyncio
import ast
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

# 风格问题样本（与 B01 #03 同款）：
# - calc_sm: 命名混乱、缺类型注解、缺空格
# - GetData: 大写函数名、函数内 import、缺异常处理
# 核心特征：无任何安全漏洞（无 SQL/命令执行/文件操作/网络请求）
SAMPLE_CODE = '''
def calc_sm(a,b,c):
    x=a+b+c
    y=x/3
    return y

def GetData(Query):
    import json
    d=json.loads(Query)
    return d["result"]
'''


# ============================================================
# B02 专项检测 —— 安全审查误报
# ============================================================

SECURITY_CATEGORIES = {
    "注入", "敏感信息", "加密缺陷", "权限控制",
    "认证", "序列化", "SSRF", "路径遍历",
}

SECURITY_SEVERITIES = {"CRITICAL", "HIGH"}


def extract_security_issues(review_results: list) -> list[dict]:
    """从 review_results 中提取 security_reviewer 的 issues 详情"""
    for r in review_results:
        if hasattr(r, 'dimension') and str(r.dimension) == 'ReviewDimension.SECURITY':
            issues = []
            for issue in r.issues:
                issues.append({
                    "severity": str(issue.severity) if hasattr(issue, 'severity') else "?",
                    "category": str(issue.category) if hasattr(issue, 'category') else "?",
                    "description": issue.description[:120] if hasattr(issue, 'description') else "",
                    "lineno": issue.lineno if hasattr(issue, 'lineno') else 0,
                })
            return issues
    return []


def extract_critic_security_items(action_items: list) -> list[dict]:
    """从 critic 输出的 action_items 中提取安全维度的高危条目"""
    security_items = []
    for item in action_items:
        category = str(item.category) if hasattr(item, 'category') else ""
        severity = str(item.severity) if hasattr(item, 'severity') else ""
        if category in SECURITY_CATEGORIES or severity in SECURITY_SEVERITIES:
            security_items.append({
                "severity": severity,
                "category": category,
                "description": item.description[:120] if hasattr(item, 'description') else "",
                "fix_instruction": item.fix_instruction[:120] if hasattr(item, 'fix_instruction') else "",
            })
    return security_items


def check_speculative_language(issues: list[dict]) -> list[str]:
    """检查 issues 描述中是否含推测性措辞"""
    speculative = []
    keywords = ["可能", "潜在", "建议加强", "建议增加", "应考虑", "推荐使用"]
    for issue in issues:
        desc = issue.get("description", "")
        for kw in keywords:
            if kw in desc:
                speculative.append(f"  [{issue.get('severity', '?')}] {desc[:100]}... (关键词: '{kw}')")
                break
    return speculative


# ============================================================
# 流式执行（与 B01 测试脚本同款）
# ============================================================

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


# ============================================================
# 主流程
# ============================================================

if __name__ == "__main__":
    print(f"=== B02 验证 #01：安全审查误报检测（风格样本） ===")
    print(f"  LLM 模型: {LLM_MODEL}")
    print(f"  最大重试: {MAX_RETRY}")
    print()

    print("正在构建工作流图...")
    app = build_graph()
    print("图编译完成")
    print()

    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = SAMPLE_CODE
    config = {"configurable": {"thread_id": "b02-test-001"}}

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

    # 检测 1: security_reviewer 是否对纯风格代码报了高危
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
                print(f"       行号: {issue['lineno']}")
        else:
            print(f"  🟡 security_reviewer 报告了 {len(sec_issues)} 条问题，但无高危：")
            for issue in sec_issues:
                print(f"     - [{issue['severity']}] {issue['category']}: {issue['description']}")

    # 检测 2: security_reviewer 的 category 是否越界标了安全类
    print()
    print("--- 检测 2: security_reviewer category 越界 ---")
    miscategorized = [
        i for i in sec_issues
        if i["category"] in ("IssueCategory.注入", "IssueCategory.敏感信息",
                             "IssueCategory.加密缺陷", "IssueCategory.权限控制")
    ]
    if miscategorized:
        print(f"  ❌ security_reviewer 对纯风格代码标了 {len(miscategorized)} 条安全类 category：")
        for issue in miscategorized:
            print(f"     - [{issue['category']}] {issue['description']}")
    else:
        print("  ✅ 无安全类 category 越界")

    # 检测 3: critic 汇总后是否仍有安全维度高危 action_item
    print()
    print("--- 检测 3: critic 汇总后的安全维度残留 ---")
    report = result.get("final_report")
    if report and hasattr(report, 'action_items') and report.action_items:
        security_action_items = extract_critic_security_items(report.action_items)
        if security_action_items:
            print(f"  ❌ critic 仍保留了 {len(security_action_items)} 条安全维度 action_item：")
            for item in security_action_items:
                print(f"     - [{item['severity']}] {item['category']}: {item['description']}")
        else:
            print("  ✅ critic 汇总后无安全维度 action_item（正确丢弃了安全误报）")
    else:
        print("  ⚠️ 无 action_items 可供检测")

    # 检测 4: 推测性措辞
    print()
    print("--- 检测 4: 推测性措辞 ---")
    speculative = check_speculative_language(sec_issues)
    if speculative:
        print(f"  ❌ security_reviewer 含 {len(speculative)} 处推测性措辞：")
        for s in speculative:
            print(f"     {s}")
    else:
        print("  ✅ 无推测性措辞")

    # ============================================================
    # 汇总
    # ============================================================
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
    print("=== B02 验证完成 ===")
