import React, { useState } from 'react';
import { Modal, Form, Radio, InputNumber, Input, Slider, Button, Space, Divider, Typography, Select, Alert, Tooltip } from 'antd';
import { SettingOutlined, EyeOutlined, RocketOutlined, InfoCircleOutlined, QuestionCircleOutlined } from '@ant-design/icons';
import type { ModelInfo } from '@/types';

const { Text } = Typography;

interface ParseModalProps {
  open: boolean;
  onCancel: () => void;
  onPreview: (config: ParseConfig) => void;
  onDirectProcess: (config: ParseConfig) => void;
  loading?: boolean;
  models?: ModelInfo[]; // 可选模型列表
}

export interface ParseConfig {
  chunk_mode: 'custom' | 'no_split' | 'structure' | 'semantic';  // 新增 structure 模式
  chunk_size?: number;
  chunk_overlap?: number;
  separator?: string;
  // structure 模式专用
  heading_level?: 1 | 2 | 3;  // 按哪个级别拆分
  embedding_model?: string;   // 语义切分模型
  vector_model?: string;      // 向量化模型
}

const ParseModal: React.FC<ParseModalProps> = ({
  open,
  onCancel,
  onPreview,
  onDirectProcess,
  loading = false,
  models = [],
}) => {
  const [form] = Form.useForm();
  const [chunkMode, setChunkMode] = useState<'custom' | 'no_split' | 'structure' | 'semantic'>('custom');

  // 过滤 embedding 模型，并排除 sys_embedding
  const embeddingModels = models.filter(m => 
    (m.type === 'embedding' || m.model_type === 'embedding') && m.id !== 'sys_embedding'
  );

  const handleValuesChange = (changedValues: any) => {
    if (changedValues.chunk_mode) {
      setChunkMode(changedValues.chunk_mode);
    }
  };

  const handlePreview = async () => {
    try {
      const values = await form.validateFields();
      const config = {
        ...values,
        vector_model: values.vector_model || values.embedding_model,
      };
      onPreview(config);
    } catch (error) {
      // validation failed
    }
  };

  const handleDirectProcess = async () => {
    try {
      const values = await form.validateFields();
      const config = {
        ...values,
        vector_model: values.vector_model || values.embedding_model,
      };
      onDirectProcess(config);
    } catch (error) {
      // validation failed
    }
  };

  return (
    <Modal
      title={
        <Space>
          <SettingOutlined />
          <span>解析设置</span>
        </Space>
      }
      open={open}
      onCancel={onCancel}
      footer={null}
      destroyOnClose
      width={600}
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          chunk_mode: 'custom',
          chunk_size: 500,
          chunk_overlap: 50,
          separator: '\n\n',
          heading_level: 2,
        }}
        onValuesChange={handleValuesChange}
      >
        <Form.Item
          name="chunk_mode"
          label="切分方式"
          rules={[{ required: true }]}
        >
          <Radio.Group>
            <Radio.Button value="custom">自定义切分</Radio.Button>
            <Radio.Button value="structure">结构化切分</Radio.Button>
            <Radio.Button value="semantic">语义切分</Radio.Button>
            <Radio.Button value="no_split">不切分</Radio.Button>
          </Radio.Group>
        </Form.Item>

        {/* 结构化切分模式 */}
        {chunkMode === 'structure' && (
          <>
            <Alert
              message="结构化切分模式"
              description={
                <div>
                  <p style={{ marginBottom: 8 }}>按文档标题层级拆分，适用于有明确章节结构的文档。</p>
                  <Text type="secondary">
                    <InfoCircleOutlined style={{ marginRight: 4 }} />
                    支持：PDF（通过字体大小识别）、Word（Heading样式）、TXT（Markdown # 语法）
                  </Text>
                </div>
              }
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
            />

            <Form.Item
              name="heading_level"
              label="按标题级别拆分"
              tooltip="选择按哪个级别的标题进行拆分，每个该级别标题及其下属内容将作为一个独立的块"
            >
              <Select>
                <Select.Option value={1}>一级标题 (H1)</Select.Option>
                <Select.Option value={2}>二级标题 (H2) - 推荐</Select.Option>
                <Select.Option value={3}>三级标题 (H3)</Select.Option>
              </Select>
            </Form.Item>

            <Form.Item label="超长内容处理" style={{ marginBottom: 8 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                如果单个标题下的内容超过最大块大小，将自动进行语义切分。
              </Text>
            </Form.Item>

            {/* 隐藏最大块大小设置，使用默认值 1000 */}
            <Form.Item name="chunk_size" noStyle initialValue={1000}>
               <InputNumber style={{ display: 'none' }} />
            </Form.Item>
            
            {/* 结构化切分模式下隐藏重叠大小设置 */}
            <Form.Item name="chunk_overlap" noStyle initialValue={0}>
               <InputNumber style={{ display: 'none' }} />
            </Form.Item>

          </>
        )}

        {/* 语义切分模式 */}
        {chunkMode === 'semantic' && (
          <>
            <Alert
              message="语义切分模式"
              description={
                <div>
                  <p style={{ marginBottom: 8 }}>智能识别语义转折点，自动进行最优分块。</p>
                  <Text type="secondary">
                    <InfoCircleOutlined style={{ marginRight: 4 }} />
                    忽略文档结构，根据上下文语义相似度进行切分。最大块大小将自动适配所选 Embedding 模型的上下文长度。
                  </Text>
                </div>
              }
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
            />
            
            {/* 隐藏最大块大小设置，使用默认值 1000 */}
            <Form.Item name="chunk_size" noStyle initialValue={1000}>
               <InputNumber style={{ display: 'none' }} />
            </Form.Item>
            
            {/* 隐藏重叠大小设置，使用默认值 0 */}
            <Form.Item name="chunk_overlap" noStyle initialValue={0}>
               <InputNumber style={{ display: 'none' }} />
            </Form.Item>
          </>
        )}

        {/* 自定义切分模式 */}
        {chunkMode === 'custom' && (
          <>
            <Form.Item
              name="separator"
              label="分隔符"
              tooltip="用于切分文本的字符，默认为双换行符"
            >
              <Input placeholder="\n\n" />
            </Form.Item>

            <Form.Item label="切分参数">
              <Space direction="vertical" style={{ width: '100%' }}>
                <Text type="secondary">块大小 (Chunk Size)</Text>
                <Form.Item name="chunk_size" noStyle>
                  <Slider
                    min={100}
                    max={2000}
                    marks={{ 100: '100', 500: '500', 1000: '1000', 2000: '2000' }}
                  />
                </Form.Item>
                <Form.Item name="chunk_size" noStyle>
                   <InputNumber min={100} max={2000} style={{ width: '100%' }} />
                </Form.Item>

                <Divider style={{ margin: '12px 0' }} />

                <Text type="secondary">重叠大小 (Overlap)</Text>
                <Form.Item name="chunk_overlap" noStyle>
                  <Slider
                    min={0}
                    max={500}
                    marks={{ 0: '0', 50: '50', 100: '100', 500: '500' }}
                  />
                </Form.Item>
                <Form.Item name="chunk_overlap" noStyle>
                   <InputNumber min={0} max={500} style={{ width: '100%' }} />
                </Form.Item>
              </Space>
            </Form.Item>
          </>
        )}

        {/* 不切分模式 */}
        {chunkMode === 'no_split' && (
          <Alert
            message="不切分模式"
            description="整个文档/页面作为一个块，适用于较短的文档或需要保持完整上下文的场景。"
            type="warning"
            showIcon
            style={{ marginBottom: 16 }}
          />
        )}

        <Divider orientation="left" plain>模型配置</Divider>

        <Form.Item
          name="embedding_model"
          label={
            <Space>
               <span>Embedding 模型</span>
               <Tooltip title="用于文本向量化入库。在结构化/语义切分模式下，也将用于计算语义相似度。">
                 <QuestionCircleOutlined />
               </Tooltip>
            </Space>
          }
          rules={[{ required: true, message: '请选择 Embedding 模型' }]}
        >
           <Select placeholder="请选择 Embedding 模型">
              {embeddingModels.map(m => (
                <Select.Option key={m.id} value={m.id}>{m.name}</Select.Option>
              ))}
           </Select>
        </Form.Item>

        <Divider />

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 12 }}>
          <Button onClick={onCancel}>取消</Button>
          <Button
            icon={<EyeOutlined />}
            onClick={handlePreview}
            loading={loading}
          >
            切分预览
          </Button>
          <Button
            type="primary"
            icon={<RocketOutlined />}
            onClick={handleDirectProcess}
            loading={loading}
          >
            处理并入库
          </Button>
        </div>
      </Form>
    </Modal>
  );
};

export default ParseModal;
