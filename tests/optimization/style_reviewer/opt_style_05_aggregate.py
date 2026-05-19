"""
风格审查员优化测试 #05：汇总 —— 串联 01-04 结果

无 LLM 调用，纯数据汇总。

用法：python tests/optimization/style_reviewer/opt_style_05_aggregate.py
"""
import sys
import subprocess
import time
from pathlib import Path

SCRIPTS = [
    ("01-smoke",       "opt_style_01_smoke.py"),
    ("02-detect",      "opt_style_02_detect.py"),
    ("03-noise",       "opt_style_03_noise.py"),
    ("04-severity",    "opt_style_04_severity.py"),
]

THIS_DIR = Path(__file__).resolve().parent


def main():
    print("=== 风格审查员优化测试 #05：汇总 ===")
    print()

    results = {}
    total_start = time.time()

    for label, script in SCRIPTS:
        script_path = THIS_DIR / script
        if not script_path.exists():
            print(f"  ⚠️  {script} 不存在，跳过")
            results[label] = "SKIP"
            continue

        print(f"--- {label} ({script}) ---")
        t0 = time.time()
        proc = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True, text=True, timeout=600,
        )
        elapsed = time.time() - t0
        passed = proc.returncode == 0
        results[label] = "PASS" if passed else "FAIL"
        print(f"  exit_code={proc.returncode}  ({elapsed:.1f}s) → {results[label]}")

        lines = proc.stdout.strip().split("\n")
        for line in lines[-5:]:
            print(f"  | {line}")
        if proc.stderr.strip():
            stderr_lines = proc.stderr.strip().split("\n")
            for line in stderr_lines[-3:]:
                print(f"  [stderr] {line}")
        print()

    total_elapsed = time.time() - total_start

    print("=== 最终审查报告 ===")
    for label, result in results.items():
        print(f"  {label:20s} {result}")
    print(f"  总耗时: {total_elapsed:.1f}s")

    all_pass = all(v == "PASS" for v in results.values() if v != "SKIP")
    status = "success" if all_pass else "partial" if any(v == "PASS" for v in results.values()) else "failed"
    print(f"  状态: {status}")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
