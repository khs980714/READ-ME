"""
수준별 도서 추천 체인
"""

from pathlib import Path
from langchain_core.messages import HumanMessage, SystemMessage
from llm import get_llm

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "level_based.txt"


def _load_system_prompt() -> str:
    if _PROMPT_PATH.exists():
        return _PROMPT_PATH.read_text(encoding="utf-8")
    return (
        "당신은 IT·개발 도서 전문 큐레이터입니다. "
        "사용자의 현재 수준에 맞는 도서를 추천하고, 각 도서가 왜 적합한지 설명해주세요. "
        "추천 도서는 반드시 제공된 목록에서만 선택하세요."
    )


def _build_messages(inputs: dict) -> list:
    question = inputs["question"]
    books = inputs.get("books", [])

    book_list = "\n".join(
        f"- [{b['book_list_id']}] {b['title']} (난이도: {b.get('difficulty', '미분류')})"
        for b in books
    )

    user_content = f"""질문: {question}

참고 도서 목록:
{book_list if book_list else '(검색된 도서 없음)'}

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
