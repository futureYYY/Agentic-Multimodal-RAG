"""
API 请求/响应 Schema 定义
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Any, Generic, TypeVar
from datetime import datetime
from enum import Enum


# ==================== 通用 ====================

T = TypeVar('T')

class ApiResponse(BaseModel, Generic[T]):
    """通用 API 响应"""
    code: int = 200
    message: str = "success"
    data: Optional[T] = None

class ErrorResponse(BaseModel):
    """错误响应"""
    code: int
    message: str


# ==================== 模型配置 ====================

class ModelsResponse(BaseModel):
    """模型列表响应"""
    llm_models: List[str]
    embedding_models: List[str]
    vlm_models: List[str]


# ==================== 知识库 ====================

class KnowledgeBaseCreate(BaseModel):
    """创建知识库请求"""
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    embedding_model: str
    vlm_model: str


class KnowledgeBaseResponse(BaseModel):
    """知识库响应"""
    id: str
    name: str
    description: Optional[str]
    embedding_model: str
    vlm_model: str
    chunk_count: int
    updated_at: datetime

    class Config:
        from_attributes = True


class KnowledgeBaseDetailResponse(KnowledgeBaseResponse):
    """知识库详情响应"""
    files_count: int


# ==================== 文件 ====================

class FileStatusEnum(str, Enum):
    """文件状态枚举"""
    PENDING = "pending"
    PARSING = "parsing"
    PENDING_CONFIRM = "pending_confirm"
    PARSED = "parsed"
    EMBEDDING = "embedding"
    READY = "ready"
    FAILED = "failed"


class FileUploadResponse(BaseModel):
    """文件上传响应"""
    file_id: str
    status: str


class FileStatusResponse(BaseModel):
    """文件状态响应"""
    id: str
    name: str
    size: int
    status: str
    progress: int
    error_message: Optional[str] = None
    chunk_count: int = 0
    created_at: datetime

    class Config:
        from_attributes = True


# ==================== Chunk ====================

class ContentTypeEnum(str, Enum):
    """内容类型枚举"""
    TEXT = "text"
    TABLE = "table"
    IMAGE_MIXED = "image_mixed"
    IMAGE = "image"


class ChunkResponse(BaseModel):
    """Chunk 响应"""
    id: str
    content: str
    original_file_name: str
    page_number: Optional[int]
    image_url: Optional[str]
    content_type: str

    class Config:
        from_attributes = True


class ChunkUpdate(BaseModel):
    """更新 Chunk 请求"""
    content: str


class VectorizeResponse(BaseModel):
    """向量化响应"""
    status: str
    message: str


# ==================== 召回测试 ====================

class RecallRequest(BaseModel):
    """召回测试请求"""
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=3, ge=1, le=100)
    score_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    rerank_enabled: bool = False
    rerank_score_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    rerank_model_id: Optional[str] = None


class RecallResult(BaseModel):
    """召回结果"""
    chunkId: Optional[str] = None
    score: float
    rerank_score: Optional[float] = None
    content: str
    fileName: str
    kbName: str
    location: str
    imageUrl: Optional[str] = None


class RecallTestResponse(BaseModel):
    """召回测试响应"""
    results: List[RecallResult]
    query_time: float


# ==================== 对话 ====================

class MessageRole(str, Enum):
    """消息角色"""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class Message(BaseModel):
    """消息"""
    role: MessageRole
    content: str


class RewriteRequest(BaseModel):
    """问题改写请求"""
    query: str = Field(..., min_length=1)


class RewriteResponse(BaseModel):
    """问题改写响应"""
    rewritten_query: str


class ChatRequest(BaseModel):
    """对话请求"""
    messages: List[Message]
    kb_ids: List[str]
    stream: bool = True
    use_rewrite: bool = False
    mode: Optional[str] = "chat"
    top_k: Optional[int] = 3
    score_threshold: Optional[float] = 0.3
    model_id: Optional[str] = None
    rerank_enabled: bool = False
    rerank_score_threshold: float = 0.0
    rerank_model_id: Optional[str] = None


# ==================== SSE 事件 ====================

class AgentThoughtEvent(BaseModel):
    """Agent 思考事件"""
    step: str
    content: str


class RagResultEvent(BaseModel):
    """RAG 结果事件"""
    citations: List[RecallResult]
    original_citations: Optional[List[RecallResult]] = None


class AnswerChunkEvent(BaseModel):
    """回答片段事件"""
    content: str


class DoneEvent(BaseModel):
    """完成事件"""
    usage: Optional[dict] = None


# ==================== 自定义模型 ====================

class CustomModelCreate(BaseModel):
    """创建自定义模型请求"""
    name: str = Field(..., min_length=1, description="模型显示名称")
    model_type: str = Field(..., description="模型类型 (llm, embedding, vlm, rerank)")
    base_url: str = Field(..., description="API Base URL")
    api_key: str = Field(..., description="API Key")
    model_name: str = Field(..., description="实际模型名称 (如 gpt-4)")
    context_length: int = Field(default=4096, description="上下文长度")


class CustomModelUpdate(BaseModel):
    """更新自定义模型请求"""
    name: Optional[str] = Field(None, min_length=1)
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model_name: Optional[str] = None
    model_type: Optional[str] = None # 虽然一般不改类型，但作为可选字段
    context_length: Optional[int] = None


class CustomModelResponse(BaseModel):
    """自定义模型响应"""
    id: str
    name: str
    model_type: str
    base_url: str
    model_name: str
    context_length: int = 4096
    is_active: bool

    class Config:
        from_attributes = True
