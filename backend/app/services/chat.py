"""
对话服务 (Agent 实现)

集成混合检索（向量 + BM25）、分批 Rerank、上下文扩展
"""

from typing import List, Dict, Any, AsyncGenerator, Tuple
from app.schemas import Message, RecallResult
from app.services.llm import LLMService
from app.services.embedding import EmbeddingService
from app.services.vector_store import VectorStoreService
from app.services.rerank import RerankService, batch_rerank, expand_context
from app.services.retrieval import get_retrieval_service
from app.services.agent_workflow import AgentWorkflow
from langchain_core.messages import HumanMessage, AIMessage
from sqlmodel import Session, select
from app.core.database import engine
from app.models import KnowledgeBase, CustomModel


import os
import base64
import time
import json
import re
from app.core.config import get_settings

settings = get_settings()


class ChatService:
    """对话服务"""

    def __init__(self):
        self.llm_service = LLMService()
        self.embedding_service = EmbeddingService()
        self.vector_service = VectorStoreService()
        self.retrieval_service = get_retrieval_service()
        self.agent_workflow = AgentWorkflow()

    async def _retrieve_with_new_service(
        self,
        session: Session,
        query: str,
        kb_ids: List[str],
        top_k: int,
        score_threshold: float,
        search_mode: str,
        vector_weight: float,
        bm25_weight: float,
        rerank_enabled: bool,
        rerank_score_threshold: float,
        rerank_model_id: str,
        rerank_top_k: int,
        context_window: int,
    ) -> Tuple[List[Dict], Dict]:
        """
        使用新的统一检索服务进行检索

        流程：
        1. 混合检索（向量 + BM25）
        2. 分批 Rerank
        3. 上下文扩展
        """
        # 1. 混合检索
        results, metrics = await self.retrieval_service.search(
            session=session,
            query=query,
            kb_ids=kb_ids,
            search_mode=search_mode,
            top_k=top_k,
            score_threshold=score_threshold,
            vector_weight=vector_weight,
            bm25_weight=bm25_weight,
            use_cache=True,
            log_retrieval=True,
        )

        # 2. Rerank
        if rerank_enabled and results:
            rerank_model = None
            if rerank_model_id:
                rerank_model = session.get(CustomModel, rerank_model_id)

            rerank_service = RerankService(session)
            results = batch_rerank(
                query=query,
                candidates=results,
                rerank_service=rerank_service,
                model=rerank_model,
                top_k=rerank_top_k,
                score_threshold=rerank_score_threshold
            )
            metrics["rerank_count"] = len(results)

        # 3. 上下文扩展
        if results and context_window > 0:
            results = expand_context(
                top_chunks=results,
                session=session,
                window_size=context_window
            )
            metrics["expanded"] = True

        return results, metrics

    async def _chat_stream_agent(
        self,
        messages: List[Message],
        kb_ids: List[str],
        top_k: int,
        score_threshold: float,
        model_id: str,
        rerank_enabled: bool,
        rerank_score_threshold: float,
        rerank_model_id: str,
        search_mode: str = "hybrid",
        vector_weight: float = 0.7,
        bm25_weight: float = 0.3,
        rerank_top_k: int = 20,
        context_window: int = 1,
    ) -> AsyncGenerator[Tuple[str, Dict[str, Any]], None]:
        """
        Agent 模式流式对话 (LangGraph 实现)
        """
        # 1. 构造初始状态
        langchain_messages = []
        for msg in messages:
             if msg.role.value == "user":
                 langchain_messages.append(HumanMessage(content=msg.content))
             elif msg.role.value == "assistant":
                 langchain_messages.append(AIMessage(content=msg.content))
        
        search_params = {
            "top_k": top_k,
            "score_threshold": score_threshold,
            "search_mode": search_mode,
            "rerank_enabled": rerank_enabled,
            "rerank_score_threshold": rerank_score_threshold,
            "rerank_model_id": rerank_model_id,
            "rerank_top_k": rerank_top_k,
            "vector_weight": vector_weight,
            "bm25_weight": bm25_weight,
            "context_window": context_window
        }

        # 确保初始状态包含必要字段
        last_user_content = langchain_messages[-1].content if langchain_messages else ""

        initial_state = {
            "messages": langchain_messages,
            "kb_ids": kb_ids,
            "search_params": search_params,
            "retry_count": 0,
            "citations": [],
            "agent_steps": [],
            "original_query": last_user_content,
            "intent": "",
            "current_plan": "",
            "rag_context": "",
            "retrieved_docs": [],
            "execution_history": [],
            "last_score": 0.0,
            "final_system_prompt": "",
            "model_config": model_config
        }

        print(f"DEBUG_CHAT: Starting LangGraph Agent with params: {search_params}")

        try:
            async for event in self.agent_workflow.app.astream_events(initial_state, version="v1"):
                kind = event["event"]
                name = event["name"]
                data = event["data"]
                
                # 1. 处理流式 Token (Answer Chunk)
                if kind == "on_chat_model_stream":
                    # 过滤掉 intent_analysis 和 grading 的输出
                    node_name = event.get("metadata", {}).get("langgraph_node")
                    if node_name in ["agent", "generate_chat"]:
                         chunk = data["chunk"]
                         if chunk.content:
                             yield ("answer_chunk", {"content": chunk.content})

                # 2. 处理节点状态更新 (Steps & Citations)
                elif kind == "on_chain_end":
                    node_name = event.get("metadata", {}).get("langgraph_node")
                    if node_name in ["intent_analysis", "agent", "process_tool_output", "grading", "generate_chat"]:
                        outputs = data["output"]
                        if isinstance(outputs, dict):
                            # 发送步骤
                            if "agent_steps" in outputs:
                                for step in outputs["agent_steps"]:
                                     yield ("agent_thought", step)
                            
                            # 发送引用
                            if "citations" in outputs:
                                 yield ("rag_result", {"citations": outputs["citations"]})
                
                # 3. 处理工具开始 (Action)
                elif kind == "on_tool_start":
                    if name == "search_knowledge_base":
                        yield ("agent_thought", {
                            "step": "action", 
                            "content": f"调用检索工具..." 
                        })

            yield ("done", {"usage": {}})
            
        except Exception as e:
            print(f"Workflow error: {e}")
            import traceback
            traceback.print_exc()
            yield ("error", {"error": str(e)})

    async def _chat_stream_normal(
        self,
        messages: List[Message],
        kb_ids: List[str],
        top_k: int,
        score_threshold: float,
        model_id: str,
        rerank_enabled: bool,
        rerank_score_threshold: float,
        rerank_model_id: str,
        search_mode: str = "hybrid",
        vector_weight: float = 0.7,
        bm25_weight: float = 0.3,
        rerank_top_k: int = 20,
        context_window: int = 1,
        use_rewrite: bool = False,
    ) -> AsyncGenerator[Tuple[str, Dict[str, Any]], None]:
        """
        普通模式：
        1. 如果没有指定知识库 -> 直接 LLM 对话
        2. 如果指定了知识库 -> RAG (混合检索 -> Rerank -> 上下文扩展 -> Generate)
        """
        total_start_time = time.time()
        print(f"DEBUG_CHAT: Start _chat_stream_normal. KBs: {kb_ids}, Mode: {search_mode}, TopK: {top_k}, Rewrite: {use_rewrite}", flush=True)

        # Get user message
        user_message = None
        for msg in reversed(messages):
            if msg.role.value == "user":
                user_message = msg.content
                break

        if not user_message:
            yield ("error", {"message": "没有找到用户消息"})
            return

        # Prepare LLM Service
        current_llm_service = self.llm_service
        actual_model_id = model_id
        if model_id:
            with Session(engine) as session:
                custom_llm = session.get(CustomModel, model_id)
                if custom_llm:
                    current_llm_service = LLMService(
                        base_url=custom_llm.base_url,
                        api_key=custom_llm.api_key,
                        model=custom_llm.model_name
                    )
                    actual_model_id = custom_llm.model_name

        # Case 1: No Knowledge Base selected -> Direct Chat
        if not kb_ids:
            yield ("agent_thought", {
                "step": "response",
                "content": "未指定知识库，正在直接生成回答..."
            })

            chat_messages = [{"role": msg.role.value, "content": msg.content} for msg in messages]
            try:
                async for chunk in current_llm_service.generate_stream(
                    messages=chat_messages,
                    model_id=actual_model_id,
                ):
                    yield ("answer_chunk", {"content": chunk})
            except Exception as e:
                print(f"DEBUG_CHAT: Chat generation failed: {e}")
                yield ("error", {"error": f"生成回答失败: {str(e)}"})

            total_duration = time.time() - total_start_time
            yield ("agent_thought", {
                "step": "response",
                "content": "回答生成完成",
                "duration": total_duration,
                "cost": total_duration
            })
            yield ("done", {"usage": {}})
            return

        # Case 2: Knowledge Base selected -> RAG with new retrieval service
        
        # Query Rewrite
        search_query = user_message
        if use_rewrite:
            yield ("agent_thought", {
                "step": "thinking",
                "content": "正在进行问题改写..."
            })
            try:
                rewritten_query = await current_llm_service.rewrite_query(user_message)
                if rewritten_query:
                    search_query = rewritten_query
                    yield ("agent_thought", {
                        "step": "thinking",
                        "content": f"问题已改写为: {rewritten_query}"
                    })
            except Exception as e:
                print(f"DEBUG_CHAT: Rewrite failed: {e}")
                # Fallback to original query
        
        yield ("agent_thought", {
            "step": "thinking",
            "content": f"正在使用 {search_mode} 模式从 {len(kb_ids)} 个知识库中检索 (Top K: {top_k})..."
        })

        all_results = []
        original_results = []

        with Session(engine) as session:
            try:
                # 使用新的检索服务
                all_results, metrics = await self._retrieve_with_new_service(
                    session=session,
                    query=search_query,
                    kb_ids=kb_ids,
                    top_k=top_k,
                    score_threshold=score_threshold,
                    search_mode=search_mode,
                    vector_weight=vector_weight,
                    bm25_weight=bm25_weight,
                    rerank_enabled=rerank_enabled,
                    rerank_score_threshold=rerank_score_threshold,
                    rerank_model_id=rerank_model_id,
                    rerank_top_k=rerank_top_k,
                    context_window=context_window,
                )

                print(f"DEBUG_CHAT: Retrieved {len(all_results)} results, metrics: {metrics}")

            except Exception as e:
                print(f"Retrieve failed: {e}")
                yield ("error", {"error": f"检索失败: {str(e)}"})
                return

        yield ("agent_thought", {
            "step": "action",
            "content": f"检索完成，共找到 {len(all_results)} 条相关记录 (vector={metrics.get('vector_count', 0)}, bm25={metrics.get('bm25_count', 0)})。",
        })

        final_results = all_results

        # Sort
        if rerank_enabled and final_results and final_results[0].get("rerank_score") is not None:
            final_results.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
        else:
            final_results.sort(key=lambda x: x.get("score", 0), reverse=True)

        final_results = final_results[:rerank_top_k if rerank_enabled else top_k]

        # Send RAG Result
        def format_citation(r):
            return {
                "score": r.get("score", 0),
                "rerank_score": r.get("rerank_score"),
                "vector_score": r.get("vector_score"),
                "bm25_score": r.get("bm25_score"),
                "content": r.get("content", ""),
                "fileName": r.get("file_name", "未知"),
                "kb_name": r.get("kb_name", ""),
                "kb_id": r.get("kb_id"),
                "location": f"第 {r.get('page_number', 1)} 页" if r.get('page_number') else "",
                "fileId": r.get("file_id"),
                "image_path": r.get("image_path"),
                "imageUrl": f"/static/images/{r.get('image_path')}" if r.get("image_path") else None,
                "heading_text": r.get("heading_text"),
                "heading_level": r.get("heading_level"),
            }

        citations = [format_citation(r) for r in final_results]

        yield ("rag_result", {
            "citations": citations,
            "original_citations": []
        })

        # Generate Answer
        step_start_time = time.time()
        yield ("agent_thought", {
            "step": "response",
            "content": "正在基于检索结果生成回答..."
        })

        context_parts = []
        for i, r in enumerate(final_results, 1):
            context_parts.append(f"[{i}] 来源: {r.get('file_name', '未知')}\n{r.get('content', '')}")
        context = "\n\n".join(context_parts) if context_parts else "未找到相关信息"

        system_prompt = f"""你是一个智能问答助手，请根据以下参考资料回答用户的问题。
参考资料：
{context}
要求：
1. 基于参考资料回答，如果资料中没有相关信息，请如实说明
2. 回答要准确、完整、有条理
3. 适当引用来源（如"根据文档..."）
4. 使用中文回答"""

        chat_messages = [{"role": msg.role.value, "content": msg.content} for msg in messages]

        # Multimodal (Images)
        retrieved_images = []
        for r in final_results:
            img_path = r.get("image_path")
            if img_path:
                full_image_path = os.path.join(settings.IMAGE_DIR, img_path)
                if os.path.exists(full_image_path):
                    try:
                        with open(full_image_path, "rb") as img_file:
                            encoded_string = base64.b64encode(img_file.read()).decode('utf-8')
                            ext = os.path.splitext(img_path)[1].lower().replace('.', '')
                            if not ext:
                                ext = 'jpeg'
                            if ext == 'jpg':
                                ext = 'jpeg'
                            retrieved_images.append(f"data:image/{ext};base64,{encoded_string}")
                    except:
                        pass

        if retrieved_images and chat_messages:
            last_msg = chat_messages[-1]
            if last_msg['role'] == 'user':
                original_text = last_msg['content']
                new_content = [{"type": "text", "text": original_text}]
                for img_data in retrieved_images:
                    new_content.append({"type": "image_url", "image_url": {"url": img_data}})
                last_msg['content'] = new_content

        try:
            async for chunk in current_llm_service.generate_stream(
                messages=chat_messages,
                system_prompt=system_prompt,
                model_id=actual_model_id,
            ):
                yield ("answer_chunk", {"content": chunk})
        except Exception as e:
            print(f"DEBUG_CHAT: Chat generation failed: {e}")
            yield ("error", {"error": f"生成回答失败: {str(e)}"})
            return

        total_duration = time.time() - total_start_time
        yield ("agent_thought", {
            "step": "response",
            "content": "回答生成完成",
            "duration": time.time() - step_start_time,
            "cost": total_duration
        })
        yield ("done", {"usage": {}})

    async def _chat_stream_langgraph(
        self,
        messages: List[Message],
        kb_ids: List[str],
        top_k: int,
        score_threshold: float,
        model_id: str,
        **kwargs
    ) -> AsyncGenerator[Tuple[str, Dict[str, Any]], None]:
        """
        基于 LangGraph 的 Agent 模式流式对话
        """
        from app.services.agent_workflow import AgentWorkflow
        from langchain_core.messages import HumanMessage, AIMessage
        
        # 1. 初始化 Workflow
        workflow = AgentWorkflow()
        
        # 2. 构造初始状态
        # 限制历史对话长度，防止上下文爆炸
        # 只保留最近的 N 轮对话 (N * 2 条消息)
        max_history_msgs = settings.MAX_CHAT_HISTORY_ROUNDS * 2
        effective_messages = messages[-max_history_msgs:] if len(messages) > max_history_msgs else messages
        
        langchain_messages = []
        for msg in effective_messages:
            if msg.role.value == "user":
                langchain_messages.append(HumanMessage(content=msg.content))
            elif msg.role.value == "assistant":
                langchain_messages.append(AIMessage(content=msg.content))
        
        # 确保 kb_ids 不为空 (兜底)
        if not kb_ids:
            with Session(engine) as session:
                all_kbs = session.exec(select(KnowledgeBase).where(KnowledgeBase.is_deleted == False)).all()
                kb_ids = [kb.id for kb in all_kbs]

        # 1.5 获取模型配置
        model_config = {}
        if model_id:
            with Session(engine) as session:
                custom_llm = session.get(CustomModel, model_id)
                if custom_llm:
                    model_config = {
                        "base_url": custom_llm.base_url,
                        "api_key": custom_llm.api_key,
                        "model_id": custom_llm.model_name
                    }

        # 构造检索参数
        search_params = {
            "top_k": top_k,
            "score_threshold": score_threshold,
            "search_mode": kwargs.get("search_mode", "hybrid"),
            "rerank_enabled": kwargs.get("rerank_enabled", False),
            "rerank_score_threshold": kwargs.get("rerank_score_threshold", 0.0),
            "rerank_model_id": kwargs.get("rerank_model_id"),
            "vector_weight": kwargs.get("vector_weight", 0.7),
            "bm25_weight": kwargs.get("bm25_weight", 0.3),
            "rerank_top_k": kwargs.get("rerank_top_k", 20),
            "context_window": kwargs.get("context_window", 1),
        }

        initial_state = {
            "messages": langchain_messages,
            "kb_ids": kb_ids,
            "search_params": search_params,
            "retry_count": 0,
            "agent_steps": [],
            "intent": "qa", # Default, will be analyzed
            "original_query": langchain_messages[-1].content if langchain_messages else "",
            "execution_history": [],
            "citations": [],
            "retrieved_docs": [],
            "last_score": 0.0,
            "rag_context": "",
            "final_system_prompt": "",
            "model_config": model_config
        }
        
        # 3. 执行并流式输出
        last_citations = []
        has_streamed = False
        
        try:
            async for event in workflow.app.astream_events(initial_state, version="v1"):
                kind = event["event"]
                name = event["name"]
                data = event["data"]
                
                # 安全检查：data 必须是字典
                if not isinstance(data, dict):
                    continue

                # 3.1 监听 LLM 生成 (流式回答)
                if kind == "on_chat_model_stream":
                    # 过滤掉 intent_analysis 和 grading 的输出
                    node_name = event.get("metadata", {}).get("langgraph_node")
                    
                    # 只有 agent 或 generate_chat 节点的 LLM 输出才是给用户的回答
                    if node_name in ["agent", "generate_chat"]:
                        chunk = data.get("chunk")
                        if chunk and chunk.content:
                            yield ("answer_chunk", {"content": chunk.content})
                            has_streamed = True
                
                # 3.2 监听 Node 输出 (Thinking/Decision/Action)
                elif kind == "on_chain_end":
                    node_name = event.get("metadata", {}).get("langgraph_node")
                    
                    # 监听所有关键节点的输出
                    if node_name in ["intent_analysis", "agent", "process_tool_output", "generate_chat"]:
                        output = data.get("output")
                        if output and isinstance(output, dict):
                            # 处理 agent_steps
                            steps = output.get("agent_steps", [])
                            if steps:
                                for step in steps:
                                     yield ("agent_thought", step)
                        
                            # NEW: 如果没有流式输出过 (streaming=False)，在这里发送完整内容
                            # 仅针对 agent 和 generate_chat 节点
                            if node_name in ["agent", "generate_chat"]:
                                # 检查是否已经流式输出过 (通过判断是否有 chunk 发送)
                                # 注意：如果是流式模式，on_chat_model_stream 应该已经发送了 answer_chunk
                                # 但为了保险起见，我们可以检查 has_streamed 标记
                                # 然而，has_streamed 在这里可能无法精确控制（因为是 loop）
                                # 更好的方式是：如果 streaming=False (我们可以从 model_config 判断，或者假设如果没有 stream event 就是 False)
                                # 简单起见，利用 has_streamed 变量
                                if not has_streamed:
                                    messages = output.get("messages", [])
                                    if messages:
                                        last_msg = messages[-1]
                                        # 检查是否为 AIMessage 且有 content
                                        content = getattr(last_msg, "content", "")
                                        tool_calls = getattr(last_msg, "tool_calls", [])
                                        
                                        # 只有当有内容且没有工具调用时才发送
                                        if content and not tool_calls:
                                             yield ("answer_chunk", {"content": content})
                                             has_streamed = True # 避免重复
                            
                            # 处理 citations (只有变化时才发送)
                            citations = output.get("citations")
                            if citations and citations != last_citations:
                                yield ("rag_result", {
                                    "citations": citations,
                                    "original_citations": []
                                })
                                last_citations = citations

                # 3.3 错误处理 (Optional)
                # if kind == "on_chain_error": ...
                
        except Exception as e:
            print(f"LangGraph Error: {e}")
            yield ("error", {"error": f"Agent 执行出错: {str(e)}"})
            
        yield ("done", {"usage": {}})

    async def chat_stream(
        self,
        messages: List[Message],
        kb_ids: List[str],
        use_rewrite: bool = False,
        mode: str = "chat",
        top_k: int = 50,
        score_threshold: float = 0.0,
        model_id: str = None,
        rerank_enabled: bool = False,
        rerank_score_threshold: float = 0.0,
        rerank_model_id: str = None,
        search_mode: str = "hybrid",
        vector_weight: float = 0.7,
        bm25_weight: float = 0.3,
        rerank_top_k: int = 20,
        context_window: int = 1,
    ) -> AsyncGenerator[Tuple[str, Dict[str, Any]], None]:
        """
        流式对话

        Args:
            messages: 消息列表
            kb_ids: 知识库 ID 列表
            use_rewrite: 是否使用问题改写
            mode: 对话模式 (chat/agent)
            top_k: 检索数量
            score_threshold: 分数阈值
            model_id: LLM 模型 ID
            rerank_enabled: 是否启用 Rerank
            rerank_score_threshold: Rerank 分数阈值
            rerank_model_id: Rerank 模型 ID
            search_mode: 检索模式 (vector/fulltext/hybrid)
            vector_weight: 向量权重
            bm25_weight: BM25 权重
            rerank_top_k: Rerank 返回数量
            context_window: 上下文窗口大小
        """
        import sys
        print(f"DEBUG_CHAT: Start chat_stream. Mode: {mode}, SearchMode: {search_mode}, TopK: {top_k}", flush=True)
        sys.stdout.flush()

        if mode == "agent":
            # 使用 LangGraph 新流程
            async for item in self._chat_stream_langgraph(
                messages=messages,
                kb_ids=kb_ids,
                top_k=top_k,
                score_threshold=score_threshold,
                model_id=model_id,
                search_mode=search_mode,
                rerank_enabled=rerank_enabled,
                rerank_score_threshold=rerank_score_threshold,
                rerank_model_id=rerank_model_id,
                vector_weight=vector_weight,
                bm25_weight=bm25_weight,
                rerank_top_k=rerank_top_k,
                context_window=context_window
            ):
                yield item
        else:
            # Normal mode (includes "chat" or any other non-agent mode)
            async for item in self._chat_stream_normal(
                messages=messages,
                kb_ids=kb_ids,
                top_k=top_k,
                score_threshold=score_threshold,
                model_id=model_id,
                rerank_enabled=rerank_enabled,
                rerank_score_threshold=rerank_score_threshold,
                rerank_model_id=rerank_model_id,
                search_mode=search_mode,
                vector_weight=vector_weight,
                bm25_weight=bm25_weight,
                rerank_top_k=rerank_top_k,
                context_window=context_window,
                use_rewrite=use_rewrite,
            ):
                yield item
