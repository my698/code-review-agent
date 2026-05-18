"""
B02 验证脚本 #06：SQL 注入混合样本 — 真漏洞 + 伪模式

混合：真 SQL 注入（应检出）+ 参数化查询（不应误报）+ json.loads 无 sink（不应误报）
验证 security_reviewer 是否能严格按确认标准判断。

检测项：
  1. 真漏洞检出：search_user 的 SQL 拼接 → 必须报告 CRITICAL 注入
  2. 伪模式压制：get_user 的参数化查询 → 不应报告
  3. 伪模式压制：format_result 的 json.loads + f-string → 不应报告注入

用法：python tests/bugfix/b02/test_b02_06_sql_mixed.py
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
# - search_user: 真 SQL 注入（cursor.execute + 字符串拼接 + 用户输入）→ 应检出
# - get_user: 参数化查询 → 不应误报
# - format_result: json.loads + f-string，无 SQL/命令执行 sink → 不应误报注入
SAMPLE_CODE = '''
import sqlite3
import json

def search_user(keyword):
    """Real SQL injection - string concatenation into query."""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    query = "SELECT * FROM users WHERE name LIKE '%" + keyword + "%'"
    cursor.execute(query)
    return cursor.fetchall()


def get_user(user_id):
    """Safe - parameterized query with placeholder."""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    return cursor.fetchone()


def format_result(raw_data):
    """Safe - json.loads is not deserialization vuln, f-string has no SQL/command sink."""
    try:
        data = json.loads(raw_data)
    except json.JSONDecodeError:
        return "Invalid data"
    name = data.get("name", "unknown")
    return f"User: {name}"
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


def extract_critic_security_items(action_items: list) -> list[dict]:
    return [
        {"severity": str(i.severity), "category": str(i.category),
         "description": i.description[:200], "fix_instruction": i.fix_instruction[:200]}
        for i in action_items
        if str(i.category) in SECURITY_CATEGORIES or str(i.severity) in ("Severity.CRITICAL", "Severity.HIGH")
    ]


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
    print(f"=== B02 验证 #06：SQL 注入混合样本 ===")
    print(f"  LLM 模型: {LLM_MODEL}")
    print(f"  最大重试: {MAX_RETRY}")
    print()

    print("正在构建工作流图...")
    app = build_graph()
    print("图编译完成")
    print()

    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = SAMPLE_CODE
    config = {"configurable": {"thread_id": "b02-test-006"}}

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
    # 检测 1: 真漏洞必须检出 — search_user SQL 注入
    # ============================================================
    print()
    print("=== 检测 1: 真漏洞检出（search_user SQL 注入） ===")
    injection_found = False
    for issue in sec_issues:
        desc = issue["description"]
        if ("注入" in issue["category"] or "sql" in desc.lower() or
            "execute" in desc.lower() or "拼接" in desc or
            (issue["lineno"] >= 8 and issue["lineno"] <= 12)):
            if issue["severity"] in ("Severity.CRITICAL", "Severity.HIGH"):
                injection_found = True
                print(f"  ✅ 真 SQL 注入已检出: [{issue['severity']}] {issue['category']} L{issue['lineno']}")
                print(f"     {issue['description']}")
                break

    if not injection_found:
        # 放宽：只要报告了注入类问题就算检出
        for issue in sec_issues:
            if "注入" in issue["category"]:
                injection_found = True
                print(f"  🟡 SQL 注入检出但严重度偏低: [{issue['severity']}] L{issue['lineno']}")
                print(f"     {issue['description']}")
                break

    if not injection_found:
        print("  ❌ 漏报：search_user 的真 SQL 注入未被检出")

    # ============================================================
    # 检测 2: 伪模式压制 — get_user 参数化查询不应误报
    # ============================================================
    print()
    print("=== 检测 2: 伪模式压制（get_user 参数化查询） ===")
    getuser_fp = [i for i in sec_issues
                  if i["lineno"] >= 17 and i["lineno"] <= 22 and "注入" in i["category"]]
    if getuser_fp:
        print(f"  ❌ 误报：参数化查询被标为注入 ({len(getuser_fp)} 条)")
        for issue in getuser_fp:
            print(f"     [{issue['severity']}] L{issue['lineno']}: {issue['description'][:100]}")
    else:
        print("  ✅ 参数化查询未被误报")

    # ============================================================
    # 检测 3: 伪模式压制 — json.loads + f-string 不应误报注入
    # ============================================================
    print()
    print("=== 检测 3: 伪模式压制（format_result 无 sink） ===")
    format_fp = [i for i in sec_issues
                 if i["lineno"] >= 26 and ("注入" in i["category"] or
                 ("json" in i["description"].lower() and "注入" in i["category"]))]
    if format_fp:
        print(f"  ❌ 误报：json.loads+f-string 无 sink 被标为安全问题 ({len(format_fp)} 条)")
        for issue in format_fp:
            print(f"     [{issue['severity']}] L{issue['lineno']}: {issue['description'][:100]}")
    else:
        print("  ✅ json.loads+f-string 未被误报")

    # ============================================================
    # 检测 4: critic 正确保留真漏洞、丢弃伪模式
    # ============================================================
    print()
    print("=== 检测 4: critic 处理结果 ===")
    report = result.get("final_report")
    if report and hasattr(report, 'action_items') and report.action_items:
        sec_action = extract_critic_security_items(report.action_items)
        print(f"  critic 安全维度 action_item: {len(sec_action)} 条")
        for item in sec_action:
            print(f"     [{item['severity']}] {item['category']}: {item['description'][:120]}")
        # 至少应该有 1 条（SQL 注入）
        if len(sec_action) >= 1:
            has_injection = any("注入" in item["category"] for item in sec_action)
            if has_injection:
                print("  ✅ critic 保留了真注入漏洞")
            else:
                print("  🟡 critic 有安全条目但非注入类")
        else:
            print("  ❌ critic 丢弃了所有安全条目（真注入被误弃）")
    else:
        print("  ⚠️ 无 action_items")

    # ============================================================
    # 汇总
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
    print("=== B02 验证 #06 完成 ===")
