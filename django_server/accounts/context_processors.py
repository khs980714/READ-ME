from django.conf import settings


def jwt_settings(request):
    """유휴 타임아웃 값을 모든 템플릿에 노출."""
    return {
        "idle_timeout_ms": settings.JWT_IDLE_TIMEOUT_MINUTES * 60 * 1000,
    }
