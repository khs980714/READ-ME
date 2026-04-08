from django.db import models


class Publisher(models.Model):
    name = models.CharField(max_length=255, unique=True, verbose_name="출판사명")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "publishers"
        verbose_name = "출판사"
        verbose_name_plural = "출판사"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Author(models.Model):
    name = models.CharField(max_length=255, unique=True, verbose_name="저자명")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "authors"
        verbose_name = "저자"
        verbose_name_plural = "저자"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True, verbose_name="카테고리명")
    description = models.TextField(blank=True, verbose_name="설명")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "categories"
        verbose_name = "카테고리"
        verbose_name_plural = "카테고리"
        ordering = ["name"]

    def __str__(self):
        return self.name


class BookList(models.Model):
    """
    도서 정보 마스터 테이블.
    (title, author, publisher) 조합이 동일하면 중복으로 간주하여 데이터 수집을 건너뜁니다.
    """

    class Difficulty(models.TextChoices):
        BEGINNER = "입문", "입문"
        ELEMENTARY = "초급", "초급"
        INTERMEDIATE = "중급", "중급"
        ADVANCED = "고급", "고급"

    title = models.CharField(max_length=500, verbose_name="도서명")
    author = models.ForeignKey(
        Author,
        on_delete=models.PROTECT,
        related_name="book_lists",
        verbose_name="저자",
    )
    publisher = models.ForeignKey(
        Publisher,
        on_delete=models.PROTECT,
        related_name="book_lists",
        verbose_name="출판사",
    )
    description = models.TextField(blank=True, verbose_name="도서 소개")
    difficulty = models.CharField(
        max_length=10, choices=Difficulty.choices, blank=True, verbose_name="난이도"
    )
    isbn = models.CharField(max_length=20, blank=True, verbose_name="ISBN")
    thumbnail_url = models.URLField(max_length=500, blank=True, verbose_name="썸네일 URL")
    published_at = models.DateField(null=True, blank=True, verbose_name="출판일")
    page_count = models.PositiveIntegerField(null=True, blank=True, verbose_name="페이지 수")
    categories = models.ManyToManyField(
        Category,
        through="BookListCategory",
        related_name="book_lists",
        verbose_name="카테고리",
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "book_list"
        verbose_name = "도서 정보"
        verbose_name_plural = "도서 정보"
        ordering = ["title"]
        unique_together = (("title", "author", "publisher"),)

    def __str__(self):
        return f"{self.title} ({self.author.name})"

    def get_author_display(self):
        return self.author.name


class BookListCategory(models.Model):
    book_list = models.ForeignKey(BookList, on_delete=models.CASCADE)
    category = models.ForeignKey(Category, on_delete=models.PROTECT)

    class Meta:
        db_table = "book_list_categories"
        unique_together = ("book_list", "category")
        verbose_name = "도서정보-카테고리"
        verbose_name_plural = "도서정보-카테고리"


class Book(models.Model):
    """
    도서 코드(book_code) + BookList FK.
    동일한 책의 여러 edition/코드를 하나의 BookList에 연결합니다.
    """

    book_code = models.CharField(
        max_length=20, unique=True, verbose_name="도서 코드", help_text="D-246 형식"
    )
    book_list = models.ForeignKey(
        BookList,
        on_delete=models.CASCADE,
        related_name="books",
        verbose_name="도서 정보",
    )
    is_active = models.BooleanField(default=True, verbose_name="활성화")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "books"
        verbose_name = "도서"
        verbose_name_plural = "도서"
        ordering = ["book_code"]

    def __str__(self):
        return f"[{self.book_code}] {self.book_list.title}"

    def get_author_display(self):
        return self.book_list.author.name

    # ── 하위 호환 프록시 프로퍼티 (템플릿/기존 코드 호환) ──────────
    @property
    def title(self):
        return self.book_list.title

    @property
    def publisher(self):
        return self.book_list.publisher

    @property
    def description(self):
        return self.book_list.description

    @property
    def difficulty(self):
        return self.book_list.difficulty

    @property
    def thumbnail_url(self):
        return self.book_list.thumbnail_url

    @property
    def isbn(self):
        return self.book_list.isbn

    @property
    def published_at(self):
        return self.book_list.published_at

    @property
    def page_count(self):
        return self.book_list.page_count

    @property
    def categories(self):
        return self.book_list.categories


class BookEmbedding(models.Model):
    """
    book_embeddings 테이블은 마이그레이션의 RunSQL로 생성됨 (pgvector vector(1024) 포함).
    book_list 단위로 임베딩을 저장하여 중복 임베딩 생성을 방지합니다.
    Django는 ORM 상태만 관리 (managed=False). 실제 임베딩 연산은 FastAPI에서 처리.
    """

    book_list = models.OneToOneField(
        BookList,
        on_delete=models.CASCADE,
        related_name="embedding",
        verbose_name="도서 정보",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = "book_embeddings"
        verbose_name = "도서 임베딩"
        verbose_name_plural = "도서 임베딩"

    def __str__(self):
        return f"Embedding({self.book_list.title})"
