"""
학습 로드맵 체인
단계별 학습 가이드 + 각 단계에 맞는 도서 추천
"""

from pathlib import Path
from langchain_core.messages import HumanMessage, SystemMessage
from llm import get_llm

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "roadmap.txt"


def _load_system_prompt() -> str:
    if _PROMPT_PATH.exists():
        return _PROMPT_PATH.read_text(encoding="utf-8")
    return (
        "당신은 IT·개발 분야 학습 로드맵 전문가입니다. "
        "단계별 학습 가이드를 제공하고 각 단계에 적합한 도서를 추천해주세요. "
        "응답은 단계별로 명확하게 구분하여 작성하고, 추천 도서는 제공된 목록에서만 선택하세요."
    )


async def roadmap_chain(inputs: dict) -> str:
    question = inputs["question"]
    books = inputs.get("books", [])
    history = inputs.get("history", [])

    book_list = "\n".join(
        f"- [{b['book_id']}] {b['title']} (난이도: {b.get('difficulty', '미분류')})"
        for b in books
    )

    user_content = f"""질문: {question}

참고 도서 목록:
{book_list if book_list else '(검색된 도서 없음)'}

위 도서 목록을 활용하여 단계별 학습 로드맵을 작성해주세요.
각 단계마다 적합한 도서를 추천하고, 왜 그 도서가 해당 단계에 적합한지 설명해주세요."""

    messages = [SystemMessage(content=_load_system_prompt())]
    for h in history[-6:]:
        if h["role"] == "user":
            messages.append(HumanMessage(content=h["content"]))
    messages.append(HumanMessage(content=user_content))

    llm = get_llm()
    response = await llm.ainvoke(messages)
    return response.content
