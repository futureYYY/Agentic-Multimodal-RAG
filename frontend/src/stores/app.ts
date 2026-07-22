/**
 * 应用全局状态管理
 */

import { create } from 'zustand';
import type { KnowledgeBase, ModelInfo, SystemSettings } from '@/types';

interface AppState {
  // 知识库列表
  knowledgeBases: KnowledgeBase[];
  setKnowledgeBases: (kbs: KnowledgeBase[]) => void;

  // 当前选中的知识库
  currentKb: KnowledgeBase | null;
  setCurrentKb: (kb: KnowledgeBase | null) => void;

  // 模型列表
  models: ModelInfo[];
  setModels: (models: ModelInfo[]) => void;

  // 系统设置
  settings: SystemSettings | null;
  setSettings: (settings: SystemSettings) => void;

  // 全局加载状态
  globalLoading: boolean;
  setGlobalLoading: (loading: boolean) => void;
}

export const useAppStore = create<AppState>((set) => ({
  knowledgeBases: [],
  setKnowledgeBases: (kbs) => set({ knowledgeBases: kbs }),

  currentKb: null,
  setCurrentKb: (kb) => set({ currentKb: kb }),

  models: [],
  setModels: (models) => set({ models: models }),

  settings: null,
  setSettings: (settings) => set({ settings: settings }),

  globalLoading: false,
  setGlobalLoading: (loading) => set({ globalLoading: loading }),
}));
