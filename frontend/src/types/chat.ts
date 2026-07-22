/**
 * 对话相关类型定义
 */

/** 消息角色 */
export type MessageRole = 'user' | 'assistant' | 'system';

/** 聊天消息 */
export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  citations?: Citation[];
  original_citations?: Citation[]; // 初排结果
  agentSteps?: AgentStep[];
  timestamp: string;
  isStreaming?: boolean;
  error?: string;
}

/** 引用信息 */
export interface Citation {
  fileName: string;
  location: string;
  score: number;
  rerank_score?: number;
  content?: string;
  kb_name?: string;
  kb_id?: string;
  chunk_id?: string;
  fileId?: string;
  image_path?: string;
}

/** Agent 思考步骤类型 */
export type AgentStepType = 'thinking' | 'decision' | 'action' | 'response';

/** Agent 思考步骤 */
export interface AgentStep {
  type: AgentStepType;
  content: string;
  timestamp: string;
}

/** 对话会话 */
export interface ChatSession {
  id: string;
  title: string;
  kbIds: string[];
  messages: ChatMessage[];
  createdAt: string;
  updatedAt: string;
}

/** 对话请求 */
export interface ChatRequest {
  messages: Array<{ role: MessageRole; content: string }>;
  kb_ids: string[];
  stream: boolean;
  use_rewrite?: boolean;
  mode?: 'normal' | 'agent';
  top_k?: number;
  score_threshold?: number;
  model_id?: string;
  rerank_enabled?: boolean;
  rerank_score_threshold?: number;
  rerank_model_id?: string;
}

/** 问题改写请求 */
export interface RewriteRequest {
  query: string;
}

/** 问题改写响应 */
export interface RewriteResponse {
  rewritten_query: string;
}

/** SSE 事件类型 */
export type SSEEventType = 'agent_thought' | 'rag_result' | 'answer_chunk' | 'done' | 'error';

/** SSE Agent思考事件数据 */
export interface AgentThoughtData {
  step: AgentStepType;
  content: string;
}

/** SSE RAG结果事件数据 */
export interface RagResultData {
  citations: Citation[];
  original_citations?: Citation[];
}

/** SSE 回答块事件数据 */
export interface AnswerChunkData {
  content: string;
}

/** SSE 完成事件数据 */
export interface DoneData {
  usage?: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
}
