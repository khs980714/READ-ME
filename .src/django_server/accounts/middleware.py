"""
JWT 쿠키 기반 인증 미들웨어.

쿠키 2개를 사용합니다.
  readme_access  — Access token  (단명, 기본 15분)
  readme_refresh — Refresh token (장명, 기본 7일)

인증 순서:
  1. Access token 유효 → request.user 설정
     └ 남은 수명 < 1/3 이면 슬라이딩 갱신
  2. Access token 만료/없음 + Refresh token 유효
     → Access token 재발급, request.user 설정
     └ Refresh token 남은 수명 < 1일이면 슬라이딩 갱신
  3. 둘 다 없거나 만료 → AnonymousUser (재로그인 필요)

세션 인증(Django Admin)이 이미 된 경우에는 건드리지 않습니다.
"""

from __future__ import annotations

from datetime import datetime, timezone

import jwt
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser

User = get_user_model()

ACCESS_COOKIE  = "readme_access"
REFRESH_COOKIE = "readme_refresh"


# ── 토큰 생성 ────────────────────────────────────────────────

def create_access_token(user) -> str:
    now = datetime.now(tz=timezone.utc)
    payload = {
        "type":     "access",
        "user_id":  user.pk,
        "username": user.username,
        "is_staff": user.is_staff,
        "iat": int(now.timestamp()),
        "exp": int((now + settings.JWT_ACCESS_TOKEN_LIFETIME).timestamp()),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm="HS256")


def create_refresh_token(user) -> str:
    now = datetime.now(tz=timezone.utc)
    payload = {
        "type":    "refresh",
        "user_id": user.pk,
        "iat": int(now.timestamp()),
        "exp": int((now + settings.JWT_REFRESH_TOKEN_LIFETIME).timestamp()),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm="HS256")


# ── 토큰 디코드 ──────────────────────────────────────────────

def _decode(token: str, expected_type: str) -> dict | None:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=["HS256"])
        if payload.get("type") != expected_type:
            return None
        return payload
    except jwt.InvalidTokenError:
        return None


# ── 쿠키 세터 / 삭제 헬퍼 ────────────────────────────────────

def _set_access_cookie(response, token: str) -> None:
    response.set_cookie(
        ACCESS_COOKIE,
        token,
        max_age=int(settings.JWT_ACCESS_TOKEN_LIFETIME.total_seconds()),
        httponly=True,
        samesite="Lax",
        secure=not settings.DEBUG,
        path="/",
    )


def _set_refresh_cookie(response, token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=int(settings.JWT_REFRESH_TOKEN_LIFETIME.total_seconds()),
        httponly=True,
        samesite="Lax",
        secure=not settings.DEBUG,
        path="/",
    )


def clear_jwt_cookies(response) -> None:
    """로그인 시와 동일한 속성으로 쿠키를 삭제해야 브라우저가 확실히 제거합니다."""
    for name in (ACCESS_COOKIE, REFRESH_COOKIE):
        response.set_cookie(
            name,
            value="",
            max_age=0,
            expires="Thu, 01 Jan 1970 00:00:00 GMT",
            path="/",
            httponly=True,
            samesite="Lax",
            secure=not settings.DEBUG,
        )


def _is_clearing_cookies(response) -> bool:
    """응답이 JWT 쿠키를 삭제하는 중인지 확인 (로그아웃 감지용)."""
    cookie = response.cookies.get(ACCESS_COOKIE)
    return cookie is not None and not cookie.value


# ── 미들웨어 ─────────────────────────────────────────────────

class JWTAuthMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        jwt_user    = None
        refresh_ok  = False   # refresh token으로 인증됐는지 여부

        if not request.user.is_authenticated:
            access_token  = request.COOKIES.get(ACCESS_COOKIE)
            refresh_token = request.COOKIES.get(REFRESH_COOKIE)

            # 1단계: Access token 검증
            if access_token:
                payload = _decode(access_token, "access")
                if payload:
                    try:
                        user = User.objects.get(pk=payload["user_id"], is_active=True)
                        request.user = user
                        jwt_user = user
                    except User.DoesNotExist:
                        pass

            # 2단계: Access token 실패 → Refresh token으로 재발급 시도
            if jwt_user is None and refresh_token:
                payload = _decode(refresh_token, "refresh")
                if payload:
                    try:
                        user = User.objects.get(pk=payload["user_id"], is_active=True)
                        request.user = user
                        jwt_user = user
                        refresh_ok = True
                    except User.DoesNotExist:
                        pass

        response = self.get_response(request)

        # 인증된 사용자가 없거나, 로그아웃 응답이면 토큰 갱신 생략
        if jwt_user is None or _is_clearing_cookies(response):
            return response

        now = datetime.now(tz=timezone.utc)

        # Access token 슬라이딩 갱신: 남은 수명 < 1/3 이거나 refresh로 인증된 경우
        access_token = request.COOKIES.get(ACCESS_COOKIE)
        needs_new_access = refresh_ok

        if not needs_new_access and access_token:
            payload = _decode(access_token, "access")
            if payload:
                remaining = payload["exp"] - now.timestamp()
                total = settings.JWT_ACCESS_TOKEN_LIFETIME.total_seconds()
                needs_new_access = remaining < total / 3

        if needs_new_access:
            _set_access_cookie(response, create_access_token(jwt_user))

        # Refresh token 슬라이딩 갱신: 남은 수명 < 1일
        refresh_token = request.COOKIES.get(REFRESH_COOKIE)
        if refresh_token:
            payload = _decode(refresh_token, "refresh")
            if payload:
                remaining = payload["exp"] - now.timestamp()
                if remaining < 86400:  # 1일(초)
                    _set_refresh_cookie(response, create_refresh_token(jwt_user))

        return response
