"""
向量存储服务 (ChromaDB)
"""

import chromadb
from chromadb.config import Settings as ChromaSettings
from typing import List, Dict, Any, Optional

from app.core.config import get_settings

settings = get_settings()


class VectorStoreService:
    """向量存储服务"""

    _client = None

    @classmethod
    def get_client(cls) -> chromadb.Client:
        """获取 ChromaDB 客户端单例"""
        if cls._client is None:
            cls._client = chromadb.PersistentClient(
                path=settings.CHROMA_DB_DIR,
                settings=ChromaSettings(
                    anonymized_telemetry=False,
                    allow_reset=True,
                ),
            )
        return cls._client

    def __init__(self):
        self.client = self.get_client()

    def create_collection(self, kb_id: str) -> None:
        """创建知识库对应的 Collection"""
        collection_name = self._get_collection_name(kb_id)
        self.client.get_or_create_collection(
            name=collection_name,
            metadata={"kb_id": kb_id},
        )

    def delete_collection(self, kb_id: str) -> None:
        """删除知识库对应的 Collection"""
        collection_name = self._get_collection_name(kb_id)
        try:
            self.client.delete_collection(name=collection_name)
        except Exception:
            pass  # Collection 不存在时忽略

    def add_documents(
        self,
        kb_id: str,
        documents: List[Dict[str, Any]],
        embeddings: List[List[float]],
    ) -> None:
        """
        添加文档到向量库

        Args:
            kb_id: 知识库 ID
            documents: 文档列表，每个文档包含 id, content, metadata
            embeddings: 对应的向量列表
        """
        collection_name = self._get_collection_name(kb_id)
        collection = self.client.get_or_create_collection(name=collection_name)

        ids = [doc["id"] for doc in documents]
        contents = [doc["content"] for doc in documents]
        metadatas = [doc.get("metadata", {}) for doc in documents]

        collection.add(
            ids=ids,
            documents=contents,
            embeddings=embeddings,
            metadatas=metadatas,
        )

    def query(
        self,
        kb_id: str,
        query_vector: List[float],
        top_k: int = 5,
        score_threshold: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """
        向量检索

        Args:
            kb_id: 知识库 ID
            query_vector: 查询向量
            top_k: 返回结果数量
            score_threshold: 相似度阈值

        Returns:
            检索结果列表
        """
        collection_name = self._get_collection_name(kb_id)

        try:
            collection = self.client.get_collection(name=collection_name)
        except Exception:
            return []

        results = collection.query(
            query_embeddings=[query_vector],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )

        # 处理结果
        output = []
        if results and results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                # ChromaDB 返回的是距离，转换为相似度
                distance = results["distances"][0][i] if results["distances"] else 0
                # 使用余弦距离转相似度: similarity = 1 - distance/2
                score = 1 - distance / 2

                if score >= score_threshold:
                    output.append({
                        "id": doc_id,
                        "content": results["documents"][0][i] if results["documents"] else "",
                        "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                        "score": score,
                    })

        return output

    def delete_by_file_id(self, kb_id: str, file_id: str) -> None:
        """删除指定文件的所有向量"""
        collection_name = self._get_collection_name(kb_id)

        try:
            collection = self.client.get_collection(name=collection_name)
            # 根据 metadata 中的 file_id 删除
            collection.delete(where={"file_id": file_id})
        except Exception:
            pass

    def get_count(self, kb_id: str) -> int:
        """获取知识库中的向量数量"""
        collection_name = self._get_collection_name(kb_id)

        try:
            collection = self.client.get_collection(name=collection_name)
            return collection.count()
        except Exception:
            return 0

    @staticmethod
    def _get_collection_name(kb_id: str) -> str:
        """生成 Collection 名称"""
        # ChromaDB collection name 有限制，需要处理
        return f"kb_{kb_id.replace('-', '_')}"
