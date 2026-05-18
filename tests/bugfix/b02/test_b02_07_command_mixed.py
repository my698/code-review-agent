"""
B02 验证脚本 #07：命令执行混合样本 — 真漏洞 + 伪模式

混合：真命令注入（应检出）+ 硬编码 subprocess（不应误报）+ 安全路径拼接（不应误报）
验证 security_reviewer 是否能区分用户输入直达危险操作 vs 安全参数。

检测项：
  1. 真漏洞检出：os.system("cp " + filename) → 必须报告命令注入
  2. 伪模式压制：subprocess.run(["ls", "-l"]) 硬编码 → 不应报告
  3. 伪模式压制：open(os.path.join(BASE, x)) 安全路径 → 不应报告路径遍历

用法：python tests/bugfix/b02/test_b02_07_command_mixed.py
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
# - run_backup: 真命令注入（os.system + 字符串拼接 + 用户输入 filename）→ 应检出
# - list_files: subprocess.run 参数硬编码 → 不应误报
# - read_log: open(os.path.join(BASE, x)) 路径前缀固定 → 不应误报路径遍历
# - eval_expr: eval(用户输入) → 应检出命令注入
SAMPLE_CODE = '''
import os
import subprocess

BACKUP_DIR = "/var/backups"
LOG_DIR = "/var/log/myapp"


def run_backup(filename):
    """Real command injection - user input concatenated into shell command."""
    os.system("cp backup/" + filename + " " + BACKUP_DIR + "/")


def list_files():
    """Safe - hardcoded command arguments, no user input."""
    subprocess.run(["ls", "-l", "/tmp"], capture_output=True)


def read_log(name):
    """Safe - os.path.join with fixed prefix, not user-controlled path."""
    path = os.path.join(LOG_DIR, name + ".log")
    with open(path) as f:
        return f.read()


def eval_expr(expr):
    """Real code injection - eval with user input."""
    return eval(expr)
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
    print(f"=== B02 验证 #07：命令执行混合样本 ===")
    print(f"  LLM 模型: {LLM_MODEL}")
    print()

    print("正在构建工作流图...")
    app = build_graph()
    print("图编译完成")
    print()

    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = SAMPLE_CODE
    config = {"configurable": {"thread_id": "b02-test-007"}}

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
    # 检测 1: 真命令注入检出
    # ============================================================
    print()
    print("=== 检测 1: 真漏洞检出（os.system + eval） ===")
    cmd_injection_found = False
    eval_found = False
    for issue in sec_issues:
        desc = issue["description"].lower()
        cat = issue["category"]
        # os.system 命令注入应在 L12 附近
        if ("注入" in cat and
            (issue["lineno"] >= 12 and issue["lineno"] <= 13 or
             "system" in desc or "cp " in desc or "命令" in desc)):
            cmd_injection_found = True
            print(f"  ✅ os.system 命令注入检出: [{issue['severity']}] L{issue['lineno']}")
            print(f"     {issue['description'][:120]}")
        # eval 注入应在 L29 附近
        if ("注入" in cat and
            (issue["lineno"] >= 28 and issue["lineno"] <= 30 or
             "eval" in desc)):
            eval_found = True
            print(f"  ✅ eval 代码注入检出: [{issue['severity']}] L{issue['lineno']}")
            print(f"     {issue['description'][:120]}")

    if not cmd_injection_found:
        print("  ❌ 漏报：os.system 命令注入未被检出")
    if not eval_found:
        print("  ❌ 漏报：eval 代码注入未被检出")

    # ============================================================
    # 检测 2: 伪模式压制 — subprocess 硬编码参数
    # ============================================================
    print()
    print("=== 检测 2: 伪模式压制（subprocess 硬编码参数） ===")
    subprocess_fp = [
        i for i in sec_issues
        if i["lineno"] >= 18 and i["lineno"] <= 21 and
        ("注入" in i["category"] or "命令" in i["description"] or
         "subprocess" in i["description"].lower())
    ]
    if subprocess_fp:
        print(f"  ❌ 误报：硬编码 subprocess 被标为安全问题 ({len(subprocess_fp)} 条)")
        for issue in subprocess_fp:
            print(f"     [{issue['severity']}] L{issue['lineno']}: {issue['description'][:100]}")
    else:
        print("  ✅ 硬编码 subprocess 未被误报")

    # ============================================================
    # 检测 3: 伪模式压制 — 安全路径拼接
    # ============================================================
    print()
    print("=== 检测 3: 伪模式压制（os.path.join 安全路径） ===")
    path_fp = [
        i for i in sec_issues
        if i["lineno"] >= 25 and i["lineno"] <= 28 and
        ("路径" in i["description"] or "路径遍历" in i["category"] or
         "open" in i["description"].lower() and "注入" in i["category"])
    ]
    if path_fp:
        print(f"  ❌ 误报：安全路径拼接被标为安全问题 ({len(path_fp)} 条)")
        for issue in path_fp:
            print(f"     [{issue['severity']}] L{issue['lineno']}: {issue['description'][:100]}")
    else:
        print("  ✅ os.path.join 安全路径未被误报")

    # ============================================================
    # 检测 4: 推测措辞检查
    # ============================================================
    print()
    print("=== 检测 4: 推测性措辞 ===")
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
    print("=== B02 验证 #07 完成 ===")
