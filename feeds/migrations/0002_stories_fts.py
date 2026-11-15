"""Full-text index of stories (feeds/migrations/0002_stories_fts.py), item 12. rowid = stories.id."""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("feeds", "0001_initial")]

    operations = [
        migrations.RunSQL(
            sql=(
                "CREATE VIRTUAL TABLE stories_fts USING fts5("
                "title, body, author, tags, content='', contentless_delete=1, "
                "tokenize='porter unicode61 remove_diacritics 2')"
            ),
            reverse_sql="DROP TABLE IF EXISTS stories_fts",
        )
    ]
