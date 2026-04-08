from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # NVIDIA NIM
    NVIDIA_API_KEY: str = ""
    NVIDIA_NIM_BASE_URL: str = "https://integrate.api.nvidia.com/v1"
    NVIDIA_LLM_MODEL: str = "meta/llama-3.1-70b-instruct"
    NVIDIA_EMBEDDING_MODEL: str = "nvidia/llama-3.2-nv-embedqa-1b-v2"

    # Supabase PostgreSQL (pgvector)
    DATABASE_URL: str = ""

    # LangSmith
    LANGCHAIN_API_KEY: str = ""
    LANGCHAIN_TRACING_V2: bool = True
    LANGCHAIN_PROJECT: str = "read-me"

    # 추천 임계값
    RECOMMENDATION_THRESHOLD: float = 0.5
    RECOMMENDATION_MAX: int = 10

    # CORS
    ALLOWED_ORIGINS: list[str] = ["http://localhost:8000", "http://django_server:8000"]

    class Config:
        env_file = "../.env"
        extra = "ignore"


settings = Settings()
