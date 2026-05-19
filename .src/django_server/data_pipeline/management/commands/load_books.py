"""
python manage.py load_books

data.csv를 읽어 Publisher, Author, BookList, Book을 생성합니다.
- 동일한 (title, author, publisher) 조합의 BookList는 중복 생성하지 않습니다.
- 중복 book_code는 건너뜁니다.
"""

import csv
import os
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models.signals import post_save

from books.models import Author, Book, BookList, Publisher
from books.services import extract_edition_info
from books.signals import on_book_saved

CSV_PATH = Path(__file__).resolve().parents[3] / "data.csv"


class Command(BaseCommand):
    help = "data.csv 기반 초기 도서 데이터 적재"

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv",
            type=str,
            default=str(CSV_PATH),
            help="CSV 파일 경로 (기본값: 루트 data.csv)",
        )

    def handle(self, *args, **options):
        csv_path = options["csv"]
        if not os.path.exists(csv_path):
            self.stderr.write(self.style.ERROR(f"파일을 찾을 수 없습니다: {csv_path}"))
            return

        created_count = 0
        skipped_count = 0

        # 초기 적재 중 파이프라인 시그널 비활성화 (DB 커넥션 폭증 방지)
        post_save.disconnect(on_book_saved, sender=Book)
        try:
            with transaction.atomic():
                with open(csv_path, newline="", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        book_code = row["번호"].strip()
                        title = row["도서명"].strip()
                        raw_authors = row["저자"].strip()
                        publisher_name = row["출판사"].strip()

                        if not book_code or not title:
                            continue

                        if Book.objects.filter(book_code=book_code).exists():
                            skipped_count += 1
                            continue

                        publisher, _ = Publisher.objects.get_or_create(name=publisher_name)

                        # 첫 번째 저자를 대표 저자로 사용
                        author_names = [a.strip() for a in raw_authors.split(",") if a.strip()]
                        primary_name = author_names[0] if author_names else "미상"
                        author, _ = Author.objects.get_or_create(name=primary_name)

                        # 제목에서 판차 분리
                        edition, base_title = extract_edition_info(title)

                        # BookList 중복 방지: (title, edition, author, publisher) 조합 확인
                        book_list, _ = BookList.objects.get_or_create(
                            title=base_title,
                            edition=edition,
                            author=author,
                            publisher=publisher,
                        )

                        Book.objects.create(book_code=book_code, book_list=book_list)

                        created_count += 1
                        display_title = book_list.full_title
                        self.stdout.write(f"  생성: [{book_code}] {display_title}")
        finally:
            post_save.connect(on_book_saved, sender=Book)

        self.stdout.write(
            self.style.SUCCESS(f"\n완료 — 생성: {created_count}건, 건너뜀: {skipped_count}건")
        )
        self.stdout.write("파이프라인을 실행하려면: python manage.py run_pipeline --all")
