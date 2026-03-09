# Skill Selection Prompt
# 이 파일은 _llm_select_and_execute()에서 LLM에게 스킬을 선택하게 할 때 사용됩니다.
# {state_block}, {action_history}, {message}, {current_tab}, {pipeline_phase} 변수로 치환됩니다.

{state_block}
{action_history}

[현재 파이프라인 단계]
{pipeline_phase}

[USER MESSAGE]
{message}

[CURRENT TAB] {current_tab}
(사용자가 현재 보고 있는 화면. 모호한 요청 해석 시 참고)

[사용 가능한 Skill 목록]
- AnalyzeDataSkill      : 데이터 분석 또는 재분석 실행
- ProblemDefinitionSkill: 최적화 문제 유형/목적함수/제약조건 정의 또는 수정
- DataNormalizationSkill: 데이터 정규화 실행
- MathModelSkill        : 수학 모델 생성/재생성/수정 (목적함수 변경, 제약조건 추가 등)
- PreDecisionSkill      : 솔버 추천 및 시뮬레이션
- StartOptimizationSkill: 최적화 실행 또는 재실행
- ShowResultSkill       : 이전 최적화 결과 재확인
- AnswerQuestionSkill   : 질문 답변 (데이터, 모델, 결과, 도메인 지식, 현재 단계 설명 등)
- GeneralReplySkill     : 일반 대화, 인사, 잡담, 기타

[스킬 선택 기준 — 중요]
1. 질문형 메시지는 반드시 AnswerQuestionSkill
   - "~인가요?", "~가능한가요?", "~설명해줘", "~알려줘", "왜~", "어떻게~", "~뭔가요?"
   - 주제가 목적함수/제약조건/모델이더라도, 설명/질문이면 AnswerQuestionSkill
   - 예: "목적함수가 뭔가요?" → AnswerQuestionSkill (MathModelSkill 아님)
   - 예: "현재 제약조건 설명해줘" → AnswerQuestionSkill (MathModelSkill 아님)

2. 명확한 실행 요청만 Action 스킬
   - "~해줘", "~시작", "~실행", "~생성해줘", "~바꿔줘"
   - 예: "수학 모델 생성해줘" → MathModelSkill
   - 예: "목적함수를 최소화로 바꿔줘" → MathModelSkill (parameters에 user_objective 포함)

3. 모호한 경우
   - 질문 느낌이 조금이라도 있으면 → AnswerQuestionSkill
   - 확실한 실행 의도가 있을 때만 → 해당 Action 스킬

[파라미터 추출 규칙]
- MathModelSkill: user_objective(목적함수 변경 내용), modify_constraints(제약조건 수정), regenerate(true/false)
- StartOptimizationSkill: solver_preference(선호 솔버), rerun(재실행 여부)
- AnswerQuestionSkill: query(질문 원문), about(질문 대상: model/result/data/domain/phase/general)
- AnalyzeDataSkill: reanalyze(재분석 여부), focus(특정 관점)

[응답 형식]
반드시 JSON만 출력하고 다른 텍스트는 절대 포함하지 마세요.
{"skill": "스킬명", "parameters": {"key": "value"}}

[예시]
- "목적함수에 대해 설명가능한가요?" → {"skill": "AnswerQuestionSkill", "parameters": {"query": "목적함수에 대해 설명가능한가요?", "about": "model"}}
- "현재 제약조건이 몇 개인가요?" → {"skill": "AnswerQuestionSkill", "parameters": {"query": "현재 제약조건이 몇 개인가요?", "about": "model"}}
- "수학 모델 생성해줘" → {"skill": "MathModelSkill", "parameters": {}}
- "목적함수를 최소 비용으로 바꿔줘" → {"skill": "MathModelSkill", "parameters": {"user_objective": "최소 비용", "regenerate": true}}
- "데이터 분석해줘" → {"skill": "AnalyzeDataSkill", "parameters": {}}
- "솔버 추천해줘" → {"skill": "PreDecisionSkill", "parameters": {}}
- "최적화 실행해줘" → {"skill": "StartOptimizationSkill", "parameters": {}}
- "안녕하세요" → {"skill": "GeneralReplySkill", "parameters": {}}
