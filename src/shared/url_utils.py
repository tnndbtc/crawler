"""
URL utilities for normalization and caching.

Provides functions to:
- Extract domain from URLs
- Normalize URLs for caching (sort query params, strip tracking params)
- Compute URL hashes for cache keys

Usage:
    from shared.url_utils import normalize_url, compute_url_hash, extract_domain

    url = "https://example.com/page?utm_source=twitter&id=123"
    normalized = normalize_url(url)  # "https://example.com/page?id=123"
    hash = compute_url_hash(url)     # SHA256 hash
    domain = extract_domain(url)     # "example.com"
"""

import hashlib
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from typing import Optional


# Tracking parameters to strip from URLs
# These params don't affect content but create cache misses
TRACKING_PARAMS = {
    # Google Analytics
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
    'utm_id', 'utm_source_platform', 'utm_creative_format', 'utm_marketing_tactic',

    # Facebook
    'fbclid', 'fb_action_ids', 'fb_action_types', 'fb_source', 'fb_ref',

    # Twitter
    'twclid', 'tw_source', 'tw_campaign',

    # Other common trackers
    'gclid', 'dclid', 'msclkid', '_ga', '_gl',
    'mc_cid', 'mc_eid',  # Mailchimp
    'ref', 'referrer',   # Generic referrer tracking
}


def extract_domain(url: str) -> str:
    """
    Extract domain from URL.

    Args:
        url: Full URL

    Returns:
        Domain name (e.g., "news.ycombinator.com")

    Examples:
        >>> extract_domain("https://news.ycombinator.com/news")
        'news.ycombinator.com'
        >>> extract_domain("http://example.com:8080/path")
        'example.com'
    """
    parsed = urlparse(url)
    domain = parsed.netloc

    # Strip port if present
    if ':' in domain:
        domain = domain.split(':')[0]

    return domain


def normalize_url(url: str, strip_fragment: bool = True) -> str:
    """
    Normalize URL for consistent caching.

    Normalization:
    - Sort query parameters alphabetically
    - Strip tracking parameters (utm_*, fbclid, etc.)
    - Optionally strip fragment (#section)
    - Lowercase scheme and domain
    - Remove default ports (80 for http, 443 for https)

    Args:
        url: URL to normalize
        strip_fragment: Whether to remove fragment (#section) (default: True)

    Returns:
        Normalized URL string

    Examples:
        >>> normalize_url("https://example.com/page?b=2&a=1&utm_source=twitter")
        'https://example.com/page?a=1&b=2'
        >>> normalize_url("HTTP://EXAMPLE.COM/PAGE")
        'http://example.com/PAGE'
    """
    parsed = urlparse(url)

    # Lowercase scheme and domain
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()

    # Remove default ports
    if ':' in netloc:
        host, port = netloc.rsplit(':', 1)
        if (scheme == 'http' and port == '80') or (scheme == 'https' and port == '443'):
            netloc = host

    # Parse and filter query parameters
    query_params = parse_qs(parsed.query, keep_blank_values=True)

    # Remove tracking parameters
    filtered_params = {
        k: v for k, v in query_params.items()
        if k.lower() not in TRACKING_PARAMS
    }

    # Sort parameters alphabetically and reconstruct query string
    # Use sorted() to ensure consistent order
    sorted_query = urlencode(sorted(filtered_params.items()), doseq=True)

    # Strip fragment if requested
    fragment = '' if strip_fragment else parsed.fragment

    # Reconstruct URL
    normalized = urlunparse((
        scheme,
        netloc,
        parsed.path,
        parsed.params,
        sorted_query,
        fragment
    ))

    return normalized


def compute_url_hash(url: str) -> str:
    """
    Compute SHA256 hash of normalized URL.

    Args:
        url: URL to hash

    Returns:
        64-character hex string (SHA256 hash)

    Examples:
        >>> compute_url_hash("https://example.com/page?b=2&a=1")
        'abc123...'  # SHA256 hash of normalized URL
    """
    normalized = normalize_url(url)
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


def is_same_domain(url1: str, url2: str) -> bool:
    """
    Check if two URLs are from the same domain.

    Args:
        url1: First URL
        url2: Second URL

    Returns:
        True if both URLs are from the same domain

    Examples:
        >>> is_same_domain("https://example.com/page1", "http://example.com/page2")
        True
        >>> is_same_domain("https://example.com/page", "https://other.com/page")
        False
    """
    return extract_domain(url1) == extract_domain(url2)


def get_cache_key_for_url(url: str) -> str:
    """
    Get cache key for URL (convenience wrapper for compute_url_hash).

    Args:
        url: URL to get cache key for

    Returns:
        Cache key (SHA256 hash)
    """
    return compute_url_hash(url)
