/**
 * 系统设置页面
 */

import React, { useState, useEffect } from 'react';
import { Card, Form, Select, InputNumber, Button, Spin, message, Divider, Table, Modal, Input, Tag, Space, Tooltip } from 'antd';
import { SaveOutlined, PlusOutlined, DeleteOutlined, InfoCircleOutlined, ThunderboltOutlined, EditOutlined } from '@ant-design/icons';
import { PageHeader } from '@/components/common';
import { getSettings, updateSettings, getModelList, createCustomModel, updateCustomModel, deleteCustomModel, getCustomModels, testCustomModel } from '@/services';
import { useAppStore } from '@/stores';
import type { SystemSettings, UpdateSettingsRequest, CustomModel } from '@/types';
import styles from './Settings.module.css';

const Settings: React.FC = () => {
  const { models, setModels } = useAppStore();
  const [form] = Form.useForm();
  const [modelForm] = Form.useForm();

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testingModel, setTestingModel] = useState<string | null>(null); // 正在测试的模型 ID
  const [settings, setSettings] = useState<SystemSettings | null>(null);
  
  // 自定义模型相关状态
  const [customModels, setCustomModels] = useState<CustomModel[]>([]);
  const [isModalVisible, setIsModalVisible] = useState(false);
  const [creatingModel, setCreatingModel] = useState(false);
  const [editingModelId, setEditingModelId] = useState<string | null>(null);

  // 加载数据
  const loadData = async () => {
    setLoading(true);
    try {
      const [settingsRes, modelsRes, customModelsRes] = await Promise.all([
        getSettings(),
        getModelList(),
        getCustomModels(), // 获取自定义模型列表
      ]);
      setSettings(settingsRes.data);

      // 修复 Models 数据结构不匹配问题
      // 后端返回的是 CustomModel[]，但前端 store 期望的是 ModelInfo[]
      // CustomModel 缺少 provider 字段，且 type 字段在后端是 model_type
      const rawModels = Array.isArray(modelsRes) ? modelsRes : (modelsRes?.data || []);
      const adaptedModels = rawModels.map((m: any) => ({
        id: m.id,
        name: m.name,
        // 适配 type 字段：后端是 model_type，前端期望 type
        type: (m.model_type || m.type || 'llm').toLowerCase(), 
        // 补充缺失的 provider 字段
        provider: m.provider || 'Custom',
        description: m.description || m.model_name
      }));
      setModels(adaptedModels);
      
      // Sort models: llm > vlm > embedding > rerank
      const sortOrder: Record<string, number> = { llm: 1, vlm: 2, embedding: 3, rerank: 4 };
      // 兼容直接返回数组或 ApiResponse 结构
      const rawCustomModels = Array.isArray(customModelsRes) ? customModelsRes : (customModelsRes?.data || []);
      const sortedModels = (rawCustomModels)
        // .filter((m: CustomModel) => m.id !== 'sys_embedding') // 暂时注释掉过滤，方便调试看到系统模型
        .sort((a: CustomModel, b: CustomModel) => {
          // Normalize type to lowercase for safe comparison
          const typeA = (a.model_type || '').toLowerCase();
          const typeB = (b.model_type || '').toLowerCase();
          const orderA = sortOrder[typeA] || 99;
          const orderB = sortOrder[typeB] || 99;
          return orderA - orderB;
      });
      setCustomModels(sortedModels);
      
      form.setFieldsValue({
        defaultEmbeddingModel: settingsRes.data.defaultEmbeddingModel,
        defaultVlmModel: settingsRes.data.defaultVlmModel,
        defaultLlmModel: settingsRes.data.defaultLlmModel,
        maxConcurrency: settingsRes.data.maxConcurrency,
        chunkSize: settingsRes.data.chunkSize,
        chunkOverlap: settingsRes.data.chunkOverlap,
        maxChatHistoryRounds: settingsRes.data.maxChatHistoryRounds,
      });
    } catch (error) {
      console.error('Load settings failed:', error);
      message.error('加载设置失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  // 保存设置
  const handleSave = async (values: SystemSettings) => {
    setSaving(true);
    try {
      const request: UpdateSettingsRequest = {
        default_embedding_model: values.defaultEmbeddingModel,
        default_vlm_model: values.defaultVlmModel,
        default_llm_model: values.defaultLlmModel,
        max_concurrency: values.maxConcurrency,
        chunk_size: values.chunkSize,
        chunk_overlap: values.chunkOverlap,
        max_chat_history_rounds: values.maxChatHistoryRounds,
      };
      await updateSettings(request);
      message.success('保存成功');
    } catch (error) {
      message.error('保存失败');
    } finally {
      setSaving(false);
    }
  };

  // 保存模型（创建或更新）
  const handleSaveModel = async (values: any) => {
    setCreatingModel(true);
    try {
      if (editingModelId) {
        await updateCustomModel(editingModelId, values);
        message.success('更新模型成功');
        setIsModalVisible(false);
        modelForm.resetFields();
        setEditingModelId(null);
        loadData(); // 重新加载列表
      } else {
        const res = await createCustomModel(values);
        message.success('添加模型成功');
        setIsModalVisible(false);
        modelForm.resetFields();
        setEditingModelId(null);
        await loadData(); // 重新加载列表
        if (res && res.data) {
             handleTestModel(res.data);
        }
      }
    } catch (error) {
      message.error(editingModelId ? '更新模型失败' : '添加模型失败');
    } finally {
      setCreatingModel(false);
    }
  };

  // 打开添加模态框
  const handleOpenCreateModal = () => {
    setEditingModelId(null);
    modelForm.resetFields();
    setIsModalVisible(true);
  };

  // 打开编辑模态框
  const handleEditModel = (model: CustomModel) => {
    setEditingModelId(model.id);
    modelForm.setFieldsValue({
      name: model.name,
      model_type: model.model_type,
      base_url: model.base_url,
      api_key: model.api_key,
      model_name: model.model_name,
      context_length: model.context_length,
    });
    setIsModalVisible(true);
  };

  // 删除自定义模型
  const handleDeleteModel = (id: string) => {
    Modal.confirm({
        title: '确认删除',
        content: '确定要删除这个模型吗？此操作无法撤销。',
        okText: '确认',
        cancelText: '取消',
        onOk: async () => {
            try {
                await deleteCustomModel(id);
                message.success('删除成功');
                loadData(); // 重新加载
            } catch (error) {
                message.error('删除失败');
            }
        },
    });
  };

  // 测试自定义模型
  const handleTestModel = async (model: CustomModel) => {
    setTestingModel(model.id);
    try {
        await testCustomModel(model.id);
        message.success(`模型 ${model.name} 连接测试成功！`);
    } catch (error: any) {
        console.error("Test failed:", error);
        // 尝试提取错误信息
        const errorMsg = error.response?.data?.detail?.message || error.message || '未知错误';
        message.error(`连接测试失败: ${errorMsg}`);
    } finally {
        setTestingModel(null);
    }
  };

  // 分类模型
  const embeddingModels = models.filter((m) => m.type === 'embedding');
  const vlmModels = models.filter((m) => m.type === 'vlm');
  const llmModels = models.filter((m) => m.type === 'llm');

  const modelColumns = [
    {
      title: '显示名称',
      dataIndex: 'name',
      key: 'name',
      width: 150,
      ellipsis: true,
    },
    {
      title: '类型',
      dataIndex: 'model_type',
      key: 'model_type',
      width: 100,
      render: (type: string) => {
        let color = 'default';
        if (type === 'llm') color = 'blue';
        else if (type === 'embedding') color = 'green';
        else if (type === 'vlm') color = 'orange';
        else if (type === 'rerank') color = 'purple';
        return <Tag color={color}>{type}</Tag>;
      },
    },
    {
      title: '实际模型名',
      dataIndex: 'model_name',
      key: 'model_name',
      width: 150,
      ellipsis: true,
    },
    {
      title: 'Base URL',
      dataIndex: 'base_url',
      key: 'base_url',
      ellipsis: {
        showTitle: false,
      },
      render: (url: string) => (
        <Tooltip title={url}>
          {url}
        </Tooltip>
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 150,
      fixed: 'right' as const,
      render: (_: any, record: CustomModel) => (
        <Space>
            <Button
                type="link"
                icon={<EditOutlined />}
                onClick={() => handleEditModel(record)}
            >
                编辑
            </Button>
            <Button
                type="link"
                icon={<ThunderboltOutlined />}
                loading={testingModel === record.id}
                onClick={() => handleTestModel(record)}
            >
                测试
            </Button>
            <Button 
            type="text" 
            danger 
            icon={<DeleteOutlined />} 
            onClick={() => handleDeleteModel(record.id)}
            >
            删除
            </Button>
        </Space>
      ),
    },
  ];

  if (loading) {
    return (
      <div className={styles.loading}>
        <Spin size="large" />
      </div>
    );
  }

  return (
    <div className={styles.container}>
      <PageHeader
        title="系统设置"
        subtitle="配置系统参数和模型选项"
      />

      <div className={styles.content}>
        {/* 系统参数设置 */}
        <div className={styles.mainPanel} style={{ marginBottom: 24 }}>
          <Card 
            title="基本设置" 
            className={styles.card}
            extra={
              <Button 
                type="primary" 
                icon={<SaveOutlined />} 
                loading={saving}
                onClick={form.submit}
              >
                保存设置
              </Button>
            }
          >
            <Form
              form={form}
              layout="vertical"
              onFinish={handleSave}
            >
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: '24px' }}>
                <Form.Item
                  name="defaultLlmModel"
                  label="默认 LLM 模型"
                  tooltip="Agent 用于推理的主要模型"
                  rules={[{ required: true }]}
                >
                  <Select options={models.filter(m => m.type === 'llm').map(m => ({ label: m.name, value: m.id }))} />
                </Form.Item>

                <Form.Item
                  name="defaultEmbeddingModel"
                  label="默认 Embedding 模型"
                  tooltip="用于知识库向量化的模型"
                  rules={[{ required: true }]}
                >
                  <Select options={models.filter(m => m.type === 'embedding').map(m => ({ label: m.name, value: m.id }))} />
                </Form.Item>

                <Form.Item
                  name="defaultVlmModel"
                  label="默认 VLM 模型"
                  tooltip="用于图片理解的多模态模型"
                  rules={[{ required: true }]}
                >
                  <Select options={models.filter(m => m.type === 'vlm').map(m => ({ label: m.name, value: m.id }))} />
                </Form.Item>
                
                <Form.Item
                  name="maxChatHistoryRounds"
                  label="最大历史对话轮数"
                  tooltip="限制发送给模型的历史消息数量（1轮=1问+1答），防止上下文爆炸"
                  rules={[{ required: true }]}
                >
                   <InputNumber min={1} max={100} style={{ width: '100%' }} />
                </Form.Item>

                <Form.Item
                  name="maxConcurrency"
                  label="最大并发解析数"
                  tooltip="文件解析时的最大并发线程数"
                  rules={[{ required: true }]}
                >
                  <InputNumber min={1} max={32} style={{ width: '100%' }} />
                </Form.Item>

                <Form.Item
                  name="chunkSize"
                  label="分块大小 (Chunk Size)"
                  tooltip="文档切片时的字符长度"
                  rules={[{ required: true }]}
                >
                  <InputNumber step={128} min={128} max={2048} style={{ width: '100%' }} />
                </Form.Item>

                <Form.Item
                  name="chunkOverlap"
                  label="分块重叠 (Chunk Overlap)"
                  tooltip="相邻分块之间的重叠字符长度"
                  rules={[{ required: true }]}
                >
                  <InputNumber step={32} min={0} max={512} style={{ width: '100%' }} />
                </Form.Item>
              </div>
            </Form>
          </Card>
        </div>

        {/* 自定义模型管理 (全宽) */}
        <div className={styles.fullPanel}>
          <Card 
            title="自定义模型管理" 
            className={styles.card}
            extra={
              <Button type="primary" size="small" icon={<PlusOutlined />} onClick={handleOpenCreateModal}>
                添加模型
              </Button>
            }
          >
            <Table 
              dataSource={customModels} 
              columns={modelColumns} 
              rowKey="id"
              pagination={false}
              size="middle"
              scroll={{ x: true }}
            />
          </Card>
        </div>
      </div>

      {/* 添加/编辑模型弹窗 */}
      <Modal
        title={editingModelId ? "编辑自定义模型" : "添加自定义模型"}
        open={isModalVisible}
        onCancel={() => setIsModalVisible(false)}
        footer={null}
      >
        <Form
          form={modelForm}
          layout="vertical"
          onFinish={handleSaveModel}
        >
          <Form.Item
            name="name"
            label="显示名称"
            rules={[{ required: true, message: '请输入显示名称' }]}
            tooltip="在界面上下拉框中显示的名称"
          >
            <Input placeholder="例如: My DeepSeek" />
          </Form.Item>

          <Form.Item
            name="model_type"
            label="模型类型"
            rules={[{ required: true, message: '请选择模型类型' }]}
          >
            <Select>
              <Select.Option value="llm">大语言模型 (LLM)</Select.Option>
              <Select.Option value="embedding">向量模型 (Embedding)</Select.Option>
              <Select.Option value="vlm">视觉模型 (VLM)</Select.Option>
              <Select.Option value="rerank">重排序模型 (Rerank)</Select.Option>
            </Select>
          </Form.Item>

          <Form.Item
            noStyle
            shouldUpdate={(prevValues, currentValues) => prevValues.model_type !== currentValues.model_type}
          >
            {({ getFieldValue }) => {
              const modelType = getFieldValue('model_type');
              const isRerank = modelType === 'rerank';
              const isEmbedding = modelType === 'embedding';
              const showContextLength = isRerank || isEmbedding;

              return (
                <>
                  <Form.Item
                    name="base_url"
                    label={isRerank ? "模型存放地址 (本地路径)" : "API Base URL"}
                    rules={[{ required: true, message: isRerank ? '请输入模型本地路径' : '请输入 Base URL' }]}
                    tooltip={isRerank ? "例如: E:\\Models\\bge-reranker-v2-m3" : "例如: https://api.deepseek.com/v1"}
                  >
                    <Input placeholder={isRerank ? "E:\\Models\\bge-reranker-v2-m3" : "https://api.openai.com/v1"} />
                  </Form.Item>

                  <Form.Item
                    name="api_key"
                    label="API Key"
                    rules={[{ required: !isRerank, message: '请输入 API Key' }]}
                    tooltip={isRerank ? "本地模型可留空" : undefined}
                  >
                    <Input.Password placeholder={isRerank ? "本地模型可不填" : "sk-..."} />
                  </Form.Item>

                  <Form.Item
                    name="model_name"
                    label="实际模型名称"
                    rules={[{ required: !isRerank, message: '请输入模型名称' }]}
                    tooltip={isRerank ? "本地模型通常不使用此字段，可随意填写" : "API 调用时使用的 model 参数值，例如: deepseek-chat"}
                  >
                    <Input placeholder={isRerank ? "default" : "gpt-3.5-turbo"} />
                  </Form.Item>

                  {showContextLength && (
                    <Form.Item
                      name="context_length"
                      label="上下文长度"
                      initialValue={isEmbedding ? 8192 : 4096}
                      rules={[{ required: true, message: '请输入上下文长度' }]}
                      tooltip="Embedding 或 Rerank 模型支持的最大 Token 数量"
                    >
                      <InputNumber style={{ width: '100%' }} min={1} max={128000} />
                    </Form.Item>
                  )}
                </>
              );
            }}
          </Form.Item>

          <Form.Item>
            <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
              <Button onClick={() => setIsModalVisible(false)}>取消</Button>
              <Button type="primary" htmlType="submit" loading={creatingModel}>
                {editingModelId ? "更新" : "添加"}
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
};

export default Settings;
