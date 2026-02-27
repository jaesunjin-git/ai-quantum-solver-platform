import random

def handle_logistics(user_message: str):
    # 2단계: 구체적 정보(트럭 수 등)가 입력되었을 때 -> 분석 수행
    if "트럭" in user_message or "배송지" in user_message:
        # 숫자 추출 (간단한 파싱)
        truck_count = "30"
        for word in user_message.split():
            if word.isdigit(): truck_count = word
            elif "대" in word: truck_count = word.replace("대", "")

        return {
            "type": "analysis",
            "text": f"입력하신 조건(트럭 {truck_count}대)으로 물류 경로 최적화 모델을 생성했습니다.\n[Confirm] 버튼을 눌러 시뮬레이션을 실행하세요.",
            "data": {
                "goal": "Minimize Fuel Cost (AI Generated)",
                "constraints": [
                    {"label": "Resources", "value": f"{truck_count} Trucks"},
                    {"label": "Targets", "value": "500 Nodes"},
                    {"label": "Time Window", "value": "07:00 ~ 19:00"},
                ]
            },
            "options": None
        }

    # 1단계: 단순 진입했을 때 -> 정보 요청
    else:
        return {
            "type": "selection",
            "text": "물류 최적화 모드입니다.\n정확한 분석을 위해 '배송지 데이터'와 '가용 트럭 수'가 필요합니다.\n\n샘플 시나리오를 선택하거나 직접 입력해주세요.",
            "options": [
                {"label": "📄 서울 강남구 / 트럭 25대", "value": "서울 강남구 배송지, 트럭 25대 최적화해줘"},
                {"label": "📄 경기 물류센터 / 트럭 50대", "value": "경기 센터 배송지, 트럭 50대 최적화해줘"}
            ],
            "data": None
        }