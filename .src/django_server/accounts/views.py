import logging

from django.contrib.auth import authenticate
from django.contrib.auth import logout as auth_logout
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .middleware import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    clear_jwt_cookies,
    create_access_token,
    create_refresh_token,
    set_access_cookie,
    set_refresh_cookie,
)

logger = logging.getLogger(__name__)


def login_page(request):
    if request.user.is_authenticated:
        next_url = request.GET.get("next") or reverse("books:list")
        return HttpResponseRedirect(next_url)

    error = None
    next_url = request.GET.get("next", reverse("books:list"))

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        next_url = request.POST.get("next", reverse("books:list"))
        user = authenticate(request, username=username, password=password)
        if user and user.is_active:
            response = HttpResponseRedirect(next_url)
            set_access_cookie(response,  create_access_token(user))
            set_refresh_cookie(response, create_refresh_token(user))
            return response
        else:
            error = "아이디 또는 비밀번호가 올바르지 않습니다."

    return render(request, "accounts/login.html", {"error": error, "next": next_url})


@require_POST
def logout_view(request):
    # Django 세션 제거 (Admin 세션 병행 사용 시에도 안전하게 처리)
    try:
        auth_logout(request)
    except Exception as exc:
        logger.warning("auth_logout 처리 중 예외 (JWT 쿠키 삭제는 계속 진행): %s", exc)

    response = HttpResponseRedirect(reverse("books:list"))
    # 로그인 시와 동일한 속성으로 삭제해야 브라우저가 확실히 제거
    clear_jwt_cookies(response)
    return response
