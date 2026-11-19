"""Story.summary (feeds/migrations/0005_story_summary.py): the plain text of each story's content,
filled in for the stories already stored."""
from django.db import migrations, models

from feeds.util import chunks, story_summary


def fill_summaries(apps, schema_editor):
    Story = apps.get_model("feeds", "Story")
    ids = list(Story.objects.values_list("id", flat=True))
    for chunk in chunks(ids, 500):
        stories = list(Story.objects.filter(id__in=chunk).only("id", "content"))
        for story in stories:
            story.summary = story_summary(story.content)
        Story.objects.bulk_update(stories, ["summary"])


class Migration(migrations.Migration):
    dependencies = [
        ("feeds", "0004_feed_icon_link"),
    ]

    operations = [
        migrations.AddField(
            model_name="story",
            name="summary",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.RunPython(fill_summaries, migrations.RunPython.noop),
    ]
