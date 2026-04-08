"""
0003 — book_embeddings 차원 변경: vector(1024) → vector(2048)

임베딩 모델을 nvidia/nv-embedqa-e5-v5 (dim=1024) 에서
nvidia/llama-3.2-nv-embedqa-1b-v2 (dim=2048) 로 변경함에 따라
book_embeddings 테이블을 재생성합니다.

⚠️  pgvector HNSW/IVFFlat 인덱스는 최대 2000 차원까지만 지원합니다.
    2048 차원 모델 사용 시 인덱스 없이 sequential scan으로 동작합니다.
    도서 수가 많아지면 IVFFlat(lists 설정 필요) 또는 2000차원 이하 모델로 전환을 권장합니다.

⚠️  기존에 저장된 임베딩 데이터는 모두 삭제됩니다.
    마이그레이션 후 run_pipeline --all 을 다시 실행하세요.
"""

from django.db import migrations


def _recreate_book_embeddings_2048(apps, schema_editor):
    """book_embeddings 재생성 — PostgreSQL + pgvector 환경에서만 실행.

    pgvector HNSW 인덱스는 최대 2000차원까지만 지원하므로,
    2048차원 모델 사용 시 인덱스를 생성하지 않습니다.
    """
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("DROP TABLE IF EXISTS book_embeddings;")
    schema_editor.execute("""
        CREATE TABLE book_embeddings (
            id              BIGSERIAL PRIMARY KEY,
            book_list_id    BIGINT NOT NULL UNIQUE
                            REFERENCES book_list(id) ON DELETE CASCADE,
            embedding       vector(2048),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)


class Migration(migrations.Migration):

    dependencies = [
        ("books", "0002_booklist_restructure"),
    ]

    operations = [
        migrations.RunPython(
            _recreate_book_embeddings_2048,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
