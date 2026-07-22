"""
Embedding 服务
"""

import httpx
from typing import List
import base64
import os

from app.core.config import get_settings

settings = get_settings()


class EmbeddingService:
    """Embedding 服务"""

    def __init__(self, base_url: str = None, api_key: str = None, model: str = None):
        self.base_url = base_url or settings.EMBEDDING_BASE_URL
        self.api_key = api_key or settings.EMBEDDING_API_KEY
        self.model = model or settings.EMBEDDING_MODEL

    async def embed_query(self, text: str, model_id: str = None) -> List[float]:
        """获取文本的向量表示"""
        embeddings = await self._embed([text], model_id)
        return embeddings[0] if embeddings else []

    async def embed_documents(self, texts: List[str], model_id: str = None) -> List[List[float]]:
        """批量获取文本的向量表示"""
        return await self._embed(texts, model_id)

    async def _embed(self, texts: List[str], model_id: str = None) -> List[List[float]]:
        """调用 Embedding API"""
        target_model = model_id or self.model
        
        # 针对 Doubao 模型 (Volcengine) 的特殊处理
        # 1. 识别：通过模型名称或 URL 特征 (如 ark.cn-beijing.volces.com)
        is_doubao = "doubao" in target_model.lower() or "volces.com" in (self.base_url or "")
        
        if is_doubao:
            return await self._embed_doubao(texts, target_model)

        url = f"{self.base_url}/embeddings"
        
        # 打印调试信息
        print(f"DEBUG_EMBED: Requesting {url}")
        print(f"DEBUG_EMBED: Model: {target_model}")
        print(f"DEBUG_EMBED: Text Count: {len(texts)}")
        if self.api_key:
            masked_key = self.api_key[:6] + "***" + self.api_key[-4:] if len(self.api_key) > 10 else "***"
            print(f"DEBUG_EMBED: API Key: {masked_key}")
        else:
             print("DEBUG_EMBED: API Key: None")

        try:
            # 增加超时时间，并关闭 verify，禁用环境变量（代理）
            async with httpx.AsyncClient(timeout=60.0, verify=False, trust_env=False) as client:
                response = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": target_model,
                        "input": texts,
                    },
                )

                if response.status_code != 200:
                    print(f"DEBUG_EMBED: Error Status: {response.status_code}")
                    print(f"DEBUG_EMBED: Error Body: {response.text}")
                    # 如果是 404，可能是路径不对，尝试一下 doubao 逻辑（作为 fallback）
                    if response.status_code == 404 and "volces" in (self.base_url or ""):
                         print("DEBUG_EMBED: 404 detected, retrying with Doubao format...")
                         return await self._embed_doubao(texts, target_model)
                    
                    raise Exception(f"Embedding API 错误: {response.text}")

                result = response.json()
                # 按 index 排序确保顺序正确
                embeddings = sorted(result["data"], key=lambda x: x["index"])
                return [e["embedding"] for e in embeddings]

        except Exception as e:
            print(f"DEBUG_EMBED: Exception: {e}")
            raise

    async def _embed_doubao(self, texts: List[str], model_id: str) -> List[List[float]]:
        """调用 Doubao (Volcengine) Embedding API"""
        # 官方示例 URL 是完整的，不需要加 /embeddings 后缀
        # 如果 base_url 已经包含了 /embeddings/multimodal，则直接使用
        url = self.base_url
        
        # 严格检查 URL 是否为完整的 endpoint
        if "volces.com" in url and not url.endswith("/embeddings/multimodal"):
             # 如果用户只填了 base (e.g., https://ark.cn-beijing.volces.com/api/v3)，则补全
             url = f"{url.rstrip('/')}/embeddings/multimodal"
        
        print(f"DEBUG_EMBED_DOUBAO: Requesting {url}")
        
        embeddings = []
        
        # 使用 requests 替代 httpx 以避免 InvalidURL: URL too long 问题
        # 这是一个临时的同步调用，运行在 Celery 线程中是可以接受的
        import requests
        import copy

        # Process in batches (Doubao Multimodal API might require single item per request, so default to 1)
        batch_size = 1
        total_texts = len(texts)
        
        for i in range(0, total_texts, batch_size):
            batch_texts = texts[i : i + batch_size]
            print(f"DEBUG_EMBED_DOUBAO: Processing batch {i // batch_size + 1}/{(total_texts + batch_size - 1) // batch_size}, size={len(batch_texts)}")
            
            batch_input = []
            
            for text in batch_texts:
                # 构造 Doubao 格式 input (单个样本)
                # 检查 text 是否包含图片占位符 [图片: xxx]
                sample_input = []
                
                if text.startswith("[图片: ") and text.endswith("]"):
                     # 提取图片路径
                     image_path_str = text[5:-1]
                     
                     # 构造完整路径 (假设图片存储在 settings.IMAGE_DIR)
                     full_image_path = os.path.join(settings.IMAGE_DIR, image_path_str)
                     
                     if os.path.exists(full_image_path):
                         try:
                             with open(full_image_path, "rb") as image_file:
                                 encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                                 
                                 # 获取扩展名以确定 mime type
                                 ext = os.path.splitext(image_path_str)[1].lower().replace('.', '')
                                 if ext == 'jpg': ext = 'jpeg'
                                 if not ext: ext = 'png'
                                 
                                 data_uri = f"data:image/{ext};base64,{encoded_string}"
                                 
                                 sample_input.append({
                                     "type": "image_url", 
                                     "image_url": {
                                         "url": data_uri
                                     }
                                 })
                         except Exception as img_err:
                             print(f"DEBUG_EMBED_DOUBAO: Image processing failed: {img_err}")
                             # Fallback to text if image fails
                             sample_input.append({"type": "text", "text": text})
                     else:
                         print(f"DEBUG_EMBED_DOUBAO: Image not found at {full_image_path}")
                         sample_input.append({"type": "text", "text": text})
                else:
                     clean_text = text.strip()
                     if not clean_text:
                         clean_text = " " 
                     
                     sample_input.append({"type": "text", "text": clean_text})
                
                batch_input.append(sample_input)

            # 发送批量请求
            try:
                # 如果 batch_size 为 1，解包一层 list，因为 API 可能不接受 List[List[Dict]]
                final_input = batch_input
                if len(batch_input) == 1:
                    final_input = batch_input[0]

                payload = {
                    "model": model_id,
                    "input": final_input
                }
                
                # 打印调试信息：Payload (脱敏处理)
                debug_payload = copy.deepcopy(payload)
                if "input" in debug_payload:
                    # 如果是 list of list
                    if isinstance(debug_payload["input"], list) and len(debug_payload["input"]) > 0 and isinstance(debug_payload["input"][0], list):
                         debug_input = []
                         for sample in debug_payload["input"]:
                             debug_sample = []
                             for item in sample:
                                 if item.get("type") == "image_url":
                                     url_val = item.get("image_url", {}).get("url", "")
                                     if len(url_val) > 50:
                                         debug_sample.append({
                                             "type": "image_url",
                                             "image_url": {"url": url_val[:50] + "...(truncated)"}
                                         })
                                     else:
                                         debug_sample.append(item)
                                 else:
                                     debug_sample.append(item)
                             debug_input.append(debug_sample)
                         debug_payload["input"] = debug_input
                    # 如果是 list of dict (single item unwrapped)
                    elif isinstance(debug_payload["input"], list):
                         debug_sample = []
                         for item in debug_payload["input"]:
                             if item.get("type") == "image_url":
                                 url_val = item.get("image_url", {}).get("url", "")
                                 if len(url_val) > 50:
                                     debug_sample.append({
                                         "type": "image_url",
                                         "image_url": {"url": url_val[:50] + "...(truncated)"}
                                     })
                                 else:
                                     debug_sample.append(item)
                             else:
                                 debug_sample.append(item)
                         debug_payload["input"] = debug_sample
                
                # print(f"DEBUG_EMBED_DOUBAO: Payload (Batch): {debug_payload}")

                response = requests.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=120.0, 
                    verify=False,
                    proxies={"http": None, "https": None} # 显式禁用代理
                )

                if response.status_code != 200:
                    print(f"DEBUG_EMBED_DOUBAO: Error Status: {response.status_code}")
                    print(f"DEBUG_EMBED_DOUBAO: Error Body: {response.text}")
                    raise Exception(f"Doubao API Error ({response.status_code}): {response.text}")
                
                result = response.json()
                
                if "data" in result:
                    data_field = result["data"]
                    
                    # 情况1: 返回 List (标准 Batch)
                    if isinstance(data_field, list):
                         if len(data_field) > 0 and "index" in data_field[0]:
                             data_field.sort(key=lambda x: x["index"])
                         
                         for item in data_field:
                             if "embedding" in item:
                                 embeddings.append(item["embedding"])
                             else:
                                 print(f"DEBUG_EMBED_DOUBAO: Item missing embedding: {item}")
                                 raise Exception("Doubao API response item missing embedding")
                    
                    # 情况2: 返回 Dict (Single Item)
                    elif isinstance(data_field, dict):
                         if "embedding" in data_field:
                             embeddings.append(data_field["embedding"])
                         else:
                             print(f"DEBUG_EMBED_DOUBAO: Data dict missing embedding: {data_field}")
                             raise Exception("Doubao API response data missing embedding")
                    
                    else:
                         print(f"DEBUG_EMBED_DOUBAO: Unexpected data structure: {type(data_field)}")
                         raise Exception("Doubao API response format error: data is not list or dict")
                else:
                    print(f"DEBUG_EMBED_DOUBAO: Invalid Response Format (missing 'data'): {result}")
                    raise Exception("Doubao API response format error: missing data")
                    
            except Exception as e:
                print(f"DEBUG_EMBED_DOUBAO: Failed for batch: {e}")
                raise e
        
        return embeddings
