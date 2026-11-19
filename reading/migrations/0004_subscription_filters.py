"""Story filters of one feed (reading/migrations/0004_subscription_filters.py)."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reading', '0003_subscription_muted'),
    ]

    operations = [
        migrations.AddField(
            model_name='subscription',
            name='filters',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
