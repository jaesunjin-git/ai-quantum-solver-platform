import { useState } from 'react';
import { X, Truck, TrendingUp, FlaskConical, Users, Cpu, Loader2 } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { API_BASE_URL } from '../config'; 

interface CreateProjectModalProps {
  onClose: () => void;
  onCreated: () => void;
}

export default function CreateProjectModal({ onClose, onCreated }: CreateProjectModalProps) {
  const [title, setTitle] = useState('');
  
  // 🌟 [중요] 기본값이 'crew'로 되어 있으므로, 사용자가 선택 안 하고 바로 만들어도 crew임.
  const [selectedType, setSelectedType] = useState('crew'); 
  const [isLoading, setIsLoading] = useState(false);
  
  const { user, authFetch } = useAuth();

  const handleCreate = async () => {
    if (!title.trim()) {
        alert("프로젝트 이름을 입력해주세요.");
        return;
    }
    const ownerName = user?.name || "user";

    setIsLoading(true);

    try {
      const response = await authFetch(`${API_BASE_URL}/api/projects`, {
        method: 'POST',
        body: JSON.stringify({
            title,
            type: selectedType,
            owner: ownerName
        }),
      });

      if (response.ok) {
        onCreated(); 
        onClose();
      } else {
        const errData = await response.json();
        console.error("Backend Error:", errData);
        alert(`생성 실패: ${errData.detail || "서버 오류"}`);
      }
    } catch (err) {
      console.error("Network Error:", err);
      alert("서버 연결 실패");
    } finally {
      setIsLoading(false);
    }
  };

  const types = [
    { id: 'crew', label: 'Crew Scheduling', icon: Users },
    { id: 'logistics', label: 'Logistics Optimization', icon: Truck },
    { id: 'finance', label: 'Portfolio Rebalancing', icon: TrendingUp },
    { id: 'material', label: 'Material Discovery', icon: FlaskConical },
    { id: 'general', label: 'General Optimization', icon: Cpu },
  ];

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 backdrop-blur-sm animate-fade-in">
      <div className="bg-slate-900 border border-slate-700 w-full max-w-md rounded-2xl p-6 shadow-2xl">
        <div className="flex justify-between items-center mb-6">
          <h2 className="text-xl font-bold text-white">Start New Project</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-white transition"><X size={24} /></button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="block text-sm text-slate-400 mb-2">Project Title</label>
            <input 
              type="text" 
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Ex) 2026 Crew Schedule Plan"
              autoFocus
              className="w-full bg-slate-800 border border-slate-600 rounded-lg p-3 text-white focus:ring-2 focus:ring-cyan-500 outline-none transition"
            />
          </div>

          <div>
            <label className="block text-sm text-slate-400 mb-2">Optimization Type</label>
            <div className="grid grid-cols-1 gap-2 max-h-[320px] overflow-y-auto pr-1 custom-scrollbar">
              {types.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setSelectedType(t.id)}
                  className={`flex items-center space-x-3 p-3 rounded-lg border transition-all text-left ${
                    selectedType === t.id 
                      ? 'bg-cyan-900/30 border-cyan-500 text-white ring-1 ring-cyan-500' 
                      : 'bg-slate-800 border-slate-700 text-slate-400 hover:bg-slate-750 hover:border-slate-500'
                  }`}
                >
                  <t.icon size={20} className={selectedType === t.id ? 'text-cyan-400' : 'text-slate-500'} />
                  <span className="font-medium">{t.label}</span>
                </button>
              ))}
            </div>
          </div>

          <button 
            type="button"
            onClick={handleCreate}
            disabled={isLoading}
            className={`w-full font-bold py-3 rounded-xl mt-4 transition shadow-lg shadow-blue-900/20 flex items-center justify-center gap-2 ${
                isLoading 
                ? 'bg-slate-700 text-slate-400 cursor-not-allowed' 
                : 'bg-blue-600 hover:bg-blue-500 text-white'
            }`}
          >
            {isLoading && <Loader2 size={18} className="animate-spin" />}
            {isLoading ? "Creating..." : "Create Project"}
          </button>
        </div>
      </div>
    </div>
  );
}