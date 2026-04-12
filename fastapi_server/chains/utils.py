"""
체인 공통 유틸리티

히스토리 슬라이딩 윈도우
  - 최근 HISTORY_WINDOW개 메시지만 유지해 입력 토큰 무한 증가를 방지합니다.
  - 기본값 6 = user 3턴 + assistant 3턴 (약 3번의 대화 맥락 유지)

난이도 순서 상수
  - DIFFICULTY_ORDER: fallback 방향 및 키워드 우선순위 판단에 공유 사용
"""

from langchain_core.messages import AIMessage, HumanMessage

# 최근 유지할 메시지 수 (user + assistant 합산)
HISTORY_WINDOW: int = 6

# 난이도 순서 (낮은 → 높은)
DIFFICULTY_ORDER: list[str] = ["입문", "초급", "중급", "고급"]


def build_history_messages(history: list[dict]) -> list:
    """히스토리를 LangChain 메시지 목록으로 변환 (슬라이딩 윈도우 적용).

    슬라이딩 윈도우 방식:
      - history[-HISTORY_WINDOW:] 만 사용 → 오래된 메시지 자동 탈락
      - HISTORY_WINDOW = 6 → 최근 3턴(왕복)만 LLM에 전달
      - 토큰 사용량 = 최대 HISTORY_WINDOW개 메시지 분량으로 고정

    Args:
        history: [{"role": "user"|"assistant", "content": "..."}, ...] 형태 목록

    Returns:
        LangChain HumanMessage / AIMessage 교대 목록
    """
    recent = history[-HISTORY_WINDOW:]
    messages = []
    for h in recent:
        role = h.get("role", "")
        content = h.get("content", "")
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))
    return messages
