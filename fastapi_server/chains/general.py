"""
일반 도서 질문 체인
"""

from pathlib import Path
from langchain_core.messages import HumanMessage, SystemMessage
from llm import get_llm

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "general.txt"


def _load_system_prompt() -> str:
    if _PROMPT_PATH.exists():
        return _PROMPT_PATH.read_text(encoding="utf-8")
    return (
        "당신은 IT·개발 도서 전문가입니다. "
        "도서에 관한 질문에 친절하고 정확하게 답변해주세요. "
        "관련 도서가 있다면 제공된 목록에서 추천해주세요."
    )


async def general_chain(inputs: dict) -> str:
    question = inputs["question"]
    books = inputs.get("books", [])
    history = inputs.get("history", [])

    book_list = "\n".join(
        f"- [{b['book_id']}] {b['title']} (난이도: {b.get('difficulty', '미분류')})"
        for b in books
    ) if books else ""

    user_content = question
    if book_list:
        user_content += f"\n\n참고할 수 있는 도서 목록:\n{book_list}"

    messages = [SystemMessage(content=_load_system_prompt())]
    for h in history[-6:]:
        if h["role"] == "user":
            messages.append(HumanMessage(content=h["content"]))
    messages.append(HumanMessage(content=user_content))

    llm = get_llm()
    response = await llm.ainvoke(messages)
    return response.content
