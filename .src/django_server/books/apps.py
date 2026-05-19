from django.apps import AppConfig


class BooksConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "books"
    verbose_name = "도서 관리"

    def ready(self):
        import books.signals  # noqa: F401
