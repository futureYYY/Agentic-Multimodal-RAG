/**
 * 通用类型定义
 */

/** API 响应基础结构 */
export interface ApiResponse<T = unknown> {
  code: number;
  message: string;
  data: T;
}

/** 分页参数 */
export interface PaginationParams {
  page: number;
  pageSize: number;
}

/** 分页响应 */
export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
}

/** 加载状态 */
export type LoadingState = 'idle' | 'loading' | 'success' | 'error';

/** 操作结果 */
export interface OperationResult {
  success: boolean;
  message: string;
}
