/**
 * 文件相关 API
 */

import request from './request';
import type {
  FileInfo,
  FileUploadResponse,
  ChunkInfo,
  ChunkListResponse,
  UpdateChunkRequest,
  ApiResponse,
} from '@/types';

/** 上传文件 */
export const uploadFile = (
  kbId: string,
  file: File,
  onProgress?: (percent: number) => void
): Promise<ApiResponse<FileUploadResponse>> => {
  const formData = new FormData();
  formData.append('file', file);

  return request.post(`/knowledge-bases/${kbId}/upload`, formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
    onUploadProgress: (progressEvent) => {
      if (progressEvent.total && onProgress) {
        const percent = Math.round((progressEvent.loaded * 100) / progressEvent.total);
        onProgress(percent);
      }
    },
  });
};

/** 获取文件列表 */
export const getFileList = (kbId: string): Promise<ApiResponse<FileInfo[]>> => {
  return request.get(`/knowledge-bases/${kbId}/files`);
};

/** 获取文件详情 */
export const getFileDetail = (fileId: string): Promise<ApiResponse<FileInfo>> => {
  return request.get(`/files/${fileId}`);
};

/** 删除文件 */
export const deleteFile = (fileId: string): Promise<ApiResponse<void>> => {
  return request.delete(`/files/${fileId}`);
};

/** 获取文件的 Chunks */
export const getFileChunks = (fileId: string): Promise<ApiResponse<ChunkListResponse>> => {
  return request.get(`/files/${fileId}/chunks`);
};

/** 更新 Chunk 内容 */
export const updateChunk = (
  chunkId: string,
  data: UpdateChunkRequest
): Promise<ApiResponse<ChunkInfo>> => {
  return request.put(`/chunks/${chunkId}`, data);
};

/** 确认入库（触发向量化） */
export const vectorizeFile = (fileId: string): Promise<ApiResponse<void>> => {
  return request.post(`/files/${fileId}/vectorize`);
};

/** 提交分块结果（用于预览模式确认） */
export const submitChunks = (fileId: string, chunks: any[]): Promise<ApiResponse<void>> => {
  return request.post(`/files/${fileId}/chunks/submit`, { chunks });
};

/** 手动触发文件解析 (支持带配置) */
export const parseFile = (fileId: string, config?: { 
  chunk_mode?: string; 
  chunk_size?: number; 
  chunk_overlap?: number; 
  preview?: boolean;
  auto_vectorize?: boolean; // 新增：是否自动向量化
}): Promise<ApiResponse<void>> => {
  return request.post(`/files/${fileId}/parse`, config);
};
