from django.contrib import admin
from django.contrib.auth import logout as auth_logout
from django.http import HttpResponseRedirect
from django.urls import path, include, reverse
from django.conf import settings
from django.conf.urls.static import static
from django.views.decorators.http import require_POST


@require_POST
def custom_logout(request):
    """POST 로그아웃 후 메인 페이지로 리다이렉트."""
    auth_logout(request)
    return HttpResponseRedirect(reverse("books:list"))


urlpatterns = [
    path("admin/", admin.site.urls),
    path("logout/", custom_logout, name="logout"),
    path("", include("books.urls")),
    path("chat/", include("chat.urls")),
    path("pipeline/", include("data_pipeline.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
