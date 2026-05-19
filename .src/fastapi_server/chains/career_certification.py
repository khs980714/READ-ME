"""
성과·증명 중심 추천 체인 (Career/Certification)
자격증 취득, 포트폴리오 완성 등 가시적 성과를 원하는 사용자에게
수험서·사례집·프로젝트 중심 도서를 추천합니다.
"""

from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from chains.retriever import extract_certification_name, extract_exam_type
from chains.utils import build_history_messages
from llm import get_llm

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "career_certification.txt"

_SYSTEM_PROMPT: str = (
    _PROMPT_PATH.read_text(encoding="utf-8")
    if _PROMPT_PATH.exists()
    else (
        "당신은 IT 자격증·취업 준비 전문 컨설턴트입니다. "
        "자격증 수험서, 포트폴리오 프로젝트 사례집, 실무 중심 도서를 우선적으로 추천해주세요. "
        "추천 도서는 반드시 제공된 목록에서만 선택하고, "
        "해당 자격증이나 포트폴리오 목표 달성에 어떻게 도움이 되는지 구체적으로 설명해주세요."
    )
)


def _build_messages(inputs: dict) -> list:
    question = inputs["question"]
    books = inputs.get("books", [])
    history = inputs.get("history", [])

    # 동일 제목 도서 dedup: LLM에는 제목당 하나만 전달 (최신 연도·높은 점수 우선)
    seen_titles: set[str] = set()
    deduped: list[dict] = []
    for b in books:
        t = b["title"]
        if t not in seen_titles:
            seen_titles.add(t)
            deduped.append(b)
    books = deduped

    def _format_book(b: dict) -> str:
        code = b.get("book_code") or "D-{:03d}".format(b["book_list_id"])
        title = b["title"]
        edition = b.get("edition", "")
        year = b.get("publication_year")
        difficulty = b.get("difficulty", "미분류")

        meta_parts = []
        if year:
            meta_parts.append(f"{year}년판")
        if edition:
            meta_parts.append(edition)
        meta = f" [{', '.join(meta_parts)}]" if meta_parts else ""

        return f"- {code} {title}{meta} (난이도: {difficulty})"

    book_list = "\n".join(_format_book(b) for b in books)

    cert_name = extract_certification_name(question)
    exam_type = extract_exam_type(question)

    notices = []
    if cert_name:
        notices.append(f"요청 자격증: **{cert_name}** — 이 자격증 도서만 추천하고, 다른 등급 자격증 도서는 제외하세요.")
    if exam_type:
        opposite = "필기" if exam_type == "실기" else "실기"
        notices.append(f"요청 시험 유형: **{exam_type}** — {opposite} 전용 도서는 추천하지 마세요. 단, '{exam_type} + {opposite} 올인원' 도서는 추천 가능합니다.")
    cert_notice = ("\n" + "\n".join(notices)) if notices else ""

    user_content = f"""질문: {question}{cert_notice}

검색된 도서 목록 (최신 연도 우선 정렬):
{book_list if book_list else '(검색된 도서 없음)'}

위 목록에서 자격증 준비 또는 포트폴리오 완성에 적합한 도서를 추천하고,
각 도서가 목표 달성에 어떻게 기여하는지 설명해주세요."""

    return [
        SystemMessage(content=_SYSTEM_PROMPT),
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
