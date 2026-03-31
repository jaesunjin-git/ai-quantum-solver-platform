import React, { useState, useEffect } from 'react';
import Sidebar from './components/Sidebar';
import CrewDashboard from './components/CrewDashboard';
import Dashboard from './components/Dashboard';
import LoginScreen from './components/LoginScreen';
import SettingsPage from './components/SettingsPage';
import { JobNotificationToast } from './components/JobNotificationToast';
import { useJobNotifier } from './hooks/useJobNotifier';
import { useAuth } from './context/AuthContext';
import { useProjectContext } from './context/ProjectContext';

const App: React.FC = () => {
  const { isAuthenticated, authFetch } = useAuth();
  const { currentProject, setCurrentProject } = useProjectContext();
  const [collapsed, setCollapsed] = useState(false);
  const [showSettings, setShowSettings] = useState(false);

  // 멀티 프로젝트 job 완료 알림
  const jobNotifier = useJobNotifier(
    authFetch,
    currentProject?.id?.toString(),
    15000,
    isAuthenticated,
  );

  // 로그아웃 시 settings 닫기
  useEffect(() => {
    if (!isAuthenticated) setShowSettings(false);
  }, [isAuthenticated]);

  if (!isAuthenticated) return <LoginScreen />;

  return (
    <div className="flex h-screen w-screen bg-slate-950 text-white overflow-hidden">
      {/* 멀티 프로젝트 job 완료 알림 */}
      {isAuthenticated && (
        <JobNotificationToast
          notifications={jobNotifier.notifications}
          onDismiss={jobNotifier.dismissNotification}
          onNavigate={(projectId) => {
            // 해당 프로젝트로 이동
            setCurrentProject?.({ id: projectId } as any);
            jobNotifier.dismissAll();
          }}
        />
      )}

      <div className={`${collapsed ? 'w-16' : 'w-64'} transition-all duration-300 border-r border-slate-800 bg-slate-900`}>
        <Sidebar
          collapsed={collapsed}
          toggleCollapse={() => setCollapsed(!collapsed)}
          onSettingsClick={() => setShowSettings(true)}
        />
      </div>

      <div className="flex-1 flex flex-col h-full overflow-hidden">
        {showSettings ? (
          <SettingsPage onClose={() => setShowSettings(false)} />
        ) : currentProject ? (
          <CrewDashboard />
        ) : (
          <Dashboard />
        )}
      </div>
    </div>
  );
};
export default App;