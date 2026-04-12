"""
진로·목적 기반 추천 체인 (Goal-Oriented)
사용자의 목표(취업, 커리어 전환, IT 입문 등)를 분석하여
자기개발 → IT 입문 → 심화 순의 로드맵 형식으로 도서를 추천합니다.
"""

from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from chains.utils import build_history_messages
from llm import get_llm

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "goal_oriented.txt"


def _load_system_prompt() -> str:
    if _PROMPT_PATH.exists():
        return _PROMPT_PATH.read_text(encoding="utf-8")
    return (
        "당신은 IT·개발 분야 커리어 멘토입니다. "
        "사용자의 목표와 현재 상황을 분석하여 단계적으로 읽어야 할 도서를 큐레이션해주세요. "
        "자기개발 → IT 입문 → 포트폴리오/취업 준비 순서로 여러 카테고리를 조합한 로드맵 형식으로 구성하고, "
        "추천 도서는 반드시 제공된 목록에서만 선택하세요."
    )


def _build_messages(inputs: dict) -> list:
    question = inputs["question"]
    books = inputs.get("books", [])
    history = inputs.get("history", [])

    book_list = "\n".join(
        f"- [{b['book_list_id']}] {b['title']} (난이도: {b.get('difficulty', '미분류')})"
        for b in books
    )

    user_content = f"""질문: {question}

참고 도서 목록:
{book_list if book_list else '(검색된 도서 없음)'}

사용자의 목표를 분석하여 단계별 도서 큐레이션을 제공해주세요.
- 1단계: 동기부여·개념 이해 (자기개발·IT 개론)
- 2단계: 핵심 기술 학습 (입문·기초)
- 3단계: 실전 적용·심화 (포트폴리오·프로젝트)
각 단계에 적합한 도서를 위 목록에서 선택하여 추천해주세요."""

    return [
        SystemMessage(content=_load_system_prompt()),
        *build_history_messages(history),
        HumanMessage(content=user_content),
    ]


async def goal_oriented_chain(inputs: dict) -> str:
    llm = get_llm()
    response = await llm.ainvoke(_build_messages(inputs))
    return response.content


async def goal_oriented_chain_stream(inputs: dict):
    """토큰 단위로 스트리밍하는 async generator."""
    llm = get_llm()
    async for chunk in llm.astream(_build_messages(inputs)):
        yield chunk.content
