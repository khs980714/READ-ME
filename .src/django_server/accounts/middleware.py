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

# Access token 슬라이딩 갱신 임계값: 남은 수명이 전체 수명의 이 비율 미만이면 재발급
_ACCESS_REFRESH_THRESHOLD_RATIO = 1 / 3
# Refresh token 슬라이딩 갱신 임계값(초): 남은 수명이 이 값 미만이면 재발급 (1일)
_REFRESH_REFRESH_THRESHOLD_SECONDS = 86400


# ── 토큰 생성 ────────────────────────────────────────────────

def _create_token(user, token_type: str, lifetime) -> str:
    now = datetime.now(tz=timezone.utc)
    payload = {
        "type":    token_type,
        "user_id": user.pk,
        "iat": int(now.timestamp()),
        "exp": int((now + lifetime).timestamp()),
    }
    if token_type == "access":
        payload["username"] = user.username
        payload["is_staff"] = user.is_staff
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm="HS256")


def create_access_token(user) -> str:
    return _create_token(user, "access", settings.JWT_ACCESS_TOKEN_LIFETIME)


def create_refresh_token(user) -> str:
    return _create_token(user, "refresh", settings.JWT_REFRESH_TOKEN_LIFETIME)


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
# set_access_cookie / set_refresh_cookie는 로그인 뷰(accounts/views.py)와
# 이 미들웨어의 슬라이딩 갱신 양쪽에서 쓰이는 공개 API입니다.

def _set_cookie(response, name: str, token: str, lifetime) -> None:
    response.set_cookie(
        name,
        token,
        max_age=int(lifetime.total_seconds()),
        httponly=True,
        samesite="Lax",
        secure=not settings.DEBUG,
        path="/",
    )


def set_access_cookie(response, token: str) -> None:
    _set_cookie(response, ACCESS_COOKIE, token, settings.JWT_ACCESS_TOKEN_LIFETIME)


def set_refresh_cookie(response, token: str) -> None:
    _set_cookie(response, REFRESH_COOKIE, token, settings.JWT_REFRESH_TOKEN_LIFETIME)


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
        jwt_user        = None
        refresh_ok      = False   # refresh token으로 인증됐는지 여부
        access_payload  = None    # 1단계에서 디코딩한 access token payload (재사용용)
        refresh_payload = None    # 2단계에서 디코딩한 refresh token payload (재사용용)

        if not request.user.is_authenticated:
            access_token  = request.COOKIES.get(ACCESS_COOKIE)
            refresh_token = request.COOKIES.get(REFRESH_COOKIE)

            # 1단계: Access token 검증
            if access_token:
                payload = _decode(access_token, "access")
                if payload:
                    access_payload = payload
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
                    refresh_payload = payload
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

        # Access token 슬라이딩 갱신: 남은 수명이 임계 비율 미만이거나 refresh로 인증된 경우
        # (1단계에서 디코딩한 payload를 재사용 — 같은 토큰을 두 번 디코딩하지 않음)
        needs_new_access = refresh_ok

        if not needs_new_access and access_payload:
            remaining = access_payload["exp"] - now.timestamp()
            total = settings.JWT_ACCESS_TOKEN_LIFETIME.total_seconds()
            needs_new_access = remaining < total * _ACCESS_REFRESH_THRESHOLD_RATIO

        if needs_new_access:
            set_access_cookie(response, create_access_token(jwt_user))

        # Refresh token 슬라이딩 갱신: 남은 수명이 임계값 미만이면 재발급
        # (2단계에서 이미 디코딩했다면 그 payload를 재사용)
        refresh_token = request.COOKIES.get(REFRESH_COOKIE)
        if refresh_token:
            payload = refresh_payload if refresh_payload is not None else _decode(refresh_token, "refresh")
            if payload:
                remaining = payload["exp"] - now.timestamp()
                if remaining < _REFRESH_REFRESH_THRESHOLD_SECONDS:
                    set_refresh_cookie(response, create_refresh_token(jwt_user))

        return response
