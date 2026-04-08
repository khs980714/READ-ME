import logging
import threading

from django.contrib import messages
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render
from django.db.models import Q
from django.views.decorators.http import require_POST

from .models import Author, Book, BookList, Category, Publisher

logger = logging.getLogger(__name__)


def _staff_required(view_func):
    """관리자(is_staff)가 아니면 도서 목록으로 리다이렉트."""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_staff:
            return redirect("books:list")
        return view_func(request, *args, **kwargs)
    wrapper.__name__ = view_func.__name__
    return wrapper


def book_list(request):
    qs = (
        Book.objects.filter(is_active=True)
        .select_related("book_list__publisher", "book_list__author")
        .prefetch_related("book_list__categories")
    )

    query = request.GET.get("q", "").strip()
    difficulty = request.GET.get("difficulty", "")
    category_id = request.GET.get("category", "")

    if query:
        qs = qs.filter(
            Q(book_list__title__icontains=query)
            | Q(book_list__author__name__icontains=query)
        ).distinct()
    if difficulty:
        qs = qs.filter(book_list__difficulty=difficulty)
    if category_id:
        qs = qs.filter(book_list__categories__id=category_id)

    categories = Category.objects.all()
    difficulties = BookList.Difficulty.choices

    return render(request, "books/list.html", {
        "books": qs,
        "query": query,
        "selected_difficulty": difficulty,
        "selected_category": category_id,
        "categories": categories,
        "difficulties": difficulties,
    })


def book_detail(request, pk):
    book = get_object_or_404(
        Book.objects.select_related("book_list__publisher", "book_list__author")
        .prefetch_related("book_list__categories"),
        pk=pk,
        is_active=True,
    )
    return render(request, "books/detail.html", {"book": book})


# ── 도서 관리 (staff 전용) ─────────────────────────────────────

@_staff_required
def book_manage(request):
    q = request.GET.get("q", "").strip()
    qs = (
        Book.objects.select_related("book_list__publisher", "book_list__author")
        .order_by("book_code")
    )
    if q:
        qs = qs.filter(
            Q(book_list__title__icontains=q)
            | Q(book_list__author__name__icontains=q)
            | Q(book_code__icontains=q)
        ).distinct()
    return render(request, "books/manage.html", {"books": qs, "query": q})


@_staff_required
def book_add(request):
    categories = Category.objects.all()
    difficulties = BookList.Difficulty.choices
    error = None
    form_data = {}

    if request.method == "POST":
        form_data = request.POST
        book_code = request.POST.get("book_code", "").strip()
        title = request.POST.get("title", "").strip()
        author_name = request.POST.get("author", "").strip()
        publisher_name = request.POST.get("publisher", "").strip()
        difficulty = request.POST.get("difficulty", "")
        isbn = request.POST.get("isbn", "").strip()
        thumbnail_url = request.POST.get("thumbnail_url", "").strip()
        description = request.POST.get("description", "").strip()
        published_at = request.POST.get("published_at", "").strip() or None
        page_count = request.POST.get("page_count", "").strip() or None
        category_ids = request.POST.getlist("categories")
        is_active = request.POST.get("is_active") == "on"

        if not all([book_code, title, author_name, publisher_name]):
            error = "도서 코드, 도서명, 저자, 출판사는 필수 항목입니다."
        else:
            try:
                author, _ = Author.objects.get_or_create(name=author_name)
                publisher, _ = Publisher.objects.get_or_create(name=publisher_name)
                book_list, created = BookList.objects.get_or_create(
                    title=title,
                    author=author,
                    publisher=publisher,
                    defaults={
                        "difficulty": difficulty,
                        "isbn": isbn,
                        "thumbnail_url": thumbnail_url,
                        "description": description,
                        "published_at": published_at,
                        "page_count": page_count,
                    },
                )
                if not created:
                    # 같은 (title, author, publisher)가 있으면 수집 정보만 갱신
                    book_list.difficulty = difficulty
                    book_list.isbn = isbn
                    book_list.thumbnail_url = thumbnail_url
                    book_list.description = description
                    book_list.published_at = published_at
                    book_list.page_count = page_count
                    book_list.save()

                book_list.categories.set(Category.objects.filter(id__in=category_ids))
                Book.objects.create(
                    book_code=book_code,
                    book_list=book_list,
                    is_active=is_active,
                )
                messages.success(request, f"도서 [{book_code}] {title} 이(가) 추가되었습니다.")
                return redirect("books:manage")
            except IntegrityError:
                error = f"도서 코드 '{book_code}'는 이미 존재합니다."
            except Exception as e:
                error = str(e)

    return render(request, "books/manage_form.html", {
        "mode": "add",
        "categories": categories,
        "difficulties": difficulties,
        "error": error,
        "form_data": form_data,
    })


@_staff_required
def book_edit(request, pk):
    book = get_object_or_404(
        Book.objects.select_related("book_list__publisher", "book_list__author")
        .prefetch_related("book_list__categories"),
        pk=pk,
    )
    book_list = book.book_list
    categories = Category.objects.all()
    difficulties = BookList.Difficulty.choices
    selected_category_ids = set(book_list.categories.values_list("id", flat=True))
    error = None

    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        author_name = request.POST.get("author", "").strip()
        publisher_name = request.POST.get("publisher", "").strip()
        difficulty = request.POST.get("difficulty", "")
        isbn = request.POST.get("isbn", "").strip()
        thumbnail_url = request.POST.get("thumbnail_url", "").strip()
        description = request.POST.get("description", "").strip()
        published_at = request.POST.get("published_at", "").strip() or None
        page_count = request.POST.get("page_count", "").strip() or None
        category_ids = request.POST.getlist("categories")
        is_active = request.POST.get("is_active") == "on"

        if not all([title, author_name, publisher_name]):
            error = "도서명, 저자, 출판사는 필수 항목입니다."
        else:
            try:
                # 변경 전 값 스냅샷 — 임베딩 재생성 여부 판단용
                prev_title       = book_list.title
                prev_description = book_list.description

                author, _ = Author.objects.get_or_create(name=author_name)
                publisher, _ = Publisher.objects.get_or_create(name=publisher_name)
                book_list.title = title
                book_list.author = author
                book_list.publisher = publisher
                book_list.difficulty = difficulty
                book_list.isbn = isbn
                book_list.thumbnail_url = thumbnail_url
                book_list.description = description
                book_list.published_at = published_at
                book_list.page_count = page_count
                book_list.save()
                book_list.categories.set(Category.objects.filter(id__in=category_ids))
                book.is_active = is_active
                book.save()

                # 제목 또는 설명이 바뀌고 설명이 있으면 임베딩 재생성
                needs_reembed = (
                    (title != prev_title or description != prev_description)
                    and bool(description)
                )
                if needs_reembed:
                    _schedule_embedding_refresh(book_list.pk)
                    messages.success(
                        request,
                        f"도서 [{book.book_code}] {title} 이(가) 수정되었습니다. "
                        "임베딩 재생성이 백그라운드에서 진행됩니다.",
                    )
                else:
                    messages.success(request, f"도서 [{book.book_code}] {title} 이(가) 수정되었습니다.")
                return redirect("books:manage")
            except Exception as e:
                error = str(e)

        selected_category_ids = set(int(i) for i in category_ids if i)

    return render(request, "books/manage_form.html", {
        "mode": "edit",
        "book": book,
        "book_list": book_list,
        "categories": categories,
        "difficulties": difficulties,
        "selected_category_ids": selected_category_ids,
        "error": error,
    })


def _schedule_embedding_refresh(book_list_pk: int) -> None:
    """임베딩 재생성을 백그라운드 스레드로 예약합니다."""
    def _run():
        try:
            from .models import BookList
            from .services import refresh_embedding
            bl = BookList.objects.get(pk=book_list_pk)
            refresh_embedding(bl)
        except Exception as exc:
            logger.error("임베딩 재생성 오류 (book_list_pk=%s): %s", book_list_pk, exc)

    threading.Thread(target=_run, daemon=True).start()


@_staff_required
@require_POST
def book_delete(request, pk):
    book = get_object_or_404(Book, pk=pk)
    book_code = book.book_code
    title = book.book_list.title
    book.delete()
    messages.success(request, f"도서 [{book_code}] {title} 이(가) 삭제되었습니다.")
    return redirect("books:manage")
