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
- goal_oriented: '무엇이 되고 싶다', '어떤 결과물을 내고 싶다' 등 진로·목적 기반 추천 질문
  예) "프론트엔드 개발자로 취업하고 싶어", "비전공자인데 IT 흐름 알고 싶어"
- career_certification: 자격증 취득·포트폴리오·수험 준비 등 가시적 성과를 원하는 질문
  예) "정보처리기사 실기 준비 책", "포트폴리오 프로젝트 아이디어 얻을 책"
- level_based: 수준·숙련도를 나타내는 표현이 포함된 질문. 기술명이 함께 있어도 수준 표현이 있으면 level_based로 분류
  수준 표현 예시: 초보, 초보자, 입문자, 중급자, 고급자, 전문가, 생초보, 시니어, 경력자, 입문, 초급, 중급, 고급, 심화
  예) "완전 생초보가 읽기 좋은 책", "AWS 전문가를 위한 도서", "파이썬 중급자용 책 추천해줘",
      "이미 개발자인데 아키텍처 공부하고 싶어", "리액트 고급 도서 알려줘"
- out_of_scope: IT·개발·자기개발 도서와 전혀 관련 없는 질문, 또는 요리·날씨 등 무관한 주제
  예) "오늘 날씨 어때?", "요리책 추천해줘", "안녕? 넌 누구야?"

판별 우선순위 (중의적 질문):
  조회·목록·있어? 표현 포함 → keyword_search (추천이 아닌 단순 목록 확인)
  수준 표현 포함 → level_based (기술명이 함께 있어도 level_based 우선)
  진로·목적 표현 포함 → goal_oriented
  자격증·포트폴리오 표현 포함 → career_certification
  기술명만 있고 수준 없음 → specific_search

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
