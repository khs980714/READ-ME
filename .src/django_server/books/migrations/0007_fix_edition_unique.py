"""
0007 — 판차 unique_together 재설계 + 언어편 복원 + N권 재추출

변경 사항:
  1. unique_together: (title, author, publisher) → (title, edition, author, publisher)
     → 같은 제목이라도 판차가 다르면 별도 BookList로 허용
       예) 기본서 1권 / 기본서 2권 → 각각 독립 BookList

  2. 언어편(자바편·파이썬편 등) 복원
     → 판차가 아닌 도서 고유 구분자이므로 title로 되돌림

  3. N권·이론편 N·기출문제집 등 충돌로 인해 미추출된 도서 재추출
     → 새 충돌 기준: (base_title, new_edition, author, publisher) 조합이 없으면 추출
"""

import re

from django.db import migrations, models


_LANG_EDITIONS = {
    "자바편", "파이썬편", "파이썬3편", "C++편", "C#편", "C언어편",
    "자바스크립트편", "코틀린편", "스위프트편", "러스트편", "Go편",
    "R언어편", "스칼라편", "PHP편", "루비편",
}

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
    r")",
    re.IGNORECASE,
)


def _extract_edition(title: str):
    m = _EDITION_RE.search(title)
    if not m:
        return "", title
    edition = m.group().strip()
    base = (title[: m.start()] + title[m.end() :]).strip()
    return edition, base


def fix_editions(apps, schema_editor):
    BookList = apps.get_model("books", "BookList")

    # ── 1. 언어편 복원 ─────────────────────────────────────────
    for bl in BookList.objects.exclude(edition=""):
        if bl.edition.strip() in _LANG_EDITIONS:
            bl.title = f"{bl.title} {bl.edition}".strip()
            bl.edition = ""
            bl.save(update_fields=["title", "edition"])

    # ── 2. N권 / 이론편 N / 기출 등 — 새 충돌 기준으로 재추출 ──
    # 충돌 기준: (base_title, new_edition, author, publisher) 이 이미 존재하는가?
    for bl in BookList.objects.filter(edition="").order_by("title"):
        edition, base_title = _extract_edition(bl.title)
        if not edition or not base_title:
            continue

        # 새 기준: 같은 (base, 새edition) 조합이 없으면 허용
        conflict = (
            BookList.objects.filter(
                title=base_title,
                edition=edition,
                author_id=bl.author_id,
                publisher_id=bl.publisher_id,
            )
            .exclude(pk=bl.pk)
            .exists()
        )
        if conflict:
            continue

        bl.edition = edition
        bl.title = base_title
        bl.save(update_fields=["title", "edition"])


class Migration(migrations.Migration):

    dependencies = [
        ("books", "0006_reextract_editions"),
    ]

    operations = [
        # 1. 구 unique_together 제거
        migrations.AlterUniqueTogether(
            name="booklist",
            unique_together=set(),
        ),
        # 2. 데이터 수정 (언어편 복원 + N권 재추출)
        migrations.RunPython(fix_editions, migrations.RunPython.noop),
        # 3. 신 unique_together 적용
        migrations.AlterUniqueTogether(
            name="booklist",
            unique_together={("title", "edition", "author", "publisher")},
        ),
    ]
