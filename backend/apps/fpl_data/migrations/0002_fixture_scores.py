from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('fpl_data', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='fixture',
            name='team_home_score',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='fixture',
            name='team_away_score',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
    ]
