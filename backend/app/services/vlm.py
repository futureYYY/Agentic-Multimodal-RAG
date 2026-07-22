"""
VLM (视觉语言模型) 服务
"""

import base64
import httpx
from typing import Optional

from app.core.config import get_settings

settings = get_settings()


class VLMService:
    """VLM 服务"""

    def __init__(self):
        self.base_url = settings.VLM_BASE_URL
        self.api_key = settings.VLM_API_KEY
        self.model = settings.VLM_MODEL
        self.prompt = settings.VLM_PROMPT

    async def describe_image(self, image_path: str) -> str:
        """获取图片描述"""
        try:
            # 读取图片并转为 base64
            with open(image_path, "rb") as f:
                image_data = base64.b64encode(f.read()).decode("utf-8")

            # 判断图片类型
            ext = image_path.split(".")[-1].lower()
            mime_type = {
                "png": "image/png",
                "jpg": "image/jpeg",
                "jpeg": "image/jpeg",
                "gif": "image/gif",
                "webp": "image/webp",
            }.get(ext, "image/png")

            # 构建请求
            async with httpx.AsyncClient(timeout=120.0, verify=False) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": self.prompt,
                                    },
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": f"data:{mime_type};base64,{image_data}"
                                        },
                                    },
                                ],
                            }
                        ],
                        "max_tokens": 1000,
                    },
                )

                if response.status_code != 200:
                    raise Exception(f"VLM API 错误: {response.text}")

                result = response.json()
                return result["choices"][0]["message"]["content"]

        except Exception as e:
            print(f"VLM 调用失败: {e}")
            return "图片解析失败"
