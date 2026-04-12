"""
성과·증명 중심 추천 체인 (Career/Certification)
자격증 취득, 포트폴리오 완성 등 가시적 성과를 원하는 사용자에게
수험서·사례집·프로젝트 중심 도서를 추천합니다.
"""

from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from chains.utils import build_history_messages
from llm import get_llm

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "career_certification.txt"


def _load_system_prompt() -> str:
    if _PROMPT_PATH.exists():
        return _PROMPT_PATH.read_text(encoding="utf-8")
    return (
        "당신은 IT 자격증·취업 준비 전문 컨설턴트입니다. "
        "자격증 수험서, 포트폴리오 프로젝트 사례집, 실무 중심 도서를 우선적으로 추천해주세요. "
        "추천 도서는 반드시 제공된 목록에서만 선택하고, "
        "해당 자격증이나 포트폴리오 목표 달성에 어떻게 도움이 되는지 구체적으로 설명해주세요."
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

위 목록에서 자격증 준비 또는 포트폴리오 완성에 적합한 도서를 추천하고,
각 도서가 목표 달성에 어떻게 기여하는지 설명해주세요."""

    return [
        SystemMessage(content=_load_system_prompt()),
        *build_history_messages(history),
        HumanMessage(content=user_content),
    ]


async def career_certification_chain(inputs: dict) -> str:
    llm = get_llm()
    response = await llm.ainvoke(_build_messages(inputs))
    return response.content


async def career_certification_chain_stream(inputs: dict):
    """토큰 단위로 스트리밍하는 async generator."""
    llm = get_llm()
    async for chunk in llm.astream(_build_messages(inputs)):
        yield chunk.content
