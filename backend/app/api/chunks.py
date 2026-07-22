"""
Chunk 管理 API 路由
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
from typing import List
from datetime import datetime

from app.core.database import get_session
from app.models import FileDocument, DocumentChunk, FileStatus
from app.schemas import ChunkResponse, ChunkUpdate, VectorizeResponse, ApiResponse

router = APIRouter(tags=["Chunk"])


@router.get("/files/{file_id}/chunks", response_model=ApiResponse[List[ChunkResponse]])
async def get_file_chunks(
    file_id: str,
    session: Session = Depends(get_session),
):
    """获取文件的切分预览数据"""
    file_doc = session.get(FileDocument, file_id)
    if not file_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 40401, "message": "文件不存在"},
        )

    statement = select(DocumentChunk).where(
        DocumentChunk.file_id == file_id
    ).order_by(DocumentChunk.original_index)

    chunks = session.exec(statement).all()

    chunk_list = [
        ChunkResponse(
            id=c.id,
            content=c.content,
            original_file_name=file_doc.name,
            page_number=c.page_number,
            image_url=f"/static/images/{c.image_path}" if c.image_path else None,
            content_type=c.content_type.value,
        )
        for c in chunks
    ]
    
    return ApiResponse(data=chunk_list)


@router.put("/chunks/{chunk_id}", response_model=ApiResponse[ChunkResponse])
async def update_chunk(
    chunk_id: str,
    data: ChunkUpdate,
    session: Session = Depends(get_session),
):
    """更新 Chunk 内容"""
    chunk = session.get(DocumentChunk, chunk_id)
    if not chunk:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 40401, "message": "Chunk 不存在"},
        )

    # 获取关联的文件信息
    file_doc = session.get(FileDocument, chunk.file_id)
    if not file_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 40401, "message": "关联文件不存在"},
        )

    # 只允许在 parsed 状态下编辑
    if file_doc.status != FileStatus.PARSED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": 40003, "message": "只有解析完成的文件才能编辑"},
        )

    # 更新内容
    chunk.content = data.content
    session.add(chunk)
    session.commit()
    session.refresh(chunk)

    return ApiResponse(
        data=ChunkResponse(
            id=chunk.id,
            content=chunk.content,
            original_file_name=file_doc.name,
            page_number=chunk.page_number,
            image_url=f"/static/images/{chunk.image_path}" if chunk.image_path else None,
            content_type=chunk.content_type.value,
        )
    )


@router.post(
    "/files/{file_id}/vectorize",
    response_model=ApiResponse[VectorizeResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def vectorize_file(
    file_id: str,
    session: Session = Depends(get_session),
):
    """确认并入库（触发向量化）"""
    file_doc = session.get(FileDocument, file_id)
    if not file_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 40401, "message": "文件不存在"},
        )

    # 检查状态
    if file_doc.status != FileStatus.PARSED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": 40003, "message": "只有解析完成的文件才能向量化"},
        )

    # 更新状态为向量化中
    file_doc.status = FileStatus.EMBEDDING
    file_doc.progress = 0
    file_doc.updated_at = datetime.utcnow()
    session.add(file_doc)
    session.commit()

    # 触发异步向量化任务
    try:
        from app.tasks.vectorize_tasks import vectorize_file_task
        vectorize_file_task.delay(file_id)
    except Exception as e:
        print(f"Celery task dispatch failed: {e}")

    return ApiResponse(
        data=VectorizeResponse(status="embedding", message="Vectorization started")
    )
