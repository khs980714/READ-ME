"""
0008 — book_embeddings 벡터 인덱스 추가 (HNSW via halfvec)

pgvector 0.6.0 이상에서 halfvec 캐스팅으로 2048차원 HNSW 인덱스를 생성합니다.
지원하지 않는 pgvector 버전에서는 인덱스 생성을 건너뛰고 경고를 출력합니다.

⚠️  halfvec 방식은 벡터를 16-bit 부동소수점으로 표현하므로 32-bit 대비
    미미한 정밀도 손실이 있습니다. 도서 추천 시나리오에서는 무시 가능한 수준입니다.

인덱스 생성 후 코사인 유사도 검색 속도가 전체 스캔(sequential scan) 대비
도서 수에 따라 10배 이상 빠를 수 있습니다.
"""

import logging

from django.db import migrations

logger = logging.getLogger(__name__)

_CREATE_INDEX_SQL = """
    CREATE INDEX IF NOT EXISTS book_embeddings_embedding_hnsw_idx
    ON book_embeddings
    USING hnsw ((embedding::halfvec(2048)) halfvec_cosine_ops);
"""

_DROP_INDEX_SQL = """
    DROP INDEX IF EXISTS book_embeddings_embedding_hnsw_idx;
"""


def _add_vector_index(apps, schema_editor):
    """HNSW 인덱스 생성 (halfvec 방식). pgvector 미지원 시 건너뜁니다."""
    if schema_editor.connection.vendor != "postgresql":
        return

    try:
        schema_editor.execute(_CREATE_INDEX_SQL)
        logger.info("book_embeddings HNSW 인덱스 생성 완료.")
    except Exception as exc:
        # pgvector 버전이 halfvec를 지원하지 않거나 다른 이유로 실패한 경우
        # 인덱스 없이 계속 동작합니다 (sequential scan).
        logger.warning(
            "book_embeddings HNSW 인덱스 생성 실패 — pgvector >= 0.6.0 이 필요합니다. "
            "인덱스 없이 계속 동작합니다. 오류: %s",
            exc,
        )


def _remove_vector_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(_DROP_INDEX_SQL)


class Migration(migrations.Migration):
    # CREATE INDEX를 트랜잭션 외부에서 실행하기 위해 atomic=False 설정
    # 실패 시에도 마이그레이션 상태가 오염되지 않도록 예외를 내부에서 처리합니다.
    atomic = False

    dependencies = [
        ("books", "0007_fix_edition_unique"),
    ]

    operations = [
        migrations.RunPython(
            _add_vector_index,
            reverse_code=_remove_vector_index,
        ),
    ]
