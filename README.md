# READ:ME

> IT·개발 도서 정보 탐색 및 AI 챗봇 기반 도서 추천 서비스

---

## 서비스 개요

**READ:ME**는 IT·개발 도서 목록을 제공하고, AI 챗봇과의 대화를 통해 사용자의 수준과 목표에 맞는 책을 추천해 주는 웹 서비스입니다.

- 도서 썸네일·설명·목차·난이도·카테고리 정보를 한눈에 확인
- 학습 로드맵, 수준별 추천, 일반 도서 질문에 응답하는 챗봇 (마크다운 렌더링 지원)
- 관리자 전용 **데이터 수집 페이지** — 실시간 Progress Bar + 로그로 파이프라인 시각화, **중지 버튼** 지원
- `book_list` 테이블로 중복 도서 수집 방지 (동일 책은 API 재호출 없이 재사용)
- LLM 기반 **카테고리 자동 분류** — 도서 제목·설명을 분석해 12개 카테고리 중 1~3개 자동 태깅
- **멀티소스 정보 수집** — 알라딘 API → YES24 → 교보문고 순 fallback으로 설명·목차 수집
- **판차·출판 연도 자동 추출** — 제목에서 판차 정보 분리 저장, 연도 메타데이터 추출
- **IP 기반 Rate Limiting** — FastAPI slowapi로 챗봇 API 과부하 방지

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
│   │   ├── views.py          # 파이프라인 UI + SSE 스트림 뷰 (중지 지원)
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
│   │   ├── chat.py           # 챗봇 추론 엔드포인트 (동기 + SSE 스트리밍)
│   │   └── embed.py          # 임베딩 생성 엔드포인트 (book_list_id 기반)
│   ├── chains/               # LangChain 체인 정의
│   │   ├── classifier.py     # 질문 유형 분류기 (regex 사전 감지 + LLM)
│   │   ├── keyword_search.py
│   │   ├── specific_search.py
│   │   ├── goal_oriented.py
│   │   ├── level_based.py
│   │   ├── career_certification.py
│   │   ├── retriever.py      # pgvector 유사도 검색 + 후처리 필터
│   │   └── utils.py
│   ├── prompts/              # 프롬프트 템플릿
│   ├── tests/
│   │   ├── classifier_test_cases.json  # 유형별 50개, 총 300개 테스트 케이스
│   │   ├── run_classifier_test.py      # 분류기 정확도 측정 스크립트
│   │   └── locustfile.py               # 부하 테스트 (Locust)
│   ├── limiter.py            # slowapi Rate Limiter
│   ├── db.py                 # psycopg2 커넥션 풀
│   └── main.py
├── infra/
│   └── init.sql              # PostgreSQL 초기화 (pgvector 확장 활성화)
├── docs/
│   ├── architecture.md       # 시스템 아키텍처 다이어그램 (Mermaid)
│   ├── erd.md                # ERD (Mermaid)
│   ├── schema.md             # DB DDL 및 설계 결정
│   ├── chain.md              # LangChain 체인 분기 구조 (Mermaid)
│   └── api.md                # API 엔드포인트 명세
├── data.csv                  # 초기 도서 데이터
├── docker-compose.yml
├── Dockerfile.django_server
├── Dockerfile.fastapi_server
├── .env.example
└── README.md
```

---

## DB 설계

- [아키텍처 다이어그램](docs/architecture.md)
- [ERD](docs/erd.md)
- [스키마 상세 (DDL)](docs/schema.md)
- [LangChain 체인 구조](docs/chain.md)
- [API 명세](docs/api.md)

### 핵심 테이블

| 테이블 | 설명 |
|---|---|
| `publishers` | 출판사 (정규화) |
| `authors` | 저자 (정규화) |
| `categories` | 카테고리 (12종) |
| `book_list` | **도서 정보 마스터** — `(title, edition, author, publisher)` 조합으로 중복 방지 |
| `book_list_categories` | book_list ↔ categories 다대다 |
| `books` | 도서 코드 레지스트리 — book_code(`D-NNN`) + book_list_id FK |
| `book_embeddings` | pgvector 임베딩 (dim=1024, book_list 단위) |
| `yes24_candidates` | YES24 검색 후보 임시 저장 — 관리자 선택 후 book_list 덮어쓰기 |
| `chat_sessions` | 챗봇 세션 (UUID, 비로그인 지원) |
| `chat_messages` | 대화 메시지 및 질문 유형 |
| `chat_recommendations` | 응답 추천 도서 + 유사도 점수 |

### `book_list` 주요 컬럼

| 컬럼 | 설명 |
|---|---|
| `title` | 판차 제거 후 정제된 도서명 |
| `subtitle` | 부제 |
| `edition` | 판차 정보 (`(개정2판)`, `(심화편)` 등, 제목에서 분리) |
| `publication_year` | 출판 연도 (제목·설명에서 자동 추출, 4자리) |
| `description` | 도서 소개 (최대 2,000자) |
| `toc` | 목차 (최대 3,000자) |
| `difficulty` | 난이도 (`입문` / `초급` / `중급` / `고급`) |
| `thumbnail_url` | 표지 이미지 원본 소스 URL |
| `thumbnail` | 로컬 저장 썸네일 파일 경로 (표시 시 우선 사용) |

### 중복 수집 방지 로직

`book_list` 테이블의 `(title, edition, author, publisher)` UNIQUE 제약으로 동일 책의 중복 수집을 방지합니다.

파이프라인 실행 시:
- `description` / `toc` / `thumbnail_url`이 이미 있으면 정보 수집 **건너뜀**
- `difficulty`가 이미 있으면 LLM 난이도 분류 **건너뜀**
- `book_embeddings`에 이미 존재하면 임베딩 생성 **건너뜀**
- `categories`가 이미 연결되어 있으면 카테고리 분류 **건너뜀**

---

## 챗봇 질문 유형

LLM이 사용자의 질문을 6가지 유형으로 분류한 후, 유형별 전용 체인으로 응답합니다.
조회·목록 의도가 명확한 패턴은 LLM 호출 없이 정규식으로 즉시 분류합니다.

| 유형 | 설명 | 예시 |
|---|---|---|
| `keyword_search` | 특정 키워드 도서 목록 조회 | "AWS 도서 조회해줘", "파이썬 책 있어?" |
| `specific_search` | 특정 기술·도구 추천 요청 | "스프링 부트 책 추천해줘", "리액트 공부할 책 알려줘" |
| `goal_oriented` | 진로·직업 목표 기반 추천 | "프론트엔드 개발자가 되고 싶어", "데이터 엔지니어로 전직하고 싶어" |
| `career_certification` | 자격증 취득·코딩 테스트·취업 면접 준비 | "정보처리기사 실기 수험서", "코딩 테스트 준비 책 알려줘" |
| `level_based` | 수준·숙련도 기반 추천 | "파이썬 중급자용 책", "생초보를 위한 자바 책" |
| `out_of_scope` | IT·개발 도서와 무관한 질문 | "오늘 날씨 어때?", "요리책 추천해줘" |

추천 카드는 유사도 threshold 이상인 도서를 최대 3개 기본 노출, 초과분은 **더보기**로 확인합니다.

---

## 도서 카테고리

LLM(NVIDIA NIM)이 도서 제목과 설명을 분석하여 아래 12개 카테고리 중 **1~3개**를 자동으로 태깅합니다.

| 카테고리 | 설명 |
|---|---|
| 프로그래밍 언어 | Python, JavaScript, Java, C/C++, Go, Rust 등 언어별 도서 |
| 웹 개발 | 프론트엔드(React, Vue 등) / 백엔드(Django, Spring, FastAPI 등) / 풀스택 |
| 모바일 개발 | Android, iOS, Flutter, React Native 등 |
| 데이터베이스 | SQL, NoSQL, DB 설계·최적화 |
| 자료구조·알고리즘 | 코딩 테스트, 알고리즘 이론 |
| 컴퓨터 과학 | 운영체제, 네트워크, 컴퓨터 구조, 컴파일러 |
| 인공지능·데이터 | 머신러닝, 딥러닝, 데이터 분석, LLM·생성 AI |
| DevOps·클라우드 | Docker, Kubernetes, CI/CD, AWS·GCP·Azure |
| 소프트웨어 공학 | 클린 코드, 설계 패턴, 테스트, 아키텍처 |
| 보안 | 정보보안, 웹 보안, 자격증(정보처리기사, CISSP 등) |
| 자격증·취업 | 수험서, 면접 준비, 코딩 테스트 |
| IT 교양 | 개발 문화, 스타트업, 비개발자 대상 IT 입문 |

> 분류 결과는 `book_list_categories` 테이블에 저장되며, 웹 UI 파이프라인 페이지에서 일괄 처리할 수 있습니다.

---

## 데이터 수집 파이프라인

### 방법 1 — 웹 UI (권장)

관리자 계정으로 로그인 후 상단 **데이터 수집** 탭에서 각 버튼 클릭:
- 실시간 Progress Bar로 진행 현황 확인
- 도서별 성공/실패/에러 메시지 로그 표시
- **중지 버튼**으로 실행 중인 파이프라인을 즉시 중단 가능

| 파이프라인 | 설명 |
|---|---|
| 전체 수집 | 설명·목차·ISBN·썸네일 → 난이도 분류 → 임베딩 순으로 일괄 처리 |
| 임베딩 누락 보정 | description 있으나 임베딩 없는 도서만 재처리 |
| 난이도 분류 | 설명 있는 전체 도서를 LLM으로 재분류. 결과 미리보기 후 선택적으로 DB 반영 |
| 출판 연도 추출 | publication_year 미등록 도서의 연도 자동 추출 |
| 카테고리 분류 | categories 미태깅 도서만 LLM으로 카테고리 자동 분류 |

### 방법 2 — CLI

```bash
# 1단계: 도서 기본 정보 적재 (CSV → DB)
docker compose exec backend python manage.py load_books

# 2단계: 정보 수집·난이도 분류·임베딩 일괄 실행
docker compose exec backend python manage.py run_pipeline --all

# 특정 도서만 실행
docker compose exec backend python manage.py run_pipeline --book-id 1
```

### 멀티소스 정보 수집 전략

도서 설명·목차·썸네일은 아래 순서로 fallback 수집합니다.

```
1차: 알라딘 Open API (TTB) — 설명 + 목차(OptResult=toc) + ISBN + 썸네일
2차: YES24 스크래핑           — 1차에서 누락된 설명·목차 보완
3차: 교보문고 스크래핑        — 2차에서도 누락된 설명·목차 보완
```

- description 텍스트에 목차가 포함된 경우 **자동으로 설명/목차 분리**
- 알라딘 후보 검색 결과에서 **수동으로 정확한 도서를 선택**하여 적용 가능 (웹 UI)

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
| `ALADIN_TTB_KEY` | 알라딘 Open API 키 (도서 정보·목차 수집 1차 소스) |
| `NVIDIA_API_KEY` | NVIDIA NIM API 키 |
| `NVIDIA_NIM_BASE_URL` / `NVIDIA_LLM_MODEL` / `NVIDIA_EMBEDDING_MODEL` | NVIDIA NIM 엔드포인트 |
| `LANGCHAIN_API_KEY` | LangSmith 트레이싱 키 |
| `MODEL_SERVER_URL` | FastAPI 서버 주소 (Docker: `http://fastapi_server:8001`) |
| `RECOMMENDATION_THRESHOLD` | 추천 유사도 임계값 (기본: `0.5`) |

---

## 실행 방법

### 사전 준비

1. **Docker Desktop** 설치 및 실행
2. **NVIDIA API 키**, **알라딘 TTB API 키** 발급

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

## 추천 정확도

### 정렬 전략

벡터 유사도 검색 후 `(정렬 키 1, 정렬 키 2)` 튜플로 재정렬합니다.

| 쿼리 유형 | 1차 정렬 키 | 2차 정렬 키 | 비고 |
|---|---|---|---|
| 자격증 (`career_certification`) | `publication_year` DESC | `score` + 개정판 보너스 | 연도가 다르면 score와 무관하게 최신 연도 우선 |
| 일반 쿼리 | 고정(0) | `score` + 연도 가중치 + 개정판 보너스 | 유사도 기반 정렬에 연도 가중치 합산 |

**연도 가중치** (일반 쿼리)

| 출판 연도 | 가중치 |
|---|---|
| 현재 연도 이상 | +0.05 |
| 1년 전 | +0.03 |
| 2~3년 전 | +0.01 |
| 4년 이상 전 | +0.00 |

**개정판 보너스**: `edition` 필드가 비어 있지 않으면 +0.02 (쿼리 유형 무관)

### 자격증 도서 필터링

`career_certification` 유형 질문에서 후처리 필터가 적용됩니다.

| 필터 | 동작 | 예시 |
|---|---|---|
| 자격증 등급 | 다른 등급 자격증 도서 제외 | "정보처리기사" 질문 → "정보처리산업기사" 도서 제외 |
| 시험 유형 | 반대 유형 전용 도서 제외 | "실기" 질문 → "필기" 전용 도서 제외 |
| 올인원 예외 | 두 유형 모두 포함 시 유지 | "필기 + 실기 올인원" → 실기/필기 어느 질문에도 포함 |

필터링 후 결과가 비면 원본 목록을 그대로 반환합니다 (안전 fallback).

---

## 테스트

### 분류기 정확도 테스트

질문 분류기의 정확도를 측정하는 테스트 스위트가 포함되어 있습니다.

```bash
docker exec read-me-fastapi_server-1 python tests/run_classifier_test.py
```

- 테스트 케이스: `fastapi_server/tests/classifier_test_cases.json` (유형별 50개, 총 300개)
- 결과 파일: `fastapi_server/tests/classifier_results.md` / `classifier_results.csv`
- 출력 형식: `| 질문 | 원하는 결과 | 실제 결과 | 통과 |`

### 부하 테스트 (Locust)

FastAPI 서버의 동시 처리 성능을 측정할 수 있습니다.

```bash
# 패키지 설치
pip install locust

# 웹 UI 모드 (http://localhost:8089)
locust -f fastapi_server/tests/locustfile.py --host http://localhost:8001

# 헤드리스 모드 (HTML 리포트 생성)
locust -f fastapi_server/tests/locustfile.py \
    --host http://localhost:8001 \
    --headless -u 20 -r 5 --run-time 60s \
    --html fastapi_server/tests/load_report.html
```

- 시나리오: `/health`, `/chat/message` (동기), `/chat/message/stream` (SSE) — 6가지 질문 유형 고루 사용
- 결과 파일: `load_report.html`, `load_stats_*.csv`

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

### FastAPI 임베딩 차원 불일치 오류

`RuntimeError: 임베딩 차원 불일치` 가 발생하면 DB 컬럼 차원과 `EMBEDDING_DIM` 설정을 확인하세요.

```bash
# 현재 DB 차원 확인
docker compose exec db psql -U readme -d readme_db \
    -c "SELECT vector_dims(embedding) FROM book_embeddings LIMIT 1;"
```
