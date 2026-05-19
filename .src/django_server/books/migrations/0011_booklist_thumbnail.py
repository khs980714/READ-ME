from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("books", "0010_remove_isbn"),
    ]

    operations = [
        migrations.AddField(
            model_name="booklist",
            name="thumbnail",
            field=models.ImageField(
                blank=True,
                upload_to="thumbnails/",
                verbose_name="썸네일 이미지",
                help_text="저장된 썸네일 파일 (로컬 볼륨 또는 R2)",
            ),
        ),
        migrations.AlterField(
            model_name="booklist",
            name="thumbnail_url",
            field=models.URLField(
                blank=True,
                max_length=500,
                verbose_name="썸네일 원본 URL",
                help_text="수집 소스 URL — 표시에는 thumbnail 필드를 우선 사용",
            ),
        ),
    ]
