import { createContext, useContext, useState, type ReactNode } from 'react';

interface User { id: string; name: string; role: string; }

interface AuthContextType {
  user: User | null;
  isAuthenticated: boolean;
  login: (role: string) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);

  const login = (role: string) => {
    // 🌟 [핵심] DB의 owner 컬럼 값("Super Admin")과 정확히 일치시켜야 함
    const userName = role === 'admin' ? 'Super Admin' : 'Researcher';
    
    setUser({ 
      id: role, 
      name: userName, 
      role 
    });
  };

  const logout = () => setUser(null);

  return (
    <AuthContext.Provider value={{ user, isAuthenticated: !!user, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth error');
  return context;
};