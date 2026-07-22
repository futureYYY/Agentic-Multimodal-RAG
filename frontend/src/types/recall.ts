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
  // 新增参数
  search_mode?: 'vector' | 'fulltext' | 'hybrid';
  vector_weight?: number;
  bm25_weight?: number;
  rerank_top_k?: number;
}

/** 召回结果项 */
export interface RecallResult {
  chunkId: string;
  fileName: string;
  location: string;
  score: number;
  rerank_score?: number;
  vector_score?: number;  // 新增
  bm25_score?: number;    // 新增
  content: string;
  imageUrl?: string;
  // 结构化信息
  heading_text?: string;
  heading_level?: number;
}

/** 召回测试响应 */
export interface RecallTestResponse {
  results: RecallResult[];
  queryTime: number;
}

/** 检索历史记录 */
export interface RetrievalLog {
  id: string;
  kb_id: string;
  query: string;
  search_mode: string;
  vector_count: number;
  bm25_count: number;
  merged_count: number;
  rerank_count: number;
  final_count: number;
  latency_ms: number;
  vector_latency_ms: number;
  bm25_latency_ms: number;
  rerank_latency_ms: number;
  top_k: number;
  score_threshold: number;
  rerank_enabled: boolean;
  vector_weight: number;
  bm25_weight: number;
  results_summary: string;
  created_at: string;
}

/** 检索历史记录列表响应 */
export interface RetrievalLogListResponse {
  logs: RetrievalLog[];
  total: number;
  page: number;
  page_size: number;
}
