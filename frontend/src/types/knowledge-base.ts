/**
 * 知识库相关类型定义
 */

/** 知识库状态 */
export type KnowledgeBaseStatus = 'active' | 'inactive' | 'processing';

/** 知识库信息 */
export interface KnowledgeBase {
  id: string;
  name: string;
  description?: string;
  embedding_model: string;
  vlm_model: string;
  chunk_count: number;
  files_count?: number;
  created_at?: string;
  updated_at: string;
  status?: KnowledgeBaseStatus;
  is_deleted?: boolean;
}

/** 创建知识库请求 */
export interface CreateKnowledgeBaseRequest {
  name: string;
  description?: string;
  embedding_model: string;
  vlm_model: string;
}

/** 知识库列表响应 */
export interface KnowledgeBaseListResponse {
  items: KnowledgeBase[];
  total: number;
}
