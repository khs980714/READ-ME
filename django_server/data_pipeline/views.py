"""
데이터 관리 파이프라인 웹 인터페이스
- GET  /pipeline/                          → 관리자 전용 데이터 관리 페이지
- POST /pipeline/run/                      → 전체 파이프라인 시작 (job_id 반환)
- GET  /pipeline/stream/<job_id>/          → 파이프라인 SSE 스트림
- POST /pipeline/embed/run/                → 임베딩 누락 도서 처리 시작 (job_id 반환)
- GET  /pipeline/embed/stream/<job_id>/    → 임베딩 SSE 스트림
- POST /pipeline/year/run/                 → 출판 연도 추출 시작 (job_id 반환)
- GET  /pipeline/year/stream/<job_id>/     → 연도 추출 SSE 스트림
- POST /pipeline/candidates/search/        → 알라딘 도서 후보 검색 (AJAX)
- POST /pipeline/candidates/apply/         → 선택 후보 도서 정보 적용 (AJAX)
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


def _staff_required(view_func):
    """관리자가 아니면 메인 페이지로 리다이렉트."""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_staff:
            return redirect("books:list")
        return view_func(request, *args, **kwargs)
    wrapper.__name__ = view_func.__name__
    return wrapper


# ── 페이지 ────────────────────────────────────────────────────

@_staff_required
def pipeline_page(request):
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
    return render(request, "data_pipeline/index.html", {
        "total_books":   total_books,
        "pending":       pending,
        "missing_embed": missing_embed,
        "unclassified":  unclassified,
        "missing_year":  missing_year,
    })


# ── 전체 파이프라인 ───────────────────────────────────────────

def _parse_force(request) -> bool:
    try:
        return bool(json.loads(request.body or "{}").get("force"))
    except Exception:
        return False


@_staff_required
@require_POST
def run_pipeline(request):
    """전체 파이프라인 시작. job_id를 반환하고 백그라운드에서 실행."""
    if _running.is_set():
        if not _parse_force(request):
            return JsonResponse({"error": "이미 파이프라인이 실행 중입니다."}, status=409)
        _running.clear()

    job_id = str(uuid.uuid4())
    q: Queue = Queue()
    with _jobs_lock:
        _jobs[job_id] = q

    threading.Thread(target=_pipeline_worker, args=(job_id, q), daemon=True).start()
    return JsonResponse({"job_id": job_id})


def _pipeline_worker(job_id: str, q: Queue):
    _running.set()
    try:
        from books.models import Book
        from books.services import run_book_pipeline

        books = list(
            Book.objects.filter(is_active=True)
            .select_related("book_list__author", "book_list__publisher")
            .order_by("book_code")
        )
        total = len(books)
        q.put({"type": "start", "total": total})

        done = errors = 0
        for book in books:
            title = book.book_list.full_title
            book_code = book.book_code
            book_list_id = book.book_list.pk
            q.put({"type": "progress", "done": done, "total": total,
                   "current": f"[{book_code}] {title}"})

            log_messages = []
            def progress_cb(msg, _bc=book_code, _t=title):
                log_messages.append(msg)

            try:
                candidates = run_book_pipeline(
                    book, progress_callback=progress_cb, return_candidates=True
                )
                done += 1
                q.put({"type": "log", "status": "success",
                       "book_code": book_code, "title": title, "messages": log_messages})
                # 알라딘 후보가 2개 이상이면 도서 선택 이벤트 emit
                if candidates and len(candidates) > 1:
                    q.put({
                        "type": "candidates",
                        "book_code": book_code,
                        "title": title,
                        "book_list_id": book_list_id,
                        "candidates": candidates,
                    })
            except Exception as e:
                errors += 1
                q.put({"type": "log", "status": "error",
                       "book_code": book_code, "title": title,
                       "error": str(e), "messages": log_messages})

        q.put({"type": "complete", "done": done, "errors": errors, "total": total})
    except Exception as e:
        q.put({"type": "fatal", "error": str(e)})
    finally:
        q.put(None)
        _running.clear()


def pipeline_stream(request, job_id: str):
    """SSE 스트림 — 파이프라인 진행 상황."""
    return _make_sse_response(_jobs, _jobs_lock, job_id)


# ── 임베딩 누락 도서 처리 ─────────────────────────────────────

@_staff_required
@require_POST
def run_embed_missing(request):
    """description 있음 + embedding 없음 도서를 SSE 방식으로 일괄 처리."""
    if _embed_running.is_set():
        if not _parse_force(request):
            return JsonResponse({"error": "이미 임베딩이 진행 중입니다."}, status=409)
        _embed_running.clear()

    from books.models import BookEmbedding, BookList
    embedded_ids = list(BookEmbedding.objects.values_list("book_list_id", flat=True))
    targets = list(
        BookList.objects
        .exclude(description="")
        .exclude(pk__in=embedded_ids)
        .order_by("title")
    )
    if not targets:
        return JsonResponse({"error": "임베딩 누락 도서가 없습니다."}, status=404)

    job_id = str(uuid.uuid4())
    q: Queue = Queue()
    with _embed_jobs_lock:
        _embed_jobs[job_id] = q

    threading.Thread(target=_embed_worker, args=(job_id, q, targets), daemon=True).start()
    return JsonResponse({"job_id": job_id})


def _embed_worker(job_id: str, q: Queue, targets: list):
    _embed_running.set()
    try:
        from books.services import refresh_embedding

        total = len(targets)
        q.put({"type": "start", "total": total})

        done = errors = 0
        for bl in targets:
            q.put({"type": "progress", "done": done, "total": total, "current": bl.title})
            try:
                refresh_embedding(bl)
                done += 1
                q.put({"type": "log", "status": "success", "title": bl.title})
            except Exception as e:
                errors += 1
                q.put({"type": "log", "status": "error",
                       "title": bl.title, "error": str(e)})

        q.put({"type": "complete", "done": done, "errors": errors, "total": total})
    except Exception as e:
        q.put({"type": "fatal", "error": str(e)})
    finally:
        q.put(None)
        _embed_running.clear()


def embed_stream(request, job_id: str):
    """SSE 스트림 — 임베딩 진행 상황."""
    return _make_sse_response(_embed_jobs, _embed_jobs_lock, job_id)


# ── 난이도 분류 ───────────────────────────────────────────────

_classify_jobs: dict[str, Queue] = {}
_classify_jobs_lock = threading.Lock()
_classify_running = threading.Event()


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
    with _classify_jobs_lock:
        _classify_jobs[job_id] = q

    threading.Thread(target=_classify_worker, args=(job_id, q, targets), daemon=True).start()
    return JsonResponse({"job_id": job_id})


def _classify_worker(job_id: str, q: Queue, targets: list):
    _classify_running.set()
    try:
        from books.services import classify_difficulty

        total = len(targets)
        q.put({"type": "start", "total": total})

        done = errors = 0
        for bl in targets:
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

        q.put({"type": "complete", "done": done, "errors": errors, "total": total})
    except Exception as e:
        q.put({"type": "fatal", "error": str(e)})
    finally:
        q.put(None)
        _classify_running.clear()


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
    with _year_jobs_lock:
        _year_jobs[job_id] = q

    threading.Thread(target=_year_worker, args=(job_id, q, targets), daemon=True).start()
    return JsonResponse({"job_id": job_id})


def _year_worker(job_id: str, q: Queue, targets: list):
    _year_running.set()
    try:
        from books.services import extract_publication_year

        total = len(targets)
        q.put({"type": "start", "total": total})

        done = errors = 0
        for bl in targets:
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

        q.put({"type": "complete", "done": done, "errors": errors, "total": total})
    except Exception as e:
        q.put({"type": "fatal", "error": str(e)})
    finally:
        q.put(None)
        _year_running.clear()


def year_stream(request, job_id: str):
    """SSE 스트림 — 연도 추출 진행 상황."""
    return _make_sse_response(_year_jobs, _year_jobs_lock, job_id)


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
