from django.urls import path
from . import views

app_name = "chat"

urlpatterns = [
    path("", views.chat_page, name="page"),
    path("api/message/", views.send_message, name="send_message"),
    path("api/message/stream/", views.stream_message, name="stream_message"),
]
