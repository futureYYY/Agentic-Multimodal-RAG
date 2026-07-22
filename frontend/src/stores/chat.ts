/**
 * 对话状态管理
 */

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import type { ChatMessage, ChatSession } from '@/types';

interface ChatState {
  // 当前会话
  currentSession: ChatSession | null;
  setCurrentSession: (session: ChatSession | null) => void;

  // 消息列表
  messages: ChatMessage[];
  addMessage: (message: ChatMessage) => void;
  updateMessage: (id: string, updates: Partial<ChatMessage>) => void;
  clearMessages: () => void;

  // 独立的消息状态 (Agent 模式和普通模式)
  agentMessages: ChatMessage[];
  normalMessages: ChatMessage[];
  setAgentMessages: (messages: ChatMessage[] | ((prev: ChatMessage[]) => ChatMessage[])) => void;
  setNormalMessages: (messages: ChatMessage[] | ((prev: ChatMessage[]) => ChatMessage[])) => void;

  // 选中的知识库 IDs
  selectedKbIds: string[];
  setSelectedKbIds: (ids: string[]) => void;

  // 选中的 LLM 模型
  selectedModel: string;
  setSelectedModel: (model: string) => void;

  // 是否正在生成 (分离状态)
  isAgentGenerating: boolean;
  isNormalGenerating: boolean;
  setAgentGenerating: (generating: boolean) => void;
  setNormalGenerating: (generating: boolean) => void;

  // 兼容旧代码的 getter/setter (如果需要，或者逐步迁移)
  // isGenerating: boolean; // Deprecated
  // setIsGenerating: (generating: boolean) => void; // Deprecated

  // 当前流式 controller (分离状态)
  agentAbortController: (() => void) | null;
  normalAbortController: (() => void) | null;
  setAgentAbortController: (controller: (() => void) | null) => void;
  setNormalAbortController: (controller: (() => void) | null) => void;

  // Deprecated controllers
  // abortController: (() => void) | null;
  // setAbortController: (controller: (() => void) | null) => void;
}

export const useChatStore = create<ChatState>()(
  persist(
    (set, get) => ({
      currentSession: null,
      setCurrentSession: (session) => set({ currentSession: session }),

      messages: [],
      addMessage: (message) =>
        set((state) => ({ messages: [...state.messages, message] })),
      updateMessage: (id, updates) =>
        set((state) => ({
          messages: state.messages.map((msg) =>
            msg.id === id ? { ...msg, ...updates } : msg
          ),
        })),
      clearMessages: () => set({ messages: [] }),

      agentMessages: [],
      normalMessages: [],
      setAgentMessages: (messages) => 
        set((state) => ({ 
          agentMessages: typeof messages === 'function' ? messages(state.agentMessages) : messages 
        })),
      setNormalMessages: (messages) => 
        set((state) => ({ 
          normalMessages: typeof messages === 'function' ? messages(state.normalMessages) : messages 
        })),

      selectedKbIds: [],
      setSelectedKbIds: (ids) => set({ selectedKbIds: ids }),

      selectedModel: '',
      setSelectedModel: (model) => set({ selectedModel: model }),

      isAgentGenerating: false,
      isNormalGenerating: false,
      setAgentGenerating: (generating) => set({ isAgentGenerating: generating }),
      setNormalGenerating: (generating) => set({ isNormalGenerating: generating }),

      agentAbortController: null,
      normalAbortController: null,
      setAgentAbortController: (controller) => set({ agentAbortController: controller }),
      setNormalAbortController: (controller) => set({ normalAbortController: controller }),
    }),
    {
      name: 'chat-storage',
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        currentSession: state.currentSession,
        messages: state.messages,
        agentMessages: state.agentMessages,
        normalMessages: state.normalMessages,
        selectedKbIds: state.selectedKbIds,
        selectedModel: state.selectedModel,
      }),
    }
  )
);
