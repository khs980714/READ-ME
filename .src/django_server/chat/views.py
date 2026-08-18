import json
import uuid
import logging

import httpx
from django.conf import settings
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from .models import ChatSession, ChatMessage, ChatRecommendation
from books.models import Book

logger = logging.getLogger(__name__)

SESSION_COOKIE = "readme_chat_session"


def _get_or_create_session(request) -> ChatSession:
    session_id = request.COOKIES.get(SESSION_COOKIE)
    if session_id:
        try:
            return ChatSession.objects.get(pk=uuid.UUID(session_id))
        except (ChatSession.DoesNotExist, ValueError):
            pass
    return ChatSession.objects.create()


def _set_session_cookie(response, session: ChatSession) -> None:
    response.set_cookie(SESSION_COOKIE, str(session.pk), max_age=60 * 60 * 24, httponly=True, samesite="Lax")


def _prepare_user_message(request):
    """요청 바디에서 사용자 메시지를 파싱하고 세션·사용자 메시지 레코드를 생성합니다.

    성공 시 (session, user_msg, content) 튜플을, 검증 실패 시 JsonResponse 에러 응답을 반환합니다.
    send_message(비스트리밍)와 stream_message가 공유하는 전처리 로직입니다.
    """
    try:
        body = json.loads(request.body)
        user_content = body.get("message", "").strip()
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "잘못된 요청입니다."}, status=400)

    if not user_content:
        return JsonResponse({"error": "메시지를 입력해주세요."}, status=400)

    session = _get_or_create_session(request)
    user_msg = ChatMessage.objects.create(session=session, role=ChatMessage.Role.USER, content=user_content)
    return session, user_msg, user_content


def chat_page(request):
    # 페이지 로드 시 항상 새 세션을 생성합니다.
    # httponly 쿠키는 JS에서 삭제할 수 없으므로, 세션 갱신은 서버에서 처리합니다.
    session = ChatSession.objects.create()
    response = render(request, "chat/chat.html", {"history": "[]"})
    _set_session_cookie(response, session)
    return response


@require_POST
def send_message(request):
    prepared = _prepare_user_message(request)
    if isinstance(prepared, JsonResponse):
        return prepared
    session, user_msg, user_content = prepared

    try:
        resp = httpx.post(
            f"{settings.MODEL_SERVER_URL}/chat/message",
            # 싱글턴: history 없이 전송
            json={"message": user_content, "history": [], "session_id": str(session.pk)},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        logger.error("FastAPI 호출 오류: %s", exc)
        return JsonResponse({"error": "AI 서버에 연결할 수 없습니다. 잠시 후 다시 시도해주세요."}, status=503)

    assistant_msg = ChatMessage.objects.create(
        session=session,
        role=ChatMessage.Role.ASSISTANT,
        content=data.get("answer", ""),
        question_type=data.get("question_type", ""),
    )

    raw_recs = data.get("recommendations", [])
    books_by_bl_id = _bulk_fetch_books(raw_recs)
    persisted_recs = _persist_recommendations(assistant_msg, raw_recs, books_by_bl_id)
    recommendations_out = _enrich_recommendations(persisted_recs, books_by_bl_id)

    response = JsonResponse({
        "answer": assistant_msg.content,
        "question_type": assistant_msg.question_type,
        "recommendations": recommendations_out,
    })
    _set_session_cookie(response, session)
    return response


@require_POST
def stream_message(request):
    """FastAPI SSE 스트림을 프록시하여 브라우저에 전달합니다."""
    prepared = _prepare_user_message(request)
    if isinstance(prepared, JsonResponse):
        return prepared
    session, user_msg, user_content = prepared

    # 스트리밍 중 누적할 응답
    accumulated = {"answer": "", "question_type": "", "recommendations": [], "books_by_bl_id": None}

    def generate():
        try:
            with httpx.stream(
                "POST",
                f"{settings.MODEL_SERVER_URL}/chat/message/stream",
                json={"message": user_content, "history": [], "session_id": str(session.pk)},
                timeout=httpx.Timeout(60.0, connect=10.0),
            ) as r:
                for line in r.iter_lines():
                    if not line:
                        continue

                    if line.startswith("data: "):
                        try:
                            event = json.loads(line[6:])
                            etype = event.get("type")
                            if etype == "answer_chunk":
                                accumulated["answer"] += event.get("content", "")
                                yield line + "\n\n"
                            elif etype == "done":
                                accumulated["question_type"] = event.get("question_type", "")
                                raw_recs = event.get("recommendations", [])
                                accumulated["recommendations"] = raw_recs
                                # 도서 상세 정보를 DB에서 조회하여 enriched done 이벤트 전송
                                # (이 조회 결과를 finally의 _save_stream_result에서 재사용 — 중복 조회 방지)
                                books_by_bl_id = _bulk_fetch_books(raw_recs)
                                accumulated["books_by_bl_id"] = books_by_bl_id
                                enriched = _enrich_recommendations(raw_recs, books_by_bl_id)
                                done_payload = json.dumps({
                                    "type": "done",
                                    "question_type": accumulated["question_type"],
                                    "recommendations": enriched,
                                }, ensure_ascii=False)
                                yield f"data: {done_payload}\n\n"
                            else:
                                yield line + "\n\n"
                        except Exception:
                            yield line + "\n\n"
                    else:
                        yield line + "\n\n"

        except Exception as exc:
            logger.error("FastAPI 스트리밍 오류: %s", exc)
            error_payload = json.dumps({"type": "error", "content": "AI 서버에 연결할 수 없습니다."})
            yield f"data: {error_payload}\n\n"
        finally:
            _save_stream_result(session, user_msg, accumulated)

    response = StreamingHttpResponse(generate(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    _set_session_cookie(response, session)
    return response


def _bulk_fetch_books(raw_recs: list) -> dict:
    """book_list_id 목록을 한 번에 조회하여 {book_list_id: Book} 딕셔너리로 반환."""
    book_list_ids = [r["book_list_id"] for r in raw_recs if r.get("book_list_id")]
    if not book_list_ids:
        return {}
    return {
        b.book_list_id: b
        for b in Book.objects.filter(book_list_id__in=book_list_ids, is_active=True)
        .select_related("book_list__publisher", "book_list__author")
    }


def _serialize_recommendation(book: Book, rec: dict) -> dict:
    """추천 도서 1건을 프런트엔드 응답 형식으로 직렬화합니다."""
    return {
        "id": book.pk,
        "book_code": book.book_code,
        "title": book.book_list.title,
        "edition": book.book_list.edition,
        "author": book.book_list.get_author_display(),
        "publisher": book.book_list.publisher.name,
        "difficulty": book.book_list.difficulty,
        "thumbnail_url": book.book_list.thumbnail_url,
        "rank": rec.get("rank", 1),
        "score": rec.get("score", 0.0),
    }


def _enrich_recommendations(raw_recs: list, books_by_bl_id: dict | None = None) -> list:
    """book_list_id → Book 일괄 조회 후 프런트엔드에 필요한 전체 필드 반환.

    books_by_bl_id를 미리 조회해 전달하면 재조회를 생략합니다.
    """
    if not raw_recs:
        return []
    if books_by_bl_id is None:
        books_by_bl_id = _bulk_fetch_books(raw_recs)
    result = []
    for rec in raw_recs:
        book = books_by_bl_id.get(rec.get("book_list_id"))
        if not book:
            continue
        result.append(_serialize_recommendation(book, rec))
    return result


def _persist_recommendations(assistant_msg: ChatMessage, raw_recs: list, books_by_bl_id: dict) -> list[dict]:
    """추천 도서를 ChatRecommendation으로 저장합니다.

    반환값은 저장(또는 이미 존재)에 성공한 raw rec 목록입니다 — 호출 측에서 이 목록을
    기준으로 사용자에게 보여줄 추천 목록을 구성하면 저장 실패한 항목은 자연히 제외됩니다.
    """
    persisted = []
    for rec in raw_recs:
        book_list_id = rec.get("book_list_id")
        if not book_list_id:
            logger.warning("추천 항목에 book_list_id 없음: %s", rec)
            continue
        book = books_by_bl_id.get(book_list_id)
        if not book:
            logger.warning("book_list_id=%s 에 해당하는 활성 Book 없음", book_list_id)
            continue
        try:
            ChatRecommendation.objects.get_or_create(
                message=assistant_msg,
                book=book,
                defaults={
                    "similarity_score": rec.get("score", 0.0),
                    "rank": rec.get("rank", 1),
                },
            )
        except Exception as exc:
            logger.error("추천 저장 오류 (book_list_id=%s): %s", book_list_id, exc)
            continue
        persisted.append(rec)
    return persisted


def _save_stream_result(session: ChatSession, user_msg: ChatMessage, accumulated: dict) -> None:
    """스트리밍 완료 후 어시스턴트 메시지와 추천 도서를 DB에 저장합니다."""
    try:
        assistant_msg = ChatMessage.objects.create(
            session=session,
            role=ChatMessage.Role.ASSISTANT,
            content=accumulated["answer"],
            question_type=accumulated["question_type"],
        )
    except Exception as exc:
        logger.error("어시스턴트 메시지 저장 오류: %s", exc)
        return

    raw_recs = accumulated.get("recommendations", [])
    if not raw_recs:
        return

    # done 이벤트 처리 시 이미 조회한 books_by_bl_id가 있으면 재사용 (N+1/중복 쿼리 방지)
    books_by_bl_id = accumulated.get("books_by_bl_id") or _bulk_fetch_books(raw_recs)
    _persist_recommendations(assistant_msg, raw_recs, books_by_bl_id)
