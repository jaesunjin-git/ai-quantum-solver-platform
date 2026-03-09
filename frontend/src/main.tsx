import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.tsx'
import './index.css'
import { AuthProvider } from './context/AuthContext.tsx'
import { ProjectProvider } from './context/ProjectContext.tsx'
import { AnalysisProvider } from './context/AnalysisContext.tsx'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AuthProvider>
      <ProjectProvider>
        <AnalysisProvider>
          <App />
        </AnalysisProvider>
      </ProjectProvider>
    </AuthProvider>
  </React.StrictMode>,
)