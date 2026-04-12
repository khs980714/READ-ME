"""
기술 스택·키워드 기반 탐색 체인 (Specific Search)
사용자가 특정 기술·도구 이름을 언급할 때 벡터 검색 결과를 바탕으로 답변합니다.
"""

from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from chains.utils import build_history_messages
from llm import get_llm

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "specific_search.txt"


def _load_system_prompt() -> str:
    if _PROMPT_PATH.exists():
        return _PROMPT_PATH.read_text(encoding="utf-8")
    return (
        "당신은 IT·개발 도서 전문가입니다. "
        "사용자가 언급한 기술이나 키워드와 가장 관련 있는 도서를 추천해주세요. "
        "추천 도서는 반드시 제공된 목록에서만 선택하고, 각 도서가 해당 기술 학습에 어떻게 도움이 되는지 설명해주세요."
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

검색된 도서 목록:
{book_list if book_list else '(검색된 도서 없음)'}

위 도서 목록에서 질문과 가장 관련 있는 도서를 추천하고, 각 도서의 특징과 도움이 되는 이유를 설명해주세요."""

    return [
        SystemMessage(content=_load_system_prompt()),
        *build_history_messages(history),
        HumanMessage(content=user_content),
    ]


async def specific_search_chain(inputs: dict) -> str:
    llm = get_llm()
    response = await llm.ainvoke(_build_messages(inputs))
    return response.content


async def specific_search_chain_stream(inputs: dict):
    """토큰 단위로 스트리밍하는 async generator."""
    llm = get_llm()
    async for chunk in llm.astream(_build_messages(inputs)):
        yield chunk.content
