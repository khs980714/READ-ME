"""
PostgreSQL 연결 (psycopg2 + pgvector)

변경 이력:
- ThreadedConnectionPool 도입: 요청마다 새 커넥션 생성·폐기 비용 제거
- asyncio.to_thread 래퍼: 동기 psycopg2 호출이 이벤트 루프를 블로킹하지 않도록 분리
- vector_search / vector_search_by_difficulty 쿼리 통합 (difficulty=None 중복 제거)
"""

import asyncio
from contextlib import contextmanager

import psycopg2
import psycopg2.pool
from pgvector.psycopg2 import register_vector

from config import settings

# ── 커넥션 풀 ───────────────────────────────────────────────────
# minconn=2: 항상 대기 중인 커넥션 유지 (첫 요청 지연 최소화)
# maxconn=10: 동시 요청 최대치 (uvicorn worker 수와 맞춤)
_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=10,
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
        1 - (be.embedding <=> %s::vector) AS score
    FROM book_embeddings be
    JOIN book_list bl ON bl.id = be.book_list_id
    WHERE EXISTS (
        SELECT 1 FROM books b
        WHERE b.book_list_id = bl.id AND b.is_active = true
    )
      AND 1 - (be.embedding <=> %s::vector) >= %s
      {difficulty_filter}
    ORDER BY score DESC
    LIMIT %s
"""


def _rows_to_dicts(rows) -> list[dict]:
    return [
        {
            "book_list_id": r[0],
            "title": r[1],
            "difficulty": r[2],
            "thumbnail_url": r[3],
            "score": float(r[4]),
        }
        for r in rows
    ]


def _vector_search_sync(
    query_embedding: list[float],
    threshold: float,
    limit: int,
    difficulty: str | None = None,
) -> list[dict]:
    """코사인 유사도 기반 도서 벡터 검색 (동기). difficulty 지정 시 필터 적용."""
    if difficulty:
        sql = _VECTOR_SEARCH_SQL.format(difficulty_filter="AND bl.difficulty = %s")
        params = (query_embedding, query_embedding, threshold, difficulty, limit)
    else:
        sql = _VECTOR_SEARCH_SQL.format(difficulty_filter="")
        params = (query_embedding, query_embedding, threshold, limit)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    return _rows_to_dicts(rows)


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
) -> list[dict]:
    """코사인 유사도 기반 도서 벡터 검색 (async)."""
    return await asyncio.to_thread(
        _vector_search_sync, query_embedding, threshold, limit
    )


async def vector_search_by_difficulty(
    query_embedding: list[float],
    threshold: float,
    limit: int,
    difficulty: str | None = None,
) -> list[dict]:
    """난이도 필터 포함 도서 벡터 검색 (async)."""
    return await asyncio.to_thread(
        _vector_search_sync, query_embedding, threshold, limit, difficulty
    )


async def upsert_embedding(book_list_id: int, embedding: list[float]) -> None:
    """book_embeddings upsert (async)."""
    await asyncio.to_thread(_upsert_embedding_sync, book_list_id, embedding)
