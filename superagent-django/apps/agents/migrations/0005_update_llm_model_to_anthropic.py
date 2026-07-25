from django.db import migrations


def update_llm_model(apps, schema_editor):
    Agent = apps.get_model("agents", "Agent")
    Agent.objects.filter(llm_model="llama-3.3-70b-versatile").update(
        llm_model="claude-haiku-4-5-20251001"
    )


def reverse_update(apps, schema_editor):
    Agent = apps.get_model("agents", "Agent")
    Agent.objects.filter(llm_model="claude-haiku-4-5-20251001").update(
        llm_model="llama-3.3-70b-versatile"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("agents", "0004_agent_template_version"),  # adjust if your latest migration file has a different name
    ]

    operations = [
        migrations.RunPython(update_llm_model, reverse_update),
    ]