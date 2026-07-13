from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("authentication", "0002_user_avatar"),
    ]

    operations = [
        migrations.CreateModel(
            name="PasswordResetToken",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token", models.CharField(db_index=True, max_length=64, unique=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reset_tokens",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ("expires_at", models.DateTimeField()),
                ("used", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "password_reset_tokens",
            },
        ),
    ]
