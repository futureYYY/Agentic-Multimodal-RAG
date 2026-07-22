/**
 * 系统设置相关 API
 */

import request from './request';
import type {
  ModelInfo,
  SystemSettings,
  UpdateSettingsRequest,
  ApiResponse,
  CustomModel,
  CustomModelCreate,
  CustomModelUpdate,
} from '@/types';

/** 获取可用模型列表 */
export const getModelList = (): Promise<ApiResponse<ModelInfo[]>> => {
  return request.get('/models');
};

/** 获取系统设置 */
export const getSettings = (): Promise<ApiResponse<SystemSettings>> => {
  return request.get('/settings');
};

/** 更新系统设置 */
export const updateSettings = (
  data: UpdateSettingsRequest
): Promise<ApiResponse<SystemSettings>> => {
  return request.put('/settings', data);
};

// ==================== 自定义模型管理 ====================

/** 获取自定义模型列表 */
export const getCustomModels = (): Promise<ApiResponse<CustomModel[]>> => {
  return request.get('/settings/models');
};

/** 创建自定义模型 */
export const createCustomModel = (
  data: CustomModelCreate
): Promise<ApiResponse<CustomModel>> => {
  return request.post('/settings/models', data);
};

/** 更新自定义模型 */
export const updateCustomModel = (
  modelId: string,
  data: CustomModelUpdate
): Promise<ApiResponse<CustomModel>> => {
  return request.put(`/settings/models/${modelId}`, data);
};

/** 删除自定义模型 */
export const deleteCustomModel = (modelId: string): Promise<ApiResponse<void>> => {
  return request.delete(`/settings/models/${modelId}`);
};

/** 测试自定义模型连接 */
export const testCustomModel = (modelId: string): Promise<ApiResponse<any>> => {
    return request.post(`/settings/models/${modelId}/test`);
};
