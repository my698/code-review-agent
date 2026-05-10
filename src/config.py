"""
统一配置模块
从 .env 文件读取所有环境变量，并导出为 Python 常量
注意：导入路径问题由 scripts/run.py 统一处理，本模块只负责读配置
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# 加载项目根目录下的 .env
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# LLM 配置
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

# 反思重试上限
MAX_RETRY = int(os.getenv("MAX_RETRY", "3"))

# 沙箱执行超时（秒）
SANDBOX_TIMEOUT = int(os.getenv("SANDBOX_TIMEOUT", "10"))

# 日志级别
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")