import logging
import threading

from django.contrib import messages
from django.core.cache import cache
from django.db import IntegrityError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.db.models import Q
from django.views.decorators.http import require_POST

from .models import Author, Book, BookList, Category, Publisher

# 카테고리 목록 캐시 TTL (초) — 카테고리는 거의 변하지 않으므로 10분 캐시
_CATEGORY_CACHE_KEY = "books:all_categories"
_CATEGORY_CACHE_TTL = 600

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
            | Q(book_list__edition__icontains=query)
            | Q(book_list__author__name__icontains=query)
        ).distinct()
    if difficulty == "미분류":
        qs = qs.filter(book_list__difficulty="")
    elif difficulty:
        qs = qs.filter(book_list__difficulty=difficulty)
    if category_id:
        qs = qs.filter(book_list__categories__id=category_id)

    categories = cache.get(_CATEGORY_CACHE_KEY)
    if categories is None:
        categories = list(Category.objects.all())
        cache.set(_CATEGORY_CACHE_KEY, categories, _CATEGORY_CACHE_TTL)
    difficulties = BookList.Difficulty.choices

    # 검색 결과 없을 때 카테고리 제안 목록 제공
    suggested_categories = []
    if not qs.exists() and (query or difficulty or category_id):
        suggested_categories = categories[:8]

    return render(request, "books/list.html", {
        "books": qs,
        "query": query,
        "selected_difficulty": difficulty,
        "selected_category": category_id,
        "categories": categories,
        "difficulties": difficulties,
        "suggested_categories": suggested_categories,
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
            | Q(book_list__edition__icontains=q)
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
        subtitle = request.POST.get("subtitle", "").strip()
        edition = request.POST.get("edition", "").strip()
        author_name = request.POST.get("author", "").strip()
        publisher_name = request.POST.get("publisher", "").strip()
        difficulty = request.POST.get("difficulty", "")
        pub_year_str = request.POST.get("publication_year", "").strip()
        publication_year = int(pub_year_str) if pub_year_str.isdigit() else None
        thumbnail_url = request.POST.get("thumbnail_url", "").strip()
        description = request.POST.get("description", "").strip()
        toc = request.POST.get("toc", "").strip()
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
                    edition=edition,
                    author=author,
                    publisher=publisher,
                    defaults={
                        "subtitle": subtitle,
                        "difficulty": difficulty,
                        "publication_year": publication_year,
                        "thumbnail_url": thumbnail_url,
                        "description": description,
                        "toc": toc,
                    },
                )
                if not created:
                    # 같은 (title, edition, author, publisher)가 있으면 수집 정보만 갱신
                    book_list.subtitle = subtitle
                    book_list.edition = edition
                    book_list.difficulty = difficulty
                    book_list.publication_year = publication_year
                    book_list.thumbnail_url = thumbnail_url
                    book_list.description = description
                    book_list.toc = toc
                    book_list.save()

                book_list.categories.set(Category.objects.filter(id__in=category_ids))
                Book.objects.create(
                    book_code=book_code,
                    book_list=book_list,
                    is_active=is_active,
                )
                # 썸네일 URL이 있으면 즉시 다운로드
                if thumbnail_url and not book_list.thumbnail:
                    from .services import _save_thumbnail
                    if _save_thumbnail(book_list, thumbnail_url):
                        book_list.save(update_fields=["thumbnail"])
                messages.success(request, f"도서 [{book_code}] {title} 이(가) 추가되었습니다.")
                return redirect("books:manage")
            except IntegrityError:
                error = f"도서 코드 '{book_code}'는 이미 존재합니다."
            except Exception as e:
                error = str(e)

    return render(request, "books/manage_form.html", {
        "mode": "add",
        "book_list": None,
        "selected_category_ids": set(),
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
    form_data = {
        "title": book_list.title,
        "subtitle": book_list.subtitle,
        "edition": book_list.edition,
        "author": book_list.author.name,
        "publisher": book_list.publisher.name,
        "difficulty": book_list.difficulty,
        "publication_year": book_list.publication_year or "",
        "thumbnail_url": book_list.thumbnail_url,
        "description": book_list.description,
        "toc": book_list.toc,
    }

    if request.method == "POST":
        form_data = request.POST
        title = request.POST.get("title", "").strip()
        subtitle = request.POST.get("subtitle", "").strip()
        edition = request.POST.get("edition", "").strip()
        author_name = request.POST.get("author", "").strip()
        publisher_name = request.POST.get("publisher", "").strip()
        difficulty = request.POST.get("difficulty", "")
        pub_year_str = request.POST.get("publication_year", "").strip()
        publication_year = int(pub_year_str) if pub_year_str.isdigit() else None
        thumbnail_url = request.POST.get("thumbnail_url", "").strip()
        description = request.POST.get("description", "").strip()
        toc = request.POST.get("toc", "").strip()
        category_ids = request.POST.getlist("categories")
        is_active = request.POST.get("is_active") == "on"

        if not all([title, author_name, publisher_name]):
            error = "도서명, 저자, 출판사는 필수 항목입니다."
        else:
            try:
                from .services import _save_thumbnail
                # 변경 전 값 스냅샷 — 임베딩 재생성·썸네일 갱신 여부 판단용
                prev_title         = book_list.title
                prev_description   = book_list.description
                prev_thumbnail_url = book_list.thumbnail_url

                author, _ = Author.objects.get_or_create(name=author_name)
                publisher, _ = Publisher.objects.get_or_create(name=publisher_name)
                book_list.title = title
                book_list.subtitle = subtitle
                book_list.edition = edition
                book_list.author = author
                book_list.publisher = publisher
                book_list.difficulty = difficulty
                book_list.publication_year = publication_year
                book_list.thumbnail_url = thumbnail_url
                book_list.description = description
                book_list.toc = toc
                book_list.save()
                book_list.categories.set(Category.objects.filter(id__in=category_ids))
                book.is_active = is_active
                book.save()

                # 썸네일 URL이 변경됐으면 기존 파일 삭제 후 재다운로드
                if thumbnail_url and thumbnail_url != prev_thumbnail_url:
                    if book_list.thumbnail:
                        book_list.thumbnail.delete(save=False)
                    if _save_thumbnail(book_list, thumbnail_url):
                        book_list.save(update_fields=["thumbnail"])

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
        "form_data": form_data,
        "categories": categories,
        "difficulties": difficulties,
        "selected_category_ids": selected_category_ids,
        "error": error,
    })


# 동시에 동일 book_list에 대한 임베딩 재생성이 중복 실행되는 것을 방지합니다.
_embedding_in_progress: set[int] = set()
_embedding_lock = threading.Lock()


def _schedule_embedding_refresh(book_list_pk: int) -> None:
    """임베딩 재생성을 백그라운드 스레드로 예약합니다.

    동일 book_list_pk에 대한 재생성이 이미 진행 중이면 중복 실행을 생략합니다.
    """
    with _embedding_lock:
        if book_list_pk in _embedding_in_progress:
            logger.info("임베딩 재생성 이미 진행 중 — 생략 (book_list_pk=%s)", book_list_pk)
            return
        _embedding_in_progress.add(book_list_pk)

    def _run():
        try:
            from .models import BookList
            from .services import refresh_embedding
            bl = BookList.objects.get(pk=book_list_pk)
            refresh_embedding(bl)
        except Exception as exc:
            logger.error("임베딩 재생성 오류 (book_list_pk=%s): %s", book_list_pk, exc)
        finally:
            with _embedding_lock:
                _embedding_in_progress.discard(book_list_pk)

    threading.Thread(target=_run, daemon=True).start()


@_staff_required
@require_POST
def book_collect(request, pk):
    """개별 도서 데이터 수집 (AJAX). force=true이면 기존 데이터 덮어쓰기."""
    book = get_object_or_404(Book, pk=pk)
    import json
    try:
        body = json.loads(request.body or "{}")
    except Exception:
        body = {}
    force = body.get("force", False)

    try:
        from .services import collect_book_data
        result = collect_book_data(book.book_list, force=force)
        return JsonResponse({"ok": True, **result})
    except Exception as exc:
        logger.error("book_collect 오류 (pk=%s): %s", pk, exc)
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)


@_staff_required
@require_POST
def book_delete(request, pk):
    book = get_object_or_404(Book, pk=pk)
    book_code = book.book_code
    title = book.book_list.title
    book.delete()
    messages.success(request, f"도서 [{book_code}] {title} 이(가) 삭제되었습니다.")
    return redirect("books:manage")
