"""
데이터 관리 파이프라인 웹 인터페이스
- GET  /pipeline/                      → 관리자 전용 데이터 관리 페이지
- POST /pipeline/run/                  → 전체 파이프라인 시작 (job_id 반환)
- GET  /pipeline/stream/<job_id>/      → 파이프라인 SSE 스트림
- POST /pipeline/embed/run/            → 임베딩 누락 도서 처리 시작 (job_id 반환)
- GET  /pipeline/embed/stream/<job_id>/ → 임베딩 SSE 스트림
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
    return render(request, "data_pipeline/index.html", {
        "total_books":   total_books,
        "pending":       pending,
        "missing_embed": missing_embed,
        "unclassified":  unclassified,
    })


# ── 전체 파이프라인 ───────────────────────────────────────────

@_staff_required
@require_POST
def run_pipeline(request):
    """전체 파이프라인 시작. job_id를 반환하고 백그라운드에서 실행."""
    if _running.is_set():
        return JsonResponse({"error": "이미 파이프라인이 실행 중입니다."}, status=409)

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
            title = book.book_list.title
            book_code = book.book_code
            q.put({"type": "progress", "done": done, "total": total,
                   "current": f"[{book_code}] {title}"})

            log_messages = []
            def progress_cb(msg, _bc=book_code, _t=title):
                log_messages.append(msg)

            try:
                run_book_pipeline(book, progress_callback=progress_cb)
                done += 1
                q.put({"type": "log", "status": "success",
                       "book_code": book_code, "title": title, "messages": log_messages})
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
        return JsonResponse({"error": "이미 임베딩이 진행 중입니다."}, status=409)

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
        return JsonResponse({"error": "이미 난이도 분류가 진행 중입니다."}, status=409)

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
        from books.services import classify_difficulty, scrape_reviews

        total = len(targets)
        q.put({"type": "start", "total": total})

        done = errors = 0
        for bl in targets:
            q.put({"type": "progress", "done": done, "total": total, "current": bl.title})
            try:
                reviews = scrape_reviews(bl.title)
                difficulty = classify_difficulty(bl.title, bl.description, reviews)
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
