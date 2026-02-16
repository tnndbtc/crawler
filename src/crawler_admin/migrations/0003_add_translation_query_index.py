# Generated migration for Feature t4: zh-Hans filtering optimization
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Add composite index to optimize zh-Hans translation filtering queries.

    Context (from /tmp/t4):
    - When lang=zh-Hans is requested, API needs to filter items by translation status
    - This requires efficient lookup: (locale='zh-Hans' AND status='complete')
    - Adding item_id to the index optimizes the JOIN back to TrendItem table
    - Enables efficient chunk-based scanning for overfetching logic
    """

    dependencies = [
        ('crawler_admin', '0002_enable_zh_hans_locale'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='trenditemtranslation',
            index=models.Index(
                fields=['locale', 'status', 'item'],
                name='crawler_admin_translation_query_idx'
            ),
        ),
    ]
