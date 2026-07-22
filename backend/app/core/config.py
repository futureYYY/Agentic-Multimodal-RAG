"""
应用配置模块
"""

from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import List
import os


class Settings(BaseSettings):
    """应用配置"""

    # 服务配置
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    ENV: str = "dev"

    # 数据库与存储
    DATABASE_URL: str = "sqlite:///./local.db"
    REDIS_URL: str = "redis://localhost:6379/0"
    CHROMA_DB_DIR: str = "./storage/chroma_db"
    UPLOAD_DIR: str = "./storage/uploads"
    IMAGE_DIR: str = "./storage/images"

    # LLM 配置
    LLM_BASE_URL: str = "http://localhost:11434/v1"
    LLM_API_KEY: str = "ollama"
    LLM_MODEL: str = "qwen2.5:7b"

    # Embedding 配置
    EMBEDDING_BASE_URL: str = "http://localhost:11434/v1"
    EMBEDDING_API_KEY: str = "ollama"
    EMBEDDING_MODEL: str = "nomic-embed-text"

    # VLM 配置
    VLM_BASE_URL: str = "http://localhost:11434/v1"
    VLM_API_KEY: str = "ollama"
    VLM_MODEL: str = "llava:7b"

    # 任务配置
    MAX_PARSING_WORKERS: int = 10

    # 切分配置
    CHUNK_SIZE: int = 500
    CHUNK_OVERLAP: int = 50

    # VLM 提示词
    VLM_PROMPT: str = "请详细描述这张图片的内容，包括图表中的数据趋势、关键文字信息。"

    # 文件限制
    MAX_FILE_SIZE: int = 100 * 1024 * 1024  # 100MB
    ALLOWED_EXTENSIONS: List[str] = [".pdf", ".docx", ".xlsx", ".csv", ".txt", ".jpg", ".jpeg", ".png", ".bmp", ".webp"]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    """获取配置单例"""
    return Settings()


# 确保存储目录存在
def ensure_directories():
    """确保必要的目录存在"""
    settings = get_settings()
    dirs = [
        settings.UPLOAD_DIR,
        settings.IMAGE_DIR,
        settings.CHROMA_DB_DIR,
    ]
    for d in dirs:
        os.makedirs(d, exist_ok=True)
