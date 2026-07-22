"""
统一检索服务

整合：
- 向量检索
- BM25 全文检索
- 混合检索（Hybrid）
- 分数归一化与融合
- 检索结果去重
- 检索缓存
"""

import time
import hashlib
import json
from typing import List, Dict, Any, Optional, Literal, Tuple
from datetime import datetime

from sqlmodel import Session, select

from app.models import DocumentChunk, FileDocument, KnowledgeBase, RetrievalLog, generate_uuid, CustomModel
from app.services.vector_store import VectorStoreService
from app.services.embedding import EmbeddingService
from app.services.bm25 import BM25Index, get_bm25_index
from app.core.config import get_settings

settings = get_settings()


class RetrievalCache:
    """检索结果缓存"""

    def __init__(self, ttl_seconds: int = None, enabled: bool = None):
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.ttl = ttl_seconds or settings.RETRIEVAL_CACHE_TTL
        self.enabled = enabled if enabled is not None else settings.RETRIEVAL_CACHE_ENABLED

    def _make_key(
        self,
        query: str,
        kb_ids: List[str],
        search_mode: str,
        top_k: int
    ) -> str:
        """生成缓存 key"""
        content = f"{query}|{sorted(kb_ids)}|{search_mode}|{top_k}"
        return hashlib.md5(content.encode()).hexdigest()

    def get(
        self,
        query: str,
        kb_ids: List[str],
        search_mode: str,
        top_k: int
    ) -> Optional[List[Dict[str, Any]]]:
        """获取缓存"""
        if not self.enabled:
            return None

        key = self._make_key(query, kb_ids, search_mode, top_k)
        if key in self.cache:
            entry = self.cache[key]
            if time.time() - entry["timestamp"] < self.ttl:
                return entry["results"]
            else:
                del self.cache[key]
        return None

    def set(
        self,
        query: str,
        kb_ids: List[str],
        search_mode: str,
        top_k: int,
        results: List[Dict[str, Any]]
    ) -> None:
        """设置缓存"""
        if not self.enabled:
            return

        key = self._make_key(query, kb_ids, search_mode, top_k)
        self.cache[key] = {
            "results": results,
            "timestamp": time.time()
        }

    def clear(self) -> None:
        """清空缓存"""
        self.cache.clear()


def normalize_scores(scores: Dict[str, float]) -> Dict[str, float]:
    """
    Min-Max 归一化分数

    将分数映射到 [0, 1] 区间
    """
    if not scores:
        return {}

    min_s = min(scores.values())
    max_s = max(scores.values())

    if max_s == min_s:
        return {k: 1.0 for k in scores}

    return {k: (v - min_s) / (max_s - min_s) for k, v in scores.items()}


def deduplicate_results(
    results: List[Dict[str, Any]],
    similarity_threshold: float = 0.85
) -> List[Dict[str, Any]]:
    """
    检索结果去重

    策略：
    1. ID 去重
    2. 内容相似度去重（基于 Jaccard 相似度）
    """
    seen_ids = set()
    seen_contents = []
    deduplicated = []

    for r in results:
        chunk_id = r.get("id") or r.get("chunk_id")

        # ID 去重
        if chunk_id in seen_ids:
            continue

        content = r.get("content", "")

        # 内容相似度去重
        is_duplicate = False
        for seen in seen_contents:
            sim = jaccard_similarity(content, seen)
            if sim > similarity_threshold:
                is_duplicate = True
                break

        if not is_duplicate:
            seen_ids.add(chunk_id)
            seen_contents.append(content)
            deduplicated.append(r)

    return deduplicated


def jaccard_similarity(text1: str, text2: str) -> float:
    """计算两个文本的 Jaccard 相似度"""
    if not text1 or not text2:
        return 0.0

    # 简单按字符切分
    set1 = set(text1)
    set2 = set(text2)

    intersection = len(set1 & set2)
    union = len(set1 | set2)

    return intersection / union if union > 0 else 0.0


class RetrievalService:
    """
    统一检索服务

    支持三种模式：
    - vector: 仅向量检索
    - fulltext: 仅 BM25 全文检索
    - hybrid: 混合检索（向量 + BM25）
    """

    def __init__(self):
        self.vector_store = VectorStoreService()
        self.bm25_index = get_bm25_index()
        self.cache = RetrievalCache()

    async def _embed_query_for_kb(self, session: Session, kb_id: str, query: str) -> List[float]:
        kb = session.get(KnowledgeBase, kb_id)
        kb_model_id = (kb.embedding_model if kb else None) or ""

        if kb_model_id:
            custom = session.get(CustomModel, kb_model_id)
            if custom:
                embedding_service = EmbeddingService(
                    base_url=custom.base_url,
                    api_key=custom.api_key,
                    model=custom.model_name,
                )
                print(f"🔎 [Retrieval] KB={kb_id} 使用自定义Embedding: {custom.name} ({custom.model_name})")
                return await embedding_service.embed_query(query, model_id=custom.model_name)

            if kb_model_id == "sys_embedding":
                embedding_service = EmbeddingService()
                print(f"🔎 [Retrieval] KB={kb_id} 使用系统Embedding: {settings.EMBEDDING_MODEL}")
                return await embedding_service.embed_query(query)

        embedding_service = EmbeddingService()
        print(f"⚠️ [Retrieval] KB={kb_id} 未配置embedding_model，回退系统默认: {settings.EMBEDDING_MODEL}")
        return await embedding_service.embed_query(query)

    async def search(
        self,
        session: Session,
        query: str,
        kb_ids: List[str],
        search_mode: Literal["vector", "fulltext", "hybrid"] = "hybrid",
        top_k: int = None,
        score_threshold: float = 0.0,
        vector_weight: float = None,
        bm25_weight: float = None,
        use_cache: bool = True,
        log_retrieval: bool = True,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        执行检索

        Args:
            session: 数据库会话
            query: 查询语句
            kb_ids: 知识库 ID 列表
            search_mode: 检索模式
            top_k: 返回数量
            score_threshold: 分数阈值
            vector_weight: 向量权重（hybrid 模式）
            bm25_weight: BM25 权重（hybrid 模式）
            use_cache: 是否使用缓存
            log_retrieval: 是否记录检索日志

        Returns:
            (检索结果列表, 检索统计信息)
        """
        top_k = top_k or settings.RETRIEVAL_TOP_K
        vector_weight = vector_weight if vector_weight is not None else settings.RETRIEVAL_VECTOR_WEIGHT
        bm25_weight = bm25_weight if bm25_weight is not None else settings.RETRIEVAL_BM25_WEIGHT

        start_time = time.time()
        metrics = {
            "vector_count": 0,
            "bm25_count": 0,
            "merged_count": 0,
            "vector_latency_ms": 0,
            "bm25_latency_ms": 0,
        }

        # 尝试从缓存获取
        if use_cache:
            cached = self.cache.get(query, kb_ids, search_mode, top_k)
            if cached:
                print(f"✅ [Retrieval] 命中缓存")
                return cached, {"from_cache": True}

        results = []
        fallback_used = False

        try:
            if search_mode == "vector":
                results, metrics = await self._vector_search(
                    session, query, kb_ids, top_k, score_threshold
                )
            elif search_mode == "fulltext":
                results, metrics = await self._bm25_search(
                    session, query, kb_ids, top_k
                )
            elif search_mode == "hybrid":
                results, metrics, fallback_used = await self._hybrid_search(
                    session, query, kb_ids, top_k, score_threshold,
                    vector_weight, bm25_weight
                )
            else:
                raise ValueError(f"未知的检索模式: {search_mode}")

        except Exception as e:
            print(f"❌ [Retrieval] 检索失败: {e}")
            # 降级处理：尝试其他模式
            if search_mode == "hybrid":
                try:
                    print(f"⚠️ [Retrieval] 降级为向量检索")
                    results, metrics = await self._vector_search(
                        session, query, kb_ids, top_k, score_threshold
                    )
                    fallback_used = True
                except Exception:
                    results = []

        # 去重
        results = deduplicate_results(results)

        # 计算总耗时
        total_latency = (time.time() - start_time) * 1000
        metrics["total_latency_ms"] = total_latency
        metrics["final_count"] = len(results)
        metrics["fallback_used"] = fallback_used

        # 缓存结果
        if use_cache and results:
            self.cache.set(query, kb_ids, search_mode, top_k, results)

        # 记录检索日志
        if log_retrieval:
            self._log_retrieval(
                session, query, kb_ids, search_mode, top_k, score_threshold,
                vector_weight, bm25_weight, metrics, results
            )

        return results, metrics

    async def _vector_search(
        self,
        session: Session,
        query: str,
        kb_ids: List[str],
        top_k: int,
        score_threshold: float
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """向量检索"""
        start_time = time.time()

        all_results = []
        for kb_id in kb_ids:
            query_vector = await self._embed_query_for_kb(session, kb_id, query)
            results = self.vector_store.query(
                kb_id=kb_id,
                query_vector=query_vector,
                top_k=top_k,
                score_threshold=score_threshold
            )

            # 补充知识库信息
            kb = session.get(KnowledgeBase, kb_id)
            kb_name = kb.name if kb else "未知"

            for r in results:
                r["kb_id"] = kb_id
                r["kb_name"] = kb_name
                all_results.append(r)

        # 补充 chunk 详情
        all_results = self._enrich_results(session, all_results)

        # 按分数排序
        all_results.sort(key=lambda x: x.get("score", 0), reverse=True)
        all_results = all_results[:top_k]

        latency = (time.time() - start_time) * 1000
        metrics = {
            "vector_count": len(all_results),
            "bm25_count": 0,
            "merged_count": len(all_results),
            "vector_latency_ms": latency,
            "bm25_latency_ms": 0,
        }

        return all_results, metrics

    async def _bm25_search(
        self,
        session: Session,
        query: str,
        kb_ids: List[str],
        top_k: int
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """BM25 全文检索"""
        start_time = time.time()

        all_results = []
        for kb_id in kb_ids:
            results = self.bm25_index.search(
                session=session,
                query=query,
                kb_id=kb_id,
                top_k=top_k
            )

            # 补充知识库信息
            kb = session.get(KnowledgeBase, kb_id)
            kb_name = kb.name if kb else "未知"

            for r in results:
                r["id"] = r["chunk_id"]
                r["score"] = r["bm25_score"]
                r["kb_id"] = kb_id
                r["kb_name"] = kb_name
                all_results.append(r)

        # 补充 chunk 详情
        all_results = self._enrich_results(session, all_results)

        # 按分数排序
        all_results.sort(key=lambda x: x.get("score", 0), reverse=True)
        all_results = all_results[:top_k]

        latency = (time.time() - start_time) * 1000
        metrics = {
            "vector_count": 0,
            "bm25_count": len(all_results),
            "merged_count": len(all_results),
            "vector_latency_ms": 0,
            "bm25_latency_ms": latency,
        }

        return all_results, metrics

    async def _hybrid_search(
        self,
        session: Session,
        query: str,
        kb_ids: List[str],
        top_k: int,
        score_threshold: float,
        vector_weight: float,
        bm25_weight: float
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any], bool]:
        """
        混合检索

        策略：
        1. 并行执行向量检索和 BM25 检索
        2. 分数归一化
        3. 加权融合
        4. 按融合分数排序
        """
        fallback_used = False

        # 1. 向量检索
        vector_start = time.time()
        vector_results = []
        try:
            for kb_id in kb_ids:
                query_vector = await self._embed_query_for_kb(session, kb_id, query)
                results = self.vector_store.query(
                    kb_id=kb_id,
                    query_vector=query_vector,
                    top_k=150,  # 多召回一些用于融合
                    score_threshold=score_threshold
                )
                for r in results:
                    r["kb_id"] = kb_id
                    vector_results.append(r)
        except Exception as e:
            print(f"⚠️ [Retrieval] 向量检索失败: {e}")
            fallback_used = True

        vector_latency = (time.time() - vector_start) * 1000

        # 2. BM25 检索
        bm25_start = time.time()
        bm25_results = []
        try:
            for kb_id in kb_ids:
                results = self.bm25_index.search(
                    session=session,
                    query=query,
                    kb_id=kb_id,
                    top_k=settings.BM25_TOP_K
                )
                for r in results:
                    r["kb_id"] = kb_id
                    bm25_results.append(r)
        except Exception as e:
            print(f"⚠️ [Retrieval] BM25 检索失败: {e}")
            fallback_used = True

        bm25_latency = (time.time() - bm25_start) * 1000

        # 如果两路都失败，抛出异常
        if not vector_results and not bm25_results:
            raise Exception("向量检索和 BM25 检索均失败")

        # 3. 分数归一化
        vector_scores = normalize_scores({r["id"]: r["score"] for r in vector_results})
        bm25_scores = normalize_scores({r["chunk_id"]: r["bm25_score"] for r in bm25_results})

        # 4. 融合分数
        all_chunk_ids = set(vector_scores.keys()) | set(bm25_scores.keys())
        fused_scores = {}

        for chunk_id in all_chunk_ids:
            v_score = vector_scores.get(chunk_id, 0)
            b_score = bm25_scores.get(chunk_id, 0)
            fused_scores[chunk_id] = vector_weight * v_score + bm25_weight * b_score

        # 5. 排序并取 top_k
        sorted_chunks = sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

        # 6. 获取完整信息
        # 合并原始结果用于获取 metadata
        all_raw_results = {r["id"]: r for r in vector_results}
        all_raw_results.update({r["chunk_id"]: r for r in bm25_results})

        merged_results = []
        for chunk_id, fused_score in sorted_chunks:
            raw = all_raw_results.get(chunk_id, {})
            merged_results.append({
                "id": chunk_id,
                "score": fused_score,
                "vector_score": vector_scores.get(chunk_id, 0),
                "bm25_score": bm25_scores.get(chunk_id, 0),
                "kb_id": raw.get("kb_id", ""),
            })

        # 补充 chunk 详情
        merged_results = self._enrich_results(session, merged_results)

        metrics = {
            "vector_count": len(vector_results),
            "bm25_count": len(bm25_results),
            "merged_count": len(merged_results),
            "vector_latency_ms": vector_latency,
            "bm25_latency_ms": bm25_latency,
        }

        return merged_results, metrics, fallback_used

    def _enrich_results(
        self,
        session: Session,
        results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        补充检索结果的详细信息

        从数据库获取：
        - chunk 内容
        - 文件名
        - 页码
        - 图片路径
        - 父子关系等
        """
        enriched = []

        for r in results:
            chunk_id = r.get("id") or r.get("chunk_id")
            if not chunk_id:
                continue

            # 获取 chunk 详情
            chunk = session.get(DocumentChunk, chunk_id)
            if not chunk:
                continue

            # 获取文件信息
            file_doc = session.get(FileDocument, chunk.file_id)
            file_name = file_doc.name if file_doc else "未知文件"

            # 获取知识库名称
            kb_id = r.get("kb_id") or (file_doc.kb_id if file_doc else "")
            kb = session.get(KnowledgeBase, kb_id) if kb_id else None
            kb_name = kb.name if kb else "未知知识库"

            # 构建位置信息
            loc_parts = []
            if chunk.page_number:
                loc_parts.append(f"第 {chunk.page_number} 页")
            if chunk.heading_text:
                loc_parts.append(f"章节: {chunk.heading_text}")
            
            location_info = " | ".join(loc_parts) if loc_parts else "未知位置"

            enriched.append({
                "id": chunk_id,
                "content": chunk.content,
                "score": r.get("score", 0),
                "location_info": location_info,
                "vector_score": r.get("vector_score"),
                "bm25_score": r.get("bm25_score"),
                "kb_id": kb_id,
                "kb_name": kb_name,
                "file_id": chunk.file_id,
                "file_name": file_name,
                "page_number": chunk.page_number,
                "content_type": chunk.content_type,
                "image_path": chunk.image_path,
                "original_index": chunk.original_index,
                # 新增字段
                "parent_id": chunk.parent_id,
                "heading_level": chunk.heading_level,
                "heading_text": chunk.heading_text,
                "chunk_type": chunk.chunk_type,
                "token_count": getattr(chunk, "token_count", 0),
            })

        return enriched

    def _log_retrieval(
        self,
        session: Session,
        query: str,
        kb_ids: List[str],
        search_mode: str,
        top_k: int,
        score_threshold: float,
        vector_weight: float,
        bm25_weight: float,
        metrics: Dict[str, Any],
        results: List[Dict[str, Any]]
    ) -> None:
        """记录检索日志"""
        try:
            # 生成结果摘要（前 3 条）
            summary = [
                {
                    "content": r.get("content", "")[:100],
                    "score": r.get("score", 0),
                    "file_name": r.get("file_name", ""),
                }
                for r in results[:3]
            ]

            log = RetrievalLog(
                id=generate_uuid(),
                kb_id=",".join(kb_ids),
                query=query[:500],  # 截断长查询
                search_mode=search_mode,
                vector_count=metrics.get("vector_count", 0),
                bm25_count=metrics.get("bm25_count", 0),
                merged_count=metrics.get("merged_count", 0),
                rerank_count=0,  # 后续 rerank 会更新
                final_count=metrics.get("final_count", 0),
                latency_ms=metrics.get("total_latency_ms", 0),
                vector_latency_ms=metrics.get("vector_latency_ms", 0),
                bm25_latency_ms=metrics.get("bm25_latency_ms", 0),
                rerank_latency_ms=0,
                top_k=top_k,
                score_threshold=score_threshold,
                rerank_enabled=False,
                vector_weight=vector_weight,
                bm25_weight=bm25_weight,
                results_summary=json.dumps(summary, ensure_ascii=False),
                created_at=datetime.utcnow()
            )

            session.add(log)
            session.commit()

        except Exception as e:
            print(f"⚠️ [Retrieval] 记录日志失败: {e}")


# 单例
_retrieval_service = None


def get_retrieval_service() -> RetrievalService:
    """获取检索服务单例"""
    global _retrieval_service
    if _retrieval_service is None:
        _retrieval_service = RetrievalService()
    return _retrieval_service
