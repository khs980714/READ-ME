from django.contrib.auth import authenticate
from django.contrib.auth import logout as auth_logout
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .middleware import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    create_access_token,
    create_refresh_token,
    _set_access_cookie,
    _set_refresh_cookie,
)


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
            _set_access_cookie(response,  create_access_token(user))
            _set_refresh_cookie(response, create_refresh_token(user))
            return response
        else:
            error = "아이디 또는 비밀번호가 올바르지 않습니다."

    return render(request, "accounts/login.html", {"error": error, "next": next_url})


@require_POST
def logout_view(request):
    auth_logout(request)
    response = HttpResponseRedirect(reverse("books:list"))
    response.delete_cookie(ACCESS_COOKIE,  path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/")
    return response
