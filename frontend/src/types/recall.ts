/**
 * 召回测试相关类型定义
 */

/** 召回测试参数 */
export interface RecallTestParams {
  query: string;
  topK: number;
  scoreThreshold: number;
  rerank_enabled?: boolean;
  rerank_score_threshold?: number;
  rerank_model_id?: string;
}

/** 召回结果项 */
export interface RecallResult {
  chunkId: string;
  fileName: string;
  location: string;
  score: number;
  rerank_score?: number;
  content: string;
  imageUrl?: string;
}

/** 召回测试响应 */
export interface RecallTestResponse {
  results: RecallResult[];
  queryTime: number;
}
