# LangGraph 状态定义 —— AgentState TypedDict + INITIAL_STATE，10 个节点共享的唯一数据载体

from typing import Annotated, Optional, TypedDict
import operator

from models import (
    CodeAnalysis,
    ReviewResult,
    CriticSummary,
    CoderResult,
    SandboxResult,
    FinalReport,
)


class AgentState(TypedDict):
    """LangGraph 流程的唯一数据载体 —— 10 个节点共享的读写状态"""

    # 输入层 —— 用户提交的原始代码，只读不修改
    original_code: str

    # 解析结果 —— code_parser 输出，初始为 None（还没解析）
    code_analysis: Optional[CodeAnalysis]

    # 三路审查结果 —— security / performance / style 并行追加写入
    review_results: Annotated[list[ReviewResult], operator.add]

    # 汇总结果 —— critic_agent 去重排序后的统一修复方案
    critic_summary: Optional[CriticSummary]

    # 修复结果 —— coder_agent 输出，重试时覆盖
    coder_result: Optional[CoderResult]

    # 沙箱验证 —— sandbox_executor 执行修复后代码的结果
    sandbox_result: Optional[SandboxResult]

    # 反思控制 —— reflect_node 写入
    retry_count: int
    reflection_notes: str

    # 人工审核 —— human_review 节点接收用户输入
    human_feedback: str

    # 最终输出 —— output_node 组装
    final_report: Optional[FinalReport]
    status: str


# 初始状态 —— Optional 字段置 None，int 置 0，str 置 ""，list 置 []
INITIAL_STATE: AgentState = {
    "original_code": "",
    "code_analysis": None,
    "review_results": [],
    "critic_summary": None,
    "coder_result": None,
    "sandbox_result": None,
    "retry_count": 0,
    "reflection_notes": "",
    "human_feedback": "",
    "final_report": None,
    "status": "running",
}
