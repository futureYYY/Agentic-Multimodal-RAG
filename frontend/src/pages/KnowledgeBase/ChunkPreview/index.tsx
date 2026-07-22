/**
 * 切分预览页面
 */

import React, { useState, useEffect } from 'react';
import { useParams, useNavigate, useLocation } from 'react-router-dom';
import {
  Card,
  Button,
  Input,
  Image,
  Spin,
  message,
  Tag,
  Modal,
  Space,
} from 'antd';
import {
  SaveOutlined,
  CheckOutlined,
  FileImageOutlined,
  TableOutlined,
  FileTextOutlined,
  AppstoreOutlined,
  ColumnWidthOutlined,
  SettingOutlined,
  RollbackOutlined
} from '@ant-design/icons';
import { PageHeader } from '@/components/common';
import { getFileChunks, updateChunk, vectorizeFile, getFileDetail, parseFile, submitChunks } from '@/services';
import type { ChunkInfo, FileInfo } from '@/types';
import ChunkCard from '@/components/ChunkCard';
import ParseModal, { ParseConfig } from '../Detail/ParseModal';
import styles from './ChunkPreview.module.css';

const { TextArea } = Input;

const ChunkPreview: React.FC = () => {
  const { kbId, fileId } = useParams<{ kbId: string; fileId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const previewState = location.state as { previewChunks?: ChunkInfo[], parseConfig?: any } | undefined;

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [file, setFile] = useState<FileInfo | null>(null);
  const [chunks, setChunks] = useState<ChunkInfo[]>([]);
  const [editingChunks, setEditingChunks] = useState<Record<string, string>>({});
  const [selectedChunk, setSelectedChunk] = useState<ChunkInfo | null>(null);
  const [detailModalVisible, setDetailModalVisible] = useState(false);
  
  // 重新配置相关
  const [reconfigVisible, setReconfigVisible] = useState(false);
  const [currentConfig, setCurrentConfig] = useState<any>(null);
  const [reparsing, setReparsing] = useState(false);

  // 网格布局控制
  const [columnCount, setColumnCount] = useState<number>(6); // 默认一行展示6个

  // 判断是否为预览模式
  const isPreviewMode = !!previewState || file?.status === 'pending_confirm';

  // 加载数据
  useEffect(() => {
    const loadData = async () => {
      if (!fileId) return;
      setLoading(true);
      try {
        // 先加载文件详情（无论是否预览模式都需要）
        const fileRes = await getFileDetail(fileId);
        setFile(fileRes.data);

        if (previewState?.previewChunks) {
            // 预览模式：使用传入的数据
            setChunks(previewState.previewChunks);
        } else {
            // 正常模式或通过 ID 进入的预览模式：加载后端数据
            const chunksRes = await getFileChunks(fileId);
            setChunks(Array.isArray(chunksRes.data) ? chunksRes.data : []);
        }
        
        // 设置配置
        if (previewState?.parseConfig) {
            setCurrentConfig(previewState.parseConfig);
        }
      } catch (error) {
        message.error('加载数据失败');
        if (!previewState) navigate(`/kb/${kbId}`);
      } finally {
        setLoading(false);
      }
    };
    loadData();
  }, [fileId, kbId, navigate, previewState]);

  // 处理内容编辑
  const handleContentChange = (chunkId: string, content: string) => {
    setEditingChunks((prev) => ({
      ...prev,
      [chunkId]: content,
    }));
  };

  // 确认入库 / 提交解析
  const handleConfirm = async () => {
    if (!fileId) return;
    
    // 如果是预览模式，提交当前 Chunks 入库
    if (isPreviewMode) {
         try {
          // 构造提交数据
          const chunksToSubmit = chunks.map(c => {
              // 处理图片路径，移除前缀
              let imagePath = c.imageUrl;
              if (imagePath && imagePath.startsWith('/static/images/')) {
                  imagePath = imagePath.replace('/static/images/', '');
              }
              
              return {
                  content: c.content,
                  page_number: c.pageNumber,
                  image_path: imagePath,
                  content_type: c.contentType,
                  vlm_description: c.vlmDescription
              };
          });

          await submitChunks(fileId, chunksToSubmit);
          message.success('已确认切分方案，开始后台入库');
          navigate(`/kb/${kbId}`);
         } catch (error) {
          message.error('操作失败');
         }
         return;
    }

    // 正常模式
    // 检查是否有未保存的编辑
    if (Object.keys(editingChunks).length > 0) {
      message.warning('请先保存所有修改');
      return;
    }

    try {
        // 正常模式：触发向量化（如果需要）
        await vectorizeFile(fileId);
        message.success('已开始向量化入库');
        navigate(`/kb/${kbId}`);
    } catch (error) {
      message.error('操作失败');
    }
  };

  // 打开配置弹窗
  const handleReconfigure = () => {
      setReconfigVisible(true);
  };
  
  // 轮询直到文件解析完成
  const waitForParsing = async (fid: string): Promise<void> => {
    return new Promise((resolve, reject) => {
      const startTime = Date.now();
      const check = async () => {
        if (Date.now() - startTime > 600000) { // 10分钟超时
           reject(new Error('等待超时'));
           return;
        }
        try {
          const res = await getFileDetail(fid);
          if (res.data.status === 'pending_confirm') {
            resolve();
          } else if (res.data.status === 'failed') {
            reject(new Error(res.data.error_message || '解析失败'));
          } else {
            setTimeout(check, 1000);
          }
        } catch (e) {
          setTimeout(check, 1000);
        }
      };
      check();
    });
  };

  // 处理重新解析预览
  const handleReParsePreview = async (config: ParseConfig) => {
      if (!fileId) return;
      setReparsing(true);
      try {
          await parseFile(fileId, config);
          message.loading({ content: '正在重新解析...', key: 'reparsing', duration: 0 });
          
          await waitForParsing(fileId);
          
          const chunksRes = await getFileChunks(fileId);
          setChunks(Array.isArray(chunksRes.data) ? chunksRes.data : []);
          setCurrentConfig(config);
          
          message.success({ content: '解析完成', key: 'reparsing' });
          setReconfigVisible(false);
      } catch (error) {
          message.error({ content: '解析失败', key: 'reparsing' });
      } finally {
          setReparsing(false);
      }
  };
  
  // 处理重新解析并直接入库
  const handleReParseProcess = async (config: ParseConfig) => {
      if (!fileId) return;
      try {
          await parseFile(fileId, config);
          message.success('已触发重新解析，完成后将自动入库');
          // 这里我们只是触发了解析，但因为是直接入库，我们可以返回详情页让用户等待，
          // 或者在此页面等待并自动 confirm。
          // 既然用户选择了直接入库，我们应该让用户回到文件列表查看进度。
          setReconfigVisible(false);
          navigate(`/kb/${kbId}`);
          
          // 如果需要自动 vectorization，Detail 页面需要处理，或者我们在这里等待？
          // 为了简单，我们只触发 parse。Detail 页面需要手动点击确认入库，除非我们传递状态。
          // 但根据需求 "处理并入库"，应该自动。
          // 我们可以调用 parseFile 后，再起一个后台任务监控？
          // 或者让 Detail 页面的 polling 逻辑处理（如果我们在 parseFile 中传递了标记？API 不支持）。
          // 既然是 "Re-parse Process"，我们其实可以复用 Detail 页面的逻辑。
          // 但这里是 Preview 页面。
          // 让我们在 Detail 页面实现自动处理逻辑（通过 autoProcessFileId）。
          // 但这里 navigate 回去，Detail 页面并不知道。
          
          // 既然已经在 Preview 页面，我们可以等待解析完成，然后调用 vectorizeFile。
          waitForParsing(fileId).then(async () => {
             await vectorizeFile(fileId);
             message.success('已开始向量化');
          });
          
      } catch (error) {
          message.error('提交失败');
      }
  };

  // ... (handleOpenDetail, handleSaveDetail 等保持不变)

  // 打开详情
  const handleOpenDetail = (chunk: ChunkInfo) => {
    setSelectedChunk(chunk);
    setDetailModalVisible(true);
  };

  // 保存详情修改 (预览模式下不支持保存修改，或者仅本地保存？暂不支持修改)
  const handleSaveDetail = async () => {
    if (!selectedChunk) return;
    const chunkId = selectedChunk.id;
    const content = editingChunks[chunkId];
    
    if (content === undefined) {
        setDetailModalVisible(false);
        return;
    }

    if (isPreviewMode) {
        // 预览模式：本地更新 State
        setChunks((prev) =>
            prev.map((c) => (c.id === chunkId ? { ...c, content } : c))
        );
        setSelectedChunk(prev => prev ? { ...prev, content } : null);
        
        setEditingChunks((prev) => {
            const next = { ...prev };
            delete next[chunkId];
            return next;
        });
        setDetailModalVisible(false);
        message.success('已更新预览内容');
        return;
    }

    setSaving(true);
    try {
      await updateChunk(chunkId, { content });
      message.success('保存成功');
      setChunks((prev) =>
        prev.map((c) => (c.id === chunkId ? { ...c, content } : c))
      );
      setSelectedChunk(prev => prev ? { ...prev, content } : null);
      
      setEditingChunks((prev) => {
        const next = { ...prev };
        delete next[chunkId];
        return next;
      });
      setDetailModalVisible(false);
    } catch (error) {
      message.error('保存失败');
    } finally {
      setSaving(false);
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

  // 辅助函数：计算显示内容（隐藏重叠部分）
  const getDisplayContent = (chunk: ChunkInfo, index: number) => {
    let content = chunk.content;
    
    // 如果不是第一个块，且存在上一个块，尝试去除重叠
    if (index > 0 && chunks[index - 1]) {
        const prevContent = chunks[index - 1].content;
        
        // 简单的重叠检测：检查当前块是否以之前块的结尾开头
        // 这里的 overlap 应该是配置中的值，但我们不一定知道确切的 overlap
        // 所以我们尝试找到最长的重叠
        // 限制最大检测长度为 500 (假设 overlap 不会超过这个值)
        const maxCheck = 500;
        const checkLen = Math.min(prevContent.length, maxCheck);
        const suffix = prevContent.slice(-checkLen);
        
        // 我们在 suffix 中寻找 content 的前缀
        // 从最长的可能重叠开始匹配
        for (let i = checkLen; i > 0; i--) {
            const potentialOverlap = suffix.slice(-i);
            if (content.startsWith(potentialOverlap)) {
                // 找到重叠，去除它
                // 为了视觉提示，我们可能想保留一个标记，但用户要求“不展示”
                // 确保我们不删除整个内容
                if (content.length > i) {
                   content = content.slice(i);
                }
                break;
            }
        }
    }
    return content;
  };

  // 辅助函数：切换列数
  const toggleColumnCount = () => {
    // 6 -> 8 -> 10 -> 4 -> 6
    if (columnCount === 6) setColumnCount(8);
    else if (columnCount === 8) setColumnCount(10);
    else if (columnCount === 10) setColumnCount(4);
    else setColumnCount(6);
  };


  
  // 详情弹窗
  const renderDetailModal = () => {
    if (!selectedChunk) return null;
    
    // 如果是预览模式，或者文件已入库（parsed），则只读
    // 注意：pending_confirm (预览模式) 是允许修改的
    // parsed 状态（查看结果）是不允许修改的
    const isReadOnly = file?.status === 'parsed' || file?.status === 'ready';
    
    const isEditing = !isReadOnly && (selectedChunk.id in editingChunks);
    
    // 获取显示内容 (去重叠)
    const displayContent = getDisplayContent(selectedChunk, chunks.indexOf(selectedChunk));
    
    // 如果已经在 editingChunks 中，说明用户修改过，直接展示修改后的内容
    // 如果不在，展示 displayContent
    const currentContent = editingChunks[selectedChunk.id] ?? displayContent;
    
    const imageUrl = getChunkImageUrl(selectedChunk);

    return (
      <Modal
        title={
          <div className={styles.chunkHeader}>
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
                    loading={saving}
                    onClick={handleSaveDetail}
                    disabled={!isEditing}
                >
                    保存修改
                </Button>
            )
        ].filter(Boolean)}
        destroyOnClose
      >
        <div className={styles.detailContent}>
             <div className={styles.contentSection}>
              <div style={{ marginBottom: 8, fontWeight: 500 }}>文本内容：</div>
              <TextArea
                value={currentContent}
                onChange={(e) => handleContentChange(selectedChunk.id, e.target.value)}
                autoSize={{ minRows: 6, maxRows: 16 }}
                className={styles.contentInput}
                readOnly={isReadOnly}
                style={isReadOnly ? { background: '#f5f5f5', color: '#666' } : {}}
              />
            </div>

            {imageUrl && (
              <div className={styles.imageSection}>
                 <div style={{ padding: '8px 12px', background: '#f5f5f5', borderBottom: '1px solid #eee', fontWeight: 500 }}>
                    原始图片
                 </div>
                 <Image
                  src={imageUrl}
                  alt="chunk image"
                  className={styles.thumbnail}
                />
              </div>
            )}

            {selectedChunk.vlmDescription && (
              <div className={styles.vlmSection}>
                <div className={styles.vlmLabel}>
                  <Tag color="purple">VLM 描述</Tag>
                </div>
                <div className={styles.vlmContent}>
                  {selectedChunk.vlmDescription}
                </div>
              </div>
            )}
        </div>
      </Modal>
    );
  };

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
        title={isPreviewMode ? "切分预览" : "切分结果"}
        subtitle={file?.name}
        showBack
        onBack={isPreviewMode ? handleReconfigure : undefined}
        breadcrumbs={[
          { title: '知识库管理', path: '/kb' },
          { title: '知识库详情', path: `/kb/${kbId}` },
          { title: isPreviewMode ? '切分预览' : '切分结果' },
        ]}
        extra={
          <Space>
             <Button 
                icon={<ColumnWidthOutlined />} 
                onClick={toggleColumnCount}
             >
                列数: {columnCount}
             </Button>

             {isPreviewMode ? (
                <>
                   <Button icon={<SettingOutlined />} onClick={handleReconfigure}>
                       调整参数
                   </Button>
                   <Button
                      type="primary"
                      icon={<CheckOutlined />}
                      onClick={handleConfirm}
                   >
                      确认并入库
                   </Button>
                </>
             ) : (
                <Button 
                    icon={<RollbackOutlined />} 
                    onClick={() => navigate(-1)}
                >
                    返回
                </Button>
             )}
          </Space>
        }
      />

      <div className={styles.chunkList} style={{ 
        flexDirection: 'row', 
        flexWrap: 'wrap', 
        gap: 12,
        alignItems: 'stretch' // 确保卡片等高
      }}>
        {chunks.map((chunk, index) => (
          <ChunkCard
            key={chunk.id}
            chunk={chunk}
            index={index}
            columnCount={columnCount}
            onClick={handleOpenDetail}
            displayContent={getDisplayContent(chunk, index)}
          />
        ))}
      </div>
      
      {renderDetailModal()}
      
      {fileId && (
          <ParseModal
              open={reconfigVisible}
              onCancel={() => setReconfigVisible(false)}
              onPreview={handleReParsePreview}
              onDirectProcess={handleReParseProcess}
              loading={reparsing}
          />
       )}
    </div>
  );
};

export default ChunkPreview;
