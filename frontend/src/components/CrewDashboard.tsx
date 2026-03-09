import React, { useState, useRef, useEffect } from 'react';
import ChatInterface from './ChatInterface';
import AnalysisReport from './AnalysisReport';
// AnalysisContext is used directly by AnalysisReport and ChatInterface 
import { MoreHorizontal, Calendar, User as UserIcon } from 'lucide-react';
import { useProjectContext } from '../context/ProjectContext';

export default function CrewDashboard() {
  const { currentProject } = useProjectContext();
  
  // 패널 너비 및 리사이징 상태
  const [panelWidth, setPanelWidth] = useState(45); 
  const [isResizing, setIsResizing] = useState(false);
  
  // 채팅에서 받은 데이터
  // analysisData is now managed via AnalysisContext inside AnalysisReport
  
  // 🌟 [핵심 추가] 우측 패널에서 버튼 클릭 시, 채팅창으로 명령을 전달할 변수
  const [autoMessage, setAutoMessage] = useState<string>("");
  const [autoEvent, setAutoEvent] = useState<{ message: string; eventType: string; eventData: any } | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);

  // 리사이징 로직
  const startResizing = (e: React.MouseEvent) => {
    e.preventDefault();
    setIsResizing(true);
  };

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isResizing || !containerRef.current) return;
      const containerRect = containerRef.current.getBoundingClientRect();
      const newWidth = ((containerRect.right - e.clientX) / containerRect.width) * 100;
      if (newWidth > 20 && newWidth < 80) setPanelWidth(newWidth);
    };
    const handleMouseUp = () => setIsResizing(false);

    if (isResizing) {
      window.addEventListener('mousemove', handleMouseMove);
      window.addEventListener('mouseup', handleMouseUp);
    }
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isResizing]);

  // 🌟 [핵심 추가] 우측 패널(Report)에서 액션 버튼을 눌렀을 때 호출되는 함수
  const handleReportAction = (_actionType: string, label: string) => {
      console.log(`📡 Dashboard Action Relay: ${label}`);
      // 버튼의 텍스트(예: "최적화 실행해줘")를 채팅창으로 자동 입력 및 전송
      setAutoMessage(label);
  };

  // 🌟 [핵심 추가] 구조화 이벤트 (문제 정의 확정 등)를 채팅창으로 전달
  const handleReportEvent = (message: string, eventType: string, eventData: any) => {
    console.log(`📡 Dashboard Event Relay: ${message} [${eventType}]`);
    setAutoEvent({ message, eventType, eventData });
  };

  const projectId = currentProject?.id || "default";
  const projectTitle = currentProject?.title || "New Project";

  return (
    <div className="flex flex-col h-full w-full bg-slate-950 overflow-hidden">
      
      {/* 워크스페이스 헤더 */}
      <div className="h-16 border-b border-slate-800 bg-slate-900 flex items-center justify-between px-6 flex-shrink-0 shadow-sm z-20">
        <div>
          <h1 className="text-lg font-bold text-white flex items-center gap-3">
            {projectTitle}
            <span className="px-2 py-0.5 rounded-full bg-green-500/10 text-green-400 text-xs border border-green-500/20 font-normal">
              Active
            </span>
          </h1>
          <div className="flex items-center gap-4 text-xs text-slate-500 mt-1">
            <span className="flex items-center gap-1"><UserIcon size={12}/> {currentProject?.owner || 'Admin'}</span>
            <span className="flex items-center gap-1"><Calendar size={12}/> Last updated: Just now</span>
          </div>
        </div>
        <button className="p-2 hover:bg-slate-800 rounded-lg text-slate-400 transition">
          <MoreHorizontal size={20} />
        </button>
      </div>

      {/* 메인 작업 영역 */}
      <div ref={containerRef} className="flex-1 flex overflow-hidden relative">
        
        {/* 🟢 [좌측] 채팅 인터페이스 */}
        <div className="h-full flex flex-col min-w-0" style={{ width: `${100 - panelWidth}%` }}>
          <div className="flex-1 h-full overflow-hidden p-0 relative"> 
             <ChatInterface
                projectId={projectId}
                initialMessage={`안녕하세요! [${projectTitle}] 프로젝트의 최적화를 도와드릴 에이전트입니다.`}
                triggerMessage={autoMessage}
                onTriggerComplete={() => setAutoMessage("")}
                triggerEvent={autoEvent}
                onTriggerEventComplete={() => setAutoEvent(null)}
              />
          </div>
        </div>

        {/* 🟡 [중앙] 리사이징 핸들 */}
        <div 
          onMouseDown={startResizing}
          className={`w-1 h-full cursor-col-resize flex items-center justify-center hover:bg-indigo-500 transition-colors z-30 bg-slate-800 hover:w-1.5`}
        >
        </div>

        {/* 🔵 [우측] 분석 결과 패널 */}
        <div 
          className="h-full flex flex-col bg-slate-900 border-l border-slate-800 shadow-xl z-10"
          style={{ width: `${panelWidth}%` }}
        >
          <AnalysisReport
              projectId={projectId}
              onAction={handleReportAction}
              onEvent={handleReportEvent}
          />
        </div>

      </div>
    </div>
  );
}