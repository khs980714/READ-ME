from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("books", "0011_booklist_thumbnail"),
    ]

    operations = [
        migrations.CreateModel(
            name="YES24Candidate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=500, verbose_name="도서명")),
                ("subtitle", models.CharField(blank=True, max_length=500, verbose_name="부제")),
                ("href", models.URLField(max_length=1000, verbose_name="YES24 링크")),
                ("edition_info", models.CharField(blank=True, max_length=200, verbose_name="개정 정보")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "book_list",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="yes24_candidates",
                        to="books.booklist",
                        verbose_name="도서 정보",
                    ),
                ),
            ],
            options={
                "verbose_name": "YES24 후보 도서",
                "verbose_name_plural": "YES24 후보 도서",
                "db_table": "yes24_candidates",
                "ordering": ["book_list", "created_at"],
            },
        ),
    ]
