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
from app.services.bm25 import get_bm25_index  # 新增：BM25 索引
from app.core.config import get_settings # Fix: import get_settings
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from sqlalchemy.exc import OperationalError

settings = get_settings() # Fix: Initialize settings

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
def submit_chunks_task(self, file_id: str, chunks_data: list, vector_model: str = None):
    """处理提交的 Chunks 任务"""
    try:
        return process_submitted_chunks(file_id, chunks_data, vector_model=vector_model)
    except Exception as e:
        raise self.retry(exc=e, countdown=60)


def process_submitted_chunks(file_id: str, chunks_data: list, vector_model: str = None):
    """
    处理前端提交的 chunks (包含手动修改后的内容)
    """
    print(f"🚀 [SubmitTask] 开始处理提交的 Chunks: {file_id}, Count={len(chunks_data)}, VectorModel={vector_model}")
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

            # 清理旧的 BM25 索引
            bm25_index = get_bm25_index()
            for c in existing_chunks:
                bm25_index.delete_chunk_index(session, c.id, file_doc.kb_id)
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
                    # 新增字段
                    parent_id=chunk_data.get("parent_id"),
                    heading_level=chunk_data.get("heading_level"),
                    heading_text=chunk_data.get("heading_text"),
                    chunk_type=chunk_data.get("chunk_type", "content"),
                )
                db_chunks.append(db_chunk)
                session.add(db_chunk)

            # 更新进度为 50% 并提交分块
            file_doc.progress = 50
            session.add(file_doc)

            safe_commit(session) # 提交以获取 ID 并保存分块
            print(f"✅ [SubmitTask] 分块保存完成，共 {len(db_chunks)} 个")

            # 建立 BM25 索引
            print("🔍 [SubmitTask] 建立 BM25 索引...")
            for chunk in db_chunks:
                bm25_index.index_chunk(session, chunk.id, chunk.content, file_doc.kb_id)
            safe_commit(session)
            print(f"✅ [SubmitTask] BM25 索引建立完成")

            # 向量化并入库
            print("🧠 [SubmitTask] 开始生成 Embedding 并入库...")
            # 刷新对象以确保在同一事务中可用（虽然 commit 会 expire，但我们马上要用它）
            session.refresh(file_doc)

            try:
                # 获取知识库信息 (为了获取 embedding_model)
                kb = session.get(KnowledgeBase, file_doc.kb_id)
                if not kb:
                    raise Exception(f"知识库不存在: {file_doc.kb_id}")
                
                # 优先使用传入的 vector_model，否则使用知识库配置
                selected_model_id = vector_model or kb.embedding_model
                embedding_model_id = selected_model_id

                # 关键修复：如果传入了 vector_model 且与当前 KB 配置不同（或是首次设置），则立即更新 KB 配置
                # 这样确保后续流程（如召回）能立即感知到模型变更
                if vector_model and vector_model != kb.embedding_model:
                    print(f"🔄 [SubmitTask] 更新知识库 {kb.id} 的 Embedding 模型: {kb.embedding_model} -> {vector_model}")
                    
                    # ⚠️ 警告：模型变更通常意味着维度变更，必须重建 Collection
                    # 否则会报 "Collection expecting embedding with dimension X, got Y"
                    print(f"⚠️ [SubmitTask] 检测到模型变更，正在删除旧的 ChromaDB Collection 以避免维度冲突...")
                    try:
                        vector_service = VectorStoreService()
                        vector_service.delete_collection(file_doc.kb_id)
                        print(f"✅ [SubmitTask] 旧 Collection 已删除")
                    except Exception as e:
                        print(f"⚠️ [SubmitTask] 删除旧 Collection 失败 (可能不存在): {e}")

                    kb.embedding_model = vector_model
                    session.add(kb)
                    safe_commit(session)
                    session.refresh(kb)

                print(f"🔍 [ParseTask] KnowledgeBase ID: {kb.id}")
                print(f"🔍 [ParseTask] Target embedding_model_id: '{embedding_model_id}' (From param: {vector_model}, From KB: {kb.embedding_model})")

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
                        "location_info": f"Page {chunk.page_number or 1} | Chunk #{chunk.original_index + 1}",
                        # 新增字段
                        "parent_id": chunk.parent_id or "",
                        "heading_level": chunk.heading_level or 0,
                        "heading_text": chunk.heading_text or "",
                        "chunk_type": chunk.chunk_type or "content",
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

                if selected_model_id and kb.embedding_model != selected_model_id:
                    kb.embedding_model = selected_model_id
                    kb.updated_at = datetime.utcnow()
                    session.add(kb)
                    safe_commit(session)
                    print(f"✅ [SubmitTask] 已更新知识库 embedding_model 为 {selected_model_id}")

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
    chunk_mode: str = "custom", 
    chunk_size: int = 500, 
    chunk_overlap: int = 50,
    separator: str = "\n\n",
    heading_level: int = 2,
    embedding_model: str = None,
    vector_model: str = None,
    auto_vectorize: bool = False
):
    """
    处理文件解析任务
    
    Args:
        file_id: 文件ID
        chunk_mode: 切分模式 (custom, no_split, structure, semantic)
        chunk_size: 块大小
        chunk_overlap: 重叠大小
        separator: 分隔符
        heading_level: 标题切分级别 (1-3)
        embedding_model: 语义切分用的 Embedding 模型 ID (Optional)
        vector_model: 向量化用的 Embedding 模型 ID (Optional)
        auto_vectorize: 解析完成后是否自动入库
    """
    # 立即打印入参，确保前端传值正确
    print(f"🚀 [ParseTask] 接收到任务: file_id={file_id}, chunk_mode={chunk_mode}, embedding_model={embedding_model}, vector_model={vector_model}", flush=True)
    
    # 类型安全转换
    try:
        if chunk_size is not None: chunk_size = int(chunk_size)
        if chunk_overlap is not None: chunk_overlap = int(chunk_overlap)
        if heading_level is not None: heading_level = int(heading_level)
    except (ValueError, TypeError) as e:
        print(f"⚠️ [ParseTask] 参数类型转换失败: {e}")
        # 使用默认值
        chunk_size = chunk_size or 500
        chunk_overlap = chunk_overlap or 50
        heading_level = heading_level or 2

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

            # --- 1. 获取向量模型配置 (用于决定 Chunk 是否超过向量模型限制) ---
            vector_max_tokens = 8192 # 默认安全值
            if vector_model:
                if vector_model == "sys_embedding":
                    vector_max_tokens = settings.EMBEDDING_MAX_CONTEXT_LENGTH
                else:
                    try:
                        custom_vec = session.get(CustomModel, vector_model)
                        if custom_vec:
                            vector_max_tokens = custom_vec.context_length
                            print(f"ℹ️ [ParseTask] 使用向量模型 {custom_vec.name} 配置: Max Tokens={vector_max_tokens}")
                    except Exception as e:
                        print(f"⚠️ [ParseTask] 获取向量模型 {vector_model} 配置失败: {e}")
            elif hasattr(settings, 'EMBEDDING_MAX_CONTEXT_LENGTH'):
                 vector_max_tokens = settings.EMBEDDING_MAX_CONTEXT_LENGTH

            # --- 2. 获取语义切分模型配置 ---
            semantic_max_tokens = None
            embedding_base_url = None
            embedding_api_key = None
            
            if embedding_model:
                if embedding_model == "sys_embedding":
                    # 处理系统预设模型
                    embedding_model = settings.EMBEDDING_MODEL
                    embedding_base_url = settings.EMBEDDING_BASE_URL
                    embedding_api_key = settings.EMBEDDING_API_KEY
                    semantic_max_tokens = settings.EMBEDDING_MAX_CONTEXT_LENGTH
                    print(f"ℹ️ [ParseTask] 语义切分使用系统预设模型: {embedding_model}")
                else:
                    try:
                        # 尝试从 CustomModel 获取配置
                        print(f"ℹ️ [ParseTask] 正在查询语义切分模型配置, ID: {embedding_model}")
                        custom_model_obj = session.get(CustomModel, embedding_model)
                        if custom_model_obj:
                             semantic_max_tokens = custom_model_obj.context_length
                             embedding_base_url = custom_model_obj.base_url
                             embedding_api_key = custom_model_obj.api_key
                             # 关键修复：将 embedding_model 更新为实际的模型名称 (model_name)，而非数据库 ID
                             embedding_model = custom_model_obj.model_name
                             print(f"ℹ️ [ParseTask] 语义切分使用自定义模型 {custom_model_obj.name} (ID: {custom_model_obj.id}, Name: {embedding_model}) - Base URL: {embedding_base_url}, Max Tokens: {semantic_max_tokens}")
                        else:
                            print(f"⚠️ [ParseTask] 未找到 ID 为 {embedding_model} 的自定义模型！将使用默认配置。")
                    except Exception as e:
                        print(f"⚠️ [ParseTask] 获取自定义模型信息失败: {e}")
                        import traceback
                        traceback.print_exc()



            # 解析文件
            file_doc.progress = 10
            session.add(file_doc)
            safe_commit(session)
            print(f"✅ [ParseTask] 开始调用 parser.parse()... Mode: {chunk_mode}")

            # 同步调用解析
            try:
                parsed_chunks = parser.parse(
                    file_path=file_doc.local_path,
                    chunk_mode=chunk_mode,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    separator=separator,
                    heading_level=heading_level,  # 新增：传递 heading_level
                    embedding_model=embedding_model,  # 传递 embedding_model
                    embedding_base_url=embedding_base_url, # 传递 base_url
                    embedding_api_key=embedding_api_key, # 传递 api_key
                    use_semantic=bool(embedding_model) or chunk_mode == "semantic", # 显式启用语义切分
                    embedding_max_tokens=vector_max_tokens, # 传递向量模型上下文长度
                    semantic_max_tokens=semantic_max_tokens # 传递语义模型上下文长度
                )

                print(f"✅ [ParseTask] 解析完成，获得 {len(parsed_chunks)} 个块")
                if chunk_mode in ["no_split", "no_chunk"]:
                    print(f"🔍 [ParseTask] No Split Mode Result: {[c.content[:50] + '...' for c in parsed_chunks]}")
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

                # 获取 metadata 中的新字段（结构化模式会有这些信息）
                metadata = chunk.metadata if hasattr(chunk, 'metadata') and chunk.metadata else {}

                db_chunk = DocumentChunk(
                    file_id=file_id,
                    content=chunk.content,
                    page_number=chunk.page_number,
                    content_type=c_type,
                    image_path=chunk.image_path,
                    original_index=idx,
                    # 新增字段
                    parent_id=metadata.get("parent_id"),
                    heading_level=metadata.get("heading_level"),
                    heading_text=metadata.get("heading_text"),
                    chunk_type=metadata.get("chunk_type", "content"),
                    token_count=chunk.token_count, # Add token_count
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
                auto_vectorize_data = []
                for c in db_chunks:
                    auto_vectorize_data.append({
                        "content": c.content,
                        "page_number": c.page_number,
                        "content_type": c.content_type.value,
                        "image_path": c.image_path,
                        # 新增字段
                        "parent_id": c.parent_id,
                        "heading_level": c.heading_level,
                        "heading_text": c.heading_text,
                        "chunk_type": c.chunk_type,
                        "token_count": c.token_count,
                    })

            # 即使在 Session 上下文中，只要 commit 了，连接锁就释放了（对于 WAL）
            # 使用 QueuePool 时，process_submitted_chunks 会获取一个新的连接
            if auto_vectorize_data:
                process_submitted_chunks(file_id, auto_vectorize_data, vector_model=vector_model)

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
