"""
0006 — 판차 패턴 추가 후 기존 데이터 재추출

추가된 패턴:
  - 개정증보판          : 카프카 핵심 가이드 개정증보판
  - N권 (끝)           : 정보처리기사 필기 기본서 1권 / 2권
  - 부록 (끝)          : 정보처리기사 필기 기본서 부록
  - 기출문제집/기출공략 : 시나공 정보처리기사 실기 기출문제집
  - 이론편 N / 실기편 N : 정보보안기사 필기 이론편 1
  - 언어편 (자바편 등) : Do it! 알고리즘 코딩 테스트 자바편

처리 대상: edition = '' (미추출) 도서만 재처리.
충돌 발생 시 title은 유지하고 edition만 저장하지 않음 (full_title 중복 방지).
"""

import re

from django.db import migrations


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
    m = _EDITION_RE.search(title)
    if not m:
        return "", title
    edition = m.group().strip()
    base = (title[: m.start()] + title[m.end() :]).strip()
    return edition, base


def reextract_editions(apps, schema_editor):
    BookList = apps.get_model("books", "BookList")

    # edition이 비어있는 도서만 대상
    for bl in BookList.objects.filter(edition=""):
        edition, base_title = _extract_edition(bl.title)
        if not edition or not base_title:
            continue

        # 충돌 발생 시 저장하지 않음 (title에 이미 edition 포함 → full_title 중복 방지)
        conflict = (
            BookList.objects.filter(
                title=base_title,
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
        ("books", "0005_add_edition_and_year"),
    ]

    operations = [
        migrations.RunPython(reextract_editions, migrations.RunPython.noop),
    ]
