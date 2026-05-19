"""
0004 — book_list 테이블 필드 변경
- published_at (DateField) 제거
- page_count (PositiveIntegerField) 제거
- toc (TextField) 추가 — 도서 목차 저장용
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("books", "0003_embedding_dim_2048"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="booklist",
            name="published_at",
        ),
        migrations.RemoveField(
            model_name="booklist",
            name="page_count",
        ),
        migrations.AddField(
            model_name="booklist",
            name="toc",
            field=models.TextField(blank=True, verbose_name="목차"),
        ),
    ]
