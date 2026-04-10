import json as _json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from chains.classifier import classify_question
from chains.roadmap import roadmap_chain, roadmap_chain_stream
from chains.level_based import level_based_chain, level_based_chain_stream
from chains.general import general_chain, general_chain_stream
from config import settings
from db import vector_search
from llm import get_embeddings

router = APIRouter()


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


def _build_chain_input(req: ChatRequest, retrieved: list) -> dict:
    return {
        "question": req.message,
        "history": [{"role": h.role, "content": h.content} for h in req.history],
        "books": retrieved,
    }


@router.post("/message", response_model=ChatResponse)
async def chat_message(req: ChatRequest):
    # 1) 질문 분류
    question_type = await classify_question(req.message)

    # 2) 도서 무관 처리
    if question_type == "unrelated":
        return ChatResponse(answer="도서와 관련없는 질문입니다.", question_type="unrelated")

    # 3) 질문 임베딩 → 벡터 검색
    q_embedding = await get_embeddings(req.message, input_type="query")
    retrieved = vector_search(
        query_embedding=q_embedding,
        threshold=settings.RECOMMENDATION_THRESHOLD,
        limit=settings.RECOMMENDATION_MAX,
    )

    # 4) 체인 선택 및 실행
    chain_input = _build_chain_input(req, retrieved)

    if question_type == "roadmap":
        answer = await roadmap_chain(chain_input)
    elif question_type == "level_based":
        answer = await level_based_chain(chain_input)
    else:
        answer = await general_chain(chain_input)

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

        # 2) 도서 무관 처리
        if question_type == "unrelated":
            yield f"data: {_json.dumps({'type': 'answer_chunk', 'content': '도서와 관련없는 질문입니다.'}, ensure_ascii=False)}\n\n"
            yield f"data: {_json.dumps({'type': 'done', 'question_type': 'unrelated', 'recommendations': []})}\n\n"
            return

        # 3) 벡터 검색
        q_embedding = await get_embeddings(req.message, input_type="query")
        retrieved = vector_search(
            query_embedding=q_embedding,
            threshold=settings.RECOMMENDATION_THRESHOLD,
            limit=settings.RECOMMENDATION_MAX,
        )

        # 4) 체인 스트리밍
        chain_input = _build_chain_input(req, retrieved)

        if question_type == "roadmap":
            stream_gen = roadmap_chain_stream(chain_input)
        elif question_type == "level_based":
            stream_gen = level_based_chain_stream(chain_input)
        else:
            stream_gen = general_chain_stream(chain_input)

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
