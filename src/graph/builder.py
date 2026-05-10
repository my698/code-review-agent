# 图构建器 —— build_graph() 组装 10 节点 + 条件边，编译成可运行的 LangGraph 应用

from langgraph.graph import StateGraph,END
from graph.state import AgentState
from config import MAX_RETRY


#三条条件边的路由函数
def should_retry_or_human(state:AgentState)->str:
    """沙箱验证后的路由：通过->人工确认，失败->反思分析"""
    if state["sandbox_result"].exit_code == 0:
        return "human_review"
    return "reflect_node"

def should_continue_or_output(state:AgentState)->str:
    """人工确认后的路由：无意见->输出报告，有意见->重新修复"""
    if state["human_feedback"] == "":
        return "output_node"
    return "coder_agent"

def retry_or_fail(state:AgentState)->str:
    """反思后的路由：未达上限->重新修复，已达上限->输出失败报告"""
    if state["retry_count"] >= MAX_RETRY:
        return "output_node"
    return "coder_agent"