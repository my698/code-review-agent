"""
项目统一入口
所有脚本和测试都应通过此文件启动，它在导入任何模块前先把 src/ 加入路径
用法：python scripts/run.py
"""
import sys
from pathlib import Path

# 项目根目录（run.py 在 scripts/ 下，往上两级是项目根）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 把 src/ 加入 Python 搜索路径，必须在 import 任何项目模块之前执行
SRC_DIR = str(PROJECT_ROOT / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# 现在可以安全导入项目模块了
from config import LLM_MODEL, DEEPSEEK_API_KEY, MAX_RETRY, SANDBOX_TIMEOUT, LOG_LEVEL

if __name__ == "__main__":
    print(f"项目入口脚本启动成功")
    print(f"  LLM Model: {LLM_MODEL}")
    print(f"  API Key 已加载: {bool(DEEPSEEK_API_KEY)}")
    print(f"  最大重试次数: {MAX_RETRY}")
    print(f"  沙箱超时: {SANDBOX_TIMEOUT}秒")
    print(f"  日志级别: {LOG_LEVEL}")