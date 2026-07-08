from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="integration",
            name="provider",
            field=models.CharField(
                max_length=50,
                choices=[
                    ("gmail", "Gmail"),
                    ("google_drive", "Google Drive"),
                    ("google_calendar", "Google Calendar"),
                    ("slack", "Slack"),
                    ("notion", "Notion"),
                    ("github", "GitHub"),
                ],
            ),
        ),
    ]
