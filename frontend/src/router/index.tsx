/**
 * 路由配置
 */

import { createBrowserRouter, Navigate, useRouteError } from 'react-router-dom';
import { AppLayout } from '@/components/layout';
import { Button, Result } from 'antd';

// 懒加载页面组件
import { lazy, Suspense } from 'react';
import { Spin } from 'antd';

// 错误处理组件
const ErrorBoundary = () => {
  const error: any = useRouteError();
  console.error(error);

  return (
    <div style={{ padding: '48px', height: '100%', display: 'flex', justifyContent: 'center', alignItems: 'center' }}>
      <Result
        status="500"
        title="出错了"
        subTitle={error.statusText || error.message || '抱歉，页面发生未知错误'}
        extra={
          <Button type="primary" onClick={() => window.location.reload()}>
            刷新页面
          </Button>
        }
      />
    </div>
  );
};

// 页面组件懒加载
const KnowledgeBaseList = lazy(() => import('@/pages/KnowledgeBase/List'));
const KnowledgeBaseDetail = lazy(() => import('@/pages/KnowledgeBase/Detail'));
const ChunkPreview = lazy(() => import('@/pages/KnowledgeBase/ChunkPreview'));
const MultiFilePreview = lazy(() => import('@/pages/KnowledgeBase/MultiFilePreview'));
const Chat = lazy(() => import('@/pages/Chat'));
const Settings = lazy(() => import('@/pages/Settings'));

// 加载中组件
const PageLoading = () => (
  <div
    style={{
      display: 'flex',
      justifyContent: 'center',
      alignItems: 'center',
      height: '100%',
      minHeight: '400px',
    }}
  >
    <Spin size="large" />
  </div>
);

// 懒加载包装
const LazyComponent = ({ children }: { children: React.ReactNode }) => (
  <Suspense fallback={<PageLoading />}>{children}</Suspense>
);

const router = createBrowserRouter([
  {
    path: '/',
    element: <AppLayout />,
    children: [
      {
        index: true,
        element: <Navigate to="/kb" replace />,
      },
      {
        path: 'kb',
        children: [
          {
            index: true,
            element: (
              <LazyComponent>
                <KnowledgeBaseList />
              </LazyComponent>
            ),
          },
          {
            path: ':kbId',
            element: (
              <LazyComponent>
                <KnowledgeBaseDetail />
              </LazyComponent>
            ),
          },
          {
            path: ':kbId/preview/:fileId',
            element: (
              <LazyComponent>
                <ChunkPreview />
              </LazyComponent>
            ),
          },
          {
            path: ':kbId/preview-all',
            element: (
              <LazyComponent>
                <MultiFilePreview />
              </LazyComponent>
            ),
          },
        ],
      },
      {
        path: 'chat',
        element: (
          <LazyComponent>
            <Chat />
          </LazyComponent>
        ),
      },
      {
        path: 'settings',
        element: (
          <LazyComponent>
            <Settings />
          </LazyComponent>
        ),
      },
    ],
  },
]);

export default router;
