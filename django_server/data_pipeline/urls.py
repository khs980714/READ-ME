from django.urls import path
from . import views

app_name = "pipeline"

urlpatterns = [
    path("", views.pipeline_page, name="index"),
    path("run/", views.run_pipeline, name="run"),
    path("stream/<str:job_id>/", views.pipeline_stream, name="stream"),
    path("embed-missing/", views.run_embed_missing, name="embed_missing"),
]
