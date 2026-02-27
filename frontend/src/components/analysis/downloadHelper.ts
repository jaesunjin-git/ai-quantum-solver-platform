import { API_BASE_URL } from '../../config';
// src/components/analysis/downloadHelper.ts
export // 다운로드 유틸
// ============================================================

async function downloadReport(projectId: string, format: 'md' | 'docx' | 'json', type: 'analysis' | 'math_model' | 'solve_result' = 'analysis'): Promise<void> {
  try {
    const token = localStorage.getItem('token') || '';
    const res = await fetch(
      `${API_BASE_URL}/api/projects/${projectId}/report/download?format=${format}&type=${type}`,
      {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      },
    );

    if (!res.ok) {
      const errText = await res.text();
      alert(`다운로드 실패: ${errText || res.statusText}`);
      return;
    }

    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${type}_${projectId}.${format}`;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(url);
  } catch (err) {
    console.error('Download error:', err);
        alert('다운로드 중 오류가 발생했습니다.');
  }
}

