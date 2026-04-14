import threading
import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Book

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Book)
def on_book_saved(sender, instance: Book, created: bool, **kwargs):
    """
    도서가 신규 생성될 때 백그라운드로 데이터 수집 파이프라인 실행.

    - created=False(수정)인 경우 즉시 반환 — 임베딩 재생성은 book_edit에서 처리.
    - BookList 단위 중복 방지: run_booklist_pipeline 내부에서 임베딩 존재 여부 확인 후 스킵.
    """
    if not created:
        return

    def _run():
        try:
            from .services import run_book_pipeline
            fresh = Book.objects.select_related(
                "book_list__author", "book_list__publisher"
            ).get(pk=instance.pk)
            logger.info(
                "신규 도서 파이프라인 시작 (book_pk=%s, book_list_pk=%s)",
                instance.pk,
                instance.book_list_id,
            )
            run_book_pipeline(fresh)
        except Exception as exc:
            logger.error("도서 파이프라인 오류 (pk=%s): %s", instance.pk, exc)

    threading.Thread(target=_run, daemon=True).start()
