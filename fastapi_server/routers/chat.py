import json as _json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from openai import RateLimitError
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from chains.career_certification import career_certification_chain, career_certification_chain_stream
from chains.classifier import classify_question
from chains.goal_oriented import goal_oriented_chain, goal_oriented_chain_stream
from chains.level_based import extract_difficulty_from_question, level_based_chain, level_based_chain_stream
from chains.retriever import retrieve_books, retrieve_books_level_based
from chains.specific_search import specific_search_chain, specific_search_chain_stream

router = APIRouter()

_OUT_OF_SCOPE_MSG = (
    "저는 IT·개발 및 자기개발 도서 추천 챗봇입니다. "
    "도서 추천, 학습 로드맵, 수준별 도서 안내 등 관련 질문을 해주시면 도와드리겠습니다!"
)

_NO_RESULTS_MSG = (
    "질문과 관련된 도서를 찾지 못했습니다. "
    "더 구체적인 기술명이나 주제어를 포함해서 다시 질문해 주세요."
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


def _build_chain_input(
    req: ChatRequest,
    retrieved: list,
    detected_level: str | None = None,
    actual_level: str | None = None,
    fallback_note: str | None = None,
) -> dict:
    return {
        "question": req.message,
        "history": [{"role": h.role, "content": h.content} for h in req.history],
        "books": retrieved,
        "detected_level": detected_level,
        "actual_level": actual_level,
        "fallback_note": fallback_note,
    }


async def _classify_and_retrieve(req: ChatRequest) -> tuple[str, list, dict]:
    """분류 → 벡터 검색 → chain_input 빌드.

    Returns:
        (question_type, retrieved_books, chain_input)
        - out_of_scope인 경우 retrieved_books=[], chain_input={}
    """
    question_type = await classify_question(req.message)

    if question_type == "out_of_scope":
        return question_type, [], {}

    if question_type == "level_based":
        detected_level = extract_difficulty_from_question(req.message)
        retrieved, actual_level, fallback_note = await retrieve_books_level_based(
            req.message, detected_level
        )
    else:
        detected_level = None
        actual_level = None
        fallback_note = None
        retrieved = await retrieve_books(req.message, question_type=question_type)

    chain_input = _build_chain_input(req, retrieved, detected_level, actual_level, fallback_note)
    return question_type, retrieved, chain_input


async def _run_chain(question_type: str, chain_input: dict) -> str:
    """질문 유형에 맞는 체인을 실행해 응답 문자열을 반환합니다."""
    if question_type == "specific_search":
        return await specific_search_chain(chain_input)
    elif question_type == "goal_oriented":
        return await goal_oriented_chain(chain_input)
    elif question_type == "career_certification":
        return await career_certification_chain(chain_input)
    else:  # level_based
        return await level_based_chain(chain_input)


def _get_stream_gen(question_type: str, chain_input: dict):
    """질문 유형에 맞는 스트리밍 async generator를 반환합니다."""
    if question_type == "specific_search":
        return specific_search_chain_stream(chain_input)
    elif question_type == "goal_oriented":
        return goal_oriented_chain_stream(chain_input)
    elif question_type == "career_certification":
        return career_certification_chain_stream(chain_input)
    else:  # level_based
        return level_based_chain_stream(chain_input)


def _build_recommendations(retrieved: list) -> list[RecItem]:
    return [
        RecItem(book_list_id=b["book_list_id"], score=b["score"], rank=i + 1)
        for i, b in enumerate(retrieved)
    ]


_RATE_LIMIT_MSG = "AI 서버가 잠시 바쁩니다. 잠시 후 다시 시도해주세요."


@router.post("/message", response_model=ChatResponse)
async def chat_message(req: ChatRequest):
    try:
        # 1) 분류 → 검색
        question_type, retrieved, chain_input = await _classify_and_retrieve(req)

        # 2) 범위 외 질문 처리
        if question_type == "out_of_scope":
            return ChatResponse(answer=_OUT_OF_SCOPE_MSG, question_type="out_of_scope")

        # 3) 검색 결과 없음 처리 (LLM 호출 없이 즉시 반환)
        if not retrieved:
            return ChatResponse(answer=_NO_RESULTS_MSG, question_type=question_type)

        # 4) 체인 실행
        answer = await _run_chain(question_type, chain_input)

    except RateLimitError:
        logger.warning("NVIDIA NIM 429 Rate Limit")
        raise HTTPException(status_code=429, detail=_RATE_LIMIT_MSG)

    return ChatResponse(
        answer=answer,
        question_type=question_type,
        recommendations=_build_recommendations(retrieved),
    )


@router.post("/message/stream")
async def chat_message_stream(req: ChatRequest):
    """SSE 스트리밍 응답 엔드포인트."""

    async def generate():
        try:
            # 1) 분류 → 검색
            question_type, retrieved, chain_input = await _classify_and_retrieve(req)
        except RateLimitError:
            logger.warning("NVIDIA NIM 429 Rate Limit (stream/classify)")
            yield f"data: {_json.dumps({'type': 'error', 'content': _RATE_LIMIT_MSG}, ensure_ascii=False)}\n\n"
            return

        # 2) 범위 외 질문 처리
        if question_type == "out_of_scope":
            yield f"data: {_json.dumps({'type': 'answer_chunk', 'content': _OUT_OF_SCOPE_MSG}, ensure_ascii=False)}\n\n"
            yield f"data: {_json.dumps({'type': 'done', 'question_type': 'out_of_scope', 'recommendations': []})}\n\n"
            return

        # 3) 검색 결과 없음 처리
        if not retrieved:
            yield f"data: {_json.dumps({'type': 'answer_chunk', 'content': _NO_RESULTS_MSG}, ensure_ascii=False)}\n\n"
            yield f"data: {_json.dumps({'type': 'done', 'question_type': question_type, 'recommendations': []})}\n\n"
            return

        # 4) 체인 스트리밍
        try:
            async for chunk in _get_stream_gen(question_type, chain_input):
                if chunk:
                    yield f"data: {_json.dumps({'type': 'answer_chunk', 'content': chunk}, ensure_ascii=False)}\n\n"
        except RateLimitError:
            logger.warning("NVIDIA NIM 429 Rate Limit (stream/chain)")
            yield f"data: {_json.dumps({'type': 'error', 'content': _RATE_LIMIT_MSG}, ensure_ascii=False)}\n\n"
            return

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
