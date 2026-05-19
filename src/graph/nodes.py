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
    # [Bug #5] LLM 结构化输出解析失败时返回 None，兜底为空 CodeAnalysis
    if analysis is None:
        analysis = CodeAnalysis()
    return {"code_analysis" : analysis}

def security_reviewer(state: AgentState)->dict:
    """安全审查员:从注入/加密/权限等角度审查代码"""
    structured_llm = llm.with_structured_output(ReviewResult)
    result = structured_llm.invoke([
        SystemMessage(content = (
            "你是资深安全审计专家。只报告确认存在的安全漏洞，不推测潜在风险。\n\n"
            "确认标准 —— 必须同时满足：\n"
            "1. 代码中存在危险操作（具体函数/模式见下方）\n"
            "2. 该危险操作的输入来自不可信数据源（用户输入/外部请求/文件读取）\n"
            "仅满足一条 → 不报告。\n\n"
            "危险操作清单：\n\n"
            "· SQL 拼接执行 — cursor.execute(sql_string) / raw() / extra() 且 sql_string 含用户输入\n"
            "  排除：参数化查询 cursor.execute(sql, (user_input,))\n\n"
            "· 命令执行 — os.system() / subprocess.call() / eval() / exec() / compile()\n"
            "  排除：subprocess.run([\"ls\", \"-l\"]) 参数列表已硬编码的情况\n\n"
            "· 路径拼接 — open(user_input) / open(path + user_input) 无校验\n"
            "  排除：open(\"config.json\") / open(os.path.join(BASE, x)) 路径前缀固定的\n\n"
            "· 硬编码凭据 — 代码中出现 password=\"xxx\" / api_key=\"sk-xxx\" / secret=\"xxx\" 等固定字符串\n"
            "  这是唯一不需要攻击面的条目 —— 凭据本身即是漏洞\n\n"
            "· 不安全反序列化 — pickle.load() / yaml.load() / marshal.load()\n"
            "  排除：json.load/loads（安全，不构成反序列化漏洞）\n\n"
            "· 不安全加密 — MD5/SHA1 做密码哈希 / 硬编码加密盐或 IV\n\n"
            "严重度：\n"
            "  CRITICAL — sql拼接/命令注入/硬编码生产凭据/pickle.load(用户输入)\n"
            "  HIGH — 其他确认漏洞\n"
            "  MEDIUM — 确认存在但危害低（如无实际利用路径的路径拼接）\n\n"
            "无确认漏洞 → issues 返回空列表 []\n"
            "不确定 → 不报告\n"
            "禁止\"可能\"\"潜在\"\"建议加强\"等推测措辞"
        )),
        HumanMessage(content = f"代码结构：{state['code_analysis']}\n\n原始代码：{state['original_code']}"),
    ])
    # [Bug #5] LLM 返回 None 时兜底为空列表
    if result is None:
        return {"review_results": []}
    # [Bug #4] 节点硬赋值 dimension，防止 LLM 把維度值写错
    result.dimension = ReviewDimension.SECURITY
    return {"review_results" : [result]}

def performance_reviewer(state: AgentState)->dict:
    """性能审查员：从时间复杂度/IO/重复计算等角度审查代码"""
    structured_llm = llm.with_structured_output(ReviewResult)
    result = structured_llm.invoke([
        SystemMessage(content = (
            "你是资深 Python 性能优化专家。审查代码中的性能问题，遵循以下原则：\n\n"
            "核心原则：只报告从代码本身可直接确认的低效模式。\n"
            " - 确定：循环内字符串 += 拼接 → 无论数据多大，都比 join 差。可以确认。\n"
            " - 不确定：如果数据量大可能会慢 → 无法从代码确认规模，不报告。\n"
            " - 不确定：建议用多线程加速 → 无法从代码确认是否有 IO 等待，不报告。\n"
            " - 如果无法确认是否存在性能问题 → 不报告，宁漏勿错。\n\n"
            "按以下五个维度审查：\n\n"
            "1. 时间复杂度 — 嵌套循环可用扁平化替代、O(n) 操作误写成 O(n²)\n"
            "2. 空间复杂度 — 不必要的中介列表/字典、一次性加载大文件可用迭代器\n"
            "3. I/O — 循环内重复打开文件、重复查询数据库\n"
            "4. 数据结构 — 列表查成员(if x in list)、list.pop(0) 等 O(n) 误用\n"
            "5. 重复计算 — 循环内重复调用同一纯函数、循环内不变的属性反复访问\n\n"
            "严重度：\n"
            " - CRITICAL — 嵌套循环无上界 / 无界读取到内存 / 循环内 N+1 查询\n"
            " - HIGH — 循环内重复 IO / 不必要的大对象拷贝 / O(n) 操作套 O(n)\n"
            " - MEDIUM — 低效数据结构(list 查成员) / 一般重复计算\n"
            " - LOW — 微小优化点，不改也可\n\n"
            "estimated_impact：尽量给出量化预估，格式如\"O(n²)→O(n)，n=10000 时提升约 100 倍\"。无法预估则留空。\n\n"
            "无确认问题 → issues 返回空列表 []。"
        )),
        HumanMessage(content = f"代码结构：{state['code_analysis']}\n\n原始代码：{state['original_code']}"),
    ])
    # [Bug #5] LLM 返回 None 时兜底为空列表
    if result is None:
        return {"review_results": []}
    # [Bug #4] 节点硬赋值 dimension
    result.dimension = ReviewDimension.PERFORMANCE
    return {"review_results" : [result]}

def style_reviewer(state: AgentState) -> dict:
    """风格审查员：从命名/格式/PEP 8等角度审查代码"""
    structured_llm = llm.with_structured_output(ReviewResult)
    result = structured_llm.invoke([
        SystemMessage(content=(
            "你是资深 Python 代码规范专家。审查代码中的风格问题。\n\n"
            "核心原则：报告客观的风格违规，不报告个人偏好。\n\n"
            "什么算客观违规：\n"
            " - 确定。违反 PEP 8 明确规定（行太长、命名格式、空白符等）\n"
            " - 确定。缺少必要的文档字符串或类型注解\n"
            " - 不确定。这个命名不够好 → 仅当命名明显误导时报告，品味差异不报\n"
            " - 不确定。这里可以更 Pythonic → 有明确 PEP 8/社区惯例支持则报，否则不报\n"
            " - 无法确认是否为客观违规 → 不报告，宁漏勿错\n\n"
            "按以下维度审查：\n"
            "1. 命名 — snake_case / CamelCase 约定\n"
            "2. 类型注解 — 公开函数是否缺少类型注解\n"
            "3. 格式 — 行长度、空行、缩进\n"
            "4. 注释 — 文档字符串缺失、注释与代码矛盾\n"
            "5. 函数设计 — 函数过长、参数过多\n"
            "6. 重复代码 — copy-paste 重复\n"
            "7. 异常处理 — bare except / 捕获过宽\n\n"
            "严重度：\n"
            "- HIGH — 可能导致 bug 的风格问题（bare except、注释与代码矛盾）\n"
            "- MEDIUM — 命名/格式违反 PEP 8\n"
            "- LOW — 建议性改进\n\n"
            "pep8_ref：违反 PEP 8 时填写条目编号如 \"E501\"。非 PEP 8 问题留空。\n\n"
            "无确认问题 → issues 返回空列表 []。"
        )),
        HumanMessage(content=f"代码结构：{state['code_analysis']}\n\n原始代码：{state['original_code']}"),
    ])
    # [Bug #5] LLM 返回 None 时兜底为空列表
    if result is None:
        return {"review_results": []}
    # [Bug #4] 节点硬赋值 dimension
    result.dimension = ReviewDimension.STYLE
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
        # [B01] critic 四分类判定：丢弃/[需人工]/[跳过]/修复
        SystemMessage(content =(
            "你是代码审查主管。请对以下问题清单：\n"
            "1. 去重：多条指向同一行号+同类问题的合并为一条\n"
            "2. 排序：按严重度(CRITICAL > HIGH > MEDIUM > LOW)优先，同级按行号\n"
            "3. 评分：根据问题数量和严重度打分(0-100)\n"
            "4. 对去重后的每条问题做判定：\n"
            "\n"
            "   第一步：该问题是否影响代码的正确性或安全性？\n"
            "   如果否 → 丢弃，不生成 action_item。\n"
            "   （纯风格、命名偏好、docstring/类型注解/注释缺失、等价写法建议、\n"
            "   代码组织建议等，只要不影响正确性和安全性，一律丢弃）\n"
            "\n"
            "   如果是 → 按以下三类处理：\n"
            "\n"
            "   [需人工] — 修复依赖当前文件之外的条件（满足任一即标注）：\n"
            "   · 需要新建文件（.env / config.py 等）\n"
            "   · 需要安装新依赖包\n"
            "   · 需要改动当前文件以外的代码\n"
            "   · 依赖项目基础设施（环境变量、密钥管理、数据库等）\n"
            "   fix_instruction 描述：问题 + 所需基础设施 + 建立后怎么改\n"
            "\n"
            "   [跳过] — 问题真实，但自动修复风险高于收益（满足任一即标注）：\n"
            "   · 修复涉及 3 行以上代码变更\n"
            "   · 修复会改变函数签名/类接口\n"
            "   · 修复涉及核心算法/状态机/并发逻辑\n"
            "   fix_instruction 描述问题 + 建议修复方向\n"
            "\n"
            "   修复 — 不属于上述两类：\n"
            "   · fix_instruction 必须包含行号 + FROM → TO\n"
            "   · 禁用\"建议\"\"考虑\"\"可改为\"等模糊词"
        )),
        HumanMessage(content =(
            f"原始代码：\n```\n{state['original_code']}\n```\n\n"
            f"问题清单 （共{sum(len(r.issues) for r in state['review_results'])}条）:\n"
            + "\n".join(issues_text)
        )),
    ])
    # [Bug #5] LLM 返回 None 时兜底
    if summary is None:
        return {}
    return {"critic_summary": summary}

def coder_agent(state: AgentState)->dict:
    """修复节点：按action_plan的fix_instruction逐一修改代码，输出CoderResult"""

    structured_llm = llm.with_structured_output(CoderResult)

    #将action_plan的每条修复指令展开成可读文本
    plan_text = []
    # [Bug #5] 消费端守卫：上游 critic_agent 可能返回 {}，避免 state['critic_summary'].action_plan 炸
    critic = state.get('critic_summary')
    if critic is None:
        return {}
    for item in critic.action_plan:
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
        # [B01] coder: 硬禁令二道防线 + 强力兜底
        # 硬禁令（#1-#3）：拦截一切来源，包括 critic 误判，零误杀
        # 兜底："核心原则"+"绝对不能"覆盖防手痒，不再单独列软禁令
        SystemMessage(content=(
            "你是 Python 代码修复专家。\n\n"
            "核心原则：最小改动 —— 只修改 fix_instruction 指定的问题行，其余代码一字不改。\n\n"
            # --- 硬禁令：绝对禁止，fix_instruction 要求也不行 ---
            "硬禁令（以下行为绝对禁止，包括 fix_instruction 要求的情况）：\n"
            "1. 禁止改名 —— 函数名、类名、变量名、参数名一律不动\n"
            "2. 禁止改签名 —— 不增删参数、不改返回类型\n"
            "3. 禁止改作用域 —— 不得把局部变量提升为全局、或把全局降为局部\n\n"
            # --- 执行规则：标签判定，硬禁令违规静默丢弃 ---
            "判断规则：\n"
            "- fix_instruction 含 [需人工] 或 [跳过] → 跳过，写入 skipped_items\n"
            "- fix_instruction 无标签 → 先过硬禁令检查：\n"
            "  · 违反硬禁令（需改名/改签名/改作用域）→ 跳过该条，静默丢弃\n"
            "  · 未违反硬禁令 → 严格按 fix_instruction 逐一修复\n"
            "- 参考优先级：human_feedback > reflection_notes > fix_instruction\n"
            "- 修改后代码必须是可直接运行的合法 Python 代码\n\n"
            # --- 强力兜底：防止任何自发多做 ---
            "你绝对不能做任何 fix_instruction 要求之外的改动。一个字都不要多改。"
        )),
        HumanMessage(content=(
            f"原始代码：\n\n```{state['original_code']}```\n\n"
            f"修复计划：（共{len(critic.action_plan)}条：\n"
            + "\n".join(plan_text)
            # 提示含标签条目必须跳过
            + "\n\n注意：含 [需人工] 或 [跳过] 标记的条目，跳过修改，将其内容原样放入 skipped_items 列表。"
            + extra_context
        )),
    ])
    # [Bug #5] LLM 返回 None 时兜底
    if result is None:
        return {}
    return {"coder_result": result}

def sandbox_executor(state: AgentState)->dict:
    """沙箱节点：执行修复后的代码，验证能否正常运行"""
    # [Bug #5] 消费端守卫：上游 coder_agent 可能返回 {}
    coder = state.get('coder_result')
    if coder is None:
        return {'sandbox_result': SandboxResult(exit_code=-1, stderr='修复代码为空', passed=False)}
    fixed_code = coder.fixed_code

    #将修复代码写入临时文件
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(fixed_code)
        tmp_path = f.name #获取文件路径

    #在子程序中执行，设置超时防止死循环
    try:
        result = subprocess.run(
            ['python3', '-W', 'error', tmp_path],
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
    # [Bug #5] 消费端守卫：上游 coder_agent 可能返回 {}
    coder = state.get('coder_result')
    changes_text = []
    if coder is not None:
        for ref in coder.changes:
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
    # [Bug #5] LLM 返回 None 时兜底
    if reflection is None:
        return {
            'reflection_notes': 'LLM 返回为空，无法分析失败原因',
            'retry_count': state['retry_count']+1,
        }
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
    # [B01-#04] 收集 [需人工] 和 [跳过] 的建议
    skipped = coder.skipped_items if coder else []

    score_before = critic.score_before if critic else 100
    if sandbox_passed:
        if changes:
            # [B03] 每处修复 +2，提升上限为剩余空间的一半（不过度膨胀）
            improvement = min(len(changes) * 2, (100 - score_before) // 2)
            score_after = min(score_before + improvement, 100)
        else:
            score_after = score_before
    else:
        # [B03] 沙箱失败扣 10 分
        score_after = max(score_before - 10, 0)

    # [B01-#04] 状态判定：有跳过项 → partial（沙箱通过但含 [需人工] 或 [跳过] 建议）
    if not sandbox_passed:
        status = "failed"
    elif skipped:
        status = "partial"
    else:
        status = "success"

    report = FinalReport(
        original_code=state['original_code'],
        fixed_code=fixed_code,
        action_items=critic.action_plan if critic else [],
        score_before=score_before,
        score_after=score_after,
        sandbox_passed=sandbox_passed,
        retry_count=state['retry_count'],
        summary=critic.summary if critic else "",
        status=status,
        skipped_items=skipped,  # [B01-#04] 透传需人工介入的建议
    )
    return {
        'final_report': report,
        'status': report.status,
    }

