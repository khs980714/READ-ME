# CLAUDE.md — READ:ME 프로젝트 가이드

이 파일은 Claude Code가 프로젝트를 이해하고 일관된 방식으로 작업하기 위한 가이드입니다.

---

## 서비스 개요

**READ:ME** — IT·개발 도서 정보 탐색 및 AI 챗봇 기반 도서 추천 서비스.

---

## 아키텍처

```
[사용자 브라우저]
      │
      ▼
[Django (django_server:8000)]  ──HTTP──►  [FastAPI (fastapi_server:8001)]
      │                                      │
      │                               LangChain · LangSmith
      │                               OpenAI SDK → NVIDIA NIM
      │
      ▼
[PostgreSQL 16 + pgvector  (Docker service: db)]
  ├── 관계형 테이블 (books, authors, publishers …)
  └── book_embeddings (pgvector, cosine similarity)
```

### 컴포넌트 역할

| 컴포넌트 | 경로 | 역할 |
|---|---|---|
| Django | `django_server/` | 웹 UI, REST API, Django Admin, 데이터 파이프라인 관리 커맨드 |
| FastAPI | `fastapi_server/` | LLM 추론, 임베딩 생성, 질문 분류, LangChain 체인 실행 |
| PostgreSQL | `db` (Docker) | PostgreSQL 16 + pgvector, 포트 5432 |

---

## 디렉토리 구조

```
READ-ME/
├── django_server/
│   ├── config/           # Django settings, urls, wsgi, asgi
│   ├── books/            # 도서 앱: models, views, serializers, admin, signals
│   ├── chat/             # 챗봇 앱: models, views, consumers(ws)
│   ├── data_pipeline/    # management commands: load_books, fetch_book_info, classify_difficulty
│   └── manage.py
├── fastapi_server/
│   ├── routers/
│   │   ├── chat.py       # POST /chat — 질문 분류 + LLM 응답 + 벡터 검색
│   │   └── embed.py      # POST /embed — 도서 임베딩 생성/갱신
│   ├── chains/           # LangChain 체인 (roadmap, level_based, general, classifier)
│   ├── prompts/          # 프롬프트 템플릿
│   └── main.py
├── docs/
│   ├── schema.md         # DB DDL + 설계 결정
│   └── erd.md            # ERD (Mermaid)
├── docker-compose.yml
├── Dockerfile.django_server
├── Dockerfile.fastapi_server
├── .env.example
├── data.csv      # 초기 도서 데이터 (최초 1회만 사용)
└── README.md
```

---

## DB 스키마 요약

> 상세 DDL: `docs/schema.md` | ERD: `docs/erd.md`

### 핵심 테이블

```
publishers  ──(1:N)──  books  ──(M:N, book_authors)──  authors
                         │
                         ├──(M:N, book_categories)──  categories
                         │
                         └──(1:1)──  book_embeddings  [pgvector]

chat_sessions ──(1:N)── chat_messages ──(1:N)── chat_recommendations ──(N:1)── books
```

### 저자 정규화 규칙

- CSV의 `,` 구분 저자는 **`authors` 테이블에 개별 저장** 후 `book_authors`로 연결.
- `book_authors.author_order` 로 표기 순서 보존.

### 난이도 Enum

`difficulty_level`: `'입문' | '초급' | '중급' | '고급'`

---

## 데이터 파이프라인

### 초기 CSV 적재 (1회)

```bash
python manage.py load_books
# data.csv → publishers, authors, books, book_authors 생성
```

### 도서 등록 후 자동 실행 (books post_save signal)

1. **Naver Book Search API** → `thumbnail_url`, `description`, `isbn`, `published_at`
2. **리뷰 스크래핑** → 텍스트 수집
3. **LLM (NVIDIA NIM)** → 난이도 분류 → `books.difficulty`
4. **OpenAI Embedding API** → `book_embeddings` upsert

---

## 챗봇 로직

### 질문 분류 (FastAPI `/chat`)

| `question_type` | 조건 | 응답 |
|---|---|---|
| `roadmap` | 학습 로드맵 요청 | 단계별 가이드 + 각 단계 추천 카드 |
| `level_based` | 수준 언급 + 추천 요청 | 해당 수준 도서 카드 |
| `general` | 기타 도서 관련 | LLM 답변 + 연관 도서 카드 |
| `unrelated` | 도서 무관 | "도서와 관련없는 질문입니다." |

### 추천 카드 규칙

- `similarity_score >= threshold` 인 도서를 `rank` 순으로 정렬
- **기본 노출**: rank 1~3
- **더보기**: rank 4 이상 (클릭 시 표시)
- 카드 정보: 책 제목, 저자, 출판사, 난이도
- 카드 클릭 → 해당 도서 상세 페이지 (`/books/<id>/`)

---

## 환경 변수

| 변수 | 설명 |
|---|---|
| `DATABASE_URL` | PostgreSQL 연결 문자열 (`postgresql://readme:readme1234@db:5432/readme_db`) |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | Naver Book Search API 인증 |
| `OPENAI_API_KEY` | 임베딩 생성용 OpenAI 키 |
| `NVIDIA_NIM_BASE_URL` / `NVIDIA_NIM_MODEL` | NVIDIA NIM 모델 엔드포인트 |
| `LANGCHAIN_API_KEY` / `LANGCHAIN_PROJECT` | LangSmith 트레이싱 |
| `MODEL_SERVER_URL` | Django → FastAPI 내부 URL (docker-compose 서비스명) |

---

## Docker Compose 서비스

| 서비스명 | 포트 | 설명 |
|---|---|---|
| `backend` | 8000 | Django |
| `fastapi_server` | 8001 | FastAPI |

---

## 코딩 컨벤션

- Python: PEP 8, 타입 힌트 사용 권장
- Django 모델 필드는 `verbose_name` 또는 `help_text`로 한국어 설명 추가
- LangChain 체인은 `fastapi_server/chains/` 에만 작성, FastAPI 라우터에는 체인 호출만
- 새 도서 관련 비즈니스 로직은 `books/services.py`에 분리
- DB 마이그레이션은 항상 리뷰 후 적용

---

## 작업 시 주의사항

- `data.csv`는 읽기 전용 참고 파일. 직접 수정 금지.
- `book_embeddings` 재생성 시 HNSW 인덱스 자동 갱신됨 (별도 작업 불필요).
- `chat_recommendations.similarity_score` threshold는 FastAPI 환경 변수로 관리.
- Django Admin에서 도서 삭제 시 `book_authors`, `book_categories`, `book_embeddings`, `chat_recommendations` cascade 삭제됨 — 주의.
