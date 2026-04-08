import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings

# LangSmith 트레이싱 환경변수 설정 (import 전에 적용)
os.environ.setdefault("LANGCHAIN_API_KEY", settings.LANGCHAIN_API_KEY)
os.environ.setdefault("LANGCHAIN_TRACING_V2", str(settings.LANGCHAIN_TRACING_V2).lower())
os.environ.setdefault("LANGCHAIN_PROJECT", settings.LANGCHAIN_PROJECT)

from routers import chat, embed  # noqa: E402

app = FastAPI(
    title="READ:ME Model Server",
    description="LangChain 기반 도서 추천 AI 서버",
    version="1.0.0",
)

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
