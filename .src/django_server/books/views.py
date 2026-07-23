import logging
import threading

from django.contrib import messages
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.db.models import Q
from django.views.decorators.http import require_POST
from django.contrib.admin.views.decorators import staff_member_required

from django.db.models import Count

from .models import Author, Book, BookList, BookListCategory, Category, Publisher

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
            | Q(book_code__icontains=query)
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

    return render(request, "books/manage.html", {
        "books": qs,
        "query": q,
    })


@_staff_required
def book_add(request):
    categories = Category.objects.all()
    difficulties = BookList.Difficulty.choices
    error = None
    form_data = {}
    duplicate_book_list = None
    duplicate_books = []

    if request.method == "POST":
        from .services import parse_book_codes

        form_data = request.POST
        book_code_raw = request.POST.get("book_code", "").strip()
        book_codes = parse_book_codes(book_code_raw)
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
        confirm_link = request.POST.get("confirm_link") == "true"

        if not book_codes or not all([title, author_name, publisher_name]):
            error = "도서 코드, 도서명, 저자, 출판사는 필수 항목입니다."
        else:
            existing_codes = list(
                Book.objects.filter(book_code__in=book_codes).values_list("book_code", flat=True)
            )
            if existing_codes:
                error = f"이미 존재하는 도서 코드입니다: {', '.join(existing_codes)}"
            else:
                try:
                    author, _ = Author.objects.get_or_create(name=author_name)
                    publisher, _ = Publisher.objects.get_or_create(name=publisher_name)

                    # 동일한 (title, edition, author, publisher) BookList 존재 여부 확인
                    try:
                        existing_bl = BookList.objects.get(
                            title=title, edition=edition, author=author, publisher=publisher
                        )
                        # 중복 BookList 발견
                        if not confirm_link:
                            duplicate_book_list = existing_bl
                            duplicate_books = list(existing_bl.books.all())
                        else:
                            # 사용자가 확인: 기존 BookList에 새 Book(들) 연결
                            with transaction.atomic():
                                for code in book_codes:
                                    Book.objects.create(
                                        book_code=code,
                                        book_list=existing_bl,
                                        is_active=is_active,
                                    )
                            messages.success(
                                request,
                                f"도서 [{', '.join(book_codes)}] {title} 이(가) 기존 도서로 연결되어 추가되었습니다.",
                            )
                            return redirect("books:manage")
                    except BookList.DoesNotExist:
                        # 중복 없음 — 신규 BookList 생성 후 코드별 Book 연결
                        with transaction.atomic():
                            book_list = BookList.objects.create(
                                title=title,
                                subtitle=subtitle,
                                edition=edition,
                                author=author,
                                publisher=publisher,
                                difficulty=difficulty,
                                publication_year=publication_year,
                                thumbnail_url=thumbnail_url,
                                description=description,
                                toc=toc,
                            )
                            book_list.categories.set(Category.objects.filter(id__in=category_ids))
                            for code in book_codes:
                                Book.objects.create(
                                    book_code=code,
                                    book_list=book_list,
                                    is_active=is_active,
                                )
                        if thumbnail_url and not book_list.thumbnail:
                            from .services import _save_thumbnail
                            if _save_thumbnail(book_list, thumbnail_url):
                                book_list.save(update_fields=["thumbnail"])
                        messages.success(request, f"도서 [{', '.join(book_codes)}] {title} 이(가) 추가되었습니다.")
                        return redirect("books:manage")

                except IntegrityError:
                    error = f"도서 코드 처리 중 중복 오류가 발생했습니다: {', '.join(book_codes)}"
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
        "duplicate_book_list": duplicate_book_list,
        "duplicate_books": duplicate_books,
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
    duplicate_book_list = None
    duplicate_books = []
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
        confirm_link = request.POST.get("confirm_link") == "true"

        if not all([title, author_name, publisher_name]):
            error = "도서명, 저자, 출판사는 필수 항목입니다."
        else:
            try:
                from .services import _save_thumbnail
                prev_title         = book_list.title
                prev_description   = book_list.description
                prev_thumbnail_url = book_list.thumbnail_url

                author, _ = Author.objects.get_or_create(name=author_name)
                publisher, _ = Publisher.objects.get_or_create(name=publisher_name)

                # 동일한 (title, edition, author, publisher) 를 가진 다른 BookList 존재 여부 확인
                duplicate_found = False
                try:
                    existing_bl = BookList.objects.get(
                        title=title, edition=edition, author=author, publisher=publisher
                    )
                    if existing_bl.pk != book_list.pk:
                        duplicate_found = True
                        if not confirm_link:
                            # 중복 경고 표시
                            duplicate_book_list = existing_bl
                            duplicate_books = list(existing_bl.books.all())
                            selected_category_ids = set(int(i) for i in category_ids if i)
                        else:
                            # 기존 BookList로 re-link
                            old_book_list = book.book_list
                            book.book_list = existing_bl
                            book.is_active = is_active
                            book.save()
                            existing_bl.categories.set(Category.objects.filter(id__in=category_ids))
                            # 기존 BookList에 더 이상 Book이 없으면 삭제
                            if not old_book_list.books.exists():
                                old_book_list.delete()
                            messages.success(
                                request,
                                f"도서 [{book.book_code}]이(가) 기존 도서 '{existing_bl.full_title}'로 연결되었습니다.",
                            )
                            return redirect("books:manage")
                except BookList.DoesNotExist:
                    pass

                if not duplicate_found:
                    # 중복 없음 — 정상 저장
                    # 썸네일: model.save() 전에 처리해 파일 경로를 한 번의 save()로 함께 저장
                    if thumbnail_url and thumbnail_url != prev_thumbnail_url:
                        if book_list.thumbnail:
                            book_list.thumbnail.delete(save=False)
                        _save_thumbnail(book_list, thumbnail_url)

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
                    book_list.save()  # thumbnail 파일 경로 포함 전체 저장
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

        if not duplicate_book_list:
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
        "duplicate_book_list": duplicate_book_list,
        "duplicate_books": duplicate_books,
    })


# ── 썸네일 일괄 갱신 ─────────────────────────────────────────
_thumb_sync_in_progress: bool = False
_thumb_sync_lock = threading.Lock()


@_staff_required
@require_POST
def book_sync_thumbnails(request):
    """thumbnail_url 기반 썸네일 일괄 갱신 (AJAX) — 백그라운드 실행.

    thumbnail_url이 있는 모든 BookList의 이미지를 재다운로드하여 저장합니다.
    링크가 없는 BookList는 건드리지 않습니다.
    """
    global _thumb_sync_in_progress
    with _thumb_sync_lock:
        if _thumb_sync_in_progress:
            return JsonResponse({"ok": False, "message": "이미 갱신이 진행 중입니다."})
        _thumb_sync_in_progress = True

    total = BookList.objects.exclude(thumbnail_url="").count()

    def _run():
        global _thumb_sync_in_progress
        try:
            from .services import _save_thumbnail
            qs = BookList.objects.exclude(thumbnail_url="")
            for bl in qs.iterator():
                # 기존 파일 먼저 삭제하여 덮어쓰기 보장
                if bl.thumbnail:
                    bl.thumbnail.delete(save=False)
                if _save_thumbnail(bl, bl.thumbnail_url):
                    bl.save(update_fields=["thumbnail"])
                elif not bl.thumbnail.name:
                    # 다운로드 실패 + 파일 삭제된 경우 DB도 비워서 일치
                    bl.save(update_fields=["thumbnail"])
        except Exception as exc:
            logger.error("썸네일 일괄 갱신 오류: %s", exc)
        finally:
            with _thumb_sync_lock:
                _thumb_sync_in_progress = False

    threading.Thread(target=_run, daemon=True).start()
    return JsonResponse({
        "ok": True,
        "message": f"총 {total}권의 썸네일 갱신이 백그라운드에서 시작되었습니다.",
        "total": total,
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
def book_scrape_url(request):
    """링크로 도서 정보 수집 (수정 폼 AJAX용).

    Request JSON: {"url": "https://..."}
    Response JSON:
        {"status": "ok", "data": {...}}
        {"status": "not_implemented", "source": "aladin"}
        {"status": "unsupported"}
        {"status": "error", "message": "..."}
    """
    import json
    try:
        body = json.loads(request.body or "{}")
    except Exception:
        return JsonResponse({"status": "error", "message": "잘못된 요청입니다."}, status=400)

    url = body.get("url", "").strip()
    if not url:
        return JsonResponse({"status": "error", "message": "URL을 입력해주세요."}, status=400)

    from .services import scrape_from_url
    result = scrape_from_url(url)
    return JsonResponse(result)


@_staff_required
@require_POST
def book_apply_thumbnail(request, pk):
    """썸네일 URL 즉시 다운로드·저장 (수정 폼 AJAX용)."""
    import json as _json
    book = get_object_or_404(Book, pk=pk)
    try:
        body = _json.loads(request.body or "{}")
    except Exception:
        return JsonResponse({"ok": False, "error": "잘못된 요청"}, status=400)

    thumbnail_url = body.get("thumbnail_url", "").strip()
    if not thumbnail_url:
        return JsonResponse({"ok": False, "error": "URL이 없습니다."}, status=400)

    try:
        from .services import _save_thumbnail
        bl = book.book_list
        if bl.thumbnail:
            bl.thumbnail.delete(save=False)
        bl.thumbnail_url = thumbnail_url
        saved = _save_thumbnail(bl, thumbnail_url)
        bl.save(update_fields=["thumbnail_url", "thumbnail"])
        return JsonResponse({"ok": True, "saved": saved})
    except Exception as exc:
        logger.error("apply_thumbnail 오류 (pk=%s): %s", pk, exc)
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


# ── 통계 페이지 ───────────────────────────────────────────────

# 카테고리별 기술 스택 키워드 정의
# 형식: { 카테고리명: { 기술명: ([포함 키워드], [제외 키워드]) } }
_TECH_KEYWORDS: dict[str, dict[str, tuple[list, list]]] = {
    "프로그래밍 언어": {
        "Python":     (["파이썬", "python"], []),
        "Java":       (["자바", "java"], ["자바스크립트", "javascript"]),
        "JavaScript": (["자바스크립트", "javascript"], []),
        "C/C++":      (["c언어", "c++", "씨언어", "c 프로그래밍"], []),
        "Go":         (["golang", "go 언어", "고언어"], []),
        "TypeScript": (["타입스크립트", "typescript"], []),
        "Kotlin":     (["코틀린", "kotlin"], []),
        "PHP":        (["php"], []),
        "Rust":       (["러스트", "rust 언어", "rust프로그래밍"], []),
        "Swift":      (["스위프트", "swift 언어"], []),
        "R":          (["r 언어", "r프로그래밍", "r로 배우"], []),
    },
    "웹 개발": {
        "React":      (["리액트", "react"], []),
        "Spring":     (["스프링", "spring"], []),
        "Vue.js":     (["뷰제이에스", "vue.js", "vue 3", "vue로"], ["리뷰"]),
        "Node.js":    (["노드", "node.js", "nodejs"], []),
        "TypeScript": (["타입스크립트", "typescript"], []),
        "Next.js":    (["넥스트", "next.js"], []),
        "HTML/CSS":   (["html", "css 레이아웃", "웹 퍼블"], []),
        "Django":     (["장고", "django"], []),
        "FastAPI":    (["fastapi"], []),
        "PHP":        (["php"], []),
    },
    "모바일 개발": {
        "Android":       (["안드로이드", "android"], []),
        "Flutter":       (["플러터", "flutter"], []),
        "iOS":           (["ios", "아이폰 앱", "아이폰앱"], []),
        "Kotlin":        (["코틀린", "kotlin"], []),
        "Swift":         (["스위프트", "swift"], []),
        "React Native":  (["react native", "리액트 네이티브"], []),
    },
    "데이터베이스": {
        "MySQL":         (["mysql", "마이에스큐엘", "마이sql"], []),
        "PostgreSQL":    (["postgresql", "postgres"], []),
        "MongoDB":       (["mongodb", "몽고db", "몽고디비"], []),
        "Oracle":        (["오라클", "oracle"], []),
        "Redis":         (["redis", "레디스"], []),
        "Elasticsearch": (["elasticsearch", "엘라스틱서치", "엘라스틱"], []),
        "SQL":           (["sql"], []),
    },
    "자료구조·알고리즘": {
        "알고리즘":        (["알고리즘"], []),
        "자료구조":        (["자료구조"], []),
        "코딩테스트":      (["코딩테스트", "코딩 테스트"], []),
        "Python 알고리즘": (["파이썬 알고리즘", "python 알고리즘", "파이썬으로 배우는 알고리즘"], []),
        "Java 알고리즘":   (["자바 알고리즘", "java 알고리즘"], []),
        "C++ 알고리즘":    (["c++ 알고리즘"], []),
    },
    "컴퓨터 과학": {
        "운영체제":   (["운영체제"], []),
        "네트워크":   (["네트워크"], []),
        "Linux":     (["리눅스", "linux", "유닉스"], []),
        "컴퓨터구조": (["컴퓨터 구조", "컴퓨터구조"], []),
        "컴파일러":   (["컴파일러"], []),
    },
    "인공지능·데이터": {
        "머신러닝":    (["머신러닝", "machine learning"], []),
        "딥러닝":      (["딥러닝", "deep learning"], []),
        "PyTorch":     (["파이토치", "pytorch"], []),
        "TensorFlow":  (["텐서플로", "tensorflow"], []),
        "pandas":      (["판다스", "pandas"], []),
        "GPT·LLM":     (["gpt", "llm", "chatgpt", "거대 언어", "거대언어"], []),
        "LangChain":   (["langchain", "랭체인"], []),
        "데이터 분석": (["데이터 분석", "데이터분석"], []),
    },
    "DevOps·클라우드": {
        "AWS":        (["aws", "아마존 웹 서비스"], []),
        "Docker":     (["도커", "docker"], []),
        "Kubernetes": (["쿠버네티스", "kubernetes", "k8s"], []),
        "Azure":      (["애저", "azure"], []),
        "GCP":        (["gcp", "구글 클라우드"], []),
        "Terraform":  (["테라폼", "terraform"], []),
        "CI/CD":      (["ci/cd", "jenkins", "깃허브 액션", "github action", "github actions"], []),
    },
    "소프트웨어 공학": {
        "클린코드":      (["클린코드", "clean code"], []),
        "디자인패턴":    (["디자인패턴", "design pattern"], []),
        "마이크로서비스":(["마이크로서비스", "microservice"], []),
        "TDD":           (["tdd", "테스트 주도", "테스트주도"], []),
        "리팩토링":      (["리팩토링", "refactoring"], []),
        "DDD":           (["ddd", "도메인 주도"], []),
        "애자일":        (["애자일", "agile"], []),
    },
    "보안": {
        "정보보안":    (["정보보안"], []),
        "네트워크보안":(["네트워크 보안", "네트워크보안"], []),
        "웹보안":      (["웹 보안", "웹보안", "owasp"], []),
        "암호학":      (["암호학", "암호 알고리즘", "cryptography"], []),
        "해킹":        (["해킹", "hacking", "해커", "버그바운티"], []),
        "침투테스트":  (["침투 테스트", "침투테스트", "penetration"], []),
        "포렌식":      (["포렌식", "forensic"], []),
    },
    "자격증·취업": {
        "정보처리기사":  (["정보처리기사"], []),
        "SQLD":          (["sqld", "sql 개발자"], []),
        "정보보안기사":  (["정보보안기사"], []),
        "네트워크관리사":(["네트워크관리사"], []),
        "AWS 자격증":    (["aws 자격", "aws certified"], []),
        "리눅스마스터":  (["리눅스마스터", "lpi"], []),
        "빅데이터":      (["빅데이터 분석기사", "빅데이터 준전문가"], []),
    },
    "IT 교양": {
        "개발자 에세이": (["개발자로", "개발자의", "프로그래머로", "개발자가"], []),
        "AI·미래기술":   (["ai 시대", "인공지능 시대", "미래 기술"], []),
        "스타트업":      (["스타트업", "창업"], []),
        "IT 경영":       (["디지털 전환", "cto", "it 경영"], []),
        "비전공자":      (["비전공자", "it 입문", "코딩 입문"], []),
    },
}

_STATS_CACHE_KEY = "books:stats_data_v2"
_STATS_CACHE_TTL = 300  # 5분


def _build_stack_data() -> dict:
    """카테고리별 기술 스택 키워드 카운트. PostgreSQL ILIKE 매칭."""
    result = {}
    for cat_name, tech_map in _TECH_KEYWORDS.items():
        entries = []
        for tech, (includes, excludes) in tech_map.items():
            include_q = Q()
            for kw in includes:
                include_q |= Q(title__icontains=kw)
            qs = BookList.objects.filter(include_q)
            for ex in excludes:
                qs = qs.exclude(title__icontains=ex)
            cnt = qs.count()
            if cnt > 0:
                entries.append({"name": tech, "count": cnt})
        result[cat_name] = sorted(entries, key=lambda x: -x["count"])
    return result


@staff_member_required
def stats_page(request):
    return render(request, "books/stats.html")


@staff_member_required
def stats_books_api(request):
    """기술 스택 클릭 시 매칭 도서 목록 반환."""
    cat  = request.GET.get("cat", "")
    tech = request.GET.get("tech", "")
    cat_map = _TECH_KEYWORDS.get(cat, {})
    if tech not in cat_map:
        return JsonResponse({"books": [], "total": 0, "tech": tech})
    includes, excludes = cat_map[tech]
    include_q = Q()
    for kw in includes:
        include_q |= Q(title__icontains=kw)
    qs = BookList.objects.filter(include_q)
    for ex in excludes:
        qs = qs.exclude(title__icontains=ex)
    total = qs.count()
    books = list(qs.order_by("title").values("id", "title", "difficulty")[:50])
    return JsonResponse({"books": books, "total": total, "tech": tech})


@staff_member_required
def stats_api(request):
    """통계 데이터 JSON API — 5분 캐시."""
    data = cache.get(_STATS_CACHE_KEY)
    if data is not None:
        return JsonResponse(data)

    unique_books = BookList.objects.count()
    assign_total = BookListCategory.objects.count()

    # 카테고리별 도서 수
    cat_counts = dict(
        Category.objects.annotate(cnt=Count("book_lists"))
        .order_by("-cnt")
        .values_list("name", "cnt")
    )

    # 난이도 분포
    diff_counts = {
        row["difficulty"]: row["cnt"]
        for row in BookList.objects
        .filter(difficulty__in=["입문", "초급", "중급", "고급"])
        .values("difficulty")
        .annotate(cnt=Count("id"))
    }

    # 출판사 Top 8
    pub_top = list(
        Publisher.objects.annotate(cnt=Count("book_lists"))
        .order_by("-cnt")[:8]
        .values_list("name", "cnt")
    )

    # 카테고리 × 난이도 교차
    heat_raw = (
        BookListCategory.objects
        .filter(book_list__difficulty__in=["입문", "초급", "중급", "고급"])
        .values("category__name", "book_list__difficulty")
        .annotate(cnt=Count("id"))
    )
    heat_data: dict[str, dict] = {}
    for row in heat_raw:
        cname = row["category__name"]
        diff  = row["book_list__difficulty"]
        heat_data.setdefault(cname, {"입문": 0, "초급": 0, "중급": 0, "고급": 0})
        heat_data[cname][diff] = row["cnt"]

    # 기술 스택
    stack_data = _build_stack_data()

    # 상위 카테고리
    top_cat = max(cat_counts.items(), key=lambda x: x[1]) if cat_counts else ("—", 0)

    result = {
        "summary": {
            "unique_books": unique_books,
            "assign_total": assign_total,
            "avg_per_book": round(assign_total / unique_books, 2) if unique_books else 0,
            "top_cat": {"name": top_cat[0], "count": top_cat[1]},
        },
        "cat_counts":  cat_counts,
        "stack_data":  stack_data,
        "diff_counts": diff_counts,
        "pub_top":     pub_top,
        "heat_data":   heat_data,
    }
    cache.set(_STATS_CACHE_KEY, result, _STATS_CACHE_TTL)
    return JsonResponse(result)
