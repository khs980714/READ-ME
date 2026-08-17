# READ:ME 시스템 아키텍처

> Mermaid 다이어그램은 GitHub · Notion · VSCode(Mermaid Preview 확장) 에서 렌더링됩니다.

---

## 1. 전체 컨테이너 구조

| 컴포넌트 | 포트 | 주요 역할 |
|---|---|---|
| **Django** | 8000 | 웹 UI, 도서 관리, 챗봇 프록시, SSE 스트림, 데이터 파이프라인 UI |
| **FastAPI** | 8001 | LLM 추론, 임베딩 생성, 질문 분류, LangChain 체인 라우팅, Rate Limiting |
| **PostgreSQL 16** | 5432 | 도서 메타데이터, pgvector 임베딩(dim=1024), 챗봇 세션·메시지 |

```mermaid
graph LR
    Browser["브라우저"]

    subgraph docker["Docker Compose"]
        Django["Django :8000"]
        FastAPI["FastAPI :8001"]
        DB["PostgreSQL 16 :5432 + pgvector"]
    end

    NVIDIA["NVIDIA NIM API"]
    Kyobo["교보문고 (스크래핑)"]
    LangSmith["LangSmith"]

    Browser -- "HTTP / SSE" --> Django
    Django -- "내부 HTTP" --> FastAPI
    Django -- psycopg2 --> DB
    FastAPI -- psycopg2 --> DB
    FastAPI -- HTTPS --> NVIDIA
    Django -- HTTPS --> Kyobo
    FastAPI -.-> LangSmith
```

---

## 2. Django 앱 구조

```mermaid
graph TD
    subgraph books["books 앱"]
        BookList["BookList (마스터)"]
        Book["Book (코드 레지스트리)"]
        Category["Category"]
        Kyobo["KyoboCandidate"]
        Embedding["BookEmbedding"]
    end

    subgraph chat["chat 앱"]
        Session["ChatSession"]
        Message["ChatMessage"]
        Rec["ChatRecommendation"]
    end

    subgraph pipeline["data_pipeline 앱"]
        PipelineView["SSE 파이프라인 뷰"]
        Commands["관리 커맨드"]
    end

    BookList --> Book
    BookList --> Embedding
    BookList --> Kyobo
    Session --> Message
    Message --> Rec
    Book --> Rec
    pipeline --> books
```

---

## 3. 데이터 수집 파이프라인

파이프라인은 웹 UI(데이터 관리 탭) 또는 CLI 커맨드로 실행합니다.

```mermaid
flowchart TD
    CSV["data.csv"] --> LoadBooks["load_books 커맨드"]
    LoadBooks --> DB_init["book_list + books 초기 생성"]

    DB_init --> Step1
    DB_init --> Step4
    DB_init --> Step5
    DB_init --> Step6

    subgraph pipeline["파이프라인 (순서 실행)"]
        Step1["① 정보 수집 (교보문고)"] --> Step2["② 난이도 분류 (LLM)"] --> Step3["③ 임베딩 생성 (nv-embedqa)"]
    end

    subgraph extra["별도 파이프라인"]
        Step4["출판 연도 추출"]
        Step5["카테고리 분류 (LLM)"]
        Step6["교보문고 후보 수집 → 수동 선택"]
    end

    pipeline --> DB_final[("PostgreSQL")]
    extra --> DB_final
```

### 정보 수집 전략

```
교보문고 검색 → 후보 선택 → 상세 페이지 스크래핑 → description · toc · thumbnail_url
```

### 난이도 분류 플로우 (v2 — 미리보기 방식)

```mermaid
flowchart LR
    AllBooks["설명 있는 전체 도서"] --> LLM["NVIDIA NIM LLM 분류"]
    LLM --> Preview["결과 미리보기 테이블"]
    Preview --> Select["관리자 항목 선택/해제"]
    Select --> Apply["DB 반영"]
    Select --> Cancel["취소 (미반영)"]
```

---

## 4. 챗봇 응답 흐름

```mermaid
flowchart TD
    User(["사용자 질문"]) --> Django["Django /chat/api/"]
    Django --> FastAPI["FastAPI /chat/message"]
    FastAPI --> Classifier["Classifier (정규식 + LLM)"]

    Classifier -->|keyword_search| KW["ILIKE DB 검색 (LLM 없음)"]
    Classifier -->|out_of_scope| OOS["거절 안내 반환"]
    Classifier -->|level_based| LB_ext["난이도 추출 → 필터 벡터 검색"]
    Classifier -->|specific_search| VS["벡터 검색 (pgvector)"]
    Classifier -->|goal_oriented| VS
    Classifier -->|career_certification| VS

    KW --> Resp["Django 후처리 + DB 저장"]
    OOS --> Resp
    LB_ext --> LB_chain["level_based_chain (LLM)"] --> Resp
    VS --> Chain["해당 체인 (LLM)"] --> Resp

    Resp --> Browser(["브라우저 응답 (JSON / SSE)"])
```

---

## 5. DB 테이블 의존 관계

```mermaid
graph LR
    publishers --> book_list
    authors --> book_list
    book_list --> books
    book_list --> book_embeddings
    book_list --> book_list_categories
    categories --> book_list_categories
    book_list --> kyobo_candidates
    chat_sessions --> chat_messages
    chat_messages --> chat_recommendations
    books --> chat_recommendations
```

---

## 6. SSE 스트리밍 시퀀스

```mermaid
sequenceDiagram
    participant B as 브라우저
    participant D as Django
    participant F as FastAPI
    participant L as NVIDIA NIM

    B->>D: POST /chat/api/message/stream/
    D->>F: POST /chat/message/stream
    F->>L: classify_question()
    L-->>F: question_type

    alt keyword_search
        F->>F: retrieve_books_by_keyword (DB ILIKE)
        F-->>D: answer_chunk
        F-->>D: done + recommendations
    else out_of_scope
        F-->>D: answer_chunk (거절)
        F-->>D: done + []
    else level_based
        F->>F: extract_difficulty + vector_search_by_difficulty
        F->>L: astream
        loop 토큰
            L-->>F: chunk
            F-->>D: answer_chunk
            D-->>B: answer_chunk
        end
        F-->>D: done + recommendations
    else specific / goal / career
        F->>F: vector_search
        F->>L: astream
        loop 토큰
            L-->>F: chunk
            F-->>D: answer_chunk
            D-->>B: answer_chunk
        end
        F-->>D: done + recommendations
    end

    D->>D: _enrich_recommendations + _save_stream_result
    D-->>B: done + enriched recommendations
```

---

## 7. Rate Limiting

| 엔드포인트 | 제한 |
|---|---|
| `POST /chat/message` | 30회 / 분 / IP |
| `POST /chat/message/stream` | 30회 / 분 / IP |

초과 시 `429 Too Many Requests`. Django 챗봇 뷰에서 429 응답을 감지하여 사용자에게 안내합니다.
