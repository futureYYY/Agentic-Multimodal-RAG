/**
 * 智能对话页面
 */

import React, { useState, useEffect, useRef } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { Card, Select, Button, Input, Spin, Collapse, Tag, message, Segmented, Slider, Divider, Space, Tooltip, Modal, Switch, Typography } from 'antd';
import {
  SendOutlined,
  StopOutlined,
  EditOutlined,
  ReloadOutlined,
  LinkOutlined,
  RobotOutlined,
  MessageOutlined,
  SettingOutlined,
  DeleteOutlined,
  QuestionCircleOutlined,
} from '@ant-design/icons';
import { PageHeader, MarkdownRenderer, EmptyState } from '@/components/common';
import { getKnowledgeBaseList, getModelList, rewriteQuery, createChatStream } from '@/services';
import { useAppStore, useChatStore } from '@/stores';
import type { ChatMessage, AgentStep, Citation, KnowledgeBase, ModelInfo } from '@/types';
import styles from './Chat.module.css';

const { Panel } = Collapse;
const { Paragraph } = Typography;

interface ChatConfig {
  kbIds: string[];
  topK: number;
  scoreThreshold: number;
  rerankEnabled: boolean;
  rerankScoreThreshold: number;
  rerankModelId?: string;
}

const Chat: React.FC = () => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const urlKbId = searchParams.get('kb_id');

  const { knowledgeBases, setKnowledgeBases, models, setModels } = useAppStore();
  
  // 派生 rerankModels
  const rerankModels = (models || []).filter((m) => m.type === 'rerank');
  
  // 从 store 中只解构需要的变量，避免解构 messages 等已被本地状态替代的变量
  const {
    selectedModel,
    setSelectedModel,
    // isGenerating, // Removed: 使用新的分离状态
    // setIsGenerating, // Removed
    // abortController, // Removed
    // setAbortController, // Removed
    isAgentGenerating,
    setAgentGenerating,
    isNormalGenerating,
    setNormalGenerating,
    agentAbortController,
    setAgentAbortController,
    normalAbortController,
    setNormalAbortController,
    agentMessages,
    setAgentMessages,
    normalMessages,
    setNormalMessages,
  } = useChatStore();

  const [inputValue, setInputValue] = useState('');
  const [rewriting, setRewriting] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const shouldAutoScrollRef = useRef(true);
  
  // 详情弹窗状态
  const [citationModalVisible, setCitationModalVisible] = useState(false);
  const [selectedCitation, setSelectedCitation] = useState<Citation | null>(null);

  // 模式状态
  const [chatMode, setChatMode] = useState<'normal' | 'agent'>('agent');
  
  // 当前模式的衍生状态
  const isGenerating = chatMode === 'normal' ? isNormalGenerating : isAgentGenerating;
  const setIsGenerating = (val: boolean) => {
    if (chatMode === 'normal') setNormalGenerating(val);
    else setAgentGenerating(val);
  };
  const abortController = chatMode === 'normal' ? normalAbortController : agentAbortController;
  const setAbortController = (ctrl: (() => void) | null) => {
    if (chatMode === 'normal') setNormalAbortController(ctrl);
    else setAgentAbortController(ctrl);
  };

  // 当前显示的 messages 引用
  const messages = chatMode === 'normal' ? normalMessages : agentMessages;
  
  // 包装 addMessage 和 updateMessage 以支持独立状态
  const addMessage = (msg: ChatMessage) => {
    if (chatMode === 'normal') {
      setNormalMessages(prev => [...prev, msg]);
    } else {
      setAgentMessages(prev => [...prev, msg]);
    }
  };
  
  const updateMessage = (id: string, updates: Partial<ChatMessage>) => {
    const setter = chatMode === 'normal' ? setNormalMessages : setAgentMessages;
    setter(prev => prev.map(msg => msg.id === id ? { ...msg, ...updates } : msg));
  };
  
  const clearMessages = () => {
    if (chatMode === 'normal') {
      setNormalMessages([]);
    } else {
      setAgentMessages([]);
    }
  };

  const [normalConfig, setNormalConfig] = useState<ChatConfig>({
    kbIds: [],
    topK: 3,
    scoreThreshold: 0.3,
    rerankEnabled: true,
    rerankScoreThreshold: 0.0,
    rerankModelId: undefined,
  });

  const [agentConfig, setAgentConfig] = useState<ChatConfig>({
    kbIds: [],
    topK: 3,
    scoreThreshold: 0.3,
    rerankEnabled: true,
    rerankScoreThreshold: 0.0,
    rerankModelId: undefined,
  });

  // 获取当前模式的配置
  const currentConfig = chatMode === 'normal' ? normalConfig : agentConfig;
  const setCurrentConfig = (newConfig: Partial<ChatConfig>) => {
    if (chatMode === 'normal') {
      setNormalConfig({ ...normalConfig, ...newConfig });
    } else {
      setAgentConfig({ ...agentConfig, ...newConfig });
    }
  };

  // 初始化加载
  useEffect(() => {
    const init = async () => {
      try {
        const [kbRes, modelRes] = await Promise.all([
          getKnowledgeBaseList(),
          getModelList(),
        ]);

        const kbData = Array.isArray(kbRes) ? kbRes : (kbRes as any).data || [];
        setKnowledgeBases(Array.isArray(kbData) ? kbData : []);

        const modelData = Array.isArray(modelRes) ? modelRes : (modelRes as any).data || [];
        setModels(Array.isArray(modelData) ? modelData : []);

        // 设置默认 LLM 模型
        const llmModels = (Array.isArray(modelData) ? modelData : []).filter((m: ModelInfo) => m.type === 'llm');
        if (llmModels.length > 0 && !selectedModel) {
          setSelectedModel(llmModels[0].id);
        }

        // 设置默认 Rerank 模型
        const rerankModels = (Array.isArray(modelData) ? modelData : []).filter((m: ModelInfo) => m.type === 'rerank');
        if (rerankModels.length > 0) {
           const defaultRerankId = rerankModels[0].id;
           setNormalConfig(prev => ({ ...prev, rerankModelId: defaultRerankId }));
           setAgentConfig(prev => ({ ...prev, rerankModelId: defaultRerankId }));
        }

        // URL 参数预选知识库 (仅针对当前默认模式 - Agent)
        if (urlKbId && (Array.isArray(kbData) ? kbData : []).some((kb: KnowledgeBase) => kb.id === urlKbId)) {
          setAgentConfig(prev => ({ ...prev, kbIds: [urlKbId] }));
          setNormalConfig(prev => ({ ...prev, kbIds: [urlKbId] }));
        }
      } catch (error) {
        console.error('初始化失败:', error);
      }
    };
    init();
  }, []);

  // 处理滚动事件
  const handleScroll = () => {
    if (messagesContainerRef.current) {
      const { scrollTop, scrollHeight, clientHeight } = messagesContainerRef.current;
      // 如果距离底部小于 100px，则允许自动滚动
      const isBottom = scrollHeight - scrollTop - clientHeight < 100;
      shouldAutoScrollRef.current = isBottom;
    }
  };

  // 自动滚动到底部
  useEffect(() => {
    if ((isGenerating || messages.length > 0) && shouldAutoScrollRef.current) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages, isGenerating]);


  // 处理问题改写
  const handleRewrite = async () => {
    if (!inputValue.trim()) return;

    setRewriting(true);
    try {
      const response = await rewriteQuery({ query: inputValue.trim(), model_id: selectedModel });
      setInputValue(response.data.rewritten_query);
      message.success('问题改写成功');
    } catch (error) {
      message.error('改写失败');
    } finally {
      setRewriting(false);
    }
  };

  // 生成消息 ID
  const generateMessageId = () => `msg_${Date.now()}_${Math.random().toString(36).slice(2)}`;

  // 发送消息
  const handleSend = async () => {
    if (!inputValue.trim() || isGenerating) return;

    // 发送新消息时强制滚动到底部
    shouldAutoScrollRef.current = true;

    // 捕获当前的 chatMode，供闭包内使用
    const capturedChatMode = chatMode;

    const userMessage: ChatMessage = {
      id: generateMessageId(),
      role: 'user',
      content: inputValue.trim(),
      timestamp: new Date().toISOString(),
    };

    const assistantMessage: ChatMessage = {
      id: generateMessageId(),
      role: 'assistant',
      content: '',
      agentSteps: [],
      citations: [],
      timestamp: new Date().toISOString(),
      isStreaming: true,
    };

    addMessage(userMessage);
    addMessage(assistantMessage);
    setInputValue('');
    setIsGenerating(true);

    // 平滑输出控制
    let accumulatedContent = '';
    
    // 动画帧 ID
    let animationFrameId: number;

    const updateMessageContent = (content: string) => {
        const setter = capturedChatMode === 'normal' ? setNormalMessages : setAgentMessages;
        setter(prev => prev.map(msg => msg.id === assistantMessage.id ? { 
            ...msg, 
            content: content 
        } : msg));
    };

    const { abort } = createChatStream(
      {
        messages: [...messages, userMessage].map((m) => ({
          role: m.role,
          content: m.content,
        })),
        kb_ids: currentConfig.kbIds,
        stream: true,
        mode: chatMode,
        top_k: currentConfig.topK,
        score_threshold: currentConfig.scoreThreshold,
        model_id: selectedModel,
        rerank_enabled: currentConfig.rerankEnabled,
        rerank_score_threshold: currentConfig.rerankScoreThreshold,
        rerank_model_id: currentConfig.rerankModelId,
      },
      {
        onAgentThought: (data) => {
            const step: AgentStep = {
              type: data.step,
              content: data.content,
              timestamp: new Date().toISOString(),
              duration: data.duration, // 后端返回的耗时
              cost: data.cost, // 总耗时
            };
            
            // 获取当前最新的消息列表
            // 注意：这里不能直接用 messages 闭包，需要用 setXxx 的回调或者 ref
            // 但为了简单，我们重新实现 updateMessage 的逻辑
            // 使用 capturedChatMode 来确保回调更新的是发起请求时的模式
            const setter = capturedChatMode === 'normal' ? setNormalMessages : setAgentMessages;
            setter(prev => {
                const msgs = [...prev];
                const msgIndex = msgs.findIndex(m => m.id === assistantMessage.id);
                if (msgIndex > -1) {
                    const msg = { ...msgs[msgIndex] };
                    msg.agentSteps = [...(msg.agentSteps || []), step];
                    msgs[msgIndex] = msg;
                }
                return msgs;
            });
          },
          onRagResult: (data) => {
             // 同样的逻辑更新 citations
            const setter = capturedChatMode === 'normal' ? setNormalMessages : setAgentMessages;
            setter(prev => {
                const msgs = [...prev];
                const msgIndex = msgs.findIndex(m => m.id === assistantMessage.id);
                if (msgIndex > -1) {
                    msgs[msgIndex] = { 
                      ...msgs[msgIndex], 
                      citations: data.citations,
                      original_citations: data.original_citations
                    };
                }
                return msgs;
            });
          },
        onAnswerChunk: (data) => {
          accumulatedContent += data.content;
          
          // 使用 requestAnimationFrame 进行节流，避免过于频繁的 setState
          if (!animationFrameId) {
              animationFrameId = requestAnimationFrame(() => {
                  updateMessageContent(accumulatedContent);
                  animationFrameId = 0;
              });
          }
        },
        onDone: () => {
          if (animationFrameId) {
             cancelAnimationFrame(animationFrameId);
             animationFrameId = 0;
          }
          
          // 确保最后一次内容被更新
          updateMessageContent(accumulatedContent);

          const finish = () => {
             // 这里的 updateMessage 依赖 chatMode 状态，如果用户切换了模式，会导致更新错误的消息列表
             // 应该使用 capturedChatMode
             const setter = capturedChatMode === 'normal' ? setNormalMessages : setAgentMessages;
             setter(prev => prev.map(msg => msg.id === assistantMessage.id ? { 
                 ...msg, 
                 content: accumulatedContent,
                 isStreaming: false 
             } : msg));

             // 清理生成状态
             if (capturedChatMode === 'normal') {
                 setNormalGenerating(false);
                 setNormalAbortController(null);
             } else {
                 setAgentGenerating(false);
                 setAgentAbortController(null);
             }
          };

          finish();
        },
        onError: (error) => {
          if (animationFrameId) {
             cancelAnimationFrame(animationFrameId);
             animationFrameId = 0;
          }
          console.error('Chat Error:', error);
          
          const setter = capturedChatMode === 'normal' ? setNormalMessages : setAgentMessages;
          setter(prev => prev.map(msg => msg.id === assistantMessage.id ? { 
             ...msg, 
             content: `发生错误，请重试: ${error.message || '未知错误'}`,
             error: error.message,
             isStreaming: false 
          } : msg));

          if (capturedChatMode === 'normal') {
             setNormalGenerating(false);
             setNormalAbortController(null);
          } else {
             setAgentGenerating(false);
             setAgentAbortController(null);
          }
        },
      }
    );

    // 设置 controller
    if (chatMode === 'normal') {
        setNormalAbortController(() => abort);
    } else {
        setAgentAbortController(() => abort);
    }
  };

  // 停止生成
  const handleStop = () => {
    // 停止当前的 controller
    if (chatMode === 'normal') {
        normalAbortController?.();
        setNormalGenerating(false);
        setNormalAbortController(null);
    } else {
        agentAbortController?.();
        setAgentGenerating(false);
        setAgentAbortController(null);
    }
  };

  // 重试
  const handleRetry = () => {
    if (messages.length < 2) return;
    const lastUserMessage = [...messages].reverse().find((m) => m.role === 'user');
    if (lastUserMessage) {
      setInputValue(lastUserMessage.content);
      clearMessages();
      messages.slice(0, -2).forEach((m) => addMessage(m));
    }
  };

  // 渲染 Agent 思考步骤
  const renderAgentSteps = (steps: AgentStep[]) => {
    return (
      <Collapse ghost size="small" className={styles.agentSteps} defaultActiveKey={['steps']}>
        <Panel 
          header={
            <Space>
              <RobotOutlined />
              <span>思考过程</span>
              {steps.length > 0 && steps[steps.length - 1].cost && (
                  <span style={{ color: '#888', fontSize: '12px', marginLeft: '8px' }}>
                      总耗时: {Number(steps[steps.length - 1].cost).toFixed(2)}s
                  </span>
              )}
            </Space>
          } 
          key="steps"
        >
          <div className={styles.stepList}>
            {steps.map((step, index) => (
              <div key={index} className={styles.stepItem}>
                <Tag color={
                  step.type === 'thinking' ? 'blue' :
                  step.type === 'action' ? 'orange' :
                  step.type === 'decision' ? 'green' :
                  step.type === 'response' ? 'purple' : 'default'
                }>
                  {step.type === 'thinking' ? '分析意图' :
                   step.type === 'action' ? '调用工具' :
                   step.type === 'decision' ? '判定检索' :
                   step.type === 'response' ? '生成回复' : step.type}
                </Tag>
                <div className={styles.stepContent} style={{ flex: 1, minWidth: 0 }}>
                    <Paragraph 
                        ellipsis={{ 
                            rows: 2, 
                            expandable: true, 
                            symbol: '展开',
                            onExpand: (e, info) => console.log('Expand:', info)
                        }}
                        style={{ marginBottom: 0, color: 'inherit' }}
                        title={undefined} // 禁用原生 title 提示，避免重复
                    >
                        {step.content}
                    </Paragraph>
                </div>
                {step.duration && (
                    <span style={{ color: '#aaa', fontSize: '12px', marginLeft: '8px', flexShrink: 0 }}>
                        {Number(step.duration).toFixed(2)}s
                    </span>
                )}
              </div>
            ))}
          </div>
        </Panel>
      </Collapse>
    );
  };

  // 渲染引用来源
  const renderCitations = (citations: Citation[], title: string = "参考来源") => {
    if (citations.length === 0) return null;

    return (
      <Collapse ghost className={styles.citations}>
        <Panel
          header={`${title} (${citations.length})`}
          key="citations"
          extra={<LinkOutlined />}
        >
          <div className={styles.citationList}>
            {citations.map((citation, index) => (
              <div key={index} className={styles.citationItem}>
                <div className={styles.citationHeader}>
                  <Tag color="orange">{citation.kb_name || '未知知识库'}</Tag>
                  <Tooltip title={citation.fileId ? "点击查看文件详情" : "文件 ID 未知"}>
                      <Tag 
                        color="blue" 
                        style={{ cursor: citation.fileId ? 'pointer' : 'default' }}
                        onClick={() => {
                            if (citation.fileId && citation.kb_id) {
                                // 新窗口打开或者跳转
                                // navigate(`/kb/${citation.kb_id}/preview/${citation.fileId}`);
                                window.open(`/kb/${citation.kb_id}/preview/${citation.fileId}`, '_blank');
                            } else if (citation.fileId && currentConfig.kbIds.length === 1) {
                                // 如果 citation 没有 kb_id 但当前只选了一个知识库，尝试使用当前知识库
                                window.open(`/kb/${currentConfig.kbIds[0]}/preview/${citation.fileId}`, '_blank');
                            }
                        }}
                      >
                        {citation.fileName || '未知文件'}
                      </Tag>
                  </Tooltip>
                  <Tag>{citation.location || '未知位置'}</Tag>
                  <Tag color="green">相似度: {citation.score.toFixed(3)}</Tag>
                  {citation.rerank_score !== undefined && citation.rerank_score !== null && (
                      <Tag color="purple">Rerank: {citation.rerank_score.toFixed(3)}</Tag>
                  )}
                </div>
                {citation.content && (
                  <div 
                    className={styles.citationContent}
                    onClick={() => {
                        setSelectedCitation(citation);
                        setCitationModalVisible(true);
                    }}
                    style={{ cursor: 'pointer' }}
                    title="点击查看完整内容"
                  >
                    {citation.image_path ? (
                        <div style={{ textAlign: 'center' }}>
                            <img 
                                src={`http://localhost:8000/static/images/${citation.image_path}`} 
                                alt="引用图片" 
                                style={{ maxWidth: '100%', maxHeight: '200px', objectFit: 'contain' }}
                            />
                        </div>
                    ) : (
                        // 修改：不截断内容，由 CSS 控制高度
                        <div style={{
                            display: '-webkit-box',
                            WebkitLineClamp: 3,
                            WebkitBoxOrient: 'vertical',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis'
                        }}>
                            {citation.content}
                        </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        </Panel>
      </Collapse>
    );
  };

  // 渲染消息
  const renderMessage = (msg: ChatMessage) => {
    const isUser = msg.role === 'user';

    return (
      <div
        key={msg.id}
        className={`${styles.messageWrapper} ${isUser ? styles.userMessage : styles.assistantMessage}`}
      >
        <div className={styles.messageContent}>
          {!isUser && msg.agentSteps && msg.agentSteps.length > 0 && (
            renderAgentSteps(msg.agentSteps)
          )}

          <div className={styles.messageBubble}>
            {msg.isStreaming && !msg.content ? (
              <div className={styles.thinking}>
                <Spin size="small" />
                <span>思考中...</span>
              </div>
            ) : msg.error ? (
              <div className={styles.error}>
                <span>{msg.content}</span>
                <Button
                  type="link"
                  size="small"
                  icon={<ReloadOutlined />}
                  onClick={handleRetry}
                >
                  重试
                </Button>
              </div>
            ) : (
              msg.content && <MarkdownRenderer content={msg.content} />
            )}
          </div>

          {!isUser && (
              <>
                  {msg.citations && renderCitations(msg.citations, msg.original_citations?.length ? "参考来源 (Rerank)" : "参考来源")}
                  {msg.original_citations && msg.original_citations.length > 0 && renderCitations(msg.original_citations, "初排结果 (Embedding)")}
              </>
          )}
        </div>
      </div>
    );
  };

  const chatModels = (models || []).filter((m) => m.type === 'llm' || m.type === 'vlm');

  return (
    <div className={styles.container}>
      <PageHeader
        title="智能对话"
        subtitle="基于知识库的智能问答"
        onBack={() => navigate(-1)}
        extra={
          <Segmented
            value={chatMode}
            onChange={(value) => setChatMode(value as 'normal' | 'agent')}
            options={[
              { label: 'Agent 模式', value: 'agent', icon: <RobotOutlined /> },
              { label: '普通模式', value: 'normal', icon: <MessageOutlined /> },
            ]}
          />
        }
      />

      {/* 配置区 */}
      <Card className={styles.configCard}>
        <div className={styles.configRow}>
          {chatMode === 'normal' && (
            <div className={styles.configItem}>
              <label>知识库</label>
              <Select
                mode="multiple"
                placeholder="选择知识库（可多选）"
                value={currentConfig.kbIds}
                onChange={(val) => setCurrentConfig({ kbIds: val })}
                style={{ width: 300 }}
                options={knowledgeBases.map((kb) => ({
                  value: kb.id,
                  label: kb.name,
                }))}
              />
            </div>
          )}
          
          <div className={styles.configItem}>
            <label>模型</label>
            <Select
              placeholder="选择 LLM 模型"
              value={selectedModel}
              onChange={setSelectedModel}
              style={{ width: 200 }}
              options={(chatModels || []).map((m) => ({
                value: m.id,
                label: m.name,
              }))}
            />
          </div>
          
          <Divider type="vertical" />
          
          <div className={styles.configItem} style={{ flex: 1, justifyContent: 'flex-end' }}>
             <Space size="large">
               <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                 <SettingOutlined />
                 <span style={{ whiteSpace: 'nowrap' }}>Top K: {currentConfig.topK}</span>
                 <Tooltip title={
                    <div style={{ fontSize: '12px' }}>
                        <div><b>策略：分库/分任务检索 + 汇总重排</b></div>
                        <div style={{ marginTop: 4 }}>此处 Top K 指<b>单次检索/单知识库</b>的召回数量。</div>
                        <div style={{ marginTop: 4 }}>系统会汇总所有检索结果（总量可能 &gt; Top K），再经 Rerank 优选出最终回答依据。</div>
                    </div>
                 }>
                    <QuestionCircleOutlined style={{ color: '#999', cursor: 'help' }} />
                 </Tooltip>
                 <Slider 
                          min={1} max={100} 
                          value={currentConfig.topK}
                          onChange={(val) => setCurrentConfig({ topK: val })}
                          style={{ width: 100 }}
                        />
               </div>
               
               <Divider type="vertical" style={{ height: 24 }} />
               
               <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                 <span style={{ whiteSpace: 'nowrap' }}>阈值: {currentConfig.scoreThreshold}</span>
                 <Slider 
                   min={0} max={1} step={0.1}
                   value={currentConfig.scoreThreshold}
                   onChange={(val) => setCurrentConfig({ scoreThreshold: val })}
                   style={{ width: 100 }}
                 />
               </div>

               <Divider type="vertical" style={{ height: 24 }} />

               <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                 <Switch 
                    checkedChildren="Rerank" 
                    unCheckedChildren="Rerank"
                    checked={currentConfig.rerankEnabled}
                    onChange={(checked) => setCurrentConfig({ rerankEnabled: checked })}
                 />
                 {currentConfig.rerankEnabled && (
                    <>
                        <Select
                            placeholder="选择 Rerank 模型"
                            value={currentConfig.rerankModelId}
                            onChange={(val) => setCurrentConfig({ rerankModelId: val })}
                            style={{ width: 160 }}
                            options={rerankModels.map((m) => ({
                                value: m.id,
                                label: m.name,
                            }))}
                        />
                        <span style={{ whiteSpace: 'nowrap' }}>阈值: {currentConfig.rerankScoreThreshold}</span>
                        <Slider 
                          min={0} max={1} step={0.01}
                          value={currentConfig.rerankScoreThreshold}
                          onChange={(val) => setCurrentConfig({ rerankScoreThreshold: val })}
                          style={{ width: 100 }}
                        />
                    </>
                 )}
               </div>
             </Space>
          </div>
        </div>
      </Card>

      {/* 对话区 */}
      <Card className={styles.chatCard}>
        <div 
          className={styles.messagesContainer}
          ref={messagesContainerRef}
          onScroll={handleScroll}
        >
          {messages.length === 0 ? (
            <EmptyState
              title={chatMode === 'agent' ? 'Agent 智能问答' : '知识库对话'}
              description={chatMode === 'agent' 
                ? 'Agent 将自动分析您的意图，并调用合适的知识库进行回答' 
                : '选择知识库，直接进行检索问答'}
            />
          ) : (
            <>
              {messages.map(renderMessage)}
              <div ref={messagesEndRef} />
            </>
          )}
        </div>

        {/* 输入区 */}
        <div className={styles.inputArea}>
          <div className={styles.inputToolbar} style={{ marginBottom: 8, display: 'flex', justifyContent: 'flex-end' }}>
             <Tooltip title="清除上下文（开启新对话）">
                <Button 
                    type="text"
                    icon={<DeleteOutlined />} 
                    onClick={() => {
                        Modal.confirm({
                            title: '确认清除上下文？',
                            content: '清除后将开始新的对话，之前的记忆将丢失。',
                            onOk: clearMessages,
                        });
                    }}
                >
                    清除上下文
                </Button>
            </Tooltip>
          </div>
          <Input.TextArea
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            placeholder={chatMode === 'agent' ? "输入问题，Agent 将为您思考..." : "输入问题..."}
            autoSize={{ minRows: 1, maxRows: 4 }}
            onPressEnter={(e) => {
              if (!e.shiftKey) {
                e.preventDefault();
                handleSend();
              }
            }}
            disabled={isGenerating}
            className={styles.input}
          />
          <div className={styles.inputActions}>
            {chatMode === 'normal' && (
              <Button
                icon={<EditOutlined />}
                onClick={handleRewrite}
                loading={rewriting}
                disabled={!inputValue.trim() || isGenerating}
              >
                改写
              </Button>
            )}
            
            {isGenerating ? (
              <Button
                type="primary"
                danger
                icon={<StopOutlined />}
                onClick={handleStop}
              >
                停止
              </Button>
            ) : (
              <Button
                type="primary"
                icon={<SendOutlined />}
                onClick={handleSend}
                disabled={!inputValue.trim()}
              >
                发送
              </Button>
            )}
          </div>
        </div>
      </Card>
      {/* 引用详情弹窗 */}
      <Modal
        title="参考来源详情"
        open={citationModalVisible}
        onCancel={() => setCitationModalVisible(false)}
        footer={[
          <Button key="close" onClick={() => setCitationModalVisible(false)}>
            关闭
          </Button>
        ]}
        width={800}
      >
        {selectedCitation && (
          <div style={{ maxHeight: '60vh', overflowY: 'auto' }}>
            <div style={{ marginBottom: 16 }}>
               <Tag color="orange">{selectedCitation.kb_name || '未知知识库'}</Tag>
               <Tag color="blue">{selectedCitation.fileName || '未知文件'}</Tag>
               <Tag>{selectedCitation.location || '未知位置'}</Tag>
               <Tag color="green">
                  {selectedCitation.rerank_score 
                    ? `Rerank: ${selectedCitation.rerank_score.toFixed(3)}` 
                    : `相似度: ${selectedCitation.score.toFixed(3)}`}
               </Tag>
            </div>
            <div style={{ whiteSpace: 'pre-wrap', lineHeight: '1.6', fontSize: '15px' }}>
                {selectedCitation.image_path ? (
                    <div style={{ textAlign: 'center' }}>
                        <img 
                            src={`http://localhost:8000/static/images/${selectedCitation.image_path}`} 
                            alt="引用图片详情" 
                            style={{ maxWidth: '100%', borderRadius: '8px' }}
                        />
                    </div>
                ) : (
                    selectedCitation.content
                )}
            </div>
            {/* 如果有图片，虽然 Citation 类型定义里暂时没有 image_path，但后端可能传过来。
                如果需要展示图片，可以在 Citation 类型中增加 image_path 字段。
                目前后端已经增加了 image_path，但前端 types/chat.ts 还没加。
                不过 JS 运行时是有的。我们先暂时忽略图片展示，或者加上。
             */}
          </div>
        )}
      </Modal>
    </div>
  );
};

export default Chat;
