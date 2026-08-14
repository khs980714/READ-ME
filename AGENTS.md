# AGENTS.md — READ:ME 프로젝트 가이드

**READ:ME** — IT·개발 도서 정보 탐색 및 AI 챗봇 기반 도서 추천 서비스.

---

## 아키텍처

```
[브라우저] → [Django :8000] → [FastAPI :8001]
                │                    │
                └──────[PostgreSQL 16 + pgvector]
```

| 컴포넌트 | 경로 | 역할 |
|---|---|---|
| Django | `.src/django_server/` | 웹 UI, REST API, 도서 관리, 파이프라인 커맨드 |
| FastAPI | `.src/fastapi_server/` | LLM 추론, 임베딩, 질문 분류, LangChain 체인 |
| PostgreSQL | Docker `db` | pgvector 포함, 포트 5432 |

---

## 자주 쓰는 명령어

```bash
# 마이그레이션
docker exec read-me-backend-1 python manage.py migrate

# 도서 초기 적재 (data.csv → DB, 최초 1회)
docker exec read-me-backend-1 python manage.py load_books

# 개별 도서 데이터 수집 (알라딘 → YES24 → 교보문고 → LLM → 임베딩)
docker exec read-me-backend-1 python manage.py fetch_book_info --all
```

---

## 환경 변수

| 변수 | 설명 |
|---|---|
| `DATABASE_URL` | `postgresql://readme:readme1234@db:5432/readme_db` |
| `ALADIN_TTB_KEY` | 알라딘 Open API — 도서 정보 수집 1차 소스 |
| `NVIDIA_NIM_BASE_URL` / `NVIDIA_LLM_MODEL` | LLM 엔드포인트 (난이도 분류) |
| `NVIDIA_EMBEDDING_MODEL` / `NVIDIA_API_KEY` | 임베딩 생성 |
| `LANGCHAIN_API_KEY` / `LANGCHAIN_PROJECT` | LangSmith 트레이싱 |
| `MODEL_SERVER_URL` | Django → FastAPI URL (`http://fastapi_server:8001`) |

---

## 비표준 설계 결정

- **BookList / Book 분리**: 동일 책의 여러 도서 코드를 `BookList`(마스터)에 연결. 수정 시 `Book`이 아닌 `book_list`를 통해 접근.
- **임베딩은 BookList 단위**: `book_embeddings`는 `book_list_id` FK. 동일 책 중복 임베딩 방지.
- **질문 분류 유형**: `keyword_search` / `specific_search` / `goal_oriented` / `career_certification` / `level_based` / `out_of_scope`
- **추천 카드**: 최대 3권 노출, 초과분은 "더보기" 모달. 모바일도 동일.
- **데이터 수집 파이프라인**: 알라딘 API(1차) → YES24 스크래핑(2차) → 교보문고 스크래핑(3차) → LLM 난이도 분류 → 임베딩 생성. Naver API 미사용.

---

## 코딩 컨벤션

- 도서 비즈니스 로직 → `books/services.py`
- LangChain 체인 → `.src/fastapi_server/chains/` (라우터에는 체인 호출만)
- DB 마이그레이션은 항상 리뷰 후 적용

---

## 주의사항

- `data.csv` — 읽기 전용. 직접 수정 금지.
- Django Admin에서 도서 삭제 시 `book_embeddings`, `chat_recommendations` cascade 삭제됨.
- `book_embeddings` 재생성 시 HNSW 인덱스 자동 갱신 (별도 작업 불필요).
