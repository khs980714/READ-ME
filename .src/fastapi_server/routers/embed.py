from fastapi import APIRouter
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from llm import get_embeddings, get_llm
from db import upsert_embedding
from langchain_core.messages import HumanMessage

from chains.utils import DIFFICULTY_ORDER

router = APIRouter()


def _is_rate_limit(exc: BaseException) -> bool:
    msg = str(exc)
    return "429" in msg or "Too Many Requests" in msg or "RateLimitError" in type(exc).__name__


_retry_on_rate_limit = retry(
    retry=retry_if_exception(_is_rate_limit),
    wait=wait_exponential(multiplier=2, min=4, max=60),
    stop=stop_after_attempt(5),
    reraise=True,
)


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
    await upsert_embedding(req.book_list_id, embedding)
    return EmbedBookResponse(book_list_id=req.book_list_id, status="ok")


async def _call_llm(prompt: str) -> str:
    """Rate-limit 재시도 포함 LLM 단일 호출. 응답 텍스트를 반환."""
    llm = get_llm()

    @_retry_on_rate_limit
    async def _invoke():
        return await llm.ainvoke([HumanMessage(content=prompt)])

    response = await _invoke()
    return response.content.strip()


@router.post("/classify", response_model=ClassifyResponse)
async def classify_difficulty(req: ClassifyRequest):
    """리뷰 + 도서 정보를 LLM으로 분석하여 난이도 반환."""
    reviews_text = "\n".join(f"- {r}" for r in req.reviews[:10]) if req.reviews else "리뷰 없음"
    prompt = DIFFICULTY_PROMPT.format(
        title=req.title,
        description=req.description[:500],
        reviews=reviews_text,
    )
    difficulty = await _call_llm(prompt)

    for v in DIFFICULTY_ORDER:
        if v in difficulty:
            return ClassifyResponse(difficulty=v)

    return ClassifyResponse(difficulty="중급")


# ── 카테고리 분류 ──────────────────────────────────────────────

VALID_CATEGORIES = [
    "프로그래밍 언어",
    "웹 개발",
    "모바일 개발",
    "데이터베이스",
    "자료구조·알고리즘",
    "컴퓨터 과학",
    "인공지능·데이터",
    "DevOps·클라우드",
    "소프트웨어 공학",
    "보안",
    "자격증·취업",
    "IT 교양",
]

CATEGORY_PROMPT = """다음 IT·개발 도서를 아래 카테고리 목록에서 가장 적합한 1~3개로 분류해주세요.
반드시 아래 목록에 있는 카테고리명만 사용하고, 쉼표로 구분하여 답하세요. 설명 없이 카테고리명만 출력하세요.

카테고리 목록:
{categories}

도서 제목: {title}
도서 소개: {description}

카테고리 (쉼표 구분):"""


class ClassifyCategoryRequest(BaseModel):
    title: str
    description: str


class ClassifyCategoryResponse(BaseModel):
    categories: list[str]


@router.post("/classify-category", response_model=ClassifyCategoryResponse)
async def classify_category(req: ClassifyCategoryRequest):
    """도서 정보를 LLM으로 분석하여 카테고리 목록 반환 (1~3개)."""
    categories_str = "\n".join(f"- {c}" for c in VALID_CATEGORIES)
    prompt = CATEGORY_PROMPT.format(
        categories=categories_str,
        title=req.title,
        description=req.description[:500],
    )
    raw = await _call_llm(prompt)

    result = [
        name.strip()
        for token in raw.replace("、", ",").split(",")
        if (name := token.strip()) in VALID_CATEGORIES
    ]

    # LLM이 유효 카테고리를 하나도 못 반환하면 IT 교양으로 fallback
    return ClassifyCategoryResponse(categories=result if result else ["IT 교양"])
