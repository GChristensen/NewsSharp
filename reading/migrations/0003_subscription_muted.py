"""Muted subscriptions (reading/migrations/0003_subscription_muted.py)."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reading', '0002_saved_fts'),
    ]

    operations = [
        migrations.AddField(
            model_name='subscription',
            name='muted',
            field=models.BooleanField(default=False),
        ),
    ]
