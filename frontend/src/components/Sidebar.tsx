import React, { useState } from 'react';
import { 
  LayoutDashboard, 
  Users, 
  Truck, 
  TrendingUp, 
  FlaskConical, 
  Settings, 
  LogOut,
  ChevronLeft,
  ChevronDown,
  ChevronRight
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { useProjectContext } from '../context/ProjectContext';

interface SidebarProps {
  collapsed: boolean;
  toggleCollapse: () => void;
  onSettingsClick?: () => void;
}

const Sidebar: React.FC<SidebarProps> = ({ collapsed, toggleCollapse, onSettingsClick }) => {
  const { logout } = useAuth();
  // 🌟 전체 프로젝트 목록과 현재 선택된 프로젝트 정보를 가져옵니다.
  const { projects, currentProject, setCurrentProject } = useProjectContext();

  // 🌟 메뉴 펼침 상태 관리 (기본적으로 Crew는 펼쳐둠)
  const [expandedCategories, setExpandedCategories] = useState<string[]>(['crew']);

  // 카테고리 토글 함수
  const toggleCategory = (type: string) => {
    setExpandedCategories(prev => 
      prev.includes(type) ? prev.filter(t => t !== type) : [...prev, type]
    );
  };

  // 메뉴 정의 (type 필드가 중요)
  const categories = [
    { id: 'crew', label: 'Crew Scheduling', icon: Users, type: 'crew' },
    { id: 'logistics', label: 'Logistics', icon: Truck, type: 'logistics' },
    { id: 'finance', label: 'Finance', icon: TrendingUp, type: 'finance' },
    { id: 'material', label: 'Material', icon: FlaskConical, type: 'material' },
  ];

  return (
    <div className="flex flex-col h-full bg-slate-900 text-slate-300 select-none">
      
      {/* 1. 헤더 */}
      <div className={`h-16 flex items-center ${collapsed ? 'justify-center' : 'justify-between px-4'} border-b border-slate-800 transition-all`}>
        <div 
            className="flex items-center gap-3 overflow-hidden cursor-pointer"
            onClick={() => setCurrentProject(null)} // 로고 누르면 홈으로
        >
          <div className="w-8 h-8 bg-indigo-600 rounded-lg flex items-center justify-center flex-shrink-0 shadow-lg shadow-indigo-500/30">
            <span className="font-bold text-white text-lg">Q</span>
          </div>
          {!collapsed && (
            <span className="font-bold text-white text-lg tracking-tight whitespace-nowrap animate-fade-in">
              KQC Solver
            </span>
          )}
        </div>
        {!collapsed && (
          <button onClick={toggleCollapse} className="text-slate-500 hover:text-white transition">
            <ChevronLeft size={20} />
          </button>
        )}
      </div>

      {/* 2. 네비게이션 트리 */}
      <div className="flex-1 py-4 overflow-y-auto custom-scrollbar overflow-x-hidden">
        <div className="space-y-1 px-3">
          
          {/* (1) Dashboard (홈) 버튼 */}
          <button
            onClick={() => setCurrentProject(null)}
            className={`w-full flex items-center ${collapsed ? 'justify-center px-0' : 'px-3'} py-2.5 rounded-lg transition-all duration-200 ${
              currentProject === null 
                ? 'bg-indigo-600 text-white shadow-md' 
                : 'hover:bg-slate-800 hover:text-white'
            }`}
            title="Dashboard"
          >
            <LayoutDashboard size={20} className="flex-shrink-0" />
            {!collapsed && <span className="ml-3 text-sm font-medium">Dashboard</span>}
          </button>

          {/* 구분선 */}
          <div className="my-2 border-t border-slate-800 mx-2"></div>

          {/* (2) 도메인별 아코디언 메뉴 */}
          {categories.map((category) => {
            // 해당 카테고리에 속한 프로젝트 필터링
            const categoryProjects = projects.filter(p => p.type === category.type);
            const isExpanded = expandedCategories.includes(category.type);
            
            // 현재 활성화된 프로젝트가 이 카테고리 안에 있는지 확인
            const isActiveCategory = currentProject?.type === category.type;

            return (
              <div key={category.id} className="space-y-1">
                {/* 상위 카테고리 버튼 */}
                <button
                  onClick={() => {
                      if (collapsed) toggleCollapse(); // 접힌 상태면 펼치기
                      toggleCategory(category.type);
                  }}
                  className={`w-full flex items-center ${collapsed ? 'justify-center px-0' : 'justify-between px-3'} py-2.5 rounded-lg transition-colors group ${
                    isActiveCategory ? 'text-indigo-400' : 'text-slate-400 hover:bg-slate-800 hover:text-white'
                  }`}
                  title={category.label}
                >
                  <div className="flex items-center">
                    <category.icon size={20} className="flex-shrink-0" />
                    {!collapsed && <span className="ml-3 text-sm font-medium">{category.label}</span>}
                  </div>
                  {/* 펼침/접힘 화살표 (사이드바가 펼쳐져 있을 때만 보임) */}
                  {!collapsed && (
                    <div className="text-slate-600 group-hover:text-slate-400">
                        {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    </div>
                  )}
                </button>

                {/* 하위 프로젝트 리스트 (아코디언 내용) */}
                {!collapsed && isExpanded && (
                  <div className="ml-4 pl-3 border-l border-slate-700 space-y-1 animate-fade-in-up origin-top">
                    {categoryProjects.length > 0 ? (
                      categoryProjects.map(project => (
                        <button
                          key={project.id}
                          onClick={() => setCurrentProject(project)} // 🌟 클릭 시 바로 이동!
                          className={`w-full text-left px-3 py-2 rounded-md text-xs truncate transition-colors ${
                            currentProject?.id === project.id
                              ? 'bg-indigo-500/10 text-indigo-300 border border-indigo-500/20'
                              : 'text-slate-500 hover:text-slate-300 hover:bg-slate-800/50'
                          }`}
                          title={project.title}
                        >
                          {project.title}
                        </button>
                      ))
                    ) : (
                      <div className="px-3 py-2 text-xs text-slate-600 italic">No projects</div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* 3. 하단 영역 */}
      <div className="p-4 border-t border-slate-800 space-y-1">
        {collapsed && (
            <button onClick={toggleCollapse} className="w-full flex justify-center py-2 text-slate-500 hover:text-white">
                <ChevronRight size={20} />
            </button>
        )}
          <button onClick={onSettingsClick} className={`w-full flex items-center ${collapsed ? 'justify-center' : 'px-3'} py-2.5 rounded-lg text-slate-400 hover:bg-slate-800 hover:text-white transition-colors`}>
          <Settings size={20} />
          {!collapsed && <span className="ml-3 text-sm">Settings</span>}
        </button>
        <button onClick={logout} className={`w-full flex items-center ${collapsed ? 'justify-center' : 'px-3'} py-2.5 rounded-lg text-rose-400 hover:bg-rose-900/20 hover:text-rose-300 transition-colors`}>
          <LogOut size={20} />
          {!collapsed && <span className="ml-3 text-sm">Logout</span>}
        </button>
      </div>
    </div>
  );
};

export default Sidebar;