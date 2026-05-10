#节点函数集合--图中所有节点的处理函数，按工作流排序

from graph.state import AgentState
from config import DEEPSEEK_API_KEY, LLM_MODEL
from langchain_deepseek import ChatDeepSeek
from langchain_core.messages import SystemMessage, HumanMessage
from models import (
    CodeAnalysis,
    ReviewResult,
    ReviewDimension,
    CriticSummary,
    CoderResult,
    SandboxResult,
    ReflectionResult,
    FinalReport,
)
import subprocess
import tempfile

llm = ChatDeepSeek(
    api_key=DEEPSEEK_API_KEY,
    model=LLM_MODEL,
    temperature=0.1,
)

def code_parser(state: AgentState)->dict:
    """入口节点：用llm提取原始代码的结构化信息，输出CodeAnalysis"""
    structured_llm = llm.with_structured_output(CodeAnalysis)
    analysis = structured_llm.invoke([
        SystemMessage(content = "你是一个代码结构分析专家，只做客观的结构提取，不给审查意见。"),
        HumanMessage(content = f"请分析一下代码结构：\n\n\n```{state['original_code']}```\n"),
    ])
    return {"code_analysis" : analysis}

def security_reviewer(state: AgentState)->dict:
    """安全审查员:从注入/加密/权限等角度审查代码"""
    structured_llm = llm.with_structured_output(ReviewResult)
    result = structured_llm.invoke([
        SystemMessage(content = "你是一个资深安全审计专家,专查注入漏洞、敏感信息泄露、加密缺陷、权限问题。"),
        HumanMessage(content = f"代码结构：{state['code_analysis']}\n\n原始代码：{state['original_code']}"),
    ])
    return {"review_results" : [result]}

def performance_reviewer(state: AgentState)->dict:
    """性能审查员：从时间复杂度/IO/重复计算等角度审查代码"""
    structured_llm = llm.with_structured_output(ReviewResult)
    result = structured_llm.invoke([
        SystemMessage(content = "你是一个资深性能优化专家，专查时间复杂度、空间浪费、冗余IO、重复计算"),
        HumanMessage(content = f"代码结构：{state['code_analysis']}\n\n原始代码：{state['original_code']}"),
    ])
    return {"review_results" : [result]}
    
def style_reviewer(state: AgentState) -> dict:
    """风格审查员：从命名/格式/PEP 8等角度审查代码"""
    structured_llm = llm.with_structured_output(ReviewResult)
    result = structured_llm.invoke([
        SystemMessage(content="你是资深Python代码规范专家，专查命名、PEP 8、类型注解、注释质量。"),
        HumanMessage(content=f"代码结构：{state['code_analysis']}\n\n原始代码：{state['original_code']}"),
    ])
    return {"review_results": [result]}

def critic_agent(state: AgentState)->dict:
    """汇总节点：对三路审查结果去重、排序、评分，输出统一修复方案"""
    structured_llm = llm.with_structured_output(CriticSummary)

    #把每条Issue展开成可读文本，critic需要看到具体内容才能去重
    issues_text = []
    for r in state['review_results']:#每个r，是一个审查员的review_result,即共循环三轮
        for issue in r.issues:#每个issue就是一个Issue，对应一个代码问题
            #将每个问题的下述信息提取整合成字符串文本，循环将所有问题的文本描述全部整理入issues_text
            issues_text.append(
                f"[{r.dimension.value}] 行{issue.lineno} {issue.severity.value}"
                f" | {issue.category.value} | {issue.description}"
                f"\n 代码：{issue.code_snippet}"
                f"\n 建议：{issue.suggestion}"
            )

    summary = structured_llm.invoke([
        SystemMessage(content =( 
            "你是代码审查主管。请对以下问题清单：\n"                       
            "1. 去重：多条指向同一行号+同类问题的合并为一条\n"             
            "2. 排序：按严重度(CRITICAL > HIGH > MEDIUM > LOW)优先，同级按行号\n"                                                    
            "3. 评分：根据问题数量和严重度打分(0-100)\n"                   
            "4. 每条生成可执行的 fix_instruction"
        )),
        HumanMessage(content =(
            f"原始代码：\n```\n{state['original_code']}\n```\n\n"
            f"问题清单 （共{sum(len(r.issues) for r in state['review_results'])}条）:\n"
            + "\n".join(issues_text)
        )),
    ])
    return {"critic_summary": summary}

def coder_agent(state: AgentState)->dict:
    """修复节点：按action_plan的fix_instruction逐一修改代码，输出CoderResult"""

    structured_llm = llm.with_structured_output(CoderResult)

    #将action_plan的每条修复指令展开成可读文本
    plan_text = []
    for item in state['critic_summary'].action_plan:
        plan_text.append(
            f" [{item.priority}] 行{item.lineno} | {item.severity.value}/{item.category.value}\n"
            f" 指令：{item.fix_instruction}"
        )

    extra_context = ""
    if state['reflection_notes']:
        extra_context += f"\n\n[上次失败反思]{state['reflection_notes']}"
    if state['human_feedback']:
        extra_context += f"\n\n[用户修改意见]{state['human_feedback']}"

    result = structured_llm.invoke([
        SystemMessage(content=(
            "你是 Python 代码修复专家。请按以下规则修改代码：\n"           
            "1. 严格按照 fix_instruction 逐一修改，不要重构其他部分\n"     
            "2. 保持原代码的缩进风格和整体结构\n"                          
            "3. 不要在修复代码周围添加注释标记（如 # FIXED）\n"            
            "4. 修改后代码必须是可直接运行的合法 Python 代码\n"            
            "5. 如果有 reflection_notes 或 human_feedback，优先参考其意见\n"
        )),
        HumanMessage(content=(
            f"原始代码：\n\n```{state['original_code']}```\n\n"
            f"修复计划：（共{len(state['critic_summary'].action_plan)}条：\n"
            + "\n".join(plan_text)
            + extra_context
        )),
    ])
    return {"coder_result": result}

def sandbox_executor(state: AgentState)->dict:
    """沙箱节点：执行修复后的代码，验证能否正常运行"""
    fixed_code = state['coder_result'].fixed_code

    #将修复代码写入临时文件
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(fixed_code)
        tmp_path = f.name #获取文件路径

    #在子程序中执行，设置超时防止死循环
    try:
        result = subprocess.run(
            ['python3', tmp_path],
            capture_output=True, text=True, timeout=10,
        )
        sandbox = SandboxResult(
            exit_code = result.returncode,
            stdout = result.stdout,
            stderr = result.stderr,
            passed = (result.returncode == 0),
        )
    except subprocess.TimeoutExpired:
        sandbox = SandboxResult(
            exit_code=-1,
            stdout='',
            stderr='执行超时（超过10秒）',
            passed=False,
        )
    return {'sandbox_result': sandbox}

def reflect_node(state: AgentState)->dict:
    """反思节点：分析沙箱失败原因，生成新的修复思路，retry_count+1"""

    reflect_llm = ChatDeepSeek(
        api_key=DEEPSEEK_API_KEY,
        model=LLM_MODEL,
        temperature=0.3,  #反思需要一点发散
    )
    structured_llm = reflect_llm.with_structured_output(ReflectionResult)

    #将上一轮修改记录展开成可读文本
    changes_text = []
    for ref in state['coder_result'].changes:
        changes_text.append(
            f"行{ref.lineno}: {ref.original} ->{ref.fixed}（{ref.reason}）"
        )

    reflection = structured_llm.invoke([
        SystemMessage(content=(
            "你是一个调试专家。修复后的代码在沙箱中执行失败了。\n"
            "请判断出失败类型（syntax_error/logic_error/new_bug/env_issue），\n"
            "找出根因，并提供新的修复策略。"
        )),
        HumanMessage(content=(
            f"原始代码：\n\n```{state['original_code']}```\n\n"
            f"上一轮修改：\n"+"\n".join(changes_text)+"\n\n"
            f"沙箱执行结果：exit_code={state['sandbox_result'].exit_code}\n"
            f"stdout={state['sandbox_result'].stdout}\n"
            f"stderr={state['sandbox_result'].stderr}\n"
        )),
    ])
    return {
        'reflection_notes': reflection.new_strategy,
        'retry_count': state['retry_count']+1,
    }

def human_review(state: AgentState) -> dict:                               
    """HITL 节点：LangGraph 在进入前自动暂停，等待用户确认或输入修改意见   
                                                                            
    用户确认（无意见）：human_feedback = ""  → 路由到 output_node          
    用户有修改意见：human_feedback = "xxx" → 路由回 coder_agent            
    human_feedback 在 graph.update_state() 时已写入，此节点直接透传。      
    """
    return {}


def output_node(state: AgentState) -> dict:
    """输出节点：组装 FinalReport，不调 LLM"""
    coder = state.get('coder_result')
    sandbox = state.get('sandbox_result')
    critic = state.get('critic_summary')

    fixed_code = coder.fixed_code if coder else ""
    changes = coder.changes if coder else []
    sandbox_passed = sandbox.passed if sandbox else False

    score_before = critic.score_before if critic else 100
    if sandbox_passed and changes:
        score_after = min(score_before + len(changes) * 3, 100)
    else:
        score_after = score_before

    report = FinalReport(
        original_code=state['original_code'],
        fixed_code=fixed_code,
        action_items=critic.action_plan if critic else [],
        score_before=score_before,
        score_after=score_after,
        sandbox_passed=sandbox_passed,
        retry_count=state['retry_count'],
        summary=critic.summary if critic else "",
        status="success" if sandbox_passed else "failed",
    )
    return {
        'final_report': report,
        'status': report.status,
    }

