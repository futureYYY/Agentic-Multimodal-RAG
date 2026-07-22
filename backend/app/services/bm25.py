"""
BM25 索引和检索服务

实现功能：
- 中英文混合分词
- BM25 倒排索引构建
- BM25 检索
- 索引增量更新
"""

import re
import json
import math
from collections import Counter
from typing import List, Dict, Any, Optional, Set
from datetime import datetime

from sqlmodel import Session, select, delete

from app.models import ChunkIndex, IndexStats, DocumentChunk, FileDocument, generate_uuid
from app.core.config import get_settings

settings = get_settings()

# 中文停用词（常见）
STOP_WORDS_CN = {
    '的', '了', '是', '在', '我', '有', '和', '就', '不', '人', '都', '一', '一个',
    '上', '也', '很', '到', '说', '要', '去', '你', '会', '着', '没有', '看', '好',
    '自己', '这', '那', '里', '请', '他', '她', '它', '们', '能', '对', '被', '与',
    '从', '但', '为', '而', '或', '如', '将', '把', '让', '给', '中', '等', '及',
    '其', '之', '以', '可', '如果', '因为', '所以', '这样', '这个', '那个', '什么',
    '怎么', '为什么', '哪里', '哪个', '谁', '如何', '多少', '还', '再', '又', '才',
    '应该', '可以', '需要', '已经', '正在', '这些', '那些', '每', '各', '某',
}

# 英文停用词（常见）
STOP_WORDS_EN = {
    'a', 'an', 'the', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of',
    'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were', 'been', 'be', 'have',
    'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could', 'should', 'may',
    'might', 'must', 'can', 'this', 'that', 'these', 'those', 'i', 'you', 'he',
    'she', 'it', 'we', 'they', 'what', 'which', 'who', 'when', 'where', 'why',
    'how', 'all', 'each', 'every', 'both', 'few', 'more', 'most', 'other', 'some',
    'such', 'no', 'nor', 'not', 'only', 'own', 'same', 'so', 'than', 'too', 'very',
}

STOP_WORDS = STOP_WORDS_CN | STOP_WORDS_EN


def tokenize(text: str) -> List[str]:
    """
    中英文混合分词

    策略：
    1. 英文按空格和标点分词，转小写
    2. 中文使用 jieba 分词
    3. 过滤停用词和单字符
    """
    if not text:
        return []

    # 尝试导入 jieba，如果没有则使用简单分词
    try:
        import jieba
        use_jieba = True
    except ImportError:
        use_jieba = False
        print("⚠️ [BM25] jieba 未安装，使用简单分词")

    # 1. 提取英文单词（包括数字和下划线）
    english_pattern = r'[a-zA-Z0-9_]+'
    english_tokens = [t.lower() for t in re.findall(english_pattern, text)]

    # 2. 处理中文
    # 移除英文部分，保留中文和标点
    chinese_text = re.sub(english_pattern, ' ', text)

    if use_jieba:
        # 使用 jieba 分词
        chinese_tokens = list(jieba.cut(chinese_text))
    else:
        # 简单按字切分中文（降级方案）
        chinese_tokens = list(chinese_text)

    # 3. 清理和过滤
    all_tokens = []
    for t in english_tokens + chinese_tokens:
        t = t.strip()
        # 过滤条件：
        # - 非空
        # - 长度 > 1（过滤单字符，但保留中文双字词）
        # - 不是纯标点
        # - 不是停用词
        if t and len(t) > 1 and not re.match(r'^[\s\W]+$', t) and t.lower() not in STOP_WORDS:
            all_tokens.append(t.lower())

    return all_tokens


class BM25Index:
    """
    BM25 倒排索引

    支持：
    - 索引构建
    - 索引查询
    - 增量更新
    - 索引删除
    """

    def __init__(self, k1: float = None, b: float = None):
        """
        初始化 BM25 索引

        Args:
            k1: 词频饱和参数 (默认 1.5)
            b: 文档长度归一化参数 (默认 0.75)
        """
        self.k1 = k1 or settings.BM25_K1
        self.b = b or settings.BM25_B

    def index_chunk(
        self,
        session: Session,
        chunk_id: str,
        content: str,
        kb_id: str
    ) -> None:
        """
        为单个 chunk 建立索引

        Args:
            session: 数据库会话
            chunk_id: chunk ID
            content: chunk 内容
            kb_id: 知识库 ID
        """
        # 分词
        tokens = tokenize(content)
        if not tokens:
            return

        # 计算词频
        term_freq = dict(Counter(tokens))
        doc_length = len(tokens)

        # 检查是否已存在索引
        existing = session.exec(
            select(ChunkIndex).where(ChunkIndex.chunk_id == chunk_id)
        ).first()

        if existing:
            # 更新现有索引
            old_term_freq = json.loads(existing.terms)
            old_doc_length = existing.doc_length

            # 先从统计中减去旧值
            self._decrement_stats(session, kb_id, old_term_freq, old_doc_length)

            # 更新索引
            existing.terms = json.dumps(term_freq, ensure_ascii=False)
            existing.doc_length = doc_length
            session.add(existing)
        else:
            # 创建新索引
            chunk_index = ChunkIndex(
                id=generate_uuid(),
                chunk_id=chunk_id,
                kb_id=kb_id,
                terms=json.dumps(term_freq, ensure_ascii=False),
                doc_length=doc_length
            )
            session.add(chunk_index)

        # 更新全局统计
        self._increment_stats(session, kb_id, term_freq, doc_length)

    def delete_chunk_index(
        self,
        session: Session,
        chunk_id: str,
        kb_id: str
    ) -> None:
        """
        删除单个 chunk 的索引

        Args:
            session: 数据库会话
            chunk_id: chunk ID
            kb_id: 知识库 ID
        """
        # 获取要删除的索引
        chunk_index = session.exec(
            select(ChunkIndex).where(ChunkIndex.chunk_id == chunk_id)
        ).first()

        if not chunk_index:
            return

        # 从统计中减去
        term_freq = json.loads(chunk_index.terms)
        self._decrement_stats(session, kb_id, term_freq, chunk_index.doc_length)

        # 删除索引
        session.delete(chunk_index)

    def delete_file_index(
        self,
        session: Session,
        file_id: str,
        kb_id: str
    ) -> None:
        """
        删除整个文件的所有 chunk 索引

        Args:
            session: 数据库会话
            file_id: 文件 ID
            kb_id: 知识库 ID
        """
        # 获取文件的所有 chunk ID
        chunks = session.exec(
            select(DocumentChunk.id).where(DocumentChunk.file_id == file_id)
        ).all()

        for chunk_id in chunks:
            self.delete_chunk_index(session, chunk_id, kb_id)

    def rebuild_kb_index(
        self,
        session: Session,
        kb_id: str
    ) -> int:
        """
        重建整个知识库的 BM25 索引

        Args:
            session: 数据库会话
            kb_id: 知识库 ID

        Returns:
            索引的 chunk 数量
        """
        print(f"🔄 [BM25] 开始重建知识库 {kb_id} 的索引...")

        # 1. 删除旧索引
        session.exec(delete(ChunkIndex).where(ChunkIndex.kb_id == kb_id))
        session.exec(delete(IndexStats).where(IndexStats.kb_id == kb_id))
        session.commit()

        # 2. 获取所有 chunks
        chunks = session.exec(
            select(DocumentChunk)
            .join(FileDocument)
            .where(FileDocument.kb_id == kb_id)
        ).all()

        # 3. 重建索引
        count = 0
        for chunk in chunks:
            self.index_chunk(session, chunk.id, chunk.content, kb_id)
            count += 1

        session.commit()
        print(f"✅ [BM25] 知识库 {kb_id} 索引重建完成，共 {count} 个 chunks")

        return count

    def search(
        self,
        session: Session,
        query: str,
        kb_id: str,
        top_k: int = None
    ) -> List[Dict[str, Any]]:
        """
        BM25 检索

        Args:
            session: 数据库会话
            query: 查询语句
            kb_id: 知识库 ID
            top_k: 返回结果数量

        Returns:
            检索结果列表 [{"chunk_id": "...", "bm25_score": 0.xx}, ...]
        """
        top_k = top_k or settings.BM25_TOP_K

        # 1. 查询分词
        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        # 2. 获取统计信息
        stats = session.get(IndexStats, kb_id)
        if not stats or stats.total_docs == 0:
            return []

        N = stats.total_docs
        avg_dl = stats.avg_doc_length
        term_doc_freq = json.loads(stats.term_doc_freq)

        # 3. 获取包含查询词的所有索引
        # 优化：只获取包含至少一个查询词的文档
        query_terms_set = set(query_tokens)

        # 获取该知识库的所有索引
        all_indices = session.exec(
            select(ChunkIndex).where(ChunkIndex.kb_id == kb_id)
        ).all()

        # 4. 计算每个文档的 BM25 分数
        scores = {}
        for idx in all_indices:
            doc_term_freq = json.loads(idx.terms)
            doc_length = idx.doc_length

            # 检查是否包含任何查询词
            if not any(t in doc_term_freq for t in query_terms_set):
                continue

            score = self._calculate_bm25(
                query_tokens, doc_term_freq, doc_length, N, avg_dl, term_doc_freq
            )

            if score > 0:
                scores[idx.chunk_id] = score

        # 5. 排序并返回 top_k
        sorted_results = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        return [
            {"chunk_id": chunk_id, "bm25_score": score}
            for chunk_id, score in sorted_results[:top_k]
        ]

    def _calculate_bm25(
        self,
        query_tokens: List[str],
        doc_term_freq: Dict[str, int],
        doc_length: int,
        N: int,
        avg_dl: float,
        term_doc_freq: Dict[str, int]
    ) -> float:
        """
        计算单个文档的 BM25 分数

        BM25 公式：
        score = Σ IDF(qi) * (tf(qi, D) * (k1 + 1)) / (tf(qi, D) + k1 * (1 - b + b * |D| / avgdl))

        其中：
        - IDF(qi) = log((N - df(qi) + 0.5) / (df(qi) + 0.5) + 1)
        - tf(qi, D) 是词 qi 在文档 D 中的频率
        - |D| 是文档长度
        - avgdl 是平均文档长度
        """
        score = 0.0

        for term in query_tokens:
            if term not in doc_term_freq:
                continue

            tf = doc_term_freq[term]
            df = term_doc_freq.get(term, 0)

            # IDF
            idf = math.log((N - df + 0.5) / (df + 0.5) + 1)

            # TF normalization
            tf_norm = (tf * (self.k1 + 1)) / (
                tf + self.k1 * (1 - self.b + self.b * doc_length / avg_dl)
            )

            score += idf * tf_norm

        return score

    def _increment_stats(
        self,
        session: Session,
        kb_id: str,
        term_freq: Dict[str, int],
        doc_length: int
    ) -> None:
        """增加统计信息"""
        stats = session.get(IndexStats, kb_id)

        if not stats:
            # 创建新的统计记录
            stats = IndexStats(
                kb_id=kb_id,
                total_docs=1,
                avg_doc_length=float(doc_length),
                term_doc_freq=json.dumps({t: 1 for t in term_freq.keys()}, ensure_ascii=False),
                updated_at=datetime.utcnow()
            )
            session.add(stats)
        else:
            # 更新统计
            old_total = stats.total_docs
            new_total = old_total + 1

            # 更新平均文档长度
            old_avg = stats.avg_doc_length
            new_avg = (old_avg * old_total + doc_length) / new_total
            stats.avg_doc_length = new_avg
            stats.total_docs = new_total

            # 更新词文档频率
            old_term_doc_freq = json.loads(stats.term_doc_freq)
            for term in term_freq.keys():
                old_term_doc_freq[term] = old_term_doc_freq.get(term, 0) + 1

            stats.term_doc_freq = json.dumps(old_term_doc_freq, ensure_ascii=False)
            stats.updated_at = datetime.utcnow()

            session.add(stats)

    def _decrement_stats(
        self,
        session: Session,
        kb_id: str,
        term_freq: Dict[str, int],
        doc_length: int
    ) -> None:
        """减少统计信息"""
        stats = session.get(IndexStats, kb_id)

        if not stats or stats.total_docs <= 0:
            return

        old_total = stats.total_docs
        new_total = max(old_total - 1, 0)

        if new_total == 0:
            # 如果没有文档了，重置统计
            stats.total_docs = 0
            stats.avg_doc_length = 0.0
            stats.term_doc_freq = "{}"
        else:
            # 更新平均文档长度
            old_avg = stats.avg_doc_length
            # 避免除零
            if old_total > 1:
                new_avg = (old_avg * old_total - doc_length) / new_total
            else:
                new_avg = 0.0
            stats.avg_doc_length = max(new_avg, 0.0)
            stats.total_docs = new_total

            # 更新词文档频率
            old_term_doc_freq = json.loads(stats.term_doc_freq)
            for term in term_freq.keys():
                if term in old_term_doc_freq:
                    old_term_doc_freq[term] = max(old_term_doc_freq[term] - 1, 0)
                    if old_term_doc_freq[term] == 0:
                        del old_term_doc_freq[term]

            stats.term_doc_freq = json.dumps(old_term_doc_freq, ensure_ascii=False)

        stats.updated_at = datetime.utcnow()
        session.add(stats)


# 单例实例
_bm25_index = None


def get_bm25_index() -> BM25Index:
    """获取 BM25 索引单例"""
    global _bm25_index
    if _bm25_index is None:
        _bm25_index = BM25Index()
    return _bm25_index
