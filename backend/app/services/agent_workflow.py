"""
基于 LangGraph 的 Agent 工作流
"""

from typing import TypedDict, List, Annotated, Literal
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langgraph.graph import StateGraph, END
from app.services.llm import LLMService
from app.services.embedding import EmbeddingService
from app.services.vector_store import VectorStoreService
from app.models import KnowledgeBase, FileDocument, DocumentChunk
from sqlmodel import Session, select
from app.core.database import engine
import json

# 定义状态
class AgentState(TypedDict):
    messages: List[BaseMessage]
    kb_ids: List[str]
    intent: str  # 'chat', 'qa'
    rewritten_query: str
    rag_context: str
    citations: List[dict]
    agent_steps: List[dict]  # 用于记录思考过程返回给前端
    top_k: int
    score_threshold: float
    final_system_prompt: str # 新增字段

class AgentWorkflow:
    def __init__(self):
        self.llm_service = LLMService()
        self.embedding_service = EmbeddingService()
        self.vector_service = VectorStoreService()
        
        # 构建图
        workflow = StateGraph(AgentState)
        
        # 添加节点
        workflow.add_node("intent_analysis", self.analyze_intent)
        workflow.add_node("rewrite_query", self.rewrite_query)
        workflow.add_node("retrieve", self.retrieve)
        workflow.add_node("generate_answer", self.generate_answer)
        workflow.add_node("generate_chat", self.generate_chat)
        
        # 设置入口
        workflow.set_entry_point("intent_analysis")
        
        # 添加条件边
        workflow.add_conditional_edges(
            "intent_analysis",
            self.route_based_on_intent,
            {
                "chat": "generate_chat",
                "qa": "rewrite_query"
            }
        )
        
        workflow.add_edge("rewrite_query", "retrieve")
        workflow.add_edge("retrieve", "generate_answer")
        workflow.add_edge("generate_answer", END)
        workflow.add_edge("generate_chat", END)
        
        self.app = workflow.compile()

    async def analyze_intent(self, state: AgentState):
        """意图分析节点"""
        last_message = state["messages"][-1].content
        
        # 简单规则判断
        if len(last_message.strip()) < 4 and last_message.strip() in ["你好", "在吗", "hello", "hi"]:
            intent = "chat"
            thought = "检测到闲聊意图，跳过检索..."
        else:
            # 可以扩展为使用 LLM 判断
            intent = "qa"
            thought = "检测到查询意图，准备检索知识库..."
            
        # 记录思考步骤
        new_steps = state.get("agent_steps", []) + [{
            "step": "decision",
            "content": thought
        }]
        
        return {"intent": intent, "agent_steps": new_steps}

    def route_based_on_intent(self, state: AgentState):
        """根据意图路由"""
        return state["intent"]

    async def rewrite_query(self, state: AgentState):
        """查询改写节点"""
        query = state["messages"][-1].content
        
        # 调用 LLM 进行改写
        try:
            rewritten = await self.llm_service.rewrite_query(query)
        except:
            rewritten = query
            
        new_steps = state.get("agent_steps", []) + [{
            "step": "thinking",
            "content": f"将问题 \"{query}\" 改写为: \"{rewritten}\""
        }]
        
        return {"rewritten_query": rewritten, "agent_steps": new_steps}

    async def retrieve(self, state: AgentState):
        """检索节点"""
        query = state["rewritten_query"]
        kb_ids = state["kb_ids"]
        top_k = state.get("top_k", 3)
        score_threshold = state.get("score_threshold", 0.3)
        
        new_steps = state.get("agent_steps", []) + [{
            "step": "action",
            "content": "正在检索知识库..."
        }]
        
        # 如果没有选知识库，去查所有? 或者直接返回空
        # Agent 模式下，如果前端没传 kb_ids，默认检索所有知识库
        target_kb_ids = kb_ids
        target_kb_names = []
        
        with Session(engine) as session:
            if not target_kb_ids:
                # 获取所有知识库 ID
                all_kbs = session.exec(select(KnowledgeBase)).all()
                target_kb_ids = [kb.id for kb in all_kbs]
                target_kb_names = [kb.name for kb in all_kbs]
            else:
                for kid in target_kb_ids:
                    kb = session.get(KnowledgeBase, kid)
                    if kb:
                        target_kb_names.append(kb.name)
        
        if not target_kb_ids:
             return {
                "rag_context": "系统暂无可用知识库", 
                "citations": [],
                "agent_steps": new_steps + [{
                    "step": "action",
                    "content": "未找到可用知识库"
                }]
            }

        # 更新思考步骤，显示检索的知识库
        kb_names_str = "、".join(target_kb_names[:3])
        if len(target_kb_names) > 3:
            kb_names_str += f" 等{len(target_kb_names)}个知识库"
            
        new_steps = new_steps + [{
            "step": "action",
            "content": f"正在检索知识库：{kb_names_str}"
        }]

        try:
            query_vector = await self.embedding_service.embed_query(query)
        except Exception as e:
            return {
                "rag_context": f"检索失败: {str(e)}", 
                "citations": [],
                "agent_steps": new_steps
            }

        all_results = []
        kb_names_map = {} # cache

        with Session(engine) as session:
            for kb_id in target_kb_ids:
                # ... (后续检索逻辑保持不变，只需把 kb_ids 换成 target_kb_ids)
                kb = session.get(KnowledgeBase, kb_id)
                if kb:
                    kb_names_map[kb_id] = kb.name

                print(f"DEBUG: 正在检索知识库 {kb_id} ({kb.name if kb else '未知'})...")
                results = self.vector_service.query(
                    kb_id=kb_id,
                    query_vector=query_vector,
                    top_k=top_k,
                    score_threshold=score_threshold,
                )
                print(f"DEBUG: 知识库 {kb_id} 返回 {len(results)} 条结果")
                for r in results:
                    r["kb_name"] = kb_names_map.get(kb_id, "未知")
                    all_results.append(r)
        
        if not all_results:
             new_steps.append({
                "step": "action",
                "content": "未找到相关知识库信息"
             })

        # 排序取 Top K
        all_results.sort(key=lambda x: x["score"], reverse=True)
        all_results = all_results[:top_k]

        # 补全 Metadata (如果缺失)
        with Session(engine) as session:
            for i, r in enumerate(all_results):
                metadata = r.get("metadata", {})
                print(f"DEBUG: [Result #{i}] 原始 Metadata: {metadata}")
                
                # 补全文件名
                if "file_name" not in metadata or not metadata["file_name"]:
                    print(f"DEBUG: [Result #{i}] 缺失 file_name，尝试补全...")
                    # 尝试从 file_id 查
                    file_id = metadata.get("file_id")
                    if file_id:
                        file_doc = session.get(FileDocument, file_id)
                        if file_doc:
                            metadata["file_name"] = file_doc.name
                            r["metadata"]["file_name"] = file_doc.name
                            print(f"DEBUG: [Result #{i}] file_name 补全成功: {file_doc.name}")
                        else:
                            print(f"DEBUG: [Result #{i}] 未找到 FileDocument (ID: {file_id})")
                    else:
                        print(f"DEBUG: [Result #{i}] Metadata 中无 file_id，无法补全文件名")
                
                # 补全 chunk_index 和 location
                if "chunk_index" not in metadata:
                     print(f"DEBUG: [Result #{i}] 缺失 chunk_index，尝试补全...")
                     # 如果连 chunk_index 都没有，尝试通过 chunk_id (uuid) 查 DocumentChunk 表
                     # 注意：这需要 ChromaDB 的 ID 和 SQL 的 ID 一致
                     chunk_doc = session.get(DocumentChunk, r["id"])
                     if chunk_doc:
                         metadata["chunk_index"] = chunk_doc.original_index
                         metadata["page_number"] = chunk_doc.page_number
                         metadata["location_info"] = f"Page {chunk_doc.page_number} | Chunk #{chunk_doc.original_index + 1}"
                         r["metadata"] = metadata # 更新回去
                         print(f"DEBUG: [Result #{i}] chunk_index 补全成功: {chunk_doc.original_index}")
                     else:
                         print(f"DEBUG: [Result #{i}] 未找到 DocumentChunk (ID: {r['id']})")

        # 构建上下文
        context_parts = []
        for i, r in enumerate(all_results, 1):
            context_parts.append(f"[{i}] 来源: {r['metadata'].get('file_name', '未知')}\n{r['content']}")
        
        context = "\n\n".join(context_parts) if context_parts else "未找到相关信息"
        print(f"DEBUG: Retrieve 节点生成 Context 长度: {len(context)}")
        
        citations = [
            {
                "score": r["score"],
                # 这里不再截断，确保兜底逻辑能获取完整内容。前端展示如果嫌长可以使用 CSS 控制
                "content": r["content"], 
                "fileName": r["metadata"].get("file_name", "未知"), # 修改为 fileName
                "kb_name": r["kb_name"],
                # 优先使用 chunk_index (如 #1)，如果不存在则使用 UUID
                "chunk_id": f"#{r['metadata'].get('chunk_index', '?')}" if 'chunk_index' in r['metadata'] else r["id"],
                "location": r["metadata"].get("location_info", ""), # 修改为 location
            }
            for r in all_results
        ]
        
        return {
            "rag_context": context, 
            "citations": citations,
            "agent_steps": new_steps
        }

    async def generate_answer(self, state: AgentState):
        """RAG 回答生成节点"""
        context = state.get("rag_context", "")
        citations = state.get("citations", [])
        
        print(f"DEBUG: Generate 节点接收 Context 长度: {len(context)}")
        
        # 兜底逻辑：如果 Context 丢失但有引用，重新构建 Context
        if not context and citations:
            print("WARNING: Context 丢失，正在从 Citations 重建...")
            context_parts = []
            for i, c in enumerate(citations, 1):
                # 尝试从 citation 恢复 content (citation 里存了 content 摘要，虽然可能被截断，但总比没有好)
                # 其实更好的办法是 retrieve 节点返回时保证 state update 成功
                # 但这里作为最后防线
                content = c.get("content", "")
                file_name = c.get("fileName", "未知") # 修改为 fileName
                context_parts.append(f"[{i}] 来源: {file_name}\n{content}")
            context = "\n\n".join(context_parts)
            print(f"DEBUG: 重建后 Context 长度: {len(context)}")

        if len(context) < 10:
             print(f"DEBUG: Context 内容警告: {context}")
        
        system_prompt = f"""你是一个智能问答助手，请根据以下参考资料回答用户的问题。

参考资料：
{context}

要求：
1. 基于参考资料回答，如果资料中没有相关信息，请如实说明
2. 回答要准确、完整、有条理
3. 适当引用来源（如"根据文档..."）
4. 使用中文回答"""

        print(f"DEBUG: Generate 节点生成的 System Prompt:\n{system_prompt[:500]}...") # 打印前500字符
        
        new_steps = state.get("agent_steps", []) + [{
            "step": "response",
            "content": "正在生成回答..."
        }]
        
        # 注意：实际生成是在 chat_stream 中流式进行的，这里主要为了状态流转
        # 我们可以在这里不真正调用 generate，而是把 prompt 准备好
        # 或者在这里调用非流式，但为了保持 chat_stream 的流式特性，
        # 我们让 workflow 返回 prompt 信息，由外部流式调用
        
        return {"agent_steps": new_steps, "final_system_prompt": system_prompt}

    async def generate_chat(self, state: AgentState):
        """闲聊生成节点"""
        system_prompt = """你是一个智能问答助手。请根据你的训练知识，准确、完整、有条理地回答用户的问题。使用中文回答。"""
        
        new_steps = state.get("agent_steps", []) + [{
            "step": "response",
            "content": "正在生成回答..."
        }]
        
        return {"agent_steps": new_steps, "final_system_prompt": system_prompt}
