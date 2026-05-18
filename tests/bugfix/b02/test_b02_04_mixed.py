"""
B02 验证脚本 #04：混合问题样本 — 安全审查误报检测

样本包含真实的硬编码密钥（确实是安全问题） + 裸 except + 资源泄露（非安全问题）。
验证 security_reviewer 是否能：
  1. 正确识别硬编码密钥（真安全漏洞）
  2. 不将裸 except / 资源泄露误标为安全漏洞
  3. 不出现推测性措辞

用法：python tests/bugfix/b02/test_b02_04_mixed.py
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

# 混合问题样本（与 B01 #04 同款）：
# - 硬编码 API 密钥 → 真正安全漏洞，应该报告
# - 裸 except → 风格/可靠性问题，不应标安全
# - 文件未关闭 → 资源管理问题，不应标安全
SAMPLE_CODE = '''
import requests

def fetch_user_data(user_id):
    api_key = "sk-abc123def456ghi789"
    url = "https://api.example.com/users/" + str(user_id)
    try:
        resp = requests.get(url, headers={"Authorization": f"Bearer {api_key}"})
        data = resp.json()
        return data
    except:
        print("Something went wrong")
        return None


def read_config(path):
    f = open(path, "r")
    content = f.read()
    f.close()
    return content
'''


SECURITY_CATEGORIES = {
    "注入", "敏感信息", "加密缺陷", "权限控制",
    "认证", "序列化", "SSRF", "路径遍历",
}

NON_SECURITY_CATEGORIES = {
    "异常处理", "资源管理", "代码风格", "可读性",
    "命名规范", "类型注解", "文档注释", "错误处理",
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


def check_category_crossover(issues: list[dict]) -> list[dict]:
    """检查安全审查员是否把非安全问题标成了安全类 category"""
    crossover = []
    for issue in issues:
        cat = issue.get("category", "")
        desc = issue.get("description", "")
        # 如果 category 是安全类，但描述提到的是异常处理/资源管理 → 误判
        if cat in SECURITY_CATEGORIES:
            non_sec_hints = ["except", "异常", "资源", "关闭", "泄露", "close", "with"]
            if any(hint in desc for hint in non_sec_hints):
                crossover.append(issue)
    return crossover


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
    print(f"=== B02 验证 #04：安全审查误报检测（混合问题样本） ===")
    print(f"  LLM 模型: {LLM_MODEL}")
    print(f"  最大重试: {MAX_RETRY}")
    print()

    print("正在构建工作流图...")
    app = build_graph()
    print("图编译完成")
    print()

    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = SAMPLE_CODE
    config = {"configurable": {"thread_id": "b02-test-004"}}

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

    # 检测 1: security_reviewer 应正确报告硬编码密钥（真漏洞不应漏）
    print()
    print("--- 检测 1: 真实安全漏洞覆盖 ---")
    sec_issues = extract_security_issues(review_results)

    if not sec_issues:
        print("  ❌ security_reviewer 未报告任何安全问题 —— 硬编码密钥被漏掉")
    else:
        found_key = any(
            "key" in i.get("description", "").lower() or
            "密钥" in i.get("description", "") or
            "敏感信息" in i.get("category", "") or
            "硬编码" in i.get("description", "")
            for i in sec_issues
        )
        if found_key:
            print("  ✅ security_reviewer 正确识别硬编码密钥")
        else:
            print("  ❌ security_reviewer 报告了问题，但未识别硬编码密钥")
            for issue in sec_issues:
                print(f"     - [{issue['severity']}] {issue['category']}: {issue['description']}")

    # 检测 2: category 越界 —— 裸 except / 资源泄露不应标为安全类
    print()
    print("--- 检测 2: category 越界（非安全问题标安全类） ---")
    crossover = check_category_crossover(sec_issues)
    if crossover:
        print(f"  ❌ {len(crossover)} 条非安全问题被标为安全类 category：")
        for issue in crossover:
            print(f"     - [{issue['severity']}] {issue['category']}: {issue['description']}")
    else:
        print("  ✅ 无 category 越界")

    # 检测 3: 高危误报（裸 except 不应是 CRITICAL 安全）
    print()
    print("--- 检测 3: 高危误报 ---")
    high_issues = [
        i for i in sec_issues
        if i["severity"] in ("Severity.CRITICAL", "Severity.HIGH")
    ]
    valid_high = []
    invalid_high = []
    for issue in high_issues:
        desc = issue.get("description", "")
        cat = issue.get("category", "")
        # 硬编码密钥 / 敏感信息泄露 → 合法高危
        if "key" in desc.lower() or "密钥" in desc or "敏感" in cat:
            valid_high.append(issue)
        else:
            invalid_high.append(issue)

    if invalid_high:
        print(f"  ❌ {len(invalid_high)} 条无效高危（非安全漏洞被标高危）：")
        for issue in invalid_high:
            print(f"     - [{issue['severity']}] {issue['category']}: {issue['description']}")
    elif valid_high:
        print(f"  ✅ {len(valid_high)} 条高危均为真实安全漏洞（硬编码密钥）")
    else:
        print("  ✅ 无高危误报")

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

    # 检测 5: critic 安全维度残留
    print()
    print("--- 检测 5: critic 安全维度残留 ---")
    report = result.get("final_report")
    if report and hasattr(report, 'action_items') and report.action_items:
        security_action_items = extract_critic_security_items(report.action_items)
        if security_action_items:
            print(f"  critic 保留 {len(security_action_items)} 条安全 action_item：")
            for item in security_action_items:
                print(f"     - [{item['severity']}] {item['category']}: {item['description']}")
        else:
            print("  ✅ critic 汇总后无安全维度 action_item")

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
    print("=== B02 验证 #04 完成 ===")
