/**
 * 应用主布局组件
 */

import React from 'react';
import Sidebar from '../Sidebar';
import MainContent from '../MainContent';
import styles from './AppLayout.module.css';

const AppLayout: React.FC = () => {
  return (
    <div className={styles.appLayout}>
      <Sidebar />
      <MainContent />
    </div>
  );
};

export default AppLayout;
