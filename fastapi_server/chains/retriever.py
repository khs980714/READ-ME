"""
벡터 검색 로직 (체인 레이어)

질문 유형별 threshold 적용 및 level_based 난이도 fallback 검색을 담당합니다.
라우터에서 직접 DB를 다루지 않도록 검색 책임을 체인 레이어에 위임합니다.
"""

import re

from chains.utils import DIFFICULTY_ORDER
from config import settings
from db import keyword_search, vector_search, vector_search_by_difficulty
from llm import get_embeddings

# 조회 의도 질문에서 기술·주제 키워드를 추출하기 위한 패턴 목록
# 우선순위 순으로 적용 (앞 패턴이 먼저 매칭)
_KEYWORD_EXTRACT_PATTERNS = [
    # "AWS 도서 조회해줘" / "파이썬 책 있어?" 형태
    re.compile(r'^(.+?)\s+(?:도서|책)\s*(?:조회|목록|있어|있나|보여|알려)', re.IGNORECASE),
    # "AWS 관련 도서" / "파이썬 관련 책"
    re.compile(r'^(.+?)\s+관련\s+(?:도서|책)', re.IGNORECASE),
    # "AWS에 대한 도서" / "파이썬에 관한 책"
    re.compile(r'^(.+?)\s*(?:에\s*대한|에\s*관한|에\s*관련된)\s+(?:도서|책)', re.IGNORECASE),
    # "도서 목록 조회해줘" 같은 순수 조회 (기술명 없음)
    re.compile(r'^(.+?)\s+(?:조회|목록)', re.IGNORECASE),
]

# 키워드 추출 후 제거할 후처리 패턴
_KEYWORD_CLEANUP = re.compile(
    r'\s*(?:도서|책|조회해줘?|조회해줄래\??|조회|있어\??|있나요\??|있나\??|'
    r'있습니까\??|있는지|보여줘?|알려줘?|알려주세요|목록|관련|관한|어떤|뭐가|뭐)\s*',
    re.IGNORECASE,
)


def extract_search_keyword(question: str) -> str:
    """조회 질문에서 검색 키워드를 추출합니다.

    예) "AWS 도서 조회해줘" → "AWS"
        "파이썬 관련 책 있어?" → "파이썬"
        "aws 책 보여줘" → "aws"
    """
    for pattern in _KEYWORD_EXTRACT_PATTERNS:
        m = pattern.match(question.strip())
        if m:
            keyword = m.group(1).strip()
            keyword = _KEYWORD_CLEANUP.sub(' ', keyword).strip()
            if keyword:
                return keyword

    # 패턴 미매칭 시: 불필요한 단어 제거 후 반환
    cleaned = _KEYWORD_CLEANUP.sub(' ', question).strip()
    return cleaned if cleaned else question.strip()

_THRESHOLD_MAP: dict[str, float] = {
    "specific_search": settings.RECOMMENDATION_THRESHOLD_SPECIFIC_SEARCH,
    "goal_oriented": settings.RECOMMENDATION_THRESHOLD_GOAL_ORIENTED,
    "career_certification": settings.RECOMMENDATION_THRESHOLD_CAREER_CERTIFICATION,
    "level_based": settings.RECOMMENDATION_THRESHOLD_LEVEL_BASED,
}


async def retrieve_books_by_keyword(question: str) -> tuple[list, str]:
    """제목·설명 ILIKE 검색. 키워드를 추출하여 DB에서 직접 조회합니다.
    limit 없이 전체 결과를 반환합니다.

    Returns:
        (books, keyword) — 검색에 사용된 키워드도 함께 반환
    """
    keyword = extract_search_keyword(question)
    books = await keyword_search(keyword=keyword)
    return books, keyword


async def retrieve_books(message: str, question_type: str) -> list:
    """질문 임베딩 → 벡터 검색 (유사도 내림차순). 질문 유형별 threshold 적용."""
    threshold = _THRESHOLD_MAP.get(
        question_type,
        settings.RECOMMENDATION_THRESHOLD_SPECIFIC_SEARCH,
    )
    q_embedding = await get_embeddings(message, input_type="query")
    return await vector_search(
        query_embedding=q_embedding,
        threshold=threshold,
        limit=settings.RECOMMENDATION_MAX,
    )


async def retrieve_books_level_based(
    message: str,
    detected_level: str | None,
) -> tuple[list, str | None, str | None]:
    """수준별 추천 도서 검색 (난이도 fallback 포함, 유사도 내림차순).

    fallback 순서:
      1. 요청 난이도 정확 검색
      2. 더 쉬운 난이도로 한 단계씩 시도 (입문 방향)
      3. 더 어려운 난이도로 한 단계씩 시도 (고급 방향)
      4. 모두 없으면 전체 검색

    Args:
        message:        사용자 질문 원문
        detected_level: 질문에서 추출된 난이도 (없으면 None)

    Returns:
        (books, actual_level, fallback_note)
        - actual_level:  실제 검색에 사용된 난이도 (fallback 발생 시 변경됨)
        - fallback_note: fallback 발생 시 LLM에 전달할 안내 문구 (없으면 None)
    """
    threshold = settings.RECOMMENDATION_THRESHOLD_LEVEL_BASED
    limit = settings.RECOMMENDATION_MAX
    q_embedding = await get_embeddings(message, input_type="query")

    # 난이도 감지 실패 → 전체 검색
    if not detected_level or detected_level not in DIFFICULTY_ORDER:
        books = await vector_search(q_embedding, threshold, limit)
        return books, None, None

    # 1) 요청 난이도 정확 검색
    books = await vector_search_by_difficulty(q_embedding, threshold, limit, detected_level)
    if books:
        return books, detected_level, None

    idx = DIFFICULTY_ORDER.index(detected_level)

    # 2) 더 쉬운 난이도로 fallback
    for i in range(idx - 1, -1, -1):
        fallback = DIFFICULTY_ORDER[i]
        books = await vector_search_by_difficulty(q_embedding, threshold, limit, fallback)
        if books:
            return books, fallback, (
                f"요청하신 **{detected_level}** 수준의 도서가 없어 "
                f"한 단계 낮은 **{fallback}** 수준의 도서를 추천합니다."
            )

    # 3) 더 어려운 난이도로 fallback
    for i in range(idx + 1, len(DIFFICULTY_ORDER)):
        fallback = DIFFICULTY_ORDER[i]
        books = await vector_search_by_difficulty(q_embedding, threshold, limit, fallback)
        if books:
            return books, fallback, (
                f"요청하신 **{detected_level}** 수준 및 더 쉬운 수준의 도서가 없어 "
                f"**{fallback}** 수준의 도서를 추천합니다."
            )

    # 4) 아무것도 없으면 전체 검색
    books = await vector_search(q_embedding, threshold, limit)
    return books, None, (
        f"요청하신 **{detected_level}** 수준의 도서를 찾지 못해 "
        f"전체 도서에서 유사도 높은 순으로 추천합니다."
    )
