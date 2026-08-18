import json as _json
import logging
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from openai import RateLimitError
from pydantic import BaseModel

from limiter import limiter

logger = logging.getLogger(__name__)

from chains.career_certification import career_certification_chain, career_certification_chain_stream
from chains.classifier import classify_question
from chains.goal_oriented import goal_oriented_chain, goal_oriented_chain_stream
from chains.keyword_search import keyword_search_chain, keyword_search_chain_stream
from chains.level_based import extract_difficulty_from_question, level_based_chain, level_based_chain_stream
from chains.retriever import retrieve_books, retrieve_books_by_keyword, retrieve_books_level_based
from chains.specific_search import specific_search_chain, specific_search_chain_stream
from chains.utils import normalize_question

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
    keyword: str | None = None,
) -> dict:
    return {
        "question": req.message,
        "history": [{"role": h.role, "content": h.content} for h in req.history],
        "books": retrieved,
        "detected_level": detected_level,
        "actual_level": actual_level,
        "fallback_note": fallback_note,
        "keyword": keyword or "",
    }


async def _classify_and_retrieve(req: ChatRequest) -> tuple[str, list, dict]:
    """분류 → 검색 → chain_input 빌드.

    Returns:
        (question_type, retrieved_books, chain_input)
        - out_of_scope인 경우 retrieved_books=[], chain_input={}
    """
    question = normalize_question(req.message)

    question_type = await classify_question(question)

    if question_type == "out_of_scope":
        return question_type, [], {}

    if question_type == "keyword_search":
        retrieved, keyword = await retrieve_books_by_keyword(question)
        chain_input = _build_chain_input(req, retrieved, keyword=keyword)
    elif question_type == "level_based":
        detected_level = extract_difficulty_from_question(question)
        retrieved, actual_level, fallback_note = await retrieve_books_level_based(
            question, detected_level
        )
        chain_input = _build_chain_input(req, retrieved, detected_level, actual_level, fallback_note)
    else:
        retrieved = await retrieve_books(question, question_type=question_type)
        chain_input = _build_chain_input(req, retrieved)

    # chain_input의 question도 정규화된 텍스트로 교체
    chain_input["question"] = question

    return question_type, retrieved, chain_input


async def _run_chain(question_type: str, chain_input: dict) -> str:
    """질문 유형에 맞는 체인을 실행해 응답 문자열을 반환합니다."""
    if question_type == "keyword_search":
        return keyword_search_chain(chain_input)
    elif question_type == "specific_search":
        return await specific_search_chain(chain_input)
    elif question_type == "goal_oriented":
        return await goal_oriented_chain(chain_input)
    elif question_type == "career_certification":
        return await career_certification_chain(chain_input)
    else:  # level_based
        return await level_based_chain(chain_input)


def _get_stream_gen(question_type: str, chain_input: dict):
    """질문 유형에 맞는 스트리밍 async generator를 반환합니다."""
    if question_type == "keyword_search":
        return keyword_search_chain_stream(chain_input)
    elif question_type == "specific_search":
        return specific_search_chain_stream(chain_input)
    elif question_type == "goal_oriented":
        return goal_oriented_chain_stream(chain_input)
    elif question_type == "career_certification":
        return career_certification_chain_stream(chain_input)
    else:  # level_based
        return level_based_chain_stream(chain_input)


# 도서 코드 패턴: 대문자+숫자 조합 + 하이픈 + 숫자 (예: IT-001, WEB-005, D-042)
_BOOK_CODE_RE = re.compile(r'\b([A-Z][A-Z0-9]*-\d+)\b')


def _reorder_by_mentioned_codes(answer: str, retrieved: list) -> list:
    """LLM 응답 텍스트에서 처음 언급된 도서 코드 순서로 retrieved를 재정렬.

    언급된 도서를 앞으로, 언급되지 않은 도서는 원래 순서를 유지하며 뒤에 배치.
    """
    seen: set[str] = set()
    mention_order: list[str] = []
    for m in _BOOK_CODE_RE.finditer(answer):
        code = m.group(1).upper()
        if code not in seen:
            seen.add(code)
            mention_order.append(code)

    if not mention_order:
        return retrieved

    order_index = {code: i for i, code in enumerate(mention_order)}
    upper_to_book = {(b.get("book_code") or "").upper(): b for b in retrieved}

    prioritized = sorted(
        [b for b in retrieved if (b.get("book_code") or "").upper() in order_index],
        key=lambda b: order_index[(b.get("book_code") or "").upper()],
    )
    rest = [b for b in retrieved if (b.get("book_code") or "").upper() not in order_index]
    return prioritized + rest


def _build_recommendation_dicts(retrieved: list) -> list[dict]:
    """재정렬된 도서 목록 → {book_list_id, score, rank} 딕셔너리 목록.

    비스트리밍(ChatResponse)·스트리밍(SSE done 이벤트) 양쪽에서 공유하는 변환 로직.
    """
    return [
        {"book_list_id": b["book_list_id"], "score": b["score"], "rank": i + 1}
        for i, b in enumerate(retrieved)
    ]


def _build_recommendations(retrieved: list) -> list[RecItem]:
    return [RecItem(**d) for d in _build_recommendation_dicts(retrieved)]


_RATE_LIMIT_MSG = "AI 서버가 잠시 바쁩니다. 잠시 후 다시 시도해주세요."


@router.post("/message", response_model=ChatResponse)
@limiter.limit("30/minute")
async def chat_message(request: Request, req: ChatRequest):
    try:
        # 1) 분류 → 검색
        question_type, retrieved, chain_input = await _classify_and_retrieve(req)

        # 2) 범위 외 질문 처리
        if question_type == "out_of_scope":
            return ChatResponse(answer=_OUT_OF_SCOPE_MSG, question_type="out_of_scope")

        # 3) 검색 결과 없음 처리 (keyword_search는 체인에서 직접 메시지 생성)
        if not retrieved and question_type != "keyword_search":
            return ChatResponse(answer=_NO_RESULTS_MSG, question_type=question_type)

        # 4) 체인 실행
        answer = await _run_chain(question_type, chain_input)

    except RateLimitError:
        logger.warning("NVIDIA NIM 429 Rate Limit")
        raise HTTPException(status_code=429, detail=_RATE_LIMIT_MSG)

    reordered = _reorder_by_mentioned_codes(answer, retrieved)
    return ChatResponse(
        answer=answer,
        question_type=question_type,
        recommendations=_build_recommendations(reordered),
    )


@router.post("/message/stream")
@limiter.limit("30/minute")
async def chat_message_stream(request: Request, req: ChatRequest):
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

        # 3) 검색 결과 없음 처리 (keyword_search는 체인에서 직접 메시지 생성)
        if not retrieved and question_type != "keyword_search":
            yield f"data: {_json.dumps({'type': 'answer_chunk', 'content': _NO_RESULTS_MSG}, ensure_ascii=False)}\n\n"
            yield f"data: {_json.dumps({'type': 'done', 'question_type': question_type, 'recommendations': []})}\n\n"
            return

        # 4) 체인 스트리밍 (응답 누적 후 코드 파싱에 사용)
        full_answer = ""
        try:
            async for chunk in _get_stream_gen(question_type, chain_input):
                if chunk:
                    full_answer += chunk
                    yield f"data: {_json.dumps({'type': 'answer_chunk', 'content': chunk}, ensure_ascii=False)}\n\n"
        except RateLimitError:
            logger.warning("NVIDIA NIM 429 Rate Limit (stream/chain)")
            yield f"data: {_json.dumps({'type': 'error', 'content': _RATE_LIMIT_MSG}, ensure_ascii=False)}\n\n"
            return

        # 5) 완료 + 추천 도서 (LLM이 언급한 코드 순서로 재정렬)
        reordered = _reorder_by_mentioned_codes(full_answer, retrieved)
        recommendations = _build_recommendation_dicts(reordered)
        yield f"data: {_json.dumps({'type': 'done', 'question_type': question_type, 'recommendations': recommendations})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
