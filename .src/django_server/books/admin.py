from django.contrib import admin
from .models import Publisher, Author, Category, BookList, BookListCategory, Book, BookEmbedding


class BookListCategoryInline(admin.TabularInline):
    model = BookListCategory
    extra = 1


@admin.register(BookList)
class BookListAdmin(admin.ModelAdmin):
    list_display = ("title", "edition", "publication_year", "author", "publisher", "difficulty", "updated_at")
    list_filter = ("difficulty", "publisher", "categories")
    search_fields = ("title", "edition", "author__name")
    readonly_fields = ("created_at", "updated_at")
    inlines = [BookListCategoryInline]
    fieldsets = (
        ("기본 정보", {"fields": ("title", "edition", "publication_year", "author", "publisher")}),
        ("수집 정보", {"fields": ("thumbnail_url", "description", "toc", "difficulty")}),
        ("날짜", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = ("book_code", "get_title", "get_author", "get_publisher", "get_difficulty", "is_active", "updated_at")
    list_filter = ("is_active", "book_list__difficulty", "book_list__publisher")
    search_fields = ("book_code", "book_list__title", "book_list__author__name")
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("book_list",)
    fieldsets = (
        ("기본 정보", {"fields": ("book_code", "book_list")}),
        ("설정", {"fields": ("is_active", "created_at", "updated_at")}),
    )

    def get_title(self, obj):
        return obj.book_list.title
    get_title.short_description = "도서명"
    get_title.admin_order_field = "book_list__title"

    def get_author(self, obj):
        return obj.book_list.author.name
    get_author.short_description = "저자"
    get_author.admin_order_field = "book_list__author__name"

    def get_publisher(self, obj):
        return obj.book_list.publisher.name
    get_publisher.short_description = "출판사"
    get_publisher.admin_order_field = "book_list__publisher__name"

    def get_difficulty(self, obj):
        return obj.book_list.difficulty
    get_difficulty.short_description = "난이도"
    get_difficulty.admin_order_field = "book_list__difficulty"


@admin.register(Publisher)
class PublisherAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)


@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "description")
    search_fields = ("name",)


@admin.register(BookEmbedding)
class BookEmbeddingAdmin(admin.ModelAdmin):
    list_display = ("book_list", "created_at", "updated_at")
    readonly_fields = ("book_list", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
