"""
B04 验证脚本 #02：should_continue_or_output 路由检测（直接测函数，不依赖 LLM）

验证 human_review 之后两个分支：
- human_feedback == "" → output_node（确认，接受结果）
- human_feedback != "" → coder_agent（给人反馈，重新修复）

用法：python tests/bugfix/b04/test_b04_02_routing_branches.py
"""
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message=".*allowed_objects.*")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SRC_DIR = str(PROJECT_ROOT / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from graph.state import INITIAL_STATE
from graph.builder import should_continue_or_output


def build_state(feedback):
    state = dict(INITIAL_STATE)
    state["human_feedback"] = feedback
    return state


if __name__ == "__main__":
    print(f"=== B04 验证 #02：should_continue_or_output 路由检测 ===")
    print()

    all_passed = True

    # 检测 1：空白确认 → output_node
    print("--- 检测 1：human 空白确认 → output_node ---")
    state = build_state("")
    result = should_continue_or_output(state)
    if result == "output_node":
        print(f"  ✅ human_feedback='' → {result}")
    else:
        print(f"  ❌ human_feedback='' → {result}（应为 output_node）")
        all_passed = False
    print()

    # 检测 2：有反馈 → coder_agent
    print("--- 检测 2：human 给反馈 → coder_agent ---")
    feedbacks = [
        "别改函数签名",
        "用 subprocess.run 替换 os.system",
        "把密码移到环境变量",
        "修",
    ]
    for fb in feedbacks:
        state = build_state(fb)
        result = should_continue_or_output(state)
        if result == "coder_agent":
            print(f"  ✅ human_feedback='{fb[:20]}...' → {result}")
        else:
            print(f"  ❌ human_feedback='{fb[:20]}...' → {result}（应为 coder_agent）")
            all_passed = False
    print()

    # 检测 3：边界值 —— None 和纯空格
    print("--- 检测 3：边界值 ---")
    for label, fb in [("None", None), ("纯空格", "   ")]:
        state = build_state(fb)
        result = should_continue_or_output(state)
        # None != "" → coder_agent; "   " != "" → coder_agent
        expected = "coder_agent"
        if result == expected:
            print(f"  ✅ human_feedback={label} → {result}")
        else:
            print(f"  ⚠ human_feedback={label} → {result}（预期 {expected}）")
    print()

    print(f"=== B04 验证 #02 {'全部通过' if all_passed else '存在失败'} ===")
    sys.exit(0 if all_passed else 1)
