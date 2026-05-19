from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("books", "0012_yes24candidate"),
    ]

    operations = [
        migrations.AddField(
            model_name="booklist",
            name="subtitle",
            field=models.CharField(blank=True, max_length=500, verbose_name="부제"),
        ),
    ]
