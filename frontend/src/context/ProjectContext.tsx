import { createContext, useContext, useState, useEffect, type ReactNode, useCallback } from 'react';
import { useAuth } from './AuthContext';
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

  const { user, token, authFetch } = useAuth();

  const fetchProjects = useCallback(async () => {
    if (!user || !token) return;

    setIsLoading(true);
    try {
      const url = `${API_BASE_URL}/api/projects`;
      const response = await authFetch(url);

      if (response.ok) {
        const data = await response.json();
        setProjects(data);
      } else if (response.status !== 401) {
        console.error(`[API Error] Status: ${response.status}`);
      }
      // 401은 authFetch가 자동 로그아웃 처리
    } catch (error) {
      console.error('[Network Error]', error);
    } finally {
      setIsLoading(false);
    }
  }, [user, token, authFetch]);

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
