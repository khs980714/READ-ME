"""
기존 도서 중 thumbnail_url은 있지만 thumbnail 파일이 없는 도서의
이미지를 일괄 다운로드합니다.

사용법:
  # 미저장 도서만 처리
  docker exec read-me-backend-1 python manage.py download_thumbnails

  # 이미 저장된 도서도 포함해서 전부 재다운로드
  docker exec read-me-backend-1 python manage.py download_thumbnails --force
"""

from django.core.management.base import BaseCommand

from books.models import BookList
from books.services import _save_thumbnail


class Command(BaseCommand):
    help = "thumbnail_url이 있고 thumbnail 파일이 없는 도서의 이미지를 일괄 저장합니다."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="이미 저장된 도서도 포함하여 전부 재다운로드합니다.",
        )

    def handle(self, *args, **options):
        force = options["force"]
        qs = BookList.objects.exclude(thumbnail_url="")
        if not force:
            qs = qs.filter(thumbnail="")

        total = qs.count()
        self.stdout.write(f"대상 도서: {total}권")

        ok = fail = 0
        for bl in qs.iterator():
            if _save_thumbnail(bl, bl.thumbnail_url):
                bl.save(update_fields=["thumbnail"])
                ok += 1
                self.stdout.write(f"  ✓ [{bl.pk}] {bl.title}")
            else:
                fail += 1
                self.stdout.write(
                    self.style.WARNING(f"  ✗ [{bl.pk}] {bl.title} — 다운로드 실패")
                )

        self.stdout.write(
            self.style.SUCCESS(f"\n완료: 성공 {ok}권 / 실패 {fail}권 / 전체 {total}권")
        )
