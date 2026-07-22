/**
 * 知识库相关 API
 */

import request from './request';
import type {
  KnowledgeBase,
  KnowledgeBaseListResponse,
  CreateKnowledgeBaseRequest,
  ApiResponse,
} from '@/types';

/** 获取知识库列表 */
export const getKnowledgeBaseList = (): Promise<ApiResponse<KnowledgeBaseListResponse>> => {
  return request.get('/knowledge-bases');
};

/** 获取知识库详情 */
export const getKnowledgeBaseDetail = (id: string): Promise<ApiResponse<KnowledgeBase>> => {
  return request.get(`/knowledge-bases/${id}`);
};

/** 创建知识库 */
export const createKnowledgeBase = (
  data: CreateKnowledgeBaseRequest
): Promise<ApiResponse<KnowledgeBase>> => {
  return request.post('/knowledge-bases', data);
};

/** 更新知识库 */
export const updateKnowledgeBase = (
  id: string,
  data: Partial<CreateKnowledgeBaseRequest>
): Promise<ApiResponse<KnowledgeBase>> => {
  return request.put(`/knowledge-bases/${id}`, data);
};

/** 删除知识库（移入回收站） */
export const deleteKnowledgeBase = (id: string): Promise<ApiResponse<void>> => {
  return request.delete(`/knowledge-bases/${id}`);
};

/** 获取回收站知识库列表 */
export const getDeletedKnowledgeBaseList = (): Promise<ApiResponse<KnowledgeBaseListResponse>> => {
  return request.get('/knowledge-bases/recycle-bin');
};

/** 从回收站恢复知识库 */
export const restoreKnowledgeBase = (id: string): Promise<ApiResponse<void>> => {
  return request.post(`/knowledge-bases/${id}/restore`);
};

/** 永久删除知识库 */
export const permanentDeleteKnowledgeBase = (id: string): Promise<ApiResponse<void>> => {
  return request.delete(`/knowledge-bases/${id}/permanent`);
};
