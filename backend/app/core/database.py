"""
数据库连接配置
"""

from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy.pool import StaticPool
from app.core.config import get_settings

settings = get_settings()

# 创建数据库引擎
# 增加 timeout 防止 database is locked
connect_args = {"check_same_thread": False, "timeout": 30} if "sqlite" in settings.DATABASE_URL else {}

# 对于文件型 SQLite，不要使用 StaticPool，否则无法利用 WAL 的并发优势
# 只有 :memory: 数据库才强制使用 StaticPool
is_memory_db = ":memory:" in settings.DATABASE_URL

engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.ENV == "dev",
    connect_args=connect_args,
    poolclass=StaticPool if is_memory_db else None, 
)

# 启用 SQLite WAL 模式以提高并发性能
if "sqlite" in settings.DATABASE_URL:
    from sqlalchemy import event
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


def init_db():
    """初始化数据库表"""
    SQLModel.metadata.create_all(engine)


def get_session():
    """获取数据库会话"""
    with Session(engine) as session:
        yield session
