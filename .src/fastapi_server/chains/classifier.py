"""
질문 유형 분류기
keyword_search | specific_search | goal_oriented | career_certification | level_based | out_of_scope
"""

import re

from langchain_core.messages import HumanMessage
from llm import get_llm

# 단순 조회 의도를 나타내는 패턴 — LLM 호출 없이 사전 감지
_KEYWORD_SEARCH_RE = re.compile(
    r'(?:도서|책)\s*(?:조회|목록)'           # "도서 조회", "책 목록"
    r'|조회\s*(?:해줘?|해줄래\??|해\s*주세요?)'  # "조회해줘", "조회해줄래"
    r'|(?:어떤|어떤\s*종류의?)?\s*(?:도서|책)\s*(?:이\s*)?있(?:어\??|나요\??|나\??|습니까\??|는지)'  # "어떤 책 있어?"
    r'|(?:도서|책)\s*(?:목록\s*)?(?:보여줘?|보여줄래\??)',  # "도서 보여줘", "책 목록 보여줘"
    re.IGNORECASE,
)

CLASSIFY_PROMPT = """당신은 사용자의 질문을 6가지 유형으로 분류하는 분류기입니다.

유형 정의:
- keyword_search: 특정 키워드의 도서를 단순 조회·목록 확인하는 질문. '조회', '있어?', '목록', '보여줘' 등의 표현이 포함됨
  예) "AWS 도서 조회해줘", "파이썬 책 있어?", "도커 관련 책 목록 보여줘", "쿠버네티스 책 있나요?"

- specific_search: 특정 기술·도구·언어를 언급하며 추천·안내를 요청하는 질문 (수준 표현 없음)
  예) "파이썬 기초 책 추천해줘", "장고(Django)로 웹 만드는 책 알려줘", "리액트 공부하려면 어떤 책이 좋아?"

- goal_oriented: '무엇이 되고 싶다', '어떤 직업을 갖고 싶다' 등 진로·직업 목표 기반 추천 질문
  핵심: 특정 직무/커리어 목표가 있고, 그것을 위한 전반적인 학습 로드맵을 원하는 경우
  예) "프론트엔드 개발자로 취업하고 싶어", "비전공자인데 IT 흐름 알고 싶어", "데이터 엔지니어가 되고 싶어"
  주의: "코딩 테스트 준비", "자격증 준비", "취업 면접 대비 책" 처럼 구체적 시험·자격·도서를 원하면 career_certification

- career_certification: 자격증 취득·코딩 테스트·취업 면접·포트폴리오 등 구체적 성과를 위한 수험서·준비서 요청
  핵심: 특정 시험 합격, 자격증 취득, 코딩 테스트 통과, 취업 면접 통과 등 가시적 목표를 위한 도서를 원하는 경우
  예) "정보처리기사 실기 준비 책", "SQLD 자격증 수험서", "코딩 테스트 통과를 위한 알고리즘 책",
      "취업 코딩 테스트 준비 책", "네이버 카카오 코딩 테스트 대비 책", "IT 취업 면접 대비 책",
      "포트폴리오 프로젝트 아이디어 얻을 책", "취업 준비하면서 자격증 따고 싶어 어떤 수험서가 좋아?"
  구분: 'XX 개발자가 되고 싶어(→ goal_oriented)' vs '코딩 테스트/자격증/면접 준비 책(→ career_certification)'

- level_based: 현재 수준·숙련도를 명시하거나 수준 표현이 포함된 질문. 기술명이 함께 있어도 수준 표현이 있으면 level_based
  명시적 수준어: 초보, 초보자, 입문자, 중급자, 고급자, 전문가, 생초보, 경력자, 입문, 초급, 중급, 고급, 심화
  묵시적 수준 표현: '이미 ~인데', '~부터 배우는', '~을 좀 아는데 더', '~를 모르는', '처음 배우는'
  예) "완전 생초보가 읽기 좋은 책", "AWS 전문가를 위한 도서", "파이썬 중급자용 책 추천해줘",
      "이미 개발자인데 아키텍처 깊게 공부하고 싶어", "네트워크 기초부터 배우는 책",
      "이미 SQL 쓸 줄 알지만 더 잘 쓰고 싶어", "리액트 고급 도서 알려줘"
  주의: 'XX가 되고 싶어' + 수준어 → 수준보다 진로 목적이 주목적이면 goal_oriented

- out_of_scope: IT·개발·자기개발 도서와 전혀 관련 없는 질문, 또는 요리·날씨 등 무관한 주제
  예) "오늘 날씨 어때?", "요리책 추천해줘", "안녕? 넌 누구야?"

판별 우선순위 (중의적 질문):
  1. 조회·목록·있어? 표현 포함 → keyword_search
  2. 수준·숙련도 표현(명시적·묵시적 모두) 포함 → level_based
  3. 코딩 테스트·자격증·취업 면접·포트폴리오 준비 표현 → career_certification
  4. 진로·직업 목표 표현(~가 되고 싶어, ~로 취업하고 싶어) → goal_oriented
  5. 기술명만 있고 수준 없음 → specific_search

반드시 다음 6가지 중 하나만 출력하세요: keyword_search, specific_search, goal_oriented, career_certification, level_based, out_of_scope

사용자 질문: {question}

유형:"""


async def classify_question(question: str) -> str:
    # 조회 의도 패턴이 명확하면 LLM 없이 즉시 분류
    if _KEYWORD_SEARCH_RE.search(question):
        return "keyword_search"

    llm = get_llm()
    response = await llm.ainvoke([HumanMessage(content=CLASSIFY_PROMPT.format(question=question))])
    result = response.content.strip().lower()

    valid = ("keyword_search", "specific_search", "goal_oriented", "career_certification", "level_based", "out_of_scope")
    for v in valid:
        if v in result:
            return v
    return "specific_search"
