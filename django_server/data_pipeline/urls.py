from django.urls import path
from . import views

app_name = "pipeline"

urlpatterns = [
    path("", views.pipeline_page, name="index"),
    path("run/", views.run_pipeline, name="run"),
    path("stream/<str:job_id>/", views.pipeline_stream, name="stream"),
    path("embed/run/", views.run_embed_missing, name="embed_run"),
    path("embed/stream/<str:job_id>/", views.embed_stream, name="embed_stream"),
    path("classify/run/", views.run_classify, name="classify_run"),
    path("classify/stream/<str:job_id>/", views.classify_stream, name="classify_stream"),
]
