// ============================================================
// ChatInterface.tsx — v2.0 (options 구조 정합 + action 처리)
// ============================================================

import React, { useState, useRef, useEffect } from 'react';
import { Send, Paperclip, Bot, User, Loader2 } from 'lucide-react';
import { API_BASE_URL } from '../config';
import { useAuth } from '../context/AuthContext';
import { useAnalysis } from '../context/AnalysisContext';

// ── 수정 1: options 인터페이스를 백엔드 구조에 맞춤 ──
interface OptionItem {
  label: string;
  action: string;          // "send" | "upload" | "download"
  message?: string;        // action이 "send"일 때 전송할 메시지
  value?: string;          // 하위 호환용
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
  const { setAnalysisData } = useAnalysis();

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

  // Derive current tab from analysisData
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
  const fileInputRef = useRef<HTMLInputElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  // 프로젝트 진입 시 DB에서 채팅 기록 복원
  const { user } = useAuth();

  useEffect(() => {
    // 프로젝트 전환 시 초기 안내 메시지로 리셋
    setMessages([{
      id: 'init',
      role: 'assistant' as const,
      content: initialMessage,
      timestamp: new Date(),
    }]);

    const loadHistory = async () => {
      try {
        if (!user) return;
        const params = new URLSearchParams({
          project_id: projectId,
          user: user.name,
          role: user.role,
        });
        const res = await fetch(`${API_BASE_URL}/api/chat/history?${params.toString()}`);
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
          // 마지막 카드 데이터를 오른쪽 패널에 복원
          if (setAnalysisData) {
            for (let i = history.length - 1; i >= 0; i--) {
              if (history[i].card_data) {
                setAnalysisData(history[i].card_data);
                break;
              }
            }
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

  // ── 수정 2: 옵션 버튼 클릭 핸들러 ──
  const handleOptionClick = (opt: OptionItem) => {
    switch (opt.action) {
      case 'send':
        // message 또는 value 중 있는 것 사용
        const text = opt.message || opt.value || opt.label;
        handleSendMessage(text);
        break;

      case 'upload':
        fileInputRef.current?.click();
        break;

      case 'download':
        handleDownload();
        break;

      default:
        // action이 없거나 알 수 없는 경우 → message/value/label 순으로 전송
        const fallbackText = opt.message || opt.value || opt.label;
        if (fallbackText) handleSendMessage(fallbackText);
        break;
    }
  };

  // 다운로드 핸들러
  const handleDownload = async () => {
    try {
      const token = localStorage.getItem('token') || '';
      const res = await fetch(
        `${API_BASE_URL}/api/projects/${projectId}/report/download?format=md`,
        {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        }
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

  // 1. 메시지 전송 (일반 대화)
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
      const response = await fetch(`${API_BASE_URL}/api/chat/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: text,
          project_id: projectId,
          current_tab: getCurrentTab(),
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

      // 오른쪽 패널 업데이트
      if (data.data) {
        console.log('📊 Analysis Data Received:', data.data);
        // 채팅 응답의 간략 데이터(4개 키)가 SolverView에서 설정한 상세 데이터(13개 키)를
        // 덮어쓰지 않도록 방지: 같은 view_mode의 상세 데이터가 이미 있으면 스킵
        const incoming = data.data;
        const isMinimalResult = incoming.view_mode === 'result' && !incoming.compile_summary && !incoming.status;
        if (isMinimalResult) {
          console.log('⏭️ Skipping minimal result data (detailed data already set by SolverView)');
        } else if (incoming.view_mode === 'normalization_complete') {
          console.log('⏭️ Skipping normalization_complete (auto-next handles transition)');
        } else {
          setAnalysisData(incoming);
        }

        // target_tab은 view_mode를 통해 자동 반영됨
        // setAnalysisData가 view_mode 기반으로 stepCache와 completedSteps를 갱신하므로
        // 별도의 switchToStep 호출 불필요 (클로저 race condition 방지)
      }

      // auto_next: 자동으로 다음 단계 진행 (채팅에 표시하지 않음)
      if (data.data?.auto_next) {
        const autoMap: Record<string, string> = {
          'data_normalization': '데이터 정규화 시작',
          'confirm_normalization': '확인',
          'math_model': '수학 모델 생성해줘',
        };
        const nextMsg = autoMap[data.data.auto_next] || data.data.auto_next;
        setTimeout(async () => {
          try {
            const autoResp = await fetch(`${API_BASE_URL}/api/chat/message`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                message: nextMsg,
                project_id: projectId,
                current_tab: getCurrentTab(),
              }),
            });
            if (autoResp.ok) {
              const autoData = await autoResp.json();
              // Only show bot response, not user message
              if (autoData.text) {
                setMessages((prev) => [...prev, {
                  id: (Date.now() + 2).toString(),
                  role: 'assistant',
                  content: autoData.text,
                  timestamp: new Date(),
                  options: autoData.options,
                }]);
              }
              // Update panel only if not normalization_complete
              if (autoData.data && autoData.data.view_mode !== 'normalization_complete') {
                setAnalysisData(autoData.data);
              }
              // Chain: if this response also has auto_next
              if (autoData.data?.auto_next) {
                const nextNext = autoMap[autoData.data.auto_next] || autoData.data.auto_next;
                setTimeout(async () => {
                  // Show loading message while math model generates
              setMessages((prev) => [...prev, {
                id: (Date.now() + 4).toString(),
                role: 'assistant',
                content: '🔄 수학 모델을 생성하고 있습니다... 잠시만 기다려주세요.',
                timestamp: new Date(),
              }]);
              const chainResp = await fetch(`${API_BASE_URL}/api/chat/message`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                      message: nextNext,
                      project_id: projectId,
                      current_tab: getCurrentTab(),
                    }),
                  });
                  if (chainResp.ok) {
                    const chainData = await chainResp.json();
                    if (chainData.text) {
                      // Remove loading message and add actual response
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
        return; // Don't setIsLoading(false) here, chain handles it
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

  // 2. 이벤트 전송 (패널 → 백엔드 구조화 이벤트)
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
      const response = await fetch(`${API_BASE_URL}/api/chat/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
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

  // 3. 파일 업로드 핸들러
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
      const uploadRes = await fetch(`${API_BASE_URL}/api/upload`, {
        method: 'POST',
        body: formData,
      });

      if (!uploadRes.ok) throw new Error('Upload failed');

      const uploadData = await uploadRes.json();
      const uploadedFiles = uploadData.uploaded_files || [];

      const triggerRes = await fetch(`${API_BASE_URL}/api/chat/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
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

      // 파일 업로드 후에도 패널 업데이트
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
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  return (
    <div className="flex flex-col h-full w-full bg-slate-900 overflow-hidden">
      {/* 메시지 영역 */}
      <div className="flex-1 overflow-y-auto p-4 space-y-6 custom-scrollbar">
        {messages.map((message) => {
          const isUser = message.role === 'user';
          return (
            <div
              key={message.id}
              className={`flex w-full items-start gap-4 ${
                isUser ? 'justify-end' : 'justify-start'
              }`}
            >
              {!isUser && (
                <div className="flex-shrink-0 flex h-10 w-10 items-center justify-center rounded-full bg-slate-700 border border-slate-600 shadow-sm">
                  <Bot className="h-5 w-5 text-cyan-400" />
                </div>
              )}
              <div
                className={`flex flex-col gap-2 max-w-[75%] ${
                  isUser ? 'items-end' : 'items-start'
                }`}
              >
                <div
                  className={`rounded-2xl px-5 py-3.5 text-[15px] leading-relaxed shadow-md ${
                    isUser
                      ? 'bg-indigo-600 text-white rounded-tr-none'
                      : 'bg-slate-800 border border-slate-700 text-slate-200 rounded-tl-none'
                  }`}
                >
                  <p className="whitespace-pre-wrap">{message.content}</p>
                </div>

                {/* ── 수정 3: 옵션 버튼 — handleOptionClick 사용 ── */}
                {message.options && message.options.length > 0 && (
                  <div className="flex flex-wrap gap-2 mt-1">
                    {message.options.map((opt, idx) => (
                      <button
                        key={idx}
                        onClick={() => handleOptionClick(opt)}
                        className="px-3 py-1.5 text-xs font-medium text-cyan-300 bg-slate-800 border border-slate-600 hover:bg-slate-700 hover:border-cyan-500 rounded-full transition-all"
                      >
                        {opt.label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              {isUser && (
                <div className="flex-shrink-0 flex h-10 w-10 items-center justify-center rounded-full bg-indigo-600 shadow-lg shadow-indigo-500/20">
                  <User className="h-5 w-5 text-white" />
                </div>
              )}
            </div>
          );
        })}
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
      <div className="p-4 bg-slate-900 border-t border-slate-800">
        <div className="flex gap-3 relative max-w-4xl mx-auto">
          <input
            type="file"
            multiple
            className="hidden"
            ref={fileInputRef}
            onChange={handleFileUpload}
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            className="flex-shrink-0 h-12 w-12 flex items-center justify-center rounded-xl border border-slate-700 text-slate-400 hover:bg-slate-800 hover:text-cyan-400 hover:border-cyan-500/50 transition-all"
          >
            <Paperclip size={20} />
          </button>
          <input
            type="text"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && !isLoading && handleSendMessage(inputValue)}
            placeholder="메시지를 입력하세요..."
            disabled={isLoading}
            className="flex-1 rounded-xl border border-slate-700 bg-slate-800 px-4 py-3 text-sm text-white placeholder-slate-500 focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 focus:outline-none transition-all disabled:opacity-50"
          />
          <button
            onClick={() => handleSendMessage(inputValue)}
            disabled={!inputValue.trim() || isLoading}
            className="flex-shrink-0 h-12 w-12 flex items-center justify-center rounded-xl bg-indigo-600 text-white hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed transition-all shadow-lg shadow-indigo-500/30"
          >
            <Send size={20} />
          </button>
        </div>
      </div>
    </div>
  );
};

export default ChatInterface;