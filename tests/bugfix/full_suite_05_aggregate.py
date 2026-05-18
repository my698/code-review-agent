"""
全量测试 #05：综合汇总 —— 运行全部 E2E 样本并生成聚合报告

依次执行 full_suite_02/03/04，汇总统计。

用法：python tests/bugfix/full_suite_05_aggregate.py
"""
import sys
import subprocess
import time
from pathlib import Path

SCRIPTS = [
    ("02_security",   "full_suite_02_security.py"),
    ("03_fix_score",  "full_suite_03_fix_score.py"),
    ("04_failure_hitl", "full_suite_04_failure_hitl.py"),
]

BASE_DIR = Path(__file__).resolve().parent

if __name__ == "__main__":
    print("=== 代码审查 Agent 全量测试 #05：综合汇总 ===")
    print(f"  子测试: {len(SCRIPTS)} 个")
    print()

    results = {}
    total_start = time.time()

    for name, script in SCRIPTS:
        print(f"--- 运行 {name} ---")
        t0 = time.time()
        proc = subprocess.run(
            [sys.executable, str(BASE_DIR / script)],
            capture_output=True, text=True, timeout=600,
        )
        elapsed = time.time() - t0
        results[name] = {
            "exit_code": proc.returncode,
            "elapsed": elapsed,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
        # show last few lines of output
        lines = proc.stdout.strip().split("\n")
        for line in lines[-8:]:
            print(f"  {line}")
        if proc.returncode != 0:
            print(f"  ❌ 退出码: {proc.returncode}")
            if proc.stderr:
                print(f"  stderr: {proc.stderr[:300]}")
        print()

    total_elapsed = time.time() - total_start

    # --- aggregate ---
    print("=== 节点耗时统计 ===")
    for name, r in results.items():
        print(f"  {name:25s} {r['elapsed']:.1f}s  (exit={r['exit_code']})")
    print(f"  {'总计':25s} {total_elapsed:.1f}s")

    print()
    print("=== 最终审查报告 ===")
    total = len(results)
    passed = sum(1 for r in results.values() if r["exit_code"] == 0)
    failed = total - passed
    print(f"  子测试数: {total}")
    print(f"  通过:     {passed}")
    print(f"  失败:     {failed}")
    print(f"  总耗时:   {total_elapsed:.1f}s")
    print(f"  状态:     {'success' if failed == 0 else 'failed'}")

    # extract score summaries from individual outputs
    print()
    print("--- 各测试关键指标 ---")
    for name, r in results.items():
        output = r["stdout"]
        # grep status line
        for line in output.split("\n"):
            if "状态:" in line and "成功" not in line and "失败" not in line:
                # this is the final report status
                pass
        # find the final status
        status_line = [l for l in output.split("\n") if l.strip().startswith("状态:") and "最终" not in l]
        if status_line:
            print(f"  {name}: {status_line[-1].strip()}")

    sys.exit(0 if failed == 0 else 1)
