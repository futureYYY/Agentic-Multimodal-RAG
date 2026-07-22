"""
向量化任务
"""

import asyncio
from datetime import datetime
from sqlmodel import Session, select

from app.tasks.celery_app import celery_app
from app.core.database import engine
from app.models import FileDocument, DocumentChunk, KnowledgeBase, FileStatus, CustomModel
from app.services.embedding import EmbeddingService
from app.services.vector_store import VectorStoreService


def run_async(coro):
    """在同步环境中运行异步函数"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(bind=True, max_retries=3)
def vectorize_file_task(self, file_id: str):
    """
    向量化文件任务

    1. 读取 DocumentChunk
    2. 调用 Embedding API
    3. 存入 ChromaDB
    4. 更新知识库统计
    """
    with Session(engine) as session:
        # 获取文件记录
        file_doc = session.get(FileDocument, file_id)
        if not file_doc:
            return {"error": "文件不存在"}

        if file_doc.status != FileStatus.EMBEDDING:
            return {"error": "文件状态不正确"}

        try:
            # 获取所有 Chunks
            chunks = session.exec(
                select(DocumentChunk)
                .where(DocumentChunk.file_id == file_id)
                .order_by(DocumentChunk.original_index)
            ).all()

            if not chunks:
                file_doc.status = FileStatus.READY
                file_doc.progress = 100
                session.add(file_doc)
                session.commit()
                return {"file_id": file_id, "chunk_count": 0}

            # 获取知识库信息 (为了获取 embedding_model)
            kb = session.get(KnowledgeBase, file_doc.kb_id)
            embedding_model_id = kb.embedding_model if kb else None

            # 初始化服务
            # 检查是否使用了自定义模型
            custom_model = session.get(CustomModel, embedding_model_id) if embedding_model_id else None
            
            if custom_model:
                embedding_service = EmbeddingService(
                    base_url=custom_model.base_url,
                    api_key=custom_model.api_key,
                    model=custom_model.model_name
                )
                # 使用实际的模型名称而不是 ID
                target_model_id = custom_model.model_name
            else:
                embedding_service = EmbeddingService()
                target_model_id = embedding_model_id

            vector_service = VectorStoreService()

            # 准备文档数据
            documents = []
            contents = []

            for chunk in chunks:
                documents.append({
                    "id": chunk.id,
                    "content": chunk.content,
                    "metadata": {
                        "file_id": file_id,
                        "file_name": file_doc.name,
                        "page_number": chunk.page_number,
                        "content_type": chunk.content_type.value,
                        "location_info": f"第 {chunk.page_number} 页" if chunk.page_number else "",
                    },
                })
                contents.append(chunk.content)

            # 批量生成向量
            total = len(contents)
            batch_size = 10
            all_embeddings = []

            for i in range(0, total, batch_size):
                batch = contents[i:i + batch_size]
                embeddings = run_async(embedding_service.embed_documents(batch, model_id=target_model_id))
                all_embeddings.extend(embeddings)

                # 更新进度
                progress = int((i + len(batch)) / total * 90)
                file_doc.progress = progress
                session.add(file_doc)
                session.commit()

            # 存入向量库
            vector_service.add_documents(
                kb_id=file_doc.kb_id,
                documents=documents,
                embeddings=all_embeddings,
            )

            # 更新文件状态
            file_doc.status = FileStatus.READY
            file_doc.progress = 100
            file_doc.updated_at = datetime.utcnow()
            session.add(file_doc)

            # 更新知识库统计
            kb = session.get(KnowledgeBase, file_doc.kb_id)
            if kb:
                # 重新计算总 chunk 数
                kb.chunk_count = vector_service.get_count(kb.id)
                kb.updated_at = datetime.utcnow()
                session.add(kb)

            session.commit()

            return {
                "file_id": file_id,
                "chunk_count": len(documents),
                "status": "ready",
            }

        except Exception as e:
            # 更新状态为失败
            file_doc.status = FileStatus.FAILED
            file_doc.error_message = str(e)
            file_doc.updated_at = datetime.utcnow()
            session.add(file_doc)
            session.commit()

            # 重试
            raise self.retry(exc=e, countdown=60)
