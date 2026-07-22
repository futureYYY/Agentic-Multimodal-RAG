/**
 * 知识库详情页面
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Card,
  Button,
  Table,
  Upload,
  Tag,
  Tabs,
  Progress,
  Space,
  message,
  Popconfirm,
  Spin,
  Statistic,
  Row,
  Col,
} from 'antd';
import {
  UploadOutlined,
  DeleteOutlined,
  EyeOutlined,
  FileTextOutlined,
  InboxOutlined,
  ScissorOutlined,
  RocketOutlined,
  SettingOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import type { UploadProps, TabsProps } from 'antd';
import { PageHeader } from '@/components/common';
import RecallTest from '../RecallTest';
import ParseModal, { ParseConfig } from './ParseModal';
import {
  getKnowledgeBaseDetail,
  getFileList,
  uploadFile,
  deleteFile,
  vectorizeFile,
  parseFile,
  getFileDetail,
  getModelList,
} from '@/services';
import { useAppStore } from '@/stores';
import { formatFileSize, formatDateTime, FILE_STATUS_CONFIG, FILE_POLL_INTERVAL } from '@/utils';
import type { FileInfo, KnowledgeBase } from '@/types';
import styles from './Detail.module.css';

const { Dragger } = Upload;

const KnowledgeBaseDetail: React.FC = () => {
  const { kbId } = useParams<{ kbId: string }>();
  const navigate = useNavigate();
  const { setCurrentKb, models, setModels } = useAppStore();

  const [loading, setLoading] = useState(true);
  const [knowledgeBase, setKnowledgeBase] = useState<KnowledgeBase | null>(null);
  const [files, setFiles] = useState<FileInfo[]>([]);
  const [uploading, setUploading] = useState(false);
  const [activeTab, setActiveTab] = useState('files');

  // 解析弹窗相关
  const [parseModalVisible, setParseModalVisible] = useState(false);
  const [currentFileId, setCurrentFileId] = useState<string | null>(null);
  const [parseLoading, setParseLoading] = useState(false);
  const [autoProcessFileId, setAutoProcessFileId] = useState<string | null>(null);

  const pollingRef = useRef<NodeJS.Timeout | null>(null);

  // 加载知识库详情
  const loadKnowledgeBase = async () => {
    if (!kbId) return;
    try {
      const response = await getKnowledgeBaseDetail(kbId);
      setKnowledgeBase(response.data);
      setCurrentKb(response.data);
    } catch (error) {
      message.error('加载知识库详情失败');
      navigate('/kb');
    }
  };

  // 加载文件列表
  const loadFiles = useCallback(async () => {
    if (!kbId) return;
    try {
      const response = await getFileList(kbId);
      setFiles(response.data || []);
    } catch (error) {
      console.error('加载文件列表失败:', error);
      setFiles([]);
    }
  }, [kbId]);

  // 加载模型列表
  const loadModels = async () => {
    // 强制加载一次，确保 models 不为空，或者直接刷新
    try {
      const response = await getModelList();
      const data = Array.isArray(response) ? response : (response as any).data || [];
      setModels(data);
    } catch (error) {
      console.error('加载模型列表失败:', error);
    }
  };

  // 初始加载
  useEffect(() => {
    const init = async () => {
      setLoading(true);
      try {
        await Promise.all([loadKnowledgeBase(), loadFiles(), loadModels()]);
      } catch (e) {
        console.error('Initialization failed:', e);
      } finally {
        setLoading(false);
      }
    };
    init();

    return () => {
      setCurrentKb(null);
    };
  }, [kbId]);

  // 轮询文件状态
  useEffect(() => {
    // 检查是否有自动处理的文件
    if (autoProcessFileId) {
      const targetFile = files.find(f => f.id === autoProcessFileId);
      if (targetFile && targetFile.status === 'pending_confirm') {
        // 自动触发入库
        handleVectorize(autoProcessFileId);
        setAutoProcessFileId(null);
      }
    }

    const hasProcessingFile = files.some(
      (f) => f.status === 'parsing' || f.status === 'embedding' || f.status === 'pending_confirm' || (autoProcessFileId && f.id === autoProcessFileId)
    );

    if (hasProcessingFile) {
      pollingRef.current = setInterval(loadFiles, FILE_POLL_INTERVAL);
    }

    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
      }
    };
  }, [files, loadFiles, autoProcessFileId]);

  // 处理文件上传
  const handleUpload: UploadProps['customRequest'] = async (options) => {
    const { file, onSuccess, onError } = options;
    if (!kbId) return;

    setUploading(true);
    try {
      await uploadFile(kbId, file as File);
      message.success('上传成功，请手动解析文件');
      onSuccess?.(null);
      loadFiles();
    } catch (error) {
      message.error('上传失败');
      onError?.(error as Error);
    } finally {
      setUploading(false);
    }
  };

  // 处理删除文件
  const handleDeleteFile = async (fileId: string) => {
    try {
      await deleteFile(fileId);
      message.success('删除成功');
      loadFiles();
    } catch (error) {
      message.error('删除失败');
    }
  };

  // 处理确认入库
  const handleVectorize = async (fileId: string) => {
    try {
      await vectorizeFile(fileId);
      message.success('已开始向量化');
      loadFiles();
    } catch (error) {
      message.error('操作失败');
    }
  };

  // 点击解析按钮
  const handleParseClick = (fileId: string) => {
    setCurrentFileId(fileId);
    setParseModalVisible(true);
  };

  // 批量统一解析
  const handleBatchParse = () => {
    // 弹出解析配置弹窗，但标记为批量模式
    // 这里我们复用 ParseModal，但可能需要稍微改动一下 ParseModal 以支持不同文案
    // 或者我们简单点，通过一个 ref 或 state 标记当前是批量操作
    setCurrentFileId(null); // null 表示批量
    setParseModalVisible(true);
  };

  // 处理预览切分 (修改以支持批量跳转)
  const handleParsePreview = async (config: ParseConfig) => {
    if (currentFileId) {
        // 单文件预览逻辑
        setParseLoading(true);
        try {
          await parseFile(currentFileId, config);
          message.loading({ content: '正在解析...', key: 'parsing', duration: 0 });
    
          // 轮询等待解析完成
          const startTime = Date.now();
          while (Date.now() - startTime < 600000) { // 10分钟超时
            try {
              const res = await getFileDetail(currentFileId);
              if (res.data.status === 'pending_confirm') {
                message.success({ content: '解析完成', key: 'parsing' });
                setParseModalVisible(false);
                // 跳转到预览页面
                navigate(`/kb/${kbId}/preview/${currentFileId}`, {
                  state: { parseConfig: config }
                });
                return;
              } else if (res.data.status === 'failed') {
                 throw new Error(res.data.error_message || '解析失败');
              }
            } catch (e) {
              // ignore error during polling
            }
            await new Promise(resolve => setTimeout(resolve, 1000));
          }
          throw new Error('等待超时');
    
        } catch (error: any) {
          message.error({ content: error.message || '触发解析失败', key: 'parsing' });
        } finally {
          setParseLoading(false);
        }
    } else {
        // 批量预览逻辑
        // 不需要等待解析，直接带配置跳转到 MultiFilePreview
        setParseModalVisible(false);
        navigate(`/kb/${kbId}/preview-all`, {
            state: { config }
        });
    }
  };

  // 直接处理并入库
  const handleParseProcess = async (config: ParseConfig) => {
    setParseLoading(true);
    try {
      if (currentFileId) {
          // 单文件逻辑：自动解析并入库
          await parseFile(currentFileId, {
             ...config,
             auto_vectorize: true
          });
          message.success('已触发自动处理任务，解析完成后将自动入库');
      } else {
          // 批量逻辑：触发所有文件的自动处理任务 (auto_vectorize=true)
          // 过滤掉不需要处理的文件
          // 规则：
          // 1. 解析完成 (parsed) -> 忽略
          // 2. 待确认 (pending_confirm) -> 忽略 (用户自己处理)
          // 3. 正在处理中 (parsing/embedding) -> 忽略
          // 4. 失败 (failed) -> 重试
          // 5. 其他 (如 uploaded) -> 处理
          const targetFiles = files.filter(f => 
             f.status === 'failed' || 
             (f.status !== 'parsed' && f.status !== 'pending_confirm' && f.status !== 'parsing' && f.status !== 'embedding')
          );

          if (targetFiles.length === 0) {
             message.info('没有需要处理的文件');
             setParseLoading(false);
             setParseModalVisible(false);
             return;
          }

          const tasks = targetFiles.map(file => 
             parseFile(file.id, { 
                 ...config,
                 auto_vectorize: true 
             })
               .then(() => true)
               .catch(e => {
                   console.error(`文件 ${file.name} 提交失败`, e);
                   return false;
               })
          );
          
          const results = await Promise.all(tasks);
          const successCount = results.filter(Boolean).length;
          message.success(`已触发 ${successCount} 个文件的自动处理任务`);
      }
      
      setParseModalVisible(false);
      setAutoProcessFileId(null); 
      loadFiles();
    } catch (error) {
      message.error('触发处理失败');
    } finally {
      setParseLoading(false);
    }
  };

  // 文件列表列定义
  const columns = [
    {
      title: '文件名',
      dataIndex: 'name',
      key: 'name',
      render: (name: string) => (
        <span className={styles.fileName}>
          <FileTextOutlined />
          {name}
        </span>
      ),
    },
    {
      title: '大小',
      dataIndex: 'size',
      key: 'size',
      width: 100,
      render: (size: number) => formatFileSize(size),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 150,
      render: (status: FileInfo['status'], record: FileInfo) => {
        const config = FILE_STATUS_CONFIG[status] || FILE_STATUS_CONFIG.failed;
        return (
          <div className={styles.statusCell}>
            <Tag color={config.color}>{config.label}</Tag>
            {(status === 'parsing' || status === 'embedding') && (
              <Progress
                percent={record.progress}
                size="small"
                className={styles.progress}
              />
            )}
          </div>
        );
      },
    },
    {
      title: '上传时间',
      dataIndex: 'createdAt',
      key: 'createdAt',
      width: 180,
      render: (date: string) => formatDateTime(date),
    },
    {
      title: '操作',
      key: 'actions',
      width: 200,
      render: (_: unknown, record: FileInfo) => (
        <Space>
          {/* 操作按钮逻辑优化：
             1. 如果不是 embedding/parsing/pending_confirm/parsed，显示“解析”和“删除”
             2. 如果是 pending_confirm 或 parsed，显示“查看结果”、“确认入库”和“删除”
          */}
          
          {(record.status !== 'embedding' && record.status !== 'parsing' && record.status !== 'pending_confirm' && record.status !== 'parsed') && (
             <Button
              type="link"
              size="small"
              icon={<ScissorOutlined />}
              onClick={() => handleParseClick(record.id)}
            >
              解析
            </Button>
          )}

          {(record.status === 'pending_confirm' || record.status === 'parsed') && (
             <Space size="small">
                <Button
                  type="link"
                  size="small"
                  icon={<EyeOutlined />}
                  onClick={() => {
                     // 跳转到预览页面查看结果
                     navigate(`/kb/${kbId}/preview/${record.id}`);
                  }}
                >
                  查看结果
                </Button>
                {record.status === 'pending_confirm' && (
                    <Button
                      type="link"
                      size="small"
                      icon={<RocketOutlined />}
                      onClick={() => handleVectorize(record.id)}
                    >
                      确认入库
                    </Button>
                )}
                {/* 已入库状态，显示重新解析 */}
                {record.status === 'parsed' && (
                    <Button
                      type="link"
                      size="small"
                      icon={<SyncOutlined />}
                      onClick={() => handleParseClick(record.id)}
                    >
                      重新解析
                    </Button>
                )}
             </Space>
          )}
          <Popconfirm
            title="确定删除该文件吗？"
            onConfirm={() => handleDeleteFile(record.id)}
            okText="确定"
            cancelText="取消"
          >
            <Button type="link" size="small" danger icon={<DeleteOutlined />}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  // Tab 配置
  const tabItems: TabsProps['items'] = [
    {
      key: 'files',
      label: '文件管理',
      children: (
        <div className={styles.tabContent}>
          <Dragger
            customRequest={handleUpload}
            showUploadList={false}
            accept=".docx,.pdf,.xlsx,.csv,.txt,.jpg,.jpeg,.png,.bmp,.webp"
            multiple
            disabled={uploading}
            className={styles.uploader}
          >
            <p className="ant-upload-drag-icon">
              <InboxOutlined />
            </p>
            <p className="ant-upload-text">点击或拖拽文件到此处上传</p>
            <p className="ant-upload-hint">
              支持 Word (.docx)、PDF、Excel (.xlsx)、CSV、TXT、图片 格式
            </p>
          </Dragger>

          <Table
            columns={columns}
            dataSource={files}
            rowKey="id"
            pagination={false}
            className={styles.fileTable}
          />
        </div>
      ),
    },
    {
      key: 'recall',
      label: '召回测试',
      children: kbId ? <RecallTest kbId={kbId} /> : null,
    },
  ];

  if (loading) {
    return (
      <div className={styles.loading}>
        <Spin size="large" />
      </div>
    );
  }

  // 批量统一查看
  const handleBatchPreview = () => {
    navigate(`/kb/${kbId}/preview-all`, {
        state: { mode: 'view' } // 传递 mode 参数，告知 MultiFilePreview 只展示待确认文件
    });
  };

  // 获取 Embedding 模型名称
  const embeddingModelName = models.find(m => m.id === knowledgeBase?.embedding_model)?.name || knowledgeBase?.embedding_model || '-';

  return (
    <div className={styles.container}>
      <PageHeader
        title={knowledgeBase?.name || '知识库详情'}
        subtitle={knowledgeBase?.description}
        showBack
        breadcrumbs={[
          { title: '知识库管理', path: '/kb' },
          { title: knowledgeBase?.name || '' },
        ]}
        extra={[
            <Button 
                key="batch-parse" 
                icon={<SettingOutlined />} 
                onClick={handleBatchParse}
            >
                统一解析
            </Button>,
            <Button 
                key="batch-preview" 
                icon={<EyeOutlined />} 
                onClick={handleBatchPreview}
            >
                统一查看
            </Button>
        ]}
      />

      {/* 统计信息 */}
      <Card className={styles.statsCard}>
        <Row gutter={24}>
          <Col span={8}>
            <Statistic title="文件数量" value={files?.length || 0} suffix="个" />
          </Col>
          <Col span={8}>
            <Statistic
              title="分块数量"
              value={knowledgeBase?.chunk_count || 0}
              suffix="个"
            />
          </Col>
          <Col span={8}>
            <Statistic
              title="Embedding 模型"
              value={embeddingModelName}
              valueStyle={{ fontSize: 16 }}
            />
          </Col>
        </Row>
      </Card>

      {/* Tab 内容 */}
      <Card className={styles.mainCard}>
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={tabItems}
        />
      </Card>

      <ParseModal
        open={parseModalVisible}
        onCancel={() => setParseModalVisible(false)}
        onPreview={handleParsePreview}
        onDirectProcess={handleParseProcess}
        loading={parseLoading}
      />
    </div>
  );
};

export default KnowledgeBaseDetail;
