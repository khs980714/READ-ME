"""
READ:ME FastAPI 서버 부하 테스트 (Locust)

실행 방법:
    # 웹 UI 모드 (http://localhost:8089)
    locust -f fastapi_server/tests/locustfile.py --host http://localhost:8001

    # 헤드리스 모드 (CI/자동화용)
    locust -f fastapi_server/tests/locustfile.py \
        --host http://localhost:8001 \
        --headless -u 20 -r 5 --run-time 60s \
        --html fastapi_server/tests/load_report.html

설치:
    pip install locust

주요 시나리오:
    - /health             : 서버 상태 확인 (가중치 낮음)
    - /chat/message       : 동기 챗 응답 (5가지 질문 유형 고루 사용)
    - /chat/message/stream: SSE 스트리밍 챗 (실제 UX와 동일)
"""

import json
import random

from locust import HttpUser, between, task

# ── 테스트용 질문 샘플 (5가지 유형 커버) ─────────────────────────
_QUESTIONS = {
    "specific_search": [
        "파이썬 책 추천해줘",
        "장고(Django)로 웹 개발하는 책 알려줘",
        "리액트 관련 도서 있어?",
        "쿠버네티스 도서 추천",
        "AWS 관련 책 찾고 있어",
    ],
    "goal_oriented": [
        "프론트엔드 개발자로 취업하고 싶어",
        "비전공자인데 IT 흐름 알고 싶어",
        "백엔드 개발자가 되려면 어떤 책 읽어야 해?",
        "데이터 분석가로 전직하고 싶어",
    ],
    "career_certification": [
        "정보처리기사 실기 준비 책 추천해줘",
        "포트폴리오 프로젝트 아이디어 얻을 수 있는 책",
        "컴퓨터활용능력 1급 준비 도서",
    ],
    "level_based": [
        "완전 생초보가 읽기 좋은 책",
        "파이썬 중급자용 책 추천해줘",
        "고급 개발자를 위한 아키텍처 책",
        "AWS 전문가를 위한 도서",
        "이미 개발자인데 심화 공부하고 싶어",
    ],
    "out_of_scope": [
        "오늘 날씨 어때?",
        "요리책 추천해줘",
        "안녕? 넌 누구야?",
    ],
}

_ALL_QUESTIONS = [q for qs in _QUESTIONS.values() for q in qs]

# out_of_scope는 DB 검색 없이 빠르게 처리되므로 가중치 낮춤
_WEIGHTED_QUESTIONS = (
    _QUESTIONS["specific_search"] * 3
    + _QUESTIONS["goal_oriented"] * 3
    + _QUESTIONS["career_certification"] * 2
    + _QUESTIONS["level_based"] * 3
    + _QUESTIONS["out_of_scope"] * 1
)


class ChatUser(HttpUser):
    """일반 사용자 시뮬레이션: 동기 챗 + 스트리밍 + 헬스체크 혼합."""

    wait_time = between(1, 3)  # 요청 간 1~3초 대기 (실제 사용자 타이핑 시간 모사)

    @task(1)
    def health_check(self):
        self.client.get("/health", name="/health")

    @task(6)
    def chat_message(self):
        """동기 챗 요청 — /chat/message"""
        question = random.choice(_WEIGHTED_QUESTIONS)
        self.client.post(
            "/chat/message",
            json={"message": question, "history": []},
            name="/chat/message",
        )

    @task(3)
    def chat_message_stream(self):
        """SSE 스트리밍 요청 — /chat/message/stream
        스트리밍 청크를 소비해 실제 클라이언트 동작을 모사합니다.
        """
        question = random.choice(_WEIGHTED_QUESTIONS)
        with self.client.post(
            "/chat/message/stream",
            json={"message": question, "history": []},
            stream=True,
            name="/chat/message/stream",
            catch_response=True,
        ) as resp:
            received = 0
            for line in resp.iter_lines():
                if line:
                    received += 1
            if received == 0:
                resp.failure("스트리밍 응답이 비어 있음")
            else:
                resp.success()

    @task(2)
    def chat_with_history(self):
        """이전 대화 히스토리가 있는 챗 요청 (슬라이딩 윈도우 테스트)."""
        history = [
            {"role": "user", "content": "파이썬 책 추천해줘"},
            {"role": "assistant", "content": "파이썬 기초 도서로 '파이썬 완전정복'을 추천드립니다."},
        ]
        question = random.choice(_QUESTIONS["specific_search"] + _QUESTIONS["level_based"])
        self.client.post(
            "/chat/message",
            json={"message": question, "history": history},
            name="/chat/message (with history)",
        )


class HeavyUser(HttpUser):
    """동시 다발 스트리밍 집중 테스트 (SSE 연결 유지 시간 측정)."""

    wait_time = between(0.5, 1.5)

    @task
    def stream_only(self):
        question = random.choice(_ALL_QUESTIONS)
        with self.client.post(
            "/chat/message/stream",
            json={"message": question, "history": []},
            stream=True,
            name="/chat/message/stream [heavy]",
            catch_response=True,
        ) as resp:
            chunks = []
            for line in resp.iter_lines():
                if line and line.startswith("data:"):
                    raw = line[5:].strip()
                    try:
                        payload = json.loads(raw)
                        if payload.get("type") == "done":
                            break
                        chunks.append(payload.get("content", ""))
                    except json.JSONDecodeError:
                        pass
            if not chunks:
                resp.failure("응답 청크 없음")
            else:
                resp.success()
