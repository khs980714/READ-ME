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


def chat_page(request):
    # 페이지 로드 시 항상 새 세션을 생성합니다.
    # httponly 쿠키는 JS에서 삭제할 수 없으므로, 세션 갱신은 서버에서 처리합니다.
    session = ChatSession.objects.create()
    response = render(request, "chat/chat.html", {"history": "[]"})
    response.set_cookie(SESSION_COOKIE, str(session.pk), max_age=60 * 60 * 24, httponly=True, samesite="Lax")
    return response


@require_POST
def send_message(request):
    try:
        body = json.loads(request.body)
        user_content = body.get("message", "").strip()
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "잘못된 요청입니다."}, status=400)

    if not user_content:
        return JsonResponse({"error": "메시지를 입력해주세요."}, status=400)

    session = _get_or_create_session(request)
    user_msg = ChatMessage.objects.create(session=session, role=ChatMessage.Role.USER, content=user_content)

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
    recommendations_out = []
    for rec in raw_recs:
        book_list_id = rec.get("book_list_id")
        if not book_list_id:
            continue
        book = books_by_bl_id.get(book_list_id)
        if not book:
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
        recommendations_out.append({
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
        })

    response = JsonResponse({
        "answer": assistant_msg.content,
        "question_type": assistant_msg.question_type,
        "recommendations": recommendations_out,
    })
    response.set_cookie(SESSION_COOKIE, str(session.pk), max_age=60 * 60 * 24, httponly=True, samesite="Lax")
    return response


@require_POST
def stream_message(request):
    """FastAPI SSE 스트림을 프록시하여 브라우저에 전달합니다."""
    try:
        body = json.loads(request.body)
        user_content = body.get("message", "").strip()
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "잘못된 요청입니다."}, status=400)

    if not user_content:
        return JsonResponse({"error": "메시지를 입력해주세요."}, status=400)

    session = _get_or_create_session(request)
    user_msg = ChatMessage.objects.create(session=session, role=ChatMessage.Role.USER, content=user_content)

    # 스트리밍 중 누적할 응답
    accumulated = {"answer": "", "question_type": "", "recommendations": []}

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
                                enriched = _enrich_recommendations(raw_recs)
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
    response.set_cookie(SESSION_COOKIE, str(session.pk), max_age=60 * 60 * 24, httponly=True, samesite="Lax")
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


def _enrich_recommendations(raw_recs: list) -> list:
    """book_list_id → Book 일괄 조회 후 프런트엔드에 필요한 전체 필드 반환."""
    if not raw_recs:
        return []
    books_by_bl_id = _bulk_fetch_books(raw_recs)
    result = []
    for rec in raw_recs:
        book = books_by_bl_id.get(rec.get("book_list_id"))
        if not book:
            continue
        result.append({
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
        })
    return result


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

    # book_list_id 목록을 한 번에 조회하여 N+1 쿼리 방지
    book_list_ids = [r["book_list_id"] for r in raw_recs if r.get("book_list_id")]
    books_by_bl_id = {
        b.book_list_id: b
        for b in Book.objects.filter(book_list_id__in=book_list_ids, is_active=True)
    }

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
