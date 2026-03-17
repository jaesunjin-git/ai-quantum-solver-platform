import { API_BASE_URL } from '../../config';

// 다운로드 유틸 — authFetch를 주입받아 401 자동 처리
export async function downloadReport(
  projectId: string,
  format: 'md' | 'docx' | 'json',
  type: 'analysis' | 'math_model' | 'solve_result' = 'analysis',
  authFetch?: (url: string, init?: RequestInit) => Promise<Response>,
): Promise<void> {
  const fetcher = authFetch || fetch;
  try {
    const res = await fetcher(
      `${API_BASE_URL}/api/projects/${projectId}/report/download?format=${format}&type=${type}`,
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
