# Generated 2026-04-08
# Adds floor_percent and cap_percent to TrendSurface.
# RunPython sets initial values for P1/P2 priority surfaces.

from django.db import migrations, models


INITIAL_ALLOCATION = {
    # key: (floor_percent, cap_percent)  — None means "don't set" (keep default)
    # P1: Chinese zone
    'wenxuecity_news':   (10, 30),
    '36kr_rss':          (5,  20),
    'netease_news_rss':  (5,  20),
    'weibo_hot':         (0,  40),
    'baidu_hot':         (0,  40),
    'zhihu_hot':         (0,  30),
    # P1: Russian zone (exile/blocked independent media)
    'meduza_rss':        (20, 50),
    'bbc_russian_rss':   (10, 40),
    'moscowtimes_rss':   (10, 40),
    # P2: Arabic zone (all RSS, important reporting)
    'aljazeera_ar_rss':  (20, 50),
    'bbcarabic_rss':     (10, 30),
    'alarabiya_rss':     (0,  30),
    # P2: Turkish zone (all RSS + independent media under pressure)
    'cumhuriyet_rss':    (10, 30),
    'bbcturkce_rss':     (10, 30),
    'hurriyet_rss':      (10, None),
    'dw_tr_rss':         (10, None),
    # Monopoly cap for high-volume ranking surfaces
    'reddit_hot':        (0,  30),
}


def set_initial_allocation(apps, schema_editor):
    TrendSurface = apps.get_model('crawler_admin', 'TrendSurface')
    for key, (floor_pct, cap_pct) in INITIAL_ALLOCATION.items():
        updated = TrendSurface.objects.filter(key=key).update(
            floor_percent=floor_pct,
            cap_percent=cap_pct,
        )
        if updated == 0:
            # Surface not seeded yet — will be set by seeds.json on next seed run
            pass


def revert_initial_allocation(apps, schema_editor):
    TrendSurface = apps.get_model('crawler_admin', 'TrendSurface')
    TrendSurface.objects.filter(key__in=INITIAL_ALLOCATION.keys()).update(
        floor_percent=0,
        cap_percent=None,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('crawler_admin', '0017_merge_20260403_0222'),
    ]

    operations = [
        migrations.AddField(
            model_name='trendsurface',
            name='floor_percent',
            field=models.IntegerField(
                default=0,
                help_text=(
                    "Floor guarantee: % of this surface's lang_group top-N% pool size "
                    "that is guaranteed per selection cycle (0 = no floor, pure competition)"
                ),
            ),
        ),
        migrations.AddField(
            model_name='trendsurface',
            name='cap_percent',
            field=models.IntegerField(
                null=True,
                blank=True,
                default=None,
                help_text=(
                    "Cap: max % of lang_group top-N% pool this surface can occupy per cycle "
                    "(floor + competition combined). None = uncapped."
                ),
            ),
        ),
        migrations.RunPython(
            set_initial_allocation,
            reverse_code=revert_initial_allocation,
        ),
    ]
