"""
文件管理 API 路由
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status, BackgroundTasks
from sqlmodel import Session, select, func
from typing import List, Dict
import os
import aiofiles
from datetime import datetime

from app.core.database import get_session
from app.core.config import get_settings
from app.models import KnowledgeBase, FileDocument, FileStatus, generate_uuid, DocumentChunk
from app.schemas import FileUploadResponse, FileStatusResponse, ApiResponse
from app.tasks.parse_tasks import process_file_parsing

router = APIRouter(tags=["File"])
settings = get_settings()


def get_file_extension(filename: str) -> str:
    """获取文件扩展名"""
    return os.path.splitext(filename)[1].lower()


@router.post(
    "/knowledge-bases/{kb_id}/upload",
    response_model=ApiResponse[FileUploadResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_file(
    kb_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    """上传文件"""
    # 检查知识库是否存在
    kb = session.get(KnowledgeBase, kb_id)
    if not kb:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 40401, "message": "知识库不存在"},
        )

    # 检查文件格式
    ext = get_file_extension(file.filename)
    if ext not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": 40001, "message": f"不支持的文件格式: {ext}"},
        )

    # 读取文件内容检查大小
    content = await file.read()
    if len(content) > settings.MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": 40002, "message": "文件过大，最大支持 100MB"},
        )

    # 生成文件 ID 和存储路径
    file_id = generate_uuid()
    kb_upload_dir = os.path.join(settings.UPLOAD_DIR, kb_id)
    os.makedirs(kb_upload_dir, exist_ok=True)

    safe_filename = f"{file_id}_{file.filename}"
    file_path = os.path.join(kb_upload_dir, safe_filename)

    # 保存文件
    async with aiofiles.open(file_path, "wb") as f:
        await f.write(content)

    # 创建文件记录
    file_doc = FileDocument(
        id=file_id,
        kb_id=kb_id,
        name=file.filename,
        local_path=file_path,
        size=len(content),
        status=FileStatus.PENDING,
    )
    session.add(file_doc)
    session.commit()
    session.refresh(file_doc)

    # 不再自动触发后台解析任务，等待用户手动触发
    # background_tasks.add_task(process_file_parsing, file_id)

    return ApiResponse(
        data=FileUploadResponse(file_id=file_id, status=file_doc.status.value)
    )


@router.get(
    "/knowledge-bases/{kb_id}/files",
    response_model=ApiResponse[List[FileStatusResponse]],
)
async def list_files(
    kb_id: str,
    session: Session = Depends(get_session),
):
    """获取文件列表及状态"""
    # 检查知识库是否存在
    kb = session.get(KnowledgeBase, kb_id)
    if not kb:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 40401, "message": "知识库不存在"},
        )

    statement = select(FileDocument).where(
        FileDocument.kb_id == kb_id
    ).order_by(FileDocument.created_at.desc())

    files = session.exec(statement).all()

    # 批量查询 chunk 数量
    chunk_counts_map = {}
    if files:
        file_ids = [f.id for f in files]
        chunk_counts = session.exec(
            select(DocumentChunk.file_id, func.count(DocumentChunk.id))
            .where(DocumentChunk.file_id.in_(file_ids))
            .group_by(DocumentChunk.file_id)
        ).all()
        chunk_counts_map = {file_id: count for file_id, count in chunk_counts}

    file_list = [
        FileStatusResponse(
            id=f.id,
            name=f.name,
            size=f.size,
            status=f.status.value,
            progress=f.progress,
            error_message=f.error_message,
            chunk_count=chunk_counts_map.get(f.id, 0),
            created_at=f.created_at,
        )
        for f in files
    ]
    
    return ApiResponse(data=file_list)


@router.get("/files/{file_id}", response_model=ApiResponse[FileStatusResponse])
async def get_file_detail(
    file_id: str,
    session: Session = Depends(get_session),
):
    """获取文件详情"""
    file_doc = session.get(FileDocument, file_id)
    if not file_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 40401, "message": "文件不存在"},
        )
        
    # 查询 chunk 数量
    chunk_count = session.exec(
        select(func.count(DocumentChunk.id)).where(DocumentChunk.file_id == file_id)
    ).one()

    return ApiResponse(
        data=FileStatusResponse(
            id=file_doc.id,
            name=file_doc.name,
            size=file_doc.size,
            status=file_doc.status.value,
            progress=file_doc.progress,
            error_message=file_doc.error_message,
            chunk_count=chunk_count,
            created_at=file_doc.created_at,
        )
    )


@router.post("/files/{file_id}/parse", response_model=ApiResponse)
async def parse_file_manually(
    file_id: str,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    # 接收可选的 config 参数
    config: dict = None 
):
    """手动触发文件解析"""
    file_doc = session.get(FileDocument, file_id)
    if not file_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 40401, "message": "文件不存在"},
        )

    # 提取 config 中的参数
    chunk_mode = config.get("chunk_mode", "auto") if config else "auto"
    chunk_size = config.get("chunk_size", 500) if config else 500
    chunk_overlap = config.get("chunk_overlap", 50) if config else 50
    auto_vectorize = config.get("auto_vectorize", False) if config else False
    
    # 触发后台任务，传递参数
    background_tasks.add_task(
        process_file_parsing, 
        file_id, 
        chunk_mode=chunk_mode, 
        chunk_size=chunk_size, 
        chunk_overlap=chunk_overlap,
        auto_vectorize=auto_vectorize
    )
    
    return ApiResponse(message="已触发解析任务")


@router.post("/files/{file_id}/chunks/submit", response_model=ApiResponse)
async def submit_file_chunks(
    file_id: str,
    data: dict, # { "chunks": [...] }
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    """提交确认的分块结果，触发入库"""
    file_doc = session.get(FileDocument, file_id)
    if not file_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 40401, "message": "文件不存在"},
        )
        
    chunks = data.get("chunks", [])
    if not chunks:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": 40003, "message": "分块数据为空"},
        )

    # 触发后台任务：process_submitted_chunks
    from app.tasks.parse_tasks import process_submitted_chunks
    background_tasks.add_task(process_submitted_chunks, file_id, chunks)

    return ApiResponse(message="已提交分块，开始后台入库")


@router.post("/files/{file_id}/vectorize", response_model=ApiResponse)
async def vectorize_file_manually(
    file_id: str,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    """手动触发向量化入库 (用于正常模式确认)"""
    file_doc = session.get(FileDocument, file_id)
    if not file_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 40401, "message": "文件不存在"},
        )
        
    # 获取当前的 chunks
    from app.models import DocumentChunk
    chunks = session.exec(select(DocumentChunk).where(DocumentChunk.file_id == file_id)).all()
    
    if not chunks:
         raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": 40004, "message": "文件尚未解析，无法入库"},
        )
        
    # 构造 chunks 数据格式
    chunks_data = [
        {
            "content": c.content,
            "page_number": c.page_number,
            "content_type": c.content_type.value,
            "image_path": c.image_path,
            "vlm_description": c.vlm_description if hasattr(c, "vlm_description") else None,
        }
        for c in chunks
    ]

    # 触发后台任务：process_submitted_chunks (复用提交逻辑)
    from app.tasks.parse_tasks import process_submitted_chunks
    background_tasks.add_task(process_submitted_chunks, file_id, chunks_data)

    return ApiResponse(message="已确认，开始后台入库")


@router.get("/files/{file_id}/chunks", response_model=ApiResponse)
async def get_file_chunks(
    file_id: str,
    session: Session = Depends(get_session),
):
    """获取文件的 Chunks"""
    from app.models import DocumentChunk
    
    file_doc = session.get(FileDocument, file_id)
    if not file_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 40401, "message": "文件不存在"},
        )
        
    chunks = session.exec(
        select(DocumentChunk)
        .where(DocumentChunk.file_id == file_id)
        .order_by(DocumentChunk.original_index)
    ).all()
    
    return ApiResponse(data=chunks)


@router.put("/chunks/{chunk_id}", response_model=ApiResponse)
async def update_chunk(
    chunk_id: str,
    data: dict, # { "content": "..." }
    session: Session = Depends(get_session),
):
    """更新 Chunk 内容"""
    from app.models import DocumentChunk
    
    chunk = session.get(DocumentChunk, chunk_id)
    if not chunk:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 40401, "message": "Chunk 不存在"},
        )
        
    if "content" in data:
        chunk.content = data["content"]
        
    session.add(chunk)
    session.commit()
    session.refresh(chunk)
    
    return ApiResponse(data=chunk)


@router.delete("/files/{file_id}", response_model=ApiResponse)
async def delete_file(
    file_id: str,
    session: Session = Depends(get_session),
):
    """删除文件"""
    file_doc = session.get(FileDocument, file_id)
    if not file_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 40401, "message": "文件不存在"},
        )

    # 删除物理文件
    if os.path.exists(file_doc.local_path):
        os.remove(file_doc.local_path)

    # 从向量库删除相关数据
    try:
        from app.services.vector_store import VectorStoreService
        vector_service = VectorStoreService()
        vector_service.delete_by_file_id(file_doc.kb_id, file_id)
    except Exception:
        pass

    # 删除数据库记录
    # 级联删除 chunks
    from app.models import DocumentChunk
    chunks = session.exec(select(DocumentChunk).where(DocumentChunk.file_id == file_id)).all()
    for chunk in chunks:
        session.delete(chunk)
        
    session.delete(file_doc)
    session.commit()
    
    return ApiResponse(message="删除成功")
