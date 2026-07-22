"""
工具函数
"""


def truncate_string(s: str, max_length: int = 200) -> str:
    """截断字符串"""
    if len(s) <= max_length:
        return s
    return s[:max_length] + "..."
