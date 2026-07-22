
from langchain_core.tools import tool
from typing import List, Optional, Literal, Dict, Any
from sqlmodel import Session, select
from app.core.database import engine
from app.models import KnowledgeBase, CustomModel
from app.services.retrieval import get_retrieval_service
from app.services.rerank import RerankService, expand_context

import json

@tool
async def search_knowledge_base(
    query: str,
    kb_ids: Optional[List[str]] = None,
    search_mode: Literal["vector", "fulltext", "hybrid"] = "hybrid",
    top_k: int = 5,
    score_threshold: float = 0.1,
    use_rerank: bool = False,
    rerank_top_k: int = 3,
    rerank_score_threshold: float = 0.0,
    rerank_model_id: Optional[str] = None,
    vector_weight: float = 0.7,
    bm25_weight: float = 0.3,
    context_window: int = 0
) -> str:
    """
    Search for information in the knowledge base.
    
    Args:
        query: The search query string.
        kb_ids: List of knowledge base IDs to search in. If None or empty, searches all available knowledge bases.
        search_mode: Search mode. Options: "vector" (semantic), "fulltext" (keyword), "hybrid" (both). Default is "hybrid".
        top_k: Number of initial results to retrieve. Default is 5.
        score_threshold: Minimum similarity score threshold for retrieval. Default is 0.4.
        use_rerank: Whether to use a reranker model to re-order results. Default is False.
        rerank_top_k: Number of results to keep after reranking. Default is 3.
        rerank_score_threshold: Minimum score threshold for reranking. Default is 0.0.
        rerank_model_id: Optional ID of the rerank model to use.
        vector_weight: Weight for vector search score in hybrid mode. Default 0.7.
        bm25_weight: Weight for BM25 search score in hybrid mode. Default 0.3.
        context_window: Number of adjacent chunks to include for context expansion. Default 0.
        
    Returns:
        A JSON string containing:
        - "context": A formatted string of retrieved documents for reading.
        - "citations": A list of structured citation objects.
    """
    
    retrieval_service = get_retrieval_service()
    
    with Session(engine) as session:
        # 1. Resolve Knowledge Bases
        target_kb_ids = kb_ids
        if not target_kb_ids:
            all_kbs = session.exec(select(KnowledgeBase).where(KnowledgeBase.is_deleted == False)).all()
            target_kb_ids = [kb.id for kb in all_kbs]
            
        if not target_kb_ids:
            return json.dumps({
                "context": "No knowledge bases found in the system.",
                "citations": []
            }, ensure_ascii=False)
            
        # 2. Execute Retrieval
        try:
            results, _ = await retrieval_service.search(
                session=session,
                query=query,
                kb_ids=target_kb_ids,
                search_mode=search_mode,
                top_k=top_k if not use_rerank else top_k * 2, # Fetch more if reranking
                score_threshold=score_threshold,
                vector_weight=vector_weight,
                bm25_weight=bm25_weight
            )
        except Exception as e:
            return json.dumps({
                "context": f"Error during retrieval: {str(e)}",
                "citations": []
            }, ensure_ascii=False)
            
        if not results:
            return json.dumps({
                "context": "No relevant documents found.",
                "citations": []
            }, ensure_ascii=False)
            
        # 3. Execute Reranking (Optional)
        if use_rerank and results:
            try:
                rerank_service = RerankService(session)
                # Resolve Rerank Model
                rerank_model = None
                if rerank_model_id:
                    rerank_model = session.get(CustomModel, rerank_model_id)

                # Prepare candidates for rerank
                candidates = [r["content"] for r in results]
                
                # Execute rerank
                reranked = rerank_service.rerank(query, candidates, model=rerank_model)
                
                # Filter and map back to results
                final_results = []
                for item in reranked:
                    idx = item["index"]
                    score = item["score"]
                    if score >= rerank_score_threshold:
                        res = results[idx]
                        res["rerank_score"] = score
                        # Update main score to rerank score for consistency
                        res["score"] = score 
                        final_results.append(res)
                
                # Sort and slice
                final_results.sort(key=lambda x: x["score"], reverse=True)
                results = final_results[:rerank_top_k]
                
            except Exception as e:
                return json.dumps({
                    "context": f"Error during reranking: {str(e)}",
                    "citations": []
                }, ensure_ascii=False)
        else:
            # If not reranking, just ensure we respect top_k
            results = results[:top_k]

        # 4. Context Expansion (Optional)
        if context_window > 0 and results:
            try:
                results = expand_context(
                    top_chunks=results,
                    session=session,
                    window_size=context_window
                )
            except Exception as e:
                # Log error but continue with unexpanded results
                print(f"Context expansion failed: {e}")

        # 5. Format Output
        formatted_results = []
        citations = []
        
        for i, r in enumerate(results, 1):
            source = r.get("file_name", "Unknown File")
            kb_name = r.get("kb_name", "Unknown KB")
            score = r.get("score", 0.0)
            content = r.get("content", "").strip()
            
            formatted_results.append(
                f"Document {i} (Source: {source}, KB: {kb_name}, Score: {score:.4f}):\n{content}\n"
            )
            
            citations.append({
                "fileName": source,
                "kb_name": kb_name,
                "score": score,
                "content": content,
                "chunk_id": r.get("id"),
                "location": r.get("location_info", "")
            })
            
        return json.dumps({
            "context": "\n".join(formatted_results),
            "citations": citations
        }, ensure_ascii=False)
