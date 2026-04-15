# DB Schema

## 개요

- **DBMS**: PostgreSQL 16 (Docker: `pgvector/pgvector:pg16`)
- **Vector Extension**: pgvector (`CREATE EXTENSION IF NOT EXISTS vector;`)
- **문자셋**: UTF-8

---

## 테이블 목록

| 테이블명 | 설명 |
|---|---|
| `publishers` | 출판사 (정규화) |
| `authors` | 저자 (정규화) |
| `categories` | 도서 카테고리 (12종, LLM 자동 분류) |
| `book_list` | **도서 정보 마스터** — `(title, edition, author, publisher)` 조합으로 중복 방지 |
| `book_list_categories` | book_list ↔ categories 다대다 |
| `books` | 도서 코드 레지스트리 — book_code(`D-NNN`) + book_list_id FK |
| `book_embeddings` | 도서 설명 벡터 임베딩 (pgvector, dim=1024, book_list 단위) |
| `yes24_candidates` | YES24 검색 1차 수집 결과 — 사용자가 선택 전까지 임시 저장 |
| `chat_sessions` | 챗봇 세션 (UUID, 비로그인 지원) |
| `chat_messages` | 챗봇 대화 메시지 |
| `chat_recommendations` | 챗봇 응답에서 추천된 도서 + 유사도 점수 |

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

COMMENT ON TABLE publishers IS '출판사 (정규화)';


-- ============================================================
-- 2. authors
-- ============================================================
CREATE TABLE authors (
    id          SERIAL          PRIMARY KEY,
    name        VARCHAR(255)    NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE authors IS '저자 (정규화)';


-- ============================================================
-- 3. categories
-- ============================================================
CREATE TABLE categories (
    id          SERIAL          PRIMARY KEY,
    name        VARCHAR(100)    NOT NULL UNIQUE,
    description TEXT,
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE categories IS 'LLM 자동 분류 카테고리 (12종)';

-- 기본 카테고리 12종
INSERT INTO categories (name) VALUES
  ('프로그래밍 언어'), ('웹 개발'), ('모바일 개발'), ('데이터베이스'),
  ('자료구조·알고리즘'), ('컴퓨터 과학'), ('인공지능·데이터'), ('DevOps·클라우드'),
  ('소프트웨어 공학'), ('보안'), ('자격증·취업'), ('IT 교양');


-- ============================================================
-- 4. book_list  (도서 정보 마스터)
-- ============================================================
CREATE TYPE difficulty_level AS ENUM ('입문', '초급', '중급', '고급');

CREATE TABLE book_list (
    id                  SERIAL              PRIMARY KEY,
    title               VARCHAR(500)        NOT NULL,
    subtitle            VARCHAR(500)        NOT NULL DEFAULT '',
    edition             VARCHAR(200)        NOT NULL DEFAULT '',
    publication_year    SMALLINT,
    author_id           INT                 NOT NULL REFERENCES authors(id)    ON DELETE RESTRICT,
    publisher_id        INT                 NOT NULL REFERENCES publishers(id) ON DELETE RESTRICT,
    description         TEXT                NOT NULL DEFAULT '',
    toc                 TEXT                NOT NULL DEFAULT '',
    difficulty          difficulty_level,
    thumbnail_url       TEXT                NOT NULL DEFAULT '',
    thumbnail           VARCHAR(500)        NOT NULL DEFAULT '',    -- 로컬 저장 파일 경로
    created_at          TIMESTAMPTZ         NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ         NOT NULL DEFAULT NOW(),
    UNIQUE (title, edition, author_id, publisher_id)
);

COMMENT ON TABLE  book_list                     IS '도서 정보 마스터 — (title, edition, author, publisher) 조합으로 중복 방지';
COMMENT ON COLUMN book_list.subtitle            IS '부제 — 제목과 별도로 저장';
COMMENT ON COLUMN book_list.edition             IS '판차 정보 — 제목에서 분리. 예: (개정2판), (심화편)';
COMMENT ON COLUMN book_list.publication_year    IS '출판 연도 — 제목 또는 설명에서 자동 추출한 4자리 연도';
COMMENT ON COLUMN book_list.description         IS '도서 소개 (최대 2,000자)';
COMMENT ON COLUMN book_list.toc                 IS '목차 (최대 3,000자)';
COMMENT ON COLUMN book_list.difficulty          IS 'LLM이 도서 설명 기반으로 분류한 난이도';
COMMENT ON COLUMN book_list.thumbnail_url       IS '표지 이미지 원본 소스 URL';
COMMENT ON COLUMN book_list.thumbnail           IS '로컬 볼륨에 저장된 썸네일 파일 경로 (우선 표시)';

-- updated_at 자동 갱신 트리거
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_book_list_updated_at
    BEFORE UPDATE ON book_list
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ============================================================
-- 5. book_list_categories  (book_list ↔ categories M:N)
-- ============================================================
CREATE TABLE book_list_categories (
    book_list_id    INT     NOT NULL REFERENCES book_list(id)   ON DELETE CASCADE,
    category_id     INT     NOT NULL REFERENCES categories(id)  ON DELETE RESTRICT,
    PRIMARY KEY (book_list_id, category_id)
);

COMMENT ON TABLE book_list_categories IS 'book_list ↔ categories 다대다 (LLM 자동 분류 결과)';


-- ============================================================
-- 6. books  (도서 코드 레지스트리)
-- ============================================================
CREATE TABLE books (
    id              SERIAL          PRIMARY KEY,
    book_code       VARCHAR(20)     NOT NULL UNIQUE,    -- D-246 형식
    book_list_id    INT             NOT NULL REFERENCES book_list(id) ON DELETE CASCADE,
    is_active       BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  books           IS '도서 코드 레지스트리 — book_code(D-NNN)와 book_list를 연결';
COMMENT ON COLUMN books.book_code IS '원본 CSV 식별 코드 (D-xxx 형식)';

CREATE TRIGGER trg_books_updated_at
    BEFORE UPDATE ON books
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ============================================================
-- 7. book_embeddings  (pgvector, book_list 단위)
-- ============================================================
CREATE TABLE book_embeddings (
    id              SERIAL          PRIMARY KEY,
    book_list_id    INT             NOT NULL UNIQUE REFERENCES book_list(id) ON DELETE CASCADE,
    embedding       VECTOR(1024)    NOT NULL,    -- NVIDIA nv-embedqa-e5-v5 기준 (dim=1024)
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  book_embeddings             IS '도서 제목+설명 벡터 임베딩 (pgvector, book_list 단위로 중복 방지)';
COMMENT ON COLUMN book_embeddings.embedding   IS 'dim=1024 (NVIDIA nv-embedqa-e5-v5)';

CREATE TRIGGER trg_book_embeddings_updated_at
    BEFORE UPDATE ON book_embeddings
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 코사인 유사도 검색용 HNSW 인덱스
CREATE INDEX idx_book_embeddings_hnsw
    ON book_embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);


-- ============================================================
-- 8. yes24_candidates  (YES24 검색 후보 임시 저장)
-- ============================================================
CREATE TABLE yes24_candidates (
    id              SERIAL          PRIMARY KEY,
    book_list_id    INT             NOT NULL REFERENCES book_list(id) ON DELETE CASCADE,
    title           VARCHAR(500)    NOT NULL,
    subtitle        VARCHAR(500)    NOT NULL DEFAULT '',
    href            VARCHAR(1000)   NOT NULL,   -- YES24 상세 페이지 URL
    edition_info    VARCHAR(200)    NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  yes24_candidates          IS 'YES24 검색 1차 수집 후보 — 관리자가 선택하면 book_list 덮어쓰기, 그 전까지 임시 보관';
COMMENT ON COLUMN yes24_candidates.href     IS 'YES24 도서 상세 페이지 URL (상세 수집 시 사용)';


-- ============================================================
-- 9. chat_sessions
-- ============================================================
CREATE TABLE chat_sessions (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_active_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE chat_sessions IS '챗봇 세션 (비로그인 사용자는 UUID로 관리)';


-- ============================================================
-- 10. chat_messages
-- ============================================================
CREATE TYPE message_role  AS ENUM ('user', 'assistant');
CREATE TYPE question_type AS ENUM (
    'keyword_search',
    'specific_search',
    'goal_oriented',
    'career_certification',
    'level_based',
    'out_of_scope'
);

CREATE TABLE chat_messages (
    id              SERIAL          PRIMARY KEY,
    session_id      UUID            NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role            message_role    NOT NULL,
    content         TEXT            NOT NULL,
    question_type   question_type,              -- assistant 메시지에만 설정
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  chat_messages               IS '챗봇 대화 메시지';
COMMENT ON COLUMN chat_messages.question_type IS 'keyword_search | specific_search | goal_oriented | career_certification | level_based | out_of_scope';

CREATE INDEX idx_chat_messages_session ON chat_messages(session_id, created_at);


-- ============================================================
-- 11. chat_recommendations
-- ============================================================
CREATE TABLE chat_recommendations (
    id                  SERIAL      PRIMARY KEY,
    message_id          INT         NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    book_id             INT         NOT NULL REFERENCES books(id)         ON DELETE CASCADE,
    similarity_score    FLOAT       NOT NULL,   -- 벡터 유사도 (0~1)
    rank                SMALLINT    NOT NULL,   -- 응답 내 노출 순위 (1부터 시작)
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (message_id, book_id)
);

COMMENT ON TABLE  chat_recommendations                  IS '챗봇 응답에서 추천된 도서 (카드 UI)';
COMMENT ON COLUMN chat_recommendations.similarity_score IS '코사인 유사도 — threshold 이상인 것만 저장';
COMMENT ON COLUMN chat_recommendations.rank             IS '1부터 시작, 3 초과분은 "더보기" UI';

CREATE INDEX idx_chat_rec_message ON chat_recommendations(message_id, rank);
```

---

## 주요 설계 결정

| 항목 | 결정 | 이유 |
|---|---|---|
| 도서 정보 마스터 | `book_list` 별도 테이블 | 동일 책의 다수 도서 코드(book_code)를 하나의 정보로 통합, 중복 수집 방지 |
| UNIQUE 제약 | `(title, edition, author_id, publisher_id)` | 판차를 별도 컬럼으로 분리하여 정확한 중복 식별 |
| 판차 분리 | `edition` 컬럼 | 제목에 포함된 `(개정2판)`, `(심화편)` 등을 정규식으로 추출·분리 |
| 출판 연도 | `publication_year SMALLINT` | 제목 또는 설명에서 4자리 연도 자동 추출 (최신 도서 우선 정렬에 활용) |
| 목차 | `toc TEXT` | 알라딘 API OptResult=toc 또는 스크래핑으로 수집, 최대 3,000자 |
| 저자 | 단일 FK (`author_id`) | CSV 구조상 대표 저자 1인으로 단순화 |
| 출판사 | 별도 테이블 (`publishers`) | 중복 데이터 방지 |
| 카테고리 | LLM 자동 분류 (1~3개) | 도서 제목+설명을 NVIDIA NIM에 입력하여 12개 카테고리 중 분류 |
| 난이도 | `difficulty_level` ENUM | 도서 설명 기반 LLM 분류 — `입문/초급/중급/고급`. 분류 결과는 미리보기 후 선택적으로 DB 반영 |
| YES24 후보 | `yes24_candidates` 임시 테이블 | 검색 결과를 먼저 저장하고 관리자가 선택 후 `book_list` 덮어쓰기. 선택 또는 건너뜀 시 후보 삭제 |
| 썸네일 | `thumbnail_url` + `thumbnail` 분리 | 원본 소스 URL과 로컬 저장 파일 경로를 별도 관리. 표시 시 로컬 파일 우선 |
| 임베딩 | `book_list` 단위 (OneToOne) | 동일 도서의 중복 임베딩 방지, HNSW 인덱스로 코사인 유사도 검색 |
| 챗봇 세션 | UUID 기반 | 비로그인 사용자도 대화 맥락 유지 |
| 추천 카드 | `similarity_score` 저장 | threshold 재조정 시 DB 재활용 가능 |
| `updated_at` | 트리거로 자동 관리 | 명시적 갱신 없이도 최종 수정 시각 추적 |

---

## 데이터 수집 파이프라인 흐름

```
[CSV 초기 적재 (1회)]
        │
        ▼
load_books 커맨드
  → publishers, authors upsert
  → book_list 생성 (title, edition 분리, author_id, publisher_id)
  → books 생성 (book_code D-NNN, book_list_id FK)
        │
        ▼
run_pipeline 커맨드 / 웹 UI (BookList 단위, 중복 건너뜀)
        │
        ├─① 멀티소스 정보 수집
        │     알라딘 Open API (TTB) → description, toc, isbn, thumbnail_url
        │     YES24 스크래핑       → 누락 필드 보완
        │     교보문고 스크래핑    → 누락 필드 보완
        │
        ├─② LLM 난이도 분류 (NVIDIA NIM)
        │     입력: title + description → 출력: difficulty (입문/초급/중급/고급)
        │
        └─③ 임베딩 생성 (NVIDIA nv-embedqa-e5-v5)
              입력: title + description → VECTOR(1024) → book_embeddings upsert


[별도 파이프라인]
  - 연도 추출:     book_list.title / description → publication_year
  - 카테고리 분류: title + description → LLM → book_list_categories
```
