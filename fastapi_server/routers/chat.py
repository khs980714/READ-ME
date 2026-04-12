import json as _json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from chains.classifier import classify_question
from chains.specific_search import specific_search_chain, specific_search_chain_stream
from chains.goal_oriented import goal_oriented_chain, goal_oriented_chain_stream
from chains.career_certification import career_certification_chain, career_certification_chain_stream
from chains.level_based import level_based_chain, level_based_chain_stream, extract_difficulty_from_question
from config import settings
from db import vector_search, vector_search_by_difficulty
from llm import get_embeddings

router = APIRouter()

_OUT_OF_SCOPE_MSG = (
    "저는 IT·개발 및 자기개발 도서 추천 챗봇입니다. "
    "도서 추천, 학습 로드맵, 수준별 도서 안내 등 관련 질문을 해주시면 도와드리겠습니다!"
)


class HistoryItem(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[HistoryItem] = []
    session_id: str = ""


class RecItem(BaseModel):
    book_list_id: int
    score: float
    rank: int


class ChatResponse(BaseModel):
    answer: str
    question_type: str
    recommendations: list[RecItem] = []


def _build_chain_input(req: ChatRequest, retrieved: list, detected_level: str | None = None) -> dict:
    return {
        "question": req.message,
        "history": [{"role": h.role, "content": h.content} for h in req.history],
        "books": retrieved,
        "detected_level": detected_level,
    }


_THRESHOLD_MAP = {
    "specific_search": lambda: settings.RECOMMENDATION_THRESHOLD_SPECIFIC_SEARCH,
    "goal_oriented": lambda: settings.RECOMMENDATION_THRESHOLD_GOAL_ORIENTED,
    "career_certification": lambda: settings.RECOMMENDATION_THRESHOLD_CAREER_CERTIFICATION,
    "level_based": lambda: settings.RECOMMENDATION_THRESHOLD_LEVEL_BASED,
}


async def _retrieve_books(message: str, question_type: str, difficulty: str | None = None) -> list:
    """질문 임베딩 → 벡터 검색.
    - level_based: 난이도 필터 적용
    - 질문 유형별 threshold 사용
    """
    threshold = _THRESHOLD_MAP.get(question_type, lambda: settings.RECOMMENDATION_THRESHOLD_SPECIFIC_SEARCH)()
    q_embedding = await get_embeddings(message, input_type="query")
    if difficulty:
        return vector_search_by_difficulty(
            query_embedding=q_embedding,
            threshold=threshold,
            limit=settings.RECOMMENDATION_MAX,
            difficulty=difficulty,
        )
    return vector_search(
        query_embedding=q_embedding,
        threshold=threshold,
        limit=settings.RECOMMENDATION_MAX,
    )


@router.post("/message", response_model=ChatResponse)
async def chat_message(req: ChatRequest):
    # 1) 질문 분류
    question_type = await classify_question(req.message)

    # 2) 범위 외 질문 처리 (DB 검색 없음)
    if question_type == "out_of_scope":
        return ChatResponse(answer=_OUT_OF_SCOPE_MSG, question_type="out_of_scope")

    # 3) level_based: 난이도 추출 후 필터링 검색
    detected_level = None
    if question_type == "level_based":
        detected_level = extract_difficulty_from_question(req.message)

    retrieved = await _retrieve_books(req.message, question_type=question_type, difficulty=detected_level)

    # 4) 체인 선택 및 실행
    chain_input = _build_chain_input(req, retrieved, detected_level)

    if question_type == "specific_search":
        answer = await specific_search_chain(chain_input)
    elif question_type == "goal_oriented":
        answer = await goal_oriented_chain(chain_input)
    elif question_type == "career_certification":
        answer = await career_certification_chain(chain_input)
    else:  # level_based
        answer = await level_based_chain(chain_input)

    # 5) 추천 도서 랭킹
    recommendations = [
        RecItem(book_list_id=b["book_list_id"], score=b["score"], rank=i + 1)
        for i, b in enumerate(retrieved)
    ]

    return ChatResponse(answer=answer, question_type=question_type, recommendations=recommendations)


@router.post("/message/stream")
async def chat_message_stream(req: ChatRequest):
    """SSE 스트리밍 응답 엔드포인트."""

    async def generate():
        # 1) 질문 분류
        question_type = await classify_question(req.message)

        # 2) 범위 외 질문 처리 (DB 검색 없음)
        if question_type == "out_of_scope":
            yield f"data: {_json.dumps({'type': 'answer_chunk', 'content': _OUT_OF_SCOPE_MSG}, ensure_ascii=False)}\n\n"
            yield f"data: {_json.dumps({'type': 'done', 'question_type': 'out_of_scope', 'recommendations': []})}\n\n"
            return

        # 3) level_based: 난이도 추출 후 필터링 검색
        detected_level = None
        if question_type == "level_based":
            detected_level = extract_difficulty_from_question(req.message)

        retrieved = await _retrieve_books(req.message, question_type=question_type, difficulty=detected_level)

        # 4) 체인 스트리밍
        chain_input = _build_chain_input(req, retrieved, detected_level)

        if question_type == "specific_search":
            stream_gen = specific_search_chain_stream(chain_input)
        elif question_type == "goal_oriented":
            stream_gen = goal_oriented_chain_stream(chain_input)
        elif question_type == "career_certification":
            stream_gen = career_certification_chain_stream(chain_input)
        else:  # level_based
            stream_gen = level_based_chain_stream(chain_input)

        async for chunk in stream_gen:
            if chunk:
                yield f"data: {_json.dumps({'type': 'answer_chunk', 'content': chunk}, ensure_ascii=False)}\n\n"

        # 5) 완료 + 추천 도서
        recommendations = [
            {"book_list_id": b["book_list_id"], "score": b["score"], "rank": i + 1}
            for i, b in enumerate(retrieved)
        ]
        yield f"data: {_json.dumps({'type': 'done', 'question_type': question_type, 'recommendations': recommendations})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
