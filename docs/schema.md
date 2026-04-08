# DB Schema

## 개요

- **DBMS**: PostgreSQL 16 (Docker: `pgvector/pgvector:pg16`)
- **Vector Extension**: pgvector (`CREATE EXTENSION IF NOT EXISTS vector;`)
- **문자셋**: UTF-8

---

## 테이블 목록

| 테이블명 | 설명 |
|---|---|
| `publishers` | 출판사 |
| `authors` | 저자 |
| `categories` | 도서 카테고리/주제 태그 |
| `books` | 도서 메인 정보 |
| `book_authors` | 도서-저자 다대다 관계 |
| `book_categories` | 도서-카테고리 다대다 관계 |
| `book_embeddings` | 도서 설명 벡터 임베딩 (pgvector) |
| `chat_sessions` | 챗봇 세션 |
| `chat_messages` | 챗봇 메시지 기록 |
| `chat_recommendations` | 챗봇 응답에서 추천된 도서 |

---

## DDL

```sql
-- ============================================================
-- Extension
-- ============================================================
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";


-- ============================================================
-- 1. publishers
-- ============================================================
CREATE TABLE publishers (
    id          SERIAL          PRIMARY KEY,
    name        VARCHAR(255)    NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE publishers IS '출판사';


-- ============================================================
-- 2. authors
-- ============================================================
CREATE TABLE authors (
    id          SERIAL          PRIMARY KEY,
    name        VARCHAR(255)    NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE authors IS '저자 (정규화 – 다수 저자는 book_authors 경유)';


-- ============================================================
-- 3. categories
-- ============================================================
CREATE TABLE categories (
    id          SERIAL          PRIMARY KEY,
    name        VARCHAR(100)    NOT NULL UNIQUE,   -- e.g. '네트워크', '알고리즘', 'OS'
    description TEXT,
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE categories IS '도서 주제/카테고리';


-- ============================================================
-- 4. books
-- ============================================================
CREATE TYPE difficulty_level AS ENUM ('입문', '초급', '중급', '고급');

CREATE TABLE books (
    id              SERIAL              PRIMARY KEY,
    book_code       VARCHAR(20)         NOT NULL UNIQUE,   -- 원본 CSV 번호 (D-246 등)
    title           VARCHAR(500)        NOT NULL,
    publisher_id    INT                 NOT NULL REFERENCES publishers(id) ON DELETE RESTRICT,
    isbn            VARCHAR(20),
    thumbnail_url   TEXT,
    description     TEXT,
    difficulty      difficulty_level,
    published_at    DATE,
    page_count      INT,
    is_active       BOOLEAN             NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ         NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ         NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  books              IS '도서 메인 정보';
COMMENT ON COLUMN books.book_code   IS '원본 CSV 식별 코드 (D-xxx)';
COMMENT ON COLUMN books.difficulty  IS 'LLM 리뷰 분석으로 산출된 난이도';

-- updated_at 자동 갱신 트리거
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_books_updated_at
    BEFORE UPDATE ON books
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ============================================================
-- 5. book_authors  (books ↔ authors M:N)
-- ============================================================
CREATE TABLE book_authors (
    book_id         INT         NOT NULL REFERENCES books(id)   ON DELETE CASCADE,
    author_id       INT         NOT NULL REFERENCES authors(id) ON DELETE RESTRICT,
    author_order    SMALLINT    NOT NULL DEFAULT 1,   -- 저자 표기 순서
    PRIMARY KEY (book_id, author_id)
);

COMMENT ON TABLE book_authors IS '도서-저자 다대다 (저자 순서 포함)';


-- ============================================================
-- 6. book_categories  (books ↔ categories M:N)
-- ============================================================
CREATE TABLE book_categories (
    book_id         INT     NOT NULL REFERENCES books(id)       ON DELETE CASCADE,
    category_id     INT     NOT NULL REFERENCES categories(id)  ON DELETE RESTRICT,
    PRIMARY KEY (book_id, category_id)
);

COMMENT ON TABLE book_categories IS '도서-카테고리 다대다';


-- ============================================================
-- 7. book_embeddings  (pgvector)
-- ============================================================
CREATE TABLE book_embeddings (
    id          SERIAL      PRIMARY KEY,
    book_id     INT         NOT NULL UNIQUE REFERENCES books(id) ON DELETE CASCADE,
    embedding   VECTOR(1024) NOT NULL,    -- NVIDIA nv-embedqa-e5-v5 기준 (dim=1024)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE book_embeddings IS '도서 설명 + 제목 벡터 임베딩 (pgvector)';

CREATE TRIGGER trg_book_embeddings_updated_at
    BEFORE UPDATE ON book_embeddings
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 코사인 유사도 검색용 HNSW 인덱스
CREATE INDEX idx_book_embeddings_hnsw
    ON book_embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);


-- ============================================================
-- 8. chat_sessions
-- ============================================================
CREATE TABLE chat_sessions (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_active_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE chat_sessions IS '챗봇 세션 (비로그인 사용자는 UUID로 관리)';


-- ============================================================
-- 9. chat_messages
-- ============================================================
CREATE TYPE message_role      AS ENUM ('user', 'assistant');
CREATE TYPE question_type     AS ENUM ('roadmap', 'level_based', 'general', 'unrelated');

CREATE TABLE chat_messages (
    id              SERIAL          PRIMARY KEY,
    session_id      UUID            NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role            message_role    NOT NULL,
    content         TEXT            NOT NULL,
    question_type   question_type,              -- assistant 메시지에만 설정
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  chat_messages               IS '챗봇 대화 메시지';
COMMENT ON COLUMN chat_messages.question_type IS 'roadmap | level_based | general | unrelated';

CREATE INDEX idx_chat_messages_session ON chat_messages(session_id, created_at);


-- ============================================================
-- 10. chat_recommendations
-- ============================================================
CREATE TABLE chat_recommendations (
    id                  SERIAL      PRIMARY KEY,
    message_id          INT         NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    book_id             INT         NOT NULL REFERENCES books(id)         ON DELETE CASCADE,
    similarity_score    FLOAT       NOT NULL,   -- 벡터 유사도 (0~1)
    rank                SMALLINT    NOT NULL,   -- 응답 내 노출 순위
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (message_id, book_id)
);

COMMENT ON TABLE  chat_recommendations                  IS '챗봇 응답에서 추천된 도서 (카드 UI)';
COMMENT ON COLUMN chat_recommendations.similarity_score IS '코사인 유사도 – threshold 이상인 것만 저장';
COMMENT ON COLUMN chat_recommendations.rank             IS '1부터 시작, 3 초과분은 "더보기" UI';

CREATE INDEX idx_chat_rec_message ON chat_recommendations(message_id, rank);
```

---

## 주요 설계 결정

| 항목 | 내용 |
|---|---|
| 저자 정규화 | CSV의 `,` 구분 저자를 `authors` + `book_authors`(순서 포함)로 분리 |
| 출판사 정규화 | `publishers` 별도 테이블 – 중복 데이터 방지 |
| 카테고리 | 챗봇 질문 분류/추천 품질 향상을 위해 추가 |
| 난이도 | Enum (`입문/초급/중급/고급`) – 리뷰 스크래핑 후 LLM 분류 |
| 벡터 임베딩 | `book_embeddings`에 분리 – 책 정보와 독립 업데이트 가능 |
| 챗봇 세션 | UUID 기반 – 비로그인 사용자도 대화 맥락 유지 |
| 추천 카드 | `similarity_score` 저장 → threshold 재조정 시 재활용 가능 |
| `updated_at` | 트리거로 자동 관리 |
