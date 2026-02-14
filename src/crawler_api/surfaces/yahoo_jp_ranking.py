"""
Yahoo Japan Ranking surface collector (STUB for testing).

In production, this would scrape Yahoo Japan trending pages.
For now, returns mock data in Japanese following the collector interface.

Surface type: ranking
Bucket: region_local (local mainstream portal)
"""

import asyncio
from typing import Optional, Tuple, List
from datetime import datetime, timezone, timedelta

from .collector_interface import CollectedItem


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect trending topics from Yahoo Japan.

    STUB IMPLEMENTATION - Returns mock data for testing.
    Uses Japanese text to test translation pipeline.

    Config params (from TrendSurface.config_json):
        - category: Category to collect from (default: "all")

    Args:
        config: Surface configuration
        cursor: Pagination cursor (unused in stub)
        limit: Maximum items to collect

    Returns:
        (items, next_cursor)
    """
    # Simulate scraping delay
    await asyncio.sleep(0.6)

    category = config.get('category', 'all')
    actual_limit = min(limit, 20)  # Yahoo typically shows top 20

    # Mock Japanese trending topics
    # In production, this would come from actual Yahoo Japan data
    japanese_topics = [
        "【速報】東京オリンピック開催決定",
        "新型スマートフォン発売予定",
        "プロ野球試合結果まとめ",
        "人気アニメ最新話配信中",
        "株価急騰、投資家注目",
        "最新映画ランキング発表",
        "AI技術の新展開",
        "料理レシピが話題に",
        "ファッショントレンド2024",
        "天気予報：週末は晴れ",
        "人気ゲーム新作発表",
        "健康食品ブーム到来",
        "旅行先人気ランキング",
        "音楽チャートトップ10",
        "ビジネス成功の秘訣",
        "科学研究の新発見",
        "政治ニュース速報",
        "スポーツ選手インタビュー",
        "ドラマ視聴率ランキング",
        "テクノロジー最前線",
    ]

    items: List[CollectedItem] = []

    # Generate mock items
    for i in range(1, min(actual_limit, len(japanese_topics)) + 1):
        published_at = datetime.now(timezone.utc) - timedelta(minutes=i*10)

        item: CollectedItem = {
            "external_id": f"yahoo_jp_stub_{i}",
            "title": f"{japanese_topics[i-1]} [スタブ]",  # [スタブ] = [STUB]
            "description": f"これはテスト用のスタブ記事です。本番環境では実際のYahoo Japanのコンテンツが表示されます。",
            "url": f"https://news.yahoo.co.jp/topics/stub{i}",
            "locale": "ja-JP",
            "published_at": published_at.isoformat(),

            # CRITICAL: rank_position (from /tmp/t9)
            "rank_position": i,

            # CRITICAL: engagement_signals (from /tmp/t9)
            "engagement_signals": {
                "views": 500000 - (i * 5000),  # Decreasing by rank
                "shares": 10000 - (i * 100),
                "comments": 1000 - (i * 10),
            },

            # CRITICAL: raw_payload (from /tmp/t9)
            "raw_payload": {
                "stub": True,
                "topic_id": f"stub_{i}",
                "category": category,
                "published_at": published_at.isoformat(),
                "metrics": {
                    "page_views": 500000 - (i * 5000),
                    "shares": 10000 - (i * 100),
                    "comments": 1000 - (i * 10),
                },
                "source": "Yahoo Japan (stub)",
            }
        }
        items.append(item)

    # No pagination in stub
    next_cursor = None

    return items, next_cursor
