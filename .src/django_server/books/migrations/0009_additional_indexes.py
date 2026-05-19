"""
0009 — 성능 인덱스 보완

자주 사용되는 쿼리 패턴에 대한 인덱스를 추가합니다:
- book_list(title): 도서 제목 검색 (ILIKE 쿼리는 pg_trgm 없이는 느리므로 prefix 검색에 활용)
- books(book_list_id, is_active): 활성 도서 필터 조회 (복합 인덱스)
- chat_messages(session_id, created_at): 채팅 히스토리 조회
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("books", "0008_book_embeddings_vector_index"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                # book_list 제목 검색 인덱스 (대소문자 무관 검색용)
                "CREATE INDEX IF NOT EXISTS idx_book_list_title "
                "ON book_list (title);",
                # books 활성 도서 필터 조회 복합 인덱스
                "CREATE INDEX IF NOT EXISTS idx_books_booklist_active "
                "ON books (book_list_id, is_active);",
                # chat_messages 히스토리 조회 인덱스
                "CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created "
                "ON chat_messages (session_id, created_at);",
            ],
            reverse_sql=[
                "DROP INDEX IF EXISTS idx_book_list_title;",
                "DROP INDEX IF EXISTS idx_books_booklist_active;",
                "DROP INDEX IF EXISTS idx_chat_messages_session_created;",
            ],
        ),
    ]
