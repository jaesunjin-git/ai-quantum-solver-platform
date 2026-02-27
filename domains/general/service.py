# services/general_consulting.py
from google import genai
from core.config import settings
from utils.prompt_loader import build_prompt_from_yaml

try:
     client = genai.Client(api_key=settings.GOOGLE_API_KEY)
except:
    pass

def handle_general_consulting(user_message: str):
    """
    1. '상담하고 싶어' 같은 짧은 인사말 -> 안내 메시지 출력
    2. '생산공정 최적화 해줘' 같은 구체적 질문 -> LLM 답변 생성
    """
    
    # [Case A] 단순 인사/시작 (메시지가 짧거나 '상담'이라는 단어만 있을 때)
    if len(user_message) < 20 and ("상담" in user_message or "시작" in user_message):
        return {
            "type": "text",
            "text": (
                "👨‍💼 **AI 최적화 컨설턴트입니다.**\n\n"
                "해결하고자 하는 문제에 대해 자유롭게 말씀해 주세요.\n"
                "KQC Quantum Solver가 적합한 알고리즘과 접근 방식을 제안해 드립니다.\n\n"
                "**💡 질문 예시:**\n"
                "- \"공장 생산 라인의 병목 현상을 줄이고 싶어.\"\n"
                "- \"배달 트럭 50대의 최적 경로를 짜고 싶어.\"\n"
                "- \"투자 포트폴리오의 위험을 최소화하고 싶어.\"\n"
            ),
            "options": []
        }

    # [Case B] 구체적인 고민 상담 (LLM 호출)
    try:
        print(f"📡 [Consultant] 상담 답변 생성 중: {user_message}")
        
        prompt = build_prompt_from_yaml("crew", "consultant", {"user_message": user_message})
        
        response = client.models.generate_content(
            model=settings.MODEL_CHAT, # 답변 생성용 모델
            contents=prompt
        )
        
        return {
            "type": "text",
            "text": f"👨‍💼 **전문가 의견:**\n\n{response.text}",
            "options": [
                {"label": "다른 문제도 물어보기", "value": "다른 최적화 문제도 상담하고 싶어"},
                {"label": "메인 메뉴로 돌아가기", "value": "처음으로 돌아가기"}
            ]
        }
        
    except Exception as e:
        return {
            "type": "text",
            "text": "죄송합니다. 상담 내용을 분석하는 중 오류가 발생했습니다.",
            "options": []
        }
