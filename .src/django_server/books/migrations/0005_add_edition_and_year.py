"""
0005 — book_list 테이블 필드 추가 + 기존 데이터 판차 추출
- edition  (CharField, 판차 정보)
- publication_year (PositiveSmallIntegerField, 출판 연도)
- 기존 도서 제목에서 판차 패턴 추출 후 edition 컬럼에 저장,
  충돌이 없으면 title에서 판차 부분을 제거합니다.
"""

import re

from django.db import migrations, models


# ── 판차 추출 패턴 (마이그레이션 내 독립 정의) ─────────────────────
_EDITION_RE = re.compile(
    r"(?:"
    r"\s*[\(\[（［][^\)\]）］]*?(?:판|편|개정|증보|완전개정|전면개정)\w*[\)\]）］]"
    r"|\s+개정증보판"
    r"|\s+(?:전면개정|완전개정|개정)\d*판"
    r"|\s+제\d+판"
    r"|\s+\d+판\b"
    r"|\s+\d+권$"
    r"|\s+부록$"
    r"|\s+기출(?:문제집|공략)$"
    r"|\s+(?:이론|실기)편\s+\d+$"
    r"|\s+(?:자바|파이썬|파이썬3|C\+\+|C#|C언어|자바스크립트|코틀린|스위프트|러스트|Go|R언어|스칼라|PHP|루비)편$"
    r")",
    re.IGNORECASE,
)


def _extract_edition(title: str):
    """(edition_str, base_title) 반환. 없으면 ('', title)."""
    m = _EDITION_RE.search(title)
    if not m:
        return "", title
    edition = m.group().strip()
    base = (title[: m.start()] + title[m.end() :]).strip()
    return edition, base


def migrate_extract_editions(apps, schema_editor):
    BookList = apps.get_model("books", "BookList")

    for bl in BookList.objects.all():
        edition, base_title = _extract_edition(bl.title)
        if not edition:
            continue

        if base_title:
            conflict = (
                BookList.objects.filter(
                    title=base_title,
                    author_id=bl.author_id,
                    publisher_id=bl.publisher_id,
                )
                .exclude(pk=bl.pk)
                .exists()
            )
            if not conflict:
                bl.edition = edition
                bl.title = base_title
                bl.save(update_fields=["title", "edition"])
                continue

        # 충돌 발생 시: 제목은 유지하고 edition만 저장
        bl.edition = edition
        bl.save(update_fields=["edition"])


class Migration(migrations.Migration):

    dependencies = [
        ("books", "0004_update_book_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="booklist",
            name="edition",
            field=models.CharField(
                blank=True,
                help_text="예: (개정2판), (심화편) — 제목에서 분리된 판차 정보",
                max_length=200,
                verbose_name="판차",
            ),
        ),
        migrations.AddField(
            model_name="booklist",
            name="publication_year",
            field=models.PositiveSmallIntegerField(
                blank=True,
                null=True,
                verbose_name="출판 연도",
                help_text="제목 또는 설명에서 추출된 4자리 연도",
            ),
        ),
        migrations.RunPython(migrate_extract_editions, migrations.RunPython.noop),
    ]
