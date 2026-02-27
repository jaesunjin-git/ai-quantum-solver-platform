import { createContext, useContext, useState, useEffect, type ReactNode, useCallback } from 'react';
import { useAuth } from './AuthContext';
// 🌟 [중요] 아까 만든 config.ts에서 주소를 가져옵니다.
import { API_BASE_URL } from '../config';

export interface Project {
  id: string;
  title: string;
  type: string;
  owner: string;
  created_at?: string;
  status?: string;
}

interface ProjectContextType {
  projects: Project[];
  currentProject: Project | null;
  setCurrentProject: (p: Project | null) => void;
  refreshProjects: () => void;
  isLoading: boolean;
}

const ProjectContext = createContext<ProjectContextType | undefined>(undefined);

export const ProjectProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [projects, setProjects] = useState<Project[]>([]);
  const [currentProject, setCurrentProject] = useState<Project | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const { user } = useAuth();

  const fetchProjects = useCallback(async () => {
    if (!user) return; // 로그인 안 했으면 중단

    setIsLoading(true);
    try {
      // 🌟 [핵심] URL 조립: config의 주소 + user 파라미터 (인코딩 필수)
      // 예: http://127.0.0.1:8000/api/projects?user=Super%20Admin
      const url = `${API_BASE_URL}/api/projects?user=${encodeURIComponent(user.name)}`;
      
      console.log(`📡 [API Request] Getting projects from: ${url}`);

      const response = await fetch(url);
      
      if (response.ok) {
        const data = await response.json();
        console.log("✅ [API Success] Projects loaded:", data);
        setProjects(data);
      } else {
        const errorText = await response.text();
        console.error(`❌ [API Error] Status: ${response.status}`, errorText);
      }
    } catch (error) {
      console.error("❌ [Network Error]", error);
    } finally {
      setIsLoading(false);
    }
  }, [user]);

  // user 정보가 생기면(로그인하면) 자동으로 목록 가져오기
  useEffect(() => {
    if (user) {
      fetchProjects();
    } else {
      setProjects([]);
    }
  }, [user, fetchProjects]);

  return (
    <ProjectContext.Provider value={{ 
        projects, 
        currentProject, 
        setCurrentProject, 
        refreshProjects: fetchProjects,
        isLoading 
    }}>
      {children}
    </ProjectContext.Provider>
  );
};

export const useProjectContext = () => {
  const context = useContext(ProjectContext);
  if (!context) throw new Error('useProjectContext Error');
  return context;
};