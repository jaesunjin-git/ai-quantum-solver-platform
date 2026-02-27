from typing import List
import os
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

class Settings:
    # 1. API 키 (환경변수에서 가져옴)
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
    DWAVE_API_TOKEN = os.getenv("DWAVE_API_TOKEN")

    # 2. 모델명 관리 (여기만 바꾸면 싹 다 바뀜!)
    # 데모용으로 가장 안정적인 모델들을 여기에 정의
    MODEL_ROUTER = "gemini-2.5-flash-lite"      # 라우터용
    MODEL_BACKUP = "gemini-2.5-flash-lite"       # 백업용
    MODEL_CHAT = "gemini-2.5-flash-lite"        # 일반 대화용
    MODEL_ANALYSIS = "gemini-2.5-flash-lite"    # 데이터 분석용
    MODEL_MODELING = "gemini-2.5-flash-lite"   # 수학 모델 생성용
    
    # 만약 2.5 Lite를 쓰고 싶다면 여기만 수정하면 됨
    # MODEL_CHAT = "gemini-2.5-flash-lite"

    # 🌟 [추가] CORS 설정 로드 (기본값 설정)
    # .env에서 읽어오되, 없으면 기본 로컬 주소 사용
    CORS_ORIGINS_STR: str = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")

    @property
    def cors_origins(self) -> List[str]:
        """콤마(,)로 구분된 문자열을 리스트로 변환"""
        return [url.strip() for url in self.CORS_ORIGINS_STR.split(",") if url.strip()]

    class Config:
        env_file = ".env"

settings = Settings()