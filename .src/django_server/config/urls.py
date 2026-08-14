from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import RedirectView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", RedirectView.as_view(pattern_name="accounts:login", permanent=False)),
    path("accounts/", include("accounts.urls")),
    path("", include("books.urls")),
    path("pipeline/", include("data_pipeline.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

