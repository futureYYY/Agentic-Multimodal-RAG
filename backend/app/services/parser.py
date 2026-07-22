"""
文件解析服务
支持 PDF, Word, Excel, CSV, TXT 格式
"""

import os
import fitz  # PyMuPDF
from typing import List, Optional, Any
from dataclasses import dataclass
from datetime import datetime
import pandas as pd
from docx import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.core.config import get_settings
from app.services.embedding import EmbeddingService
import asyncio

try:
    from langchain_experimental.text_splitter import SemanticChunker
    from langchain_community.embeddings import OllamaEmbeddings
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    HAS_LANGCHAIN = True
except ImportError:
    HAS_LANGCHAIN = False

settings = get_settings()

def run_async(coro):
    """在同步环境中运行异步函数"""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    if loop.is_running():
        # 如果已经在循环中 (虽然 parser 通常在 celery 线程，不应在 loop 中)，
        # 但为了防止意外，这里做一个简单的 fallback 或者报错
        # 对于 Celery worker，通常是没有运行 loop 的
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(lambda: asyncio.run(coro)).result()
    else:
        return loop.run_until_complete(coro)

class EmbeddingServiceAdapter:
    """LangChain Embedding 适配器 (使用 EmbeddingService)"""
    def __init__(self, base_url: str = None, api_key: str = None, model: str = None):
        self.service = EmbeddingService(base_url=base_url, api_key=api_key, model=model)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        # 同步调用异步方法
        return run_async(self.service.embed_documents(texts, model_id=self.service.model))

    def embed_query(self, text: str) -> List[float]:
        # 同步调用异步方法
        return run_async(self.service.embed_query(text, model_id=self.service.model))


@dataclass
class ParsedChunk:
    """解析后的块"""
    content: str
    page_number: Optional[int]
    content_type: str  # text, table, image
    image_path: Optional[str] = None
    metadata: Optional[dict] = None
    token_count: int = 0


class TextSplitter:
    """
    文本切分器

    优化：语义完整性切分
    - 按字数初步分块
    - 检查块结尾是否是完整语义（完整句子）
    - 如果不完整，向后扩展到最近的句子边界
    - 允许块大小超出 chunk_size 最多 20%
    """

    # 语义边界定义（优先级从高到低）
    SENTENCE_BOUNDARIES_CN = ['。', '？', '！', '；']
    SENTENCE_BOUNDARIES_EN = ['. ', '? ', '! ', '; ']
    PARAGRAPH_BOUNDARY = '\n\n'

    def __init__(
        self,
        chunk_size: int = None,
        chunk_overlap: int = None,
        separator: str = "\n\n",
        max_overflow_ratio: float = 0.2,  # 允许超出的最大比例
    ):
        self.chunk_size = chunk_size or settings.CHUNK_SIZE
        self.chunk_overlap = chunk_overlap or settings.CHUNK_OVERLAP
        self.separator = separator.replace("\\n", "\n") if separator else "\n\n"
        self.max_overflow_ratio = max_overflow_ratio
        self.max_chunk_size = int(self.chunk_size * (1 + max_overflow_ratio))
        
        # 初始化 LangChain 的 Token Splitter (如果可用)
        self.langchain_splitter = None
        if HAS_LANGCHAIN:
            try:
                # 使用 tiktoken 计算长度，但仍按字符递归切分以保持语义
                # 指定 cl100k_base (GPT-4/Embedding) 编码器，对中文支持更好且更通用
                # 注意：Qwen 和 DeepSeek 的词表对中文压缩率更高（Token 数更少）
                # 使用 cl100k_base 估算长度是安全的（Safe Upper Bound），不会导致上下文溢出
                self.langchain_splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
                    encoding_name="cl100k_base",
                    chunk_size=self.chunk_size,
                    chunk_overlap=self.chunk_overlap,
                    separators=[
                        "\n\n",
                        "\n",
                        "。", "！", "？", "；", # 中文标点
                        ". ", "! ", "? ", "; ", # 英文标点
                        " ",
                        ""
                    ]
                )
                print(f"🔧 [TextSplitter] 初始化 LangChain Token-based Splitter (chunk_size={self.chunk_size} tokens, 编码器=cl100k_base, 兼容 GPT-4/Qwen/DeepSeek)", flush=True)
            except Exception as e:
                print(f"⚠️ [TextSplitter] 初始化 LangChain Splitter 失败: {e}，回退到基于字符的切分", flush=True)

    def _find_sentence_boundary(self, text: str, start: int, end: int, forward: bool = True) -> int:

        """
        在指定范围内查找句子边界

        Args:
            text: 文本
            start: 搜索起始位置
            end: 搜索结束位置
            forward: True 向后查找，False 向前查找

        Returns:
            找到的边界位置（包含边界符号），如果没找到返回 -1
        """
        if start >= end:
            return -1
    
        search_text = text[start:end]

        # 所有可能的边界符号
        boundaries = [self.PARAGRAPH_BOUNDARY] + self.SENTENCE_BOUNDARIES_CN + self.SENTENCE_BOUNDARIES_EN

        if forward:
            # 向后查找：找最近的边界
            best_pos = -1
            for boundary in boundaries:
                pos = search_text.find(boundary)
                if pos != -1:
                    actual_pos = start + pos + len(boundary)
                    if best_pos == -1 or actual_pos < best_pos:
                        best_pos = actual_pos
            return best_pos
        else:
            # 向前查找：找最后一个边界
            best_pos = -1
            for boundary in boundaries:
                pos = search_text.rfind(boundary)
                if pos != -1:
                    actual_pos = start + pos + len(boundary)
                    if actual_pos > best_pos:
                        best_pos = actual_pos
            return best_pos

    def split(self, text: str) -> List[str]:
        """
        切分文本
        
        如果 HAS_LANGCHAIN 为 True，则优先使用基于 Token 的 RecursiveCharacterTextSplitter。
        否则使用基于字符的语义完整性切分。
        """
        if not text:
            return []

        # 优先使用 LangChain 的 Token Splitter
        if self.langchain_splitter:
            try:
                # RecursiveCharacterTextSplitter.split_text 返回 List[str]
                return self.langchain_splitter.split_text(text)
            except Exception as e:
                print(f"❌ [TextSplitter] LangChain 切分失败: {e}，回退到规则切分", flush=True)

        # 回退到基于字符的切分逻辑
        """
        算法流程：
        1. 按 chunk_size 初步定位切分点 end
        2. 在 [end, end + chunk_size * 0.2] 范围内查找最近的句子边界（向后扩展）
        3. 如果找到，扩展到该边界
        4. 如果找不到，在 [end - chunk_size * 0.3, end] 范围内向前查找
        5. 仍找不到则强制切分
        """
        if not text:
            return []

        text = text.strip()
        if len(text) <= self.chunk_size:
            return [text]

        chunks = []
        start = 0
        text_len = len(text)

        while start < text_len:
            # 如果剩余长度小于块大小，直接作为一个块
            remaining = text_len - start
            if remaining <= self.chunk_size:
                chunk = text[start:].strip()
                if chunk:
                    chunks.append(chunk)
                break

            # 初步切分点
            end = start + self.chunk_size
            best_end = end

            # 步骤 2: 向后扩展查找边界 [end, end + chunk_size * 0.2]
            # 注意：如果 max_overflow_ratio 为 0（例如二次切分模式），则 forward_search_end = end，不会进行向后查找
            forward_search_end = min(end + int(self.chunk_size * self.max_overflow_ratio), text_len)
            
            forward_boundary = -1
            if forward_search_end > end:
                 forward_boundary = self._find_sentence_boundary(text, end, forward_search_end, forward=True)

            if forward_boundary != -1:
                best_end = forward_boundary
            else:
                # 步骤 4: 向前查找边界 [end - chunk_size * 0.3, end]
                backward_search_start = max(start + int(self.chunk_size * 0.5), start)
                backward_boundary = self._find_sentence_boundary(text, backward_search_start, end, forward=False)

                if backward_boundary != -1 and backward_boundary > start:
                    best_end = backward_boundary
                # 否则强制切分（保持 best_end = end）

            # 提取 chunk
            chunk = text[start:best_end].strip()
            if chunk:
                chunks.append(chunk)

            # 下一块的起始位置，考虑重叠
            next_start = best_end - self.chunk_overlap

            # 防止死循环：确保至少前进一步
            if next_start <= start:
                next_start = best_end

            start = next_start

        return chunks


@dataclass
class HeadingInfo:
    """标题信息"""
    level: int  # 1, 2, 3
    text: str   # 标题文本
    start_pos: int  # 在原文中的起始位置
    end_pos: int    # 在原文中的结束位置（标题行结束）


@dataclass
class StructuredChunk:
    """结构化分块结果"""
    content: str
    heading_level: Optional[int]
    heading_text: Optional[str]
    heading_path: List[str]  # 标题层级路径
    parent_id: Optional[str]  # 父 chunk ID
    chunk_type: str  # "heading" | "content"
    token_count: int = 0


class StructuredSplitter:
    """
    结构化分块器

    按文档标题层级拆分，支持：
    - PDF（通过字体大小识别）
    - Word（Heading 样式）
    - TXT（Markdown # 语法）
    """

    def __init__(
        self,
        heading_level: int = 2,  # 按哪个级别拆分
        chunk_size: int = 500,
        chunk_overlap: int = 50, # 新增：支持自定义重叠
        max_overflow_ratio: float = 0.5,  # 结构化模式允许更大的溢出
        use_semantic: bool = False,
        embedding_model: str = None,
        embedding_base_url: str = None,
        embedding_api_key: str = None, # 新增：API Key
        embedding_max_tokens: int = 8192,
        semantic_max_tokens: int = None, # 新增：语义模型最大上下文
    ):
        self.heading_level = heading_level
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.max_chunk_size = int(chunk_size * (1 + max_overflow_ratio))
        self.use_semantic = use_semantic
        self.embedding_model = embedding_model
        self.embedding_base_url = embedding_base_url
        self.embedding_api_key = embedding_api_key
        self.embedding_max_tokens = embedding_max_tokens
        # 如果未指定语义模型限制，默认与向量模型一致，或者给一个安全值
        self.semantic_max_tokens = semantic_max_tokens or embedding_max_tokens
        self.text_splitter = TextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        
        self.semantic_splitter = None
        if self.use_semantic and HAS_LANGCHAIN:
            self._init_semantic_splitter()

    def _init_semantic_splitter(self):
        """初始化语义切分器"""
        # 优先使用传入的 base_url，否则使用全局配置
        raw_base_url = self.embedding_base_url or settings.EMBEDDING_BASE_URL
        
        # 清洗 base_url (EmbeddingService 内部也会处理，但为了 log 清晰)
        base_url = raw_base_url.rstrip("/")
        
        # 优先使用传入的模型，否则使用全局默认配置
        model_name = self.embedding_model or settings.EMBEDDING_MODEL
        api_key = self.embedding_api_key or settings.EMBEDDING_API_KEY
        
        print(f"🔧 [StructuredSplitter] Initializing EmbeddingServiceAdapter with base_url: {base_url}, model: {model_name}, has_key: {bool(api_key)}")
        
        # 使用适配器替代 OllamaEmbeddings
        embeddings = EmbeddingServiceAdapter(
            base_url=base_url,
            api_key=api_key,
            model=model_name
        )
        
        self.semantic_splitter = SemanticChunker(
            embeddings=embeddings,
            breakpoint_threshold_type="percentile",
            breakpoint_threshold_amount=95,
            buffer_size=1,
            min_chunk_size=100,  # 避免过小的碎片
            sentence_split_regex=r"(?<=[。？！\n])"  # 支持中文切分
        )

    def _count_tokens(self, text: str) -> int:
        """计算文本 Token 数"""
        if not text:
            return 0
        if self.text_splitter.langchain_splitter:
            try:
                if hasattr(self.text_splitter.langchain_splitter, '_length_function'):
                    return self.text_splitter.langchain_splitter._length_function(text)
                elif hasattr(self.text_splitter.langchain_splitter, '_tokenizer'):
                    return len(self.text_splitter.langchain_splitter._tokenizer.encode(text))
            except Exception:
                pass
        # 回退估算：中文约 1.5 char/token，英文约 4 char/token，保守起见用 1 char = 1 token (对于 context limit check)
        # 或者为了安全，用 len(text)
        return len(text)

    def _split_content(self, content: str, ignore_limit_check: bool = False) -> List[str]:
        """根据配置选择切分策略"""
        if not content.strip():
            return []
            
        # 1. 计算 Token 数量
        # 解释：这里必须计算 Token，原因如下：
        # (1) 无论是否使用语义切分，我们都需要确保最终生成的 Chunk 不会超过 向量模型(Embedding Model) 的限制。
        #     如果超过了，向量化时会报错。
        # (2) 如果使用语义切分，我们需要确保传给 语义模型 的文本片段不会超过它的上下文窗口。
        #     虽然我们不想"多此一举"，但为了程序的健壮性，防止 API 报错，这是必要的"安全检查"。
        token_count = self._count_tokens(content)
        
        # 2. 检查是否超过向量模型限制 (Vector Model Context)
        # 如果未超过限制，直接保留完整块（优先保证结构树）
        # 注意：如果 ignore_limit_check 为 True (如纯语义切分模式)，则跳过此检查，强制进行语义切分尝试
        if not ignore_limit_check and token_count <= self.embedding_max_tokens:
            return [content]

        if not ignore_limit_check:
            print(f"⚠️ [StructuredSplitter] 内容长度 {token_count} 超过向量模型限制 {self.embedding_max_tokens}，准备切分", flush=True)
        else:
            print(f"🧠 [StructuredSplitter] 强制语义切分模式 (Tokens: {token_count})", flush=True)

        # 3. 超过向量限制，判断使用语义切分还是固定切分
        # 逻辑优化 (响应用户建议)：
        # 不再预先估算并进行物理切分，而是直接将内容传给语义切分模型。
        # 理由：SemanticChunker 内部是按句子进行 Embedding 的，只要单句不超限，整体长文本通常也能处理。
        # 如果切分后的结果依然过大，再进行二次处理。
        
        print(f"🔍 [StructuredSplitter] Checking semantic split: use_semantic={self.use_semantic}, has_splitter={self.semantic_splitter is not None}")

        if self.use_semantic and self.semantic_splitter:
            print(f"🧠 [StructuredSplitter] 使用语义切分 (Content Tokens: {token_count})", flush=True)
            try:
                # SemanticChunker 返回的是 Document 对象列表
                docs = self.semantic_splitter.create_documents([content])
                chunks = [doc.page_content for doc in docs]
                print(f"🧠 [StructuredSplitter] 语义切分完成，获得 {len(chunks)} 个片段", flush=True)
                
                # 再次检查切分后的片段是否满足向量模型限制
                # 如果某个片段依然过大（虽然语义完整，但 Embedding 模型吃不下），需要二次切分
                final_chunks = []
                for chunk in chunks:
                    chunk_tokens = self._count_tokens(chunk)
                    if chunk_tokens > self.embedding_max_tokens:
                        print(f"⚠️ [StructuredSplitter] 语义片段依然过大 ({chunk_tokens} > {self.embedding_max_tokens})，进行固定二次切分", flush=True)
                        fixed_splitter = TextSplitter(
                            chunk_size=self.chunk_size,
                            chunk_overlap=self.chunk_overlap,
                            max_overflow_ratio=0
                        )
                        final_chunks.extend(fixed_splitter.split(chunk))
                    else:
                        final_chunks.append(chunk)
                return final_chunks
            except Exception as e:
                print(f"❌ [StructuredSplitter] 语义切分失败: {e}，回退到固定切分", flush=True)
                # Fallback to fixed split below
        
        # 4. 回退或默认使用固定切分 (TextSplitter)
        print(f"🔨 [StructuredSplitter] 使用固定切分 (Tokens: {token_count}, Chunk Size: {self.chunk_size})", flush=True)
        # 对应需求："对超出的部分...使用固定切分的方式如：500token切分一个chuck"

        # 这里使用初始化时配置的 chunk_size (通常是 500)
        return self.text_splitter.split(content)

    def split_by_headings(
        self,
        text: str,
        headings: List[HeadingInfo],
        generate_id_func
    ) -> List[StructuredChunk]:
        """
        根据提取的标题信息进行结构化分块

        Args:
            text: 完整文本
            headings: 标题信息列表
            generate_id_func: 生成 chunk ID 的函数

        Returns:
            结构化分块列表
        """
        if not headings:
            # 没有识别到标题，使用普通切分
            return self._fallback_split(text, generate_id_func)

        chunks = []
        heading_path = []  # 当前的标题层级路径
        parent_ids = {}    # 记录每个层级的父 ID
        accumulated_headings = [] # 暂存空内容的标题，用于合并到下一个块

        # 0. 处理前言/概述 (Preamble)
        # 如果第一个标题不在文件开头，说明前面有内容
        if headings and headings[0].start_pos > 0:
            preamble_content = text[:headings[0].start_pos].strip()
            if preamble_content:
                # 优化：对前言部分也进行切分检查 (支持语义切分)
                # 如果前言过长，会调用 _split_content 进行语义/固定切分
                preamble_sub_chunks = self._split_content(preamble_content)
                
                for idx, pc in enumerate(preamble_sub_chunks):
                    chunks.append(StructuredChunk(
                        content=pc,
                        heading_level=0, 
                        heading_text="前言",
                        heading_path=["前言"],
                        parent_id=None,
                        chunk_type="content",
                        token_count=self._count_tokens(pc)
                    ))

        for i, heading in enumerate(headings):
            # 1. 更新标题路径 (先更新路径，确保即使内容为空，路径也是正确的)
            while heading_path and len(heading_path) >= heading.level:
                heading_path.pop()
            heading_path.append(heading.text)
            
            # 构建完整的标题上下文 (用于拼接到每个 chunk 前面)
            # 例如: "1. Project Overview\n1.1 Project Background"
            full_heading_context = "\n".join(heading_path)

            # 2. 确定内容范围
            content_start = heading.end_pos
            if i + 1 < len(headings):
                content_end = headings[i + 1].start_pos
            else:
                content_end = len(text)

            content = text[content_start:content_end].strip()

            # 修复逻辑：检查内容是否为空，避免生成空块
            # 但不应跳过标题路径更新
            if not content and heading.level > self.heading_level:
                 continue

            # 确定父 chunk ID
            parent_level = heading.level - 1
            parent_id = parent_ids.get(parent_level) if parent_level > 0 else None

            # 判断是否需要按此级别拆分
            if heading.level <= self.heading_level:
                # 修复逻辑：如果有后续标题且当前内容为空，则跳过生成独立 Chunk，
                # 让当前标题通过 heading_path 自动合并到下一个 Chunk 中。
                # 同时维护 parent_ids 以保持层级连通性 (将父节点ID传递给当前层级，相当于当前层级透明化)。
                if not content and i < len(headings) - 1:
                    parent_ids[heading.level] = parent_id
                    continue

                # 创建标题块
                chunk_id = generate_id_func()
                parent_ids[heading.level] = chunk_id

                # 如果内容过长，需要二次切分
                # 优化逻辑：优先判断是否超过向量模型限制 (embedding_max_tokens)
                # 必须考虑加上标题上下文后的总长度
                token_count = self._count_tokens(content)
                heading_tokens = self._count_tokens(full_heading_context)
                
                # 如果 (标题 + 内容) 超过限制，则进行切分
                if (token_count + heading_tokens) > self.embedding_max_tokens:
                    # 创建标题 chunk (保留作为父节点结构)
                    # 内容包含完整标题路径，确保检索上下文
                    chunks.append(StructuredChunk(
                        content=full_heading_context, # 使用完整路径作为内容
                        heading_level=heading.level,
                        heading_text=heading.text,
                        heading_path=list(heading_path),
                        parent_id=parent_id,
                        chunk_type="heading",
                        token_count=heading_tokens
                    ))

                    # 内容进行二次切分
                    # 如果启用了语义切分 (use_semantic=True)，并且内容超过了向量限制，
                    # 那么 _split_content 内部会自动处理语义切分逻辑
                    # (即: content > embedding_max_tokens -> 尝试语义切分)
                    # 所以这里不需要额外传递 ignore_limit_check=True，除非我们想在未超限时也强行切分
                    # 但结构化切分的原则是“尽量保留结构”，所以仅在超限时切分是合理的。
                    sub_chunks = self._split_content(content)
                    for sub_content in sub_chunks:
                        # 关键修复：将标题上下文拼接到每个子块的内容前面
                        # 确保 "1.1 Project Background" 的切片包含 "1. Project Overview" 信息
                        chunk_content_with_context = f"{full_heading_context}\n\n{sub_content}"
                        
                        chunks.append(StructuredChunk(
                            content=chunk_content_with_context,
                            heading_level=heading.level,
                            heading_text=heading.text,
                            heading_path=list(heading_path),
                            parent_id=chunk_id,
                            chunk_type="content",
                            token_count=self._count_tokens(chunk_content_with_context)
                        ))
                else:
                    # 标题和内容合并为一个块
                    # 同样使用完整标题上下文
                    full_content = f"{full_heading_context}\n\n{content}" if content else full_heading_context
                    chunks.append(StructuredChunk(
                        content=full_content,
                        heading_level=heading.level,
                        heading_text=heading.text,
                        heading_path=list(heading_path),
                        parent_id=parent_id,
                        chunk_type="heading"
                    ))
            else:
                # 低于目标层级的标题，内容合并到上一个块或作为子块
                if content:
                    # 使用完整标题上下文
                    full_content = f"{full_heading_context}\n\n{content}"
                    # 找到最近的父级块
                    nearest_parent_id = None
                    for lvl in range(heading.level - 1, 0, -1):
                        if lvl in parent_ids:
                            nearest_parent_id = parent_ids[lvl]
                            break

                    chunks.append(StructuredChunk(
                        content=full_content,
                        heading_level=heading.level,
                        heading_text=heading.text,
                        heading_path=list(heading_path),
                        parent_id=nearest_parent_id,
                        chunk_type="content"
                    ))

        return chunks

    def _fallback_split(self, text: str, generate_id_func) -> List[StructuredChunk]:
        """当没有识别到标题时，使用普通切分"""
        # Fallback 时，是否应该忽略 limit check? 
        # 如果是 Structure 模式但没找到标题，通常还是希望尽量保留完整块，除非超长
        # 所以这里保持 ignore_limit_check=False
        text_chunks = self._split_content(text)
        return [
            StructuredChunk(
                content=tc,
                heading_level=None,
                heading_text=None,
                heading_path=[],
                parent_id=None,
                chunk_type="content",
                token_count=self._count_tokens(tc)
            )
            for tc in text_chunks
        ]

    @staticmethod
    def extract_markdown_headings(text: str) -> List[HeadingInfo]:
        """
        从 Markdown 格式文本中提取标题
        
        支持：
        - # 一级标题
        - ## 二级标题
        - ### 三级标题
        
        优化：
        - 排除代码块 (```...```) 中的注释行
        """
        import re
        headings = []
        
        # 匹配代码块 OR 标题
        # Group 1: 代码块
        # Group 2: 标题的 # 符号
        # Group 3: 标题内容
        # 优化：支持标题前有空格缩进 (^\s*)
        pattern = re.compile(r'(```[\s\S]*?```)|^\s*(#{1,6})\s+(.+)$', re.MULTILINE)

        print(f"🔍 [Parser] Extracting headings from text length: {len(text)}")

        for match in pattern.finditer(text):
            if match.group(1):
                # 匹配到代码块，跳过
                continue
            
            if match.group(2):
                # 匹配到标题
                level = len(match.group(2))
                title = match.group(3).strip()
                # 过滤掉仅包含 # 的行或空标题
                if not title:
                    continue
                
                print(f"  -> Found heading match: '{match.group(0).strip()}'")
                print(f"     Group 2 (Hashes): '{match.group(2)}' (Len: {len(match.group(2))})")
                print(f"     Group 3 (Title): '{title}'")
                
                print(f"  -> Final Level: {level}")
                    
                headings.append(HeadingInfo(
                    level=level,
                    text=title,
                    start_pos=match.start(),
                    end_pos=match.end()
                ))
        
        print(f"🔍 [Parser] Total headings found: {len(headings)}")
        return headings

    @staticmethod
    def extract_docx_headings(doc) -> List[HeadingInfo]:
        """
        从 Word 文档中提取标题

        通过 paragraph.style.name 识别：
        - Heading 1 → H1
        - Heading 2 → H2
        - Heading 3 → H3
        """
        headings = []
        current_pos = 0

        for para in doc.paragraphs:
            para_text = para.text.strip()
            para_len = len(para_text) + 1  # +1 for newline

            if para.style and para.style.name:
                style_name = para.style.name.lower()

                level = None
                if 'heading 1' in style_name or style_name == 'heading1':
                    level = 1
                elif 'heading 2' in style_name or style_name == 'heading2':
                    level = 2
                elif 'heading 3' in style_name or style_name == 'heading3':
                    level = 3

                if level and para_text:
                    headings.append(HeadingInfo(
                        level=level,
                        text=para_text,
                        start_pos=current_pos,
                        end_pos=current_pos + len(para_text)
                    ))

            current_pos += para_len

        return headings

    @staticmethod
    def extract_pdf_headings(page, avg_font_size: float) -> List[HeadingInfo]:
        """
        从 PDF 页面中提取标题

        通过字体大小判断：
        - 字体大小 > 正文平均字体 * 1.5 → H1
        - 字体大小 > 正文平均字体 * 1.3 → H2
        - 字体大小 > 正文平均字体 * 1.15 → H3
        - 加粗 + 独占一行 → 更可能是标题
        """
        headings = []
        blocks = page.get_text("dict", sort=True)["blocks"]

        current_pos = 0
        for block in blocks:
            if block["type"] != 0:  # 非文本块
                continue

            for line in block["lines"]:
                line_text = ""
                max_font_size = 0
                is_bold = False

                for span in line["spans"]:
                    line_text += span["text"]
                    font_size = span["size"]
                    max_font_size = max(max_font_size, font_size)

                    # 检查是否加粗
                    flags = span.get("flags", 0)
                    if flags & 2 ** 4:  # Bold flag
                        is_bold = True

                line_text = line_text.strip()
                if not line_text:
                    continue

                # 判断是否为标题
                level = None
                if max_font_size > avg_font_size * 1.5:
                    level = 1
                elif max_font_size > avg_font_size * 1.3:
                    level = 2
                elif max_font_size > avg_font_size * 1.15 and is_bold:
                    level = 3
                elif is_bold and len(line_text) < 50:  # 加粗的短行可能是标题
                    level = 3

                if level:
                    headings.append(HeadingInfo(
                        level=level,
                        text=line_text,
                        start_pos=current_pos,
                        end_pos=current_pos + len(line_text)
                    ))

                current_pos += len(line_text) + 1

        return headings

    @staticmethod
    def calculate_avg_font_size(doc) -> float:
        """计算 PDF 文档的平均字体大小"""
        font_sizes = []
        for page in doc:
            blocks = page.get_text("dict")["blocks"]
            for block in blocks:
                if block["type"] != 0:
                    continue
                for line in block["lines"]:
                    for span in line["spans"]:
                        if span["text"].strip():
                            font_sizes.append(span["size"])

        if not font_sizes:
            return 12.0  # 默认值

        # 返回中位数，避免被大标题影响
        font_sizes.sort()
        mid = len(font_sizes) // 2
        return font_sizes[mid]


class FileParser:
    """文件解析器"""

    def __init__(self, kb_id: str):
        self.kb_id = kb_id
        self.image_dir = os.path.join(settings.IMAGE_DIR, kb_id)
        os.makedirs(self.image_dir, exist_ok=True)

    def parse(
        self,
        file_path: str,
        chunk_mode: str = "auto",  # auto, no_chunk, custom, structure
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        separator: str = "\n\n",
        heading_level: int = 2,  # structure 模式专用
        use_semantic: bool = False,  # 是否使用语义切分
        embedding_model: str = None,  # 动态指定 Embedding 模型
        embedding_base_url: str = None, # 动态指定 Embedding Base URL
        embedding_api_key: str = None, # 动态指定 Embedding API Key
        embedding_max_tokens: int = 8192, # Embedding 模型最大上下文 (用于向量模型限制检查)
        semantic_max_tokens: int = None, # 语义模型最大上下文 (用于语义切分限制检查)
    ) -> List[ParsedChunk]:
        """
        解析文件

        Args:
            file_path: 文件路径
            chunk_mode: 切分模式 (custom, no_split, structure)
            chunk_size: 块大小
            chunk_overlap: 重叠大小
            separator: 分隔符
            heading_level: 结构化模式的标题级别 (1, 2, 3)
            use_semantic: 是否使用语义切分 (仅在 structure 模式或 fallback 下生效)
            embedding_model: 指定使用的 Embedding 模型名称，为 None 时使用默认配置
            embedding_base_url: 指定使用的 Embedding Base URL
            embedding_api_key: 指定使用的 Embedding API Key
            embedding_max_tokens: 向量模型最大上下文长度
            semantic_max_tokens: 语义模型最大上下文长度
        """
        # 强制重叠逻辑：
        # 1. 如果不是自定义模式 (custom)，强制使用 10% 的重叠，忽略前端传入的 chunk_overlap
        # 2. 即使是自定义模式，如果 chunk_overlap 未设置或不合理，也建议有默认值（这里完全尊重用户输入）
        if chunk_mode != 'custom':
            if chunk_size is not None:
                chunk_overlap = int(chunk_size * 0.1)
                print(f"🔧 [Parser] Force set chunk_overlap to {chunk_overlap} (10% of {chunk_size}) for mode '{chunk_mode}'")
            else:
                chunk_overlap = 0 # 安全回退
                print(f"🔧 [Parser] Chunk size is None, set chunk_overlap to 0 for mode '{chunk_mode}'")

        # 确保 chunk_size 有默认值，避免 TextSplitter 初始化失败
        if chunk_size is None:
            chunk_size = 500 # 默认值


        self.splitter = TextSplitter(chunk_size, chunk_overlap, separator)
        
        # 语义切分逻辑开关
        # 1. 如果是 semantic 模式，强制开启 use_semantic
        # 2. 如果是 structure 模式，也强制开启 use_semantic (用户需求：必选项)
        if chunk_mode == 'semantic' or chunk_mode == 'structure':
            use_semantic = True
            print(f"🔧 [Parser] Mode '{chunk_mode}' detected, forcing use_semantic=True")
        
        self.structured_splitter = StructuredSplitter(
            heading_level, 
            chunk_size, 
            chunk_overlap=chunk_overlap,  # 传递重叠大小
            use_semantic=use_semantic,
            embedding_model=embedding_model,
            embedding_base_url=embedding_base_url,
            embedding_api_key=embedding_api_key, # 传递 API Key
            embedding_max_tokens=embedding_max_tokens,
            semantic_max_tokens=semantic_max_tokens
        )

        self.chunk_mode = chunk_mode
        self.heading_level = heading_level

        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".pdf":
            return self._parse_pdf(file_path)
        elif ext == ".docx":
            return self._parse_docx(file_path)
        elif ext == ".xlsx":
            return self._parse_excel(file_path)
        elif ext == ".csv":
            return self._parse_csv(file_path)
        elif ext == ".txt" or ext == ".md":
            return self._parse_txt(file_path)
        elif ext in [".jpg", ".jpeg", ".png", ".bmp", ".webp"]:
            return self._parse_image_file(file_path)
        else:
            raise ValueError(f"不支持的文件格式: {ext}")

    def _save_image(self, image_bytes: bytes, file_id: str, page_num: int, img_index: int, ext: str = "png") -> str:
        """保存图片到本地"""
        image_filename = f"{file_id}_p{page_num}_i{img_index}.{ext}"
        image_path = os.path.join(self.image_dir, image_filename)
        
        with open(image_path, "wb") as f:
            f.write(image_bytes)
            
        # 返回相对路径或文件名，用于前端展示和后续处理
        # 这里返回文件名，前端通过 /static/images/{kb_id}/{filename} 访问
        # 或者后端统一处理路径
        return f"{self.kb_id}/{image_filename}"

    def _process_text_content(self, text: str, page_num: int) -> List[ParsedChunk]:
        """处理文本内容（根据切分策略）"""
        if not text.strip():
            return []
            
        chunks = []
        
        # 语义切分模式
        if self.chunk_mode == "semantic":
             # 复用 structured_splitter 中的逻辑，它已经初始化好了 semantic_splitter
             # 强制忽略向量模型长度限制，进行语义切分
             text_chunks = self.structured_splitter._split_content(text, ignore_limit_check=True)
             for tc in text_chunks:
                chunks.append(ParsedChunk(
                    content=tc,
                    page_number=page_num,
                    content_type="text",
                    metadata={"chunk_type": "semantic"},
                    token_count=self.structured_splitter._count_tokens(tc)
                ))
             return chunks

        # 处理不切分模式
        if self.chunk_mode == "no_split" or self.chunk_mode == "no_chunk":
            # 即使不切分，也可能需要处理一些基本的清理
            # 修正：不切分模式下，保留全部文本为一个 Chunk，不进行任何拆分
            # 注意：如果文件是 PDF，这里处理的是单页内容聚合；如果需要全文档不切分，需要在上层处理
            # 对于 _process_text_content 来说，它接收的是一段文本（可能是全文也可能是单页），这里直接返回即可
            cleaned_text = text.strip()
            if cleaned_text:
                chunks.append(ParsedChunk(
                    content=cleaned_text,
                    page_number=page_num,
                    content_type="text",
                    token_count=self.splitter.count_tokens(cleaned_text)
                ))
        else:
            text_chunks = self.splitter.split(text)
            for tc in text_chunks:
                chunks.append(ParsedChunk(
                    content=tc,
                    page_number=page_num,
                    content_type="text",
                    token_count=self.splitter.count_tokens(tc)
                ))
        return chunks

    def _parse_pdf(self, file_path: str) -> List[ParsedChunk]:
        """解析 PDF 文件 (包含图片提取和顺序保持)"""
        chunks = []
        doc = fitz.open(file_path)
        file_id = os.path.basename(file_path).split("_")[0]

        # for no_split mode: aggregate all text
        full_doc_text_buffer = []

        for page_num, page in enumerate(doc, start=1):
            # 获取页面上的所有块 (text, image)
            # sort=True 会根据垂直坐标排序，符合阅读顺序
            blocks = page.get_text("dict", sort=True)["blocks"]
            
            # 如果是不切分模式，我们在页面级别聚合所有文本
            # 否则我们依然在 block 级别处理，以便正确插入图片
            page_text_buffer = ""
            
            # 临时存储本页的chunks，最后再根据模式决定如何合并
            page_chunks = []
            current_text_buffer = ""

            for block_idx, block in enumerate(blocks):
                if block["type"] == 0:  # Text Block
                    # 提取文本
                    block_text = ""
                    for line in block["lines"]:
                        for span in line["spans"]:
                            block_text += span["text"]
                        block_text += "\n"
                    
                    if self.chunk_mode == "no_split" or self.chunk_mode == "no_chunk":
                        page_text_buffer += block_text
                    else:
                        current_text_buffer += block_text
                    
                elif block["type"] == 1:  # Image Block
                    # 2. 处理图片
                    try:
                        image_bytes = block["image"]
                        image_ext = block["ext"]
                        img_size = len(image_bytes)
                        
                        # 获取图片尺寸信息 (用于调试和过滤)
                        width, height = 0, 0
                        try:
                            # 尝试从 bytes 创建 pixmap 获取尺寸
                            pix = fitz.Pixmap(image_bytes)
                            width, height = pix.width, pix.height
                            pix = None # 释放资源
                        except Exception:
                            pass

                        # 打印调试信息
                        print(f"🔍 [PDF Image Debug] Page: {page_num} | Size: {img_size} bytes ({img_size/1024:.2f} KB) | Dim: {width}x{height} | Ext: {image_ext}")

                        # 过滤过小的图片 (小于 3KB)
                        if img_size < 3072:
                            print(f"   -> SKIPPED (Size < 3KB)")
                            # 如果图片无效，不打断文本流
                            continue
                        
                        # 过滤极端长宽比的图片 (通常是分割线)
                        # 例如: 宽度是高度的 50 倍，或者高度是宽度的 50 倍
                        if width > 0 and height > 0:
                            ratio = width / height
                            if ratio > 50 or ratio < 0.02:
                                print(f"   -> SKIPPED (Extreme Aspect Ratio: {ratio:.2f})")
                                continue

                        # 只有图片有效时，才结算之前的文本 (仅在非 no_split 模式下)
                        if self.chunk_mode != "no_split" and self.chunk_mode != "no_chunk":
                            if current_text_buffer:
                                page_chunks.extend(self._process_text_content(current_text_buffer, page_num))
                                current_text_buffer = ""

                        # 保存图片
                        saved_path = self._save_image(image_bytes, file_id, page_num, block_idx, image_ext)
                        
                        # 创建图片块 (图片总是单独成块)
                        # 注意：在 no_split 模式下，图片也会成为独立的块插入在文本之间，或者追加在最后？
                        # 通常 no_split 意味着文本不切分，但图片还是独立的
                        # 为了简单，我们先把图片存入 page_chunks
                        page_chunks.append(ParsedChunk(
                            content=f"[图片: {saved_path}]", # 占位符内容
                            page_number=page_num,
                            content_type="image",
                            image_path=saved_path,
                            metadata={
                                "timestamp": datetime.now().isoformat(),
                                "original_name": f"image_{page_num}_{block_idx}"
                            }
                        ))
                    except Exception as e:
                        print(f"PDF 图片提取失败 (Page {page_num}): {e}")
            
            # 页面结束处理
            if self.chunk_mode == "no_split" or self.chunk_mode == "no_chunk":
                # 不切分模式：累积文本到全局 buffer，不立即切分
                if page_text_buffer:
                    full_doc_text_buffer.append(page_text_buffer)
                # 追加本页提取的所有图片 (图片依然是独立的 chunk)
                chunks.extend(page_chunks)
            else:
                # 普通切分模式：处理剩余的 buffer
                if current_text_buffer:
                    page_chunks.extend(self._process_text_content(current_text_buffer, page_num))
                chunks.extend(page_chunks)

        doc.close()

        # no_split 模式下，最后统一生成一个文本 Chunk
        if (self.chunk_mode == "no_split" or self.chunk_mode == "no_chunk") and full_doc_text_buffer:
             full_text = "\n".join(full_doc_text_buffer)
             # 使用 page_number=1 或 0 表示全文
             # 插入到开头，作为整个文档的文本内容
             text_chunks = self._process_text_content(full_text, 1)
             if text_chunks:
                 # 将文本块插在最前面
                 chunks.insert(0, text_chunks[0])

        return chunks

    def _parse_docx(self, file_path: str) -> List[ParsedChunk]:
        """解析 Word 文件 (包含图片提取和顺序保持)"""
        chunks = []
        doc = DocxDocument(file_path)
        file_id = os.path.basename(file_path).split("_")[0]
        
        current_text_buffer = ""
        # 用于 no_split 模式的页面级缓存
        page_text_buffer = ""
        page_image_chunks = []
        
        # 结构化模式专用
        full_text_buffer = ""
        headings = []
        
        # 辅助函数：处理段落中的图片和文本
        def process_paragraph(para, page_num):
            nonlocal current_text_buffer, page_text_buffer, page_image_chunks, full_text_buffer, headings
            
            # 临时文本，用于当前段落
            para_text = ""
            
            for run in para.runs:
                # 1. 提取文本
                if run.text:
                    if self.chunk_mode == "structure" or self.chunk_mode == "semantic":
                         # 结构化/语义模式：累积到全文 buffer
                         full_text_buffer += run.text
                    
                    if self.chunk_mode == "no_split" or self.chunk_mode == "no_chunk":
                        page_text_buffer += run.text
                    else:
                        current_text_buffer += run.text
                    para_text += run.text
                
                # 2. 检查图片
                # 查找 run 元素下的 drawing 标签
                if 'drawing' in run.element.xml:
                    drawings = run.element.findall('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}drawing')
                    for drawing in drawings:
                        # 找到 blip 元素获取 rId
                        nsmap = {
                            'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
                            'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
                        }
                        blips = drawing.findall('.//a:blip', namespaces=nsmap)
                        for blip in blips:
                            rId = blip.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed')
                            if rId:
                                try:
                                    image_part = doc.part.related_parts[rId]
                                    image_bytes = image_part.blob
                                    
                                    # 过滤过小的图片 (小于 2KB)
                                    if len(image_bytes) < 2048:
                                        print(f"🔍 [Parser] 跳过过小图片 (Word): {len(image_bytes)} bytes")
                                        continue

                                    content_type = image_part.content_type
                                    ext = content_type.split('/')[-1] if '/' in content_type else "png"
                                    if ext == "jpeg": ext = "jpg"
                                    
                                    # 保存图片
                                    img_idx = len(chunks) + len(page_image_chunks)
                                    saved_path = self._save_image(image_bytes, file_id, page_num, img_idx, ext)
                                    
                                    image_chunk = ParsedChunk(
                                        content=f"[图片: {saved_path}]",
                                        page_number=page_num,
                                        content_type="image",
                                        image_path=saved_path,
                                        metadata={
                                            "timestamp": datetime.now().isoformat(),
                                            "original_name": f"image_docx_{img_idx}"
                                        }
                                    )
                                    
                                    if self.chunk_mode == "no_split" or self.chunk_mode == "no_chunk":
                                        # 在 no_split 模式下，图片单独收集，不打断文本流
                                        page_image_chunks.append(image_chunk)
                                    elif self.chunk_mode == "structure":
                                        # 结构化模式：图片直接作为独立块
                                        chunks.append(image_chunk)
                                    else:
                                        # 普通模式：结算前面的文本
                                        if current_text_buffer:
                                            chunks.extend(self._process_text_content(current_text_buffer, page_num))
                                            current_text_buffer = ""
                                        chunks.append(image_chunk)
                                        
                                except Exception as e:
                                    print(f"Word 图片提取失败: {e}")
            
            # 处理结构化模式的文本收集和标题提取
            if self.chunk_mode == "structure":
                clean_para_text = para_text.strip()
                if clean_para_text:
                    # 检查是否是标题
                    style_name = para.style.name.lower() if para.style and para.style.name else ""
                    level = None
                    # Word 标题样式通常是 "Heading 1", "Heading 2" 等
                    # 中文版可能是 "标题 1"
                    if 'heading 1' in style_name or style_name == 'heading1' or style_name == '标题 1' or style_name == '标题 1 char': level = 1
                    elif 'heading 2' in style_name or style_name == 'heading2' or style_name == '标题 2' or style_name == '标题 2 char': level = 2
                    elif 'heading 3' in style_name or style_name == 'heading3' or style_name == '标题 3' or style_name == '标题 3 char': level = 3
                    
                    if level:
                        start_pos = len(full_text_buffer)
                        end_pos = start_pos + len(clean_para_text)
                        headings.append(HeadingInfo(
                            level=level,
                            text=clean_para_text,
                            start_pos=start_pos,
                            end_pos=end_pos
                        ))
                    
                    full_text_buffer += clean_para_text + "\n\n"

            # 段落结束换行
            if self.chunk_mode == "no_split" or self.chunk_mode == "no_chunk":
                page_text_buffer += "\n"
            elif self.chunk_mode == "structure":
                pass # 已经在上面处理了
            else:
                current_text_buffer += "\n"

        # 遍历文档元素
        page_num_estimate = 1
        para_count = 0
        
        for element in doc.element.body.iterchildren():
            # 检查是否需要分页结算 (仅针对 no_split 模式)
            if (self.chunk_mode == "no_split" or self.chunk_mode == "no_chunk") and para_count > 10:
                # 不切分模式：仅更新页码估算，结算图片，保留文本
                if page_image_chunks:
                    chunks.extend(page_image_chunks)
                    page_image_chunks = []
                
                para_count = 0
                page_num_estimate += 1
            
            # 普通模式的分页估算
            elif para_count > 10:
                para_count = 0
                page_num_estimate += 1

            if element.tag.endswith('p'): # Paragraph
                process_paragraph(Paragraph(element, doc), page_num_estimate)
                para_count += 1
                    
            elif element.tag.endswith('tbl'): # Table
                # 提取表格内容
                table = Table(element, doc)
                rows_data = []
                for row in table.rows:
                    row_cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                    rows_data.append(row_cells)
                
                if rows_data:
                    # 优化表格处理：将第一行识别为表头，确保 Markdown 表格格式正确且包含表头信息
                    if len(rows_data) > 0:
                        try:
                            # 使用第一行作为列名 (表头)
                            headers = rows_data[0]
                            data = rows_data[1:]
                            df = pd.DataFrame(data, columns=headers)
                            # 转换为 Markdown，index=False 隐藏 pandas 索引，默认使用 columns 作为 headers
                            md_table = df.to_markdown(index=False)
                        except Exception as e:
                            # 降级处理：如果转换失败（如列数不一致），则不指定 columns
                            print(f"表格转换标准模式失败，降级处理: {e}")
                            df = pd.DataFrame(rows_data)
                            # headers="keys" 会显示 0, 1, 2... 作为表头，为了保留原始信息，
                            # 这里尝试用 tabulate 的 headers="firstrow" 逻辑，但通过 pandas 较难直接实现
                            # 因此直接生成，第一行数据会变成 Markdown 的数据行，表头为 0, 1, 2...
                            # 这是一个折衷方案，防止崩溃
                            md_table = df.to_markdown(index=False)
                    else:
                        md_table = ""
                    
                    if self.chunk_mode == "structure":
                        # 结构化模式：表格作为文本的一部分
                        if md_table:
                            full_text_buffer += f"\n{md_table}\n\n"
                    elif self.chunk_mode == "semantic":
                         # 语义模式：表格也拼接到全文
                         if md_table:
                            full_text_buffer += f"\n{md_table}\n\n"
                    elif self.chunk_mode == "no_split" or self.chunk_mode == "no_chunk":
                        # no_split 模式：表格作为 Markdown 拼接到文本中
                        page_text_buffer += f"\n{md_table}\n"
                    else:
                        # 普通模式：结算文本，表格单独成块
                        if current_text_buffer:
                            chunks.extend(self._process_text_content(current_text_buffer, page_num_estimate))
                            current_text_buffer = ""
                        
                        chunks.append(ParsedChunk(
                            content=md_table,
                            page_number=page_num_estimate,
                            content_type="table",
                        ))

        # 处理剩余文本
        if self.chunk_mode == "structure":
            # 结构化模式：进行最终切分
            if full_text_buffer:
                # 生成 ID 的计数器
                chunk_counter = [0]
                def generate_id():
                    chunk_counter[0] += 1
                    return f"chunk_{chunk_counter[0]}"

                print(f"🔍 [Structure Split] Text Len: {len(full_text_buffer)}, Headings: {len(headings)}")
                
                structured_chunks = self.structured_splitter.split_by_headings(
                    full_text_buffer, headings, generate_id
                )
                
                # 转换为 ParsedChunk
                text_chunks = [
                    ParsedChunk(
                        content=sc.content,
                        page_number=1, # 结构化模式下页码较难精确对应，暂定 1
                        content_type="text",
                        metadata={
                            "heading_level": sc.heading_level,
                            "heading_text": sc.heading_text,
                            "heading_path": sc.heading_path,
                            "parent_id": sc.parent_id,
                            "chunk_type": sc.chunk_type,
                        },
                        token_count=self.structured_splitter._count_tokens(sc.content)
                    )
                    for sc in structured_chunks
                ]
                chunks.extend(text_chunks)
                
        elif self.chunk_mode == "semantic":
            # 纯语义切分模式：不关心标题结构，直接对全文进行语义切分
            if full_text_buffer:
                # 复用 StructuredSplitter 中的 _split_content 方法（它封装了语义切分逻辑）
                # 但需要先确保 semantic_splitter 已初始化 (在 __init__ 中已处理)
                
                print(f"🔍 [Semantic Split] Processing text length: {len(full_text_buffer)}")
                semantic_chunks = self.structured_splitter._split_content(full_text_buffer)
                
                for idx, content in enumerate(semantic_chunks):
                    chunks.append(ParsedChunk(
                        content=content,
                        page_number=1, # 语义切分难以精确定位页码，暂定 1
                        content_type="text",
                        metadata={
                            "chunk_type": "semantic_content",
                            "original_index": idx
                        },
                        token_count=self.splitter.count_tokens(content)
                    ))
            
            # 同样追加图片
            if page_image_chunks:
                chunks.extend(page_image_chunks)

        elif self.chunk_mode == "no_split" or self.chunk_mode == "no_chunk":
            if page_image_chunks:
                chunks.extend(page_image_chunks)
            
            # 最后统一处理所有文本，作为一个 Chunk 放在最前面
            if page_text_buffer:
                text_chunks = self._process_text_content(page_text_buffer, 1)
                if text_chunks:
                    chunks.insert(0, text_chunks[0])
        else:
            if current_text_buffer:
                chunks.extend(self._process_text_content(current_text_buffer, page_num_estimate))
            
        return chunks

    def _parse_txt(self, file_path: str) -> List[ParsedChunk]:
        """解析 TXT/MD 文件（支持结构化模式）"""
        try:
            with open(file_path, mode='r', encoding='utf-8') as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(file_path, mode='r', encoding='gbk') as f:
                content = f.read()

        # 结构化模式：提取 Markdown 标题
        if self.chunk_mode == "structure":
            headings = StructuredSplitter.extract_markdown_headings(content)

            if headings:
                # 生成 ID 的计数器
                chunk_counter = [0]

                def generate_id():
                    chunk_counter[0] += 1
                    return f"chunk_{chunk_counter[0]}"

                structured_chunks = self.structured_splitter.split_by_headings(
                    content, headings, generate_id
                )

                # 转换为 ParsedChunk
                return [
                    ParsedChunk(
                        content=sc.content,
                        page_number=1,
                        content_type="text",
                        metadata={
                            "heading_level": sc.heading_level,
                            "heading_text": sc.heading_text,
                            "heading_path": sc.heading_path,
                            "parent_id": sc.parent_id,
                            "chunk_type": sc.chunk_type,
                        },
                        token_count=self.structured_splitter._count_tokens(sc.content)
                    )
                    for sc in structured_chunks
                ]
            else:
                print(f"⚠️ [Parser] 文件未识别到 Markdown 标题，降级为普通切分")

        # 普通模式或降级
        return self._process_text_content(content, 1)

    def _parse_image_file(self, file_path: str) -> List[ParsedChunk]:
        """解析单独的图片文件"""
        chunks = []
        file_name = os.path.basename(file_path)
        # file_path format: .../kb_id/file_id_filename
        # we need file_id
        parts = file_name.split("_")
        file_id = parts[0] if parts else "unknown"
        
        ext = os.path.splitext(file_name)[1].lower().lstrip(".")
        if ext == "jpeg": ext = "jpg"
        
        try:
            with open(file_path, "rb") as f:
                image_bytes = f.read()
                
            # 保存图片到 standalone 图片目录
            # 创建 standalone 目录
            standalone_dir = os.path.join(self.image_dir, "standalone")
            os.makedirs(standalone_dir, exist_ok=True)
            
            image_filename = f"{file_id}.{ext}"
            image_path = os.path.join(standalone_dir, image_filename)
            
            with open(image_path, "wb") as f:
                f.write(image_bytes)
            
            # 相对路径: kb_id/standalone/filename
            saved_path = f"{self.kb_id}/standalone/{image_filename}"
            
            chunks.append(ParsedChunk(
                content=f"[图片: {saved_path}]",
                page_number=1,
                content_type="image",
                image_path=saved_path,
                metadata={
                    "timestamp": datetime.now().isoformat(),
                    "original_name": file_name,
                    "is_standalone_image": True
                }
            ))
        except Exception as e:
            print(f"Error parsing image file {file_path}: {e}")
            # 如果出错，可能返回空或者抛出异常
            raise e
            
        return chunks

    def _parse_excel(self, file_path: str) -> List[ParsedChunk]:
        """解析 Excel 文件"""
        print(f"🔍 [Excel Parser] 开始解析: {file_path}")
        chunks = []
        try:
            xlsx = pd.ExcelFile(file_path)
            print(f"🔍 [Excel Parser] 工作表数量: {len(xlsx.sheet_names)}, 名称: {xlsx.sheet_names}")

            for sheet_name in xlsx.sheet_names:
                print(f"🔍 [Excel Parser] 正在处理工作表: {sheet_name}")
                df = pd.read_excel(xlsx, sheet_name=sheet_name)
                print(f"🔍 [Excel Parser] 工作表 '{sheet_name}' 行数: {len(df)}, 列数: {len(df.columns)}")

                # Excel 通常按行切分，不使用通用 TextSplitter
                chunk_size = 20
                for i in range(0, len(df), chunk_size):
                    chunk_df = df.iloc[i:i + chunk_size]
                    md_table = chunk_df.to_markdown(index=False)

                    chunks.append(ParsedChunk(
                        content=f"[工作表: {sheet_name}]\n{md_table}",
                        page_number=i // chunk_size + 1,
                        content_type="table",
                    ))
                print(f"🔍 [Excel Parser] 工作表 '{sheet_name}' 处理完成，当前总块数: {len(chunks)}")

            print(f"✅ [Excel Parser] 解析完成，总块数: {len(chunks)}")
        except Exception as e:
            print(f"❌ [Excel Parser] 解析异常: {e}")
            import traceback
            traceback.print_exc()
            raise e
        return chunks

    def _parse_csv(self, file_path: str) -> List[ParsedChunk]:
        """解析 CSV 文件"""
        chunks = []
        df = pd.read_csv(file_path)
        chunk_size = 20
        for i in range(0, len(df), chunk_size):
            chunk_df = df.iloc[i:i + chunk_size]
            md_table = chunk_df.to_markdown(index=False)

            chunks.append(ParsedChunk(
                content=md_table,
                page_number=i // chunk_size + 1,
                content_type="table",
            ))
        return chunks