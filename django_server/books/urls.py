from django.urls import path
from . import views

app_name = "books"

urlpatterns = [
    path("", views.book_list, name="list"),
    path("books/<int:pk>/", views.book_detail, name="detail"),
    # 도서 관리 (staff 전용)
    path("manage/", views.book_manage, name="manage"),
    path("manage/add/", views.book_add, name="add"),
    path("manage/<int:pk>/edit/", views.book_edit, name="edit"),
    path("manage/<int:pk>/delete/", views.book_delete, name="delete"),
    path("manage/<int:pk>/collect/", views.book_collect, name="collect"),
    path("manage/<int:pk>/apply-thumbnail/", views.book_apply_thumbnail, name="apply_thumbnail"),
    path("manage/scrape-url/", views.book_scrape_url, name="scrape_url"),
    path("manage/sync-thumbnails/", views.book_sync_thumbnails, name="sync_thumbnails"),
]
