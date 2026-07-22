"""
对话服务 (Agent 实现)
"""

from typing import List, Dict, Any, AsyncGenerator, Tuple
from app.schemas import Message, RecallResult
from app.services.llm import LLMService
from app.services.embedding import EmbeddingService
from app.services.vector_store import VectorStoreService
from app.services.rerank import RerankService
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
# ... (rest of the class)
    """对话服务"""

    def __init__(self):
        self.llm_service = LLMService()
        self.embedding_service = EmbeddingService()
        self.vector_service = VectorStoreService()
        # RerankService 需要 session，这里我们在使用时动态创建或传入
        # 或者简化为静态方法/每次创建

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
    ) -> AsyncGenerator[Tuple[str, Dict[str, Any]], None]:
        """
        Agent 模式流式对话 (HyDE -> Rerank -> Generate)
        """
        total_start_time = time.time()
        print(f"DEBUG_CHAT: Start _chat_stream_agent. TopK: {top_k}", flush=True)

        # Get user message
        user_message = None
        for msg in reversed(messages):
            if msg.role.value == "user":
                user_message = msg.content
                break
        
        if not user_message:
            print("DEBUG_CHAT: No user message found")
            yield ("error", {"error": "没有找到用户消息"})
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

        # Step 1: Intent Recognition & Splitting
        step_start_time = time.time()
        yield ("agent_thought", {
            "step": "thinking",
            "content": "正在进行意图识别与任务拆分..."
        })

        split_prompt = f"""You are an intelligent assistant. Analyze the user's question.
User Question: {user_message}

Task:
1. Determine intent: "chat" (greeting/small talk/general knowledge) or "rag" (requires specific information/facts from knowledge base).
2. If "rag", split the question into sub-questions (max 3) for retrieval.
   - Sub-questions should be self-contained.
   - If simple, use 1 sub-question.
3. Output JSON ONLY.

JSON Format:
{{
  "intent": "chat" | "rag",
  "sub_questions": ["sub_q1", "sub_q2", ...]
}}
"""
        intent = "rag"
        sub_questions = [user_message]
        
        try:
            print("DEBUG_CHAT: Requesting intent split...", flush=True)
            response = await current_llm_service.generate(
                messages=[{"role": "user", "content": split_prompt}],
                model_id=actual_model_id
            )
            # Try to find JSON in the response
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                intent = data.get("intent", "rag").lower()
                sub_questions = data.get("sub_questions", [user_message])
                if not isinstance(sub_questions, list):
                    sub_questions = [str(sub_questions)]
                # Ensure max 3
                sub_questions = sub_questions[:3]
            
            yield ("agent_thought", {
                "step": "decision",
                "content": f"意图: {intent}, 拆分为 {len(sub_questions)} 个子问题: {sub_questions}",
                "duration": time.time() - step_start_time
            })
        except Exception as e:
            print(f"DEBUG_CHAT: Split failed: {e}", flush=True)
            yield ("agent_thought", {
                "step": "decision",
                "content": "意图识别失败，默认执行全量检索。",
                "duration": time.time() - step_start_time
            })

        # Handle Chat Intent
        if intent == "chat":
             step_start_time = time.time()
             yield ("agent_thought", {
                "step": "response",
                "content": "正在直接生成回答..."
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
                "duration": time.time() - step_start_time,
                "cost": total_duration
             })
             yield ("done", {"usage": {}})
             return

        # Handle RAG Intent
        all_results = []
        seen_ids = set()
        
        # Step 2: HyDE & Retrieval Loop
        yield ("agent_thought", {
            "step": "thinking",
            "content": f"正在并行处理 {len(sub_questions)} 个子任务 (Top K: {top_k})..."
        })
        
        # Prepare KB info
        kb_names = []
        with Session(engine) as session:
            if not kb_ids:
                all_kbs = session.exec(select(KnowledgeBase).where(KnowledgeBase.is_deleted == False)).all()
                kb_ids = [kb.id for kb in all_kbs]
                kb_names = [kb.name for kb in all_kbs]
                print(f"DEBUG_CHAT: Auto-selected KBs: {kb_ids}")
            else:
                for kb_id in kb_ids:
                    kb = session.get(KnowledgeBase, kb_id)
                    if kb: kb_names.append(kb.name)
        
        # Determine Embedding Service (use first KB's config)
        current_embedding_service = self.embedding_service
        if kb_ids:
            with Session(engine) as session:
                first_kb = session.get(KnowledgeBase, kb_ids[0])
                if first_kb:
                    emb_model_id = first_kb.embedding_model
                    custom_model = session.get(CustomModel, emb_model_id)
                    if custom_model:
                        current_embedding_service = EmbeddingService(
                            base_url=custom_model.base_url,
                            api_key=custom_model.api_key,
                            model=custom_model.model_name
                        )

        # Loop through sub-questions
        for i, sub_q in enumerate(sub_questions):
            step_start_time = time.time()
            print(f"DEBUG_CHAT: Processing sub-question {i+1}: {sub_q}")
            
            # HyDE
            hyde_query = sub_q
            try:
                hyde_prompt = f"Please write a passage to answer the question: {sub_q}\nPassage:"
                hypothesis = await current_llm_service.generate(
                    messages=[{"role": "user", "content": hyde_prompt}],
                    model_id=actual_model_id
                )
                if hypothesis:
                    hyde_query = hypothesis
                    yield ("agent_thought", {
                        "step": "thinking",
                        "content": f"子问题 {i+1} HyDE 生成完成:\n{hypothesis}",
                        "duration": time.time() - step_start_time
                    })
            except Exception as e:
                print(f"DEBUG_CHAT: HyDE failed for sub_q {i}: {e}")
            
            # Embed & Retrieve
            try:
                query_vector = await current_embedding_service.embed_query(hyde_query)
                
                # Query all KBs
                with Session(engine) as session:
                    kb_names_map = {kb.id: kb.name for kb in session.exec(select(KnowledgeBase).where(KnowledgeBase.id.in_(kb_ids))).all()}
                    
                    for kb_id in kb_ids:
                        results = self.vector_service.query(
                            kb_id=kb_id,
                            query_vector=query_vector,
                            top_k=top_k,
                            score_threshold=score_threshold
                        )
                        for r in results:
                            # Deduplicate
                            if r["id"] not in seen_ids:
                                seen_ids.add(r["id"])
                                r["kb_name"] = kb_names_map.get(kb_id, "未知")
                                r["kb_id"] = kb_id
                                r["sub_question"] = sub_q # Track origin
                                all_results.append(r)
            except Exception as e:
                print(f"Retrieve failed for sub_q {i}: {e}")

        yield ("agent_thought", {
            "step": "action",
            "content": f"所有子任务检索完成，共找到 {len(all_results)} 条候选记录 (将重排序优选 Top {top_k})。",
        })

        # Step 3: Rerank & Finalize
        final_results = all_results
        original_results = []
        
        if rerank_enabled and all_results:
             step_start_time = time.time()
             yield ("agent_thought", {
                "step": "thinking",
                "content": "正在进行全局重排序..."
             })
             
             sorted_initial = sorted(all_results, key=lambda x: x["score"], reverse=True)
             original_results = sorted_initial

             with Session(engine) as session:
                 try:
                     rerank_service = RerankService(session)
                     rerank_model = None
                     if rerank_model_id:
                        rerank_model = session.get(CustomModel, rerank_model_id)

                     candidates = [r["content"] for r in all_results]
                     # Rerank against ORIGINAL user query (or combined sub-questions?)
                     # Using original user_message is usually best for global relevance.
                     rerank_results = rerank_service.rerank(user_message, candidates, model=rerank_model)
                     
                     if rerank_results:
                         new_results = []
                         for rr in rerank_results:
                             idx = rr.get("index")
                             score = rr.get("score", 0.0)
                             if idx is not None and isinstance(idx, int) and 0 <= idx < len(all_results):
                                 if score >= rerank_score_threshold:
                                     item = all_results[idx].copy()
                                     item["rerank_score"] = score
                                     new_results.append(item)
                         final_results = new_results
                 except Exception as e:
                     print(f"DEBUG_CHAT: Rerank failed: {e}")
             
             yield ("agent_thought", {
                "step": "decision",
                "content": f"重排序完成，保留 {len(final_results)} 条高分结果。",
                "duration": time.time() - step_start_time
             })

        # Sort and Slice
        if rerank_enabled and final_results and "rerank_score" in final_results[0]:
            final_results.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
        else:
            final_results.sort(key=lambda x: x["score"], reverse=True)
            
        final_results = final_results[:top_k] # Use top_k

        # Send RAG Result
        def format_citation(r):
            return {
                "score": r["score"],
                "rerank_score": r.get("rerank_score"),
                "content": r["content"],
                "fileName": r["metadata"].get("file_name", "未知"),
                "kb_name": r["kb_name"],
                "kb_id": r.get("kb_id"),
                "location": r["metadata"].get("location_info", ""),
                "fileId": r["metadata"].get("file_id"),
                "image_path": r["metadata"].get("image_path"),
                "imageUrl": f"/static/images/{r['metadata'].get('image_path')}" if r["metadata"].get("image_path") else None
            }

        citations = [format_citation(r) for r in final_results]
        original_citations = []
        if rerank_enabled:
             original_citations = [format_citation(r) for r in original_results]

        yield ("rag_result", {
            "citations": citations,
            "original_citations": original_citations
        })

        # Step 4: Generate Answer
        step_start_time = time.time()
        yield ("agent_thought", {
            "step": "response",
            "content": "正在基于检索结果生成回答..."
        })

        context_parts = []
        for i, r in enumerate(final_results, 1):
            context_parts.append(f"[{i}] 来源: {r['metadata'].get('file_name', '未知')}\n{r['content']}")
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
        for r in all_results: 
            img_path = r["metadata"].get("image_path")
            if img_path:
                 full_image_path = os.path.join(settings.IMAGE_DIR, img_path)
                 if os.path.exists(full_image_path):
                     try:
                         with open(full_image_path, "rb") as img_file:
                             encoded_string = base64.b64encode(img_file.read()).decode('utf-8')
                             ext = os.path.splitext(img_path)[1].lower().replace('.', '')
                             if not ext: ext = 'jpeg'
                             if ext == 'jpg': ext = 'jpeg'
                             retrieved_images.append(f"data:image/{ext};base64,{encoded_string}")
                     except: pass

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
    ) -> AsyncGenerator[Tuple[str, Dict[str, Any]], None]:
        """
        普通模式：
        1. 如果没有指定知识库 -> 直接 LLM 对话
        2. 如果指定了知识库 -> RAG (Embed -> Retrieve -> Rerank -> Generate)
        不进行意图识别、不进行 HyDE、不自动选择所有知识库。
        """
        total_start_time = time.time()
        print(f"DEBUG_CHAT: Start _chat_stream_normal. Messages: {len(messages)}, KBs: {kb_ids}, TopK: {top_k}", flush=True)

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

        # Case 2: Knowledge Base selected -> RAG
        # Prepare KB info for display/embedding config
        kb_names_map = {}
        with Session(engine) as session:
            kbs = session.exec(select(KnowledgeBase).where(KnowledgeBase.id.in_(kb_ids))).all()
            kb_names_map = {kb.id: kb.name for kb in kbs}
        
        # Determine Embedding Service (use first KB's config)
        current_embedding_service = self.embedding_service
        if kb_ids:
            with Session(engine) as session:
                first_kb = session.get(KnowledgeBase, kb_ids[0])
                if first_kb:
                    emb_model_id = first_kb.embedding_model
                    custom_model = session.get(CustomModel, emb_model_id)
                    if custom_model:
                        current_embedding_service = EmbeddingService(
                            base_url=custom_model.base_url,
                            api_key=custom_model.api_key,
                            model=custom_model.model_name
                        )

        yield ("agent_thought", {
            "step": "thinking",
            "content": f"正在从 {len(kb_ids)} 个知识库中检索 (Top K: {top_k})..."
        })

        all_results = []
        seen_ids = set()
        
        # Embed & Retrieve
        try:
            query_vector = await current_embedding_service.embed_query(user_message)
            
            for kb_id in kb_ids:
                results = self.vector_service.query(
                    kb_id=kb_id,
                    query_vector=query_vector,
                    top_k=top_k,
                    score_threshold=score_threshold
                )
                for r in results:
                    # Deduplicate
                    if r["id"] not in seen_ids:
                        seen_ids.add(r["id"])
                        r["kb_name"] = kb_names_map.get(kb_id, "未知")
                        r["kb_id"] = kb_id
                        all_results.append(r)
        except Exception as e:
            print(f"Retrieve failed: {e}")
            yield ("error", {"error": f"检索失败: {str(e)}"})
            return

        yield ("agent_thought", {
            "step": "action",
            "content": f"检索完成，共找到 {len(all_results)} 条相关记录。",
        })

        # Rerank & Finalize
        final_results = all_results
        original_results = []
        
        if rerank_enabled and all_results:
             step_start_time = time.time()
             yield ("agent_thought", {
                "step": "thinking",
                "content": "正在进行重排序..."
             })
             
             # Sort by vector score first for baseline
             sorted_initial = sorted(all_results, key=lambda x: x["score"], reverse=True)
             original_results = sorted_initial

             with Session(engine) as session:
                 try:
                     rerank_service = RerankService(session)
                     rerank_model = None
                     if rerank_model_id:
                        rerank_model = session.get(CustomModel, rerank_model_id)

                     candidates = [r["content"] for r in all_results]
                     rerank_results = rerank_service.rerank(user_message, candidates, model=rerank_model)
                     
                     if rerank_results:
                         new_results = []
                         for rr in rerank_results:
                             idx = rr.get("index")
                             score = rr.get("score", 0.0)
                             if idx is not None and isinstance(idx, int) and 0 <= idx < len(all_results):
                                 if score >= rerank_score_threshold:
                                     item = all_results[idx].copy()
                                     item["rerank_score"] = score
                                     new_results.append(item)
                         final_results = new_results
                 except Exception as e:
                     print(f"DEBUG_CHAT: Rerank failed: {e}")
             
             yield ("agent_thought", {
                "step": "decision",
                "content": f"重排序完成，保留 {len(final_results)} 条高分结果。",
                "duration": time.time() - step_start_time
             })

        # Sort and Slice
        if rerank_enabled and final_results and "rerank_score" in final_results[0]:
            final_results.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
        else:
            final_results.sort(key=lambda x: x["score"], reverse=True)
            
        final_results = final_results[:top_k]

        # Send RAG Result
        def format_citation(r):
            return {
                "score": r["score"],
                "rerank_score": r.get("rerank_score"),
                "content": r["content"],
                "fileName": r["metadata"].get("file_name", "未知"),
                "kb_name": r["kb_name"],
                "kb_id": r.get("kb_id"),
                "location": r["metadata"].get("location_info", ""),
                "fileId": r["metadata"].get("file_id"),
                "image_path": r["metadata"].get("image_path"),
                "imageUrl": f"/static/images/{r['metadata'].get('image_path')}" if r["metadata"].get("image_path") else None
            }

        citations = [format_citation(r) for r in final_results]
        original_citations = []
        if rerank_enabled:
             original_citations = [format_citation(r) for r in original_results]

        yield ("rag_result", {
            "citations": citations,
            "original_citations": original_citations
        })

        # Generate Answer
        step_start_time = time.time()
        yield ("agent_thought", {
            "step": "response",
            "content": "正在基于检索结果生成回答..."
        })

        context_parts = []
        for i, r in enumerate(final_results, 1):
            context_parts.append(f"[{i}] 来源: {r['metadata'].get('file_name', '未知')}\n{r['content']}")
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
        for r in all_results: 
            img_path = r["metadata"].get("image_path")
            if img_path:
                 full_image_path = os.path.join(settings.IMAGE_DIR, img_path)
                 if os.path.exists(full_image_path):
                     try:
                         with open(full_image_path, "rb") as img_file:
                             encoded_string = base64.b64encode(img_file.read()).decode('utf-8')
                             ext = os.path.splitext(img_path)[1].lower().replace('.', '')
                             if not ext: ext = 'jpeg'
                             if ext == 'jpg': ext = 'jpeg'
                             retrieved_images.append(f"data:image/{ext};base64,{encoded_string}")
                     except: pass

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


    async def chat_stream(
        self,
        messages: List[Message],
        kb_ids: List[str],
        use_rewrite: bool = False,
        mode: str = "chat",
        top_k: int = 3,
        score_threshold: float = 0.3,
        model_id: str = None,
        rerank_enabled: bool = False,
        rerank_score_threshold: float = 0.0,
        rerank_model_id: str = None,
    ) -> AsyncGenerator[Tuple[str, Dict[str, Any]], None]:
        """
        流式对话
        """
        import sys
        print(f"DEBUG_CHAT: Start chat_stream. Messages count: {len(messages)}, KBs: {kb_ids}, Mode: {mode}, TopK: {top_k}", flush=True)
        sys.stdout.flush()

        if mode == "agent":
            async for item in self._chat_stream_agent(
                messages=messages,
                kb_ids=kb_ids,
                top_k=top_k,
                score_threshold=score_threshold,
                model_id=model_id,
                rerank_enabled=rerank_enabled,
                rerank_score_threshold=rerank_score_threshold,
                rerank_model_id=rerank_model_id
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
                rerank_model_id=rerank_model_id
            ):
                yield item
