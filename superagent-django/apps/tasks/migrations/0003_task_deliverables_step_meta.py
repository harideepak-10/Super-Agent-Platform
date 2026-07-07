from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0002_add_task_priority"),
    ]

    operations = [
        # Task — deliverables output list + optional step estimate
        migrations.AddField(
            model_name="task",
            name="deliverables",
            field=models.JSONField(blank=True, default=list,
                help_text="Files produced by the task. Shape: [{name, url, type}]"),
        ),
        migrations.AddField(
            model_name="task",
            name="total_steps_estimate",
            field=models.PositiveIntegerField(null=True, blank=True,
                help_text="Orchestrator sets this when splitting across agents"),
        ),
        # TaskStep — per-step agent name + human-readable title/detail
        migrations.AddField(
            model_name="taskstep",
            name="agent_name",
            field=models.CharField(max_length=255, blank=True, default="",
                help_text="Which agent handled this step"),
        ),
        migrations.AddField(
            model_name="taskstep",
            name="title",
            field=models.CharField(max_length=255, blank=True, default="",
                help_text="Short human-readable label e.g. 'Sending email'"),
        ),
        migrations.AddField(
            model_name="taskstep",
            name="detail",
            field=models.TextField(blank=True, default="",
                help_text="One-line detail e.g. 'To harideepak@...'"),
        ),
    ]
