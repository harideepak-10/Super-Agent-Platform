from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("agents", "0002_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="agent",
            name="template_id",
            field=models.PositiveSmallIntegerField(
                blank=True,
                null=True,
                help_text="1=Email, 2=Research, 3=Document, 4=Calendar, 5=Reporting",
            ),
        ),
    ]
