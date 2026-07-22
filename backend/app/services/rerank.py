"""
Rerank 服务模块
"""

from openai import OpenAI
import httpx
import json
import re
from typing import List, Dict, Any, Optional
from app.models.models import CustomModel, ModelType
from app.core.database import get_session
from sqlmodel import select, Session
from app.core.rerank_model import RerankModelLoader

def extract_json_from_text(text: str) -> str:
    """从模型响应中提取 JSON 字符串"""
    # 1. 移除 <think> 思考过程
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    
    # 2. 尝试提取 markdown 代码块
    match = re.search(r'```(?:json)?\s*(.*?)```', text, re.DOTALL)
    if match:
        return match.group(1).strip()
        
    # 3. 尝试寻找第一个 { 和最后一个 }
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        return text[start:end+1]
        
    return text.strip()

class RerankService:
    def __init__(self, session: Session):
        self.session = session

    def get_rerank_model_by_id(self, model_id: str) -> Optional[CustomModel]:
        """根据 ID 获取 Rerank 模型"""
        return self.session.get(CustomModel, model_id)

    def get_default_rerank_model(self) -> Optional[CustomModel]:
        """获取默认（激活）的 Rerank 模型"""
        statement = select(CustomModel).where(
            CustomModel.model_type == ModelType.RERANK,
            CustomModel.is_active == True
        )
        return self.session.exec(statement).first()

    def rerank(self, query: str, candidates: List[str], model: Optional[CustomModel] = None) -> List[Dict[str, Any]]:
        """
        执行重排序
        :param query: 查询语句
        :param candidates: 候选文本列表
        :param model: 指定模型
        :return: 排序后的结果 [{"text": "...", "score": 0.xx, "index": original_index}, ...]
        """
        if not candidates:
            return []

        if not model:
            model = self.get_default_rerank_model()
        
        if not model:
            print("No active rerank model found.")
            return []

        try:
            # 判断是否为本地模型 (根据 base_url 是否为 http 开头)
            # 如果是本地路径，使用 RerankModelLoader
            is_remote_api = model.base_url.startswith("http://") or model.base_url.startswith("https://")
            
            if not is_remote_api:
                # 使用本地模型加载器
                loader = RerankModelLoader()
                results = loader.predict(
                    query=query,
                    passages=candidates,
                    model_path=model.base_url,
                    max_len=model.context_length
                )
                return results
            else:
                # 使用 OpenAI 兼容 API (保留原有逻辑作为远程调用支持)
                return self._rerank_via_api(query, candidates, model)

        except Exception as e:
            print(f"Rerank execution failed: {e}")
            import traceback
            traceback.print_exc()
            return []

    def _rerank_via_api(self, query: str, candidates: List[str], model: CustomModel) -> List[Dict[str, Any]]:
        """通过 API 调用 Rerank"""
        client = OpenAI(
            base_url=model.base_url,
            api_key=model.api_key,
            http_client=httpx.Client(
                trust_env=False
            )
        )

        prompt = f"""你是一个文本相关性重排序模型，请计算查询与每个候选文本的相关性得分（0-1，越接近1越相关）。

查询："{query}"

候选文本列表：
"""
        for i, text in enumerate(candidates):
            safe_text = text[:500].replace("\n", " ") 
            prompt += f"[{i}] {safe_text}\n"

        prompt += """
任务要求：
1. 仅对上述【候选文本列表】中的条目进行打分，绝对不要把【查询】本身作为候选文本。
2. 仅返回 JSON 格式，不要包含任何推理过程或额外文字。
3. JSON 结构示例：{"candidates": [{"index": 0, "score": 0.95, "text": "..."}, {"index": 1, "score": 0.12, "text": "..."}]}
   - index: 必须对应候选文本列表中的方括号索引 [i]
   - text: 候选文本的内容
   - score: 相关性得分 (0-1)
4. 结果按 score 降序排列。

/no_think
"""
        try:
            response = client.chat.completions.create(
                model=model.model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                stream=False,
            )
            result_text = response.choices[0].message.content
            json_str = extract_json_from_text(result_text)
            data = json.loads(json_str)
            
            if "candidates" in data:
                results = data["candidates"]
            elif isinstance(data, list):
                results = data
            else:
                return []
            
            final_results = []
            for item in results:
                if "index" in item and "score" in item:
                    final_results.append({
                        "index": item["index"],
                        "score": float(item["score"]),
                        "text": item.get("text", "")
                    })
            final_results.sort(key=lambda x: x["score"], reverse=True)
            return final_results
        except Exception as e:
            print(f"API Rerank failed: {e}")
            return []

def test_rerank_connection(model_in: Any) -> Dict[str, Any]:
    """测试 Rerank 模型连接"""
    try:
        # 兼容 Pydantic v1/v2 和 SQLModel
        base_url = getattr(model_in, "base_url", None)
        model_name = getattr(model_in, "model_name", None)
        context_length = getattr(model_in, "context_length", 4096)
        
        # 兼容字典访问 (如果是 dict)
        if base_url is None and isinstance(model_in, dict):
            base_url = model_in.get("base_url")
            model_name = model_in.get("model_name")
            context_length = model_in.get("context_length", 4096)

        if not base_url:
            return {"success": False, "message": "模型路径/地址不能为空"}

        is_remote_api = base_url.startswith("http://") or base_url.startswith("https://")

        if not is_remote_api:
            # 测试本地加载
            try:
                loader = RerankModelLoader()
                # 尝试加载模型
                loader.load_model(base_url, max_len=context_length)
                
                # 尝试简单推理
                query = "测试"
                passages = ["这是一个测试文档", "这是另一个无关文档"]
                results = loader.predict(query, passages, base_url, context_length)
                
                return {"success": True, "message": "本地模型加载并测试成功", "data": results}
            except Exception as e:
                return {"success": False, "message": f"本地模型加载失败: {str(e)}", "error": str(e)}
        else:
            # 测试远程 API (保持原有逻辑)
            # ... (原有 API 测试逻辑，略简化或复用)
            # 为保持简洁，这里只保留基本的 API 测试
            api_key = getattr(model_in, "api_key", "")
            if isinstance(model_in, dict):
                api_key = model_in.get("api_key", "")
                
            client = OpenAI(base_url=base_url, api_key=api_key)
            # ... 发送简单请求 ...
            # 鉴于用户主要关注本地，这里简单做个连通性检查即可，或者复用上面的 prompt
            # 为了完整性，还是写全一点
            try:
                 response = client.chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": "test"}],
                    max_tokens=10
                )
                 return {"success": True, "message": "API 连接成功"}
            except Exception as e:
                return {"success": False, "message": f"API 连接失败: {str(e)}"}

    except Exception as e:
        return {"success": False, "message": f"未知错误: {str(e)}", "error": str(e)}
