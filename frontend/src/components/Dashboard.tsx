import { useState } from 'react';
import {
  CreditCard, Clock, Activity, Plus, Server, ArrowRight,
  Truck, TrendingUp, FlaskConical, Folder, Users, Lock, Trash2, X, Pencil,
} from 'lucide-react';
import CreateProjectModal from './CreateProjectModal';
import { useProjectContext } from '../context/ProjectContext';
import { useAuth } from '../context/AuthContext';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '';

export default function Dashboard() {
  const [showModal, setShowModal] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; title: string } | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [editTarget, setEditTarget] = useState<{ id: string; title: string } | null>(null);
  const [editTitle, setEditTitle] = useState('');

  const { projects, refreshProjects, setCurrentProject } = useProjectContext();
  const { user } = useAuth();

  // RBAC 필터링
  const filteredProjects = projects.filter(project => {
    if (!user) return false;
    if (user.role === 'admin') return true;
    return project.owner === user.name;
  });

  // 아이콘 헬퍼
  const getProjectIcon = (type: string) => {
    switch (type) {
      case 'crew': return Users;
      case 'logistics': return Truck;
      case 'finance': return TrendingUp;
      case 'material': return FlaskConical;
      default: return Folder;
    }
  };

  // 날짜 포맷팅
  const formatDate = (dateString?: string) => {
    if (!dateString) return 'Just now';
    return new Date(dateString).toLocaleDateString();
  };

  // 프로젝트명 수정
  const handleRename = async (projectId: string) => {
  const trimmed = editTitle.trim();
  if (!trimmed || !user) {
    setEditTarget(null);
    return;
  }
  try {
    const API = import.meta.env.VITE_API_BASE_URL || '';
    const res = await fetch(
      `${API}/api/projects/${projectId}?user=${encodeURIComponent(user.name)}&role=${user.role}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: trimmed }),
      }
    );
    if (!res.ok) throw new Error('rename failed');
    await refreshProjects();
  } catch (e) {
    console.error(e);
    alert('프로젝트명 변경에 실패했습니다.');
  }
  setEditTarget(null);
};


  // 삭제 실행
  const handleDelete = async () => {
    if (!deleteTarget || !user) return;
    setDeleting(true);
    try {
      const params = new URLSearchParams({
        user: user.name,
        role: user.role,
      });
      const res = await fetch(
        `${API_BASE_URL}/api/projects/${deleteTarget.id}?${params.toString()}`,
        { method: 'DELETE' },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        alert(`삭제 실패: ${err.detail || res.statusText}`);
        return;
      }
      await refreshProjects();
    } catch (err) {
      console.error('Delete error:', err);
      alert('삭제 중 오류가 발생했습니다.');
    } finally {
      setDeleting(false);
      setDeleteTarget(null);
    }
  };

  const canEdit = (owner: string) => {
    if (!user) return false;
    if (user.role === 'admin') return true;
    return user.name === owner;
  };

  // 삭제 가능 여부 확인
  const canDelete = (projectOwner: string) => {
    if (!user) return false;
    if (user.role === 'admin') return true;
    return projectOwner === user.name;
  };

  return (
    <div className="p-6 space-y-6 overflow-y-auto h-full bg-slate-950 animate-fade-in custom-scrollbar">

      {/* 1. 상단 웰컴 메시지 */}
      <div className="flex justify-between items-end pb-2">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            Hello, {user?.name}
            {user?.role === 'admin' && (
              <span className="text-xs bg-indigo-600 px-2 py-0.5 rounded text-white font-normal">Admin</span>
            )}
          </h1>
          <p className="text-slate-400 text-sm">
            {user?.role === 'admin' ? 'System Overview' : 'My Research Projects'}
          </p>
        </div>
      </div>

      {/* 2. KPI Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div className="bg-slate-900 p-5 rounded-xl border border-slate-800 shadow-lg">
          <div className="flex justify-between items-start mb-2">
            <div className="p-2 bg-green-500/10 rounded-lg"><CreditCard className="text-green-400" size={20} /></div>
            <span className="text-xs font-bold text-green-400 bg-green-400/10 px-2 py-1 rounded">+12%</span>
          </div>
          <div className="text-2xl font-bold text-white mb-1">$1.24M</div>
          <div className="text-xs text-slate-400">Total Saved Cost</div>
        </div>

        <div className="bg-slate-900 p-5 rounded-xl border border-slate-800 shadow-lg">
          <div className="flex justify-between items-start mb-2">
            <div className="p-2 bg-blue-500/10 rounded-lg"><Clock className="text-blue-400" size={20} /></div>
            <span className="text-xs font-bold text-blue-400 bg-blue-400/10 px-2 py-1 rounded">Fast</span>
          </div>
          <div className="text-2xl font-bold text-white mb-1">12m 45s</div>
          <div className="text-xs text-slate-400">Avg. Solve Time</div>
        </div>

        <div className="bg-slate-900 p-5 rounded-xl border border-slate-800 shadow-lg">
          <div className="flex justify-between items-start mb-2">
            <div className="p-2 bg-purple-500/10 rounded-lg"><Activity className="text-purple-400" size={20} /></div>
            <span className="text-xs font-bold text-slate-300">85% Left</span>
          </div>
          <div className="text-2xl font-bold text-white mb-1">8,450</div>
          <div className="text-xs text-slate-400">QPU Credits</div>
        </div>

        <div className="bg-slate-900 p-5 rounded-xl border border-slate-800 shadow-lg">
          <div className="flex justify-between items-start mb-2">
            <div className="p-2 bg-cyan-500/10 rounded-lg"><Server className="text-cyan-400" size={20} /></div>
            <span className="text-xs font-bold text-cyan-400 bg-cyan-400/10 px-2 py-1 rounded">Active</span>
          </div>
          <div className="text-2xl font-bold text-white mb-1">{filteredProjects.length} Running</div>
          <div className="text-xs text-slate-400">Visible Projects</div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">

        {/* 3. 프로젝트 리스트 영역 */}
        <div className="lg:col-span-2 space-y-6">

          {/* 새 프로젝트 배너 */}
          <div className="bg-gradient-to-r from-indigo-900/40 to-cyan-900/40 border border-indigo-500/30 p-6 rounded-2xl flex items-center justify-between shadow-lg">
            <div>
              <h2 className="text-xl font-bold text-white mb-2">Start New Optimization</h2>
              <p className="text-slate-300 text-sm">AI가 최적의 양자 솔루션을 제안합니다.</p>
            </div>
            <button
              onClick={() => setShowModal(true)}
              className="flex items-center space-x-2 bg-indigo-600 hover:bg-indigo-500 text-white px-5 py-3 rounded-xl font-bold transition shadow-lg shadow-indigo-900/20"
            >
              <Plus size={18} />
              <span>New Project</span>
            </button>
          </div>

          {/* 리스트 테이블 */}
          <div className="bg-slate-900 rounded-xl border border-slate-800 overflow-hidden shadow-md">
            <div className="p-4 border-b border-slate-800 flex justify-between items-center">
              <h3 className="font-bold text-white">
                {user?.role === 'admin' ? 'All Projects (Admin View)' : 'My Projects'}
              </h3>
              <span className="text-xs text-slate-500">Total {filteredProjects.length}</span>
            </div>

            <div className="divide-y divide-slate-800">
              {filteredProjects.length === 0 ? (
                <div className="p-10 text-center text-slate-500">
                  <Folder size={48} className="mx-auto mb-3 opacity-20" />
                  <p>표시할 프로젝트가 없습니다.</p>
                  <p className="text-xs mt-1 text-slate-600">
                    (DB: {projects.length}개 / Filtered: 0개)
                  </p>
                </div>
              ) : (
                filteredProjects.map((project) => {
                  const Icon = getProjectIcon(project.type);
                  const isMine = project.owner === user?.name;

                  return (
                    <div
                      key={project.id}
                      className="p-4 flex items-center justify-between hover:bg-slate-800/50 transition group"
                    >
                      {/* 왼쪽: 프로젝트 정보 (클릭 시 프로젝트 진입) */}
                      <div
                        className="flex items-center space-x-4 flex-1 cursor-pointer"
                        onClick={() => setCurrentProject(project)}
                      >
                        <div className="w-12 h-12 bg-slate-800 rounded-xl flex items-center justify-center border border-slate-700 group-hover:border-indigo-500/50 group-hover:text-indigo-400 transition-colors">
                          <Icon size={20} className="text-slate-400 group-hover:text-indigo-400" />
                        </div>
                        <div>
                          <div className="text-white font-bold text-sm group-hover:text-indigo-300 transition-colors flex items-center gap-2">
                            {editTarget?.id === project.id ? (
                              <input
                                autoFocus
                                className="bg-slate-800 border border-indigo-500 rounded px-2 py-1 text-sm text-white outline-none w-96"
                                value={editTitle}
                                onChange={(e) => setEditTitle(e.target.value)}
                                onKeyDown={(e) => {
                                  if (e.key === 'Enter') handleRename(project.id);
                                  if (e.key === 'Escape') setEditTarget(null);
                                }}
                                onBlur={() => handleRename(project.id)}
                                onClick={(e) => e.stopPropagation()}
                              />
                            ) : (
                              <span>{project.title}</span>
                            )}
                            {user?.role === 'admin' && !isMine && <Lock size={12} className="text-slate-600" />}
                          </div>
                          <div className="text-xs text-slate-500 mt-0.5 flex items-center gap-2">
                            <span className={isMine ? 'text-cyan-400' : 'text-slate-500'}>{project.owner}</span>
                            <span className="w-1 h-1 rounded-full bg-slate-600"></span>
                            <span>{formatDate(project.created_at)}</span>
                          </div>
                        </div>
                      </div>

                      {/* 오른쪽: 타입 뱃지 + 삭제 버튼 + 화살표 */}
                      <div className="flex items-center gap-2 text-xs text-slate-500">
                        <span className="px-2 py-1 rounded bg-slate-800 border border-slate-700 text-slate-300 capitalize">
                          {project.type}
                        </span>

                        {canEdit(project.owner) && (
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setEditTarget({ id: project.id, title: project.title });
                              setEditTitle(project.title);
                            }}
                            className="p-1.5 rounded-lg text-slate-600 hover:text-indigo-400 hover:bg-indigo-400/10 transition opacity-0 group-hover:opacity-100"
                            title="이름 변경"
                          >
                            <Pencil size={16} />
                          </button>
                        )}

                        {/* 삭제 버튼 */}
                        {canDelete(project.owner) && (
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setDeleteTarget({ id: project.id, title: project.title });
                            }}
                            className="p-1.5 rounded-lg text-slate-600 hover:text-red-400 hover:bg-red-400/10 transition opacity-0 group-hover:opacity-100"
                            title="프로젝트 삭제"
                          >
                            <Trash2 size={16} />
                          </button>
                        )}

                        <ArrowRight
                          size={16}
                          className="text-slate-600 group-hover:text-white transition-transform group-hover:translate-x-1 cursor-pointer"
                          onClick={() => setCurrentProject(project)}
                        />
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </div>

        {/* 4. 우측 시스템 상태 패널 */}
        <div className="bg-slate-900 rounded-xl border border-slate-800 h-fit shadow-md">
          <div className="p-4 border-b border-slate-800">
            <h3 className="font-bold text-white">Quantum Systems</h3>
          </div>
          <div className="p-4 space-y-4">
            <StatusItem name="D-Wave Advantage" status="Available" color="green" />
            <StatusItem name="IonQ Aria" status="High Load" color="yellow" />
            <StatusItem name="IBM Eagle" status="Maintenance" color="red" />

            <div className="mt-4 pt-4 border-t border-slate-800">
              <div className="flex justify-between text-xs text-slate-400 mb-2">
                <span>Global QPU Load</span>
                <span>45%</span>
              </div>
              <div className="w-full bg-slate-800 h-2 rounded-full overflow-hidden">
                <div className="bg-cyan-500 h-full w-[45%] rounded-full"></div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* 프로젝트 생성 모달 */}
      {showModal && (
        <CreateProjectModal
          onClose={() => setShowModal(false)}
          onCreated={() => refreshProjects()}
        />
      )}

      {/* 삭제 확인 모달 */}
      {deleteTarget && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50">
          <div className="bg-slate-900 border border-slate-700 rounded-2xl p-6 w-full max-w-md shadow-2xl">
            {/* 헤더 */}
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-bold text-white">프로젝트 삭제</h3>
              <button
                onClick={() => setDeleteTarget(null)}
                className="p-1 hover:bg-slate-800 rounded-lg text-slate-400 transition"
              >
                <X size={20} />
              </button>
            </div>

            {/* 경고 내용 */}
            <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 mb-6">
              <p className="text-sm text-slate-300 mb-2">
                다음 프로젝트를 삭제하시겠습니까?
              </p>
              <p className="text-white font-bold">{deleteTarget.title}</p>
              <p className="text-xs text-red-400 mt-2">
                관련된 채팅 기록과 작업 데이터가 모두 삭제되며, 복구할 수 없습니다.
              </p>
            </div>

            {/* 버튼 */}
            <div className="flex gap-3">
              <button
                onClick={() => setDeleteTarget(null)}
                className="flex-1 py-2.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 font-bold transition border border-slate-700"
              >
                취소
              </button>
              <button
                onClick={handleDelete}
                disabled={deleting}
                className="flex-1 py-2.5 rounded-xl bg-red-600 hover:bg-red-500 text-white font-bold transition disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
              >
                {deleting ? (
                  <>
                    <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    <span>삭제 중...</span>
                  </>
                ) : (
                  <>
                    <Trash2 size={16} />
                    <span>삭제</span>
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// 헬퍼 컴포넌트
function StatusItem({ name, status, color }: { name: string; status: string; color: 'green' | 'yellow' | 'red' }) {
  const colorClass = {
    green: 'text-green-400 bg-green-400/10',
    yellow: 'text-yellow-400 bg-yellow-400/10',
    red: 'text-red-400 bg-red-400/10',
  };
  const dotClass = { green: 'bg-green-500', yellow: 'bg-yellow-500', red: 'bg-red-500' };

  return (
    <div className="flex items-center justify-between">
      <div className="flex items-center space-x-2">
        <div className={`w-2 h-2 rounded-full ${dotClass[color]} animate-pulse`}></div>
        <span className="text-sm text-slate-300">{name}</span>
      </div>
      <span className={`text-xs font-bold px-2 py-1 rounded ${colorClass[color]}`}>{status}</span>
    </div>
  );
}