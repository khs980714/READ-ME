"""
0010 — ISBN 필드 제거

BookList 테이블에서 isbn 컬럼을 삭제합니다.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("books", "0009_additional_indexes"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="booklist",
            name="isbn",
        ),
    ]
