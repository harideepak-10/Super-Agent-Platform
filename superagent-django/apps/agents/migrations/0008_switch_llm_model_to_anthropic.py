from django.db import migrations


def switch_to_anthropic(apps, schema_editor):
    Agent = apps.get_model("agents", "Agent")
    Agent.objects.filter(llm_model="llama-3.3-70b-versatile").update(
        llm_model="claude-haiku-4-5-20251001"
    )


def revert_to_groq(apps, schema_editor):
    Agent = apps.get_model("agents", "Agent")
    Agent.objects.filter(llm_model="claude-haiku-4-5-20251001").update(
        llm_model="llama-3.3-70b-versatile"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("agents", "0007_create_finance_agent"),  # check this matches your latest file
    ]

    operations = [
        migrations.RunPython(switch_to_anthropic, revert_to_groq),
    ]