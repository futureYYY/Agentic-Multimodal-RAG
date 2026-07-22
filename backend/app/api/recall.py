"""
召回测试 API 路由

支持三种检索模式：
- vector: 仅向量检索
- fulltext: 仅 BM25 检索
- hybrid: 混合检索（默认）
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlmodel import Session, select, delete, func
from typing import List, Optional

from app.core.database import get_session
from app.models import KnowledgeBase, CustomModel, RetrievalLog
from app.schemas import (
    RecallRequest, RecallResult, ApiResponse, RecallTestResponse,
    RetrievalLogResponse, RetrievalLogListResponse
)
from app.services.retrieval import get_retrieval_service
from app.services.rerank import RerankService, batch_rerank, expand_context
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
    """
    执行召回测试

    支持三种检索模式：
    - vector: 仅向量检索
    - fulltext: 仅 BM25 检索
    - hybrid: 混合检索（默认）

    流程：
    1. 粗召回（向量 / BM25 / 混合）
    2. 可选：Rerank 精排
    3. 可选：上下文扩展
    """
    start_time = time.time()

    # 检查知识库是否存在
    kb = session.get(KnowledgeBase, kb_id)
    if not kb:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 40401, "message": "知识库不存在"},
        )

    try:
        # 1. 使用统一检索服务执行检索
        retrieval_service = get_retrieval_service()
        results, metrics = await retrieval_service.search(
            session=session,
            query=data.query,
            kb_ids=[kb_id],
            search_mode=data.search_mode,
            top_k=data.top_k,
            score_threshold=data.score_threshold,
            vector_weight=data.vector_weight,
            bm25_weight=data.bm25_weight,
            use_cache=True,
            log_retrieval=True,
        )

        print(f"📊 [Recall] 检索完成: mode={data.search_mode}, "
              f"vector={metrics.get('vector_count', 0)}, bm25={metrics.get('bm25_count', 0)}, "
              f"merged={metrics.get('merged_count', 0)}, latency={metrics.get('total_latency_ms', 0):.1f}ms")

        # 2. 执行重排序 (Rerank)
        if data.rerank_enabled and results:
            try:
                rerank_start = time.time()
                rerank_service = RerankService(session)

                rerank_model = None
                if data.rerank_model_id:
                    rerank_model = session.get(CustomModel, data.rerank_model_id)

                # 使用分批 Rerank
                results = batch_rerank(
                    query=data.query,
                    candidates=results,
                    rerank_service=rerank_service,
                    model=rerank_model,
                    top_k=data.rerank_top_k,
                    score_threshold=data.rerank_score_threshold
                )

                rerank_latency = (time.time() - rerank_start) * 1000
                print(f"📊 [Recall] Rerank 完成: count={len(results)}, latency={rerank_latency:.1f}ms")

            except Exception as e:
                print(f"⚠️ [Recall] Rerank 执行失败: {e}")
                # 出错时保留原结果

        # 3. 上下文扩展
        if data.context_window > 0 and results:
            try:
                results = expand_context(
                    top_chunks=results,
                    session=session,
                    window_size=data.context_window
                )
                print(f"📊 [Recall] Context Expanded: window_size={data.context_window}")
            except Exception as e:
                print(f"⚠️ [Recall] Context Expansion Failed: {e}")

        # 4. 格式化返回结果
        result_list = []
        for r in results:
            # 处理图片 URL
            image_path = r.get("image_path")
            image_url = None

            if image_path:
                image_url = f"/static/images/{image_path}"
            else:
                # 尝试从 content 中解析 [图片: path]
                content = r.get("content", "")
                match = re.search(r"\[图片:\s*(.+?)\]", content)
                if match:
                    path_in_content = match.group(1).strip()
                    if not path_in_content.startswith("/static/images/"):
                        image_url = f"/static/images/{path_in_content}"
                    else:
                        image_url = path_in_content

            # 位置信息
            location = ""
            page_number = r.get("page_number")
            if page_number:
                location = f"第 {page_number} 页"

            result_list.append(RecallResult(
                chunkId=r.get("id"),
                score=r.get("score", 0),
                rerank_score=r.get("rerank_score"),
                vector_score=r.get("vector_score"),
                bm25_score=r.get("bm25_score"),
                content=r.get("content", ""),
                fileName=r.get("file_name", "未知"),
                kbName=r.get("kb_name", kb.name),
                location=location,
                imageUrl=image_url,
                heading_text=r.get("heading_text"),
                heading_level=r.get("heading_level"),
            ))

        end_time = time.time()
        query_time = (end_time - start_time) * 1000  # 毫秒

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


@router.post(
    "/recall/multi",
    response_model=ApiResponse[RecallTestResponse],
)
async def recall_test_multi(
    data: RecallRequest,
    kb_ids: List[str],
    session: Session = Depends(get_session),
):
    """
    多知识库联合召回测试
    """
    start_time = time.time()

    # 验证知识库存在
    for kb_id in kb_ids:
        kb = session.get(KnowledgeBase, kb_id)
        if not kb:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": 40401, "message": f"知识库 {kb_id} 不存在"},
            )

    try:
        # 使用统一检索服务
        retrieval_service = get_retrieval_service()
        results, metrics = await retrieval_service.search(
            session=session,
            query=data.query,
            kb_ids=kb_ids,
            search_mode=data.search_mode,
            top_k=data.top_k,
            score_threshold=data.score_threshold,
            vector_weight=data.vector_weight,
            bm25_weight=data.bm25_weight,
        )

        # Rerank
        if data.rerank_enabled and results:
            rerank_service = RerankService(session)
            rerank_model = None
            if data.rerank_model_id:
                rerank_model = session.get(CustomModel, data.rerank_model_id)

            results = batch_rerank(
                query=data.query,
                candidates=results,
                rerank_service=rerank_service,
                model=rerank_model,
                top_k=data.rerank_top_k,
                score_threshold=data.rerank_score_threshold
            )

        # 3. 上下文扩展
        if data.context_window > 0 and results:
            try:
                results = expand_context(
                    top_chunks=results,
                    session=session,
                    window_size=data.context_window
                )
            except Exception as e:
                print(f"⚠️ [Recall] Context Expansion Failed: {e}")

        # 格式化结果
        result_list = []
        for r in results:
            image_url = None
            if r.get("image_path"):
                image_url = f"/static/images/{r['image_path']}"

            location = ""
            if r.get("page_number"):
                location = f"第 {r['page_number']} 页"

            result_list.append(RecallResult(
                chunkId=r.get("id"),
                score=r.get("score", 0),
                rerank_score=r.get("rerank_score"),
                vector_score=r.get("vector_score"),
                bm25_score=r.get("bm25_score"),
                content=r.get("content", ""),
                fileName=r.get("file_name", "未知"),
                kbName=r.get("kb_name", ""),
                location=location,
                imageUrl=image_url,
                heading_text=r.get("heading_text"),
                heading_level=r.get("heading_level"),
            ))

        query_time = (time.time() - start_time) * 1000

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


# ==================== 检索历史记录 API ====================

@router.get(
    "/retrieval-logs",
    response_model=ApiResponse[RetrievalLogListResponse],
)
async def get_retrieval_logs(
    kb_id: Optional[str] = Query(None, description="知识库 ID（可选，不传则返回所有）"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    session: Session = Depends(get_session),
):
    """
    获取检索历史记录列表
    """
    try:
        # 构建查询
        query = select(RetrievalLog)
        count_query = select(func.count(RetrievalLog.id))

        if kb_id:
            # 支持模糊匹配（因为 kb_id 可能是逗号分隔的多个 ID）
            query = query.where(RetrievalLog.kb_id.contains(kb_id))
            count_query = count_query.where(RetrievalLog.kb_id.contains(kb_id))

        # 获取总数
        total = session.exec(count_query).one()

        # 分页查询，按创建时间倒序
        query = query.order_by(RetrievalLog.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)
        logs = session.exec(query).all()

        # 转换为响应格式
        log_responses = [
            RetrievalLogResponse.model_validate(log)
            for log in logs
        ]

        return ApiResponse(data=RetrievalLogListResponse(
            logs=log_responses,
            total=total,
            page=page,
            page_size=page_size
        ))

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": 50002, "message": f"获取历史记录失败: {str(e)}"},
        )


@router.get(
    "/retrieval-logs/{log_id}",
    response_model=ApiResponse[RetrievalLogResponse],
)
async def get_retrieval_log_detail(
    log_id: str,
    session: Session = Depends(get_session),
):
    """
    获取单条检索历史记录详情
    """
    log = session.get(RetrievalLog, log_id)
    if not log:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 40402, "message": "历史记录不存在"},
        )

    return ApiResponse(data=RetrievalLogResponse.model_validate(log))


@router.delete(
    "/retrieval-logs/{log_id}",
    response_model=ApiResponse,
)
async def delete_retrieval_log(
    log_id: str,
    session: Session = Depends(get_session),
):
    """
    删除单条检索历史记录
    """
    log = session.get(RetrievalLog, log_id)
    if not log:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 40402, "message": "历史记录不存在"},
        )

    session.delete(log)
    session.commit()

    return ApiResponse(message="删除成功")


@router.delete(
    "/retrieval-logs",
    response_model=ApiResponse,
)
async def clear_retrieval_logs(
    kb_id: Optional[str] = Query(None, description="知识库 ID（可选，不传则清空所有）"),
    session: Session = Depends(get_session),
):
    """
    清空检索历史记录
    """
    try:
        if kb_id:
            # 删除指定知识库的记录
            statement = delete(RetrievalLog).where(RetrievalLog.kb_id.contains(kb_id))
        else:
            # 删除所有记录
            statement = delete(RetrievalLog)

        session.exec(statement)
        session.commit()

        return ApiResponse(message="清空成功")

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": 50003, "message": f"清空历史记录失败: {str(e)}"},
        )
