import React, { useState } from 'react';
import { Modal, Form, Radio, InputNumber, Input, Slider, Button, Space, Divider, Typography } from 'antd';
import { SettingOutlined, EyeOutlined, RocketOutlined } from '@ant-design/icons';

const { Text } = Typography;

interface ParseModalProps {
  open: boolean;
  onCancel: () => void;
  onPreview: (config: ParseConfig) => void;
  onDirectProcess: (config: ParseConfig) => void;
  loading?: boolean;
}

export interface ParseConfig {
  chunk_mode: 'custom' | 'no_split';
  chunk_size?: number;
  chunk_overlap?: number;
  separator?: string;
}

const ParseModal: React.FC<ParseModalProps> = ({
  open,
  onCancel,
  onPreview,
  onDirectProcess,
  loading = false,
}) => {
  const [form] = Form.useForm();
  const [chunkMode, setChunkMode] = useState<'custom' | 'no_split'>('custom');

  const handleValuesChange = (changedValues: any) => {
    if (changedValues.chunk_mode) {
      setChunkMode(changedValues.chunk_mode);
    }
  };

  const handlePreview = async () => {
    try {
      const values = await form.validateFields();
      onPreview(values);
    } catch (error) {
      // validation failed
    }
  };

  const handleDirectProcess = async () => {
    try {
      const values = await form.validateFields();
      onDirectProcess(values);
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
          separator: '\\n\\n',
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
            <Radio.Button value="no_split">不切分</Radio.Button>
          </Radio.Group>
        </Form.Item>

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
