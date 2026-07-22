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

        const chunk = decoder.decode(value, { stream: true });
        console.log('Received Chunk:', chunk); // Debug logging
        buffer += chunk;
        
        const lines = buffer.split('\n');
        // 保留最后一个可能不完整的行在 buffer 中
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmedLine = line.trim();
          if (!trimmedLine) continue; // Skip empty lines

          if (trimmedLine.startsWith('event:')) {
            currentEventType = trimmedLine.slice(6).trim() as SSEEventType;
            continue;
          }

          if (trimmedLine.startsWith('data:')) {
            const dataStr = trimmedLine.slice(5).trim();
            if (!dataStr) continue;

            try {
              const eventData = JSON.parse(dataStr);
              console.log('Parsed Event:', currentEventType, eventData); // Debug logging

              // 尝试推断事件类型 (如果没有显式 eventType)
              let inferredType = currentEventType;
              if (!inferredType) {
                  if ('step' in eventData) inferredType = 'agent_thought';
                  else if ('citations' in eventData) inferredType = 'rag_result';
                  else if ('content' in eventData) inferredType = 'answer_chunk';
                  else if ('usage' in eventData) inferredType = 'done';
                  else if ('error' in eventData) inferredType = 'error';
              }

              // 处理事件
              if (inferredType === 'agent_thought' || inferredType === 'thought') {
                handlers.onAgentThought?.(eventData as AgentThoughtData);
              } else if (inferredType === 'rag_result' || inferredType === 'rag') {
                handlers.onRagResult?.(eventData as RagResultData);
              } else if (inferredType === 'answer_chunk' || inferredType === 'answer') {
                handlers.onAnswerChunk?.(eventData as AnswerChunkData);
              } else if (inferredType === 'done') {
                handlers.onDone?.(eventData as DoneData);
                isDone = true;
              } else if (inferredType === 'error') {
                 // 主动抛出业务错误
                 throw new Error(eventData.error || eventData.message || 'Unknown error');
              }
              
              // 重置 eventType
              currentEventType = null; 
            } catch (e: any) {
              console.error('Failed to parse SSE data:', e, 'Raw:', dataStr);
              // 如果是业务错误，传递给 onError
              if (currentEventType === 'error' || (e.message && e.message !== 'Unexpected end of JSON input')) {
                  handlers.onError?.(e);
                  // 不要 return，继续处理后续行
              }
            }
          }
          // 忽略不以 event: 或 data: 开头的行（可能是注释或 keep-alive）
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
