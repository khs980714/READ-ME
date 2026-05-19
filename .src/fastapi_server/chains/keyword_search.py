"""
키워드 단순 조회 체인 (Keyword Search)

"AWS 도서 조회해줘", "파이썬 책 있어?" 처럼 특정 키워드의 도서 목록을
직접 조회하는 질문에 응답합니다.
벡터 유사도 대신 제목·설명 ILIKE 검색을 사용하므로 대소문자·조사에 무관하게
DB에 존재하는 모든 관련 도서를 반환합니다.
LLM 호출 없이 포맷된 결과만 반환해 응답 지연을 최소화합니다.
"""

from chains.utils import DIFFICULTY_ORDER


def _difficulty_label(difficulty: str | None) -> str:
    if difficulty in DIFFICULTY_ORDER:
        return difficulty
    return "미분류"


def keyword_search_chain(inputs: dict) -> str:
    """검색된 도서 목록을 포맷해 반환합니다 (LLM 호출 없음)."""
    books: list = inputs.get("books", [])
    keyword: str = inputs.get("keyword", "")
    question: str = inputs.get("question", "")

    if not books:
        return (
            f"**'{keyword}'** 관련 도서를 찾지 못했습니다. "
            "다른 키워드로 다시 검색해 주세요."
        )

    header = f"**'{keyword}'** 관련 도서 {len(books)}권을 찾았습니다.\n"
    lines = []
    for i, b in enumerate(books, start=1):
        diff = _difficulty_label(b.get("difficulty"))
        lines.append(f"{i}. {b['title']} (난이도: {diff})")

    return header + "\n".join(lines)


async def keyword_search_chain_stream(inputs: dict):
    """keyword_search_chain 결과를 한 번에 yield (스트리밍 인터페이스 호환)."""
    yield keyword_search_chain(inputs)
