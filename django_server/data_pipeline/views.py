"""
데이터 관리 파이프라인 웹 인터페이스
- GET  /pipeline/                              → 관리자 전용 데이터 관리 페이지
- POST /pipeline/run/                          → 전체 파이프라인 시작 (job_id 반환)
- GET  /pipeline/stream/<job_id>/              → 파이프라인 SSE 스트림
- POST /pipeline/stop/<job_id>/               → 실행 중인 작업 중단
- POST /pipeline/embed/run/                    → 임베딩 누락 도서 처리 시작 (job_id 반환)
- GET  /pipeline/embed/stream/<job_id>/        → 임베딩 SSE 스트림
- POST /pipeline/classify/run/                 → 난이도 분류 시작 (job_id 반환)
- GET  /pipeline/classify/stream/<job_id>/     → 난이도 분류 SSE 스트림
- POST /pipeline/year/run/                     → 출판 연도 추출 시작 (job_id 반환)
- GET  /pipeline/year/stream/<job_id>/         → 연도 추출 SSE 스트림
- POST /pipeline/category/run/                 → 카테고리 분류 시작 (job_id 반환)
- GET  /pipeline/category/stream/<job_id>/     → 카테고리 분류 SSE 스트림
- POST /pipeline/candidates/search/            → 알라딘 도서 후보 검색 (AJAX)
- POST /pipeline/candidates/apply/             → 선택 후보 도서 정보 적용 (AJAX)
"""

import json
import threading
import uuid
from queue import Empty, Queue

from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

# ── job 저장소 (메모리) ───────────────────────────────────────
_jobs: dict[str, Queue] = {}
_jobs_lock = threading.Lock()
_running = threading.Event()          # 파이프라인 동시 실행 방지

_embed_jobs: dict[str, Queue] = {}
_embed_jobs_lock = threading.Lock()
_embed_running = threading.Event()    # 임베딩 동시 실행 방지

_year_jobs: dict[str, Queue] = {}
_year_jobs_lock = threading.Lock()
_year_running = threading.Event()     # 연도 추출 동시 실행 방지

_classify_jobs: dict[str, Queue] = {}
_classify_jobs_lock = threading.Lock()
_classify_running = threading.Event()

_category_jobs: dict[str, Queue] = {}
_category_jobs_lock = threading.Lock()
_category_running = threading.Event()

# ── 취소 플래그 (job_id → Event) ─────────────────────────────
_cancel_flags: dict[str, threading.Event] = {}
_cancel_flags_lock = threading.Lock()


def _staff_required(view_func):
    """관리자가 아니면 메인 페이지로 리다이렉트."""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_staff:
            return redirect("books:list")
        return view_func(request, *args, **kwargs)
    wrapper.__name__ = view_func.__name__
    return wrapper


def _register_cancel(job_id: str) -> threading.Event:
    e = threading.Event()
    with _cancel_flags_lock:
        _cancel_flags[job_id] = e
    return e


def _unregister_cancel(job_id: str):
    with _cancel_flags_lock:
        _cancel_flags.pop(job_id, None)


# ── 페이지 ────────────────────────────────────────────────────

@_staff_required
def pipeline_page(request):
    from django.db.models import Count
    from books.models import Book, BookEmbedding, BookList
    total_books = Book.objects.filter(is_active=True).count()
    pending = BookList.objects.filter(description="").count()
    embedded_ids = BookEmbedding.objects.values_list("book_list_id", flat=True)
    missing_embed = (
        BookList.objects
        .exclude(description="")
        .exclude(pk__in=embedded_ids)
        .count()
    )
    # description 있음 + difficulty 미분류
    unclassified = BookList.objects.exclude(description="").filter(difficulty="").count()
    # publication_year 미등록
    missing_year = BookList.objects.filter(publication_year__isnull=True).count()
    # description 있음 + 카테고리 없음
    missing_category = (
        BookList.objects
        .exclude(description="")
        .annotate(cat_count=Count("categories"))
        .filter(cat_count=0)
        .count()
    )
    return render(request, "data_pipeline/index.html", {
        "total_books":      total_books,
        "pending":          pending,
        "missing_embed":    missing_embed,
        "unclassified":     unclassified,
        "missing_year":     missing_year,
        "missing_category": missing_category,
    })


# ── 작업 중단 ─────────────────────────────────────────────────

@_staff_required
@require_POST
def stop_job(request, job_id: str):
    """실행 중인 파이프라인 작업을 중단 요청."""
    with _cancel_flags_lock:
        e = _cancel_flags.get(job_id)
    if e:
        e.set()
        return JsonResponse({"ok": True})
    return JsonResponse({"error": "실행 중인 작업이 없습니다."}, status=404)


# ── 전체 파이프라인 ───────────────────────────────────────────

def _parse_force(request) -> bool:
    try:
        return bool(json.loads(request.body or "{}").get("force"))
    except Exception:
        return False


@_staff_required
@require_POST
def run_pipeline(request):
    """전체 파이프라인 시작. job_id를 반환하고 백그라운드에서 실행.
    BookList(중복 제거된 도서 정보) 단위로 처리합니다.
    """
    if _running.is_set():
        if not _parse_force(request):
            return JsonResponse({"error": "이미 파이프라인이 실행 중입니다."}, status=409)
        _running.clear()

    job_id = str(uuid.uuid4())
    q: Queue = Queue()
    cancel_event = _register_cancel(job_id)
    with _jobs_lock:
        _jobs[job_id] = q

    threading.Thread(target=_pipeline_worker, args=(job_id, q, cancel_event), daemon=True).start()
    return JsonResponse({"job_id": job_id})


def _pipeline_worker(job_id: str, q: Queue, cancel_event: threading.Event):
    _running.set()
    try:
        from books.models import BookList
        from books.services import run_booklist_pipeline

        # books 테이블이 아닌 book_list(중복 없는 도서 정보) 기준으로 처리
        book_lists = list(
            BookList.objects
            .select_related("author", "publisher")
            .order_by("title")
        )
        total = len(book_lists)
        q.put({"type": "start", "total": total})

        done = errors = 0
        for bl in book_lists:
            if cancel_event.is_set():
                q.put({"type": "cancelled", "done": done, "total": total})
                break

            title = bl.full_title
            book_list_id = bl.pk
            q.put({"type": "progress", "done": done, "total": total,
                   "current": title})

            log_messages = []
            def progress_cb(msg):
                log_messages.append(msg)

            try:
                candidates = run_booklist_pipeline(
                    bl, progress_callback=progress_cb, return_candidates=True
                )
                done += 1
                q.put({"type": "log", "status": "success",
                       "title": title, "messages": log_messages})
                # 알라딘 후보가 2개 이상이면 도서 선택 이벤트 emit
                if candidates and len(candidates) > 1:
                    q.put({
                        "type": "candidates",
                        "title": title,
                        "book_list_id": book_list_id,
                        "candidates": candidates,
                    })
            except Exception as e:
                errors += 1
                q.put({"type": "log", "status": "error",
                       "title": title, "error": str(e), "messages": log_messages})

        if not cancel_event.is_set():
            q.put({"type": "complete", "done": done, "errors": errors, "total": total})
    except Exception as e:
        q.put({"type": "fatal", "error": str(e)})
    finally:
        q.put(None)
        _running.clear()
        _unregister_cancel(job_id)


def pipeline_stream(request, job_id: str):
    """SSE 스트림 — 파이프라인 진행 상황."""
    return _make_sse_response(_jobs, _jobs_lock, job_id)


# ── 임베딩 누락 도서 처리 ─────────────────────────────────────

@_staff_required
@require_POST
def run_embed_missing(request):
    """description 있는 모든 도서의 임베딩을 생성·갱신합니다 (SSE 방식)."""
    if _embed_running.is_set():
        if not _parse_force(request):
            return JsonResponse({"error": "이미 임베딩이 진행 중입니다."}, status=409)
        _embed_running.clear()

    from books.models import BookList
    targets = list(
        BookList.objects
        .exclude(description="")
        .order_by("title")
    )
    if not targets:
        return JsonResponse({"error": "임베딩할 도서가 없습니다."}, status=404)

    job_id = str(uuid.uuid4())
    q: Queue = Queue()
    cancel_event = _register_cancel(job_id)
    with _embed_jobs_lock:
        _embed_jobs[job_id] = q

    threading.Thread(target=_embed_worker, args=(job_id, q, targets, cancel_event), daemon=True).start()
    return JsonResponse({"job_id": job_id})


def _embed_worker(job_id: str, q: Queue, targets: list, cancel_event: threading.Event):
    _embed_running.set()
    try:
        from books.services import refresh_embedding

        total = len(targets)
        q.put({"type": "start", "total": total})

        done = errors = 0
        for bl in targets:
            if cancel_event.is_set():
                q.put({"type": "cancelled", "done": done, "total": total})
                break

            q.put({"type": "progress", "done": done, "total": total, "current": bl.title})
            try:
                refresh_embedding(bl)
                done += 1
                q.put({"type": "log", "status": "success", "title": bl.title})
            except Exception as e:
                errors += 1
                q.put({"type": "log", "status": "error",
                       "title": bl.title, "error": str(e)})

        if not cancel_event.is_set():
            q.put({"type": "complete", "done": done, "errors": errors, "total": total})
    except Exception as e:
        q.put({"type": "fatal", "error": str(e)})
    finally:
        q.put(None)
        _embed_running.clear()
        _unregister_cancel(job_id)


def embed_stream(request, job_id: str):
    """SSE 스트림 — 임베딩 진행 상황."""
    return _make_sse_response(_embed_jobs, _embed_jobs_lock, job_id)


# ── 난이도 분류 ───────────────────────────────────────────────

@_staff_required
@require_POST
def run_classify(request):
    """description 있음 + difficulty 미분류 도서를 SSE 방식으로 일괄 분류."""
    if _classify_running.is_set():
        if not _parse_force(request):
            return JsonResponse({"error": "이미 난이도 분류가 진행 중입니다."}, status=409)
        _classify_running.clear()

    from books.models import BookList
    targets = list(
        BookList.objects
        .exclude(description="")
        .filter(difficulty="")
        .order_by("title")
    )
    if not targets:
        return JsonResponse({"error": "분류할 도서가 없습니다."}, status=404)

    job_id = str(uuid.uuid4())
    q: Queue = Queue()
    cancel_event = _register_cancel(job_id)
    with _classify_jobs_lock:
        _classify_jobs[job_id] = q

    threading.Thread(target=_classify_worker, args=(job_id, q, targets, cancel_event), daemon=True).start()
    return JsonResponse({"job_id": job_id})


def _classify_worker(job_id: str, q: Queue, targets: list, cancel_event: threading.Event):
    _classify_running.set()
    try:
        from books.services import classify_difficulty

        total = len(targets)
        q.put({"type": "start", "total": total})

        done = errors = 0
        for bl in targets:
            if cancel_event.is_set():
                q.put({"type": "cancelled", "done": done, "total": total})
                break

            q.put({"type": "progress", "done": done, "total": total, "current": bl.title})
            try:
                difficulty = classify_difficulty(bl.title, bl.description, [])
                if difficulty and difficulty in ("입문", "초급", "중급", "고급"):
                    bl.difficulty = difficulty
                    bl.save(update_fields=["difficulty"])
                    done += 1
                    q.put({"type": "log", "status": "success",
                           "title": bl.title, "difficulty": difficulty})
                else:
                    errors += 1
                    q.put({"type": "log", "status": "error",
                           "title": bl.title,
                           "error": f"분류 실패 (응답: {difficulty!r})"})
            except Exception as e:
                errors += 1
                q.put({"type": "log", "status": "error",
                       "title": bl.title, "error": str(e)})

        if not cancel_event.is_set():
            q.put({"type": "complete", "done": done, "errors": errors, "total": total})
    except Exception as e:
        q.put({"type": "fatal", "error": str(e)})
    finally:
        q.put(None)
        _classify_running.clear()
        _unregister_cancel(job_id)


def classify_stream(request, job_id: str):
    """SSE 스트림 — 난이도 분류 진행 상황."""
    return _make_sse_response(_classify_jobs, _classify_jobs_lock, job_id)


# ── 출판 연도 추출 ────────────────────────────────────────────

@_staff_required
@require_POST
def run_extract_year(request):
    """전체 도서의 출판 연도를 제목·설명에서 추출하여 저장."""
    if _year_running.is_set():
        if not _parse_force(request):
            return JsonResponse({"error": "이미 연도 추출이 진행 중입니다."}, status=409)
        _year_running.clear()

    from books.models import BookList
    targets = list(BookList.objects.all().order_by("title"))
    if not targets:
        return JsonResponse({"error": "도서가 없습니다."}, status=404)

    job_id = str(uuid.uuid4())
    q: Queue = Queue()
    cancel_event = _register_cancel(job_id)
    with _year_jobs_lock:
        _year_jobs[job_id] = q

    threading.Thread(target=_year_worker, args=(job_id, q, targets, cancel_event), daemon=True).start()
    return JsonResponse({"job_id": job_id})


def _year_worker(job_id: str, q: Queue, targets: list, cancel_event: threading.Event):
    _year_running.set()
    try:
        from books.services import extract_publication_year

        total = len(targets)
        q.put({"type": "start", "total": total})

        done = errors = 0
        for bl in targets:
            if cancel_event.is_set():
                q.put({"type": "cancelled", "done": done, "total": total})
                break

            q.put({"type": "progress", "done": done, "total": total,
                   "current": bl.full_title})
            try:
                year = extract_publication_year(bl.title, bl.description)
                bl.publication_year = year
                bl.save(update_fields=["publication_year"])
                done += 1
                year_str = str(year) if year else "미등록"
                q.put({"type": "log", "status": "success",
                       "title": bl.full_title, "year": year_str})
            except Exception as e:
                errors += 1
                q.put({"type": "log", "status": "error",
                       "title": bl.full_title, "error": str(e)})

        if not cancel_event.is_set():
            q.put({"type": "complete", "done": done, "errors": errors, "total": total})
    except Exception as e:
        q.put({"type": "fatal", "error": str(e)})
    finally:
        q.put(None)
        _year_running.clear()
        _unregister_cancel(job_id)


def year_stream(request, job_id: str):
    """SSE 스트림 — 연도 추출 진행 상황."""
    return _make_sse_response(_year_jobs, _year_jobs_lock, job_id)


# ── 카테고리 분류 ─────────────────────────────────────────────

@_staff_required
@require_POST
def run_classify_category(request):
    """description 있음 + 카테고리 없음 도서를 SSE 방식으로 일괄 분류."""
    if _category_running.is_set():
        if not _parse_force(request):
            return JsonResponse({"error": "이미 카테고리 분류가 진행 중입니다."}, status=409)
        _category_running.clear()

    from django.db.models import Count
    from books.models import BookList
    targets = list(
        BookList.objects
        .exclude(description="")
        .annotate(cat_count=Count("categories"))
        .filter(cat_count=0)
        .order_by("title")
    )
    if not targets:
        return JsonResponse({"error": "분류할 도서가 없습니다."}, status=404)

    job_id = str(uuid.uuid4())
    q: Queue = Queue()
    cancel_event = _register_cancel(job_id)
    with _category_jobs_lock:
        _category_jobs[job_id] = q

    threading.Thread(target=_category_worker, args=(job_id, q, targets, cancel_event), daemon=True).start()
    return JsonResponse({"job_id": job_id})


def _category_worker(job_id: str, q: Queue, targets: list, cancel_event: threading.Event):
    _category_running.set()
    try:
        from books.models import BookListCategory, Category
        from books.services import classify_category

        total = len(targets)
        q.put({"type": "start", "total": total})

        done = errors = 0
        for bl in targets:
            if cancel_event.is_set():
                q.put({"type": "cancelled", "done": done, "total": total})
                break

            q.put({"type": "progress", "done": done, "total": total, "current": bl.title})
            try:
                category_names = classify_category(bl.title, bl.description)
                if not category_names:
                    errors += 1
                    q.put({"type": "log", "status": "error",
                           "title": bl.title, "error": "카테고리 반환 없음"})
                    continue

                assigned = []
                for name in category_names:
                    cat, _ = Category.objects.get_or_create(name=name)
                    BookListCategory.objects.get_or_create(book_list=bl, category=cat)
                    assigned.append(name)

                done += 1
                q.put({"type": "log", "status": "success",
                       "title": bl.title, "categories": assigned})
            except Exception as e:
                errors += 1
                q.put({"type": "log", "status": "error",
                       "title": bl.title, "error": str(e)})

        if not cancel_event.is_set():
            q.put({"type": "complete", "done": done, "errors": errors, "total": total})
    except Exception as e:
        q.put({"type": "fatal", "error": str(e)})
    finally:
        q.put(None)
        _category_running.clear()
        _unregister_cancel(job_id)


def category_stream(request, job_id: str):
    """SSE 스트림 — 카테고리 분류 진행 상황."""
    return _make_sse_response(_category_jobs, _category_jobs_lock, job_id)


# ── 알라딘 도서 후보 선택 ─────────────────────────────────────

@_staff_required
@require_POST
def search_book_candidates(request):
    """특정 book_list의 알라딘 후보 도서 목록 반환 (AJAX)."""
    import json as _json
    try:
        body = _json.loads(request.body or "{}")
    except Exception:
        body = {}

    book_list_id = body.get("book_list_id")
    if not book_list_id:
        return JsonResponse({"error": "book_list_id 필요"}, status=400)

    from books.models import BookList
    from books.services import fetch_aladin_book_info
    try:
        bl = BookList.objects.get(pk=book_list_id)
        _, candidates = fetch_aladin_book_info(bl.title, max_results=5)
        return JsonResponse({"candidates": candidates})
    except BookList.DoesNotExist:
        return JsonResponse({"error": "도서 없음"}, status=404)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)


@_staff_required
@require_POST
def apply_book_candidate(request):
    """선택한 알라딘 후보 도서 정보를 BookList에 저장 (AJAX)."""
    import json as _json
    try:
        body = _json.loads(request.body or "{}")
    except Exception:
        body = {}

    book_list_id = body.get("book_list_id")
    item_id = body.get("item_id")
    if not book_list_id or not item_id:
        return JsonResponse({"error": "book_list_id, item_id 필요"}, status=400)

    from books.models import BookList
    from books.services import fetch_aladin_by_item_id
    try:
        bl = BookList.objects.get(pk=book_list_id)
        info = fetch_aladin_by_item_id(str(item_id))
        if not info:
            return JsonResponse({"error": "알라딘에서 도서 정보를 가져올 수 없습니다."}, status=404)

        update_fields = []
        if info.get("thumbnail_url"):
            bl.thumbnail_url = info["thumbnail_url"]
            update_fields.append("thumbnail_url")
        if info.get("description"):
            bl.description = info["description"]
            update_fields.append("description")
        if info.get("isbn"):
            bl.isbn = info["isbn"]
            update_fields.append("isbn")
        if info.get("toc"):
            bl.toc = info["toc"]
            update_fields.append("toc")

        if update_fields:
            bl.save(update_fields=update_fields)

        return JsonResponse({"ok": True, "updated_fields": update_fields})
    except BookList.DoesNotExist:
        return JsonResponse({"error": "도서 없음"}, status=404)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)


# ── SSE 공통 헬퍼 ─────────────────────────────────────────────

def _make_sse_response(jobs_dict: dict, lock: threading.Lock, job_id: str):
    with lock:
        q = jobs_dict.get(job_id)

    if q is None:
        def _not_found():
            yield f"data: {json.dumps({'type': 'error', 'error': '잘못된 job_id입니다.'})}\n\n"
        return StreamingHttpResponse(_not_found(), content_type="text/event-stream")

    def event_stream():
        try:
            while True:
                try:
                    event = q.get(timeout=30)
                except Empty:
                    yield ": keepalive\n\n"
                    continue
                if event is None:
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            with lock:
                jobs_dict.pop(job_id, None)

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
