"""
벡터 검색 로직 (체인 레이어)

질문 유형별 threshold 적용 및 level_based 난이도 fallback 검색을 담당합니다.
라우터에서 직접 DB를 다루지 않도록 검색 책임을 체인 레이어에 위임합니다.
"""

from chains.utils import DIFFICULTY_ORDER
from config import settings
from db import vector_search, vector_search_by_difficulty
from llm import get_embeddings

_THRESHOLD_MAP: dict[str, float] = {
    "specific_search": settings.RECOMMENDATION_THRESHOLD_SPECIFIC_SEARCH,
    "goal_oriented": settings.RECOMMENDATION_THRESHOLD_GOAL_ORIENTED,
    "career_certification": settings.RECOMMENDATION_THRESHOLD_CAREER_CERTIFICATION,
    "level_based": settings.RECOMMENDATION_THRESHOLD_LEVEL_BASED,
}


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
