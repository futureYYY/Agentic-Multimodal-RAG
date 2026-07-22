/**
 * 图片灯箱组件
 */

import React from 'react';
import { Modal, Image } from 'antd';
import styles from './ImageLightbox.module.css';

interface ImageLightboxProps {
  visible: boolean;
  imageUrl: string;
  title?: string;
  onClose: () => void;
}

const ImageLightbox: React.FC<ImageLightboxProps> = ({
  visible,
  imageUrl,
  title,
  onClose,
}) => {
  return (
    <Modal
      open={visible}
      title={title}
      footer={null}
      onCancel={onClose}
      width="auto"
      centered
      className={styles.lightboxModal}
    >
      <div className={styles.imageContainer}>
        <Image
          src={imageUrl}
          alt={title}
          preview={false}
          className={styles.image}
        />
      </div>
    </Modal>
  );
};

export default ImageLightbox;
