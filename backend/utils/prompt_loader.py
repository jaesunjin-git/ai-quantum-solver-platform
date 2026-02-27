import os
from functools import lru_cache

# 프로젝트 루트 경로 (backend/)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPT_DIR = os.path.join(BASE_DIR, "prompts")

@lru_cache() # 성능 최적화: 한 번 읽은 파일은 메모리에 캐싱
def load_prompt(domain: str, filename: str) -> str:
    """
    지정된 도메인의 마크다운 프롬프트 파일을 읽어서 문자열로 반환합니다.
    예: load_prompt("crew", "system") -> backend/prompts/crew/system.md 읽음
    """
    file_path = os.path.join(PROMPT_DIR, domain, f"{filename}.md")
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        print(f"⚠️ Prompt file not found: {file_path}")
        return "You are a helpful AI assistant." # 파일 없을 때 기본값