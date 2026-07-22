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
    token_count: Optional[int] = 0

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
    top_k: int = Field(default=50, ge=1, le=200)
    score_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    rerank_enabled: bool = False
    rerank_score_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    rerank_model_id: Optional[str] = None
    # 新增字段
    search_mode: str = Field(default="hybrid", description="检索模式: vector | fulltext | hybrid")
    vector_weight: float = Field(default=0.7, ge=0.0, le=1.0)
    bm25_weight: float = Field(default=0.3, ge=0.0, le=1.0)
    rerank_top_k: int = Field(default=20, ge=5, le=50)
    context_window: int = Field(default=1, ge=0, le=5, description="上下文窗口大小 (0=不扩展)")


class RecallResult(BaseModel):
    """召回结果"""
    chunkId: Optional[str] = None
    score: float
    rerank_score: Optional[float] = None
    vector_score: Optional[float] = None  # 新增：向量分数
    bm25_score: Optional[float] = None    # 新增：BM25 分数
    content: str
    fileName: str
    kbName: str
    location: str
    imageUrl: Optional[str] = None
    # 新增：结构化信息
    heading_text: Optional[str] = None
    heading_level: Optional[int] = None


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
    top_k: Optional[int] = 50
    score_threshold: Optional[float] = 0.0
    model_id: Optional[str] = None
    rerank_enabled: bool = False
    rerank_score_threshold: float = 0.0
    rerank_model_id: Optional[str] = None
    # 新增字段
    search_mode: str = "hybrid"  # vector | fulltext | hybrid
    vector_weight: float = 0.7
    bm25_weight: float = 0.3
    rerank_top_k: int = 20
    context_window: int = 1  # 上下文窗口大小


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


# ==================== 检索日志 ====================

class RetrievalLogResponse(BaseModel):
    """检索日志响应"""
    id: str
    kb_id: str
    query: str
    search_mode: str
    vector_count: int
    bm25_count: int
    merged_count: int
    rerank_count: int
    final_count: int
    latency_ms: float
    vector_latency_ms: float
    bm25_latency_ms: float
    rerank_latency_ms: float
    top_k: int
    score_threshold: float
    rerank_enabled: bool
    vector_weight: float
    bm25_weight: float
    results_summary: str
    created_at: datetime

    class Config:
        from_attributes = True


class RetrievalLogListResponse(BaseModel):
    """检索日志列表响应"""
    logs: List[RetrievalLogResponse]
    total: int
    page: int
    page_size: int
