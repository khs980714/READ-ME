"""
벡터 검색 로직 (체인 레이어)

질문 유형별 threshold 적용 및 level_based 난이도 fallback 검색을 담당합니다.
라우터에서 직접 DB를 다루지 않도록 검색 책임을 체인 레이어에 위임합니다.
"""

import re

from chains.utils import DIFFICULTY_ORDER
from config import settings
from db import keyword_search, vector_search
from llm import get_embeddings

# ── 자격증명 추출 패턴 ─────────────────────────────────────────
# 우선순위: 산업기사 > 기술사 > 기능사 > 기사 (긴 접미사 먼저 → 오매칭 방지)
_CERT_NAME_RE = re.compile(
    r'[가-힣]+(?:산업기사|기술사|기능사|기사)',
)


def extract_certification_name(question: str) -> str | None:
    """질문에서 자격증명을 추출합니다.

    예) "정보처리기사 실기 대비 도서" → "정보처리기사"
        "정보처리산업기사 추천해줘"  → "정보처리산업기사"
        "파이썬 도서 추천"           → None (자격증명 없음)
    """
    m = _CERT_NAME_RE.search(question)
    return m.group() if m else None


def filter_by_certification(cert_name: str, books: list[dict]) -> list[dict]:
    """자격증명과 다른 등급의 자격증 도서를 필터링합니다.

    도서 제목에서 자격증명을 추출한 뒤, 질문에 언급된 자격증명과 다르면 제외합니다.
    제목에 자격증명이 없는 도서(일반 도서)는 유지합니다.

    예) cert_name="정보처리기사" → "정보처리산업기사 실기" 제목의 도서 제외
        cert_name="정보처리기사" → "파이썬 완벽 가이드" 제목의 도서 유지

    필터링 결과가 비어있으면 원본 목록을 반환합니다 (안전 fallback).
    """
    filtered = []
    for book in books:
        title_cert = _CERT_NAME_RE.search(book["title"])
        if title_cert is None:
            filtered.append(book)
        elif title_cert.group() == cert_name:
            filtered.append(book)

    return filtered if filtered else books


def extract_exam_type(question: str) -> str | None:
    """질문에서 시험 유형(실기/필기)을 추출합니다.

    둘 다 포함되거나 둘 다 없으면 None 반환 (필터링 불필요).
    """
    has_silgi = "실기" in question
    has_pilgi = "필기" in question
    if has_silgi and not has_pilgi:
        return "실기"
    if has_pilgi and not has_silgi:
        return "필기"
    return None


def filter_by_exam_type(exam_type: str, books: list[dict]) -> list[dict]:
    """시험 유형(실기/필기)과 맞지 않는 도서를 필터링합니다.

    판단 기준 (제목 기준):
      - 요청 유형만 포함 → 유지
      - 요청 유형 + 반대 유형 모두 포함 (예: '필기 + 실기 올인원') → 유지
      - 반대 유형만 포함 → 제외
      - 둘 다 없음 (일반 도서) → 유지

    예) exam_type="실기"
      "정보처리기사 실기"             → 유지 (실기 있음)
      "정보처리기사 필기"             → 제외 (필기만 있음)
      "정보처리기사 필기 + 실기 올인원" → 유지 (둘 다 있음)
      "파이썬 완벽 가이드"            → 유지 (둘 다 없음)

    필터링 결과가 비어있으면 원본 목록을 반환합니다 (안전 fallback).
    """
    opposite = "필기" if exam_type == "실기" else "실기"
    filtered = []
    for book in books:
        title = book["title"]
        has_target = exam_type in title
        has_opposite = opposite in title
        if has_opposite and not has_target:
            # 반대 유형만 있음 → 제외
            continue
        filtered.append(book)

    return filtered if filtered else books

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


_CERTIFICATION_KEYWORDS = re.compile(
    r'자격증|수험서|기출(?:문제)?|필기\s*(?:시험|준비|대비)|실기\s*(?:시험|준비|대비)|'
    r'SQLD|ITQ|MOS|CISA|리눅스마스터|정보보안기사|컴퓨터활용|컴활|워드프로세서',
    re.IGNORECASE,
)


def _is_certification_query(question: str) -> bool:
    """질문이 자격증 취득 관련인지 판별합니다 (취업·포트폴리오와 구분)."""
    return bool(
        _CERTIFICATION_KEYWORDS.search(question) or extract_certification_name(question)
    )


async def retrieve_books(message: str, question_type: str) -> list:
    """질문 임베딩 → 벡터 검색 (유사도 내림차순). 질문 유형별 threshold 적용.

    career_certification 유형:
      - 최신 연도 가중치를 강화하여 최신 수험서를 우선 노출.
      - 질문에 자격증명(예: 정보처리기사)이 명시된 경우, 다른 등급 자격증 도서를 후처리 필터링.
      - 자격증 관련 질문이면 자격증 카테고리로 범위를 좁혀 검색.
    """
    threshold = _THRESHOLD_MAP.get(
        question_type,
        settings.RECOMMENDATION_THRESHOLD_SPECIFIC_SEARCH,
    )
    q_embedding = await get_embeddings(message, input_type="query")
    is_certification = question_type == "career_certification"

    # 자격증 질문이면 자격증 카테고리 필터 적용
    category_filter: str | None = None
    if is_certification and _is_certification_query(message):
        category_filter = "자격증"

    books = await vector_search(
        query_embedding=q_embedding,
        threshold=threshold,
        limit=settings.RECOMMENDATION_MAX,
        is_certification=is_certification,
        category=category_filter,
    )

    # 카테고리 필터 후 결과가 없으면 필터 없이 재검색 (안전 fallback)
    if is_certification and category_filter and not books:
        books = await vector_search(
            query_embedding=q_embedding,
            threshold=threshold,
            limit=settings.RECOMMENDATION_MAX,
            is_certification=is_certification,
        )

    if is_certification:
        cert_name = extract_certification_name(message)
        if cert_name:
            books = filter_by_certification(cert_name, books)

        exam_type = extract_exam_type(message)
        if exam_type:
            books = filter_by_exam_type(exam_type, books)

    return books


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
    books = await vector_search(q_embedding, threshold, limit, detected_level)
    if books:
        return books, detected_level, None

    idx = DIFFICULTY_ORDER.index(detected_level)

    # 2) 더 쉬운 난이도로 fallback
    for i in range(idx - 1, -1, -1):
        fallback = DIFFICULTY_ORDER[i]
        books = await vector_search(q_embedding, threshold, limit, fallback)
        if books:
            return books, fallback, (
                f"요청하신 **{detected_level}** 수준의 도서가 없어 "
                f"한 단계 낮은 **{fallback}** 수준의 도서를 추천합니다."
            )

    # 3) 더 어려운 난이도로 fallback
    for i in range(idx + 1, len(DIFFICULTY_ORDER)):
        fallback = DIFFICULTY_ORDER[i]
        books = await vector_search(q_embedding, threshold, limit, fallback)
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
