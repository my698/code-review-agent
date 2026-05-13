"""
项目统一入口 —— 驱动完整代码审查工作流
用法：python scripts/run.py
"""
import sys
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 把 src/ 加入 Python 搜索路径
SRC_DIR = str(PROJECT_ROOT / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from config import LLM_MODEL, DEEPSEEK_API_KEY, MAX_RETRY
from graph.builder import build_graph
from graph.state import INITIAL_STATE

# 测试用示例代码 —— 故意放了几个问题（硬编码密钥、pickle 反序列化、裸 except、无类型注解）
SAMPLE_CODE = """
import pickle

API_SECRET = "sk-abc123def456"

def load_user_data(filename):
    f = open(filename, "rb")
    data = pickle.load(f)
    f.close()
    return data

def divide(a, b):
    try:
        result = a / b
    except:
        result = 0
    return result

def process_items(items):
    result = []
    for i in range(len(items)):
        result.append(items[i].upper() + str(i))
    return result
"""

if __name__ == "__main__":
    print(f"=== 代码审查 Agent 启动 ===")
    print(f"  LLM 模型: {LLM_MODEL}")
    print(f"  API Key 已加载: {bool(DEEPSEEK_API_KEY)}")
    print(f"  最大重试次数: {MAX_RETRY}")
    print()

    # 1. 构建编译图
    print("正在构建工作流图...")
    app = build_graph()
    print("图编译完成，10 个节点 + 条件边已就位")
    print()

    # 2. 准备输入
    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = SAMPLE_CODE

    config = {"configurable": {"thread_id": "demo-001"}}

    print("=== 待审查代码 ===")
    print(SAMPLE_CODE)
    print()

    # 3. 运行工作流，处理 HITL 中断
    print("正在执行审查流程...")
    result = app.invoke(initial_state, config)

    # 检查是否在 human_review 前中断（LangGraph 1.1.x 中断不抛异常，需查 next）
    state_snapshot = app.get_state(config)
    if state_snapshot.next:
        print(">>> 暂停在 human_review 节点，等待人工确认...")
        print(">>> (演示模式) 自动批准修复结果")
        app.update_state(config, {"human_feedback": ""})
        result = app.invoke(None, config)

    # 4. 输出最终报告
    print()
    print("=== 最终审查报告 ===")
    report = result.get("final_report")
    if report:
        print(f"  状态: {report.status}")
        print(f"  修复前评分: {report.score_before}")
        print(f"  修复后评分: {report.score_after}")
        print(f"  沙箱通过: {report.sandbox_passed}")
        print(f"  重试次数: {report.retry_count}")
        print(f"  问题数: {len(report.action_items)}")
        print(f"  摘要: {report.summary}")
        if report.fixed_code:
            print()
            print("=== 修复后代码 ===")
            print(report.fixed_code)
    else:
        print("报告未生成，请检查上游流程")
