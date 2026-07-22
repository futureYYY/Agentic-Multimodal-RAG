/**
 * 召回测试相关 API
 */

import request from './request';
import type {
  RecallTestParams,
  RecallTestResponse,
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
  };
  return request.post(`/knowledge-bases/${kbId}/recall`, requestBody);
};
