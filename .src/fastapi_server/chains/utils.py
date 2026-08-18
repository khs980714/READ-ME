"""
체인 공통 유틸리티

히스토리 슬라이딩 윈도우
  - 최근 HISTORY_WINDOW개 메시지만 유지해 입력 토큰 무한 증가를 방지합니다.
  - 기본값 6 = user 3턴 + assistant 3턴 (약 3번의 대화 맥락 유지)

난이도 순서 상수
  - DIFFICULTY_ORDER: fallback 방향 및 키워드 우선순위 판단에 공유 사용

체인 실행 헬퍼 (load_prompt / run_chain / stream_chain)
  - specific_search·goal_oriented·career_certification·level_based 체인이 공통으로
    사용하던 "프롬프트 파일 로드 + LLM invoke/stream" 보일러플레이트를 통합합니다.
"""

import re
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage

from llm import get_llm

# 최근 유지할 메시지 수 (user + assistant 합산)
HISTORY_WINDOW: int = 6

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

_ASCII_ALPHA_RE = re.compile(r'[a-zA-Z]+')


def normalize_question(text: str) -> str:
    """영문자를 모두 대문자로 변환합니다.

    임베딩·LLM 입력 전에 적용하여 'aws'/'AWS'/'Aws' 간 유사도 편차를 제거합니다.
    한글·숫자·특수문자는 그대로 유지됩니다.

    예) "aws 도서 조회해줘"  → "AWS 도서 조회해줘"
        "파이썬 django 책"  → "파이썬 DJANGO 책"
    """
    return _ASCII_ALPHA_RE.sub(lambda m: m.group().upper(), text)


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


def load_prompt(name: str, fallback: str) -> str:
    """prompts/{name}.txt 파일이 있으면 그 내용을, 없으면 fallback 문자열을 반환합니다."""
    path = _PROMPTS_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8") if path.exists() else fallback


def format_book_line(b: dict) -> str:
    """도서 목록을 프롬프트에 넣기 위한 기본 포맷 라인.

    예) "- D-001 혼자 공부하는 파이썬 (난이도: 입문)"
    """
    code = b.get("book_code") or "D-{:03d}".format(b["book_list_id"])
    return "- {} {} (난이도: {})".format(code, b["title"], b.get("difficulty", "미분류"))


async def run_chain(build_messages_fn, inputs: dict) -> str:
    """build_messages_fn(inputs)로 메시지를 만들어 LLM을 단일 호출하고 응답 텍스트를 반환합니다."""
    llm = get_llm()
    response = await llm.ainvoke(build_messages_fn(inputs))
    return response.content


async def stream_chain(build_messages_fn, inputs: dict):
    """build_messages_fn(inputs)로 메시지를 만들어 토큰 단위로 스트리밍하는 async generator."""
    llm = get_llm()
    async for chunk in llm.astream(build_messages_fn(inputs)):
        yield chunk.content
