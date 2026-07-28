from django.db import migrations


def revert_llm_model(apps, schema_editor):
    Agent = apps.get_model("agents", "Agent")
    Agent.objects.filter(llm_model__startswith="claude-").update(
        llm_model="llama-3.3-70b-versatile"
    )


def reverse_revert(apps, schema_editor):
    Agent = apps.get_model("agents", "Agent")
    Agent.objects.filter(llm_model="llama-3.3-70b-versatile").update(
        llm_model="claude-haiku-4-5-20251001"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("agents", "0005_update_llm_model_to_anthropic"),  # check this matches your latest file
    ]

    operations = [
        migrations.RunPython(revert_llm_model, reverse_revert),
    ]