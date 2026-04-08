from django.contrib import admin
from .models import ChatSession, ChatMessage, ChatRecommendation


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    readonly_fields = ("role", "content", "question_type", "created_at")
    can_delete = False


@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "last_active_at")
    readonly_fields = ("id", "created_at", "last_active_at")
    inlines = [ChatMessageInline]


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ("session", "role", "question_type", "content_preview", "created_at")
    list_filter = ("role", "question_type")
    readonly_fields = ("created_at",)

    def content_preview(self, obj):
        return obj.content[:60]
    content_preview.short_description = "내용"
