/**
 * 召回测试相关 API
 */

import request from './request';
import type {
  RecallTestParams,
  RecallTestResponse,
  RetrievalLogListResponse,
  RetrievalLog,
  ApiResponse,
} from '@/types';

/** 执行召回测试 */
export const executeRecallTest = (
  kbId: string,
  params: RecallTestParams
): Promise<ApiResponse<RecallTestResponse>> => {
  // 转换参数为后端需要的格式 (snake_case)
  const requestBody = {
    query: params.query,
    top_k: params.topK,
    score_threshold: params.scoreThreshold,
    rerank_enabled: params.rerank_enabled,
    rerank_score_threshold: params.rerank_score_threshold,
    rerank_model_id: params.rerank_model_id,
    // 新增参数
    search_mode: params.search_mode || 'hybrid',
    vector_weight: params.vector_weight ?? 0.7,
    bm25_weight: params.bm25_weight ?? 0.3,
    rerank_top_k: params.rerank_top_k ?? 20,
  };
  return request.post(`/knowledge-bases/${kbId}/recall`, requestBody);
};

/** 获取检索历史记录列表 */
export const getRetrievalLogs = (params?: {
  kb_id?: string;
  page?: number;
  page_size?: number;
}): Promise<ApiResponse<RetrievalLogListResponse>> => {
  return request.get('/retrieval-logs', { params });
};

/** 获取单条检索历史记录详情 */
export const getRetrievalLogDetail = (
  logId: string
): Promise<ApiResponse<RetrievalLog>> => {
  return request.get(`/retrieval-logs/${logId}`);
};

/** 删除单条检索历史记录 */
export const deleteRetrievalLog = (
  logId: string
): Promise<ApiResponse<void>> => {
  return request.delete(`/retrieval-logs/${logId}`);
};

/** 清空检索历史记录 */
export const clearRetrievalLogs = (
  kbId?: string
): Promise<ApiResponse<void>> => {
  return request.delete('/retrieval-logs', { params: { kb_id: kbId } });
};
