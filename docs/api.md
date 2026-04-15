# READ:ME API 문서

> 최종 업데이트: 2026-04-16
> FastAPI 자동 문서: `http://localhost:8001/docs`

---

## Django REST API (포트 8000)

### 챗봇

#### POST `/chat/message/`
일반 모드 채팅 메시지 전송 (JSON 응답).

**Request**
```json
{
  "message": "파이썬 입문서 추천해줘",
  "session_id": "abc123"
}
```

**Response** `200 OK`
```json
{
  "answer": "파이썬 입문에 좋은 책을 추천드립니다...",
  "question_type": "specific_search",
  "recommendations": [
    {
      "id": 12,
      "book_list_id": 5,
      "title": "점프 투 파이썬",
      "author": "박응용",
      "publisher": "이지스퍼블리싱",
      "difficulty": "입문",
      "thumbnail_url": "https://...",
      "score": 0.87,
      "rank": 1,
      "book_code": "D-012"
    }
  ]
}
```

**Error Responses**

| Status | 상황 |
|--------|------|
| 429 | Rate Limit 초과 (AI 서버) |
| 500 | 서버 내부 오류 |

---

#### POST `/chat/message/stream/`
스트리밍 모드 채팅 메시지 전송 (SSE).

**Request** — `/chat/message/`와 동일

**Response** `text/event-stream` — 각 이벤트는 `data: {...}` 형식

| `type` | 필드 | 설명 |
|--------|------|------|
| `answer_chunk` | `content` | LLM 응답 토큰 (점진적으로 수신) |
| `done` | `question_type`, `recommendations` | 스트림 종료 + 추천 도서 목록 |
| `error` | `content` | 오류 메시지 (Rate Limit, 타임아웃 등) |

```
data: {"type": "answer_chunk", "content": "파이썬"}
data: {"type": "answer_chunk", "content": " 입문에"}
data: {"type": "done", "question_type": "specific_search", "recommendations": [...]}
```

---

#### GET `/chat/history/`
현재 세션의 채팅 히스토리 조회.

**Response** `200 OK`
```json
{
  "messages": [
    {"role": "user", "content": "파이썬 추천해줘"},
    {"role": "assistant", "content": "점프 투 파이썬을 추천드립니다..."}
  ]
}
```

---

### 도서

#### GET `/books/`
도서 목록 조회 (필터 지원).

**Query Parameters**

| 파라미터 | 타입 | 설명 |
|---------|------|------|
| `q` | string | 제목·저자 검색어 |
| `difficulty` | string | `입문 \| 초급 \| 중급 \| 고급 \| 미분류` |
| `category` | int | 카테고리 ID |

**Response** `200 OK` — HTML 페이지 렌더링

---

#### GET `/books/<id>/`
도서 상세 정보 조회.

**Response** `200 OK` — HTML 페이지 렌더링

---

### 데이터 파이프라인 (staff 전용)

#### POST `/pipeline/run/`
전체 파이프라인 시작 (도서 정보 수집 → 난이도 분류 → 임베딩 생성).

**Response** `200 OK`
```json
{ "job_id": "550e8400-e29b-41d4-a716-446655440000" }
```

**Error** `409 Conflict` — 이미 실행 중

---

#### GET `/pipeline/stream/<job_id>/`
파이프라인 진행 상황 SSE 스트림.

| `type` | 필드 | 설명 |
|--------|------|------|
| `start` | `total` | 처리 대상 도서 수 |
| `progress` | `done`, `total`, `current` | 진행 상황 |
| `step` | `title`, `step` | 현재 도서 단계 |
| `log` | `status`, `title`, `messages` | 처리 결과 로그 |
| `candidates` | `title`, `book_list_id`, `candidates` | 알라딘 후보 도서 목록 |
| `cancelled` | `done`, `total` | 작업 취소 |
| `complete` | `done`, `errors`, `total` | 완료 |
| `fatal` | `error` | 치명적 오류 |

---

#### POST `/pipeline/stop/<job_id>/`
실행 중인 파이프라인 작업 중단.

**Response** `200 OK`
```json
{ "ok": true }
```

---

#### POST `/pipeline/embed/run/`
임베딩 누락 도서 처리 시작.

#### GET `/pipeline/embed/stream/<job_id>/`
임베딩 진행 상황 SSE (파이프라인과 동일한 이벤트 형식).

---

#### POST `/pipeline/classify/run/`
전체 도서(설명 있는) 난이도 분류 시작. 결과는 DB에 즉시 저장하지 않고 `preview` 이벤트로 반환합니다.

#### GET `/pipeline/classify/stream/<job_id>/`
난이도 분류 진행 상황 SSE.

| `type` | 필드 | 설명 |
|--------|------|------|
| `start` | `total` | 처리 대상 도서 수 |
| `progress` | `done`, `total`, `current` | 진행 상황 |
| `log` | `status`, `title`, `difficulty` | 도서별 분류 결과 |
| `cancelled` | `done`, `total` | 작업 취소 |
| `preview` | `done`, `errors`, `total`, `results` | 분류 완료 + 결과 목록 |
| `fatal` | `error` | 치명적 오류 |

`preview.results` 형식:
```json
[
  {
    "book_list_id": 5,
    "title": "점프 투 파이썬",
    "old_difficulty": "",
    "new_difficulty": "입문"
  }
]
```

#### POST `/pipeline/classify/apply/`
난이도 분류 미리보기 결과를 선택적으로 DB에 반영합니다.

**Request**
```json
{
  "items": [
    { "book_list_id": 5, "difficulty": "입문" },
    { "book_list_id": 12, "difficulty": "중급" }
  ]
}
```

**Response** `200 OK`
```json
{ "ok": true, "applied": 2 }
```

---

#### POST `/pipeline/year/run/`
출판 연도 추출 시작.

#### GET `/pipeline/year/stream/<job_id>/`
연도 추출 진행 상황 SSE.

---

#### POST `/pipeline/category/run/`
카테고리 분류 시작.

#### GET `/pipeline/category/stream/<job_id>/`
카테고리 분류 진행 상황 SSE.

---

#### POST `/pipeline/candidates/search/`
알라딘 도서 후보 검색 (staff 전용, AJAX).

**Request**
```json
{ "book_list_id": 42 }
```

**Response** `200 OK`
```json
{
  "candidates": [
    {
      "title": "점프 투 파이썬",
      "author": "박응용",
      "publisher": "이지스퍼블리싱",
      "thumbnail_url": "https://...",
      "item_id": "12345678"
    }
  ]
}
```

---

#### POST `/pipeline/candidates/apply/`
선택한 알라딘 후보 도서 정보 적용 (staff 전용, AJAX).

**Request**
```json
{ "book_list_id": 42, "item_id": "12345678" }
```

**Response** `200 OK`
```json
{ "ok": true, "updated_fields": ["thumbnail_url", "description", "toc"] }
```

---

#### POST `/pipeline/yes24/run/`
YES24 후보 수집 파이프라인 시작.

#### GET `/pipeline/yes24/stream/<job_id>/`
YES24 후보 수집 진행 상황 SSE.

#### GET `/pipeline/yes24/candidates/`
저장된 YES24 후보 목록 반환 (AJAX).

**Response** `200 OK`
```json
{
  "groups": [
    {
      "book_list_id": 42,
      "book_title": "점프 투 파이썬",
      "book_codes": ["D-012"],
      "candidates": [
        { "title": "점프 투 파이썬", "subtitle": "", "href": "https://...", "edition_info": "" }
      ]
    }
  ]
}
```

#### POST `/pipeline/yes24/apply/`
YES24 후보 상세 수집 후 DB 덮어쓰기 (AJAX).

**Request**
```json
{ "book_list_id": 42, "href": "https://www.yes24.com/..." }
```

**Response** `200 OK`
```json
{ "ok": true, "updated_fields": ["description", "toc", "thumbnail_url"] }
```

#### POST `/pipeline/yes24/dismiss/`
YES24 후보 건너뜀 (AJAX).

**Request**
```json
{ "book_list_id": 42 }
```

**Response** `200 OK`
```json
{ "ok": true }
```

#### POST `/pipeline/yes24/reset/`
수집된 YES24 후보 전체 초기화 (AJAX).

---

#### GET `/pipeline/thumbnails/scan/`
미사용 썸네일 파일 목록 반환 (AJAX).

#### POST `/pipeline/thumbnails/clean/`
미사용 썸네일 파일 일괄 삭제 (AJAX).

---

## FastAPI 모델 서버 API (포트 8001)

> Django 서버에서 내부적으로 호출합니다. 직접 외부 호출은 권장하지 않습니다.
> 자동 문서: `http://localhost:8001/docs`

### 챗봇

#### POST `/chat/message`
질문 분류 + LLM 응답 + 벡터 검색 (Rate Limit: 30회/분/IP).

**Request**
```json
{
  "message": "파이썬 입문서 추천해줘",
  "history": [
    {"role": "user", "content": "이전 질문"},
    {"role": "assistant", "content": "이전 답변"}
  ],
  "session_id": "abc123"
}
```

**Response** `200 OK`
```json
{
  "answer": "파이썬 입문에 좋은 책을...",
  "question_type": "specific_search",
  "recommendations": [
    { "book_list_id": 5, "score": 0.87, "rank": 1 }
  ]
}
```

**질문 유형 (`question_type`)**

| 값 | 분류 방식 | 설명 |
|----|------|------|
| `keyword_search` | 정규식 즉시 감지 | 도서 목록 직접 조회 (LLM 없음) |
| `specific_search` | LLM | 특정 기술·키워드 탐색 |
| `goal_oriented` | LLM | 진로·목적 기반 큐레이션 |
| `career_certification` | LLM | 자격증·포트폴리오 |
| `level_based` | 정규식 / LLM | 수준별 추천 |
| `out_of_scope` | LLM | 도서 무관 질문 |

---

#### POST `/chat/message/stream`
스트리밍 응답 (SSE, Rate Limit: 30회/분/IP).

---

### 임베딩

#### POST `/embed/book`
도서 임베딩 생성·갱신.

**Request**
```json
{
  "book_list_id": 5,
  "title": "점프 투 파이썬",
  "description": "파이썬 기초부터..."
}
```

**Response** `200 OK`
```json
{ "ok": true, "book_list_id": 5 }
```

---

#### POST `/embed/classify`
LLM 난이도 분류.

**Request**
```json
{
  "title": "점프 투 파이썬",
  "description": "파이썬 기초부터...",
  "reviews": ["쉽게 따라할 수 있어요"]
}
```

**Response** `200 OK`
```json
{ "difficulty": "입문" }
```

---

#### POST `/embed/classify-category`
LLM 카테고리 분류.

**Request**
```json
{
  "title": "점프 투 파이썬",
  "description": "파이썬 기초부터..."
}
```

**Response** `200 OK`
```json
{ "categories": ["프로그래밍 언어", "웹 개발"] }
```

---

#### GET `/health`
서버 헬스 체크.

**Response** `200 OK`
```json
{ "status": "ok" }
```
