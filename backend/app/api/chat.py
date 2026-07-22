"""
对话 API 路由
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlmodel import Session
from typing import AsyncGenerator
import json

from app.core.database import get_session
from app.models import KnowledgeBase
from app.schemas import (
    RewriteRequest,
    RewriteResponse,
    ChatRequest,
    ApiResponse,
)
from app.services.chat import ChatService
from app.services.llm import LLMService

router = APIRouter(tags=["Chat"])


@router.post("/chat/rewrite", response_model=ApiResponse[RewriteResponse])
async def rewrite_query(data: RewriteRequest):
    """问题改写"""
    try:
        llm_service = LLMService()
        rewritten = await llm_service.rewrite_query(data.query)
        return ApiResponse(data=RewriteResponse(rewritten_query=rewritten))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": 50001, "message": f"问题改写失败: {str(e)}"},
        )


@router.post("/chat/completions")
async def chat_completions(
    data: ChatRequest,
    session: Session = Depends(get_session),
):
    """对话接口 (SSE 流式响应)"""
    # 验证知识库存在
    for kb_id in data.kb_ids:
        kb = session.get(KnowledgeBase, kb_id)
        if not kb:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": 40401, "message": f"知识库不存在: {kb_id}"},
            )

    async def generate_sse() -> AsyncGenerator[str, None]:
        """生成 SSE 事件流"""
        chat_service = ChatService()

        try:
            async for event_type, event_data in chat_service.chat_stream(
                messages=data.messages,
                kb_ids=data.kb_ids,
                use_rewrite=data.use_rewrite,
                mode=data.mode,
                top_k=data.top_k,
                score_threshold=data.score_threshold,
                model_id=data.model_id,
                rerank_enabled=data.rerank_enabled,
                rerank_score_threshold=data.rerank_score_threshold,
                rerank_model_id=data.rerank_model_id,
            ):
                # 格式化 SSE 事件
                yield f"event: {event_type}\n"
                yield f"data: {json.dumps(event_data, ensure_ascii=False)}\n\n"

        except Exception as e:
            # 发送错误事件
            error_data = {"error": str(e)}
            yield f"event: error\n"
            yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        generate_sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
