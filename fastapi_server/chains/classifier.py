"""
질문 유형 분류기
specific_search | goal_oriented | career_certification | level_based | out_of_scope
"""

from langchain_core.messages import HumanMessage
from llm import get_llm

CLASSIFY_PROMPT = """당신은 사용자의 질문을 5가지 유형으로 분류하는 분류기입니다.

유형 정의:
- specific_search: 특정 기술·도구·언어를 언급하지만 수준 표현이 없는 질문
  예) "파이썬 기초 책 추천해줘", "장고(Django)로 웹 만드는 책 알려줘", "리액트 관련 책 있어?"
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
  수준 표현 포함 → level_based (기술명이 함께 있어도 level_based 우선)
  진로·목적 표현 포함 → goal_oriented
  자격증·포트폴리오 표현 포함 → career_certification
  기술명만 있고 수준 없음 → specific_search

반드시 다음 5가지 중 하나만 출력하세요: specific_search, goal_oriented, career_certification, level_based, out_of_scope

사용자 질문: {question}

유형:"""


async def classify_question(question: str) -> str:
    llm = get_llm()
    response = await llm.ainvoke([HumanMessage(content=CLASSIFY_PROMPT.format(question=question))])
    result = response.content.strip().lower()

    valid = ("specific_search", "goal_oriented", "career_certification", "level_based", "out_of_scope")
    for v in valid:
        if v in result:
            return v
    return "specific_search"
