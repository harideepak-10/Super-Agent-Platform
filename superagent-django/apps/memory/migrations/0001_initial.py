# Generated manually — 2026-06-23

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("authentication", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CustomerProfile",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("email", models.EmailField()),
                ("name", models.CharField(blank=True, max_length=255)),
                ("company", models.CharField(blank=True, max_length=255)),
                ("role", models.CharField(blank=True, max_length=255)),
                ("preferred_language", models.CharField(default="en", max_length=10)),
                (
                    "communication_style",
                    models.CharField(
                        choices=[
                            ("formal", "Formal"),
                            ("casual", "Casual"),
                            ("technical", "Technical"),
                            ("brief", "Brief & Direct"),
                        ],
                        default="formal",
                        max_length=20,
                    ),
                ),
                (
                    "urgency_preference",
                    models.CharField(
                        choices=[
                            ("same_day", "Same Day"),
                            ("24h", "Within 24 Hours"),
                            ("48h", "Within 48 Hours"),
                            ("low", "Low Priority"),
                        ],
                        default="24h",
                        max_length=20,
                    ),
                ),
                ("custom_instructions", models.TextField(blank=True)),
                ("escalation_contacts", models.JSONField(default=list)),
                ("interaction_summary", models.TextField(blank=True)),
                ("common_topics", models.JSONField(default=list)),
                ("agent_notes", models.TextField(blank=True)),
                ("interaction_count", models.PositiveIntegerField(default=0)),
                (
                    "last_interaction_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="customer_profiles",
                        to="authentication.workspace",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "customer_profiles",
                "ordering": ["-last_interaction_at"],
                "unique_together": {("workspace", "email")},
            },
        ),
    ]
