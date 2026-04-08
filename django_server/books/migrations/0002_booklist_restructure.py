"""
0002 — BookList 분리 및 books 테이블 슬림화

변경 내용:
- book_list 테이블 신규 생성 (title, author FK, publisher FK, description, difficulty, ...)
- book_list_categories M:N through 테이블 신규 생성
- books 테이블에 book_list_id FK 추가 (nullable → 데이터 이관 → non-nullable)
- 기존 books 컬럼 제거: title, publisher, isbn, thumbnail_url, description, difficulty, published_at, page_count
- BookAuthor, BookCategory 모델/테이블 제거
- book_embeddings 테이블 재생성 (book_id → book_list_id)
"""

import django.db.models.deletion
from django.db import migrations, models


# ── 헬퍼 함수 ────────────────────────────────────────────────────

def _recreate_book_embeddings(apps, schema_editor):
    """book_embeddings 재생성 — PostgreSQL + pgvector 환경에서만 실행."""
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("DROP TABLE IF EXISTS book_embeddings;")
    schema_editor.execute("""
        CREATE TABLE IF NOT EXISTS book_embeddings (
            id              BIGSERIAL PRIMARY KEY,
            book_list_id    BIGINT NOT NULL UNIQUE
                            REFERENCES book_list(id) ON DELETE CASCADE,
            embedding       vector(1024),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    schema_editor.execute("""
        CREATE INDEX IF NOT EXISTS book_embeddings_hnsw_idx
            ON book_embeddings USING hnsw (embedding vector_cosine_ops);
    """)


def populate_book_list(apps, schema_editor):
    """기존 books 데이터를 book_list로 이관."""
    Book = apps.get_model("books", "Book")
    BookList = apps.get_model("books", "BookList")
    BookAuthor = apps.get_model("books", "BookAuthor")
    BookCategory = apps.get_model("books", "BookCategory")
    BookListCategory = apps.get_model("books", "BookListCategory")
    Author = apps.get_model("books", "Author")

    # 저자 없는 도서에 사용할 기본 저자
    unknown_author, _ = Author.objects.get_or_create(name="미상")

    for book in Book.objects.select_related("publisher").all():
        first_rel = BookAuthor.objects.filter(book=book).order_by("author_order").first()
        author = first_rel.author if first_rel else unknown_author

        book_list, _ = BookList.objects.get_or_create(
            title=book.title,
            author=author,
            publisher=book.publisher,
            defaults={
                "description": book.description,
                "difficulty": book.difficulty,
                "isbn": book.isbn,
                "thumbnail_url": book.thumbnail_url,
                "published_at": book.published_at,
                "page_count": book.page_count,
            },
        )

        # FK 연결
        Book.objects.filter(pk=book.pk).update(book_list=book_list)

        # 카테고리 이관
        for bc in BookCategory.objects.filter(book=book):
            BookListCategory.objects.get_or_create(
                book_list=book_list, category=bc.category
            )


class Migration(migrations.Migration):

    dependencies = [
        ("books", "0001_initial"),
    ]

    operations = [
        # ── 1. BookList 테이블 생성 ──────────────────────────────
        migrations.CreateModel(
            name="BookList",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=500, verbose_name="도서명")),
                ("description", models.TextField(blank=True, verbose_name="도서 소개")),
                ("difficulty", models.CharField(
                    blank=True,
                    choices=[("입문", "입문"), ("초급", "초급"), ("중급", "중급"), ("고급", "고급")],
                    max_length=10,
                    verbose_name="난이도",
                )),
                ("isbn", models.CharField(blank=True, max_length=20, verbose_name="ISBN")),
                ("thumbnail_url", models.URLField(blank=True, max_length=500, verbose_name="썸네일 URL")),
                ("published_at", models.DateField(blank=True, null=True, verbose_name="출판일")),
                ("page_count", models.PositiveIntegerField(blank=True, null=True, verbose_name="페이지 수")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("author", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="book_lists",
                    to="books.author",
                    verbose_name="저자",
                )),
                ("publisher", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="book_lists",
                    to="books.publisher",
                    verbose_name="출판사",
                )),
            ],
            options={
                "verbose_name": "도서 정보",
                "verbose_name_plural": "도서 정보",
                "db_table": "book_list",
                "ordering": ["title"],
            },
        ),
        migrations.AlterUniqueTogether(
            name="booklist",
            unique_together={("title", "author", "publisher")},
        ),

        # ── 2. BookListCategory M:N 테이블 생성 ──────────────────
        migrations.CreateModel(
            name="BookListCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("book_list", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="books.booklist")),
                ("category", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="books.category")),
            ],
            options={
                "verbose_name": "도서정보-카테고리",
                "verbose_name_plural": "도서정보-카테고리",
                "db_table": "book_list_categories",
                "unique_together": {("book_list", "category")},
            },
        ),
        migrations.AddField(
            model_name="booklist",
            name="categories",
            field=models.ManyToManyField(
                blank=True,
                related_name="book_lists",
                through="books.BookListCategory",
                to="books.category",
                verbose_name="카테고리",
            ),
        ),

        # ── 3. books 에 nullable book_list FK 추가 ───────────────
        migrations.AddField(
            model_name="book",
            name="book_list",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="books",
                to="books.booklist",
                verbose_name="도서 정보",
            ),
        ),

        # ── 4. 데이터 이관 ────────────────────────────────────────
        migrations.RunPython(populate_book_list, reverse_code=migrations.RunPython.noop),

        # ── 5. book_list FK non-nullable로 변경 ──────────────────
        migrations.AlterField(
            model_name="book",
            name="book_list",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="books",
                to="books.booklist",
                verbose_name="도서 정보",
            ),
        ),

        # ── 6. books 의 기존 M:N / FK 필드 제거 ─────────────────
        migrations.RemoveField(model_name="book", name="authors"),
        migrations.RemoveField(model_name="book", name="categories"),

        # ── 7. BookAuthor / BookCategory 모델 제거 ───────────────
        migrations.DeleteModel(name="BookAuthor"),
        migrations.DeleteModel(name="BookCategory"),

        # ── 8. books 의 기존 컬럼 제거 ───────────────────────────
        migrations.RemoveField(model_name="book", name="title"),
        migrations.RemoveField(model_name="book", name="publisher"),
        migrations.RemoveField(model_name="book", name="isbn"),
        migrations.RemoveField(model_name="book", name="thumbnail_url"),
        migrations.RemoveField(model_name="book", name="description"),
        migrations.RemoveField(model_name="book", name="difficulty"),
        migrations.RemoveField(model_name="book", name="published_at"),
        migrations.RemoveField(model_name="book", name="page_count"),

        # ── 9. book_embeddings 재생성 (book_id → book_list_id) ───
        # PostgreSQL + pgvector 환경에서만 실행
        migrations.RunPython(
            _recreate_book_embeddings,
            reverse_code=migrations.RunPython.noop,
        ),

        # ── 10. BookEmbedding 모델 상태 동기화 (managed=False) ───
        # 0001_initial 에서 book FK는 migration state에 포함되지 않았으므로
        # book_list FK만 state에 추가합니다 (DB는 이미 RunSQL에서 처리).
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="bookembedding",
                    name="book_list",
                    field=models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="embedding",
                        to="books.booklist",
                        verbose_name="도서 정보",
                    ),
                ),
            ],
            database_operations=[],  # 이미 RunSQL에서 처리
        ),
    ]
