"""
数据库模型定义
"""

from sqlmodel import SQLModel, Field, Relationship
from typing import Optional, List
from datetime import datetime
from enum import Enum
import uuid


def generate_uuid() -> str:
    """生成 UUID 字符串"""
    return str(uuid.uuid4())


class FileStatus(str, Enum):
    """文件处理状态"""
    PENDING = "pending"
    PARSING = "parsing"
    PENDING_CONFIRM = "pending_confirm" # 解析完成，等待确认
    PARSED = "parsed"
    EMBEDDING = "embedding"
    READY = "ready"
    FAILED = "failed"


class ContentType(str, Enum):
    """内容类型"""
    TEXT = "text"
    TABLE = "table"
    IMAGE_MIXED = "image_mixed"
    IMAGE = "image"


class KnowledgeBase(SQLModel, table=True):
    """知识库模型"""
    __tablename__ = "knowledge_bases"

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    name: str = Field(index=True) # 允许重名
    description: Optional[str] = None
    embedding_model: str
    vlm_model: str
    chunk_count: int = Field(default=0)
    is_deleted: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # 关系
    files: List["FileDocument"] = Relationship(back_populates="knowledge_base")


class FileDocument(SQLModel, table=True):
    """文件文档模型"""
    __tablename__ = "file_documents"

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    kb_id: str = Field(foreign_key="knowledge_bases.id", index=True)
    name: str
    local_path: str
    size: int
    status: FileStatus = Field(default=FileStatus.PENDING)
    progress: int = Field(default=0)
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # 关系
    knowledge_base: Optional[KnowledgeBase] = Relationship(back_populates="files")
    chunks: List["DocumentChunk"] = Relationship(back_populates="file_document")


class DocumentChunk(SQLModel, table=True):
    """文档切分块模型"""
    __tablename__ = "document_chunks"

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    file_id: str = Field(foreign_key="file_documents.id", index=True)
    content: str
    page_number: Optional[int] = None
    content_type: ContentType = Field(default=ContentType.TEXT)
    image_path: Optional[str] = None
    original_index: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # 关系
    file_document: Optional[FileDocument] = Relationship(back_populates="chunks")


class ModelType(str, Enum):
    """模型类型"""
    LLM = "llm"
    EMBEDDING = "embedding"
    VLM = "vlm"
    RERANK = "rerank"


class CustomModel(SQLModel, table=True):
    """自定义模型配置"""
    __tablename__ = "custom_models"

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    name: str = Field(index=True, description="显示名称")
    model_type: ModelType = Field(index=True)
    base_url: str = Field(description="API地址或本地模型路径")
    api_key: str = Field(default="", description="API Key (本地模型可为空)")
    model_name: str = Field(description="实际调用的模型名称")
    context_length: int = Field(default=4096, description="上下文长度")
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
