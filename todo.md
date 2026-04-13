1. 도서 정보를 수집하는 과정을 볼 떄 목차에 대한 정보가 있는지 확인해주고 있다면 db 도서 관련 테이블에 목차를 넣어줘. 추가로 도서 정보를 보는 페이지에서도 목차가 있으면 목차도 볼 수 있도록 만들어줘.
2. AI 추천 검색을 할 때 도서 번호가 [nnn] 이런식으로 출력이 되는데 도서 관리에서 코드칸에 출력되는것과 같이 D-nnn으로 출력되게 만들어줘.
3. 이거는 실험하는건데 도서를 카테고리로 자동으로 나눌 수 있을까? 있다면 무슨 카테고리가 있을 지 이 아래에 작성해줘.

[카테고리 자동 분류 실험 검토]

가능합니다. 현재 서비스에 맞는 카테고리 분류 방안:

방법: LLM(NVIDIA NIM)에 도서 제목+설명을 입력해 카테고리를 자동 분류 (난이도 분류와 동일한 방식).
이미 서비스에 `categories` / `book_list_categories` 테이블과 모델이 구축되어 있으므로 분류 로직만 추가하면 됩니다.

추천 카테고리 목록 (IT·개발 도서 기준):

  프로그래밍 언어
    - Python / JavaScript / Java / C/C++ / Go / Rust / Kotlin / Swift 등 언어별

  웹 개발
    - 프론트엔드 (HTML/CSS, React, Vue, Angular 등)
    - 백엔드 (Django, Spring, Node.js, FastAPI 등)
    - 풀스택

  모바일 개발
    - Android / iOS / 크로스플랫폼(Flutter, React Native)

  데이터베이스
    - SQL / NoSQL / DB 설계·최적화

  자료구조·알고리즘
    - 코딩 테스트 / 알고리즘 이론

  컴퓨터 과학 기초
    - 운영체제 / 네트워크 / 컴퓨터 구조 / 컴파일러

  인공지능·데이터
    - 머신러닝 / 딥러닝 / 데이터 분석 / LLM·생성 AI

  DevOps·클라우드
    - Docker / Kubernetes / CI/CD / AWS·GCP·Azure

  소프트웨어 공학
    - 클린 코드 / 설계 패턴 / 테스트 / 아키텍처

  보안
    - 정보보안 / 웹 보안 / 자격증(정보처리기사, CISSP 등)

  자격증·취업 준비
    - 수험서 / 면접 준비 / 코딩 테스트

  자기개발·IT 교양
    - 개발 문화 / 스타트업 / 비개발자 대상 IT 입문

구현 포인트:
  - classify_difficulty()와 유사하게 FastAPI /embed/classify-category 엔드포인트 추가
  - 프롬프트: "다음 도서를 아래 카테고리 중 1~3개로 분류해주세요. 제목: {title}, 설명: {description}"
  - 분류 결과를 book_list_categories 테이블에 upsert
  - data_pipeline 커맨드(classify_category)로 기존 도서 일괄 처리 가능