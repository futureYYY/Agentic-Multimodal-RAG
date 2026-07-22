/**
 * 主内容区组件
 */

import React from 'react';
import { Outlet } from 'react-router-dom';
import styles from './MainContent.module.css';

const MainContent: React.FC = () => {
  return (
    <main className={styles.mainContent}>
      <Outlet />
    </main>
  );
};

export default MainContent;
