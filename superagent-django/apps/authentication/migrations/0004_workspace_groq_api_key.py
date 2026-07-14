from django.db import migrations


class Migration(migrations.Migration):
    """No-op: groq_api_key field removed from Workspace."""

    dependencies = [
        ("authentication", "0003_passwordresettoken"),
    ]

    operations = []
