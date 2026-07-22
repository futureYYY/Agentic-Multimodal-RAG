/**
 * 知识库列表页面
 */

import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Card, Button, Row, Col, Spin, message, Modal, Form, Input, Select, Table, Space, Popconfirm, Tooltip, Tag } from 'antd';
import {
  PlusOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  ClockCircleOutlined,
  RestOutlined,
  DeleteOutlined,
  ExclamationCircleOutlined,
  UndoOutlined,
} from '@ant-design/icons';
import { PageHeader, EmptyState } from '@/components/common';
import {
  getKnowledgeBaseList,
  createKnowledgeBase,
  getModelList,
  getDeletedKnowledgeBaseList,
  deleteKnowledgeBase,
  restoreKnowledgeBase,
  permanentDeleteKnowledgeBase,
} from '@/services';
import { useAppStore } from '@/stores';
import { formatRelativeTime, formatNumber } from '@/utils';
import type { KnowledgeBase, ModelInfo, CreateKnowledgeBaseRequest } from '@/types';
import styles from './List.module.css';

const KnowledgeBaseList: React.FC = () => {
  const navigate = useNavigate();
  const { knowledgeBases, setKnowledgeBases, models, setModels } = useAppStore();

  const [loading, setLoading] = useState(false);
  const [createModalVisible, setCreateModalVisible] = useState(false);
  const [createLoading, setCreateLoading] = useState(false);
  
  // 回收站相关状态
  const [recycleBinVisible, setRecycleBinVisible] = useState(false);
  const [deletedKbs, setDeletedKbs] = useState<KnowledgeBase[]>([]);
  const [recycleLoading, setRecycleLoading] = useState(false);

  const [form] = Form.useForm();

  // 加载知识库列表
  const loadKnowledgeBases = async () => {
    setLoading(true);
    try {
      const response = await getKnowledgeBaseList();
      // 后端直接返回数组，或者在 data 字段中
      const data = response && Array.isArray(response) ? response : (response as any)?.data || [];
      setKnowledgeBases(Array.isArray(data) ? data : []);
    } catch (error) {
      message.error('加载知识库列表失败');
    } finally {
      setLoading(false);
    }
  };

  // 加载回收站列表
  const loadDeletedKnowledgeBases = async () => {
    setRecycleLoading(true);
    try {
      const response = await getDeletedKnowledgeBaseList();
      const data = response && Array.isArray(response) ? response : (response as any)?.data || [];
      setDeletedKbs(Array.isArray(data) ? data : []);
    } catch (error) {
      message.error('加载回收站列表失败');
    } finally {
      setRecycleLoading(false);
    }
  };

  // 恢复知识库
  const handleRestore = async (id: string) => {
    try {
      await restoreKnowledgeBase(id);
      message.success('恢复成功');
      loadDeletedKnowledgeBases();
      loadKnowledgeBases();
    } catch (error) {
      message.error('恢复失败');
    }
  };

  // 永久删除知识库
  const handlePermanentDelete = async (id: string) => {
    try {
      await permanentDeleteKnowledgeBase(id);
      message.success('永久删除成功');
      loadDeletedKnowledgeBases();
    } catch (error) {
      message.error('删除失败');
    }
  };

  // 移入回收站
  const handleDelete = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation(); // 阻止卡片点击跳转
    Modal.confirm({
      title: '移入回收站',
      icon: <ExclamationCircleOutlined />,
      content: '此操作会将知识库移入回收站，您可以稍后恢复。',
      okText: '移入回收站',
      cancelText: '取消',
      onOk: async () => {
        try {
          await deleteKnowledgeBase(id);
          message.success('已移入回收站');
          loadKnowledgeBases();
        } catch (error) {
          message.error('操作失败');
        }
      },
    });
  };

  // 加载模型列表
  const loadModels = async () => {
    try {
      const response = await getModelList();
      // 后端直接返回数组
      const data = Array.isArray(response) ? response : (response as any).data || [];
      setModels(data);
    } catch (error) {
      console.error('加载模型列表失败:', error);
    }
  };

  useEffect(() => {
    loadKnowledgeBases();
    loadModels();
  }, []);

  // 处理创建知识库
  const handleCreate = async (values: CreateKnowledgeBaseRequest) => {
    setCreateLoading(true);
    try {
      await createKnowledgeBase(values);
      message.success('创建成功');
      setCreateModalVisible(false);
      form.resetFields();
      loadKnowledgeBases();
    } catch (error: any) {
      const errorMsg = error.response?.data?.message || '创建失败换个知识库名称试试？';
      if (errorMsg.includes('exists') || errorMsg.includes('已存在')) {
        message.error('创建失败换个知识库名称试试？');
      } else {
        message.error(errorMsg);
      }
    } finally {
      setCreateLoading(false);
    }
  };

  // 渲染知识库卡片
  const renderKnowledgeBaseCard = (kb: KnowledgeBase) => {
    // 获取 Embedding 模型名称
    const embeddingModelId = kb.embedding_model;
    const embeddingModelName = models.find(m => m.id === embeddingModelId)?.name || embeddingModelId || '-';

    return (
      <Col xs={24} sm={12} lg={12} xl={8} key={kb.id}>
        <Card
          className={styles.kbCard}
          hoverable
          onClick={() => navigate(`/kb/${kb.id}`)}
          actions={[
            <Tooltip title="移入回收站" key="delete">
              <DeleteOutlined
                onClick={(e) => handleDelete(kb.id, e)}
                className={styles.deleteIcon}
              />
            </Tooltip>,
          ]}
        >
          <div className={styles.cardIcon}>
            <DatabaseOutlined />
          </div>
          <div className={styles.cardContent}>
            <h3 className={styles.cardTitle}>{kb.name}</h3>
            {kb.description && (
              <p className={styles.cardDescription}>{kb.description}</p>
            )}
            <div className={styles.cardMeta}>
              <span className={styles.metaItem}>
                <FileTextOutlined />
                {formatNumber(kb.chunk_count || 0)} 个分块
              </span>
              <span className={styles.metaItem}>
                <ClockCircleOutlined />
                {kb.updated_at ? formatRelativeTime(kb.updated_at) : '-'}
              </span>
            </div>
            <div className={styles.cardModelInfo} style={{ marginTop: 8, fontSize: 12, color: '#888' }}>
                <Tag style={{ marginRight: 0 }}>{embeddingModelName}</Tag>
            </div>
          </div>
        </Card>
      </Col>
    );
  };

  // 获取 embedding 模型列表
  const embeddingModels = models.filter((m) => m.type?.toLowerCase() === 'embedding');
  const vlmModels = models.filter((m) => m.type === 'vlm');

  return (
    <div className={styles.container}>
      <PageHeader
        title="知识库管理"
        subtitle="管理您的私有知识库，上传文档并构建智能问答系统"
        extra={
          <Space>
            <Button
              icon={<RestOutlined />}
              onClick={() => {
                setRecycleBinVisible(true);
                loadDeletedKnowledgeBases();
              }}
            >
              回收站
            </Button>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => setCreateModalVisible(true)}
            >
              新建知识库
            </Button>
          </Space>
        }
      />

      {loading ? (
        <div className={styles.loading}>
          <Spin size="large" />
        </div>
      ) : (knowledgeBases || []).length === 0 ? (
        <EmptyState
          title="暂无知识库"
          description="创建您的第一个知识库，开始构建智能问答系统"
        />
      ) : (
        <Row gutter={[16, 16]}>{(knowledgeBases || []).map(renderKnowledgeBaseCard)}</Row>
      )}

      {/* 创建知识库弹窗 */}
      <Modal
        title="新建知识库"
        open={createModalVisible}
        onCancel={() => {
          setCreateModalVisible(false);
          form.resetFields();
        }}
        footer={null}
        destroyOnClose
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={handleCreate}
          className={styles.createForm}
        >
          <Form.Item
            name="name"
            label="知识库名称"
            rules={[{ required: true, message: '请输入知识库名称' }]}
          >
            <Input placeholder="请输入知识库名称" maxLength={50} />
          </Form.Item>

          <Form.Item name="description" label="描述">
            <Input.TextArea
              placeholder="请输入知识库描述（选填）"
              rows={3}
              maxLength={200}
            />
          </Form.Item>

          <Form.Item
            name="embedding_model"
            label="Embedding 模型"
            rules={[{ required: true, message: '请选择 Embedding 模型' }]}
            tooltip="用于文本向量化的模型，创建后不可更改"
          >
            <Select placeholder="请选择 Embedding 模型">
              {embeddingModels.map((model) => (
                <Select.Option key={model.id} value={model.id}>
                  {model.name}
                </Select.Option>
              ))}
            </Select>
          </Form.Item>

          <Form.Item
            name="vlm_model"
            label="VLM 模型"
            hidden
            initialValue="default"
          >
             <Input />
          </Form.Item>

          <Form.Item className={styles.formActions}>
            <Button
              onClick={() => {
                setCreateModalVisible(false);
                form.resetFields();
              }}
            >
              取消
            </Button>
            <Button type="primary" htmlType="submit" loading={createLoading}>
              创建
            </Button>
          </Form.Item>
        </Form>
      </Modal>

      {/* 回收站弹窗 */}
      <Modal
        title={
          <Space>
            <RestOutlined />
            <span>回收站</span>
          </Space>
        }
        open={recycleBinVisible}
        onCancel={() => setRecycleBinVisible(false)}
        footer={null}
        width={800}
        destroyOnClose
      >
        <Table
          dataSource={deletedKbs}
          rowKey="id"
          loading={recycleLoading}
          pagination={{ pageSize: 5 }}
          columns={[
            {
              title: '知识库名称',
              dataIndex: 'name',
              key: 'name',
              render: (text) => <span style={{ fontWeight: 500 }}>{text}</span>,
            },
            {
              title: '分块数量',
              dataIndex: 'chunkCount',
              key: 'chunkCount',
              render: (count) => formatNumber(count || 0),
            },
            {
              title: '操作',
              key: 'action',
              render: (_, record) => (
                <Space>
                  <Tooltip title="恢复知识库">
                    <Button
                      type="link"
                      icon={<UndoOutlined />}
                      onClick={() => handleRestore(record.id)}
                    >
                      恢复
                    </Button>
                  </Tooltip>
                  <Tooltip title="永久删除后无法恢复">
                    <Popconfirm
                      title="永久删除"
                      description="确定要永久删除该知识库吗？此操作不可恢复！"
                      onConfirm={() => handlePermanentDelete(record.id)}
                      okText="删除"
                      cancelText="取消"
                      okButtonProps={{ danger: true }}
                      icon={<ExclamationCircleOutlined style={{ color: 'red' }} />}
                    >
                      <Button type="link" danger icon={<DeleteOutlined />}>
                        永久删除
                      </Button>
                    </Popconfirm>
                  </Tooltip>
                </Space>
              ),
            },
          ]}
        />
      </Modal>
    </div>
  );
};

export default KnowledgeBaseList;
