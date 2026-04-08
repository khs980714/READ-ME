# READ:ME

> IT·개발 도서 정보 탐색 및 AI 챗봇 기반 도서 추천 서비스

---

## 서비스 개요

**READ:ME**는 IT·개발 도서 목록을 제공하고, AI 챗봇과의 대화를 통해 사용자의 수준과 목표에 맞는 책을 추천해 주는 웹 서비스입니다.

- 도서 썸네일·설명·난이도 정보를 한눈에 확인
- 학습 로드맵, 수준별 추천, 일반 도서 질문에 응답하는 챗봇 (마크다운 렌더링 지원)
- 관리자 전용 **데이터 수집 페이지** — 실시간 Progress Bar + 로그로 파이프라인 시각화
- `book_list` 테이블로 중복 도서 수집 방지 (동일 책은 API 재호출 없이 재사용)

---

## 기술 스택

**Backend**

![Django](https://img.shields.io/badge/Django-092E20?style=for-the-badge&logo=django&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)

**Database**

![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)
![pgvector](https://img.shields.io/badge/pgvector-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)

**AI / LLM**

![LangChain](https://img.shields.io/badge/LangChain-1C3C3C?style=for-the-badge&logo=langchain&logoColor=white)
![LangSmith](https://img.shields.io/badge/LangSmith-FF6B35?style=for-the-badge&logo=langchain&logoColor=white)
![NVIDIA](https://img.shields.io/badge/NVIDIA_NIM-76B900?style=for-the-badge&logo=nvidia&logoColor=white)

**Infra**

![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![Docker Compose](https://img.shields.io/badge/Docker_Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white)

---

## 프로젝트 구조

```
READ-ME/
├── django_server/                  # Django 웹 서버
│   ├── config/               # 프로젝트 설정 (settings, urls, wsgi)
│   ├── books/                # 도서 앱 (BookList·Book 모델, 뷰, 관리자)
│   ├── chat/                 # 챗봇 앱 (세션, 메시지, 추천)
│   ├── data_pipeline/        # 데이터 수집 페이지 + 관리 커맨드
│   │   ├── views.py          # 파이프라인 UI + SSE 스트림 뷰
│   │   ├── urls.py
│   │   └── management/commands/
│   │       ├── load_books.py
│   │       └── run_pipeline.py
│   ├── templates/
│   │   ├── base.html
│   │   ├── books/
│   │   ├── chat/
│   │   └── data_pipeline/
│   ├── static/
│   ├── entrypoint.sh         # 컨테이너 시작 스크립트 (migrate → collectstatic → gunicorn)
│   └── manage.py
├── fastapi_server/             # FastAPI 모델 서빙
│   ├── routers/
│   │   ├── chat.py           # 챗봇 추론 엔드포인트
│   │   └── embed.py          # 임베딩 생성 엔드포인트 (book_list_id 기반)
│   ├── chains/               # LangChain 체인 정의
│   ├── prompts/              # 프롬프트 템플릿
│   └── main.py
├── infra/
│   └── init.sql              # PostgreSQL 초기화 (pgvector 확장 활성화)
├── docs/
│   ├── schema.md             # DB DDL 및 설계 결정
│   └── erd.md                # ERD (Mermaid)
├── data.csv                  # 초기 도서 데이터
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

### 핵심 테이블

| 테이블 | 설명 |
|---|---|
| `publishers` | 출판사 (정규화) |
| `authors` | 저자 (정규화) |
| `book_list` | **도서 정보 마스터** — (title, author, publisher) 조합으로 중복 방지 |
| `book_list_categories` | book_list ↔ categories 다대다 |
| `books` | 도서 코드 레지스트리 — book_code + book_list_id FK |
| `book_embeddings` | pgvector 임베딩 (dim=1024, book_list 단위) |
| `chat_sessions` | 챗봇 세션 (UUID, 비로그인 지원) |
| `chat_messages` | 대화 메시지 및 질문 유형 |
| `chat_recommendations` | 응답 추천 도서 + 유사도 점수 |

### 중복 수집 방지 로직

`book_list` 테이블의 `(title, author, publisher)` UNIQUE 제약으로 동일 책의 중복 수집을 방지합니다.

파이프라인 실행 시:
- `description`이 이미 있으면 Naver API 호출 **건너뜀**
- `difficulty`가 이미 있으면 LLM 난이도 분류 **건너뜀**
- `book_embeddings`에 이미 존재하면 임베딩 생성 **건너뜀**

---

## 챗봇 질문 유형

| 유형 | 설명 | 응답 |
|---|---|---|
| `roadmap` | 학습 로드맵 요청 | 단계별 가이드 + 추천 도서 카드 |
| `level_based` | 수준 명시 추천 요청 | 해당 수준 도서 카드 |
| `general` | 기타 도서 관련 질문 | LLM 답변 (마크다운) + 연관 도서 카드 |
| `unrelated` | 도서와 무관한 질문 | "도서와 관련없는 질문입니다." |

추천 카드는 유사도 threshold 이상인 도서를 최대 3개 기본 노출, 초과분은 **더보기**로 확인합니다.

---

## 데이터 수집 파이프라인

### 방법 1 — 웹 UI (권장)

관리자 계정으로 로그인 후 상단 **데이터 수집** 탭에서 버튼 클릭:
- 실시간 Progress Bar로 진행 현황 확인
- 도서별 성공/실패/에러 메시지 로그 표시

### 방법 2 — CLI

```bash
# 1단계: 도서 기본 정보 적재 (CSV → DB)
docker compose exec backend python manage.py load_books

# 2단계: Naver API · 난이도 분류 · 임베딩 순차 실행
docker compose exec backend python manage.py run_pipeline --all

# 특정 도서만 실행
docker compose exec backend python manage.py run_pipeline --book-id 1
```

---

## 환경 변수

`.env.example`을 복사 후 값을 채워주세요.

```bash
cp .env.example .env
```

| 변수 | 설명 |
|---|---|
| `DJANGO_SECRET_KEY` | Django 시크릿 키 |
| `DJANGO_DEBUG` | 디버그 모드 (`True` / `False`) |
| `ALLOWED_HOSTS` | 허용 호스트 (쉼표 구분, 예: `localhost,127.0.0.1`) |
| `DJANGO_SUPERUSER_USERNAME` | 컨테이너 시작 시 자동 생성할 관리자 아이디 |
| `DJANGO_SUPERUSER_PASSWORD` | 관리자 비밀번호 |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | DB 접속 정보 |
| `DATABASE_URL` | `postgresql://<USER>:<PW>@db:5432/<DB>` |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | Naver Developers 앱 자격증명 |
| `NVIDIA_API_KEY` | NVIDIA NIM API 키 |
| `NVIDIA_NIM_BASE_URL` / `NVIDIA_LLM_MODEL` / `NVIDIA_EMBEDDING_MODEL` | NVIDIA NIM 엔드포인트 |
| `LANGCHAIN_API_KEY` | LangSmith 트레이싱 키 |
| `MODEL_SERVER_URL` | FastAPI 서버 주소 (Docker: `http://fastapi_server:8001`) |
| `RECOMMENDATION_THRESHOLD` | 추천 유사도 임계값 (기본: `0.5`) |

---

## 실행 방법

### 사전 준비

1. **Docker Desktop** 설치 및 실행
2. **NVIDIA API 키**, **Naver API 키** 발급

### 빠른 시작

```bash
# 1. 환경 변수 설정
cp .env.example .env
# .env 파일을 열어 API 키 등 실제 값 입력

# 2. 컨테이너 빌드 및 실행
docker compose up --build -d

# 3. 컨테이너 로그 확인 (migrate & collectstatic 자동 실행됨)
docker compose logs -f backend

# 4. 초기 도서 데이터 적재 (최초 1회)
docker compose exec backend python manage.py load_books

# 5. 웹 브라우저에서 http://localhost:8000 접속
#    관리자 로그인 후 '데이터 수집' 탭에서 파이프라인 실행
```

> migrate, collectstatic, 슈퍼유저 생성은 컨테이너 시작 시 **entrypoint.sh**가 자동으로 처리합니다.

### DB 완전 초기화

```bash
docker compose down -v
```

---

## 접속 URL

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
| ChatBot 이용 | O | O |
| 데이터 수집 페이지 접근 | X (메인으로 리다이렉트) | O |
| Django Admin | X | O |
| 도서 등록 / 수정 / 삭제 | X | O |

---

## 트러블슈팅

### Django 서버에 접속이 안 될 때

```bash
# 컨테이너 상태 확인
docker compose ps

# Django 서버 로그 확인
docker compose logs backend

# DB 마이그레이션 수동 실행
docker compose exec backend python manage.py migrate

# 정적 파일 수집 수동 실행
docker compose exec backend python manage.py collectstatic --noinput
```

### `DisallowedHost` 에러

`.env`의 `ALLOWED_HOSTS`에 접속 호스트를 추가하세요.

```
ALLOWED_HOSTS=localhost,127.0.0.1,your-server-ip
```

### pgvector 관련 오류

```bash
# pgvector 확장 수동 활성화
docker compose exec db psql -U readme -d readme_db -c "CREATE EXTENSION IF NOT EXISTS vector;"
```
