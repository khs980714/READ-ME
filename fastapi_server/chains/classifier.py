"""
질문 유형 분류기
specific_search | goal_oriented | career_certification | level_based | out_of_scope
"""

from langchain_core.messages import HumanMessage
from llm import get_llm

CLASSIFY_PROMPT = """당신은 사용자의 질문을 5가지 유형으로 분류하는 분류기입니다.

유형 정의:
- specific_search: 특정 기술·도구·언어 이름을 직접 언급하며 관련 도서를 찾는 질문
  예) "파이썬 기초 책 추천해줘", "장고(Django)로 웹 만드는 책 알려줘"
- goal_oriented: '무엇이 되고 싶다', '어떤 결과물을 내고 싶다' 등 진로·목적 기반 추천 질문
  예) "프론트엔드 개발자로 취업하고 싶어", "비전공자인데 IT 흐름 알고 싶어"
- career_certification: 자격증 취득·포트폴리오·수험 준비 등 가시적 성과를 원하는 질문
  예) "정보처리기사 실기 준비 책", "포트폴리오 프로젝트 아이디어 얻을 책"
- level_based: 자신의 현재 수준(입문/초급/중급/고급/생초보/경험자 등)을 언급하며 도서 추천을 요청하는 질문
  예) "완전 생초보가 읽기 좋은 책", "이미 개발자인데 아키텍처 공부하고 싶어"
- out_of_scope: IT·개발·자기개발 도서와 전혀 관련 없는 질문, 또는 요리·날씨 등 무관한 주제
  예) "오늘 날씨 어때?", "요리책 추천해줘", "안녕? 넌 누구야?"

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
