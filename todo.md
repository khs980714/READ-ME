# READ:ME 프로젝트 점검 및 작업 목록

> 최종 점검일: 2026-04-14  
> 점검 범위: Django 서버, FastAPI 서버, 프론트엔드 템플릿, 데이터 파이프라인, DB 설계, 보안, 성능

---

## 점검 요약

| 영역 | 상태 |
|------|------|
| 핵심 기능 구현 | ✅ 완료 (도서 CRUD, 챗봇, 파이프라인, 인증) |
| UI/UX | ⚠️ 일부 개선 필요 |
| 버그 | ✅ Critical 3건, High 4건, Medium 8건 모두 수정 완료 |
| 중복 코드 | ✅ threshold 제거, Book 조회 통합, 임베딩 중복 방어 완료 |
| 보안 | 🔴 JWT Secret 관리, Rate Limiting 부재 |
| 성능 | ⚠️ N+1 쿼리, 벡터 인덱스 누락 |

---

## ✅ 완료된 이전 작업

- [x] **목차(TOC) 필드 추가 및 표시** — DB 모델(`toc` 필드), 수집 파이프라인 분리 로직, 상세 페이지 렌더링 구현 완료
- [x] **카테고리 자동 분류 실험** — 12개 카테고리 정의, FastAPI `/embed/classify-category` 엔드포인트, 파이프라인 커맨드 구현 완료

---

## ✅ Critical — 수정 완료 (2026-04-14)

### BUG-01. 챗봇 도서 코드 표시 오류 ✅
- **원인 (분석)**: 두 가지 중첩 문제
  1. `fastapi_server/db.py:87` — book_code 없을 때 fallback이 `str(r[0])`(예: `246`)
  2. 4개 체인 `_build_messages` — LLM 컨텍스트에서 코드를 `[{code}]`로 래핑해 LLM이 괄호까지 출력
  3. 4개 프롬프트 — `[코드] 값 그대로` 지시로 LLM이 괄호를 유지
- **수정 내용**:
  - `db.py:87`: `str(r[0])` → `f"D-{r[0]:03d}"` (fallback도 D-XXX 형식)
  - `chains/{specific_search,goal_oriented,career_certification,level_based}.py`: `- [{code}]` → `- {code}` (컨텍스트에서 괄호 제거)
  - `prompts/*.txt` 4개: `[코드] 값 그대로` → `"D-" 로 시작하는 코드 값 그대로 (예: D-001)`

### BUG-02. 임베딩 벡터 차원 검증 ✅
- **분석**: 마이그레이션 `0003`의 `vector(2048)`은 실제 모델(`nvidia/llama-3.2-nv-embedqa-1b-v2`, 2048차원)과 일치. 차원 불일치 버그 아님.
  실제 문제: DB와 config 간 차원이 달라질 경우 런타임까지 감지 불가
- **수정 내용**:
  - `fastapi_server/config.py`: `EMBEDDING_DIM: int = 2048` 상수 추가 (모델·DB 동기화 기준점)
  - `fastapi_server/main.py`: 서버 시작 시 `_verify_embedding_dim()` 호출 → DB 실제 차원과 config 불일치 시 즉시 `RuntimeError` 발생

### BUG-03. 스트리밍 추천 도서 저장 신뢰성 ✅
- **분석**: `book_list_id` 자체는 정상 전달됨. 실제 문제는 `create()` 호출 시 IntegrityError 가능성 + N+1 쿼리
- **수정 내용** (`django_server/chat/views.py` `_save_stream_result`):
  - `Book` 조회를 `filter(book_list_id__in=[...])` 일괄 조회로 변경 (N+1 제거)
  - `ChatRecommendation.objects.create()` → `get_or_create()` (중복 저장 방어)
  - 항목별 `try/except` 분리 → 한 항목 오류가 나머지 저장을 막지 않도록 처리
  - `book_list_id` 누락 시 `logger.warning` 명시

---

## ✅ High Priority — 수정 완료 (2026-04-14)

### BUG-04. N+1 쿼리 — 챗봇 추천 도서 조회 ✅
- **수정 내용** (`django_server/chat/views.py`):
  - `_bulk_fetch_books(raw_recs)` 공통 함수 추가 — `book_list_id__in` 일괄 조회 + `select_related`
  - `_enrich_recommendations()`: 루프 개별 쿼리 → `_bulk_fetch_books()` 사용으로 교체 (N+1 제거)
  - `send_message()`: 동일하게 `_bulk_fetch_books()` 사용으로 교체

### BUG-05. 임베딩 재생성 중복 호출 ✅
- **분석**: Signal(`created=True` 전용)과 `book_edit`의 `_schedule_embedding_refresh`는 실행 시점이 달라 실제 동시 실행은 드물지만, 파이프라인 대시보드와 edit이 겹칠 경우 race condition 가능
- **수정 내용** (`django_server/books/views.py`):
  - 모듈 레벨 `_embedding_in_progress: set[int]` + `_embedding_lock: threading.Lock` 추가
  - `_schedule_embedding_refresh()`: 동일 `book_list_pk`가 이미 진행 중이면 즉시 반환
  - `finally` 블록에서 `_embedding_in_progress.discard()` 로 정리
- **수정 내용** (`django_server/books/signals.py`):
  - `created=False` 즉시 반환 동작 주석 명확화
  - 파이프라인 시작 시 `book_pk`, `book_list_pk` 로깅 추가

### BUG-06. pgvector 검색 인덱스 누락 ✅
- **수정 내용**: `django_server/books/migrations/0008_book_embeddings_vector_index.py` 추가
  - `halfvec` 캐스팅 방식 HNSW 인덱스: `((embedding::halfvec(2048)) halfvec_cosine_ops)` — pgvector 0.6.0 이상 지원
  - pgvector 미지원 버전에서는 `logger.warning` 후 건너뜀 (마이그레이션 실패 없음)
  - `atomic = False`: `CREATE INDEX`를 트랜잭션 외부에서 실행

### BUG-07. ChatRecommendation 저장 경쟁 조건 ✅
- **수정 내용** (`django_server/chat/views.py` `send_message()`):
  - `Book` 조회를 `_bulk_fetch_books()` 로 일괄 처리 (N+1 동시 제거)
  - `ChatRecommendation.objects.create()` → `get_or_create()` (중복 저장 방어)
  - 항목별 `try/except` 분리 → 한 도서 저장 실패가 다른 도서를 막지 않도록 처리

---

## ✅ Medium Priority — 수정 완료 (2026-04-14)

### FEAT-01. 도서 목록 페이지 — 카테고리 필터 + 배지 표시 ✅
- **수정 내용** (`templates/books/list.html`, `books/views.py`):
  - 필터 바에 카테고리 `<select>` 드롭다운 추가
  - grid/list 뷰 도서 카드에 카테고리 배지 최대 2개 표시 (`book.categories.all|slice:":2"`)
  - `style.css`: `.empty-suggest-label`, `.empty-suggest-cats`, `.badge--link` 스타일 추가

### FEAT-02. 파이프라인 대시보드 — 도서 후보 선택 UI ✅ (이미 구현됨)
- **분석**: `data_pipeline/index.html`에 overlay HTML, JS 모달 함수, SSE `candidates` 이벤트 핸들러가 이미 완전히 구현되어 있음 — 추가 작업 불필요

### FEAT-03. 챗봇 — 순위 배지 및 유사도 점수 표시 ✅
- **수정 내용** (`static/js/chat.js` `makeCard()`):
  - 썸네일 위 `#1`, `#2` 등 rank 배지 추가 (`.rec-card-rank`)
  - 카드 하단에 `유사도 XX%` 표시 (`.rec-card-score`)
  - `chat.css`: `.rec-card-thumb`에 `position: relative` + `.rec-card-rank`, `.rec-card-score` 스타일 추가

### FEAT-04. 목차 — 계층 구조 렌더링 ✅
- **수정 내용** (`templates/books/detail.html`):
  - JS 파서: 줄별 선행 공백으로 depth 계산 → `.toc-item.toc-depth-{0~3}` 클래스 적용
  - 챕터/파트 헤더 패턴 감지 시 `.toc-header` 클래스 추가 (bold 강조)
  - `style.css`: `.toc-list`, `.toc-item`, `.toc-depth-*`, `.toc-header` 스타일 추가

### FEAT-05. 검색 결과 없을 때 카테고리 제안 ✅
- **수정 내용** (`books/views.py`, `templates/books/list.html`):
  - 검색 조건이 있고 결과가 없을 때 카테고리 최대 8개 `suggested_categories` 컨텍스트 전달
  - 빈 결과 영역에 카테고리 배지 링크로 탐색 유도

### REFACTOR-01. 미사용 threshold 설정 제거 ✅
- **수정 내용** (`django_server/config/settings.py`):
  - `RECOMMENDATION_THRESHOLD = 0.5` 제거 — Django 코드베이스에서 미참조, FastAPI `config.py`에서 관리

### REFACTOR-02. Book 조회 공통 함수 분리 ✅ (이미 완료)
- **분석**: `_bulk_fetch_books()` 함수가 이미 `chat/views.py`에 구현되어 `send_message()`, `_enrich_recommendations()`, `_save_stream_result()` 모두에서 사용 중

### REFACTOR-03. 파이프라인 경쟁 조건 수정 ✅
- **수정 내용** (`django_server/data_pipeline/views.py`):
  - 5개 파이프라인 모두 `_running.set()` / `_embed_running.set()` / `_classify_running.set()` / `_year_running.set()` / `_category_running.set()` 호출을 워커 스레드 내부에서 → `threading.Thread(...).start()` 호출 직전으로 이동
  - race condition 제거: `is_set()` 체크 → `set()` → `start()` 순서 보장

---

## ✅ Low Priority — 수정 완료 (2026-04-14)

### PERF-01. 커넥션 풀 튜닝 ✅
- **수정 내용** (`fastapi_server/db.py`):
  - `minconn=2 → 3`, `maxconn=10 → 15`
  - locust 부하 테스트 결과 기반 조정 (동시 ~2 req/s 피크 고려)

### PERF-02. 파이프라인 병렬 처리 ✅
- **수정 내용** (`django_server/data_pipeline/views.py`):
  - `_pipeline_worker`에 `ThreadPoolExecutor(max_workers=3)` 도입
  - API Rate Limit 고려해 최대 3개 병렬 처리
  - 취소 이벤트(`cancel_event`) 병렬 환경에서도 정상 동작

### PERF-03. 캐싱 레이어 추가 ✅
- **수정 내용**:
  - `django_server/config/settings.py`: `CACHES` 설정 추가 (LocMemCache)
  - `books/views.py`: 카테고리 목록 10분 캐시 (`books:all_categories`)
  - `data_pipeline/views.py`: 파이프라인 통계 1분 캐시, 완료 시 캐시 무효화

### PERF-04. DB 인덱스 보완 ✅
- **수정 내용**: `0009_additional_indexes.py` 마이그레이션 추가
  - `idx_book_list_title`: `book_list(title)` 검색 인덱스
  - `idx_books_booklist_active`: `books(book_list_id, is_active)` 복합 인덱스
  - `idx_chat_messages_session_created`: `chat_messages(session_id, created_at)` 인덱스

### SEC-01. Rate Limiting 추가 ✅
- **수정 내용**:
  - `fastapi_server/requirements.txt`: `slowapi>=0.1.9` 추가
  - `fastapi_server/limiter.py`: 공유 Limiter 인스턴스 생성
  - `fastapi_server/main.py`: `app.state.limiter`, `RateLimitExceeded` 핸들러 등록
  - `fastapi_server/routers/chat.py`: `/message`, `/message/stream` 엔드포인트에 `@limiter.limit("30/minute")` 적용

### SEC-02. JWT Secret Key 관리 강화 ✅
- **수정 내용** (`django_server/config/settings.py`):
  - `JWT_SECRET_KEY` 미설정 시 `warnings.warn()`으로 경고 출력
  - `.env.example` 주석 업데이트: 프로덕션 필수 설정 명시

### SEC-03. `DJANGO_DEBUG=False` 기본값 적용 ✅
- **수정 내용** (`.env.example`):
  - `DJANGO_DEBUG=True` → `DJANGO_DEBUG=False` (개발 시 True 설명 주석 추가)

### SEC-04. 스크래핑 timeout 및 파서 명시 ✅
- **수정 내용** (`django_server/books/services.py`):
  - YES24, 교보문고 스크래핑: `BeautifulSoup(..., "lxml")` → `BeautifulSoup(..., "html.parser")`
  - timeout은 이미 설정되어 있었음 (`timeout=10`)

### UX-01. 모바일 반응형 개선 ✅
- **수정 내용** (`django_server/templates/books/manage_form.html`):
  - `@media (max-width: 640px)`: 모드 토글, 폼 액션 버튼 수직 정렬 추가
  - `@media (max-width: 400px)`: 3열 그리드 1열 전환 추가

### UX-02. 파이프라인 단계별 진행 상태 표시 ✅
- **수정 내용**:
  - `data_pipeline/views.py`: `progress_cb`에서 `step` SSE 이벤트 emit
  - `templates/data_pipeline/index.html`: `.progress-step` 요소 추가, `step` 이벤트 핸들러, CSS 스타일 추가

### UX-03. 오류 메시지 구체화 ✅
- **수정 내용** (`static/js/chat.js`):
  - `httpErrorMessage(status)` 함수 추가: 429 Rate Limit, 502/504 Timeout 상황별 안내
  - 일반/스트리밍 모드 양쪽에 적용

### DOCS-01. API 문서 정비 ✅
- **수정 내용**: `docs/api.md` 신규 작성
  - Django REST API 전체 엔드포인트 (챗봇, 도서, 파이프라인)
  - FastAPI 모델 서버 API (chat, embed)
  - 요청/응답 예시 및 오류 코드 포함

---

## 중복 기능 정리 현황

| 중복 항목 | 위치 | 조치 |
|-----------|------|------|
| threshold 정의 | `config.py` 2곳 + `retriever.py` | ✅ REFACTOR-01: Django `settings.py` 미사용 상수 제거 |
| Book 조회 패턴 | `chat/views.py` 3곳 반복 | ✅ REFACTOR-02: `_bulk_fetch_books()` 공통 함수로 통합 |
| 임베딩 재생성 호출 | `signals.py`, `views.py`, `data_pipeline` | ✅ BUG-05: 중복 실행 방어 lock 추가 |
| 난이도 분류 호출 | `embed.py`, `data_pipeline/views.py` | 이미 단일 서비스 함수로 통합 ✅ |

---

## 아키텍처 강점 (유지)

- **역할 분리**: Django(웹·데이터) ↔ FastAPI(LLM·벡터) 분리로 독립 확장 가능
- **멀티소스 폴백**: 알라딘 → YES24 → 교보문고 자동 전환
- **난이도 폴백**: 요청 수준 → 쉬운 난이도 → 어려운 난이도 → 전체 순 검색
- **SSE 스트리밍**: 토큰 단위 실시간 응답
- **정규화 DB 설계**: Publishers, Authors, Categories 분리, book_list UNIQUE constraint
