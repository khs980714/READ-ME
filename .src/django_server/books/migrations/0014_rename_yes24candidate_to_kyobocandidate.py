from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("books", "0013_booklist_subtitle"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="YES24Candidate",
            new_name="KyoboCandidate",
        ),
        migrations.AlterModelTable(
            name="kyobocandidate",
            table="kyobo_candidates",
        ),
        migrations.AlterModelOptions(
            name="kyobocandidate",
            options={
                "ordering": ["book_list", "created_at"],
                "verbose_name": "교보문고 후보 도서",
                "verbose_name_plural": "교보문고 후보 도서",
            },
        ),
        migrations.AlterField(
            model_name="kyobocandidate",
            name="book_list",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="kyobo_candidates",
                to="books.booklist",
                verbose_name="도서 정보",
            ),
        ),
        migrations.AlterField(
            model_name="kyobocandidate",
            name="href",
            field=models.URLField(max_length=1000, verbose_name="교보문고 링크"),
        ),
        migrations.AlterField(
            model_name="kyobocandidate",
            name="id",
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
        ),
    ]
