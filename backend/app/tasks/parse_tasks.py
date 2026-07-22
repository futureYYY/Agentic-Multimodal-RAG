"""
文件解析任务
"""

import asyncio
from datetime import datetime
from sqlmodel import Session, select

from app.tasks.celery_app import celery_app
from app.core.database import engine
from app.models import FileDocument, DocumentChunk, FileStatus, ContentType, KnowledgeBase, CustomModel
from app.services.parser import FileParser
from app.services.vector_store import VectorStoreService
from app.services.embedding import EmbeddingService
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from sqlalchemy.exc import OperationalError

# 定义重试策略：捕获 OperationalError (通常包含 database is locked)，最多重试 5 次，指数退避
db_retry = retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(OperationalError)
)

@db_retry
def safe_commit(session: Session):
    """带重试机制的数据库提交"""
    session.commit()


def run_async(coro):
    """在同步环境中运行异步函数"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(bind=True, max_retries=3)
def parse_file_task(self, file_id: str, **kwargs):
    """Celery 任务包装器"""
    try:
        return process_file_parsing(file_id, **kwargs)
    except Exception as e:
        raise self.retry(exc=e, countdown=60)


@celery_app.task(bind=True, max_retries=3)
def submit_chunks_task(self, file_id: str, chunks_data: list):
    """处理提交的 Chunks 任务"""
    try:
        return process_submitted_chunks(file_id, chunks_data)
    except Exception as e:
        raise self.retry(exc=e, countdown=60)


def process_submitted_chunks(file_id: str, chunks_data: list):
    """
    处理前端提交的 chunks (包含手动修改后的内容)
    """
    print(f"🚀 [SubmitTask] 开始处理提交的 Chunks: {file_id}, Count={len(chunks_data)}")
    with Session(engine, expire_on_commit=False) as session:
        # 获取文件记录
        file_doc = session.get(FileDocument, file_id)
        if not file_doc:
            print(f"❌ [SubmitTask] 文件不存在: {file_id}")
            return {"error": "文件不存在"}

        try:
            # 更新状态为处理中
            file_doc.status = FileStatus.EMBEDDING # 直接进入 Embedding 阶段，因为已经 Parse 过了
            file_doc.progress = 10
            file_doc.updated_at = datetime.utcnow()
            session.add(file_doc)
            safe_commit(session)
            
            # 清理旧数据
            print("🧹 [SubmitTask] 清理旧数据...")
            existing_chunks = session.exec(
                select(DocumentChunk).where(DocumentChunk.file_id == file_id)
            ).all()
            for c in existing_chunks:
                session.delete(c)
            safe_commit(session)

            try:
                vector_service = VectorStoreService()
                vector_service.delete_by_file_id(file_doc.kb_id, file_id)
            except Exception as e:
                print(f"⚠️ [SubmitTask] 清理向量库失败: {e}")

            # 保存 Chunks
            db_chunks = []
            print("🔄 [SubmitTask] 开始保存分块...")

            for idx, chunk_data in enumerate(chunks_data):
                # chunk_data 是字典
                try:
                    c_type = ContentType(chunk_data.get("content_type", "text"))
                except ValueError:
                    c_type = ContentType.TEXT

                db_chunk = DocumentChunk(
                    file_id=file_id,
                    content=chunk_data.get("content", ""),
                    page_number=chunk_data.get("page_number", 0),
                    content_type=c_type,
                    image_path=chunk_data.get("image_path", ""),
                    original_index=idx,
                    vlm_description=chunk_data.get("vlm_description", ""), # 支持 VLM 描述
                )
                db_chunks.append(db_chunk)
                session.add(db_chunk)
            
            # 更新进度为 50% 并提交分块
            file_doc.progress = 50
            session.add(file_doc)
            
            safe_commit(session) # 提交以获取 ID 并保存分块
            print(f"✅ [SubmitTask] 分块保存完成，共 {len(db_chunks)} 个")

            # 向量化并入库
            print("🧠 [SubmitTask] 开始生成 Embedding 并入库...")
            # 刷新对象以确保在同一事务中可用（虽然 commit 会 expire，但我们马上要用它）
            session.refresh(file_doc)

            try:
                # 获取知识库信息 (为了获取 embedding_model)
                kb = session.get(KnowledgeBase, file_doc.kb_id)
                if not kb:
                    raise Exception(f"知识库不存在: {file_doc.kb_id}")
                
                embedding_model_id = kb.embedding_model
                print(f"🔍 [ParseTask] KnowledgeBase ID: {kb.id}")
                print(f"🔍 [ParseTask] Retrieved embedding_model_id from KB: '{embedding_model_id}'")

                # 检查是否为自定义模型
                custom_model = session.get(CustomModel, embedding_model_id)
                
                embedding_service = None
                if custom_model:
                    print(f"🔍 [ParseTask] Using Custom Model: {custom_model.name} ({custom_model.model_name})")
                    embedding_service = EmbeddingService(
                        base_url=custom_model.base_url,
                        api_key=custom_model.api_key,
                        model=custom_model.model_name
                    )
                    # 使用实际模型名称覆盖 ID
                    embedding_model_id = custom_model.model_name
                else:
                    print(f"🔍 [ParseTask] Using System/Default Model: {embedding_model_id}")
                    embedding_service = EmbeddingService()

                vector_service = VectorStoreService()

                documents_for_chroma = []
                contents_to_embed = []

                for chunk in db_chunks:
                    text_content = chunk.content
                    
                    metadata = {
                        "file_id": file_id,
                        "file_name": file_doc.name,
                        "chunk_index": chunk.original_index,
                        "page_number": chunk.page_number or 0,
                        "content_type": chunk.content_type.value,
                        "image_path": chunk.image_path or "",
                        "location_info": f"Page {chunk.page_number or 1} | Chunk #{chunk.original_index + 1}"
                    }
                    
                    documents_for_chroma.append({
                        "id": chunk.id,
                        "content": text_content,
                        "metadata": metadata
                    })
                    contents_to_embed.append(text_content)

                print(f"   -> 正在为 {len(contents_to_embed)} 个块生成向量...")
                embeddings = run_async(embedding_service.embed_documents(contents_to_embed, model_id=embedding_model_id))
                
                print(f"   -> 正在写入 ChromaDB...")
                # 由于 embedding 过程是异步的，session 可能会因为 expire_on_commit=True 而失效
                # 虽然我们 refresh 过了，但为了保险，这里再次 refresh 或者 merge
                # 不过 file_doc 在这里并不需要 update，我们只是用 kb_id
                
                vector_service.add_documents(
                    kb_id=file_doc.kb_id,
                    documents=documents_for_chroma,
                    embeddings=embeddings
                )
                print("✅ [SubmitTask] 向量入库完成！")

            except Exception as embed_err:
                print(f"❌ [SubmitTask] 向量化失败: {embed_err}")
                raise embed_err

            # 完成
            # 这里必须重新获取 file_doc，因为经历了耗时的 embedding 过程，
            # 且之前的 commit 可能导致对象过期或 detached
            file_doc = session.get(FileDocument, file_id)
            if file_doc:
                file_doc.status = FileStatus.PARSED
                file_doc.progress = 100
                file_doc.updated_at = datetime.utcnow()
                session.add(file_doc)
                safe_commit(session)

                # 更新知识库的 chunk_count
                try:
                    kb = session.get(KnowledgeBase, file_doc.kb_id)
                    if kb:
                        # 统计该知识库下所有文件的 chunks 总数
                        from sqlalchemy import func
                        total_chunks = session.exec(
                            select(func.count(DocumentChunk.id))
                            .join(FileDocument)
                            .where(FileDocument.kb_id == kb.id)
                        ).one()
                        
                        kb.chunk_count = total_chunks
                        kb.updated_at = datetime.utcnow()
                        session.add(kb)
                        safe_commit(session)
                        print(f"✅ [SubmitTask] 更新知识库 {kb.id} 的 chunk_count 为 {total_chunks}")
                except Exception as kb_err:
                    print(f"⚠️ [SubmitTask] 更新知识库 chunk_count 失败: {kb_err}")

            print("🎉 [SubmitTask] 任务完成。")

        except Exception as e:
            print(f"❌ [SubmitTask] 异常: {e}")
            import traceback
            traceback.print_exc()
            file_doc = session.get(FileDocument, file_id)
            if file_doc:
                file_doc.status = FileStatus.FAILED
                file_doc.error_message = str(e)
                session.add(file_doc)
                safe_commit(session)
            raise e


def process_file_parsing(
    file_id: str,
    chunk_mode: str = "auto",
    chunk_size: int = 500,
    chunk_overlap: int = 50,
    separator: str = "\n\n",
    auto_vectorize: bool = False
):
    """
    解析文件核心逻辑（解耦 Celery）
    """
    print(f"🚀 [ParseTask] 开始处理文件解析: {file_id}, Mode={chunk_mode}")
    with Session(engine) as session:
        # 获取文件记录
        file_doc = session.get(FileDocument, file_id)
        if not file_doc:
            print(f"❌ [ParseTask] 文件不存在: {file_id}")
            return {"error": "文件不存在"}

        try:
            print(f"📄 [ParseTask] 文件信息: {file_doc.name}, 路径: {file_doc.local_path}")
            
            # 更新状态为解析中
            file_doc.status = FileStatus.PARSING
            file_doc.progress = 0
            file_doc.updated_at = datetime.utcnow()
            session.add(file_doc)
            safe_commit(session)
            
            # 清理旧的 Chunks (支持重解析)
            print("🧹 [ParseTask] 清理旧数据...")
            existing_chunks = session.exec(
                select(DocumentChunk).where(DocumentChunk.file_id == file_id)
            ).all()
            for c in existing_chunks:
                session.delete(c)
            safe_commit(session)

            # 清理向量库中的旧数据
            try:
                vector_service = VectorStoreService()
                vector_service.delete_by_file_id(file_doc.kb_id, file_id)
                print(f"🧹 [ParseTask] 已从向量库清理文件 {file_id} 的数据")
            except Exception as e:
                print(f"⚠️ [ParseTask] 清理向量库失败 (可能之前未入库): {e}")

            # 初始化解析器
            parser = FileParser(kb_id=file_doc.kb_id)

            # 解析文件
            file_doc.progress = 10
            session.add(file_doc)
            safe_commit(session)
            print("✅ [ParseTask] 开始调用 parser.parse()...")

            # 同步调用解析
            try:
                parsed_chunks = parser.parse(
                    file_path=file_doc.local_path,
                    chunk_mode=chunk_mode,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    separator=separator
                )
                print(f"✅ [ParseTask] 解析完成，获得 {len(parsed_chunks)} 个块")
            except Exception as parse_err:
                print(f"❌ [ParseTask] parser.parse() 内部抛出异常: {parse_err}")
                import traceback
                traceback.print_exc()
                raise parse_err

            # 保存 Chunks
            db_chunks = []
            print("🔄 [ParseTask] 开始保存分块...")

            for idx, chunk in enumerate(parsed_chunks):
                # 映射 ContentType
                try:
                    c_type = ContentType(chunk.content_type)
                except ValueError:
                    c_type = ContentType.TEXT

                db_chunk = DocumentChunk(
                    file_id=file_id,
                    content=chunk.content,
                    page_number=chunk.page_number,
                    content_type=c_type,
                    image_path=chunk.image_path,
                    original_index=idx,
                )
                db_chunks.append(db_chunk)
                session.add(db_chunk)

            print(f"✅ [ParseTask] 分块处理完成，共生成 {len(db_chunks)} 个数据库记录块")

            # 更新文件状态为解析完成 (PENDING_CONFIRM)
            file_doc.status = FileStatus.PENDING_CONFIRM
            file_doc.progress = 50
            file_doc.updated_at = datetime.utcnow()
            session.add(file_doc)
            
            # 暂时不更新知识库分块计数，因为还未入库
            # ...

            safe_commit(session)
            print("🎉 [ParseTask] 解析完成，等待用户确认入库。")
            
            # 准备自动向量化的数据
            auto_vectorize_data = None
            if auto_vectorize:
                print(f"🚀 [ParseTask] 自动触发向量化入库: {file_id}")
                auto_vectorize_data = [
                    {
                        "content": c.content,
                        "page_number": c.page_number,
                        "content_type": c.content_type.value,
                        "image_path": c.image_path,
                        "vlm_description": c.vlm_description if hasattr(c, "vlm_description") else None,
                    }
                    for c in db_chunks
                ]

            # 即使在 Session 上下文中，只要 commit 了，连接锁就释放了（对于 WAL）
            # 使用 QueuePool 时，process_submitted_chunks 会获取一个新的连接
            if auto_vectorize_data:
                process_submitted_chunks(file_id, auto_vectorize_data)

            return {
                "file_id": file_id,
                "chunk_count": len(db_chunks) if 'db_chunks' in locals() else 0,
                "status": "pending_confirm",
            }

        except Exception as e:
            # 更新状态为失败
            print(f"❌ [ParseTask] 全局异常捕获: {e}")
            import traceback
            traceback.print_exc()
            
            # 重新获取 session 中的对象以防过期
            file_doc = session.get(FileDocument, file_id)
            if file_doc:
                file_doc.status = FileStatus.FAILED
                file_doc.error_message = str(e)
                file_doc.updated_at = datetime.utcnow()
                session.add(file_doc)
                safe_commit(session)
            
            raise e
