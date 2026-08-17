from django.urls import path
from . import views

app_name = "pipeline"

urlpatterns = [
    path("", views.pipeline_page, name="index"),
    path("books-list/", views.books_list_api, name="books_list"),
    path("run/", views.run_pipeline, name="run"),
    path("collect/run/", views.run_collect_only, name="collect_run"),
    path("collect/stream/<str:job_id>/", views.collect_stream, name="collect_stream"),
    path("stream/<str:job_id>/", views.pipeline_stream, name="stream"),
    path("stop/<str:job_id>/", views.stop_job, name="stop"),
    path("embed/run/", views.run_embed_missing, name="embed_run"),
    path("embed/stream/<str:job_id>/", views.embed_stream, name="embed_stream"),
    path("classify/run/", views.run_classify, name="classify_run"),
    path("classify/stream/<str:job_id>/", views.classify_stream, name="classify_stream"),
    path("classify/apply/", views.classify_apply, name="classify_apply"),
    path("year/run/", views.run_extract_year, name="year_run"),
    path("year/stream/<str:job_id>/", views.year_stream, name="year_stream"),
    path("category/run/", views.run_classify_category, name="category_run"),
    path("category/stream/<str:job_id>/", views.category_stream, name="category_stream"),
    path("category/apply/", views.category_apply, name="category_apply"),
    path("candidates/search/", views.search_book_candidates, name="candidates_search"),
    path("candidates/apply/", views.apply_book_candidate, name="candidates_apply"),
    path("thumbnails/scan/", views.scan_thumbnails, name="thumbnails_scan"),
    path("thumbnails/clean/", views.clean_thumbnails, name="thumbnails_clean"),
    path("kyobo/run/", views.run_kyobo_pipeline, name="kyobo_run"),
    path("kyobo/stream/<str:job_id>/", views.kyobo_pipeline_stream, name="kyobo_stream"),
    path("kyobo/candidates/", views.get_kyobo_candidates, name="kyobo_candidates"),
    path("kyobo/apply/", views.apply_kyobo_candidate, name="kyobo_apply"),
    path("kyobo/dismiss/", views.dismiss_kyobo_candidates, name="kyobo_dismiss"),
    path("kyobo/reset/", views.reset_kyobo_candidates, name="kyobo_reset"),
]
