# Generated manually — 2026-06-23
# Split from 0001 because CustomerInteraction has a FK to tasks.Task

import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("memory", "0001_initial"),
        ("tasks", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="CustomerInteraction",
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
                (
                    "interaction_type",
                    models.CharField(
                        choices=[
                            ("email_read", "Email Read"),
                            ("email_classified", "Email Classified"),
                            ("draft_created", "Draft Created"),
                            ("email_sent", "Email Sent"),
                            ("thread_summarized", "Thread Summarized"),
                            ("action_items_extracted", "Action Items Extracted"),
                        ],
                        max_length=30,
                    ),
                ),
                ("summary", models.TextField(blank=True)),
                ("metadata", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "customer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="interactions",
                        to="memory.customerprofile",
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="customer_interactions",
                        to="tasks.task",
                    ),
                ),
            ],
            options={
                "db_table": "customer_interactions",
                "ordering": ["-created_at"],
            },
        ),
    ]
