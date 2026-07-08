import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("authentication", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="QuickTask",
            fields=[
                ("id",          models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("title",       models.CharField(max_length=120)),
                ("prompt",      models.TextField()),
                ("agent_type",  models.CharField(blank=True, default="", max_length=50)),
                ("icon",        models.CharField(default="zap", max_length=50)),
                ("run_count",   models.PositiveIntegerField(default=0)),
                ("source",      models.CharField(
                    choices=[("default", "Default"), ("manual", "Manual"), ("auto", "Auto-promoted")],
                    default="default",
                    max_length=20,
                )),
                ("order",       models.PositiveIntegerField(default=0)),
                ("created_at",  models.DateTimeField(auto_now_add=True)),
                ("last_run_at", models.DateTimeField(blank=True, null=True)),
                ("workspace",   models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="quick_tasks",
                    to="authentication.workspace",
                )),
                ("user",        models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="quick_tasks",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "ordering": ["order", "-run_count"],
            },
        ),
        migrations.AlterUniqueTogether(
            name="quicktask",
            unique_together={("workspace", "user", "title")},
        ),
    ]
