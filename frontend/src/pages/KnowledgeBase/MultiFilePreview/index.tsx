import React, { useState, useEffect } from 'react';
import { useParams, useNavigate, useLocation } from 'react-router-dom';
import { Layout, Menu, Button, message, Spin, Empty, Modal, Space, Input, Tag, Image } from 'antd';
import { PageHeader } from '@/components/common';
import { 
  FileTextOutlined, 
  RocketOutlined, 
  SettingOutlined,
  CheckCircleOutlined,
  SyncOutlined,
  SaveOutlined,
  FileImageOutlined,
  TableOutlined,
  RollbackOutlined,
  FolderOutlined
} from '@ant-design/icons';
import type { FileInfo, ChunkInfo } from '@/types';

const IMAGE_EXTENSIONS = ['jpg', 'jpeg', 'png', 'bmp', 'webp'];
const IMAGE_FOLDER_ID = 'standalone_images';
import { getFileList, parseFile, submitChunks, getFileDetail, getFileChunks, updateChunk, vectorizeFile } from '@/services';
import ChunkCard from '@/components/ChunkCard';
import ParseModal, { ParseConfig } from '../Detail/ParseModal';
import styles from './MultiFilePreview.module.css';

const { TextArea } = Input;

const MultiFilePreview: React.FC = () => {
  const { kbId } = useParams<{ kbId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const initialConfig = (location.state as { config?: ParseConfig })?.config;

  const [files, setFiles] = useState<FileInfo[]>([]);
  const [imageFiles, setImageFiles] = useState<FileInfo[]>([]);
  const [selectedFileId, setSelectedFileId] = useState<string>('');
  const [chunks, setChunks] = useState<ChunkInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [parsing, setParsing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  
  // 详情编辑相关
  const [selectedChunk, setSelectedChunk] = useState<ChunkInfo | null>(null);
  const [detailModalVisible, setDetailModalVisible] = useState(false);
  const [editingChunks, setEditingChunks] = useState<Record<string, string>>({});
  const [savingChunk, setSavingChunk] = useState(false);

  // 当前解析配置
  const [parseConfig, setParseConfig] = useState<ParseConfig>(initialConfig || {
    chunk_size: 500,
    chunk_overlap: 50,
    separators: ['\n\n'],
    chunking_mode: 'hierarchical'
  });
  const [isConfigModalVisible, setIsConfigModalVisible] = useState(false);

  // 加载文件列表
  useEffect(() => {
    if (kbId) {
      loadFiles();
    }
  }, [kbId]);

  // 统一查看模式
  const isViewMode = (location.state as { mode?: string })?.mode === 'view';

  const loadFiles = async () => {
    try {
      const res = await getFileList(kbId!);
      const fileList = res.data || [];
      
      let validFiles = fileList.filter(f => f.status !== 'deleted');
      
      // 如果是“统一查看”模式，展示 pending_confirm 或 parsed 的文件
      if (isViewMode) {
          // 仅展示已完成 (parsed) 的文件
          validFiles = validFiles.filter(f => f.status === 'parsed');
      } else {
          // “统一解析”预览模式：
          // 过滤掉已完成 (parsed) 的文件，只展示需要处理的（失败的 failed、新上传的 uploaded 等）
          validFiles = validFiles.filter(f => f.status !== 'parsed');
      }
      
      // 分离图片文件
      const imgs = validFiles.filter(f => {
          const ext = f.name.split('.').pop()?.toLowerCase();
          return ext && IMAGE_EXTENSIONS.includes(ext);
      });
      setImageFiles(imgs);

      const others = validFiles.filter(f => {
          const ext = f.name.split('.').pop()?.toLowerCase();
          return !ext || !IMAGE_EXTENSIONS.includes(ext);
      });

      let finalFiles = [...others];
      if (imgs.length > 0) {
           const imageFolder = {
              id: IMAGE_FOLDER_ID,
              name: '图片库',
              status: 'parsed', 
              chunk_count: imgs.reduce((acc, f) => acc + (f.chunk_count || 0), 0),
              size: imgs.reduce((acc, f) => acc + f.size, 0),
              createdAt: imgs[0]?.createdAt || new Date().toISOString(),
              updatedAt: imgs[0]?.updatedAt || new Date().toISOString(),
              kb_id: kbId!,
              type: 'folder' // 标记为文件夹
           } as unknown as FileInfo;
           finalFiles.unshift(imageFolder);
      }
      
      setFiles(finalFiles);
      if (finalFiles.length > 0 && !selectedFileId) {
        setSelectedFileId(finalFiles[0].id);
      }
    } catch (error) {
      message.error('加载文件列表失败');
    }
  };

  // 当选中文件或配置变更时，重新预览
  useEffect(() => {
    if (selectedFileId && kbId) {
      previewFile(selectedFileId);
    }
  }, [selectedFileId, parseConfig, kbId]);

  // 预览文件逻辑
  const previewFile = async (fileId: string) => {
    setLoading(true);
    setChunks([]); // 先清空，避免显示旧内容

    // 处理图片文件夹
    if (fileId === IMAGE_FOLDER_ID) {
        try {
            const processImage = async (file: FileInfo) => {
                 if (file.status === 'parsed' || file.status === 'pending_confirm') {
                     const res = await getFileChunks(file.id);
                     return Array.isArray(res.data) ? res.data : [];
                 }
                 
                 if (isViewMode) return [];
                 
                 try {
                    await parseFile(file.id, {
                        ...parseConfig,
                        preview: true
                    });
                    
                    const startTime = Date.now();
                    while (Date.now() - startTime < 30000) { // 30秒超时
                        const detail = await getFileDetail(file.id);
                        if (detail.data.status === 'pending_confirm' || detail.data.status === 'parsed') {
                            const res = await getFileChunks(file.id);
                            return Array.isArray(res.data) ? res.data : [];
                        }
                        if (detail.data.status === 'failed') break;
                        await new Promise(r => setTimeout(r, 500));
                    }
                 } catch (e) {
                     console.error(`Failed to parse image ${file.name}`, e);
                 }
                 return [];
            };
            
            const results = await Promise.all(imageFiles.map(processImage));
            setChunks(results.flat());
        } catch (error) {
            message.error('加载图片预览失败');
        } finally {
            setLoading(false);
        }
        return;
    }

    try {
      // 如果是 view 模式且文件已经是 pending_confirm 或 parsed，直接获取 chunks，不需要重新 parse
      const currentFile = files.find(f => f.id === fileId);
      
      if (isViewMode || (currentFile && (currentFile.status === 'pending_confirm' || currentFile.status === 'parsed'))) {
           // 直接获取结果
           const chunksRes = await getFileChunks(fileId);
           setChunks(Array.isArray(chunksRes.data) ? chunksRes.data : []);
           setLoading(false);
           return;
      }

      // 否则触发解析预览
      // 1. 触发解析
      await parseFile(fileId, {
        ...parseConfig,
        preview: true 
      });
      
      // 2. 轮询等待解析完成
      const startTime = Date.now();
      let isTimeout = true;
      
      while (Date.now() - startTime < 600000) { // 10分钟超时
        try {
          const res = await getFileDetail(fileId);
          if (res.data.status === 'pending_confirm' || res.data.status === 'parsed') {
             // 解析完成，获取 chunks
             const chunksRes = await getFileChunks(fileId);
             setChunks(Array.isArray(chunksRes.data) ? chunksRes.data : []);
             isTimeout = false;
             break;
          } else if (res.data.status === 'failed') {
             throw new Error(res.data.error_message || '解析失败');
          }
        } catch (e) {
          // ignore error
        }
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
      
      if (isTimeout) {
          throw new Error('等待超时');
      }

    } catch (error: any) {
      console.error(error);
      message.error(error.message || '预览解析失败');
    } finally {
      setLoading(false);
    }
  };

  // 辅助函数：尝试获取图片 URL
  const getChunkImageUrl = (chunk: ChunkInfo) => {
    if (chunk.imageUrl) return chunk.imageUrl;
    // 匹配 [图片: xxx] 格式
    const match = chunk.content.match(/\[图片:\s*(.+?)\]/);
    if (match && match[1]) {
      const path = match[1].trim();
      // 如果已经是 /static 开头，直接返回
      if (path.startsWith('/static/')) return path;
      // 否则拼接 /static/images/
      return `/static/images/${path}`;
    }
    return null;
  };

  // 获取 Chunk 类型图标
  const getChunkTypeIcon = (chunk: ChunkInfo) => {
    const imageUrl = getChunkImageUrl(chunk);
    if (imageUrl || chunk.contentType === 'image') {
        return <FileImageOutlined />;
    }
    if (chunk.contentType === 'table') {
        return <TableOutlined />;
    }
    return <FileTextOutlined />;
  };

  // 获取 Chunk 类型标签
  const getChunkTypeTag = (chunk: ChunkInfo) => {
    const config: Record<string, { label: string; color: string }> = {
      text: { label: '文本', color: 'blue' },
      image: { label: '图片', color: 'purple' },
      table: { label: '表格', color: 'green' },
      mixed: { label: '混合', color: 'orange' },
    };
    
    let type = chunk.contentType;
    if (getChunkImageUrl(chunk)) {
        type = 'image';
    }

    const c = config[type] || config.text;
    return <Tag color={c.color}>{c.label}</Tag>;
  };

  // 打开详情
  const handleOpenDetail = (chunk: ChunkInfo) => {
    setSelectedChunk(chunk);
    setDetailModalVisible(true);
  };

  // 保存详情修改
  const handleSaveDetail = async () => {
    if (!selectedChunk) return;
    const chunkId = selectedChunk.id;
    const content = editingChunks[chunkId];
    
    if (content === undefined) {
        setDetailModalVisible(false);
        return;
    }

    setSavingChunk(true);
    try {
      await updateChunk(chunkId, { content });
      message.success('保存成功');
      
      // 更新本地状态：chunks 和 selectedChunk
      setChunks((prev) =>
        prev.map((c) => (c.id === chunkId ? { ...c, content } : c))
      );
      setSelectedChunk(prev => prev ? { ...prev, content } : null);
      
      // 注意：editingChunks 需要保留还是清除？
      // 如果清除，下次打开详情时会显示 updated content。
      // 但这里为了 UI 响应，我们已经更新了 chunks。
      // 所以清除 editingChunks 是安全的。
      setEditingChunks((prev) => {
        const next = { ...prev };
        delete next[chunkId];
        return next;
      });
      setDetailModalVisible(false);
    } catch (error) {
      message.error('保存失败');
    } finally {
      setSavingChunk(false);
    }
  };

  // 处理内容编辑
  const handleContentChange = (chunkId: string, content: string) => {
    setEditingChunks((prev) => ({
      ...prev,
      [chunkId]: content,
    }));
  };

  // 详情弹窗
  const renderDetailModal = () => {
    if (!selectedChunk) return null;
    
    // 如果是 view 模式（统一查看），或者文件状态为 parsed（已入库），则只读
    // 注意：pending_confirm 状态是允许修改的
    // 我们需要知道当前选中文件的状态。files 是所有文件列表。
    const currentFile = files.find(f => f.id === selectedFileId);
    // 这里我们假设如果 isViewMode=true，或者文件状态是 parsed，则只读。
    // 但 isViewMode 其实是根据 pending_confirm 过滤的。
    // 用户需求：状态为解析完成（parsed/completed）点击进入后，不允许修改。
    // 而 pending_confirm（待确认）是可以修改的。
    // 所以这里的 readOnly 取决于文件状态。
    const isReadOnly = isViewMode || (currentFile?.status === 'parsed') || (currentFile?.status === 'ready');
    
    const isEditing = !isReadOnly && (selectedChunk.id in editingChunks);
    const currentContent = editingChunks[selectedChunk.id] ?? selectedChunk.content;
    const imageUrl = getChunkImageUrl(selectedChunk);

    return (
      <Modal
        title={
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
             {getChunkTypeIcon(selectedChunk)} 
             切分详情 #{chunks.indexOf(selectedChunk) + 1}
             {getChunkTypeTag(selectedChunk)}
          </div>
        }
        open={detailModalVisible}
        onCancel={() => setDetailModalVisible(false)}
        width={800}
        footer={[
            <Button key="close" onClick={() => setDetailModalVisible(false)}>
                {isReadOnly ? '关闭' : '取消'}
            </Button>,
            !isReadOnly && (
                <Button 
                    key="save" 
                    type="primary" 
                    icon={<SaveOutlined />} 
                    loading={savingChunk}
                    onClick={handleSaveDetail}
                    disabled={!isEditing}
                >
                    保存修改
                </Button>
            )
        ].filter(Boolean)}
        destroyOnClose
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
             <div>
              <div style={{ marginBottom: 8, fontWeight: 500 }}>文本内容：</div>
              <TextArea
                value={currentContent}
                onChange={(e) => handleContentChange(selectedChunk.id, e.target.value)}
                autoSize={{ minRows: 6, maxRows: 16 }}
                readOnly={isReadOnly}
                style={isReadOnly ? { background: '#f5f5f5', color: '#666' } : {}}
              />
            </div>

            {imageUrl && (
              <div>
                 <div style={{ padding: '8px 12px', background: '#f5f5f5', borderBottom: '1px solid #eee', fontWeight: 500 }}>
                    原始图片
                 </div>
                 <div style={{ padding: 12, border: '1px solid #eee', borderRadius: '0 0 4px 4px' }}>
                     <Image
                      src={imageUrl}
                      alt="chunk image"
                      style={{ maxWidth: '100%', maxHeight: 400, objectFit: 'contain' }}
                    />
                 </div>
              </div>
            )}

            {selectedChunk.vlmDescription && (
              <div>
                <div style={{ marginBottom: 8 }}>
                  <Tag color="purple">VLM 描述</Tag>
                </div>
                <div style={{ padding: 12, background: '#f9f9f9', borderRadius: 4, fontSize: 13, lineHeight: 1.5 }}>
                  {selectedChunk.vlmDescription}
                </div>
              </div>
            )}
        </div>
      </Modal>
    );
  };

  // 处理配置修改 (Preview)
  const handleConfigPreview = (newConfig: ParseConfig) => {
    setParseConfig(newConfig);
    setIsConfigModalVisible(false);
    message.success('配置已更新，正在重新解析当前文件...');
  };
  
  // 处理配置修改并直接处理 (Direct Process)
  const handleConfigDirectProcess = async (newConfig: ParseConfig) => {
      // 1. 更新配置
      setParseConfig(newConfig);
      setIsConfigModalVisible(false);
      
      // 2. 触发“全部入库”逻辑
      setSubmitting(true);
      try {
          // 处理图片文件夹
          const realFiles = [...files];
          // 如果列表包含图片文件夹，需要把它展开为实际的图片文件列表
          const imageFolderIndex = realFiles.findIndex(f => f.id === IMAGE_FOLDER_ID);
          if (imageFolderIndex !== -1) {
              // 移除文件夹，加入实际图片文件
              realFiles.splice(imageFolderIndex, 1, ...imageFiles);
          }

          // 并行触发所有文件的解析任务 (auto_vectorize=true)
          const tasks = realFiles.map(file => 
             parseFile(file.id, { 
                 ...newConfig,
                 auto_vectorize: true // 自动入库
             })
               .then(() => true)
               .catch(e => {
                   console.error(`文件 ${file.name} 提交失败`, e);
                   return false;
               })
          );
          
          const results = await Promise.all(tasks);
          const successCount = results.filter(Boolean).length;
          
          message.success(`已触发 ${successCount} 个文件的自动处理任务，请稍后在详情页查看进度`);
          navigate(`/kb/${kbId}`);
      } catch (error) {
          message.error('批量处理失败');
      } finally {
          setSubmitting(false);
      }
  };

  // 批量提交入库
  const handleSubmitAll = async () => {
    Modal.confirm({
      title: '确认全部入库',
      content: `确定要将列表中的 ${files.length} 个文件按照当前配置进行解析入库吗？这可能需要一些时间。`,
      okText: '开始处理',
      cancelText: '取消',
      onOk: async () => {
        setSubmitting(true);
        try {
          // 处理图片文件夹
          const realFiles = [...files];
          // 如果列表包含图片文件夹，需要把它展开为实际的图片文件列表
          const imageFolderIndex = realFiles.findIndex(f => f.id === IMAGE_FOLDER_ID);
          if (imageFolderIndex !== -1) {
              // 移除文件夹，加入实际图片文件
              realFiles.splice(imageFolderIndex, 1, ...imageFiles);
          }

          // 并行处理所有文件 (恢复并发，后端已支持 auto_vectorize 和 WAL 模式)
          const tasks = realFiles.map(async (file) => {
             try {
                // 检查当前状态
                const detail = await getFileDetail(file.id);
                if (detail.data.status === 'pending_confirm') {
                    // 已经解析好，直接入库
                    await vectorizeFile(file.id);
                } else {
                    // 还没解析，触发解析并设置 auto_vectorize=true
                    // 这样后端会在解析完成后自动触发向量化，前端无需等待
                    await parseFile(file.id, { 
                        ...parseConfig,
                        auto_vectorize: true // 关键参数
                    });
                }
                return true;
             } catch (e) {
                 console.error(`文件 ${file.name} 提交失败`, e);
                 return false;
             }
          });
          
          const results = await Promise.all(tasks);
          const successCount = results.filter(Boolean).length;
          
          message.success(`已触发 ${successCount} 个文件的处理任务，请稍后在详情页查看进度`);
          navigate(`/kb/${kbId}`);
        } catch (error) {
          message.error('批量提交失败');
        } finally {
          setSubmitting(false);
        }
      }
    });
  };

  return (
    <div className={styles.container}>
      <PageHeader
        className={styles.header}
        onBack={() => navigate(-1)}
        title={isViewMode ? "批量查看结果" : "批量切分预览"}
        subTitle={`共 ${files.length} 个文件`}
        extra={[
          !isViewMode && (
            <Button 
                key="config" 
                icon={<SettingOutlined />} 
                onClick={() => setIsConfigModalVisible(true)}
            >
                调整参数
            </Button>
          ),
          !isViewMode ? (
            <Button 
                key="submit" 
                type="primary" 
                icon={<RocketOutlined />} 
                loading={submitting}
                onClick={handleSubmitAll}
            >
                全部入库
            </Button>
          ) : (
            <Button 
                key="back" 
                icon={<RollbackOutlined />} 
                onClick={() => navigate(-1)}
            >
                返回
            </Button>
          )
        ]}
      />
      
      <div className={styles.layout}>
        <div className={styles.sider}>
          <div className={styles.fileListHeader}>文件列表</div>
          <div className={styles.menuContainer}>
            <Menu
              mode="inline"
              selectedKeys={[selectedFileId]}
              onClick={({ key }) => setSelectedFileId(key)}
              style={{ borderRight: 0 }}
            >
              {files.map(file => (
                <Menu.Item 
                    key={file.id} 
                    icon={file.id === IMAGE_FOLDER_ID ? <FolderOutlined /> : <FileTextOutlined />}
                    style={{ height: 'auto', padding: '8px 16px', lineHeight: 1.5 }}
                >
                  <div className={styles.fileItem}>
                      <div className={styles.fileInfo}>
                        <span className={styles.fileName} title={file.name}>{file.name}</span>
                        <span className={styles.fileMeta}>
                           {(file.chunk_count || 0)} chunks
                        </span>
                      </div>
                      {file.status === 'parsed' && <CheckCircleOutlined style={{ color: '#52c41a' }} />}
                  </div>
                </Menu.Item>
              ))}
            </Menu>
          </div>
        </div>
        
        <div className={styles.content}>
          <div className={styles.previewHeader}>
            <span className={styles.currentFileName}>
                当前预览: {files.find(f => f.id === selectedFileId)?.name}
            </span>
            <span className={styles.chunkCount}>
                共生成 {chunks.length} 个切分块
            </span>
          </div>
          
          <div className={styles.previewArea}>
            {loading ? (
              <div className={styles.loading}>
                <Spin tip="正在解析预览..." size="large" />
              </div>
            ) : chunks.length > 0 ? (
              <div className={styles.grid}>
                 {chunks.map((chunk, index) => (
                    <ChunkCard 
                        key={chunk.id}
                        chunk={chunk}
                        index={index}
                        columnCount={4} // 固定 4 列，或者自适应
                        onClick={handleOpenDetail} // 支持点击详情
                        displayContent={chunk.content} // 简单展示
                    />
                 ))}
              </div>
            ) : (
              <Empty description="暂无切分结果" />
            )}
          </div>
        </div>
      </div>

      {renderDetailModal()}
      
      <ParseModal
        open={isConfigModalVisible}
        onCancel={() => setIsConfigModalVisible(false)}
        onPreview={handleConfigPreview}
        onDirectProcess={handleConfigDirectProcess}
        loading={false}
      />
    </div>
  );
};

export default MultiFilePreview;
