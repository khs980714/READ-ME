import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from config import settings
from limiter import limiter

# LangSmith 트레이싱 환경변수 설정 (import 전에 적용)
os.environ.setdefault("LANGCHAIN_API_KEY", settings.LANGCHAIN_API_KEY)
os.environ.setdefault("LANGCHAIN_TRACING_V2", str(settings.LANGCHAIN_TRACING_V2).lower())
os.environ.setdefault("LANGCHAIN_PROJECT", settings.LANGCHAIN_PROJECT)

from routers import chat, embed  # noqa: E402


def _verify_embedding_dim() -> None:
    """DB의 book_embeddings 컬럼 차원이 config.EMBEDDING_DIM과 일치하는지 검증합니다.

    불일치 시 임베딩 저장·검색이 모두 실패하므로 서버 시작 단계에서 조기에 감지합니다.
    book_embeddings 테이블이 비어 있으면 검증을 건너뜁니다.
    """
    import logging
    from db import get_conn

    logger = logging.getLogger(__name__)
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT vector_dims(embedding) FROM book_embeddings LIMIT 1;"
                )
                row = cur.fetchone()
                if row is None:
                    logger.info("book_embeddings 테이블이 비어 있어 차원 검증을 건너뜁니다.")
                    return
                actual_dim = row[0]
                expected_dim = settings.EMBEDDING_DIM
                if actual_dim != expected_dim:
                    raise RuntimeError(
                        f"임베딩 차원 불일치: DB={actual_dim}, config={expected_dim}. "
                        "마이그레이션 또는 EMBEDDING_DIM 설정을 확인하세요."
                    )
                logger.info("임베딩 차원 검증 완료: %d차원", actual_dim)
    except RuntimeError:
        raise
    except Exception as exc:
        logger.warning("임베딩 차원 검증 중 오류 (무시됨): %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 서버 시작 시 커넥션 풀 초기화 (첫 요청 지연 제거)
    from db import _get_pool
    _get_pool()
    # 임베딩 차원이 DB와 config 간 일치하는지 검증
    _verify_embedding_dim()
    yield
    # 서버 종료 시 커넥션 풀 해제
    from db import _pool
    if _pool is not None:
        _pool.closeall()


app = FastAPI(
    title="READ:ME Model Server",
    description="LangChain 기반 도서 추천 AI 서버",
    version="1.0.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router, prefix="/chat", tags=["chat"])
app.include_router(embed.router, prefix="/embed", tags=["embed"])


@app.get("/health")
def health():
    return {"status": "ok"}
