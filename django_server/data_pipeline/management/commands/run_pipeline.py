"""
python manage.py run_pipeline [--book-id <id>] [--all]

특정 도서 또는 전체 도서에 대해 데이터 수집 파이프라인을 재실행합니다.
(Naver API → 리뷰 스크래핑 → 난이도 분류 → 임베딩 생성)
BookList 단위 중복 방지로 이미 수집된 데이터는 건너뜁니다.
"""

from django.core.management.base import BaseCommand, CommandError

from books.models import Book
from books.services import run_book_pipeline


class Command(BaseCommand):
    help = "도서 데이터 수집 파이프라인 수동 실행"

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--book-id", type=int, help="특정 도서 ID")
        group.add_argument("--all", action="store_true", help="전체 활성 도서 대상 실행")

    def handle(self, *args, **options):
        if options["all"]:
            books = Book.objects.filter(is_active=True).select_related(
                "book_list__author", "book_list__publisher"
            )
            self.stdout.write(f"전체 {books.count()}권 파이프라인 실행 시작...")
            for book in books:
                self.stdout.write(f"  처리 중: [{book.book_code}] {book.book_list.title}")
                try:
                    run_book_pipeline(book)
                    self.stdout.write(self.style.SUCCESS(f"  완료: [{book.book_code}]"))
                except Exception as e:
                    self.stderr.write(self.style.ERROR(f"  오류 [{book.book_code}]: {e}"))
        else:
            book_id = options["book_id"]
            try:
                book = Book.objects.select_related(
                    "book_list__author", "book_list__publisher"
                ).get(pk=book_id)
            except Book.DoesNotExist:
                raise CommandError(f"도서를 찾을 수 없습니다: id={book_id}")
            self.stdout.write(f"파이프라인 실행: [{book.book_code}] {book.book_list.title}")
            run_book_pipeline(book)
            self.stdout.write(self.style.SUCCESS("완료"))
