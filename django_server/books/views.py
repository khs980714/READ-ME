from django.shortcuts import get_object_or_404, render
from django.db.models import Q

from .models import Book, BookList, Category


def book_list(request):
    qs = (
        Book.objects.filter(is_active=True)
        .select_related("book_list__publisher", "book_list__author")
        .prefetch_related("book_list__categories")
    )

    query = request.GET.get("q", "").strip()
    difficulty = request.GET.get("difficulty", "")
    category_id = request.GET.get("category", "")

    if query:
        qs = qs.filter(
            Q(book_list__title__icontains=query)
            | Q(book_list__author__name__icontains=query)
        ).distinct()
    if difficulty:
        qs = qs.filter(book_list__difficulty=difficulty)
    if category_id:
        qs = qs.filter(book_list__categories__id=category_id)

    categories = Category.objects.all()
    difficulties = BookList.Difficulty.choices

    return render(request, "books/list.html", {
        "books": qs,
        "query": query,
        "selected_difficulty": difficulty,
        "selected_category": category_id,
        "categories": categories,
        "difficulties": difficulties,
    })


def book_detail(request, pk):
    book = get_object_or_404(
        Book.objects.select_related("book_list__publisher", "book_list__author")
        .prefetch_related("book_list__categories"),
        pk=pk,
        is_active=True,
    )
    return render(request, "books/detail.html", {"book": book})
