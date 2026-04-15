# READ:ME — LangChain 체인 분기 구조

## 전체 흐름

```mermaid
flowchart TD
    A(["사용자 입력"]) --> B["Django /chat/api/"]
    B --> C{"전송 모드"}
    C -->|일반| D["FastAPI POST /chat/message"]
    C -->|스트리밍| E["FastAPI POST /chat/message/stream"]

    D & E --> F["Classifier (정규식 + LLM)"]
    F --> G{"question_type"}

    G -->|keyword_search| KW["ILIKE DB 검색 (LLM 없음)"]
    G -->|out_of_scope| H["거절 안내 반환"]
    G -->|level_based| I["난이도 추출 → 필터 벡터 검색"]
    G -->|specific_search| K["임베딩 생성 → 벡터 검색"]
    G -->|goal_oriented| K
    G -->|career_certification| K

    KW --> KW_fmt["keyword_search_chain"]
    I --> Q["level_based_chain"]
    K -->|specific_search| N["specific_search_chain"]
    K -->|goal_oriented| O["goal_oriented_chain"]
    K -->|career_certification| P["career_certification_chain"]

    KW_fmt & N & O & P & Q --> R{"응답 방식"}
    R -->|일반| S["LLM 전체 응답"]
    R -->|스트리밍| T["토큰 단위 SSE 스트리밍"]

    S --> W["Django DB 저장"]
    T --> X["Django enrichment + DB 저장"]
    H --> Y(["브라우저 JSON 응답"])
    W --> Y
    X --> Z(["브라우저 SSE 스트림"])
```

## 질문 유형 요약

| 유형 | 분류 방식 | 검색 방식 | LLM 호출 |
|---|---|---|---|
| `keyword_search` | 정규식 즉시 감지 | ILIKE DB 검색 | 없음 |
| `specific_search` | LLM | 벡터 유사도 | 있음 |
| `goal_oriented` | LLM | 벡터 유사도 | 있음 |
| `career_certification` | LLM | 벡터 유사도 + 후처리 필터 | 있음 |
| `level_based` | 정규식 / LLM | 난이도 필터 벡터 검색 | 있음 |
| `out_of_scope` | LLM | 없음 | 없음 |

## 체인 입력/출력 명세

```mermaid
classDiagram
    class ChatRequest {
        +str message
        +list history
        +str session_id
    }

    class ClassifierChain {
        +classify_question(question) str
    }

    class KeywordSearchChain {
        +keyword_search_chain(inputs) str
        검색: ILIKE DB, LLM 없음
    }

    class DifficultyExtractor {
        +extract_difficulty_from_question(question) str
        방식: 키워드 매핑 (룰 기반)
    }

    class VectorSearch {
        +vector_search(embedding, threshold, limit) list
    }

    class VectorSearchByDifficulty {
        +vector_search_by_difficulty(embedding, threshold, limit, difficulty) list
    }

    class SpecificSearchChain {
        +specific_search_chain(inputs) str
        +specific_search_chain_stream(inputs) AsyncGenerator
    }

    class GoalOrientedChain {
        +goal_oriented_chain(inputs) str
        +goal_oriented_chain_stream(inputs) AsyncGenerator
    }

    class CareerCertificationChain {
        +career_certification_chain(inputs) str
        +career_certification_chain_stream(inputs) AsyncGenerator
    }

    class LevelBasedChain {
        +level_based_chain(inputs) str
        +level_based_chain_stream(inputs) AsyncGenerator
    }

    ChatRequest --> ClassifierChain
    ClassifierChain --> KeywordSearchChain : keyword_search
    ClassifierChain --> DifficultyExtractor : level_based
    DifficultyExtractor --> VectorSearchByDifficulty
    ClassifierChain --> VectorSearch : specific / goal / career
    VectorSearch --> SpecificSearchChain
    VectorSearch --> GoalOrientedChain
    VectorSearch --> CareerCertificationChain
    VectorSearchByDifficulty --> LevelBasedChain
```

## 질문 유형별 플로우

```mermaid
flowchart LR
    subgraph kw["keyword_search — 도서 목록 직접 조회"]
        KW1(["목록 조회 질문"]) --> KW2["ILIKE DB 검색"] --> KW3["keyword_search_chain"]
    end

    subgraph ss["specific_search — 기술·키워드 탐색"]
        SS1(["키워드 언급 질문"]) --> SS2["벡터 검색"] --> SS3["specific_search_chain"]
    end

    subgraph go["goal_oriented — 진로·목적 큐레이션"]
        GO1(["취업·입문 목표 질문"]) --> GO2["벡터 검색"] --> GO3["goal_oriented_chain"]
    end

    subgraph cc["career_certification — 자격증·포트폴리오"]
        CC1(["자격증·코딩테스트 질문"]) --> CC2["벡터 검색"] --> CC3["후처리 필터"] --> CC4["career_certification_chain"]
    end

    subgraph lb["level_based — 수준별 추천"]
        LB1(["수준 언급 질문"]) --> LB2["난이도 키워드 추출"] --> LB3["난이도 필터 벡터 검색"] --> LB4["level_based_chain"]
    end

    subgraph oos["out_of_scope — 범위 외"]
        OS1(["무관한 질문"]) --> OS2["거절 안내 메시지"]
    end
```

## SSE 이벤트 포맷 (스트리밍 모드)

```mermaid
sequenceDiagram
    participant B as 브라우저
    participant D as Django
    participant F as FastAPI
    participant L as LLM

    B->>D: POST /chat/api/message/stream/
    D->>F: POST /chat/message/stream
    F->>L: classify_question()
    L-->>F: question_type

    alt keyword_search
        F->>F: retrieve_books_by_keyword (ILIKE)
        F-->>D: {"type":"answer_chunk","content":"목록"}
        F-->>D: {"type":"done","question_type":"keyword_search","recommendations":[...]}
    else out_of_scope
        F-->>D: {"type":"answer_chunk","content":"안내 메시지"}
        F-->>D: {"type":"done","question_type":"out_of_scope","recommendations":[]}
    else level_based
        F->>F: extract_difficulty + vector_search_by_difficulty
        F->>L: astream(level_based_chain)
        loop 토큰 스트리밍
            L-->>F: chunk
            F-->>D: {"type":"answer_chunk"}
            D-->>B: {"type":"answer_chunk"}
        end
        F-->>D: {"type":"done","question_type":"level_based","recommendations":[...]}
    else specific / goal / career
        F->>F: vector_search
        F->>L: astream
        loop 토큰 스트리밍
            L-->>F: chunk
            F-->>D: {"type":"answer_chunk"}
            D-->>B: {"type":"answer_chunk"}
        end
        F-->>D: {"type":"done","question_type":"...","recommendations":[...]}
    end

    D->>D: _enrich_recommendations + _save_stream_result
    D-->>B: {"type":"done","recommendations":[enriched]}
```
