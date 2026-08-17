# ERD (Entity Relationship Diagram)

아래 다이어그램은 [Mermaid](https://mermaid.js.org/) 문법으로 작성되었습니다.  
GitHub, Notion, VSCode(Mermaid Preview 확장) 등에서 렌더링됩니다.

```mermaid
erDiagram

    %% ── 도서 도메인 ──────────────────────────────────────────

    publishers {
        int     id          PK
        varchar name        UK
        timestamptz created_at
    }

    authors {
        int     id          PK
        varchar name        UK
        timestamptz created_at
    }

    categories {
        int     id          PK
        varchar name        UK
        text    description
        timestamptz created_at
    }

    book_list {
        int      id                  PK
        varchar  title                   "판차 제거 정제 제목"
        varchar  subtitle                "부제 (있는 경우)"
        varchar  edition                 "(개정2판) 등 분리된 판차"
        smallint publication_year        "제목·설명에서 추출한 연도"
        int      author_id          FK   "대표 저자"
        int      publisher_id       FK
        text     description             "도서 소개 (max 2,000자)"
        text     toc                     "목차 (max 8,000자)"
        enum     difficulty              "입문|초급|중급|고급"
        text     thumbnail_url           "표지 이미지 원본 URL"
        varchar  thumbnail               "로컬 저장 썸네일 파일 경로"
        timestamptz created_at
        timestamptz updated_at
    }

    book_list_categories {
        int book_list_id    PK,FK
        int category_id     PK,FK
    }

    books {
        int     id              PK
        varchar book_code       UK  "D-246 등 원본 코드"
        int     book_list_id    FK
        bool    is_active
        timestamptz created_at
        timestamptz updated_at
    }

    book_embeddings {
        int     id              PK
        int     book_list_id    UK,FK  "book_list 단위 중복 방지"
        vector  embedding              "dim=1024 (NVIDIA nv-embedqa-e5-v5)"
        timestamptz created_at
        timestamptz updated_at
    }

    kyobo_candidates {
        int     id              PK
        int     book_list_id    FK     "연결된 도서 정보"
        varchar title                  "교보문고 검색 결과 도서명"
        varchar subtitle               "부제/저자"
        url     href                   "교보문고 상세 페이지 URL"
        varchar edition_info           "개정판 정보"
        timestamptz created_at
    }

    %% ── 챗봇 도메인 ──────────────────────────────────────────

    chat_sessions {
        uuid    id              PK
        timestamptz created_at
        timestamptz last_active_at
    }

    chat_messages {
        int     id              PK
        uuid    session_id      FK
        enum    role                "user|assistant"
        text    content
        enum    question_type       "keyword_search|specific_search|goal_oriented|career_certification|level_based|out_of_scope"
        timestamptz created_at
    }

    chat_recommendations {
        int      id                 PK
        int      message_id         FK
        int      book_id            FK
        float    similarity_score       "코사인 유사도"
        smallint rank                   "1=최상위, 3 초과=더보기"
        timestamptz created_at
    }

    %% ── 관계 ─────────────────────────────────────────────────

    publishers      ||--o{ book_list            : "출판"
    authors         ||--o{ book_list            : "집필"
    book_list       ||--o{ book_list_categories : "분류"
    categories      ||--o{ book_list_categories : "분류"
    book_list       ||--o{ books               : "코드 등록"
    book_list       ||--|| book_embeddings      : "임베딩"
    book_list       ||--o{ kyobo_candidates    : "후보"
    chat_sessions   ||--o{ chat_messages        : "포함"
    chat_messages   ||--o{ chat_recommendations : "추천"
    books           ||--o{ chat_recommendations : "추천됨"
```

---

## 관계 요약

```
publishers  (1) ──── (N) book_list
authors     (1) ──── (N) book_list   [ 대표 저자 단일 FK ]
categories  (M) ──── (N) book_list   [ book_list_categories 경유 ]
book_list   (1) ──── (N) books        [ 동일 책의 다수 코드 지원, 중복 수집 방지 ]
book_list   (1) ──── (1) book_embeddings
book_list   (1) ──── (N) kyobo_candidates  [ 교보문고 검색 후보 임시 저장 ]
chat_sessions  (1) ── (N) chat_messages
chat_messages  (1) ── (N) chat_recommendations
books        (1) ──── (N) chat_recommendations
```

---

## 데이터 흐름

```
[CSV 초기 적재 (1회)]
        │
        ▼
   load_books 커맨드
     → publishers, authors upsert
     → book_list 생성 (title + edition 분리, author_id, publisher_id)
     → books 생성 (book_code D-NNN, book_list_id FK)
        │
        ▼
   run_pipeline (BookList 단위)
        │
        ├──① 정보 수집 (교보문고 스크래핑)
        │     검색 → 후보 선택 → 상세 페이지 ──► description, toc, thumbnail_url
        │
        ├──② LLM 난이도 분류 (NVIDIA NIM)
        │     title + description ──► difficulty (입문/초급/중급/고급)
        │     ※ 분류 결과는 미리보기 후 선택적으로 DB 반영
        │
        └──③ 임베딩 생성 (NVIDIA nv-embedqa-e5-v5)
              title + description ──► VECTOR(1024) ──► book_embeddings upsert

[별도 파이프라인]
   출판 연도 추출:    title / description ──► publication_year
   카테고리 분류:    title + description ──► LLM ──► book_list_categories (1~3개)
   교보문고 후보 수집:  검색 결과 ──► kyobo_candidates ──► 수동 선택 후 book_list 갱신

[사용자 챗봇 질문]
        │
        ▼
   chat_sessions (신규 or 기존 UUID)
        │
        ▼
   chat_messages (role=user)
        │
        ▼
   FastAPI(LangChain) ──► 질문 분류(question_type)
        │
        ├── keyword_search       ──► ILIKE DB 검색 (LLM 없음)
        ├── specific_search      ──► 벡터 검색 + specific_search_chain
        ├── goal_oriented        ──► 벡터 검색 + goal_oriented_chain
        ├── career_certification ──► 벡터 검색 + 후처리 필터 + career_certification_chain
        ├── level_based          ──► 난이도 필터 벡터 검색 + level_based_chain
        └── out_of_scope         ──► "IT·개발 도서 챗봇 안내" 반환 (DB 검색 없음)
                │
                ▼
   pgvector 코사인 유사도 검색 ──► similarity_score ≥ threshold
        │
        ▼
   chat_messages (role=assistant) + chat_recommendations (rank, score)
        │
        ▼
   Django 응답 ──► LLM 텍스트 + 추천 카드 UI (rank ≤ 3 기본, 더보기)
```
