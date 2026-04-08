from django.urls import path
from . import views

app_name = "books"

urlpatterns = [
    path("", views.book_list, name="list"),
    path("books/<int:pk>/", views.book_detail, name="detail"),
    # 도서 관리 (staff 전용)
    path("manage/", views.book_manage, name="manage"),
    path("manage/add/", views.book_add, name="add"),
    path("api/naver-search/", views.naver_search_api, name="naver_search"),
    path("manage/<int:pk>/edit/", views.book_edit, name="edit"),
    path("manage/<int:pk>/delete/", views.book_delete, name="delete"),
]
