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


def estimate_tokens(text: str) -> int:
    """
    估算文本的 token 数量

    策略：中文按字符数估算，英文按单词数估算
    """
    import re
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    english_words = len(re.findall(r'[a-zA-Z]+', text))
    return chinese_chars + english_words


def batch_rerank(
    query: str,
    candidates: List[Dict[str, Any]],
    rerank_service: RerankService,
    model: CustomModel,
    max_context_tokens: int = None,
    top_k: int = None,
    score_threshold: float = 0.0
) -> List[Dict[str, Any]]:
    """
    分批调用 Rerank 模型

    当候选集超过模型上下文限制时，分批处理后合并结果

    Args:
        query: 查询语句
        candidates: 候选文档列表，每个包含 "content" 字段
        rerank_service: Rerank 服务实例
        model: Rerank 模型配置
        max_context_tokens: 单批最大 token 数
        top_k: 返回的 top-k 数量
        score_threshold: 分数阈值

    Returns:
        重排序后的结果列表
    """
    from app.core.config import get_settings
    settings = get_settings()

    max_context_tokens = max_context_tokens or settings.RERANK_MAX_CONTEXT
    top_k = top_k or settings.RERANK_DEFAULT_TOP_K

    if not candidates:
        return []

    # 1. 估算每个 candidate 的 token 数
    candidates_with_tokens = []
    for c in candidates:
        tokens = estimate_tokens(c.get("content", ""))
        candidates_with_tokens.append({**c, "_tokens": tokens})

    # 2. 分批
    batches = []
    current_batch = []
    current_tokens = estimate_tokens(query)  # 查询也占用 token

    for candidate in candidates_with_tokens:
        tokens = candidate["_tokens"]

        # 如果单个文档就超过限制，单独成批
        if tokens > max_context_tokens:
            if current_batch:
                batches.append(current_batch)
                current_batch = []
                current_tokens = estimate_tokens(query)
            # 截断过长的文档
            truncated_content = candidate.get("content", "")[:max_context_tokens * 2]
            batches.append([{**candidate, "content": truncated_content}])
            continue

        # 如果加入当前候选后超过限制，先结算当前批次
        if current_tokens + tokens > max_context_tokens and current_batch:
            batches.append(current_batch)
            current_batch = []
            current_tokens = estimate_tokens(query)

        current_batch.append(candidate)
        current_tokens += tokens

    if current_batch:
        batches.append(current_batch)

    print(f"📊 [Rerank] 分 {len(batches)} 批处理 {len(candidates)} 条候选")

    # 3. 分批调用 rerank
    all_results = []
    for batch_idx, batch in enumerate(batches):
        batch_texts = [c.get("content", "") for c in batch]

        try:
            rerank_results = rerank_service.rerank(query, batch_texts, model=model)

            for rr in rerank_results:
                idx = rr.get("index")
                if idx is not None and 0 <= idx < len(batch):
                    result = {**batch[idx]}
                    result["rerank_score"] = rr.get("score", 0)
                    # 移除临时字段
                    result.pop("_tokens", None)
                    all_results.append(result)

            print(f"   批次 {batch_idx + 1}/{len(batches)} 完成")

        except Exception as e:
            print(f"⚠️ [Rerank] 批次 {batch_idx + 1} 失败: {e}")
            # 批次失败时，保留原始分数
            for c in batch:
                result = {**c}
                result["rerank_score"] = c.get("score", 0)
                result.pop("_tokens", None)
                all_results.append(result)

    # 4. 按 rerank_score 排序
    all_results.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)

    # 5. 过滤低于阈值的结果
    filtered_results = [
        r for r in all_results
        if r.get("rerank_score", 0) >= score_threshold
    ]

    # 6. 返回 top_k
    return filtered_results[:top_k]


def expand_context(
    top_chunks: List[Dict[str, Any]],
    session,
    window_size: int = None,
    max_parent_length: int = None
) -> List[Dict[str, Any]]:
    """
    扩展上下文窗口

    策略：
    1. 父文档拼接（同一 parent_id 下的子 chunk）
    2. 前后各 window_size 个 chunk

    Args:
        top_chunks: Rerank 后的 top 结果
        session: 数据库会话
        window_size: 前后上下文 chunk 数
        max_parent_length: 父文档最大拼接长度

    Returns:
        扩展后的结果列表
    """
    from sqlmodel import select
    from app.models import DocumentChunk
    from app.core.config import get_settings

    settings = get_settings()
    window_size = window_size if window_size is not None else settings.CONTEXT_WINDOW_SIZE
    max_parent_length = max_parent_length or settings.MAX_PARENT_LENGTH

    expanded = []
    seen_ids = set()

    for chunk in top_chunks:
        chunk_id = chunk.get("id")
        if not chunk_id or chunk_id in seen_ids:
            continue

        # 1. 获取当前 chunk 的完整信息
        db_chunk = session.get(DocumentChunk, chunk_id)
        if not db_chunk:
            # 如果数据库中没有，直接使用原始数据
            expanded.append(chunk)
            seen_ids.add(chunk_id)
            continue

        # 2. 父文档拼接
        parent_id = db_chunk.parent_id
        parent_content = ""

        if parent_id:
            # 获取同一父 chunk 下的所有子 chunk
            siblings = session.exec(
                select(DocumentChunk)
                .where(DocumentChunk.parent_id == parent_id)
                .order_by(DocumentChunk.original_index)
            ).all()

            sibling_ids = [s.id for s in siblings]
            seen_ids.update(sibling_ids)

            # 拼接内容，注意长度限制
            sibling_contents = [s.content for s in siblings]
            parent_content = "\n".join(sibling_contents)

            if len(parent_content) > max_parent_length:
                # 截断，保留当前 chunk 所在的部分
                # 找到当前 chunk 在兄弟中的位置
                current_idx = next((i for i, s in enumerate(siblings) if s.id == chunk_id), 0)

                # 保留当前 chunk 前后的内容
                kept_siblings = []
                current_len = 0
                for i, s in enumerate(siblings):
                    if current_len + len(s.content) > max_parent_length:
                        break
                    if abs(i - current_idx) <= 2:  # 保留附近的
                        kept_siblings.append(s.content)
                        current_len += len(s.content)
                    elif i < current_idx and current_len < max_parent_length // 2:
                        kept_siblings.append(s.content)
                        current_len += len(s.content)

                parent_content = "\n".join(kept_siblings)
        else:
            parent_content = db_chunk.content
            seen_ids.add(chunk_id)

        # 3. 前后上下文窗口
        prev_contents = []
        next_contents = []

        if window_size > 0:
            # 获取前面的 chunks
            prev_chunks = session.exec(
                select(DocumentChunk)
                .where(
                    DocumentChunk.file_id == db_chunk.file_id,
                    DocumentChunk.original_index < db_chunk.original_index
                )
                .order_by(DocumentChunk.original_index.desc())
                .limit(window_size)
            ).all()

            # 获取后面的 chunks
            next_chunks = session.exec(
                select(DocumentChunk)
                .where(
                    DocumentChunk.file_id == db_chunk.file_id,
                    DocumentChunk.original_index > db_chunk.original_index
                )
                .order_by(DocumentChunk.original_index)
                .limit(window_size)
            ).all()

            prev_contents = [c.content for c in reversed(prev_chunks)]
            next_contents = [c.content for c in next_chunks]

            # 标记这些 chunk 已处理
            seen_ids.update(c.id for c in prev_chunks)
            seen_ids.update(c.id for c in next_chunks)

        # 4. 组合完整上下文
        all_parts = []
        if prev_contents:
            all_parts.extend(prev_contents)
        all_parts.append(parent_content)
        if next_contents:
            all_parts.extend(next_contents)

        full_context = "\n".join(filter(None, all_parts))

        # 5. 构建扩展结果
        expanded.append({
            "id": chunk_id,
            "content": full_context,
            "original_content": chunk.get("content", db_chunk.content),  # 保留原始内容
            "score": chunk.get("score", 0),
            "rerank_score": chunk.get("rerank_score", 0),
            "kb_id": chunk.get("kb_id", ""),
            "kb_name": chunk.get("kb_name", ""),
            "file_id": chunk.get("file_id", db_chunk.file_id),
            "file_name": chunk.get("file_name", ""),
            "page_number": chunk.get("page_number", db_chunk.page_number),
            "content_type": chunk.get("content_type", db_chunk.content_type),
            "image_path": chunk.get("image_path", db_chunk.image_path),
            "metadata": {
                "is_expanded": True,
                "parent_id": parent_id,
                "has_prev_context": len(prev_contents) > 0,
                "has_next_context": len(next_contents) > 0,
                "heading_level": chunk.get("heading_level", db_chunk.heading_level),
                "heading_text": chunk.get("heading_text", db_chunk.heading_text),
            }
        })

    return expanded
