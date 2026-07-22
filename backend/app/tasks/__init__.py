"""
任务模块导出
"""

from app.tasks.celery_app import celery_app
# from app.tasks.parse_tasks import parse_file_task
# from app.tasks.vectorize_tasks import vectorize_file_task

__all__ = [
    "celery_app",
    # "parse_file_task",
    # "vectorize_file_task",
]
