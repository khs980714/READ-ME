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
- POST /pipeline/category/apply/               → 카테고리 분류 결과 DB 반영
- POST /pipeline/candidates/search/            → 교보문고 도서 후보 검색 (AJAX)
- POST /pipeline/candidates/apply/             → 선택 후보 도서 정보 적용 (AJAX)
- GET  /pipeline/thumbnails/scan/              → 미사용 썸네일 파일 목록 반환 (AJAX)
- POST /pipeline/thumbnails/clean/             → 미사용 썸네일 파일 일괄 삭제 (AJAX)
- POST /pipeline/kyobo/run/                    → 교보문고 후보 수집 시작 (job_id 반환)
- GET  /pipeline/kyobo/stream/<job_id>/        → 교보문고 후보 수집 SSE 스트림
- GET  /pipeline/kyobo/candidates/             → 저장된 교보문고 후보 목록 반환 (AJAX)
- POST /pipeline/kyobo/apply/                  → 선택 후보 상세 수집 후 DB 덮어쓰기 (AJAX)
- POST /pipeline/kyobo/dismiss/                → 해당 book_list 후보 제거 (AJAX)
"""

import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from queue import Empty, Queue

from django.core.cache import cache
from django.core.files.storage import default_storage
from django.db.models import Count
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from books.models import (
    Author,
    Book,
    BookEmbedding,
    BookList,
    BookListCategory,
    Category,
    KyoboCandidate,
    Publisher,
)
from books.services import (
    classify_category,
    classify_difficulty,
    extract_publication_year,
    fetch_book_info,
    fetch_kyobo_book_detail,
    fetch_kyobo_candidates,
    refresh_embedding,
    run_booklist_pipeline,
    save_thumbnail,
)

logger = logging.getLogger(__name__)


class _JobStore:
    """단일 파이프라인 작업 유형의 job Queue 저장소 + 동시 실행 제어."""

    def __init__(self):
        self._jobs: dict[str, Queue] = {}
        self._lock = threading.Lock()
        self._running = threading.Event()

    def is_running(self) -> bool:
        return self._running.is_set()

    def set_running(self):
        self._running.set()

    def clear_running(self):
        self._running.clear()

    def add(self, job_id: str, q: Queue):
        with self._lock:
            self._jobs[job_id] = q

    def get_response(self, job_id: str) -> "StreamingHttpResponse":
        return _make_sse_response(self._jobs, self._lock, job_id)


# ── job 저장소 인스턴스 ────────────────────────────────────────
_pipeline_store  = _JobStore()
_embed_store     = _JobStore()
_year_store      = _JobStore()
_classify_store  = _JobStore()
_category_store  = _JobStore()
_collect_store   = _JobStore()
_kyobo_store     = _JobStore()

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


# ── job 시작/종료 공용 헬퍼 ────────────────────────────────────
# 7개 run_*/워커가 반복하던 "is_running 체크 → job_id/Queue/cancel_event 준비 →
# 백그라운드 스레드 기동" / "None sentinel → store 초기화 → cancel 해제" 로직을 통합합니다.

def _start_job(
    request,
    store: "_JobStore",
    busy_message: str,
    worker_fn,
    worker_args: tuple = (),
    targets_fn=None,
    empty_message: str | None = None,
) -> JsonResponse:
    """SSE 파이프라인 작업을 시작합니다.

    targets_fn이 주어지면 먼저 호출해 대상 목록을 구하고(비어있으면 404),
    worker_fn은 (job_id, q, targets, cancel_event, *worker_args)로 호출됩니다.
    targets_fn이 없으면 대상 조회를 워커가 직접 수행한다고 보고
    worker_fn은 (job_id, q, cancel_event, *worker_args)로 호출됩니다.
    """
    if store.is_running():
        if not _parse_force(request):
            return JsonResponse({"error": busy_message}, status=409)
        store.clear_running()

    job_id = str(uuid.uuid4())
    q: Queue = Queue()
    cancel_event = _register_cancel(job_id)

    if targets_fn is not None:
        targets = targets_fn()
        if not targets:
            return JsonResponse({"error": empty_message}, status=404)
        args = (job_id, q, targets, cancel_event, *worker_args)
    else:
        args = (job_id, q, cancel_event, *worker_args)

    store.add(job_id, q)
    store.set_running()
    threading.Thread(target=worker_fn, args=args, daemon=True).start()
    return JsonResponse({"job_id": job_id})


@contextmanager
def _job_lifecycle(job_id: str, q: Queue, store: "_JobStore", invalidate_stats: bool = False):
    """워커 함수의 공통 종료 처리 (None sentinel 전송, store 상태 초기화, 취소 플래그 해제)."""
    try:
        yield
    finally:
        q.put(None)
        store.clear_running()
        _unregister_cancel(job_id)
        if invalidate_stats:
            _invalidate_pipeline_stats()


# ── 페이지 ────────────────────────────────────────────────────

_PIPELINE_STATS_CACHE_KEY = "pipeline:stats"
_PIPELINE_STATS_CACHE_TTL = 60  # 1분 캐시 (파이프라인 실행 후 캐시 무효화)


def _invalidate_pipeline_stats() -> None:
    cache.delete(_PIPELINE_STATS_CACHE_KEY)


def _get_pipeline_stats() -> dict:
    """파이프라인 통계 계산 — 결과를 1분 캐시합니다."""
    stats = cache.get(_PIPELINE_STATS_CACHE_KEY)
    if stats is not None:
        return stats

    embedded_ids = BookEmbedding.objects.values_list("book_list_id", flat=True)
    stats = {
        "total_books_all":    Book.objects.count(),
        "total_books_active": Book.objects.filter(is_active=True).count(),
        "total_book_lists":   BookList.objects.count(),
        "pending": BookList.objects.filter(description="").count(),
        "missing_embed": (
            BookList.objects
            .exclude(description="")
            .exclude(pk__in=embedded_ids)
            .count()
        ),
        "unclassified": BookList.objects.exclude(description="").filter(difficulty="").count(),
        "missing_year": BookList.objects.filter(publication_year__isnull=True).count(),
        "missing_category": (
            BookList.objects
            .exclude(description="")
            .annotate(cat_count=Count("categories"))
            .filter(cat_count=0)
            .count()
        ),
        "kyobo_pending": KyoboCandidate.objects.values("book_list").distinct().count(),
        "thumb_with_url":    BookList.objects.exclude(thumbnail_url="").count(),
        "thumb_without_url": BookList.objects.filter(thumbnail_url="").count(),
        "thumb_with_file":   BookList.objects.exclude(thumbnail="").count(),
    }
    cache.set(_PIPELINE_STATS_CACHE_KEY, stats, _PIPELINE_STATS_CACHE_TTL)
    return stats


@_staff_required
def pipeline_page(request):
    return render(request, "data_pipeline/index.html", _get_pipeline_stats())


@_staff_required
def books_list_api(request):
    """통계 카드 popover용 — 각 유형별 도서 목록 반환 (AJAX)."""
    type_ = request.GET.get("type", "")
    embedded_ids = BookEmbedding.objects.values_list("book_list_id", flat=True)

    if type_ == "uncollected":
        bls = BookList.objects.filter(description="")
    elif type_ == "no_category":
        bls = (BookList.objects.exclude(description="")
               .annotate(cat_count=Count("categories")).filter(cat_count=0))
    elif type_ == "no_difficulty":
        bls = BookList.objects.exclude(description="").filter(difficulty="")
    elif type_ == "no_embedding":
        bls = BookList.objects.exclude(description="").exclude(pk__in=embedded_ids)
    else:
        return JsonResponse({"error": "올바르지 않은 type"}, status=400)

    result = []
    for bl in bls.prefetch_related("books").order_by("title")[:200]:
        book = bl.books.filter(is_active=True).first() or bl.books.first()
        if book:
            result.append({
                "title": bl.title,
                "book_code": book.book_code,
                "url": reverse("books:detail", args=[book.pk]),
            })

    return JsonResponse({"books": result})


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


# ── 데이터 수집 전용 (분류·임베딩 제외) ──────────────────────────

@_staff_required
@require_POST
def run_collect_only(request):
    """description·thumbnail·toc 수집만 실행 (분류·임베딩 없음, SSE)."""
    return _start_job(
        request, _collect_store, "이미 데이터 수집이 진행 중입니다.",
        worker_fn=_collect_worker,
        targets_fn=lambda: list(
            BookList.objects.filter(description="")
            .select_related("author", "publisher").order_by("title")
        ),
        empty_message="수집할 도서가 없습니다.",
    )


def _collect_worker(job_id: str, q: Queue, targets: list, cancel_event: threading.Event):
    with _job_lifecycle(job_id, q, _collect_store):
        try:
            total = len(targets)
            q.put({"type": "start", "total": total})

            done = errors = 0
            for bl in targets:
                if cancel_event.is_set():
                    q.put({"type": "cancelled", "done": done, "total": total})
                    break

                q.put({"type": "progress", "done": done, "total": total, "current": bl.title})
                try:
                    info = fetch_book_info(bl.title, bl.author.name)
                    update_fields = []
                    if info.get("thumbnail_url") and not bl.thumbnail_url:
                        bl.thumbnail_url = info["thumbnail_url"]
                        update_fields.append("thumbnail_url")
                    if info.get("description") and not bl.description:
                        bl.description = info["description"]
                        update_fields.append("description")
                    if info.get("toc") and not bl.toc:
                        bl.toc = info["toc"]
                        update_fields.append("toc")
                    if bl.thumbnail_url and not bl.thumbnail:
                        if save_thumbnail(bl, bl.thumbnail_url):
                            update_fields.append("thumbnail")
                    if update_fields:
                        bl.save(update_fields=update_fields)
                    done += 1
                    q.put({"type": "log", "status": "success",
                           "title": bl.title, "messages": update_fields})
                except Exception as e:
                    errors += 1
                    q.put({"type": "log", "status": "error", "title": bl.title, "error": str(e)})

            if not cancel_event.is_set():
                q.put({"type": "complete", "done": done, "errors": errors, "total": total})
        except Exception as e:
            q.put({"type": "fatal", "error": str(e)})


def collect_stream(request, job_id: str):
    """SSE 스트림 — 데이터 수집 진행 상황."""
    return _collect_store.get_response(job_id)


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
    return _start_job(
        request, _pipeline_store, "이미 파이프라인이 실행 중입니다.",
        worker_fn=_pipeline_worker,
    )


def _pipeline_worker(job_id: str, q: Queue, cancel_event: threading.Event):
    with _job_lifecycle(job_id, q, _pipeline_store, invalidate_stats=True):
        try:
            # books 테이블이 아닌 book_list(중복 없는 도서 정보) 기준으로 처리
            book_lists = list(
                BookList.objects
                .select_related("author", "publisher")
                .order_by("title")
            )
            total = len(book_lists)
            q.put({"type": "start", "total": total})

            done_lock = threading.Lock()
            done = errors = 0

            def _process_one(bl):
                nonlocal done, errors
                if cancel_event.is_set():
                    return
                title = bl.full_title
                book_list_id = bl.pk
                q.put({"type": "progress", "done": done, "total": total, "current": title})

                log_messages = []
                def progress_cb(msg, _title=title):
                    log_messages.append(msg)
                    # 단계 메시지를 step 이벤트로 실시간 전송
                    q.put({"type": "step", "title": _title, "step": msg})

                try:
                    candidates = run_booklist_pipeline(
                        bl, progress_callback=progress_cb, return_candidates=True
                    )
                    with done_lock:
                        done += 1
                    q.put({"type": "log", "status": "success",
                           "title": title, "messages": log_messages})
                    # 후보가 2개 이상이면 도서 선택 이벤트 emit
                    if candidates and len(candidates) > 1:
                        q.put({
                            "type": "candidates",
                            "title": title,
                            "book_list_id": book_list_id,
                            "candidates": candidates,
                        })
                except Exception as e:
                    with done_lock:
                        errors += 1
                    q.put({"type": "log", "status": "error",
                           "title": title, "error": str(e), "messages": log_messages})

            # API Rate Limit을 고려해 최대 3개 병렬 처리
            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = {executor.submit(_process_one, bl): bl for bl in book_lists}
                for future in as_completed(futures):
                    future.result()  # 예외 재전파 방지 (내부에서 처리됨)
                    if cancel_event.is_set():
                        executor.shutdown(wait=False, cancel_futures=True)
                        q.put({"type": "cancelled", "done": done, "total": total})
                        return

            if not cancel_event.is_set():
                q.put({"type": "complete", "done": done, "errors": errors, "total": total})
        except Exception as e:
            q.put({"type": "fatal", "error": str(e)})


def pipeline_stream(request, job_id: str):
    """SSE 스트림 — 파이프라인 진행 상황."""
    return _pipeline_store.get_response(job_id)


# ── 임베딩 누락 도서 처리 ─────────────────────────────────────

@_staff_required
@require_POST
def run_embed_missing(request):
    """description 있는 모든 도서의 임베딩을 생성·갱신합니다 (SSE 방식)."""
    return _start_job(
        request, _embed_store, "이미 임베딩이 진행 중입니다.",
        worker_fn=_embed_worker,
        targets_fn=lambda: list(BookList.objects.exclude(description="").order_by("title")),
        empty_message="임베딩할 도서가 없습니다.",
    )


def _embed_worker(job_id: str, q: Queue, targets: list, cancel_event: threading.Event):
    with _job_lifecycle(job_id, q, _embed_store):
        try:
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


def embed_stream(request, job_id: str):
    """SSE 스트림 — 임베딩 진행 상황."""
    return _embed_store.get_response(job_id)


# ── 난이도 분류 ───────────────────────────────────────────────

@_staff_required
@require_POST
def run_classify(request):
    """description 있는 전체 도서를 SSE 방식으로 일괄 분류 (DB 저장 없이 결과 반환)."""
    return _start_job(
        request, _classify_store, "이미 난이도 분류가 진행 중입니다.",
        worker_fn=_classify_worker,
        targets_fn=lambda: list(BookList.objects.exclude(description="").order_by("title")),
        empty_message="분류할 도서가 없습니다.",
    )


def _classify_worker(job_id: str, q: Queue, targets: list, cancel_event: threading.Event):
    """분류 결과를 DB에 바로 저장하지 않고 preview 이벤트로 반환한다."""
    with _job_lifecycle(job_id, q, _classify_store):
        try:
            total = len(targets)
            q.put({"type": "start", "total": total})

            done = errors = 0
            results = []
            for bl in targets:
                if cancel_event.is_set():
                    q.put({"type": "cancelled", "done": done, "total": total})
                    break

                q.put({"type": "progress", "done": done, "total": total, "current": bl.title})
                old_difficulty = bl.difficulty
                try:
                    difficulty = classify_difficulty(bl.title, bl.description, [])
                    if difficulty and difficulty in BookList.Difficulty.values:
                        results.append({
                            "book_list_id": bl.id,
                            "title": bl.title,
                            "old_difficulty": old_difficulty,
                            "new_difficulty": difficulty,
                        })
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
                time.sleep(0.5)

            if not cancel_event.is_set():
                q.put({"type": "preview", "done": done, "errors": errors,
                       "total": total, "results": results})
        except Exception as e:
            q.put({"type": "fatal", "error": str(e)})


def classify_stream(request, job_id: str):
    """SSE 스트림 — 난이도 분류 진행 상황."""
    return _classify_store.get_response(job_id)


@_staff_required
@require_POST
def classify_apply(request):
    """선택된 분류 결과를 DB에 반영한다."""
    data = json.loads(request.body)
    items = data.get("items", [])
    applied = 0
    for item in items:
        bl_id = item.get("book_list_id")
        diff = item.get("difficulty")
        if not bl_id or diff not in BookList.Difficulty.values:
            continue
        BookList.objects.filter(pk=bl_id).update(difficulty=diff)
        applied += 1
    return JsonResponse({"ok": True, "applied": applied})


# ── 출판 연도 추출 ────────────────────────────────────────────

@_staff_required
@require_POST
def run_extract_year(request):
    """전체 도서의 출판 연도를 제목·설명에서 추출하여 저장."""
    return _start_job(
        request, _year_store, "이미 연도 추출이 진행 중입니다.",
        worker_fn=_year_worker,
        targets_fn=lambda: list(BookList.objects.all().order_by("title")),
        empty_message="도서가 없습니다.",
    )


def _year_worker(job_id: str, q: Queue, targets: list, cancel_event: threading.Event):
    with _job_lifecycle(job_id, q, _year_store):
        try:
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


def year_stream(request, job_id: str):
    """SSE 스트림 — 연도 추출 진행 상황."""
    return _year_store.get_response(job_id)


# ── 카테고리 분류 ─────────────────────────────────────────────

@_staff_required
@require_POST
def run_classify_category(request):
    """description 있는 전체 도서를 SSE 방식으로 일괄 분류 (DB 저장 없이 결과 반환)."""
    return _start_job(
        request, _category_store, "이미 카테고리 분류가 진행 중입니다.",
        worker_fn=_category_worker,
        targets_fn=lambda: list(
            BookList.objects.exclude(description="")
            .prefetch_related("categories").order_by("title")
        ),
        empty_message="분류할 도서가 없습니다.",
    )


def _category_worker(job_id: str, q: Queue, targets: list, cancel_event: threading.Event):
    """분류 결과를 DB에 바로 저장하지 않고 preview 이벤트로 반환한다."""
    with _job_lifecycle(job_id, q, _category_store):
        try:
            total = len(targets)
            q.put({"type": "start", "total": total})

            done = errors = 0
            results = []
            for bl in targets:
                if cancel_event.is_set():
                    q.put({"type": "cancelled", "done": done, "total": total})
                    break

                q.put({"type": "progress", "done": done, "total": total, "current": bl.title})
                old_categories = list(bl.categories.values_list("name", flat=True))
                try:
                    category_names = classify_category(bl.title, bl.description)
                    if not category_names:
                        errors += 1
                        q.put({"type": "log", "status": "error",
                               "title": bl.title, "error": "카테고리 반환 없음"})
                        time.sleep(0.5)
                        continue

                    results.append({
                        "book_list_id": bl.id,
                        "title": bl.title,
                        "old_categories": old_categories,
                        "new_categories": category_names,
                    })
                    done += 1
                    q.put({"type": "log", "status": "success",
                           "title": bl.title, "categories": category_names})
                except Exception as e:
                    errors += 1
                    q.put({"type": "log", "status": "error",
                           "title": bl.title, "error": str(e)})
                time.sleep(0.5)

            if not cancel_event.is_set():
                q.put({"type": "preview", "done": done, "errors": errors,
                       "total": total, "results": results})
        except Exception as e:
            q.put({"type": "fatal", "error": str(e)})


def category_stream(request, job_id: str):
    """SSE 스트림 — 카테고리 분류 진행 상황."""
    return _category_store.get_response(job_id)


@_staff_required
@require_POST
def category_apply(request):
    """선택된 카테고리 분류 결과를 DB에 반영한다."""
    data = json.loads(request.body)
    items = data.get("items", [])
    applied = 0
    for item in items:
        bl_id = item.get("book_list_id")
        category_names = item.get("categories", [])
        if not bl_id or not category_names:
            continue
        try:
            bl = BookList.objects.get(pk=bl_id)
            bl.categories.clear()
            for name in category_names:
                cat, _ = Category.objects.get_or_create(name=name)
                BookListCategory.objects.get_or_create(book_list=bl, category=cat)
            applied += 1
        except BookList.DoesNotExist:
            continue
    return JsonResponse({"ok": True, "applied": applied})


# ── 도서 후보 선택 (교보문고) ──────────────────────────────────

@_staff_required
@require_POST
def search_book_candidates(request):
    """특정 book_list의 교보문고 후보 도서 목록 반환 (AJAX)."""
    try:
        body = json.loads(request.body or "{}")
    except Exception:
        body = {}

    book_list_id = body.get("book_list_id")
    if not book_list_id:
        return JsonResponse({"error": "book_list_id 필요"}, status=400)

    try:
        bl = BookList.objects.get(pk=book_list_id)
        candidates = fetch_kyobo_candidates(bl.title, max_results=5)
        return JsonResponse({"candidates": candidates})
    except BookList.DoesNotExist:
        return JsonResponse({"error": "도서 없음"}, status=404)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)


@_staff_required
@require_POST
def apply_book_candidate(request):
    """선택한 교보문고 후보 도서 정보를 BookList에 저장 (AJAX)."""
    try:
        body = json.loads(request.body or "{}")
    except Exception:
        body = {}

    book_list_id = body.get("book_list_id")
    href = body.get("href")
    if not book_list_id or not href:
        return JsonResponse({"error": "book_list_id, href 필요"}, status=400)

    try:
        bl = BookList.objects.get(pk=book_list_id)
        info = fetch_kyobo_book_detail(href)
        if not info:
            return JsonResponse({"error": "교보문고에서 도서 정보를 가져올 수 없습니다."}, status=404)

        update_fields = []
        if info.get("thumbnail_url"):
            bl.thumbnail_url = info["thumbnail_url"]
            update_fields.append("thumbnail_url")
        if info.get("description"):
            bl.description = info["description"]
            update_fields.append("description")
        if info.get("toc"):
            bl.toc = info["toc"]
            update_fields.append("toc")
        bl.source_url = href
        update_fields.append("source_url")

        if bl.thumbnail_url and (info.get("thumbnail_url") or not bl.thumbnail):
            if save_thumbnail(bl, bl.thumbnail_url):
                update_fields.append("thumbnail")

        if update_fields:
            bl.save(update_fields=update_fields)

        return JsonResponse({"ok": True, "updated_fields": update_fields})
    except BookList.DoesNotExist:
        return JsonResponse({"error": "도서 없음"}, status=404)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)


# ── 교보문고 후보 수집 파이프라인 ─────────────────────────────

@_staff_required
@require_POST
def run_kyobo_pipeline(request):
    """교보문고 검색 1차 수집 — 도서별 후보 목록을 DB에 저장하고 job_id 반환.

    body.resume (bool, default True): True이면 이미 후보가 있는 도서는 건너뜀.
    """
    body = json.loads(request.body or "{}")
    resume = body.get("resume", True)
    return _start_job(
        request, _kyobo_store, "이미 교보문고 수집이 진행 중입니다.",
        worker_fn=_kyobo_pipeline_worker,
        worker_args=(resume,),
    )


def _kyobo_pipeline_worker(job_id: str, q: Queue, cancel_event: threading.Event, resume: bool = True):
    with _job_lifecycle(job_id, q, _kyobo_store, invalidate_stats=True):
        try:
            all_book_lists = list(BookList.objects.select_related("author").order_by("title"))

            if resume:
                # 이미 후보가 수집된 도서는 건너뜀
                processed_ids = set(
                    KyoboCandidate.objects.values_list("book_list_id", flat=True).distinct()
                )
                book_lists = [bl for bl in all_book_lists if bl.id not in processed_ids]
                skipped = len(all_book_lists) - len(book_lists)
            else:
                book_lists = all_book_lists
                skipped = 0

            total = len(book_lists)
            q.put({"type": "start", "total": total, "skipped": skipped})

            done = errors = 0
            for bl in book_lists:
                if cancel_event.is_set():
                    q.put({"type": "cancelled", "done": done, "total": total})
                    break

                q.put({"type": "progress", "done": done, "total": total, "current": bl.full_title})
                try:
                    candidates = fetch_kyobo_candidates(bl.full_title)
                    # 기존 후보 초기화 후 새로 저장
                    KyoboCandidate.objects.filter(book_list=bl).delete()
                    for c in candidates:
                        KyoboCandidate.objects.create(
                            book_list=bl,
                            title=c["title"],
                            subtitle=c.get("subtitle", ""),
                            href=c["href"],
                            edition_info=c.get("edition_info", ""),
                        )
                    done += 1
                    q.put({
                        "type": "log", "status": "success",
                        "title": bl.full_title,
                        "count": len(candidates),
                    })
                except Exception as exc:
                    errors += 1
                    q.put({"type": "log", "status": "error",
                           "title": bl.full_title, "error": str(exc)})

                # rate limit
                time.sleep(0.5)

            if not cancel_event.is_set():
                q.put({"type": "complete", "done": done, "errors": errors, "total": total})
        except Exception as exc:
            q.put({"type": "fatal", "error": str(exc)})


def kyobo_pipeline_stream(request, job_id: str):
    return _kyobo_store.get_response(job_id)


# ── 교보문고 후보 관리 ────────────────────────────────────────

@_staff_required
def get_kyobo_candidates(request):
    """저장된 교보문고 후보 목록을 book_list 단위로 반환."""
    raw = (
        KyoboCandidate.objects
        .select_related("book_list__author")
        .order_by("book_list__title", "created_at")
    )

    groups: dict[int, dict] = {}
    for cand in raw:
        bl = cand.book_list
        if bl.pk not in groups:
            # 해당 book_list에 연결된 book_code 수집
            codes = list(bl.books.values_list("book_code", flat=True))
            groups[bl.pk] = {
                "book_list_id": bl.pk,
                "book_title":   bl.full_title,
                "book_codes":   codes,
                "candidates":   [],
            }
        groups[bl.pk]["candidates"].append({
            "id":           cand.pk,
            "title":        cand.title,
            "subtitle":     cand.subtitle,
            "href":         cand.href,
            "edition_info": cand.edition_info,
        })

    return JsonResponse({"groups": list(groups.values())})


@_staff_required
@require_POST
def apply_kyobo_candidate(request):
    """선택한 교보문고 후보의 상세 페이지를 수집하여 BookList에 덮어씁니다 (2-3).
    후보 적용 후 해당 book_list의 후보 전체 삭제 (2-4).
    """
    body = json.loads(request.body or "{}")
    book_list_id = body.get("book_list_id")
    href = body.get("href")
    if not book_list_id or not href:
        return JsonResponse({"error": "book_list_id, href 필요"}, status=400)

    try:
        bl = BookList.objects.select_related("author", "publisher").get(pk=book_list_id)
    except BookList.DoesNotExist:
        return JsonResponse({"error": "도서 없음"}, status=404)

    info = fetch_kyobo_book_detail(href)
    if not info:
        return JsonResponse({"error": "교보문고 상세 수집 실패"}, status=500)

    update_fields = []
    prev_description = bl.description

    if info.get("title"):
        bl.title = info["title"].strip()
        update_fields.append("title")

    if "subtitle" in info:
        bl.subtitle = info["subtitle"].strip()
        update_fields.append("subtitle")

    if info.get("author"):
        author, _ = Author.objects.get_or_create(name=info["author"].strip())
        bl.author = author
        update_fields.append("author")

    if info.get("publisher"):
        publisher, _ = Publisher.objects.get_or_create(name=info["publisher"].strip())
        bl.publisher = publisher
        update_fields.append("publisher")

    if info.get("description"):
        bl.description = info["description"]
        update_fields.append("description")

    if info.get("toc"):
        bl.toc = info["toc"]
        update_fields.append("toc")

    if info.get("thumbnail_url"):
        bl.thumbnail_url = info["thumbnail_url"]
        update_fields.append("thumbnail_url")

    if info.get("edition_info"):
        bl.edition = info["edition_info"]
        update_fields.append("edition")

    bl.source_url = href
    update_fields.append("source_url")

    if update_fields:
        bl.save(update_fields=update_fields)

    # 썸네일 파일 갱신
    if info.get("thumbnail_url"):
        if bl.thumbnail:
            bl.thumbnail.delete(save=False)
        if save_thumbnail(bl, info["thumbnail_url"]):
            bl.save(update_fields=["thumbnail"])

    # 설명이 변경됐으면 임베딩 재생성 (백그라운드)
    if "description" in update_fields and bl.description:
        threading.Thread(target=refresh_embedding, args=(bl,), daemon=True).start()

    # 해당 book_list 후보 전체 삭제 (2-4)
    KyoboCandidate.objects.filter(book_list=bl).delete()
    _invalidate_pipeline_stats()

    return JsonResponse({"ok": True, "updated_fields": update_fields})


@_staff_required
@require_POST
def dismiss_kyobo_candidates(request):
    """해당 book_list의 교보문고 후보를 적용 없이 삭제합니다."""
    body = json.loads(request.body or "{}")
    book_list_id = body.get("book_list_id")
    if not book_list_id:
        return JsonResponse({"error": "book_list_id 필요"}, status=400)

    deleted, _ = KyoboCandidate.objects.filter(book_list_id=book_list_id).delete()
    _invalidate_pipeline_stats()
    return JsonResponse({"ok": True, "deleted": deleted})


@_staff_required
@require_POST
def reset_kyobo_candidates(request):
    """수집된 교보문고 후보 전체를 초기화합니다."""
    deleted, _ = KyoboCandidate.objects.all().delete()
    _invalidate_pipeline_stats()
    return JsonResponse({"ok": True, "deleted": deleted})


# ── 미사용 썸네일 정리 ────────────────────────────────────────

def _get_orphan_thumbnails() -> list[str]:
    """스토리지의 thumbnails/ 디렉터리에서 DB에 참조되지 않는 파일 경로 목록을 반환합니다."""
    used = set(
        BookList.objects
        .exclude(thumbnail="")
        .values_list("thumbnail", flat=True)
    )
    try:
        _, filenames = default_storage.listdir("thumbnails/")
    except (FileNotFoundError, OSError):
        return []

    orphans = []
    for filename in filenames:
        path = f"thumbnails/{filename}"
        if path not in used:
            orphans.append(path)
    return orphans


@_staff_required
def scan_thumbnails(request):
    """미사용 썸네일 파일 목록을 반환합니다 (삭제하지 않음)."""
    orphans = _get_orphan_thumbnails()
    return JsonResponse({"count": len(orphans), "files": orphans})


@_staff_required
@require_POST
def clean_thumbnails(request):
    """미사용 썸네일 파일을 일괄 삭제합니다."""
    orphans = _get_orphan_thumbnails()
    deleted = []
    failed = []
    for path in orphans:
        try:
            default_storage.delete(path)
            deleted.append(path.split("/")[-1])
        except Exception as exc:
            logger.warning("썸네일 삭제 실패 (%s): %s", path, exc)
            failed.append(path.split("/")[-1])

    return JsonResponse({
        "deleted": len(deleted),
        "failed": len(failed),
        "files": deleted,
    })


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
