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

settings = get_settings()


@dataclass
class ParsedChunk:
    """解析后的块"""
    content: str
    page_number: Optional[int]
    content_type: str  # text, table, image
    image_path: Optional[str] = None
    metadata: Optional[dict] = None


class TextSplitter:
    """文本切分器"""

    def __init__(
        self,
        chunk_size: int = None,
        chunk_overlap: int = None,
        separator: str = "\n\n",
    ):
        self.chunk_size = chunk_size or settings.CHUNK_SIZE
        self.chunk_overlap = chunk_overlap or settings.CHUNK_OVERLAP
        self.separator = separator.replace("\\n", "\n") if separator else "\n\n"

    def split(self, text: str) -> List[str]:
        """切分文本"""
        if not text:
            return []
            
        if len(text) <= self.chunk_size:
            return [text]

        chunks = []
        start = 0

        while start < len(text):
            # 如果剩余长度小于块大小，直接作为一个块
            if len(text) - start <= self.chunk_size:
                chunks.append(text[start:].strip())
                break

            end = start + self.chunk_size

            # 尝试在自然边界处切分
            best_end = end
            
            # 查找最近的分隔符（向前查找）
            # 优先使用配置的分隔符，其次是通用分隔符
            separators = [self.separator, "\n", "。", ".", " "]
            
            found_sep = False
            for sep in separators:
                # 在 [start + chunk_size/2, end] 范围内查找
                min_search_pos = max(start + self.chunk_size // 2, start)
                sep_pos = text.rfind(sep, min_search_pos, end)
                if sep_pos > start:
                    best_end = sep_pos + len(sep)
                    found_sep = True
                    break
            
            if not found_sep:
                # 强制切分
                best_end = end

            chunk = text[start:best_end].strip()
            if chunk:
                chunks.append(chunk)
            
            # 下一块的起始位置，考虑重叠
            start = best_end - self.chunk_overlap
            
            # 防止死循环
            if start <= start: # 如果重叠导致没前进
                 start = best_end

        return chunks


class FileParser:
    """文件解析器"""

    def __init__(self, kb_id: str):
        self.kb_id = kb_id
        self.image_dir = os.path.join(settings.IMAGE_DIR, kb_id)
        os.makedirs(self.image_dir, exist_ok=True)

    def parse(
        self, 
        file_path: str, 
        chunk_mode: str = "auto", # auto, no_chunk, custom
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        separator: str = "\n\n"
    ) -> List[ParsedChunk]:
        """
        解析文件
        
        Args:
            file_path: 文件路径
            chunk_mode: 切分模式
            chunk_size: 块大小
            chunk_overlap: 重叠大小
            separator: 分隔符
        """
        self.splitter = TextSplitter(chunk_size, chunk_overlap, separator)
        self.chunk_mode = chunk_mode
        
        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".pdf":
            return self._parse_pdf(file_path)
        elif ext == ".docx":
            return self._parse_docx(file_path)
        elif ext == ".xlsx":
            return self._parse_excel(file_path)
        elif ext == ".csv":
            return self._parse_csv(file_path)
        elif ext == ".txt":
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
        # 处理不切分模式
        if self.chunk_mode == "no_split" or self.chunk_mode == "no_chunk":
            # 即使不切分，也可能需要处理一些基本的清理
            cleaned_text = text.strip()
            if cleaned_text:
                chunks.append(ParsedChunk(
                    content=cleaned_text,
                    page_number=page_num,
                    content_type="text"
                ))
        else:
            text_chunks = self.splitter.split(text)
            for tc in text_chunks:
                chunks.append(ParsedChunk(
                    content=tc,
                    page_number=page_num,
                    content_type="text"
                ))
        return chunks

    def _parse_pdf(self, file_path: str) -> List[ParsedChunk]:
        """解析 PDF 文件 (包含图片提取和顺序保持)"""
        chunks = []
        doc = fitz.open(file_path)
        file_id = os.path.basename(file_path).split("_")[0]

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
                # 不切分模式：处理整个页面的文本
                if page_text_buffer:
                    # 将文本块插入到 chunks 开头（或者根据业务逻辑）
                    # 这里简单的处理：文本作为一个大块，图片作为独立块
                    # 如果希望图片穿插在文本中，那 no_split 的定义就比较模糊了
                    # 既然是 "按页不切分"，那么这一页的所有文本应该是一个块
                    chunks.extend(self._process_text_content(page_text_buffer, page_num))
                # 追加本页提取的所有图片
                chunks.extend(page_chunks)
            else:
                # 普通切分模式：处理剩余的 buffer
                if current_text_buffer:
                    page_chunks.extend(self._process_text_content(current_text_buffer, page_num))
                chunks.extend(page_chunks)

        doc.close()
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
        
        # 辅助函数：处理段落中的图片和文本
        def process_paragraph(para, page_num):
            nonlocal current_text_buffer, page_text_buffer, page_image_chunks
            
            # 临时文本，用于当前段落
            para_text = ""
            
            for run in para.runs:
                # 1. 提取文本
                if run.text:
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
                                    else:
                                        # 普通模式：结算前面的文本
                                        if current_text_buffer:
                                            chunks.extend(self._process_text_content(current_text_buffer, page_num))
                                            current_text_buffer = ""
                                        chunks.append(image_chunk)
                                        
                                except Exception as e:
                                    print(f"Word 图片提取失败: {e}")
            
            # 段落结束换行
            if self.chunk_mode == "no_split" or self.chunk_mode == "no_chunk":
                page_text_buffer += "\n"
            else:
                current_text_buffer += "\n"

        # 遍历文档元素
        page_num_estimate = 1
        para_count = 0
        
        for element in doc.element.body.iterchildren():
            # 检查是否需要分页结算 (仅针对 no_split 模式)
            if (self.chunk_mode == "no_split" or self.chunk_mode == "no_chunk") and para_count > 10:
                # 结算上一页的文本和图片
                if page_text_buffer:
                    chunks.extend(self._process_text_content(page_text_buffer, page_num_estimate))
                    page_text_buffer = ""
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
                    df = pd.DataFrame(rows_data)
                    md_table = df.to_markdown(index=False, header=False)
                    
                    if self.chunk_mode == "no_split" or self.chunk_mode == "no_chunk":
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
        if self.chunk_mode == "no_split" or self.chunk_mode == "no_chunk":
            if page_text_buffer:
                chunks.extend(self._process_text_content(page_text_buffer, page_num_estimate))
            if page_image_chunks:
                chunks.extend(page_image_chunks)
        else:
            if current_text_buffer:
                chunks.extend(self._process_text_content(current_text_buffer, page_num_estimate))
            
        return chunks

    def _parse_txt(self, file_path: str) -> List[ParsedChunk]:
        """解析 TXT 文件"""
        try:
            with open(file_path, mode='r', encoding='utf-8') as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(file_path, mode='r', encoding='gbk') as f:
                content = f.read()
        
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
        chunks = []
        xlsx = pd.ExcelFile(file_path)

        for sheet_name in xlsx.sheet_names:
            df = pd.read_excel(xlsx, sheet_name=sheet_name)
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
