import { useEffect, useState } from 'react';
import { Save, Loader2, Shield, Cpu, ToggleLeft, ToggleRight, Key, Timer, X } from 'lucide-react';
import { useAuth } from '../context/AuthContext';

interface SolverSetting {
  solver_id: string;
  solver_name: string;
  provider: string;
  category: string;
  description: string;
  enabled: boolean;
  has_api_key: boolean;
  time_limit_sec: number | null;      // DB 설정값 (null이면 YAML 기본값 사용)
  time_limit_default: number | null;  // YAML max_time_seconds (표시용)
}

export default function SolverSettings() {
  const { authFetch } = useAuth();
  const [solvers, setSolvers] = useState<SolverSetting[]>([]);
  const [apiKeys, setApiKeys] = useState<Record<string, string>>({});
  const [timeLimits, setTimeLimits] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');

  useEffect(() => {
    fetchSettings();
  }, []);

  const fetchSettings = async () => {
    try {
      const res = await authFetch('/api/settings/solvers');
      if (res.ok) {
        const data: SolverSetting[] = await res.json();
        setSolvers(data);
        // DB에 저장된 time_limit_sec가 있으면 입력 필드에 초기값 설정
        const initialLimits: Record<string, string> = {};
        data.forEach((s) => {
          if (s.time_limit_sec !== null && s.time_limit_sec !== undefined) {
            initialLimits[s.solver_id] = String(s.time_limit_sec);
          }
        });
        setTimeLimits(initialLimits);
      }
    } catch (e) {
      console.error('Failed to load solver settings:', e);
    } finally {
      setLoading(false);
    }
  };

  const toggleSolver = (solverId: string) => {
    setSolvers((prev) =>
      prev.map((s) => (s.solver_id === solverId ? { ...s, enabled: !s.enabled } : s))
    );
  };

  const handleApiKeyChange = (solverId: string, value: string) => {
    setApiKeys((prev) => ({ ...prev, [solverId]: value }));
  };

  const handleTimeLimitChange = (solverId: string, value: string) => {
    // 숫자만 허용
    if (value === '' || /^\d+$/.test(value)) {
      setTimeLimits((prev) => ({ ...prev, [solverId]: value }));
    }
  };

  const clearTimeLimit = (solverId: string) => {
    setTimeLimits((prev) => {
      const next = { ...prev };
      delete next[solverId];
      return next;
    });
  };

  const handleSave = async () => {
    setSaving(true);
    setMessage('');
    try {
      const body = {
        solvers: solvers.map((s) => {
          const rawLimit = timeLimits[s.solver_id];
          const parsedLimit = rawLimit ? parseInt(rawLimit, 10) : null;
          return {
            solver_id: s.solver_id,
            enabled: s.enabled,
            api_key: apiKeys[s.solver_id] ?? null,
            time_limit_sec: parsedLimit && parsedLimit > 0 ? parsedLimit : null,
          };
        }),
      };
      const res = await authFetch('/api/settings/solvers', {
        method: 'PUT',
        body: JSON.stringify(body),
      });
      if (res.ok) {
        setMessage('저장되었습니다.');
        await fetchSettings();
        setTimeout(() => setMessage(''), 3000);
      } else {
        const err = await res.json();
        setMessage(`오류: ${err.detail || '저장 실패'}`);
      }
    } catch (e) {
      setMessage('저장 중 오류가 발생했습니다.');
    } finally {
      setSaving(false);
    }
  };

  const getCategoryLabel = (cat: string) => {
    const map: Record<string, { label: string; color: string }> = {
      quantum_hybrid: { label: '양자 하이브리드', color: 'bg-purple-500/20 text-purple-400' },
      quantum_native: { label: '양자 네이티브', color: 'bg-violet-500/20 text-violet-400' },
      quantum_gate: { label: '양자 게이트', color: 'bg-indigo-500/20 text-indigo-400' },
      quantum_analog: { label: '양자 아날로그', color: 'bg-fuchsia-500/20 text-fuchsia-400' },
      quantum_simulator: { label: '양자 시뮬레이터', color: 'bg-pink-500/20 text-pink-400' },
      quantum_hybrid_gpu: { label: '양자-GPU', color: 'bg-rose-500/20 text-rose-400' },
      classical: { label: '고전 (CPU)', color: 'bg-blue-500/20 text-blue-400' },
      classical_gpu: { label: '고전 (GPU)', color: 'bg-cyan-500/20 text-cyan-400' },
    };
    return map[cat] || { label: cat, color: 'bg-slate-500/20 text-slate-400' };
  };

  const formatDefaultTime = (seconds: number | null) => {
    if (!seconds) return null;
    if (seconds < 60) return `${seconds}초`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}분`;
    return `${Math.round(seconds / 3600)}시간`;
  };

  const grouped = solvers.reduce<Record<string, SolverSetting[]>>((acc, s) => {
    if (!acc[s.provider]) acc[s.provider] = [];
    acc[s.provider].push(s);
    return acc;
  }, {});

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Loader2 className="animate-spin text-cyan-400" size={32} />
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto custom-scrollbar py-6">
      {/* 상단 저장 바 */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <p className="text-sm text-slate-400">
            활성화된 솔버만 추천 결과에 표시됩니다. 모두 비활성 시 전체 솔버가 표시됩니다.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {message && (
            <span className={`text-sm ${message.includes('오류') ? 'text-red-400' : 'text-green-400'}`}>
              {message}
            </span>
          )}
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-4 py-2 bg-cyan-600 hover:bg-cyan-500 disabled:bg-slate-700 text-white rounded-lg font-medium text-sm flex items-center gap-2 transition"
          >
            {saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
            저장
          </button>
        </div>
      </div>

      {/* 벤더별 솔버 목록 */}
      <div className="space-y-6">
        {Object.entries(grouped).map(([provider, providerSolvers]) => (
          <div key={provider} className="bg-slate-900 rounded-xl border border-slate-800 overflow-hidden">
            <div className="px-5 py-3 bg-slate-800/50 border-b border-slate-700 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Shield size={16} className="text-slate-400" />
                <h3 className="text-sm font-bold text-white">{provider}</h3>
              </div>
              <span className="text-xs text-slate-500">
                {providerSolvers.filter((s) => s.enabled).length}/{providerSolvers.length} 활성
              </span>
            </div>
            <div className="divide-y divide-slate-800">
              {providerSolvers.map((solver) => {
                const cat = getCategoryLabel(solver.category);
                const defaultTimeLabel = formatDefaultTime(solver.time_limit_default);
                const hasCustomLimit = timeLimits[solver.solver_id] !== undefined && timeLimits[solver.solver_id] !== '';

                return (
                  <div key={solver.solver_id} className="px-5 py-4">
                    <div className="flex items-start justify-between">
                      <div className="flex-1">
                        <div className="flex items-center gap-2">
                          <Cpu size={14} className="text-slate-400" />
                          <span className="text-sm font-bold text-white">{solver.solver_name}</span>
                          <span className={`text-[10px] px-1.5 py-0.5 rounded ${cat.color}`}>
                            {cat.label}
                          </span>
                        </div>
                        <p className="text-xs text-slate-500 mt-1 ml-5">{solver.description}</p>

                        {/* API Key (양자 솔버만) */}
                        {!solver.category.startsWith('classical') && (
                          <div className="mt-2 ml-5 flex items-center gap-2">
                            <Key size={12} className="text-slate-500" />
                            <input
                              type="password"
                              placeholder={solver.has_api_key ? '••••••••(설정됨)' : 'API Key 입력'}
                              value={apiKeys[solver.solver_id] || ''}
                              onChange={(e) => handleApiKeyChange(solver.solver_id, e.target.value)}
                              className="px-2 py-1 text-xs bg-slate-800 border border-slate-700 rounded text-slate-300 placeholder-slate-600 w-64 focus:outline-none focus:border-cyan-500"
                            />
                          </div>
                        )}

                        {/* Time Limit (모든 솔버) */}
                        <div className="mt-2 ml-5 flex items-center gap-2">
                          <Timer size={12} className="text-slate-500" />
                          <div className="relative flex items-center gap-1">
                            <input
                              type="text"
                              inputMode="numeric"
                              placeholder={defaultTimeLabel ? `기본값: ${defaultTimeLabel}` : '초 단위 입력'}
                              value={timeLimits[solver.solver_id] || ''}
                              onChange={(e) => handleTimeLimitChange(solver.solver_id, e.target.value)}
                              className="px-2 py-1 text-xs bg-slate-800 border border-slate-700 rounded text-slate-300 placeholder-slate-600 w-36 focus:outline-none focus:border-cyan-500"
                            />
                            <span className="text-xs text-slate-600">초</span>
                            {hasCustomLimit && (
                              <button
                                onClick={() => clearTimeLimit(solver.solver_id)}
                                title="기본값으로 초기화"
                                className="ml-1 text-slate-500 hover:text-slate-300 transition"
                              >
                                <X size={12} />
                              </button>
                            )}
                          </div>
                          {hasCustomLimit && defaultTimeLabel && (
                            <span className="text-[10px] text-slate-600">
                              (YAML 기본: {defaultTimeLabel})
                            </span>
                          )}
                        </div>
                      </div>

                      {/* 활성화 토글 */}
                      <button onClick={() => toggleSolver(solver.solver_id)} className="flex-shrink-0 ml-4">
                        {solver.enabled ? (
                          <ToggleRight size={28} className="text-cyan-400" />
                        ) : (
                          <ToggleLeft size={28} className="text-slate-600" />
                        )}
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
