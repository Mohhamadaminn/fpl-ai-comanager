from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("fpl_data", "0003_xp_model_inputs"),
    ]

    operations = [
        migrations.AddField(
            model_name="team",
            name="code",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
