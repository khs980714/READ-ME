import json
import uuid
import logging

import httpx
from django.conf import settings
from django.http import JsonResponse
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

    history = list(
        session.messages.exclude(pk=user_msg.pk).order_by("-created_at")[:10].values("role", "content")
    )[::-1]

    try:
        resp = httpx.post(
            f"{settings.MODEL_SERVER_URL}/chat/message",
            json={"message": user_content, "history": history, "session_id": str(session.pk)},
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

    recommendations_out = []
    for rec in data.get("recommendations", []):
        # FastAPI는 book_list_id를 book_list_id 키로 반환
        book_list_id = rec.get("book_list_id")
        if not book_list_id:
            continue
        book = Book.objects.filter(book_list_id=book_list_id, is_active=True).select_related(
            "book_list__publisher", "book_list__author"
        ).first()
        if not book:
            continue
        ChatRecommendation.objects.create(
            message=assistant_msg,
            book=book,
            similarity_score=rec.get("score", 0.0),
            rank=rec.get("rank", 1),
        )
        recommendations_out.append({
            "id": book.pk,
            "title": book.book_list.title,
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
    response.set_cookie(SESSION_COOKIE, str(session.pk), max_age=60 * 60 * 24 * 30, httponly=True, samesite="Lax")
    return response
