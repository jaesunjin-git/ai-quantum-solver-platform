// src/config.ts

// .env에서 값을 읽어오고, 없으면 기본값(localhost)을 씁니다.
// 이렇게 해두면 나중에 주소가 바뀌어도 여기만 고치면 됩니다.
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000';
