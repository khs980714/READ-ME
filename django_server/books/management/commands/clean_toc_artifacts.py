"""Management command: clean_toc_artifacts

YES24 스크래핑 시 목차에 남아있는 UI 잔재(「접어보기」 등)를 DB에서 정리합니다.
"""
from django.core.management.base import BaseCommand

from books.models import BookList
from books.services import clean_toc_artifacts


class Command(BaseCommand):
    help = "BookList.toc 에 남아있는 '접어보기' 등 UI 잔재를 제거합니다."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="실제로 저장하지 않고 대상 목록만 출력합니다.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        qs = BookList.objects.exclude(toc="").exclude(toc__isnull=True)
        updated = 0

        for bl in qs.iterator():
            cleaned = clean_toc_artifacts(bl.toc)
            if cleaned != bl.toc:
                if dry_run:
                    self.stdout.write(f"[DRY-RUN] pk={bl.pk} «{bl.title}»")
                else:
                    bl.toc = cleaned
                    bl.save(update_fields=["toc"])
                updated += 1

        label = "대상" if dry_run else "수정"
        self.stdout.write(self.style.SUCCESS(f"완료: {updated}건 {label}됨"))
