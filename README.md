# READ:ME

> 도서 정보 탐색 및 AI 챗봇 기반 도서 추천 서비스

---

## 서비스 개요

**READ:ME**는 IT·개발 도서 목록을 제공하고, AI 챗봇과의 대화를 통해 사용자의 수준과 목표에 맞는 책을 추천해 주는 웹 서비스입니다.

- 도서 썸네일·설명·난이도 정보를 한눈에 확인
- 학습 로드맵, 수준별 추천, 일반 도서 질문에 응답하는 챗봇
- 관리자가 도서를 등록하면 자동으로 외부 API·리뷰 스크래핑·난이도 분류 실행

---

## 기술 스택

| 영역 | 기술 |
|---|---|
| 웹 프레임워크 | Django |
| 모델 서빙 | FastAPI |
| DB | Supabase (PostgreSQL + pgvector) |
| AI/LLM | LangChain · LangSmith · OpenAI SDK (NVIDIA NIM 모델) |
| 외부 API | Naver Developer Book Search API |
| 컨테이너 | Docker · Docker Compose |

---

## 프로젝트 구조

```
READ-ME/
├── django_server/                  # Django 웹 서버
│   ├── config/               # 프로젝트 설정 (settings, urls, wsgi)
│   ├── books/                # 도서 앱 (모델, 뷰, 관리자)
│   ├── chat/                 # 챗봇 앱 (세션, 메시지, 추천)
│   ├── data_pipeline/        # CSV 적재 · API 수집 · 스크래핑 관리 커맨드
│   └── manage.py
├── fastapi_server/             # FastAPI 모델 서빙
│   ├── routers/
│   │   ├── chat.py           # 챗봇 추론 엔드포인트
│   │   └── embed.py          # 임베딩 생성 엔드포인트
│   ├── chains/               # LangChain 체인 정의
│   └── main.py
├── docs/
│   ├── schema.md             # DB DDL 및 설계 결정
│   └── erd.md                # ERD (Mermaid)
├── example_data.csv          # 초기 도서 데이터 (최초 1회 사용)
├── docker-compose.yml
├── Dockerfile.django_server
├── Dockerfile.fastapi_server
├── .env.example
└── README.md
```

---

## DB 설계

- [스키마 상세 (DDL)](docs/schema.md)
- [ERD](docs/erd.md)

주요 테이블:

| 테이블 | 설명 |
|---|---|
| `publishers` | 출판사 (정규화) |
| `authors` | 저자 (정규화, 다수 저자 지원) |
| `books` | 도서 메인 정보 |
| `book_authors` | 도서↔저자 다대다 (순서 포함) |
| `book_categories` | 도서↔카테고리 다대다 |
| `book_embeddings` | 도서 벡터 임베딩 (pgvector, dim=1536) |
| `chat_sessions` | 챗봇 세션 (UUID, 비로그인 지원) |
| `chat_messages` | 대화 메시지 및 질문 유형 |
| `chat_recommendations` | 응답에 포함된 추천 도서 + 유사도 점수 |

---

## 챗봇 질문 유형

| 유형 | 설명 | 응답 |
|---|---|---|
| `roadmap` | 학습 로드맵 요청 | 단계별 가이드 + 각 단계 추천 도서 카드 |
| `level_based` | 수준 명시 추천 요청 | 해당 수준에 맞는 도서 카드 |
| `general` | 기타 도서 관련 질문 | LLM 답변 + 연관 도서 카드 |
| `unrelated` | 도서와 무관한 질문 | "도서와 관련없는 질문입니다." |

추천 카드는 유사도 threshold 이상인 도서를 최대 3개 기본 노출하고, 초과분은 **더보기**로 확인합니다.

---

## 데이터 수집 파이프라인

1. **초기 적재**: `example_data.csv` → `python manage.py load_books`
2. **관리자 도서 등록** (Django Admin) → post-save 시그널로 자동 실행:
   - Naver Book Search API → 썸네일, 책 소개, ISBN 수집
   - 리뷰 스크래핑 → LLM 난이도 분류 (`입문 / 초급 / 중급 / 고급`)
   - OpenAI Embedding API → `book_embeddings` 적재

---

## 환경 변수

`.env.example`을 복사하여 `.env`를 생성하세요.

```env
# Django
DJANGO_SECRET_KEY=
DJANGO_DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# Supabase / PostgreSQL
DATABASE_URL=postgresql://user:password@host:5432/dbname

# Naver API
NAVER_CLIENT_ID=
NAVER_CLIENT_SECRET=

# OpenAI / NVIDIA NIM
OPENAI_API_KEY=
NVIDIA_NIM_BASE_URL=
NVIDIA_NIM_MODEL=

# LangSmith
LANGCHAIN_API_KEY=
LANGCHAIN_TRACING_V2=true
LANGCHAIN_PROJECT=read-me

# FastAPI
MODEL_SERVER_URL=http://fastapi_server:8001
```

---

## 실행 방법

### 개발 환경 (Docker Compose)

```bash
# 1. 환경 변수 설정
cp .env.example .env
# .env 파일을 편집하여 실제 값 입력

# 2. 컨테이너 빌드 및 실행
docker compose up --build

# 3. DB 마이그레이션
docker compose exec django_server python manage.py migrate

# 4. 초기 도서 데이터 적재 (최초 1회)
docker compose exec django_server python manage.py load_books

# 5. 관리자 계정 생성
docker compose exec django_server python manage.py createsuperuser
```

### 접속

| 서비스 | URL |
|---|---|
| Django 웹 | http://localhost:8000 |
| Django Admin | http://localhost:8000/admin |
| FastAPI (모델 서버) | http://localhost:8001 |
| FastAPI Docs | http://localhost:8001/docs |

---

## 권한

| 기능 | 비로그인 | 관리자 |
|---|---|---|
| 도서 목록 / 상세 조회 | O | O |
| 챗봇 이용 | O | O |
| 도서 등록 / 수정 / 삭제 | X | O |
| 데이터 수집 트리거 | X | O (자동) |
