"""
召回测试 API 路由
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session
from typing import List

from app.core.database import get_session
from app.models import KnowledgeBase, CustomModel
from app.schemas import RecallRequest, RecallResult, ApiResponse, RecallTestResponse
from app.services.vector_store import VectorStoreService
from app.services.embedding import EmbeddingService
from app.services.rerank import RerankService
import time
import re

router = APIRouter(tags=["Recall"])


@router.post(
    "/knowledge-bases/{kb_id}/recall",
    response_model=ApiResponse[RecallTestResponse],
)
async def recall_test(
    kb_id: str,
    data: RecallRequest,
    session: Session = Depends(get_session),
):
    """执行召回测试"""
    start_time = time.time()
    
    # 检查知识库是否存在
    kb = session.get(KnowledgeBase, kb_id)
    if not kb:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 40401, "message": "知识库不存在"},
        )

    try:
        # 获取查询向量
        # 检查是否使用了自定义模型
        custom_model = session.get(CustomModel, kb.embedding_model)
        if custom_model:
            embedding_service = EmbeddingService(
                base_url=custom_model.base_url,
                api_key=custom_model.api_key,
                model=custom_model.model_name
            )
            query_vector = await embedding_service.embed_query(data.query)
        else:
            embedding_service = EmbeddingService()
            # 如果不是自定义模型ID，则直接使用 kb.embedding_model 作为模型名称
            query_vector = await embedding_service.embed_query(data.query, model_id=kb.embedding_model)

        # 执行向量检索
        vector_service = VectorStoreService()
        results = vector_service.query(
            kb_id=kb_id,
            query_vector=query_vector,
            top_k=data.top_k,
            score_threshold=data.score_threshold,
        )

        # 执行重排序 (Rerank)
        if data.rerank_enabled and results:
            try:
                rerank_service = RerankService(session)
                # 仅提取文本内容作为候选
                candidates = [r["content"] for r in results]
                
                rerank_model = None
                if data.rerank_model_id:
                    rerank_model = session.get(CustomModel, data.rerank_model_id)
                
                rerank_results = rerank_service.rerank(data.query, candidates, model=rerank_model)
                
                if rerank_results:
                    # 根据 rerank 结果重新构建结果列表
                    new_results = []
                    for rr in rerank_results:
                        idx = rr.get("index")
                        score = rr.get("score", 0.0)
                        
                        # 校验索引有效性
                        if idx is not None and isinstance(idx, int) and 0 <= idx < len(results):
                            # 过滤分数
                            if score >= data.rerank_score_threshold:
                                item = results[idx]
                                item["rerank_score"] = score
                                new_results.append(item)
                    
                    # 确保结果按 rerank_score 排序
                    new_results.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
                    results = new_results
                    
            except Exception as e:
                print(f"Rerank execution failed: {e}")
                # 出错时保留原结果，或者可以选择抛出异常
                # 这里选择保留原结果但打印日志

        # 格式化返回结果
        result_list = []
        for r in results:
            # 处理图片 URL
            image_path = r["metadata"].get("image_path")
            image_url = None
            
            if image_path:
                image_url = f"/static/images/{image_path}"
            else:
                # 尝试从 content 中解析 [图片: path]
                match = re.search(r"\[图片:\s*(.+?)\]", r["content"])
                if match:
                    path_in_content = match.group(1).strip()
                    if not path_in_content.startswith("/static/images/"):
                            image_url = f"/static/images/{path_in_content}"
                    else:
                            image_url = path_in_content

            result_list.append(RecallResult(
                chunkId=r.get("id"),
                score=r["score"],
                rerank_score=r.get("rerank_score"),
                content=r["content"],
                fileName=r["metadata"].get("file_name", "未知"),
                kbName=kb.name,
                location=r["metadata"].get("location_info", ""),
                imageUrl=image_url
            ))
        
        end_time = time.time()
        query_time = (end_time - start_time) * 1000 # 毫秒

        return ApiResponse(data=RecallTestResponse(
            results=result_list,
            query_time=query_time
        ))

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": 50001, "message": f"召回测试失败: {str(e)}"},
        )
