from fastapi import APIRouter
from pydantic import BaseModel

from llm import get_embeddings, get_llm
from db import upsert_embedding
from langchain_core.messages import HumanMessage

router = APIRouter()


class EmbedBookRequest(BaseModel):
    book_list_id: int
    title: str
    description: str


class EmbedBookResponse(BaseModel):
    book_list_id: int
    status: str


class ClassifyRequest(BaseModel):
    title: str
    description: str
    reviews: list[str] = []


class ClassifyResponse(BaseModel):
    difficulty: str


DIFFICULTY_PROMPT = """다음 도서의 정보와 독자 리뷰를 분석하여 난이도를 판단해주세요.
난이도는 반드시 다음 4가지 중 하나만 답하세요: 입문, 초급, 중급, 고급

도서 제목: {title}
도서 소개: {description}
독자 리뷰:
{reviews}

난이도 (입문/초급/중급/고급 중 하나만):"""


@router.post("/book", response_model=EmbedBookResponse)
async def embed_book(req: EmbedBookRequest):
    """도서 제목+설명을 임베딩하여 pgvector에 저장 (book_list 단위).

    llm.get_embeddings 내부에서 8,000자 한도가 적용됩니다.
    """
    text = f"{req.title}\n{req.description}".strip()
    embedding = await get_embeddings(text)
    upsert_embedding(req.book_list_id, embedding)
    return EmbedBookResponse(book_list_id=req.book_list_id, status="ok")


@router.post("/classify", response_model=ClassifyResponse)
async def classify_difficulty(req: ClassifyRequest):
    """리뷰 + 도서 정보를 LLM으로 분석하여 난이도 반환."""
    reviews_text = "\n".join(f"- {r}" for r in req.reviews[:10]) if req.reviews else "리뷰 없음"
    prompt = DIFFICULTY_PROMPT.format(
        title=req.title,
        description=req.description[:500],
        reviews=reviews_text,
    )
    llm = get_llm()
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    difficulty = response.content.strip()

    valid = ("입문", "초급", "중급", "고급")
    for v in valid:
        if v in difficulty:
            return ClassifyResponse(difficulty=v)

    return ClassifyResponse(difficulty="중급")
