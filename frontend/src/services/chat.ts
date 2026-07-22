/**
 * 对话相关 API
 */

import request from './request';
import type {
  RewriteRequest,
  RewriteResponse,
  ChatRequest,
  SSEEventType,
  AgentThoughtData,
  RagResultData,
  AnswerChunkData,
  DoneData,
  ApiResponse,
} from '@/types';

/** 问题改写 */
export const rewriteQuery = (data: RewriteRequest): Promise<ApiResponse<RewriteResponse>> => {
  return request.post('/chat/rewrite', data);
};

/** SSE 事件处理器类型 */
export interface SSEEventHandlers {
  onAgentThought?: (data: AgentThoughtData) => void;
  onRagResult?: (data: RagResultData) => void;
  onAnswerChunk?: (data: AnswerChunkData) => void;
  onDone?: (data: DoneData) => void;
  onError?: (error: Error) => void;
}

/** 创建 SSE 对话连接 */
export const createChatStream = (
  data: ChatRequest,
  handlers: SSEEventHandlers
): { abort: () => void } => {
  const controller = new AbortController();

  console.log('Chat Request Payload:', JSON.stringify(data, null, 2));

  fetch('/api/v1/chat/completions', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(data),
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        const errorText = await response.text().catch(() => '');
        console.error('Fetch failed:', response.status, response.statusText, errorText);
        throw new Error(`HTTP error! status: ${response.status} ${response.statusText} - ${errorText.slice(0, 100)}`);
      }

      const reader = response.body?.getReader();
      if (!reader) {
        throw new Error('No response body');
      }

      const decoder = new TextDecoder();
      let buffer = '';
      let currentEventType: SSEEventType | null = null;
      let isDone = false;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('event:')) {
            currentEventType = line.slice(6).trim() as SSEEventType;
            continue;
          }

          if (line.startsWith('data:')) {
            const dataStr = line.slice(5).trim();
            if (!dataStr) continue;

            try {
              const eventData = JSON.parse(dataStr);

              // 如果有明确的 eventType，优先处理
              if (currentEventType === 'error') {
                 throw new Error(eventData.error || eventData.message || 'Unknown error');
              }

              // 根据数据结构或 eventType 判断事件类型
              if (currentEventType === 'thought' || 'step' in eventData) {
                handlers.onAgentThought?.(eventData as AgentThoughtData);
              } else if (currentEventType === 'rag' || 'citations' in eventData) {
                handlers.onRagResult?.(eventData as RagResultData);
              } else if (currentEventType === 'answer' || ('content' in eventData && typeof eventData.content === 'string')) {
                handlers.onAnswerChunk?.(eventData as AnswerChunkData);
              } else if (currentEventType === 'done' || 'usage' in eventData || Object.keys(eventData).length === 0) {
                handlers.onDone?.(eventData as DoneData);
                isDone = true;
              }
              
              // 重置 eventType (通常 SSE 是一对 event/data)
              currentEventType = null; 
            } catch (e: any) {
              console.error('Failed to parse SSE data:', e);
              // 如果是业务错误，传递给 onError
              if (currentEventType === 'error' || (e.message && e.message !== 'Unexpected end of JSON input')) {
                  handlers.onError?.(e);
                  return; // 停止处理
              }
            }
          }
        }
      }
      
      // 如果流结束但没有收到 done 事件，手动触发 done
      if (!isDone) {
        handlers.onDone?.({} as DoneData);
      }
    })
    .catch((error) => {
      if (error.name !== 'AbortError') {
        handlers.onError?.(error);
      }
    });

  return {
    abort: () => controller.abort(),
  };
};
