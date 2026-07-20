from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0003_task_deliverables_step_meta"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="conversation_id",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
    ]
