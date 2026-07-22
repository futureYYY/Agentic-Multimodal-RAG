/**
 * 召回测试模块
 *
 * 支持三种检索模式：
 * - vector: 仅向量检索
 * - fulltext: 仅 BM25 检索
 * - hybrid: 混合检索（默认）
 */

import React, { useState, useEffect } from 'react';
import { Card, Input, Slider, Button, Row, Col, Spin, Tag, Progress, Image, Switch, Divider, Select, message, Radio, Tooltip, Tabs, Table, Popconfirm, Modal, Space } from 'antd';
import { SearchOutlined, FileTextOutlined, InfoCircleOutlined, ThunderboltOutlined, HistoryOutlined, DeleteOutlined, ClearOutlined, EyeOutlined } from '@ant-design/icons';
import { EmptyState, MarkdownRenderer } from '@/components/common';
import { executeRecallTest, getCustomModels, getRetrievalLogs, deleteRetrievalLog, clearRetrievalLogs } from '@/services';
import { DEFAULT_RECALL_PARAMS } from '@/utils';
import type { RecallResult, CustomModel, RetrievalLog } from '@/types';
import styles from './RecallTest.module.css';
import dayjs from 'dayjs';

interface RecallTestProps {
  kbId: string;
}

const RecallTest: React.FC<RecallTestProps> = ({ kbId }) => {
  const [activeTab, setActiveTab] = useState<'test' | 'history'>('test');

  // 测试配置状态
  const [query, setQuery] = useState('');
  const [topK, setTopK] = useState(50);
  const [scoreThreshold, setScoreThreshold] = useState(0);

  // 新增：检索模式
  const [searchMode, setSearchMode] = useState<'hybrid' | 'vector' | 'fulltext'>('hybrid');
  const [vectorWeight, setVectorWeight] = useState(0.7);
  const [bm25Weight, setBm25Weight] = useState(0.3);

  // Rerank 配置
  const [rerankEnabled, setRerankEnabled] = useState(true);
  const [rerankScoreThreshold, setRerankScoreThreshold] = useState(0.0);
  const [rerankTopK, setRerankTopK] = useState(20);
  const [rerankModelId, setRerankModelId] = useState<string>();
  const [rerankModels, setRerankModels] = useState<CustomModel[]>([]);

  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<RecallResult[]>([]);
  const [queryTime, setQueryTime] = useState<number | null>(null);
  const [hasSearched, setHasSearched] = useState(false);

  // 历史记录状态
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyLogs, setHistoryLogs] = useState<RetrievalLog[]>([]);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [historyPage, setHistoryPage] = useState(1);
  const [historyPageSize, setHistoryPageSize] = useState(10);
  const [detailModalVisible, setDetailModalVisible] = useState(false);
  const [selectedLog, setSelectedLog] = useState<RetrievalLog | null>(null);

  // 获取模型列表
  useEffect(() => {
    const fetchModels = async () => {
      try {
        const res = await getCustomModels();
        const models = res.data.filter((m: CustomModel) => m.model_type === 'rerank' && m.is_active);
        setRerankModels(models);
        if (models.length > 0) {
          setRerankModelId(models[0].id);
        }
      } catch (error) {
        console.error('Failed to fetch models:', error);
      }
    };
    fetchModels();
  }, []);

  // 获取历史记录
  const fetchHistory = async (page = 1, pageSize = 10) => {
    setHistoryLoading(true);
    try {
      const res = await getRetrievalLogs({
        kb_id: kbId,
        page,
        page_size: pageSize,
      });
      setHistoryLogs(res.data.logs);
      setHistoryTotal(res.data.total);
      setHistoryPage(res.data.page);
      setHistoryPageSize(res.data.page_size);
    } catch (error) {
      console.error('Failed to fetch history:', error);
      message.error('获取历史记录失败');
    } finally {
      setHistoryLoading(false);
    }
  };

  // 切换到历史记录时加载数据
  useEffect(() => {
    if (activeTab === 'history') {
      fetchHistory(historyPage, historyPageSize);
    }
  }, [activeTab, kbId]);

  // 执行召回测试
  const handleSearch = async () => {
    if (!query.trim()) return;

    setLoading(true);
    setHasSearched(true);
    try {
      const response = await executeRecallTest(kbId, {
        query: query.trim(),
        topK,
        scoreThreshold,
        rerank_enabled: rerankEnabled,
        rerank_score_threshold: rerankScoreThreshold,
        rerank_model_id: rerankModelId,
        // 新增参数
        search_mode: searchMode,
        vector_weight: vectorWeight,
        bm25_weight: bm25Weight,
        rerank_top_k: rerankTopK,
      });
      setResults(response.data.results);
      setQueryTime(response.data.queryTime);
    } catch (error) {
      setResults([]);
      message.error('召回测试失败');
    } finally {
      setLoading(false);
    }
  };

  // 删除单条历史记录
  const handleDeleteLog = async (logId: string) => {
    try {
      await deleteRetrievalLog(logId);
      message.success('删除成功');
      fetchHistory(historyPage, historyPageSize);
    } catch (error) {
      message.error('删除失败');
    }
  };

  // 清空历史记录
  const handleClearHistory = async () => {
    try {
      await clearRetrievalLogs(kbId);
      message.success('清空成功');
      fetchHistory(1, historyPageSize);
    } catch (error) {
      message.error('清空失败');
    }
  };

  // 查看历史记录详情
  const handleViewDetail = (log: RetrievalLog) => {
    setSelectedLog(log);
    setDetailModalVisible(true);
  };

  // 历史记录表格列定义
  const historyColumns = [
    {
      title: '查询',
      dataIndex: 'query',
      key: 'query',
      width: 200,
      ellipsis: true,
      render: (text: string) => (
        <Tooltip title={text}>
          <span>{text.length > 30 ? text.slice(0, 30) + '...' : text}</span>
        </Tooltip>
      ),
    },
    {
      title: '模式',
      dataIndex: 'search_mode',
      key: 'search_mode',
      width: 80,
      render: (mode: string) => {
        const colorMap: Record<string, string> = {
          hybrid: 'purple',
          vector: 'blue',
          fulltext: 'orange',
        };
        return <Tag color={colorMap[mode] || 'default'}>{mode}</Tag>;
      },
    },
    {
      title: '召回数',
      key: 'counts',
      width: 120,
      render: (_: any, record: RetrievalLog) => (
        <Space size="small">
          <Tooltip title={`向量: ${record.vector_count}, BM25: ${record.bm25_count}`}>
            <Tag>{record.merged_count} → {record.final_count}</Tag>
          </Tooltip>
        </Space>
      ),
    },
    {
      title: '耗时',
      dataIndex: 'latency_ms',
      key: 'latency_ms',
      width: 100,
      render: (v: number) => `${v.toFixed(1)}ms`,
    },
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 160,
      render: (v: string) => dayjs(v).format('YYYY-MM-DD HH:mm:ss'),
    },
    {
      title: '操作',
      key: 'actions',
      width: 120,
      render: (_: any, record: RetrievalLog) => (
        <Space size="small">
          <Tooltip title="查看详情">
            <Button
              type="text"
              size="small"
              icon={<EyeOutlined />}
              onClick={() => handleViewDetail(record)}
            />
          </Tooltip>
          <Popconfirm
            title="确定删除这条记录？"
            onConfirm={() => handleDeleteLog(record.id)}
            okText="确定"
            cancelText="取消"
          >
            <Tooltip title="删除">
              <Button type="text" size="small" danger icon={<DeleteOutlined />} />
            </Tooltip>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  // 渲染结果卡片
  const renderResultCard = (result: RecallResult, index: number) => {
    const scorePercent = Math.round(result.score * 100);
    const scoreColor =
      scorePercent >= 80 ? '#52c41a' : scorePercent >= 60 ? '#faad14' : '#ff4d4f';

    return (
      <Card key={result.chunkId} className={styles.resultCard}>
        <div className={styles.resultHeader}>
          <div className={styles.resultRank}>#{index + 1}</div>
          <div className={styles.scoreSection}>
            {result.rerank_score !== undefined && result.rerank_score !== null ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <div style={{ display: 'flex', alignItems: 'center' }}>
                  <span className={styles.scoreLabel} style={{ marginRight: 4, fontWeight: 'bold' }}>Rerank:</span>
                  <Tag color="geekblue" style={{ fontSize: 14, padding: '2px 8px' }}>
                    {result.rerank_score.toFixed(4)}
                  </Tag>
                </div>
                {result.vector_score !== undefined && result.vector_score !== null && (
                  <div style={{ display: 'flex', alignItems: 'center', opacity: 0.8 }}>
                    <span className={styles.scoreLabel} style={{ marginRight: 4 }}>Vector:</span>
                    <Tag color="cyan">{result.vector_score.toFixed(3)}</Tag>
                  </div>
                )}
                {result.bm25_score !== undefined && result.bm25_score !== null && (
                  <div style={{ display: 'flex', alignItems: 'center', opacity: 0.8 }}>
                    <span className={styles.scoreLabel} style={{ marginRight: 4 }}>BM25:</span>
                    <Tag color="purple">{result.bm25_score.toFixed(3)}</Tag>
                  </div>
                )}
                <div style={{ display: 'flex', alignItems: 'center', opacity: 0.6 }}>
                  <span className={styles.scoreLabel} style={{ marginRight: 4 }}>Fusion:</span>
                  <Tag>{result.score.toFixed(3)}</Tag>
                </div>
              </div>
            ) : (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                {result.vector_score !== undefined && result.vector_score !== null && (
                  <div style={{ display: 'flex', alignItems: 'center' }}>
                    <span className={styles.scoreLabel} style={{ marginRight: 4 }}>Vector:</span>
                    <Tag color="cyan">{result.vector_score.toFixed(3)}</Tag>
                  </div>
                )}
                {result.bm25_score !== undefined && result.bm25_score !== null && (
                  <div style={{ display: 'flex', alignItems: 'center' }}>
                    <span className={styles.scoreLabel} style={{ marginRight: 4 }}>BM25:</span>
                    <Tag color="purple">{result.bm25_score.toFixed(3)}</Tag>
                  </div>
                )}
                <div style={{ display: 'flex', alignItems: 'center' }}>
                  <span className={styles.scoreLabel} style={{ marginRight: 4 }}>Fusion:</span>
                  <Tag color={scoreColor}>{result.score.toFixed(3)}</Tag>
                </div>
              </div>
            )}
          </div>
        </div>

        <div className={styles.resultMeta}>
          <Tag icon={<FileTextOutlined />}>{result.fileName}</Tag>
          {result.location && <Tag>{result.location}</Tag>}
          {result.heading_text && (
            <Tooltip title={`H${result.heading_level}: ${result.heading_text}`}>
              <Tag color="orange">{result.heading_text.slice(0, 20)}{result.heading_text.length > 20 ? '...' : ''}</Tag>
            </Tooltip>
          )}
        </div>

        <div className={styles.resultContent}>
          <MarkdownRenderer content={result.content} />
        </div>

        {result.imageUrl && (
          <div className={styles.resultImage}>
            <Image
              src={result.imageUrl}
              alt="相关图片"
              height={200}
              style={{ objectFit: 'contain' }}
            />
          </div>
        )}
      </Card>
    );
  };

  // 渲染测试页面
  const renderTestTab = () => (
    <Row gutter={24}>
      {/* 左侧：参数配置 */}
      <Col span={8}>
        <Card title="参数配置" className={styles.configCard}>
          <div className={styles.formItem}>
            <label className={styles.label}>查询问题</label>
            <Input.TextArea
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="输入要查询的问题..."
              rows={4}
              onPressEnter={(e) => {
                if (!e.shiftKey) {
                  e.preventDefault();
                  handleSearch();
                }
              }}
            />
          </div>

          <Divider orientation="left" style={{ margin: '12px 0', fontSize: 12 }}>
            <ThunderboltOutlined /> 检索模式
          </Divider>

          <div className={styles.formItem}>
            <Radio.Group
              value={searchMode}
              onChange={(e) => setSearchMode(e.target.value)}
              buttonStyle="solid"
              style={{ width: '100%', display: 'flex' }}
            >
              <Radio.Button value="hybrid" style={{ flex: 1, textAlign: 'center' }}>
                混合检索
              </Radio.Button>
              <Radio.Button value="vector" style={{ flex: 1, textAlign: 'center' }}>
                向量
              </Radio.Button>
              <Radio.Button value="fulltext" style={{ flex: 1, textAlign: 'center' }}>
                BM25
              </Radio.Button>
            </Radio.Group>
          </div>

          {searchMode === 'hybrid' && (
            <div className={styles.formItem}>
              <label className={styles.label}>
                <Tooltip title="调整向量检索和 BM25 的权重比例">
                  <InfoCircleOutlined style={{ marginRight: 4 }} />
                </Tooltip>
                权重配比: 向量 {(vectorWeight * 100).toFixed(0)}% / BM25 {(bm25Weight * 100).toFixed(0)}%
              </label>
              <Slider
                min={0}
                max={1}
                step={0.1}
                value={vectorWeight}
                onChange={(v) => {
                  setVectorWeight(v);
                  setBm25Weight(1 - v);
                }}
                marks={{ 0: 'BM25', 0.5: '均衡', 1: '向量' }}
                tooltip={{ formatter: (v) => `向量: ${((v || 0) * 100).toFixed(0)}%` }}
              />
            </div>
          )}

          <Divider style={{ margin: '12px 0' }} />

          <div className={styles.formItem}>
            <label className={styles.label}>召回数量 (Top K): {topK}</label>
            <Slider
              min={10}
              max={200}
              value={topK}
              onChange={setTopK}
              marks={{ 10: '10', 50: '50', 100: '100', 200: '200' }}
            />
          </div>

          <div className={styles.formItem}>
            <label className={styles.label}>
              相似度阈值: {scoreThreshold.toFixed(2)}
            </label>
            <Slider
              min={0}
              max={1}
              step={0.05}
              value={scoreThreshold}
              onChange={setScoreThreshold}
              marks={{ 0: '0', 0.5: '0.5', 1: '1' }}
            />
          </div>

          <Divider orientation="left" style={{ margin: '12px 0', fontSize: 12 }}>
            Rerank 精排
          </Divider>

          <div className={styles.formItem} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
            <label className={styles.label} style={{ marginBottom: 0 }}>启用 Rerank</label>
            <Switch checked={rerankEnabled} onChange={setRerankEnabled} />
          </div>

          {rerankEnabled && (
            <>
              <div className={styles.formItem}>
                <label className={styles.label}>选择 Rerank 模型</label>
                <Select
                  placeholder="请选择模型"
                  value={rerankModelId}
                  onChange={setRerankModelId}
                  style={{ width: '100%' }}
                  options={rerankModels.map((m) => ({ label: m.name, value: m.id }))}
                />
              </div>
              <div className={styles.formItem}>
                <label className={styles.label}>Rerank 返回数量: {rerankTopK}</label>
                <Slider
                  min={5}
                  max={50}
                  value={rerankTopK}
                  onChange={setRerankTopK}
                  marks={{ 5: '5', 20: '20', 50: '50' }}
                />
              </div>
              <div className={styles.formItem}>
                <label className={styles.label}>
                  Rerank 阈值: {rerankScoreThreshold.toFixed(2)}
                </label>
                <Slider
                  min={0}
                  max={1}
                  step={0.05}
                  value={rerankScoreThreshold}
                  onChange={setRerankScoreThreshold}
                  marks={{ 0: '0', 0.5: '0.5', 1: '1' }}
                />
              </div>
            </>
          )}

          <Button
            type="primary"
            icon={<SearchOutlined />}
            onClick={handleSearch}
            loading={loading}
            disabled={!query.trim()}
            block
            size="large"
            style={{ marginTop: 16 }}
          >
            执行召回
          </Button>
        </Card>
      </Col>

      {/* 右侧：结果展示 */}
      <Col span={16}>
        <Card
          title={
            <span>
              召回结果
              {results.length > 0 && <Tag style={{ marginLeft: 8 }}>{results.length} 条</Tag>}
            </span>
          }
          className={styles.resultContainer}
          extra={
            queryTime !== null && queryTime !== undefined && (
              <span className={styles.queryTime}>
                查询耗时: {Number(queryTime).toFixed(2)}ms
              </span>
            )
          }
        >
          <Spin spinning={loading} size="large">
            <div style={{ minHeight: 300, display: 'flex', flexDirection: 'column' }}>
              {!hasSearched ? (
                <EmptyState
                  title="输入问题开始测试"
                  description="在左侧输入查询问题，点击执行召回查看结果"
                />
              ) : results.length === 0 ? (
                <EmptyState
                  title={loading ? "正在搜索..." : "未找到相关结果"}
                  description={loading ? "请稍候，正在召回相关片段" : "尝试降低相似度阈值或更换关键词"}
                />
              ) : (
                <div className={styles.resultList}>
                  {results.map((result, index) => renderResultCard(result, index))}
                </div>
              )}
            </div>
          </Spin>
        </Card>
      </Col>
    </Row>
  );

  // 渲染历史记录页面
  const renderHistoryTab = () => (
    <Card
      title="检索历史记录"
      extra={
        <Popconfirm
          title="确定清空该知识库的所有历史记录？"
          onConfirm={handleClearHistory}
          okText="确定"
          cancelText="取消"
        >
          <Button icon={<ClearOutlined />} danger size="small">
            清空历史
          </Button>
        </Popconfirm>
      }
    >
      <Table
        columns={historyColumns}
        dataSource={historyLogs}
        rowKey="id"
        loading={historyLoading}
        pagination={{
          current: historyPage,
          pageSize: historyPageSize,
          total: historyTotal,
          showSizeChanger: true,
          showQuickJumper: true,
          showTotal: (total) => `共 ${total} 条记录`,
          onChange: (page, pageSize) => {
            setHistoryPage(page);
            setHistoryPageSize(pageSize);
            fetchHistory(page, pageSize);
          },
        }}
        locale={{ emptyText: <EmptyState title="暂无历史记录" description="执行召回测试后将自动记录" /> }}
      />
    </Card>
  );

  return (
    <div className={styles.container}>
      <Tabs
        activeKey={activeTab}
        onChange={(key) => setActiveTab(key as 'test' | 'history')}
        items={[
          {
            key: 'test',
            label: (
              <span>
                <SearchOutlined />
                召回测试
              </span>
            ),
            children: renderTestTab(),
          },
          {
            key: 'history',
            label: (
              <span>
                <HistoryOutlined />
                历史记录
              </span>
            ),
            children: renderHistoryTab(),
          },
        ]}
      />

      {/* 历史记录详情弹窗 */}
      <Modal
        title="检索详情"
        open={detailModalVisible}
        onCancel={() => setDetailModalVisible(false)}
        footer={[
          <Button key="close" onClick={() => setDetailModalVisible(false)}>
            关闭
          </Button>
        ]}
        width={700}
      >
        {selectedLog && (
          <div>
            <div style={{ marginBottom: 16 }}>
              <Tag color="purple">{selectedLog.search_mode}</Tag>
              <Tag>Top K: {selectedLog.top_k}</Tag>
              <Tag color={selectedLog.rerank_enabled ? 'green' : 'default'}>
                {selectedLog.rerank_enabled ? 'Rerank 启用' : 'Rerank 禁用'}
              </Tag>
            </div>

            <div style={{ marginBottom: 16 }}>
              <strong>查询：</strong>
              <div style={{ padding: '8px 12px', background: '#f5f5f5', borderRadius: 4, marginTop: 4 }}>
                {selectedLog.query}
              </div>
            </div>

            <Row gutter={16} style={{ marginBottom: 16 }}>
              <Col span={8}>
                <div style={{ textAlign: 'center', padding: 12, background: '#f0f5ff', borderRadius: 4 }}>
                  <div style={{ fontSize: 24, fontWeight: 'bold', color: '#1890ff' }}>{selectedLog.vector_count}</div>
                  <div style={{ fontSize: 12, color: '#666' }}>向量召回</div>
                  <div style={{ fontSize: 11, color: '#999' }}>{selectedLog.vector_latency_ms.toFixed(1)}ms</div>
                </div>
              </Col>
              <Col span={8}>
                <div style={{ textAlign: 'center', padding: 12, background: '#f6ffed', borderRadius: 4 }}>
                  <div style={{ fontSize: 24, fontWeight: 'bold', color: '#52c41a' }}>{selectedLog.bm25_count}</div>
                  <div style={{ fontSize: 12, color: '#666' }}>BM25 召回</div>
                  <div style={{ fontSize: 11, color: '#999' }}>{selectedLog.bm25_latency_ms.toFixed(1)}ms</div>
                </div>
              </Col>
              <Col span={8}>
                <div style={{ textAlign: 'center', padding: 12, background: '#fff7e6', borderRadius: 4 }}>
                  <div style={{ fontSize: 24, fontWeight: 'bold', color: '#fa8c16' }}>{selectedLog.final_count}</div>
                  <div style={{ fontSize: 12, color: '#666' }}>最终返回</div>
                  <div style={{ fontSize: 11, color: '#999' }}>{selectedLog.latency_ms.toFixed(1)}ms 总耗时</div>
                </div>
              </Col>
            </Row>

            {selectedLog.search_mode === 'hybrid' && (
              <div style={{ marginBottom: 16 }}>
                <strong>权重配置：</strong>
                <Tag style={{ marginLeft: 8 }}>向量: {(selectedLog.vector_weight * 100).toFixed(0)}%</Tag>
                <Tag>BM25: {(selectedLog.bm25_weight * 100).toFixed(0)}%</Tag>
              </div>
            )}

            {selectedLog.results_summary && selectedLog.results_summary !== '[]' && (
              <div>
                <strong>结果摘要：</strong>
                <div style={{ marginTop: 8 }}>
                  {JSON.parse(selectedLog.results_summary).map((item: any, index: number) => (
                    <div key={index} style={{ padding: 8, background: '#fafafa', borderRadius: 4, marginBottom: 8 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                        <Tag color="blue">{item.file_name}</Tag>
                        <Tag color="green">分数: {item.score?.toFixed(3)}</Tag>
                      </div>
                      <div style={{ fontSize: 12, color: '#666' }}>
                        {item.content?.slice(0, 100)}...
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
};

export default RecallTest;
