import React, { useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { Lock, User, Loader2 } from 'lucide-react';

const LoginScreen: React.FC = () => {
  const { login } = useAuth();
  const [loading, setLoading] = useState(false);

  const handleLogin = (role: string) => {
    setLoading(true);
    setTimeout(() => login(role), 500);
  };

  return (
    <div className="flex h-screen items-center justify-center bg-slate-950">
      <div className="w-full max-w-md p-8 bg-slate-900 border border-slate-800 rounded-2xl shadow-2xl">
        <div className="text-center mb-8">
          <Lock className="w-12 h-12 text-indigo-500 mx-auto mb-4" />
          <h1 className="text-2xl font-bold text-white">Quantum Solver</h1>
          <p className="text-slate-400">Enterprise Edition</p>
        </div>
        <div className="space-y-4">
          <button onClick={() => handleLogin('admin')} disabled={loading} className="w-full p-4 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl font-bold flex justify-center items-center gap-2 transition">
            {loading ? <Loader2 className="animate-spin" /> : <Lock size={18} />} Admin Login
          </button>
          <button onClick={() => handleLogin('user')} disabled={loading} className="w-full p-4 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-xl font-bold flex justify-center items-center gap-2 transition border border-slate-700">
            {loading ? <Loader2 className="animate-spin" /> : <User size={18} />} Researcher Login
          </button>
        </div>
      </div>
    </div>
  );
};
export default LoginScreen;