from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # NVIDIA NIM
    NVIDIA_API_KEY: str = ""
    NVIDIA_NIM_BASE_URL: str = "https://integrate.api.nvidia.com/v1"
    NVIDIA_LLM_MODEL: str = "meta/llama-3.1-70b-instruct"
    NVIDIA_EMBEDDING_MODEL: str = "nvidia/llama-nemotron-embed-1b-v2"

    # Supabase PostgreSQL (pgvector)
    DATABASE_URL: str = ""

    # LangSmith
    LANGCHAIN_API_KEY: str = ""
    LANGCHAIN_TRACING_V2: bool = True
    LANGCHAIN_PROJECT: str = "read-me"

    # 추천 임계값 (질문 유형별)
    # specific_search: 특정 기술·키워드 → 정밀 검색
    RECOMMENDATION_THRESHOLD_SPECIFIC_SEARCH: float = 0.45
    # goal_oriented: 진로·목적 기반 → 다양한 카테고리 도서 필요
    RECOMMENDATION_THRESHOLD_GOAL_ORIENTED: float = 0.30
    # career_certification: 자격증·포트폴리오
    RECOMMENDATION_THRESHOLD_CAREER_CERTIFICATION: float = 0.38
    # level_based: difficulty 필터 후 보완
    RECOMMENDATION_THRESHOLD_LEVEL_BASED: float = 0.35
    RECOMMENDATION_MAX: int = 10

    # 임베딩 차원 (nvidia/llama-nemotron-embed-1b-v2 = 2048, EOL된 llama-3.2-nv-embedqa-1b-v2 후속 모델)
    # DB 컬럼(book_embeddings.embedding)과 반드시 일치해야 합니다.
    # 변경 시 새 마이그레이션으로 컬럼 차원도 함께 변경하세요.
    EMBEDDING_DIM: int = 2048

    # CORS
    ALLOWED_ORIGINS: list[str] = ["http://localhost:8000", "http://django_server:8000"]

    class Config:
        env_file = "../.env"
        extra = "ignore"


settings = Settings()
