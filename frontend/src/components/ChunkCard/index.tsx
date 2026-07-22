import React from 'react';
import { Card, Tag } from 'antd';
import { FileImageOutlined, TableOutlined, FileTextOutlined } from '@ant-design/icons';
import type { ChunkInfo } from '@/types';
import styles from './ChunkCard.module.css';

interface ChunkCardProps {
  chunk: ChunkInfo;
  index: number;
  columnCount: number;
  onClick: (chunk: ChunkInfo) => void;
  // 以前的 getDisplayContent 逻辑应该由父组件处理好 content 传进来，或者在这里处理
  // 为了复用，建议传入 displayContent
  displayContent: string;
}

const ChunkCard: React.FC<ChunkCardProps> = ({ 
  chunk, 
  index, 
  columnCount, 
  onClick, 
  displayContent 
}) => {
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

  const imageUrl = getChunkImageUrl(chunk);

   // 渲染头部
   const renderChunkHeader = () => {
       // 检查是否是单独上传的图片文件 (根据 metadata)
       const isStandaloneImage = chunk.metadata?.is_standalone_image;
       const originalName = chunk.metadata?.original_name as string | undefined;

       return (
         <div className={styles.chunkHeader} style={{ marginBottom: 4, fontSize: 12 }}>
             <span className={styles.chunkIndex}>
             {getChunkTypeIcon(chunk)} #{index + 1}
             </span>
             <div style={{ transform: 'scale(0.8)', transformOrigin: 'right center', display: 'flex', gap: 4 }}>
                 {(chunk.contentType !== 'text' || chunk.vlmDescription) && getChunkTypeTag(chunk)}
                 {isStandaloneImage && originalName && (
                     <Tag color="cyan" style={{ marginRight: 0 }} title={`来自: ${originalName}`}>
                         来自: {originalName.length > 8 ? originalName.slice(0, 8) + '...' : originalName}
                     </Tag>
                 )}
             </div>
         </div>
       );
   };

  // 计算 grid 列宽样式
  const cardStyle: React.CSSProperties = {
      width: `calc((100% - ${(columnCount - 1) * 12}px) / ${columnCount})`,
      cursor: 'pointer',
      padding: 8,
      minHeight: 120, // 确保有一定高度
      display: 'flex',
      flexDirection: 'column',
  };

  return (
    <Card
      key={chunk.id}
      className={styles.chunkCard}
      style={cardStyle}
      bodyStyle={{ padding: 8, flex: 1, display: 'flex', flexDirection: 'column' }}
      onClick={() => onClick(chunk)}
      hoverable
      size="small"
    >
      {renderChunkHeader()}
      
      <div className={styles.previewContent} style={{ 
          fontSize: 12, 
          lineHeight: 1.4, 
          color: '#666',
          flex: 1,
          overflow: 'hidden',
          display: '-webkit-box',
          WebkitLineClamp: 4, // 限制行数
          WebkitBoxOrient: 'vertical',
          wordBreak: 'break-all'
      }}>
        {imageUrl ? (
          <div style={{ 
              height: '100%', 
              width: '100%',
              display: 'flex', 
              alignItems: 'center', 
              justifyContent: 'center', 
              background: '#f5f5f5', 
              borderRadius: 4,
              overflow: 'hidden'
          }}>
              <img 
                  src={imageUrl} 
                  alt="chunk thumbnail" 
                  style={{ 
                      width: '100%', 
                      height: '100%', 
                      objectFit: 'cover' 
                  }} 
              />
          </div>
        ) : (
          displayContent
        )}
      </div>
    </Card>
  );
};

export default ChunkCard;
