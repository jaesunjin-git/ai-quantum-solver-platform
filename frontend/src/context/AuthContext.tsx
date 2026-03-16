import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from 'react';
import { API_BASE_URL } from '../config';

interface User { id: number; username: string; name: string; role: string; }

interface AuthContextType {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  login: (username: string, password: string) => Promise<string | null>;
  register: (username: string, password: string, displayName?: string, role?: string) => Promise<string | null>;
  logout: () => void;
  authFetch: (url: string, init?: RequestInit) => Promise<Response>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

// ── JWT 만료 체크 (base64 디코딩, 라이브러리 불필요) ──
function isTokenExpired(token: string): boolean {
  try {
    const parts = token.split('.');
    if (parts.length !== 3) return true;
    const payload = JSON.parse(atob(parts[1].replace(/-/g, '+').replace(/_/g, '/')));
    if (!payload.exp) return false;
    // 30초 여유 (네트워크 지연 대비)
    return Date.now() >= (payload.exp * 1000) - 30_000;
  } catch {
    return true;
  }
}

export const AuthProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);

  const logout = useCallback(() => {
    setUser(null);
    setToken(null);
    localStorage.removeItem('token');
    localStorage.removeItem('user');
  }, []);

  // 페이지 새로고침 시 localStorage에서 복원 + 만료 체크
  useEffect(() => {
    const savedToken = localStorage.getItem('token');
    const savedUser = localStorage.getItem('user');
    if (savedToken && savedUser) {
      // ★ 만료된 토큰이면 복원하지 않고 정리
      if (isTokenExpired(savedToken)) {
        localStorage.removeItem('token');
        localStorage.removeItem('user');
        return;
      }
      try {
        setToken(savedToken);
        setUser(JSON.parse(savedUser));
      } catch {
        localStorage.removeItem('token');
        localStorage.removeItem('user');
      }
    }
  }, []);

  // ★ 공통 fetch 래퍼: Authorization 헤더 자동 추가 + 401 자동 로그아웃
  const authFetch = useCallback(async (url: string, init?: RequestInit): Promise<Response> => {
    // 요청 전 토큰 만료 체크
    const currentToken = localStorage.getItem('token');
    if (currentToken && isTokenExpired(currentToken)) {
      logout();
      return new Response(JSON.stringify({ detail: '세션이 만료되었습니다. 다시 로그인해주세요.' }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    const headers = new Headers(init?.headers);
    if (currentToken && !headers.has('Authorization')) {
      headers.set('Authorization', `Bearer ${currentToken}`);
    }
    if (!headers.has('Content-Type') && init?.body && typeof init.body === 'string') {
      headers.set('Content-Type', 'application/json');
    }

    const response = await fetch(url, { ...init, headers });

    // ★ 401 응답 시 자동 로그아웃
    if (response.status === 401) {
      logout();
    }

    return response;
  }, [logout]);

  const login = async (username: string, password: string): Promise<string | null> => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: '로그인 실패' }));
        return err.detail || '로그인 실패';
      }
      const data = await res.json();
      const userObj: User = {
        id: data.user.id,
        username: data.user.username,
        name: data.user.display_name || data.user.username,
        role: data.user.role,
      };
      setToken(data.access_token);
      setUser(userObj);
      localStorage.setItem('token', data.access_token);
      localStorage.setItem('user', JSON.stringify(userObj));
      return null; // 성공
    } catch {
      return '서버에 연결할 수 없습니다.';
    }
  };

  const register = async (
    username: string, password: string, displayName?: string, role?: string
  ): Promise<string | null> => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username,
          password,
          display_name: displayName || username,
          role: role || 'user',
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: '회원가입 실패' }));
        return err.detail || '회원가입 실패';
      }
      const data = await res.json();
      const userObj: User = {
        id: data.user.id,
        username: data.user.username,
        name: data.user.display_name || data.user.username,
        role: data.user.role,
      };
      setToken(data.access_token);
      setUser(userObj);
      localStorage.setItem('token', data.access_token);
      localStorage.setItem('user', JSON.stringify(userObj));
      return null;
    } catch {
      return '서버에 연결할 수 없습니다.';
    }
  };

  return (
    <AuthContext.Provider value={{
      user, token, isAuthenticated: !!user,
      login, register, logout, authFetch,
    }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth error');
  return context;
};
