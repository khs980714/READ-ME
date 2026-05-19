"""
PostgreSQL 연결 (psycopg2 + pgvector)

변경 이력:
- ThreadedConnectionPool 도입: 요청마다 새 커넥션 생성·폐기 비용 제거
- asyncio.to_thread 래퍼: 동기 psycopg2 호출이 이벤트 루프를 블로킹하지 않도록 분리
- vector_search / vector_search_by_difficulty 쿼리 통합 (difficulty=None 중복 제거)
"""

import asyncio
import re
from contextlib import contextmanager

import psycopg2
import psycopg2.pool
from pgvector.psycopg2 import register_vector

from config import settings

# ── 커넥션 풀 ───────────────────────────────────────────────────
# minconn=3: 항상 대기 중인 커넥션 유지 (첫 요청 지연 최소화)
# maxconn=15: locust 부하 테스트 기반 조정 (동시 요청 최대치)
#   - /chat/message: 평균 ~2 req/s, 피크 시 동시 처리 고려
#   - /chat/message/stream: heavy 시나리오 ~1.2 req/s
#   - uvicorn worker 기본 1개 기준, 스레드 풀 여유분 포함
_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=3,
            maxconn=15,
            dsn=settings.DATABASE_URL,
        )
    return _pool


@contextmanager
def get_conn():
    """풀에서 커넥션을 대여하고 반납하는 컨텍스트 매니저."""
    pool = _get_pool()
    conn = pool.getconn()
    register_vector(conn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


# ── 동기 쿼리 함수 ─────────────────────────────────────────────

_VECTOR_SEARCH_SQL = """
    SELECT
        bl.id,
        bl.title,
        bl.difficulty,
        bl.thumbnail_url,
        1 - (be.embedding <=> %s::vector) AS score,
        bl.publication_year,
        bl.edition,
        (SELECT b.book_code FROM books b WHERE b.book_list_id = bl.id AND b.is_active = true ORDER BY b.book_code LIMIT 1) AS book_code
    FROM book_embeddings be
    JOIN book_list bl ON bl.id = be.book_list_id
    WHERE EXISTS (
        SELECT 1 FROM books b
        WHERE b.book_list_id = bl.id AND b.is_active = true
    )
      AND 1 - (be.embedding <=> %s::vector) >= %s
      {difficulty_filter}
      {category_filter}
    ORDER BY score DESC
    LIMIT %s
"""

_CATEGORY_FILTER_SQL = """AND EXISTS (
        SELECT 1 FROM book_list_categories blc
        JOIN categories c ON c.id = blc.category_id
        WHERE blc.book_list_id = bl.id AND c.name = %s
    )"""


def _rows_to_dicts(rows) -> list[dict]:
    return [
        {
            "book_list_id":     r[0],
            "title":            r[1],
            "difficulty":       r[2],
            "thumbnail_url":    r[3],
            "score":            float(r[4]),
            "publication_year": r[5],
            "edition":          r[6] or "",
            "book_code":        r[7] or f"D-{r[0]:03d}",
        }
        for r in rows
    ]


# 제목/설명에서 연도를 추출하기 위한 패턴 (2020~2039 범위)
_YEAR_RE = re.compile(r"20[2-3]\d")


def _make_sort_key(is_certification: bool = False):
    """정렬 키 팩토리. 항상 (primary, secondary) 튜플을 반환합니다.

    자격증 쿼리 (is_certification=True):
      연도를 1차 키로 사용 → 최신 출판 도서가 점수와 무관하게 항상 앞에 위치.
      같은 연도 내에서는 순수 코사인 유사도(score) 순으로 정렬.
        primary  = publication_year (0 → 연도 미상, 가장 낮은 우선순위)
        secondary = score

    일반 쿼리 (is_certification=False):
      연도 보정 없이 순수 코사인 유사도(score)만 사용.
        primary  = 0 (고정)
        secondary = score
    """
    def _key(book: dict) -> tuple:
        score = book["score"]

        if is_certification:
            year = book.get("publication_year")
            if not year:
                match = _YEAR_RE.search(book["title"])
                if match:
                    year = int(match.group())
            return (year or 0, score)
        else:
            return (0, score)

    return _key


def _vector_search_sync(
    query_embedding: list[float],
    threshold: float,
    limit: int,
    difficulty: str | None = None,
    is_certification: bool = False,
    category: str | None = None,
) -> list[dict]:
    """코사인 유사도 기반 도서 벡터 검색 (동기). difficulty/category 지정 시 필터 적용."""
    difficulty_filter = "AND bl.difficulty = %s" if difficulty else ""
    category_filter = _CATEGORY_FILTER_SQL if category else ""
    sql = _VECTOR_SEARCH_SQL.format(
        difficulty_filter=difficulty_filter,
        category_filter=category_filter,
    )

    params: list = [query_embedding, query_embedding, threshold]
    if difficulty:
        params.append(difficulty)
    if category:
        params.append(category)
    params.append(limit)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    books = _rows_to_dicts(rows)
    # 유사도 점수 기준으로 먼저 가져온 뒤, 최신 연도 + 개정판 우선 재정렬
    return sorted(books, key=_make_sort_key(is_certification), reverse=True)


def _upsert_embedding_sync(book_list_id: int, embedding: list[float]) -> None:
    """book_embeddings upsert (동기)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO book_embeddings (book_list_id, embedding)
                VALUES (%s, %s::vector)
                ON CONFLICT (book_list_id)
                DO UPDATE SET embedding = EXCLUDED.embedding, updated_at = NOW()
                """,
                (book_list_id, embedding),
            )


# ── async 공개 API ─────────────────────────────────────────────
# asyncio.to_thread: 동기 DB 호출을 스레드 풀에서 실행 → 이벤트 루프 블로킹 방지

async def vector_search(
    query_embedding: list[float],
    threshold: float,
    limit: int,
    is_certification: bool = False,
    category: str | None = None,
) -> list[dict]:
    """코사인 유사도 기반 도서 벡터 검색 (async)."""
    return await asyncio.to_thread(
        _vector_search_sync, query_embedding, threshold, limit, None, is_certification, category
    )


async def vector_search_by_difficulty(
    query_embedding: list[float],
    threshold: float,
    limit: int,
    difficulty: str | None = None,
    is_certification: bool = False,
) -> list[dict]:
    """난이도 필터 포함 도서 벡터 검색 (async)."""
    return await asyncio.to_thread(
        _vector_search_sync, query_embedding, threshold, limit, difficulty, is_certification
    )


async def upsert_embedding(book_list_id: int, embedding: list[float]) -> None:
    """book_embeddings upsert (async)."""
    await asyncio.to_thread(_upsert_embedding_sync, book_list_id, embedding)


# ── 키워드(제목) 검색 ──────────────────────────────────────────

_KEYWORD_SEARCH_SQL = """
    SELECT
        bl.id,
        bl.title,
        bl.difficulty,
        bl.thumbnail_url,
        1.0 AS score,
        bl.publication_year,
        bl.edition,
        (SELECT b.book_code FROM books b WHERE b.book_list_id = bl.id AND b.is_active = true ORDER BY b.book_code LIMIT 1) AS book_code
    FROM book_list bl
    WHERE EXISTS (
        SELECT 1 FROM books b
        WHERE b.book_list_id = bl.id AND b.is_active = true
    )
      AND (bl.title ILIKE %s OR bl.description ILIKE %s)
    ORDER BY bl.title
"""


def _keyword_search_sync(keyword: str) -> list[dict]:
    """제목·설명 ILIKE 검색 (동기). limit 없이 전체 결과 반환.

    동일 제목의 개정판이 먼저 노출되도록 최신 연도 + 개정판 우선 정렬 적용.
    """
    pattern = f"%{keyword}%"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_KEYWORD_SEARCH_SQL, (pattern, pattern))
            rows = cur.fetchall()
    books = _rows_to_dicts(rows)
    return sorted(books, key=_make_sort_key(False), reverse=True)


async def keyword_search(keyword: str) -> list[dict]:
    """제목·설명 ILIKE 검색 (async). limit 없이 전체 결과 반환."""
    return await asyncio.to_thread(_keyword_search_sync, keyword)
