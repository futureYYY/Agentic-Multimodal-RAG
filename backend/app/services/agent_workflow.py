"""
基于 LangGraph 的 Agent 工作流 (Cyclic Graph)
"""

from typing import TypedDict, List, Annotated, Literal, Dict, Any
import operator
import json

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.graph.message import add_messages

from app.services.llm import LLMService
from app.tools import search_knowledge_base

# 定义状态
class AgentState(TypedDict):
    # 消息历史 (自动管理)
    messages: Annotated[List[BaseMessage], add_messages]
    
    # 业务上下文
    kb_ids: List[str]
    intent: str  # 'chat', 'qa'
    original_query: str # 用户原始问题
    current_plan: str # 当前执行计划
    
    # RAG 相关 (用于前端展示)
    rag_context: str
    citations: List[dict]
    retrieved_docs: List[Any] # 累计召回文档 (暂存)
    search_params: Dict[str, Any] # 前端传递的默认检索参数
    
    # 流程控制
    retry_count: int
    last_score: float # 最近一次评分
    execution_history: List[str] # 执行路径记录
    
    agent_steps: Annotated[List[dict], operator.add] # Append only
    final_system_prompt: str
    
    # LLM 配置 (动态注入)
    model_config: Dict[str, Any]

class AgentWorkflow:
    def __init__(self):
        self.llm_service = LLMService()
        self.tools = [search_knowledge_base]
        self.tool_node = ToolNode(self.tools)
        
        # 构建图
        workflow = StateGraph(AgentState)
        
        # 添加节点
        workflow.add_node("intent_analysis", self.analyze_intent)
        workflow.add_node("agent", self.agent_node)
        workflow.add_node("tools", self.run_tools)
        workflow.add_node("process_tool_output", self.process_tool_output) # Observation
        workflow.add_node("grading", self.grade_documents) # Scoring
        workflow.add_node("generate_chat", self.generate_chat)
        
        # 设置入口
        workflow.set_entry_point("intent_analysis")
        
        # 添加条件边: Intent Analysis -> (Agent or Chat)
        workflow.add_conditional_edges(
            "intent_analysis",
            self.route_based_on_intent,
            {
                "chat": "generate_chat",
                "qa": "agent"
            }
        )
        
        # 添加条件边: Agent -> (Tools or End)
        workflow.add_conditional_edges(
            "agent",
            self.should_continue,
            {
                "continue": "tools",
                "end": END
            }
        )
        
        # Tools -> Process Output
        workflow.add_edge("tools", "process_tool_output")
        
        # Process Output -> Grading
        workflow.add_edge("process_tool_output", "grading")
        
        # Grading -> Agent (Loop back)
        workflow.add_edge("grading", "agent")
        
        workflow.add_edge("generate_chat", END)
        
        self.app = workflow.compile()

    def _get_llm_service(self, state: AgentState) -> LLMService:
        """从 State 中获取 LLM 配置并创建服务实例"""
        config = state.get("model_config", {})
        if config and config.get("model_id"):
            # 如果配置中有 model_id，说明是自定义模型
            return LLMService(
                base_url=config.get("base_url"),
                api_key=config.get("api_key"),
                model=config.get("model_id")
            )
        return self.llm_service

    async def analyze_intent(self, state: AgentState, config: RunnableConfig):
        """意图分析节点"""
        # 获取最新的一条 HumanMessage
        last_message = state["messages"][-1]
        content = last_message.content
        
        # 简单规则快速判断
        if len(content.strip()) < 10 and content.strip().lower() in ["你好", "在吗", "hello", "hi", "help", "test"]:
            return {
                "intent": "chat", 
                "agent_steps": [{
                    "step": "decision",
                    "content": "检测到闲聊意图 (Fast Path)"
                }],
                "original_query": content,
                "retry_count": 0,
                "execution_history": []
            }

        # LLM 意图判断
        prompt = f"""Analyze the user input: "{content}"
        
Classify intent as:
- "chat": Greeting, small talk, or general knowledge question that DOES NOT require external documentation.
- "qa": Question that likely requires retrieving specific information from a knowledge base.

Output ONLY one word: "chat" or "qa".
"""
        try:
            llm = self._get_llm_service(state)
            # Use streaming=False to avoid "No generations found in stream" error with some local models
            response = await llm.get_chat_model(temperature=0.01, streaming=False).ainvoke([HumanMessage(content=prompt)], config=config)
            intent = response.content.strip().lower()
            if "qa" in intent or "knowledge" in intent:
                intent = "qa"
            else:
                intent = "chat"
        except Exception as e:
            print(f"Intent analysis failed: {e}")
            intent = "qa" # Fallback to QA
            
        new_steps = [{
            "step": "decision",
            "content": f"意图分析: {intent}"
        }]
        
        return {
            "intent": intent, 
            "agent_steps": new_steps,
            "original_query": content,
            "retry_count": 0,
            "execution_history": []
        }

    def route_based_on_intent(self, state: AgentState):
        return state["intent"]

    async def agent_node(self, state: AgentState, config: RunnableConfig):
        """Agent 思考节点"""
        # 准备 System Prompt
        system_msg = SystemMessage(content=f"""你是一个专业的智能助手，专门用于帮助用户从知识库中检索和解答问题。

你的工作流程如下：
1. **分析意图**：仔细分析用户的问题，确定需要查询什么信息。
2. **执行检索**：使用 `search_knowledge_base` 工具查询知识库。
   - 只需提供 `query` 参数。系统会自动处理知识库 ID 和检索配置。
   - 尽量使用准确的关键词。
   - 如果用户的问题比较复杂，可以拆解为多次查询。
3. **响应反馈**：
   - 检索完成后，系统会评估检索结果的相关性。
   - 如果系统提示 "检索结果相关性评分较低"，**你必须** 尝试改写查询关键词，并再次调用工具。
   - 在重试时，如果之前的尝试没有结果，请尝试在参数中降低 `score_threshold` (例如 0.1) 以获取更多结果。
   - 不要重复使用相同的查询。尝试不同的切入点。
4. **生成回答**：
   - 当检索结果被系统接受，或者系统提示 "已达到最大重试次数" 时，请基于现有的所有搜索结果回答用户的问题。
   - 严谨引用来源，回答要准确、完整。
   - 如果多次尝试后仍无结果，请诚实告知用户。

注意事项：
- 始终使用中文回答。
- 优先使用工具获取信息，不要编造事实。
- 在思考过程中，请明确输出你的决策逻辑 (例如 "Thinking: 收到重试提示，尝试改写关键词为...")。
- **重要**：最终回答用户时，**不要**包含 "Thinking: ..." 这样的思考过程块。只输出最终的回答内容。思考过程仅用于中间步骤。""")
        
        messages = state["messages"]
        invocation_messages = [system_msg] + messages
        
        llm = self._get_llm_service(state)
        # Use streaming=False to prevent crashes on local models
        model = llm.get_chat_model(streaming=False).bind_tools(self.tools)
        
        response = await model.ainvoke(invocation_messages, config=config)
        
        # 处理 Thinking 块：将其从 content 中剥离，放入 agent_steps
        import re
        content = response.content
        thinking_blocks = re.findall(r"Thinking:\s*(.*?)(?:\n\n|\Z)", content, re.DOTALL | re.IGNORECASE)
        
        # 清理 content 中的 Thinking 块
        clean_content = re.sub(r"Thinking:\s*.*?(?:\n\n|\Z)", "", content, flags=re.DOTALL | re.IGNORECASE).strip()
        
        # 如果清理后内容为空（只剩 Thinking），则保留原始内容或者设为占位符（视情况而定）
        # 但通常 Agent 会输出 Thinking 后再输出 Action 或 Answer
        if clean_content:
             response.content = clean_content
        
        steps = []
        if thinking_blocks:
            for block in thinking_blocks:
                steps.append({
                    "step": "thinking",
                    "content": block.strip()
                })
        else:
            steps.append({
                "step": "thinking",
                "content": "Agent 正在思考..."
            })
        
        if response.tool_calls:
            for tool_call in response.tool_calls:
                function_name = tool_call.get("name")
                function_args = tool_call.get("args")
                # 截断过长的参数显示
                args_str = str(function_args)
                if len(args_str) > 100:
                    args_str = args_str[:100] + "..."
                
                steps.append({
                    "step": "action",
                    "content": f"调用工具: {function_name}, 参数: {args_str}"
                })
        
        return {"messages": [response], "agent_steps": steps}

    async def run_tools(self, state: AgentState, config: RunnableConfig):
        """自定义工具执行节点，注入配置参数"""
        messages = state["messages"]
        last_message = messages[-1]
        
        if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
            return {}
            
        tool_outputs = []
        for tool_call in last_message.tool_calls:
            if tool_call["name"] == "search_knowledge_base":
                # Agent 生成的参数
                agent_args = tool_call["args"].copy()
                
                # 前端传递的默认参数
                search_params = state.get("search_params", {})
                
                # 最终参数：基础参数由 Agent 决定，但资源配置和上下文扩展等高级参数由系统注入
                final_args = {}
                
                # 1. 注入环境/资源参数 (用户/系统指定)
                final_args["kb_ids"] = state.get("kb_ids", [])
                
                # 2. 注入 Search Params (如果 Agent 没有指定，则使用默认值)
                # 注意：Agent 可能会 hallucinate 一些参数，我们需要小心处理
                # 这里我们允许 Agent 覆盖 search_mode 等策略参数，但对于 rerank_model_id 等资源参数优先使用 search_params
                
                # 注入基础检索参数
                if "top_k" in search_params and "top_k" not in agent_args:
                    final_args["top_k"] = search_params["top_k"]
                    
                if "score_threshold" in search_params and "score_threshold" not in agent_args:
                    final_args["score_threshold"] = search_params["score_threshold"]

                # 注入 Rerank 配置
                if "rerank_model_id" in search_params:
                    final_args["rerank_model_id"] = search_params["rerank_model_id"]
                
                # 注入上下文扩展参数 (通常 Agent 不会控制这个)
                if "context_window" in search_params:
                    final_args["context_window"] = search_params["context_window"]
                
                # 注入重排序参数 (如果 Agent 没指定)
                # 注意：tool definition 中参数名是 use_rerank，但前端通常传 rerank_enabled
                # 我们需要映射一下
                if "rerank_enabled" in search_params and "use_rerank" not in agent_args:
                     final_args["use_rerank"] = search_params["rerank_enabled"]
                
                if "rerank_top_k" in search_params and "rerank_top_k" not in agent_args:
                     final_args["rerank_top_k"] = search_params["rerank_top_k"]
                     
                if "rerank_score_threshold" in search_params and "rerank_score_threshold" not in agent_args:
                     final_args["rerank_score_threshold"] = search_params["rerank_score_threshold"]

                # 注入检索权重 (如果 Agent 没指定)
                if "vector_weight" in search_params and "vector_weight" not in agent_args:
                    final_args["vector_weight"] = search_params["vector_weight"]
                if "bm25_weight" in search_params and "bm25_weight" not in agent_args:
                    final_args["bm25_weight"] = search_params["bm25_weight"]
                
                # 3. 注入 Agent 决策参数 (覆盖前面的默认值，除了资源 ID)
                for k, v in agent_args.items():
                    # 保护资源 ID 不被覆盖
                    if k == "rerank_model_id" and "rerank_model_id" in final_args:
                        continue
                    final_args[k] = v
                
                # Execute tool
                try:
                    result = await search_knowledge_base.ainvoke(final_args, config=config)
                except Exception as e:
                    result = json.dumps({"error": str(e), "context": "", "citations": []})
                
                tool_outputs.append(
                    ToolMessage(
                        content=str(result),
                        name=tool_call["name"],
                        tool_call_id=tool_call["id"],
                    )
                )
        
        return {"messages": tool_outputs}

    def should_continue(self, state: AgentState):
        """决定是继续调用工具还是结束"""
        messages = state["messages"]
        last_message = messages[-1]
        
        # 如果有 tool_calls，则继续
        if last_message.tool_calls:
            return "continue"
        
        # 否则结束
        return "end"

    async def process_tool_output(self, state: AgentState):
        """处理工具输出，解析 JSON，记录 Observation"""
        messages = state["messages"]
        last_message = messages[-1] # ToolMessage
        
        if not isinstance(last_message, ToolMessage):
            return {}

        new_steps = []
        citations = []
        rag_context = ""
        
        try:
            # 解析工具返回的 JSON
            data = json.loads(last_message.content)
            rag_context = data.get("context", "")
            citations = data.get("citations", [])
            
            # 记录 Observation
            new_steps.append({
                "step": "observation",
                "content": f"检索到 {len(citations)} 条相关记录"
            })
            
        except Exception as e:
            print(f"Error parsing tool output: {e}")
            rag_context = str(last_message.content) # Fallback
            new_steps.append({
                "step": "observation",
                "content": f"检索出错或格式异常: {str(e)}"
            })
            
        return {
            "citations": citations, # 更新 citations
            "rag_context": rag_context,
            "agent_steps": new_steps
        }

    async def grade_documents(self, state: AgentState, config: RunnableConfig):
        """文档评分节点，处理重试逻辑"""
        citations = state.get("citations", [])
        
        # 0. 如果无结果
        if not citations:
             score = 0.0
        else:
            # 1. 评分
            query = state.get("original_query", "")
            context = state.get("rag_context", "")
            
            prompt = f"""你是一个文档相关性评分员。
用户问题: {query}
检索到的文档片段:
{context[:2000]}... (截断)

请对文档与问题的相关性进行评分 (0.0 到 1.0)。
- 1.0: 完美匹配，包含直接答案。
- 0.8: 高度相关，包含大部分信息。
- 0.5: 部分相关，可能需要推断。
- 0.0: 完全不相关。

只输出一个数字，例如: 0.85
"""
            try:
                llm = self._get_llm_service(state)
                response = await llm.get_chat_model(temperature=0.1).ainvoke([HumanMessage(content=prompt)], config=config)
                score_str = response.content.strip()
                import re
                match = re.search(r"0\.\d+|1\.0|0|1", score_str)
                score = float(match.group()) if match else 0.5
            except Exception as e:
                print(f"Grading failed: {e}")
                score = 0.5 # Fallback
            
        steps = [{
            "step": "reflection",
            "content": f"Score: {score} (相关性评分)"
        }]
        
        # 2. 检查重试逻辑
        messages = []
        retry_count = state.get("retry_count", 0)
        
        if score < 0.8: # 阈值 0.8
            if retry_count < 3:
                new_retry_count = retry_count + 1
                hint = f"Observation: 检索结果相关性评分较低 ({score})。请尝试改写查询或拆分问题进行重试。这是第 {new_retry_count}/3 次重试。"
                messages.append(HumanMessage(content=hint)) # 注入 HumanMessage 作为提示
                
                steps.append({
                    "step": "reflection",
                    "content": f"评分过低 ({score} < 0.8)，准备第 {new_retry_count} 次重试..."
                })
                
                return {
                    "last_score": score,
                    "retry_count": new_retry_count,
                    "messages": messages,
                    "agent_steps": steps
                }
            else:
                hint = f"Observation: 检索结果相关性评分较低 ({score})，且已达到最大重试次数。请基于现有信息尽力回答，并在回答中说明信息可能不完整。"
                messages.append(HumanMessage(content=hint))
                
                steps.append({
                    "step": "reflection",
                    "content": "已达到最大重试次数，将基于现有信息回答。"
                })
                
                return {
                    "last_score": score,
                    "messages": messages,
                    "agent_steps": steps
                }
        
        return {"last_score": score, "agent_steps": steps}

    async def generate_chat(self, state: AgentState, config: RunnableConfig):
        """闲聊生成节点"""
        system_prompt = """你是一个智能问答助手。请根据你的训练知识，准确、完整、有条理地回答用户的问题。使用中文回答。"""
        
        # 闲聊不涉及 RAG，直接结束
        # 这里其实应该调用 LLM 生成一个回复，但根据旧逻辑，似乎是返回 prompt 让前端/外部流式调用？
        # 旧逻辑：return {"final_system_prompt": system_prompt}
        # 新逻辑：我们需要生成回复。
        
        messages = state["messages"]
        llm = self._get_llm_service(state)
        # Use streaming=False to ensure compatibility
        model = llm.get_chat_model(streaming=False)
        response = await model.ainvoke([SystemMessage(content=system_prompt)] + messages, config=config)
        
        return {
            "messages": [response],
            "agent_steps": [{"step": "response", "content": "生成闲聊回复"}]
        }