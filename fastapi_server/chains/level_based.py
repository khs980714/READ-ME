"""
수준별 도서 추천 체인 (Level-Based)
사용자가 언급한 수준(입문/초급/중급/고급)을 파악하고,
메타데이터(difficulty) 필터링으로 좁혀진 도서 목록을 기반으로 추천합니다.
"""

from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from chains.utils import DIFFICULTY_ORDER, build_history_messages
from llm import get_llm

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "level_based.txt"

# 난이도별 키워드 매핑
# 우선순위: 질문에 여러 수준 키워드가 동시에 있으면 가장 높은 난이도를 반환합니다.
# (예: "중급자인데 고급 심화 책" → 사용자가 목표로 하는 '고급' 반환)
_LEVEL_KEYWORDS: dict[str, list[str]] = {
    "입문": ["입문자", "입문", "생초보", "완전초보", "처음", "아무것도 모", "기초도 없", "비전공자", "코딩 모"],
    "초급": ["초보자", "초급", "기초", "기본", "막 시작", "배우기 시작", "입문은 했"],
    "중급": ["중급자", "중급", "어느 정도", "어느정도", "조금 할 줄", "실무 경험"],
    "고급": ["전문가", "고급자", "고급", "심화", "깊이", "아키텍처", "전문", "시니어", "이미 개발자", "경력자", "경력"],
}


def extract_difficulty_from_question(question: str) -> str | None:
    """질문 텍스트에서 난이도 키워드를 찾아 반환합니다.

    여러 난이도 키워드가 동시에 매칭되면 가장 높은 난이도를 반환합니다.
    사용자가 자신의 현재 수준(낮음)과 원하는 책 수준(높음)을 함께 언급할 때
    목표 수준을 우선 반영하기 위함입니다.
    예) "중급자인데 고급 심화 책 추천" → "고급" 반환

    매칭 키워드 없으면 None을 반환합니다.
    """
    matched_levels: set[str] = set()
    for difficulty, keywords in _LEVEL_KEYWORDS.items():
        for kw in keywords:
            if kw in question:
                matched_levels.add(difficulty)
                break  # 같은 난이도를 중복 추가하지 않음

    if not matched_levels:
        return None

    # DIFFICULTY_ORDER 역순(고급 → 입문)으로 순회하여 가장 높은 매칭 난이도 반환
    for level in reversed(DIFFICULTY_ORDER):
        if level in matched_levels:
            return level

    return None


_SYSTEM_PROMPT: str = (
    _PROMPT_PATH.read_text(encoding="utf-8")
    if _PROMPT_PATH.exists()
    else (
        "당신은 IT·개발 도서 전문 큐레이터입니다. "
        "사용자의 현재 수준에 딱 맞는 도서를 추천하고, 각 도서가 왜 적합한지 설명해주세요. "
        "추천 도서는 반드시 제공된 목록에서만 선택하세요."
    )
)


def _build_messages(inputs: dict) -> list:
    question = inputs["question"]
    books = inputs.get("books", [])
    history = inputs.get("history", [])
    detected_level = inputs.get("detected_level")
    actual_level = inputs.get("actual_level")
    fallback_note = inputs.get("fallback_note")

    book_list = "\n".join(
        f"- [{b.get('book_code', b['book_list_id'])}] {b['title']} (난이도: {b.get('difficulty', '미분류')})"
        for b in books
    )

    level_note = f"\n사용자 요청 수준: {detected_level}" if detected_level else ""
    actual_note = (
        f"\n실제 추천 수준: {actual_level}"
        if actual_level and actual_level != detected_level
        else ""
    )
    fallback_section = f"\n[안내 사항] {fallback_note}" if fallback_note else ""

    user_content = f"""질문: {question}{level_note}{actual_note}{fallback_section}

추천 도서 목록 (유사도 높은 순):
{book_list if book_list else '(검색된 도서 없음)'}

위 도서 목록을 바탕으로 추천해주세요.{' 응답 첫 줄에 [안내 사항]의 내용을 자연스럽게 사용자에게 먼저 전달해주세요.' if fallback_note else ''}"""

    return [
        SystemMessage(content=_SYSTEM_PROMPT),
        *build_history_messages(history),
        HumanMessage(content=user_content),
    ]


async def level_based_chain(inputs: dict) -> str:
    llm = get_llm()
    response = await llm.ainvoke(_build_messages(inputs))
    return response.content


async def level_based_chain_stream(inputs: dict):
    """토큰 단위로 스트리밍하는 async generator."""
    llm = get_llm()
    async for chunk in llm.astream(_build_messages(inputs)):
        yield chunk.content
