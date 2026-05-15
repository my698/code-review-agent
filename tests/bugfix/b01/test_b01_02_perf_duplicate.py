"""
B01 验证脚本 #02：性能问题样本 — 测试禁令在多维代码上的稳定性

O(n^2) 循环 + 冗余字典查找，代码比 #01 更长，验证 coder 是否仍守边界。

用法：python tests/bugfix/b01/test_b01_02_perf_duplicate.py
"""
import sys
import asyncio
import ast
import time
import warnings
from pathlib import Path

# [2026-05-15] 抑制 LangGraph 弃用警告 + Deserializing 日志噪音
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

# 性能问题样本：O(n^2) + 冗余字典查找
SAMPLE_CODE = '''
def find_duplicates(items):
    """Find all duplicate items in a list."""
    duplicates = []
    for i in range(len(items)):
        for j in range(len(items)):
            if i != j and items[i] == items[j] and items[i] not in duplicates:
                duplicates.append(items[i])
    return duplicates


def get_scores(students):
    """Build a dict of student name -> score from the students dict."""
    result = {}
    for name in students:
        if name in students:
            score = students[name]
            result[name] = score
    return result
'''


def extract_functions(code: str) -> dict[str, dict]:
    tree = ast.parse(code)
    funcs = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef):
            funcs[node.name] = {
                "args": [a.arg for a in node.args.args],
                "lineno": node.lineno,
            }
    return funcs


def extract_imports(code: str) -> set[str]:
    tree = ast.parse(code)
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split(".")[0])
    return imports


def run_checks(original: str, fixed: str) -> dict:
    """对比原始代码和修复后代码，返回检测结果"""
    orig_funcs = extract_functions(original)
    fixed_funcs = extract_functions(fixed)
    orig_imports = extract_imports(original)
    fixed_imports = extract_imports(fixed)

    results = {}

    # 检查 1: 函数名是否被修改
    orig_names = set(orig_funcs.keys())
    fixed_names = set(fixed_funcs.keys())
    renamed = orig_names - fixed_names
    added = fixed_names - orig_names
    results["重命名函数"] = list(renamed) if renamed else None
    results["新增函数"] = list(added) if added else None

    # 检查 2: 参数列表是否被修改
    param_changes = []
    for name in orig_names & fixed_names:
        orig_args = orig_funcs[name]["args"]
        fixed_args = fixed_funcs[name]["args"]
        if orig_args != fixed_args:
            param_changes.append({
                "函数": name,
                "原始参数": orig_args,
                "修复后参数": fixed_args,
            })
    results["参数变更"] = param_changes if param_changes else None

    # 检查 3: 是否添加了新的 import
    new_imports = fixed_imports - orig_imports
    results["新增import"] = list(new_imports) if new_imports else None

    # 检查 4: 行数膨胀
    orig_lines = len(original.strip().split("\n"))
    fixed_lines = len(fixed.strip().split("\n"))
    line_ratio = fixed_lines / max(orig_lines, 1)
    results["行数膨胀"] = f"{orig_lines} → {fixed_lines} 行 (膨胀 {line_ratio:.1f}x)" if line_ratio > 1.5 else None

    # 检查 5: 是否有变量提升到模块级（作用域变更）
    scoped = set()
    tree = ast.parse(fixed)
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    scoped.add(target.id)
    # 原始代码中在函数内部的变量不应出现在模块级别
    orig_tree = ast.parse(original)
    orig_module_vars = set()
    for node in ast.iter_child_nodes(orig_tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    orig_module_vars.add(target.id)
    new_module_vars = scoped - orig_module_vars
    results["作用域变更"] = list(new_module_vars) if new_module_vars else None

    return results


async def run_with_timing(app, config, initial_state):
    """流式执行，记录节点耗时"""
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
    print(f"=== B01 验证 #02：性能问题样本 ===")
    print(f"  LLM 模型: {LLM_MODEL}")
    print(f"  最大重试: {MAX_RETRY}")
    print()

    print("正在构建工作流图...")
    app = build_graph()
    print("图编译完成")
    print()

    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = SAMPLE_CODE
    config = {"configurable": {"thread_id": "b01-test-002"}}

    print("=== 待审查代码 ===")
    print(SAMPLE_CODE)

    result, cost = asyncio.run(run_with_timing(app, config, initial_state))

    # 节点耗时
    print()
    print("=== 节点耗时统计 ===")
    for node_name in [
        "code_parser", "security_reviewer", "performance_reviewer",
        "style_reviewer", "critic_agent", "coder_agent",
        "sandbox_executor", "reflect_node", "human_review", "output_node",
    ]:
        if node_name in cost:
            print(f"  {node_name:25s} {cost[node_name]:.1f}s")

    # B01 专项验证
    print()
    print("=== B01 越界检测 ===")

    report = result.get("final_report")
    if not report:
        print("报告未生成，无法检测")
        sys.exit(1)

    fixed_code = report.fixed_code
    if not fixed_code:
        print("未生成修复代码")
        sys.exit(1)

    checks = run_checks(SAMPLE_CODE, fixed_code)

    passed = True
    for check_name, violation in checks.items():
        if violation:
            print(f"  ❌ {check_name}: {violation}")
            passed = False
        else:
            print(f"  ✅ {check_name}: 通过")

    print()
    print("=== 修复后代码 ===")
    print(fixed_code)

    if report.status == "failed":
        print()
        print(f"  流程状态: failed（重试 {report.retry_count} 次后失败）")

    print()
    if passed:
        print("=== B01 验证通过：coder 未越界重构 ===")
    else:
        print("=== B01 验证失败：coder 仍然越界（见上方检测项） ===")
        sys.exit(1)
