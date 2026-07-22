"""
LLM 服务
"""

from openai import AsyncOpenAI
import httpx
from typing import List, Dict, AsyncGenerator, Any
from app.core.config import get_settings

settings = get_settings()

class LLMService:
    """LLM 服务"""

    def __init__(self, base_url: str = None, api_key: str = None, model: str = None):
        self.base_url = base_url or settings.LLM_BASE_URL
        self.api_key = api_key or settings.LLM_API_KEY
        self.model = model or settings.LLM_MODEL
        self._client = None

    def _get_client(self):
        if not self._client:
            self._client = AsyncOpenAI(
                base_url=self.base_url,
                api_key=self.api_key,
                timeout=120.0,
                http_client=httpx.AsyncClient(
                    trust_env=False
                )
            )
        return self._client

    async def rewrite_query(self, query: str) -> str:
        """问题改写"""
        system_prompt = """你是一个问题优化助手。请将用户输入的口语化问题改写为更清晰、更完整的查询语句。
要求：
1. 保持原意不变
2. 补充缺失的上下文
3. 使用更专业的表述
4. 直接返回改写后的问题，不要添加解释"""

        client = self._get_client()
        
        try:
            response = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query},
                ],
                max_tokens=200,
                temperature=0.3,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"Rewrite Query Error: {e}")
            raise e

    async def generate_stream(
        self,
        messages: List[Dict[str, str]],
        system_prompt: str = None,
        model_id: str = None,
        temperature: float = 0.7,
    ) -> AsyncGenerator[str, None]:
        """
        流式生成回答
        """
        client = self._get_client()
        target_model = model_id or self.model

        # 构造消息列表
        request_messages = []
        if system_prompt:
            request_messages.append({"role": "system", "content": system_prompt})
        
        request_messages.extend(messages)

        try:
            print(f"DEBUG_LLM: Sending request to {target_model}")
            # print(f"DEBUG_LLM: Messages structure: {request_messages}") # 调试用，注意日志脱敏

            response = await client.chat.completions.create(
                model=target_model,
                messages=request_messages,
                stream=True,
                temperature=temperature,
            )

            async for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        except Exception as e:
            print(f"LLM Generate Error: {e}")
            raise e

    async def generate(
        self,
        messages: List[Dict[str, str]],
        system_prompt: str = None,
        model_id: str = None,
    ) -> str:
        """非流式生成回答"""
        client = self._get_client()
        target_model = model_id or self.model
        request_messages = []

        if system_prompt:
            request_messages.append({"role": "system", "content": system_prompt})

        request_messages.extend(messages)

        try:
            response = await client.chat.completions.create(
                model=target_model,
                messages=request_messages,
                max_tokens=2000,
                temperature=0.7,
            )
            return response.choices[0].message.content
        except Exception as e:
             print(f"LLM Generate Error: {e}")
             raise e
