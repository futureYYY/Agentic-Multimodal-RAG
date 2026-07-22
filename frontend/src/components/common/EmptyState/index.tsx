/**
 * 空状态组件
 */

import React from 'react';
import { Empty, Button } from 'antd';
import styles from './EmptyState.module.css';

interface EmptyStateProps {
  title?: string;
  description?: string;
  actionText?: string;
  onAction?: () => void;
  image?: React.ReactNode;
}

const EmptyState: React.FC<EmptyStateProps> = ({
  title = '暂无数据',
  description,
  actionText,
  onAction,
  image,
}) => {
  return (
    <div className={styles.emptyState}>
      <Empty
        image={image || Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          <div className={styles.content}>
            <div className={styles.title}>{title}</div>
            {description && <div className={styles.description}>{description}</div>}
          </div>
        }
      >
        {actionText && onAction && (
          <Button type="primary" onClick={onAction}>
            {actionText}
          </Button>
        )}
      </Empty>
    </div>
  );
};

export default EmptyState;
