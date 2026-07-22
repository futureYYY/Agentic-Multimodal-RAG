/**
 * 常量定义
 */

import type { FileStatus } from '@/types';

/** 文件状态配置 */
export const FILE_STATUS_CONFIG: Record<
  FileStatus,
  { label: string; color: string }
> = {
  pending: { label: '待解析', color: 'default' },
  parsing: { label: '解析中', color: 'processing' },
  pending_confirm: { label: '待确认', color: 'warning' },
  parsed: { label: '解析完成', color: 'success' },
  embedding: { label: '向量化中', color: 'processing' }, // 后端返回的是 embedding
  ready: { label: '已入库', color: 'success' }, // 后端返回的是 ready
  failed: { label: '失败', color: 'error' },
};

/** 支持的文件类型 */
export const SUPPORTED_FILE_TYPES = [
  '.docx',
  '.pdf',
  '.xlsx',
  '.csv',
];

/** 文件类型 MIME 映射 */
export const FILE_MIME_TYPES: Record<string, string> = {
  '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  '.pdf': 'application/pdf',
  '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  '.csv': 'text/csv',
};

/** 文件轮询间隔（毫秒） */
export const FILE_POLL_INTERVAL = 3000;

/** 默认分页大小 */
export const DEFAULT_PAGE_SIZE = 20;

/** 召回测试默认参数 */
export const DEFAULT_RECALL_PARAMS = {
  topK: 5,
  scoreThreshold: 0.5,
};
