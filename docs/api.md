# READ:ME API 문서

> 최종 업데이트: 2026-04-14
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

**Response** `text/event-stream`

각 이벤트는 `data: {...}\n\n` 형식의 JSON 스트림입니다.

| `type` | 필드 | 설명 |
|--------|------|------|
| `answer_chunk` | `content` | LLM 응답 토큰 (점진적으로 수신) |
| `done` | `question_type`, `recommendations` | 스트림 종료 + 추천 도서 목록 |
| `error` | `content` | 오류 메시지 (Rate Limit, 타임아웃 등) |

**예시 스트림**
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

#### GET `/books/api/naver-search/`
네이버 도서 검색 API 프록시 (staff 전용, 도서 추가 팝오버용).

**Query Parameters**
| 파라미터 | 타입 | 설명 |
|---------|------|------|
| `q` | string | 검색어 |

**Response** `200 OK`
```json
{
  "results": [
    {
      "title": "점프 투 파이썬",
      "author": "박응용",
      "publisher": "이지스퍼블리싱",
      "thumbnail_url": "https://...",
      "description": "파이썬 기초부터...",
      "isbn": "9791163030904"
    }
  ]
}
```

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
| `step` | `title`, `step` | 현재 도서 단계 (정보 수집 / 난이도 분류 / 임베딩 생성) |
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

#### POST `/pipeline/classify/run/`
난이도 미분류 도서 일괄 분류 시작.

#### POST `/pipeline/year/run/`
출판 연도 추출 시작.

#### POST `/pipeline/category/run/`
카테고리 분류 시작.

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
      "isbn": "9791163030904",
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
{ "ok": true, "updated_fields": ["thumbnail_url", "description", "isbn", "toc"] }
```

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
| 값 | 설명 |
|----|------|
| `keyword_search` | 도서 목록 직접 조회 |
| `specific_search` | 특정 기술·키워드 탐색 |
| `goal_oriented` | 진로·목적 기반 큐레이션 |
| `career_certification` | 자격증·포트폴리오 |
| `level_based` | 수준별 추천 |
| `out_of_scope` | 도서 무관 질문 |

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
  "reviews": ["쉽게 따라할 수 있어요", "입문자에게 딱이에요"]
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
{ "categories": ["Python", "웹 개발"] }
```

---

#### GET `/health`
서버 헬스 체크.

**Response** `200 OK`
```json
{ "status": "ok" }
```
