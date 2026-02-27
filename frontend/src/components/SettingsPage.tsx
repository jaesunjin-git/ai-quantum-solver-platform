import { useState } from 'react';
import { Settings, Cpu, X } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import SolverSettings from './SolverSettings';

const TABS = [
  { id: 'solver', label: '솔버 설정', icon: Cpu, component: SolverSettings, adminOnly: true },
];

export default function SettingsPage({ onClose }: { onClose: () => void }) {
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';

  const visibleTabs = TABS.filter((t) => !t.adminOnly || isAdmin);
  const [activeTab, setActiveTab] = useState(visibleTabs[0]?.id || '');

  const ActiveComponent = visibleTabs.find((t) => t.id === activeTab)?.component;

  return (
    <div className="flex-1 flex flex-col bg-slate-950 overflow-hidden">
      {/* 헤더 */}
      <div className="flex-shrink-0 px-8 py-5 border-b border-slate-800 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-cyan-900/30 rounded-lg border border-cyan-500/30">
            <Settings className="text-cyan-400" size={24} />
          </div>
          <div>
            <h1 className="text-xl font-bold text-white">Settings</h1>
            <p className="text-sm text-slate-400">시스템 설정을 관리합니다</p>
          </div>
        </div>
        <button
          onClick={onClose}
          className="p-2 hover:bg-slate-800 rounded-lg text-slate-400 hover:text-white transition"
        >
          <X size={20} />
        </button>
      </div>

      {/* 본문 */}
      <div className="flex-1 flex overflow-hidden">
        {/* 왼쪽 탭 메뉴 */}
        <div className="w-48 flex-shrink-0 border-r border-slate-800 bg-slate-900/50 py-4 px-3">
          {visibleTabs.map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`w-full flex items-center gap-2 px-3 py-2.5 rounded-lg text-sm transition mb-1 ${
                  isActive
                    ? 'bg-cyan-900/30 text-cyan-400 border border-cyan-500/30'
                    : 'text-slate-400 hover:bg-slate-800 hover:text-white'
                }`}
              >
                <Icon size={16} />
                {tab.label}
              </button>
            );
          })}

          {/* Admin 전용 탭이 없을 때 안내 */}
          {visibleTabs.length === 0 && (
            <p className="text-xs text-slate-500 px-3 py-2">
              사용 가능한 설정이 없습니다.
            </p>
          )}
        </div>

        {/* 오른쪽 콘텐츠 */}
        <div className="flex-1 px-8 overflow-hidden flex flex-col">
          {ActiveComponent ? (
            <ActiveComponent />
          ) : (
            <div className="flex-1 flex items-center justify-center text-slate-500">
              <p>설정 항목을 선택해주세요.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}