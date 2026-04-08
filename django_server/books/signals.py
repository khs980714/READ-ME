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
    BookList 단위 중복 방지 로직은 run_book_pipeline 내부에서 처리됨.
    """
    if not created:
        return

    def _run():
        try:
            from .services import run_book_pipeline
            fresh = Book.objects.select_related(
                "book_list__author", "book_list__publisher"
            ).get(pk=instance.pk)
            run_book_pipeline(fresh)
        except Exception as exc:
            logger.error("도서 파이프라인 오류 (pk=%s): %s", instance.pk, exc)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
