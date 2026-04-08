"""
데이터 수집 파이프라인 웹 인터페이스
- GET  /pipeline/         → 관리자 전용 파이프라인 페이지
- POST /pipeline/run/     → 파이프라인 시작 (job_id 반환)
- GET  /pipeline/stream/<job_id>/ → SSE 진행 상황 스트림
"""

import json
import threading
import uuid
from queue import Empty, Queue

from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

# ── 진행 중인 job 저장소 (메모리) ────────────────────────────
_jobs: dict[str, Queue] = {}
_jobs_lock = threading.Lock()
_running = threading.Event()  # 동시 실행 방지


def _staff_required(view_func):
    """관리자가 아니면 메인 페이지로 리다이렉트."""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_staff:
            return redirect("books:list")
        return view_func(request, *args, **kwargs)
    wrapper.__name__ = view_func.__name__
    return wrapper


@_staff_required
def pipeline_page(request):
    from books.models import Book, BookList
    total_books = Book.objects.filter(is_active=True).count()
    pending = BookList.objects.filter(description="").count()
    return render(request, "data_pipeline/index.html", {
        "total_books": total_books,
        "pending": pending,
    })


@_staff_required
@require_POST
def run_pipeline(request):
    """파이프라인 시작. job_id를 반환하고 백그라운드에서 실행."""
    if _running.is_set():
        return JsonResponse({"error": "이미 파이프라인이 실행 중입니다."}, status=409)

    job_id = str(uuid.uuid4())
    q: Queue = Queue()

    with _jobs_lock:
        _jobs[job_id] = q

    thread = threading.Thread(target=_pipeline_worker, args=(job_id, q), daemon=True)
    thread.start()

    return JsonResponse({"job_id": job_id})


def _pipeline_worker(job_id: str, q: Queue):
    """백그라운드 파이프라인 실행 쓰레드."""
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

        done = 0
        errors = 0

        for book in books:
            title = book.book_list.title
            book_code = book.book_code

            q.put({
                "type": "progress",
                "done": done,
                "total": total,
                "current": f"[{book_code}] {title}",
            })

            log_messages = []

            def progress_cb(msg, _bc=book_code, _t=title):
                log_messages.append(msg)

            try:
                run_book_pipeline(book, progress_callback=progress_cb)
                done += 1
                q.put({
                    "type": "log",
                    "status": "success",
                    "book_code": book_code,
                    "title": title,
                    "messages": log_messages,
                })
            except Exception as e:
                errors += 1
                q.put({
                    "type": "log",
                    "status": "error",
                    "book_code": book_code,
                    "title": title,
                    "error": str(e),
                    "messages": log_messages,
                })

        q.put({"type": "complete", "done": done, "errors": errors, "total": total})

    except Exception as e:
        q.put({"type": "fatal", "error": str(e)})
    finally:
        q.put(None)  # sentinel — SSE 종료
        _running.clear()


def pipeline_stream(request, job_id: str):
    """SSE 스트림 — 파이프라인 진행 상황을 실시간으로 전달."""
    with _jobs_lock:
        q = _jobs.get(job_id)

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
            with _jobs_lock:
                _jobs.pop(job_id, None)

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
