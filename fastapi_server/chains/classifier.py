"""
질문 유형 분류기
roadmap | level_based | general | unrelated
"""

from langchain_core.messages import HumanMessage
from llm import get_llm

CLASSIFY_PROMPT = """당신은 사용자의 질문을 4가지 유형으로 분류하는 분류기입니다.

유형 정의:
- roadmap: 학습 순서, 공부 로드맵, 단계별 학습 경로에 관한 질문
- level_based: 자신의 수준(입문/초급/중급/고급)을 언급하며 도서 추천을 요청하는 질문
- general: 특정 도서 정보, 도서 비교, 도서 관련 기타 질문
- unrelated: IT·개발 도서와 전혀 관련 없는 질문

반드시 다음 4가지 중 하나만 출력하세요: roadmap, level_based, general, unrelated

사용자 질문: {question}

유형:"""


async def classify_question(question: str) -> str:
    llm = get_llm()
    response = await llm.ainvoke([HumanMessage(content=CLASSIFY_PROMPT.format(question=question))])
    result = response.content.strip().lower()

    valid = ("roadmap", "level_based", "general", "unrelated")
    for v in valid:
        if v in result:
            return v
    return "general"
