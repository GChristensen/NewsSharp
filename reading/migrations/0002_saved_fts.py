"""Full-text index of saved stories (reading/migrations/0002_saved_fts.py), item 12.
rowid = saved_stories.id."""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("reading", "0001_initial")]

    operations = [
        migrations.RunSQL(
            sql=(
                "CREATE VIRTUAL TABLE saved_fts USING fts5("
                "title, body, author, tags, user_tags, content='', contentless_delete=1, "
                "tokenize='porter unicode61 remove_diacritics 2')"
            ),
            reverse_sql="DROP TABLE IF EXISTS saved_fts",
        )
    ]
