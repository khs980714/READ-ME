"""
수준별 도서 추천 체인 (Level-Based)
사용자가 언급한 수준(입문/초급/중급/고급)을 파악하고,
메타데이터(difficulty) 필터링으로 좁혀진 도서 목록을 기반으로 추천합니다.
"""

from pathlib import Path
from langchain_core.messages import HumanMessage, SystemMessage
from llm import get_llm

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "level_based.txt"

# 질문에서 난이도를 추출하기 위한 키워드 매핑
_LEVEL_KEYWORDS: dict[str, list[str]] = {
    "입문": ["입문", "생초보", "완전초보", "처음", "아무것도 모", "기초도 없", "비전공자", "코딩 모"],
    "초급": ["초급", "기초", "기본", "막 시작", "배우기 시작", "입문은 했"],
    "중급": ["중급", "어느 정도", "어느정도", "조금 할 줄", "실무 경험"],
    "고급": ["고급", "심화", "깊이", "아키텍처", "전문", "시니어", "이미 개발자", "경력"],
}


def extract_difficulty_from_question(question: str) -> str | None:
    """질문 텍스트에서 난이도 키워드를 찾아 반환합니다. 매칭 없으면 None."""
    for difficulty, keywords in _LEVEL_KEYWORDS.items():
        for kw in keywords:
            if kw in question:
                return difficulty
    return None


def _load_system_prompt() -> str:
    if _PROMPT_PATH.exists():
        return _PROMPT_PATH.read_text(encoding="utf-8")
    return (
        "당신은 IT·개발 도서 전문 큐레이터입니다. "
        "사용자의 현재 수준에 딱 맞는 도서를 추천하고, 각 도서가 왜 적합한지 설명해주세요. "
        "추천 도서는 반드시 제공된 목록에서만 선택하세요."
    )


def _build_messages(inputs: dict) -> list:
    question = inputs["question"]
    books = inputs.get("books", [])
    detected_level = inputs.get("detected_level")

    book_list = "\n".join(
        f"- [{b['book_list_id']}] {b['title']} (난이도: {b.get('difficulty', '미분류')})"
        for b in books
    )

    level_note = f"\n감지된 수준: {detected_level}" if detected_level else ""

    user_content = f"""질문: {question}{level_note}

수준에 맞는 도서 목록:
{book_list if book_list else '(해당 수준의 검색된 도서 없음 — 전체 목록에서 추천합니다)'}

사용자의 수준에 맞는 도서를 추천해주세요. 각 도서의 특징과 이 수준에 적합한 이유를 설명해주세요."""

    return [
        SystemMessage(content=_load_system_prompt()),
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
