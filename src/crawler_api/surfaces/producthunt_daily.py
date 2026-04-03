"""
Product Hunt Daily surface collector.

Fetches today's top products from the Product Hunt GraphQL API using
OAuth2 client credentials authentication.

API docs: https://api.producthunt.com/v2/docs
Requires: api_key (client_id) and api_secret (client_secret) in config.

Locale: en-US
Surface type: ranking
Bucket: hot_now
"""

import logging
from typing import Optional, Tuple, List

from .collector_interface import CollectedItem
from shared.http_client import RateLimitedClient

logger = logging.getLogger(__name__)

PRODUCTHUNT_TOKEN_URL = "https://api.producthunt.com/v1/oauth/token"
PRODUCTHUNT_GRAPHQL_URL = "https://api.producthunt.com/v2/api/graphql"

GRAPHQL_QUERY = """
query {
  posts(first: 30, order: VOTES) {
    edges {
      node {
        id
        name
        tagline
        description
        url
        votesCount
        commentsCount
        thumbnail { url }
        topics { edges { node { name } } }
        createdAt
      }
    }
  }
}
"""


async def _get_access_token(client: RateLimitedClient, api_key: str, api_secret: str) -> str:
    """
    Obtain a bearer access token from Product Hunt using client credentials.

    Args:
        client: HTTP client
        api_key: Product Hunt client_id
        api_secret: Product Hunt client_secret

    Returns:
        Bearer access token string

    Raises:
        httpx.HTTPError: On request failure
        KeyError: If response does not contain access_token
    """
    payload = {
        "client_id": api_key,
        "client_secret": api_secret,
        "grant_type": "client_credentials",
    }
    response = await client.post(
        PRODUCTHUNT_TOKEN_URL,
        json=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    response.raise_for_status()
    data = response.json()
    return data["access_token"]


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect today's top products from Product Hunt via GraphQL API.

    Config params (from TrendSurface.config_json):
        - api_key: Product Hunt OAuth2 client_id (REQUIRED)
        - api_secret: Product Hunt OAuth2 client_secret (REQUIRED)

    Args:
        config: Surface configuration
        cursor: Pagination cursor (unused — fetches top posts for today)
        limit: Maximum items to collect (capped at 30, GraphQL query limit)

    Returns:
        (items, None) — no pagination cursor

    Raises:
        ValueError: If api_key or api_secret is missing from config
        httpx.HTTPError: On HTTP request failures
    """
    api_key = config.get("api_key")
    api_secret = config.get("api_secret")

    if not api_key or not api_secret:
        raise ValueError(
            "Product Hunt requires api_key and api_secret in config"
        )

    logger.info("Fetching Product Hunt daily top products via GraphQL API...")

    async with RateLimitedClient(timeout=30.0) as client:
        # Step 1: obtain OAuth2 access token
        access_token = await _get_access_token(client, api_key, api_secret)
        logger.info("Product Hunt: access token obtained successfully")

        # Step 2: query GraphQL
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        response = await client.post(
            PRODUCTHUNT_GRAPHQL_URL,
            json={"query": GRAPHQL_QUERY},
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()

    edges = data.get("data", {}).get("posts", {}).get("edges", [])
    logger.info(f"Product Hunt GraphQL returned {len(edges)} posts")

    items: List[CollectedItem] = []

    for rank, edge in enumerate(edges[:limit], start=1):
        node = edge.get("node", {})
        if not node:
            continue

        post_id = str(node.get("id", ""))
        name = node.get("name", "Untitled")
        tagline = node.get("tagline", "")
        description = node.get("description", "")
        url = node.get("url", "")
        votes = node.get("votesCount", 0)
        comments = node.get("commentsCount", 0)
        created_at = node.get("createdAt")  # ISO8601 string

        # Thumbnail URL
        thumbnail_url = node.get("thumbnail", {}).get("url", "") if node.get("thumbnail") else ""

        # Topics / tags
        topic_edges = node.get("topics", {}).get("edges", [])
        topics = [
            te["node"]["name"]
            for te in topic_edges
            if te.get("node", {}).get("name")
        ]

        # Title: name + tagline
        title = f"{name} — {tagline}" if tagline else name

        item: CollectedItem = {
            "external_id": post_id,
            "title": title,
            "url": url,
            "locale": "en-US",
            "rank_position": rank,
            "engagement_signals": {
                "upvotes": votes,
                "comments": comments,
            },
            "raw_payload": {
                **node,
                "_thumbnail_url": thumbnail_url,
                "_topics": topics,
                "source": "producthunt_daily",
            },
        }

        if description:
            item["description"] = description[:1000]
        if created_at:
            item["published_at"] = created_at

        items.append(item)

    logger.info(f"Collected {len(items)} products from Product Hunt")
    return items, None
