"""
PostgreSQL 연결 (psycopg2 + pgvector)
요청별 독립 커넥션을 사용하여 트랜잭션 상태 오염 문제를 방지합니다.
"""

from contextlib import contextmanager

import psycopg2
from pgvector.psycopg2 import register_vector
from config import settings


@contextmanager
def get_conn():
    """요청별 독립 커넥션. 성공 시 commit, 실패 시 rollback 후 close."""
    conn = psycopg2.connect(settings.DATABASE_URL)
    register_vector(conn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def vector_search(query_embedding: list[float], threshold: float, limit: int) -> list[dict]:
    """코사인 유사도 기반 도서 벡터 검색 (book_list 단위)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
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
                ORDER BY score DESC
                LIMIT %s
                """,
                (query_embedding, query_embedding, threshold, limit),
            )
            rows = cur.fetchall()
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


def upsert_embedding(book_list_id: int, embedding: list[float]) -> None:
    """book_embeddings upsert (book_list 단위)."""
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
