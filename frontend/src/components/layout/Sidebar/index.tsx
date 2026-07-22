/**
 * 侧边栏组件
 */

import React from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { Menu } from 'antd';
import {
  DatabaseOutlined,
  MessageOutlined,
  SettingOutlined,
} from '@ant-design/icons';
import type { MenuProps } from 'antd';
import styles from './Sidebar.module.css';

type MenuItem = Required<MenuProps>['items'][number];

const menuItems: MenuItem[] = [
  {
    key: '/kb',
    icon: <DatabaseOutlined />,
    label: '知识库管理',
  },
  {
    key: '/chat',
    icon: <MessageOutlined />,
    label: '智能对话',
  },
  {
    key: '/settings',
    icon: <SettingOutlined />,
    label: '系统设置',
  },
];

const Sidebar: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();

  // 根据当前路径获取选中的菜单项
  const getSelectedKey = () => {
    const { pathname } = location;
    if (pathname.startsWith('/kb')) return '/kb';
    if (pathname.startsWith('/chat')) return '/chat';
    if (pathname.startsWith('/settings')) return '/settings';
    return '/kb';
  };

  const handleMenuClick: MenuProps['onClick'] = ({ key }) => {
    navigate(key);
  };

  return (
    <aside className={styles.sidebar}>
      <div className={styles.logo}>
        <div className={styles.logoIcon}>
          <DatabaseOutlined />
        </div>
        <span className={styles.logoText}>AgentRAG</span>
      </div>

      <Menu
        mode="inline"
        selectedKeys={[getSelectedKey()]}
        items={menuItems}
        onClick={handleMenuClick}
        className={styles.menu}
      />

      <div className={styles.footer}>
        <span>v0.1.0</span>
      </div>
    </aside>
  );
};

export default Sidebar;
