"""
LLM / Embedding 클라이언트 — NVIDIA NIM (OpenAI 호환 엔드포인트)
- LLM:       NVIDIA_LLM_MODEL       (예: meta/llama-3.1-70b-instruct)
- Embedding: NVIDIA_EMBEDDING_MODEL (예: nvidia/nv-embedqa-e5-v5)
"""

import logging
from functools import lru_cache

from langchain_openai import ChatOpenAI
from openai import AsyncOpenAI

from config import settings

logger = logging.getLogger(__name__)

# ── 토큰 한도 설정 ────────────────────────────────────────────
# llama-3.2-nv-embedqa-1b-v2 의 최대 시퀀스 길이는 8,192 토큰.
# 한국어 음절 1자 ≈ 1 토큰 기준으로 안전 한도를 8,000자로 설정.
_MAX_EMBED_CHARS = 8_000


def _truncate(text: str, max_chars: int = _MAX_EMBED_CHARS) -> str:
    """토큰 한도 초과를 방지하기 위해 텍스트를 잘라냅니다.
    단어/문장 중간에 잘리지 않도록 마지막 공백 이전까지 유지합니다.
    """
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    # 단어 중간에 자르지 않도록 마지막 공백 기준으로 재조정
    last_space = truncated.rfind(" ")
    if last_space > max_chars * 0.8:  # 공백이 80% 이후에 있으면 사용
        truncated = truncated[:last_space]
    logger.debug("텍스트 잘림: %d → %d자 (토큰 한도 초과 방지)", len(text), len(truncated))
    return truncated


@lru_cache(maxsize=1)
def get_llm() -> ChatOpenAI:
    """NVIDIA NIM LLM (OpenAI 호환)."""
    return ChatOpenAI(
        model=settings.NVIDIA_LLM_MODEL,
        api_key=settings.NVIDIA_API_KEY,
        base_url=settings.NVIDIA_NIM_BASE_URL,
        temperature=0.3,
        max_tokens=2048,
    )


_nim_client: AsyncOpenAI | None = None


def _get_nim_client() -> AsyncOpenAI:
    global _nim_client
    if _nim_client is None:
        _nim_client = AsyncOpenAI(
            api_key=settings.NVIDIA_API_KEY,
            base_url=settings.NVIDIA_NIM_BASE_URL,
        )
    return _nim_client


async def get_embeddings(text: str, input_type: str = "passage") -> list[float]:
    """NVIDIA NIM Embedding API로 벡터 생성.

    Args:
        text: 임베딩할 텍스트 (내부에서 512 토큰 한도에 맞게 자동 잘림)
        input_type: "passage" (도서 저장용) | "query" (검색 쿼리용)
    """
    safe_text = _truncate(text)
    client = _get_nim_client()
    response = await client.embeddings.create(
        model=settings.NVIDIA_EMBEDDING_MODEL,
        input=safe_text,
        encoding_format="float",
        extra_body={"input_type": input_type},
    )
    return response.data[0].embedding
