from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("agents", "0003_agent_template_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="agent",
            name="template_version",
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
