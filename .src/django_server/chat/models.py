import uuid
from django.db import models
from books.models import Book


class ChatSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_active_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "chat_sessions"
        verbose_name = "챗봇 세션"
        verbose_name_plural = "챗봇 세션"
        ordering = ["-last_active_at"]

    def __str__(self):
        return str(self.id)


class ChatMessage(models.Model):
    class Role(models.TextChoices):
        USER = "user", "사용자"
        ASSISTANT = "assistant", "AI"

    class QuestionType(models.TextChoices):
        """FastAPI 분류기(chains/classifier.py)가 실제로 반환하는 값과 일치시킴.

        주의: 질문 유형 분류 체계 자체는 별도로 전면 개편 예정이므로, 여기서는
        Admin 필터·표시가 실제 저장값을 인식하도록 값만 맞추고 구조는 바꾸지 않음.
        """
        KEYWORD_SEARCH        = "keyword_search",        "도서 목록 조회"
        SPECIFIC_SEARCH       = "specific_search",        "특정 기술·키워드 탐색"
        GOAL_ORIENTED         = "goal_oriented",           "진로·목적 큐레이션"
        CAREER_CERTIFICATION  = "career_certification",    "자격증·포트폴리오"
        LEVEL_BASED           = "level_based",             "수준별 추천"
        OUT_OF_SCOPE          = "out_of_scope",             "도서 무관"

    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name="messages", verbose_name="세션")
    role = models.CharField(max_length=20, choices=Role.choices, verbose_name="역할")
    content = models.TextField(verbose_name="내용")
    question_type = models.CharField(
        max_length=20, choices=QuestionType.choices, blank=True, verbose_name="질문 유형",
        help_text="assistant 메시지에만 설정"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "chat_messages"
        verbose_name = "챗봇 메시지"
        verbose_name_plural = "챗봇 메시지"
        ordering = ["created_at"]

    def __str__(self):
        return f"[{self.role}] {self.content[:40]}"


class ChatRecommendation(models.Model):
    message = models.ForeignKey(ChatMessage, on_delete=models.CASCADE, related_name="recommendations", verbose_name="메시지")
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="recommendations", verbose_name="도서")
    similarity_score = models.FloatField(verbose_name="유사도 점수")
    rank = models.PositiveSmallIntegerField(verbose_name="추천 순위")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "chat_recommendations"
        unique_together = ("message", "book")
        ordering = ["rank"]
        verbose_name = "추천 도서"
        verbose_name_plural = "추천 도서"

    def __str__(self):
        return f"Rank{self.rank} {self.book.title}"
