// ============================================================
// ChatInterface.tsx — v3.0 (sub-components: ChatMessageBubble, ChatInputBar)
// ============================================================

import React, { useState, useRef, useEffect } from 'react';
import { Bot, Loader2 } from 'lucide-react';
import { API_BASE_URL } from '../config';
import { useAuth } from '../context/AuthContext';
import { useAnalysis } from '../context/AnalysisContext';
import { ChatMessageBubble } from './ChatMessageBubble';
import { ChatInputBar } from './ChatInputBar';

interface OptionItem {
  label: string;
  action: string;
  message?: string;
  value?: string;
}

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  data?: any;
  options?: OptionItem[];
}

interface TriggerEvent {
  message: string;
  eventType: string;
  eventData: any;
}

interface ChatInterfaceProps {
  projectId: string;
  initialMessage?: string;
  triggerMessage?: string;
  onTriggerComplete?: () => void;
  triggerEvent?: TriggerEvent | null;
  onTriggerEventComplete?: () => void;
}

const ChatInterface: React.FC<ChatInterfaceProps> = ({
  projectId,
  initialMessage = "안녕하세요! 무엇을 도와드릴까요?",
  triggerMessage,
  onTriggerComplete,
  triggerEvent,
  onTriggerEventComplete,
}) => {
  const { setAnalysisData, restoreFromHistory, setStageValidation } = useAnalysis();

  const [messages, setMessages] = useState<Message[]>([
    {
      id: '1',
      role: 'assistant',
      content: initialMessage,
      timestamp: new Date(),
    },
  ]);

  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const { analysisData } = useAnalysis();

  const getCurrentTab = (): string | null => {
    if (!analysisData?.data) return null;
    const viewMode = analysisData.data.view_mode;
    const viewToTab: Record<string, string> = {
      'file_uploaded': 'analysis',
      'report': 'analysis',
      'math_model': 'math_model',
      'solver': 'solver',
      'result': 'result',
    };
    return viewToTab[viewMode] || null;
  };

  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const { user, authFetch } = useAuth();

  useEffect(() => {
    setMessages([{
      id: 'init',
      role: 'assistant' as const,
      content: initialMessage,
      timestamp: new Date(),
    }]);

    const loadHistory = async () => {
      try {
        if (!user) return;
        const params = new URLSearchParams({ project_id: projectId });
        const res = await authFetch(`${API_BASE_URL}/api/chat/history?${params.toString()}`);
        if (!res.ok) return;
        const history = await res.json();
        if (history.length > 0) {
          const restored: Message[] = history.map((h: any, idx: number) => ({
            id: `history-${h.id || idx}`,
            role: h.role as 'user' | 'assistant',
            content: h.text || '',
            timestamp: new Date(),
            type: h.type || 'text',
            data: h.card_data || undefined,
            options: h.options || undefined,
          }));
          setMessages(restored);
          // 모든 history의 card_data를 순서대로 복원하여 completedSteps + stepCache 재구성
          const allCardData = history
            .filter((h: any) => h.card_data)
            .map((h: any) => h.card_data);
          if (allCardData.length > 0) {
            restoreFromHistory(allCardData);
          }
        }
      } catch (e) {
        console.error('채팅 기록 로드 실패:', e);
      }
    };
    loadHistory();
  }, [projectId]);

  useEffect(() => {
    if (triggerMessage && triggerMessage.trim() !== '') {
      handleSendMessage(triggerMessage);
      if (onTriggerComplete) onTriggerComplete();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [triggerMessage]);

  useEffect(() => {
    if (triggerEvent) {
      handleSendEvent(triggerEvent.message, triggerEvent.eventType, triggerEvent.eventData);
      if (onTriggerEventComplete) onTriggerEventComplete();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [triggerEvent]);

  const handleOptionClick = (opt: OptionItem) => {
    switch (opt.action) {
      case 'send': {
        const text = opt.message || opt.value || opt.label;
        handleSendMessage(text);
        break;
      }
      case 'upload':
        // ChatInputBar handles file input ref internally;
        // trigger via DOM query as fallback
        document.querySelector<HTMLInputElement>('input[type="file"]')?.click();
        break;
      case 'download':
        handleDownload();
        break;
      default: {
        const fallbackText = opt.message || opt.value || opt.label;
        if (fallbackText) handleSendMessage(fallbackText);
        break;
      }
    }
  };

  const handleDownload = async () => {
    try {
      const res = await authFetch(
        `${API_BASE_URL}/api/projects/${projectId}/report/download?format=md`,
      );
      if (!res.ok) {
        alert('다운로드할 리포트가 없습니다.');
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `analysis_report_${projectId}.md`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error('Download error:', err);
      alert('다운로드 중 오류가 발생했습니다.');
    }
  };

  const handleSendMessage = async (text: string) => {
    if (!text.trim()) return;

    const newMessage: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: text,
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, newMessage]);
    setInputValue('');
    setIsLoading(true);

    try {
      const response = await authFetch(`${API_BASE_URL}/api/chat/message`, {
        method: 'POST',
        body: JSON.stringify({
          message: text,
          project_id: projectId,
          current_tab: getCurrentTab(),
        }),
      });

      if (!response.ok) {
        if (response.status === 401) throw new Error('세션이 만료되었습니다.');
        const errorText = await response.text();
        throw new Error(`Server Error: ${errorText}`);
      }

      const data = await response.json();

      const botResponse: Message = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: data.text || '응답 내용이 없습니다.',
        timestamp: new Date(),
        options: data.options,
      };

      setMessages((prev) => [...prev, botResponse]);

      if (data.data) {
        console.log('📊 Analysis Data Received:', data.data);
        console.log('📊 proposal keys:', data.data.proposal ? Object.keys(data.data.proposal) : 'no proposal');
        if (data.data.proposal) {
          console.log('📊 proposal.hard_constraints:', Object.keys(data.data.proposal.hard_constraints || {}));
          console.log('📊 proposal.parameters:', Object.keys(data.data.proposal.parameters || {}));
          console.log('📊 proposal.objective:', data.data.proposal.objective?.target);
        }
        const incoming = data.data;
        const isMinimalResult = incoming.view_mode === 'result' && !incoming.compile_summary && !incoming.status;
        if (isMinimalResult) {
          console.log('⏭️ Skipping minimal result data (detailed data already set by SolverView)');
        } else if (incoming.view_mode === 'normalization_complete') {
          console.log('⏭️ Skipping normalization_complete (auto-next handles transition)');
        } else {
          setAnalysisData(incoming);
        }
      }

      // auto_next: 자동으로 다음 단계 진행
      if (data.data?.auto_next) {
        const autoMap: Record<string, string> = {
          'data_normalization': '데이터 정규화 시작',
          'confirm_normalization': '확인',
          'math_model': '수학 모델 생성해줘',
        };
        const nextMsg = autoMap[data.data.auto_next] || data.data.auto_next;
        setTimeout(async () => {
          try {
            const autoResp = await authFetch(`${API_BASE_URL}/api/chat/message`, {
              method: 'POST',
              body: JSON.stringify({
                message: nextMsg,
                project_id: projectId,
                current_tab: getCurrentTab(),
              }),
            });
            if (autoResp.ok) {
              const autoData = await autoResp.json();
              if (autoData.text) {
                setMessages((prev) => [...prev, {
                  id: (Date.now() + 2).toString(),
                  role: 'assistant',
                  content: autoData.text,
                  timestamp: new Date(),
                  options: autoData.options,
                }]);
              }
              if (autoData.data && autoData.data.view_mode !== 'normalization_complete') {
                setAnalysisData(autoData.data);
              }
              // Chain: if this response also has auto_next
              if (autoData.data?.auto_next) {
                const nextNext = autoMap[autoData.data.auto_next] || autoData.data.auto_next;
                setTimeout(async () => {
                  setMessages((prev) => [...prev, {
                    id: (Date.now() + 4).toString(),
                    role: 'assistant',
                    content: '🔄 수학 모델을 생성하고 있습니다... 잠시만 기다려주세요.',
                    timestamp: new Date(),
                  }]);
                  const chainResp = await authFetch(`${API_BASE_URL}/api/chat/message`, {
                    method: 'POST',
                    body: JSON.stringify({
                      message: nextNext,
                      project_id: projectId,
                      current_tab: getCurrentTab(),
                    }),
                  });
                  if (chainResp.ok) {
                    const chainData = await chainResp.json();
                    if (chainData.text) {
                      setMessages((prev) => prev.filter(m => m.content !== '🔄 수학 모델을 생성하고 있습니다... 잠시만 기다려주세요.').concat([{
                        id: (Date.now() + 3).toString(),
                        role: 'assistant',
                        content: chainData.text,
                        timestamp: new Date(),
                        options: chainData.options,
                      }]));
                    }
                    if (chainData.data) {
                      setAnalysisData(chainData.data);
                    }
                  }
                  setIsLoading(false);
                }, 500);
              } else {
                setIsLoading(false);
              }
            }
          } catch (e) {
            console.error('auto_next error:', e);
            setIsLoading(false);
          }
        }, 500);
        return;
      }
    } catch (error: any) {
      console.error('Chat Error:', error);
      setMessages((prev) => [
        ...prev,
        {
          id: Date.now().toString(),
          role: 'assistant',
          content: '오류가 발생했습니다. 다시 시도해 주세요.',
          timestamp: new Date(),
        },
      ]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleSendEvent = async (text: string, eventType: string, eventData: any) => {
    const newMessage: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: text,
      timestamp: new Date(),
    };
    setMessages((prev) => [...prev, newMessage]);
    setIsLoading(true);

    try {
      const response = await authFetch(`${API_BASE_URL}/api/chat/message`, {
        method: 'POST',
        body: JSON.stringify({
          message: text,
          project_id: projectId,
          current_tab: getCurrentTab(),
          event_type: eventType,
          event_data: eventData,
        }),
      });

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`Server Error: ${errorText}`);
      }

      const data = await response.json();

      const botResponse: Message = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: data.text || '응답 내용이 없습니다.',
        timestamp: new Date(),
        options: data.options,
      };
      setMessages((prev) => [...prev, botResponse]);

      if (data.data) {
        setAnalysisData(data.data);
      }

      if (data.data?.auto_next) {
        const autoMap: Record<string, string> = {
          'data_normalization': '데이터 정규화 시작',
          'confirm_normalization': '확인',
          'math_model': '수학 모델 생성해줘',
        };
        const nextMsg = autoMap[data.data.auto_next] || data.data.auto_next;
        setTimeout(() => handleSendMessage(nextMsg), 500);
      }
    } catch (error) {
      console.error('Event Send Error:', error);
      setMessages((prev) => [...prev, {
        id: Date.now().toString(),
        role: 'assistant',
        content: '오류가 발생했습니다. 다시 시도해 주세요.',
        timestamp: new Date(),
      }]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleFileUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files;
    if (!files || files.length === 0) return;

    setMessages((prev) => [
      ...prev,
      {
        id: Date.now().toString(),
        role: 'user',
        content: `📂 파일 업로드 중... (${files.length}개)`,
        timestamp: new Date(),
      },
    ]);
    setIsLoading(true);

    const formData = new FormData();
    Array.from(files).forEach((file) => {
      formData.append('files', file);
    });
    formData.append('project_id', projectId);

    try {
      const uploadRes = await authFetch(`${API_BASE_URL}/api/upload`, {
        method: 'POST',
        body: formData,
      });

      if (!uploadRes.ok) throw new Error('Upload failed');

      const uploadData = await uploadRes.json();
      const uploadedFiles = uploadData.uploaded_files || [];

      if (uploadData.validation) {
        setStageValidation(uploadData.validation);
      }

      const triggerRes = await authFetch(`${API_BASE_URL}/api/chat/message`, {
        method: 'POST',
        body: JSON.stringify({
          message: '',
          project_id: projectId,
          event_type: 'FILES_UPLOADED',
          event_data: { files: uploadedFiles },
        }),
      });

      const triggerData = await triggerRes.json();

      const botResponse: Message = {
        id: Date.now().toString(),
        role: 'assistant',
        content: triggerData.text || `파일 ${files.length}개가 처리되었습니다.`,
        timestamp: new Date(),
        options: triggerData.options,
      };

      setMessages((prev) => [...prev, botResponse]);

      if (triggerData.data) {
        setAnalysisData(triggerData.data);
      }
    } catch (error) {
      console.error('Upload Error:', error);
      setMessages((prev) => [
        ...prev,
        {
          id: Date.now().toString(),
          role: 'assistant',
          content: '파일 업로드에 실패했습니다. 다시 시도해 주세요.',
          timestamp: new Date(),
        },
      ]);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-full w-full bg-slate-900 overflow-hidden">
      {/* 메시지 영역 */}
      <div className="flex-1 overflow-y-auto p-4 space-y-6 custom-scrollbar">
        {messages.map((message) => (
          <ChatMessageBubble
            key={message.id}
            message={message}
            onOptionClick={handleOptionClick}
          />
        ))}
        {isLoading && (
          <div className="flex items-start gap-4 justify-start">
            <div className="flex-shrink-0 flex h-10 w-10 items-center justify-center rounded-full bg-slate-700 border border-slate-600">
              <Bot className="h-5 w-5 text-cyan-400" />
            </div>
            <div className="flex items-center gap-3 rounded-2xl bg-slate-800 px-5 py-4 border border-slate-700 rounded-tl-none">
              <Loader2 className="h-5 w-5 animate-spin text-cyan-500" />
              <span className="text-sm text-slate-400">Processing...</span>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* 입력 영역 */}
      <ChatInputBar
        inputValue={inputValue}
        isLoading={isLoading}
        onInputChange={setInputValue}
        onSend={handleSendMessage}
        onFileUpload={handleFileUpload}
      />
    </div>
  );
};

export default ChatInterface;
